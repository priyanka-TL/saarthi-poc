import os
import uuid
from dataclasses import dataclass, field
from typing import Optional, List, Any

from src.agents.protocol import TurnContext, AgentSessionView, Option, SessionDelta, SessionState
from src.repositories.audit import AuditLogRepository
from src.repositories.conversations import ConversationRepository
from src.repositories.messages import MessageRepository
from src.repositories.sessions import AgentSessionRepository
from src.services.router_service import RouterService, RouteDecision
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
        mitra_rest: Optional[Any] = None,
        mitra_sessions: Optional[Any] = None,
    ):
        self._db = session
        self._registry = registry
        self._handlers = handler_factory
        self._llm_factory = llm_factory
        self._mitra_rest = mitra_rest
        self._mitra_sessions = mitra_sessions

        self._conversations = ConversationRepository(session)
        self._messages = MessageRepository(session)
        self._sessions = SessionService(session)
        self._sessions_repo = AgentSessionRepository(session)
        self._audit = AuditLogRepository(session)

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

        # 4. route (v2: 5 gates)
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
            session_view = self._finalize(session_view, agent, ctx_in.user)

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
        
        # 14. touch conversation (set title from first user message, truncated per contract)
        raw_title = ctx_in.text
        title_for_conv = raw_title if len(raw_title) <= 60 else raw_title[:57] + "..."
        self._conversations.touch(conv.id, title_from=title_for_conv)
        self._db.commit()
        
        return TurnResult(
            conversation=conv,
            message=msg,
            agent=agent,
            decision=decision,
            turn=turn,
            session=session_view
        )
        
    def _finalize(self, session_view, agent, user):
        """Finalisation, triggered by AgentTurn.terminal (design doc §4.7, §8.5).

        Mitra's Story.session is UNIQUE (story_models.py:30, verified against
        real source) -- a second finalize() call for one session fails on
        Mitra's side. claim_finalizing()'s conditional UPDATE + uq_sess_remote
        together make a duplicate structurally impossible on Saarthi's side
        too, which is why the claim is step 1 and everything else only runs
        if it's won.
        """
        # 1. THE CLAIM.
        claimed = self._sessions.claim_finalizing(session_view.id)
        if claimed is None:
            # Zero rows: another request already owns finalisation. Return
            # whatever the DB actually shows right now (still 'finalizing',
            # or already 'completed' with its real result_ref if the winner
            # finished first) -- the client polls either way.
            current = self._sessions.get(session_view.id)
            return current if current is not None else session_view

        # 2. Audit the claim.
        self._audit.insert(
            action="session_finalize",
            entity_type="agent_session",
            entity_id=claimed.id,
            before=session_view.model_dump(mode="json"),
            after=claimed.model_dump(mode="json"),
        )

        # 3. CLOSE THE CHANNEL cleanly BEFORE calling finalize.
        if self._mitra_sessions is not None:
            self._mitra_sessions.close(claimed.conversation_id)

        # 4. finalize() with the user's token.
        try:
            story_id, _content = self._mitra_rest.finalize(
                session_id=claimed.remote_session_id,
                profile_id=claimed.remote_profile_id,
                flow=agent.spec.remote.flow_name,
                language=claimed.language,
                token=user.token,
            )
        except Exception as e:
            # Don't leave the session stuck in 'finalizing' forever -- that
            # state has no other way out. Not explicitly in the doc's
            # sequence, but a session that can never reach a terminal state
            # is a real bug.
            self._sessions.apply(claimed, SessionDelta(state=SessionState.failed, error=str(e)))
            raise

        # 6. get_report() -- fetched BEFORE the completed-transition, not after.
        # SessionService.apply() rejects every call on an already-terminal
        # session, including same-state field-only updates (terminal states
        # are final, by design -- see SessionService.ALLOWED). A second
        # apply() call to attach report_url after transitioning to
        # 'completed' would raise InvalidTransitionError. Fetching the
        # report first lets result_ref and report_url land in the SAME
        # apply() call instead.
        report_url = self._mitra_rest.get_report(
            claimed.remote_session_id, media_type=agent.spec.remote.report_media_type,
        )

        # 5. Transition to completed -- apply() sets ended_at/finalized_at itself.
        #    report_url stays null if the report hasn't been generated yet;
        #    a client polls /api/sessions/{id}/report for it later (outside
        #    this method's scope).
        delta_fields = {"result_ref": story_id}
        if report_url:
            delta_fields["report_url"] = report_url
        completed = self._sessions.apply(
            claimed, SessionDelta(state=SessionState.completed, **delta_fields),
        )
        self._conversations.unpin(claimed.conversation_id)
        return completed
