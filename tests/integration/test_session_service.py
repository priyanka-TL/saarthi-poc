import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text, select

from src.db.engine import SessionLocal
from src.db.models import Conversation
from src.domain.core import UserContext
from src.domain.agent_spec import RemoteFlowAgentSpec, RemoteSpec, RoutingSpec
from src.agents.protocol import SessionDelta, SessionState
from src.repositories.conversations import ConversationRepository
from src.repositories.sessions import AgentSessionRepository
from src.services.session_service import (
    SessionService,
    InvalidTransitionError,
)


def _insert_agent_row(session, key: str) -> uuid.UUID:
    row = session.execute(text("""
        INSERT INTO agents (key, name, description, agent_type)
        VALUES (:key, :key, 'test agent', 'remote_flow') RETURNING id
    """), {"key": key}).fetchone()
    return row[0]


class _RegisteredAgentStub:
    """Duck-types src.services.agent_registry.RegisteredAgent's fields that
    SessionService.open_for actually reads (id, spec.routing.pin_session)."""
    def __init__(self, agent_id: uuid.UUID, pin_session: bool):
        self.id = str(agent_id)
        self.spec = RemoteFlowAgentSpec(
            key="test_remote_flow",
            name="Test Remote Flow",
            description="test agent for session service tests",
            agent_type="remote_flow",
            routing=RoutingSpec(pin_session=pin_session, exit_keywords=["/exit"]),
            remote=RemoteSpec(provider="mitra", flow_name="guest-mi-story", bot_route_env="TEST_BOT_ROUTE_ENV"),
        )


def _new_user() -> UserContext:
    return UserContext(
        user_id=f"user_{uuid.uuid4().hex[:8]}",
        email="test@example.com",
        display_name="Test User",
        tenant_code=f"TENANT_{uuid.uuid4().hex[:8]}",
    )


def _new_conversation(session) -> uuid.UUID:
    repo = ConversationRepository(session)
    conv = repo.get_or_create(None, _new_user())
    return conv.id


# ---------------------------------------------------------------------------
# open_for
# ---------------------------------------------------------------------------


def test_open_for_creates_pending_when_pin_session():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        session.commit()

        svc = SessionService(session)
        agent = _RegisteredAgentStub(agent_id, pin_session=True)
        result = svc.open_for(conv_id, agent)
        session.commit()

        assert result is not None
        assert result.state == "pending"
        assert result.agent_id == agent_id
        assert result.conversation_id == conv_id
    finally:
        session.close()


def test_open_for_returns_none_without_pin_session_and_no_open_session():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        session.commit()

        svc = SessionService(session)
        agent = _RegisteredAgentStub(agent_id, pin_session=False)
        assert svc.open_for(conv_id, agent) is None
    finally:
        session.close()


def test_open_for_attaches_existing_open_session():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        svc = SessionService(session)
        agent = _RegisteredAgentStub(agent_id, pin_session=True)

        first = svc.open_for(conv_id, agent)
        session.commit()

        second = svc.open_for(conv_id, agent)
        assert second is not None
        assert second.id == first.id
    finally:
        session.close()


# ---------------------------------------------------------------------------
# apply(): invalid transitions rejected, same-state updates allowed
# ---------------------------------------------------------------------------


def test_apply_rejects_invalid_transition():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        repo = AgentSessionRepository(session)
        pending = repo.create_pending(conv_id, agent_id)
        session.commit()

        svc = SessionService(session)
        with pytest.raises(InvalidTransitionError):
            svc.apply(pending, SessionDelta(state=SessionState.completed, result_ref="9931"))
    finally:
        session.close()


def test_apply_rejects_transition_from_terminal_state():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        repo = AgentSessionRepository(session)
        pending = repo.create_pending(conv_id, agent_id)
        svc = SessionService(session)
        failed = svc.apply(pending, SessionDelta(state=SessionState.failed))
        session.commit()

        with pytest.raises(InvalidTransitionError):
            svc.apply(failed, SessionDelta(state=SessionState.failed))
    finally:
        session.close()


def test_apply_allows_same_state_field_only_update():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        repo = AgentSessionRepository(session)
        pending = repo.create_pending(conv_id, agent_id)
        session.commit()

        svc = SessionService(session)
        remote_id = f"remote-{uuid.uuid4().hex}"
        authing = svc.apply(pending, SessionDelta(state=SessionState.authenticating, remote_session_id=remote_id, remote_profile_id="42"))
        in_progress = svc.apply(authing, SessionDelta(state=SessionState.in_progress))
        awaiting = svc.apply(in_progress, SessionDelta(state=SessionState.awaiting_user, step=1))
        again = svc.apply(awaiting, SessionDelta(state=SessionState.awaiting_user, step=2))
        session.commit()

        assert again.state == "awaiting_user"
        assert again.step == 2
        assert again.turn_count == 4  # bumped once per apply() call (4 calls made above)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# claim_finalizing: concurrency
# ---------------------------------------------------------------------------


