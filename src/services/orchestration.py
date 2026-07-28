import os
import uuid
from dataclasses import dataclass, field
from typing import Optional, List, Any

from src.agents.protocol import TurnContext, AgentSessionView, Option
from src.repositories.conversations import ConversationRepository
from src.repositories.messages import MessageRepository
from src.repositories.sessions import AgentSessionRepository
from src.services.router_service import RouterService, RouteDecision
from src.services.config_mode_router import decide_sub_agent
from src.services.session_service import SessionService
from src.services.agent_registry import AgentRegistry
from src.agents.factory import HandlerFactory
from src.logger import get_logger

logger = get_logger("orchestration")

@dataclass
class TurnInput:
    request_id: str
    conversation_id: Optional[uuid.UUID]
    user: Any
    text: str
    option_id: Optional[str] = None
    agent_key: Optional[str] = None

@dataclass
class TurnResult:
    conversation: Any
    message: Any
    agent: Any
    decision: RouteDecision
    turn: Any
    session: Optional[AgentSessionView]

class RateLimitsDummy:
    def check(self, conv_id, user, limits):
        pass

class ToolsRepoDummy:
    def bulk_insert(self, msg_id, agent_id, tool_traces):
        pass

class OrchestrationService:
    def __init__(
        self,
        session,
        registry: AgentRegistry,
        handler_factory: HandlerFactory,
        llm_factory: Any,
        router_service: Optional[RouterService] = None,
    ):
        self._db = session
        self._registry = registry
        self._handlers = handler_factory
        self._llm_factory = llm_factory
        
        self._conversations = ConversationRepository(session)
        self._messages = MessageRepository(session)
        self._sessions = SessionService(session)
        self._sessions_repo = AgentSessionRepository(session)
        
        self._router = router_service or RouterService(session, registry, llm_factory)
        
        # Dummies for missing components
        self._rate_limits = RateLimitsDummy()
        self._tools_repo = ToolsRepoDummy()

    def handle_turn(self, ctx_in: TurnInput) -> TurnResult:
        # 1. get_or_create conv
        conv = self._conversations.get_or_create(ctx_in.conversation_id, ctx_in.user)

        # 2. allocate seq under a row lock — also serialises double-submits (§1.6)
        seq = self._conversations.next_seq_for_update(conv.id)
        
        # 3. insert user message
        self._messages.insert(
            conv.id, seq, role="user", content=ctx_in.text,
            selected_option_id=ctx_in.option_id, request_id=ctx_in.request_id
        )

        # 4. route
        router_mode = os.environ.get("SAARTHI_ROUTER", "v2")
        if router_mode == "v1":
            visible = [a for a in self._registry.routable() if a.spec.access.matches(ctx_in.user)]
            # We mock the TurnContext here just for routing if it were to use it, 
            # but config_mode_router uses strings
            agent = decide_sub_agent(self._llm_factory, visible, self._registry.default(), ctx_in.text)
            decision = RouteDecision(agent, reason="llm", confidence=1.0, router_latency_ms=0)
        else:
            # v2 routing (5 gates)
            # Build partial context for router
            ctx_partial = TurnContext(
                request_id=ctx_in.request_id,
                conversation_id=conv.id,
                user=ctx_in.user,
                text=ctx_in.text,
                option_id=ctx_in.option_id,
                history=[], # router might use recent history
                session=None,
                locale=ctx_in.user.locale if hasattr(ctx_in.user, 'locale') else "en"
            )
            # Populate history for router so it can be history-aware (gate 4)
            ctx_partial = TurnContext(
                request_id=ctx_in.request_id,
                conversation_id=conv.id,
                user=ctx_in.user,
                text=ctx_in.text,
                option_id=ctx_in.option_id,
                history=self._messages.recent(conv.id, self._registry.default().spec.memory) if self._registry.default() else [],
                session=None,
                locale=ctx_partial.locale
            )
            decision = self._router.select(conv, ctx_partial, explicit_key=ctx_in.agent_key)
            agent = decision.agent

        # 5. enforce limits
        self._rate_limits.check(conv.id, ctx_in.user, agent.spec.limits)

        # 6. open the session
        session_view = self._sessions.open_for(conv.id, agent)

        # 7. apply the pin
        if agent.spec.routing.pin_session:
            self._conversations.pin(conv.id, getattr(agent, "id", agent.key)) # Fallback if agent.id not mapped as UUID

        # 8. assemble history
        history = self._messages.recent(conv.id, agent.spec.memory)
        
        # 9. COMMIT <- releases the lock
        self._db.commit()

        # 10. handler.handle
        handler = self._handlers.build(agent.spec, agent.checksum)
        
        locale = ctx_in.user.locale if hasattr(ctx_in.user, 'locale') else "en"
        ctx = TurnContext(
            request_id=ctx_in.request_id,
            conversation_id=conv.id,
            user=ctx_in.user,
            text=ctx_in.text,
            option_id=ctx_in.option_id,
            history=history,
            session=session_view,
            locale=locale
        )
        
        turn = handler.handle(ctx)

        # 11. apply session delta; finalise if terminal
        if turn.session_delta and session_view:
            session_view = self._sessions.apply(session_view, turn.session_delta)
        if turn.terminal and session_view:
            session_view = self._finalize(session_view, agent, turn)

        # 12. persist the agent message
        options_dict = [o.__dict__ for o in turn.options] if turn.options else None
        
        msg = self._messages.insert(
            conv.id, 
            self._conversations.next_seq_for_update(conv.id),
            role="assistant", 
            content=turn.text,
            agent_id=agent.id, 
            agent_config_id=getattr(agent, "config_id", None), # Handle missing config_id
            agent_session_id=session_view.id if session_view else None,
            route_reason=decision.reason,
            route_confidence=decision.confidence,
            options=options_dict, 
            model=turn.model,
            prompt_tokens=turn.prompt_tokens,
            completion_tokens=turn.completion_tokens,
            latency_ms=turn.latency_ms, 
            error=turn.error,
            request_id=ctx_in.request_id
        )

        # 13. persist tool traces
        self._tools_repo.bulk_insert(msg.id, agent.id, turn.tool_traces)
        
        # 14. touch conversation
        self._conversations.touch(conv.id, title_from=ctx_in.text)
        self._db.commit()
        
        return TurnResult(
            conversation=conv,
            message=msg,
            agent=agent,
            decision=decision,
            turn=turn,
            session=session_view
        )
        
    def _finalize(self, session_view, agent, turn):
        # Dummy finalizer to satisfy the invocation
        # In a real impl, this uses claim_finalizing and hits Mitra finalize endpoint
        claimed = self._sessions.claim_finalizing(session_view.id)
        if claimed:
            from src.agents.protocol import SessionDelta, SessionState
            # transition to completed
            return self._sessions.apply(claimed, SessionDelta(state=SessionState.completed))
        return session_view
