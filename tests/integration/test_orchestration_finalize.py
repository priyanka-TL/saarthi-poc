"""Integration tests for OrchestrationService._finalize (design doc §4.7, §8.5).

Real Postgres for SessionService/repositories (the whole point is exercising
the real claim_finalizing/apply/audit/unpin logic together) -- only the
Mitra-facing REST client and channel pool are faked, matching
tests/unit/test_remote_flow_handler.py's established fake style.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pytest
from sqlalchemy import text, select

from src.db.engine import SessionLocal
from src.db.models import Conversation
from src.domain.core import UserContext
from src.domain.agent_spec import RemoteFlowAgentSpec, RemoteSpec, RoutingSpec
from src.agents.protocol import SessionDelta, SessionState
from src.repositories.conversations import ConversationRepository
from src.repositories.sessions import AgentSessionRepository
from src.services.orchestration import OrchestrationService


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeMitraRest:
    def __init__(self, call_order: List[str]):
        self._call_order = call_order
        self.finalize_calls: List[Tuple] = []
        self.get_report_calls: List[Tuple] = []
        self._story_id = "9931"
        self._content = "narrative content"
        self._report_url: Optional[str] = None
        self._finalize_error: Optional[Exception] = None

    def finalize(self, session_id, profile_id, flow, language, token):
        self._call_order.append("finalize")
        self.finalize_calls.append((session_id, profile_id, flow, language, token))
        if self._finalize_error is not None:
            raise self._finalize_error
        return self._story_id, self._content

    def get_report(self, session_id, media_type="application/pdf"):
        self._call_order.append("get_report")
        self.get_report_calls.append((session_id, media_type))
        return self._report_url


class _FakeMitraSessions:
    def __init__(self, call_order: List[str]):
        self._call_order = call_order
        self.close_calls: List[uuid.UUID] = []

    def close(self, conversation_id):
        self._call_order.append("close")
        self.close_calls.append(conversation_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_agent_row(session, key: str) -> uuid.UUID:
    row = session.execute(text("""
        INSERT INTO agents (key, name, description, agent_type)
        VALUES (:key, :key, 'test agent', 'remote_flow') RETURNING id
    """), {"key": key}).fetchone()
    return row[0]


def _new_user(token: Optional[str] = "user-jwt-token") -> UserContext:
    return UserContext(
        user_id=f"user_{uuid.uuid4().hex[:8]}",
        email="test@example.com",
        display_name="Test User",
        tenant_code=f"TENANT_{uuid.uuid4().hex[:8]}",
        token=token,
    )


def _new_conversation(session, user=None) -> uuid.UUID:
    repo = ConversationRepository(session)
    conv = repo.get_or_create(None, user or _new_user())
    return conv.id


@dataclass
class _Agent:
    id: uuid.UUID
    spec: RemoteFlowAgentSpec
    checksum: str = "test-checksum"

    @property
    def key(self) -> str:
        return self.spec.key


def _remote_agent(agent_id: uuid.UUID) -> _Agent:
    remote = RemoteSpec(
        provider="mitra",
        flow_name="guest-mi-story",
        bot_route_env="TEST_BOT_ROUTE",
        company_env="TEST_COMPANY",
        report_media_type="application/pdf",
    )
    spec = RemoteFlowAgentSpec(
        key="record_stories",
        name="Record Stories",
        description="test",
        agent_type="remote_flow",
        routing=RoutingSpec(pin_session=True, exit_keywords=["/exit"]),
        remote=remote,
    )
    return _Agent(id=agent_id, spec=spec)


def _session_ready_for_finalizing(session, conv_id, agent_id) -> "AgentSessionDTO":
    """Drives a real agent_sessions row up to 'awaiting_user' with
    remote_session_id/remote_profile_id/language set -- the real precondition
    for claim_finalizing to succeed."""
    from src.services.session_service import SessionService
    svc = SessionService(session)
    repo = AgentSessionRepository(session)
    pending = repo.create_pending(conv_id, agent_id)
    authing = svc.apply(pending, SessionDelta(
        state=SessionState.authenticating,
        remote_session_id=f"remote-{uuid.uuid4().hex[:8]}",
        remote_profile_id=f"profile-{uuid.uuid4().hex[:8]}",
    ))
    in_progress = svc.apply(authing, SessionDelta(state=SessionState.in_progress))
    awaiting = svc.apply(in_progress, SessionDelta(state=SessionState.awaiting_user, step=5))
    return awaiting


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_won_claim_runs_full_sequence_in_order():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        conv_repo = ConversationRepository(session)
        conv_repo.pin(conv_id, agent_id)

        sess_view = _session_ready_for_finalizing(session, conv_id, agent_id)
        session.commit()

        call_order: List[str] = []
        rest = _FakeMitraRest(call_order)
        pool = _FakeMitraSessions(call_order)
        orch = OrchestrationService(
            session=session, registry=None, handler_factory=None, llm_factory=None,
            mitra_rest=rest, mitra_sessions=pool,
        )

        user = _new_user(token="the-real-token")
        agent = _remote_agent(agent_id)
        result = orch._finalize(sess_view, agent, user)
        session.commit()

        # Ordering: close BEFORE finalize.
        assert call_order.index("close") < call_order.index("finalize")

        # finalize() called with the right args.
        assert len(rest.finalize_calls) == 1
        called_sid, called_pid, called_flow, called_lang, called_token = rest.finalize_calls[0]
        assert called_sid == sess_view.remote_session_id
        assert called_pid == sess_view.remote_profile_id
        assert called_flow == "guest-mi-story"
        assert called_lang == sess_view.language
        assert called_token == "the-real-token"

        # Resulting session is completed with result_ref/finalized_at/ended_at.
        assert result.state == "completed"
        assert result.result_ref == "9931"
        assert result.finalized_at is not None
        assert result.ended_at is not None

        # Conversation unpinned.
        fresh_conv = session.execute(select(Conversation).where(Conversation.id == conv_id)).scalar_one()
        assert fresh_conv.pinned_agent_id is None

        # Audit row written.
        audit_rows = session.execute(text(
            "SELECT action, entity_type, entity_id FROM audit_logs WHERE entity_id = :id"
        ), {"id": result.id}).fetchall()
        assert len(audit_rows) == 1
        assert audit_rows[0][0] == "session_finalize"
        assert audit_rows[0][1] == "agent_session"
    finally:
        session.close()


def test_report_url_set_when_present():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        sess_view = _session_ready_for_finalizing(session, conv_id, agent_id)
        session.commit()

        call_order: List[str] = []
        rest = _FakeMitraRest(call_order)
        rest._report_url = "https://example.com/story.pdf"
        pool = _FakeMitraSessions(call_order)
        orch = OrchestrationService(
            session=session, registry=None, handler_factory=None, llm_factory=None,
            mitra_rest=rest, mitra_sessions=pool,
        )

        result = orch._finalize(sess_view, _remote_agent(agent_id), _new_user())
        session.commit()

        assert result.report_url == "https://example.com/story.pdf"
    finally:
        session.close()


def test_report_url_left_null_when_absent():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        sess_view = _session_ready_for_finalizing(session, conv_id, agent_id)
        session.commit()

        call_order: List[str] = []
        rest = _FakeMitraRest(call_order)  # _report_url stays None
        pool = _FakeMitraSessions(call_order)
        orch = OrchestrationService(
            session=session, registry=None, handler_factory=None, llm_factory=None,
            mitra_rest=rest, mitra_sessions=pool,
        )

        result = orch._finalize(sess_view, _remote_agent(agent_id), _new_user())
        session.commit()

        assert result.report_url is None
        assert result.state == "completed"  # still completed, just no report yet
    finally:
        session.close()


def test_finalize_exception_transitions_to_failed_and_propagates():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        sess_view = _session_ready_for_finalizing(session, conv_id, agent_id)
        session.commit()

        call_order: List[str] = []
        rest = _FakeMitraRest(call_order)
        rest._finalize_error = RuntimeError("upstream exploded")
        pool = _FakeMitraSessions(call_order)
        orch = OrchestrationService(
            session=session, registry=None, handler_factory=None, llm_factory=None,
            mitra_rest=rest, mitra_sessions=pool,
        )

        with pytest.raises(RuntimeError, match="upstream exploded"):
            orch._finalize(sess_view, _remote_agent(agent_id), _new_user())
        session.commit()

        fresh = AgentSessionRepository(session).get(sess_view.id)
        assert fresh.state == "failed"
        assert fresh.error == "upstream exploded"
    finally:
        session.close()


def test_losing_claim_returns_cached_state_without_calling_finalize():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        sess_view = _session_ready_for_finalizing(session, conv_id, agent_id)
        session.commit()

        # Simulate another request already winning the claim.
        from src.services.session_service import SessionService
        SessionService(session).claim_finalizing(sess_view.id)
        session.commit()

        call_order: List[str] = []
        rest = _FakeMitraRest(call_order)
        pool = _FakeMitraSessions(call_order)
        orch = OrchestrationService(
            session=session, registry=None, handler_factory=None, llm_factory=None,
            mitra_rest=rest, mitra_sessions=pool,
        )

        result = orch._finalize(sess_view, _remote_agent(agent_id), _new_user())

        assert result.state == "finalizing"
        assert len(rest.finalize_calls) == 0
        assert len(pool.close_calls) == 0

        audit_rows = session.execute(text(
            "SELECT count(*) FROM audit_logs WHERE entity_id = :id"
        ), {"id": sess_view.id}).scalar()
        assert audit_rows == 0
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Regression: a real bug found in production -- a handler returning
# terminal=True with state=finalizing IN ITS OWN DELTA (rather than leaving
# the delta at awaiting_user and letting terminal=True alone drive
# _finalize()) wedges the session in 'finalizing' forever. handle_turn()'s
# own step 11 applies the handler's delta BEFORE calling _finalize(), so
# claim_finalizing() -- claimable only from {in_progress, awaiting_user} --
# finds the session already 'finalizing' and can never claim it. Every test
# above calls orch._finalize() directly, bypassing handle_turn() entirely,
# so none of them exercise the interaction between step 11's apply() and
# _finalize() at all.
#
# This test alone does NOT catch a regression of the original bug -- it uses
# a fake handler that always emits the correct contract, so it only proves
# handle_turn() correctly drives a well-behaved handler's delta through to
# 'completed'. The other half -- proving RemoteFlowAgentHandler itself emits
# that correct contract -- is
# tests/unit/test_remote_flow_handler.py::test_completed_session_returns_terminal_true_and_awaiting_user_state.
# The two together are what would have caught this before it shipped.
# ---------------------------------------------------------------------------


class _FakeTerminalHandler:
    """A handler that DOES follow the correct contract: terminal=True with
    session_delta.state left at awaiting_user. This is what
    RemoteFlowAgentHandler.handle() must produce (design doc §4.7)."""
    def handle(self, ctx):
        from src.agents.protocol import AgentTurn
        return AgentTurn(
            text="Thank you, your story is complete.",
            terminal=True,
            session_delta=SessionDelta(state=SessionState.awaiting_user, step=99),
        )


class _FakeHandlerFactory:
    def __init__(self, handler):
        self._handler = handler

    def build(self, spec, checksum):
        return self._handler


class _FakeRouter:
    def __init__(self, agent):
        self._agent = agent

    def select(self, conv, ctx, explicit_key=None):
        from src.services.router_service import RouteDecision
        return RouteDecision(agent=self._agent, reason="pinned", confidence=1.0, router_latency_ms=0)


class _FakeRegistry:
    """handle_turn() calls registry.default() unconditionally while building
    router history context, even though this test's router never needs it."""
    def default(self):
        return None