def test_concurrent_claim_finalizing_exactly_one_wins():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        repo = AgentSessionRepository(session)
        pending = repo.create_pending(conv_id, agent_id)
        svc = SessionService(session)
        remote_id = f"remote-{uuid.uuid4().hex}"
        authing = svc.apply(pending, SessionDelta(state=SessionState.authenticating, remote_session_id=remote_id))
        in_progress = svc.apply(authing, SessionDelta(state=SessionState.in_progress))
        awaiting = svc.apply(in_progress, SessionDelta(state=SessionState.awaiting_user, step=1))
        session.commit()
        session_id = awaiting.id
    finally:
        session.close()

    def try_claim():
        thread_session = SessionLocal()
        try:
            thread_svc = SessionService(thread_session)
            result = thread_svc.claim_finalizing(session_id)
            thread_session.commit()
            return result
        finally:
            thread_session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(try_claim) for _ in range(2)]
        results = [f.result() for f in futures]

    winners = [r for r in results if r is not None]
    losers = [r for r in results if r is None]
    assert len(winners) == 1, "exactly one concurrent claim_finalizing call must succeed"
    assert len(losers) == 1
    assert winners[0].state == "finalizing"

    # The loser's caller would now fetch the cached result via get().
    verify = SessionLocal()
    try:
        cached = AgentSessionRepository(verify).get(session_id)
        assert cached.state == "finalizing"
    finally:
        verify.close()


def test_claim_finalizing_returns_none_when_not_claimable():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        repo = AgentSessionRepository(session)
        pending = repo.create_pending(conv_id, agent_id)  # state == 'pending', not claimable
        session.commit()

        svc = SessionService(session)
        assert svc.claim_finalizing(pending.id) is None
    finally:
        session.close()


# ---------------------------------------------------------------------------
# abandon(): unpins the conversation in the same transaction
# ---------------------------------------------------------------------------


def test_abandon_unpins_conversation_same_transaction():
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        conv_repo = ConversationRepository(session)
        conv_repo.pin(conv_id, agent_id)

        repo = AgentSessionRepository(session)
        pending = repo.create_pending(conv_id, agent_id)
        svc = SessionService(session)
        remote_id = f"remote-{uuid.uuid4().hex}"
        authing = svc.apply(pending, SessionDelta(state=SessionState.authenticating, remote_session_id=remote_id))
        in_progress = svc.apply(authing, SessionDelta(state=SessionState.in_progress))
        svc.apply(in_progress, SessionDelta(state=SessionState.awaiting_user, step=1))
        session.commit()

        abandoned = svc.abandon(conv_id, reason="/exit")
        session.commit()

        assert abandoned is not None
        assert abandoned.state == "abandoned"
        assert abandoned.ended_at is not None
    finally:
        session.close()

    # Re-read with a FRESH session/connection to prove it's really committed,
    # not just visible in-process.
    verify = SessionLocal()
    try:
        conv = verify.execute(select(Conversation).where(Conversation.id == conv_id)).scalar_one()
        assert conv.pinned_agent_id is None

        sess_row = AgentSessionRepository(verify).get(abandoned.id)
        assert sess_row.state == "abandoned"

        audit_rows = verify.execute(text(
            "SELECT action, entity_type, entity_id, note FROM audit_logs WHERE entity_id = :id"
        ), {"id": abandoned.id}).fetchall()
        assert len(audit_rows) == 1
        assert audit_rows[0][0] == "session_abandon"
        assert audit_rows[0][1] == "agent_session"
        assert audit_rows[0][3] == "/exit"
    finally:
        verify.close()


def test_abandon_returns_none_when_no_open_session():
    session = SessionLocal()
    try:
        conv_id = _new_conversation(session)
        session.commit()

        svc = SessionService(session)
        assert svc.abandon(conv_id, reason="/exit") is None
    finally:
        session.close()


def test_abandon_works_from_in_progress_not_just_awaiting_user():
    """Abandon is an escape hatch reachable from ANY non-terminal state, per
    §6.5's pin lifecycle diagram (pinned --> abandoned drawn from the whole
    composite state) -- unlike apply()'s strict per-state map, which would
    reject in_progress -> abandoned."""
    session = SessionLocal()
    try:
        agent_id = _insert_agent_row(session, f"agent_{uuid.uuid4().hex[:8]}")
        conv_id = _new_conversation(session)
        repo = AgentSessionRepository(session)
        pending = repo.create_pending(conv_id, agent_id)
        svc = SessionService(session)
        remote_id = f"remote-{uuid.uuid4().hex}"
        authing = svc.apply(pending, SessionDelta(state=SessionState.authenticating, remote_session_id=remote_id))
        in_progress = svc.apply(authing, SessionDelta(state=SessionState.in_progress))
        session.commit()
        assert in_progress.state == "in_progress"

        abandoned = svc.abandon(conv_id, reason="crash recovery")
        session.commit()

        assert abandoned is not None
        assert abandoned.state == "abandoned"
    finally:
        session.close()