def test_handle_turn_with_terminal_delta_actually_reaches_completed():
    """The concrete end-to-end regression test for the bug described above."""
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        user = _new_user(token="the-real-token")
        conv_id = _new_conversation(session, user=user)
        conv_repo = ConversationRepository(session)
        conv_repo.pin(conv_id, agent_id)

        sess_view = _session_ready_for_finalizing(session, conv_id, agent_id)
        session.commit()

        call_order: List[str] = []
        rest = _FakeMitraRest(call_order)
        pool = _FakeMitraSessions(call_order)
        agent = _remote_agent(agent_id)

        orch = OrchestrationService(
            session=session,
            registry=_FakeRegistry(),
            handler_factory=_FakeHandlerFactory(_FakeTerminalHandler()),
            llm_factory=None,
            router_service=_FakeRouter(agent),
            mitra_rest=rest,
            mitra_sessions=pool,
        )

        from src.services.orchestration import TurnInput
        ctx_in = TurnInput(
            request_id="req-1", conversation_id=conv_id, user=user, text="last answer",
        )
        result = orch.handle_turn(ctx_in)
        session.commit()

        assert result.session is not None
        assert result.session.state == "completed", (
            "a handler reporting terminal=True must actually reach 'completed', "
            "not get stuck in 'finalizing'"
        )
        assert result.session.result_ref == "9931"
        assert len(rest.finalize_calls) == 1

        fresh = AgentSessionRepository(session).get(sess_view.id)
        assert fresh.state == "completed"
    finally:
        session.close()
