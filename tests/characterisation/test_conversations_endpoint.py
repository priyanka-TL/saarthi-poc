"""GET /api/conversations and GET /api/conversations/<id>/messages -- power
the sidebar's recent-conversations list and resuming one into the chat pane.

Real Flask app + real Postgres, matching this directory's established
convention (see test_session_routes.py). Identity is controlled by
monkeypatching StaticTokenUserProvider.get_user (same pattern as
tests/integration/test_config_versioning_lifecycle.py's admin_client
fixture), so tests don't depend on a real LLM call just to resolve "who am I."
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.db.engine import SessionLocal
from src.domain.core import UserContext


def _seed_conversation(user: UserContext, title, last_message_at, message_count=1) -> uuid.UUID:
    db = SessionLocal()
    try:
        conv_id = uuid.uuid4()
        db.execute(text("""
            INSERT INTO conversations
                (id, tenant_code, external_user_id, title, status, locale, message_count, metadata, last_message_at)
            VALUES
                (:id, :tenant_code, :external_user_id, :title, 'active', 'en', :message_count, '{}', :last_message_at)
        """), {
            "id": conv_id, "tenant_code": user.tenant_code, "external_user_id": user.user_id,
            "title": title, "message_count": message_count, "last_message_at": last_message_at,
        })
        db.commit()
        return conv_id
    finally:
        db.close()


def _general_support_agent() -> tuple[uuid.UUID, str]:
    """Reuses the real, permanent general_support agent as the FK target for
    a seeded assistant message -- deliberately NOT a throwaway inserted row.
    A fixed-name throwaway (`agents.name` is unique) collides with itself on
    a second test run and, if left uncleaned, accumulates cruft exactly like
    an earlier, unrelated test-hygiene bug in test_admin_routes.py already
    did once this session. Reusing a real agent sidesteps both problems."""
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT id, name FROM agents WHERE key = 'general_support'")).fetchone()
        assert row is not None, "general_support agent must exist (src/config/agents/general_support.yaml)"
        return row.id, row.name
    finally:
        db.close()


def _seed_message(
    conversation_id: uuid.UUID, seq: int, role: str, content: str,
    agent_id=None, options=None, selected_option_id=None,
) -> uuid.UUID:
    db = SessionLocal()
    try:
        msg_id = uuid.uuid4()
        db.execute(text("""
            INSERT INTO conversation_messages
                (id, conversation_id, seq, role, content, agent_id, options, selected_option_id)
            VALUES
                (:id, :conversation_id, :seq, :role, :content, :agent_id, :options, :selected_option_id)
        """), {
            "id": msg_id, "conversation_id": conversation_id, "seq": seq, "role": role, "content": content,
            "agent_id": agent_id, "options": json.dumps(options) if options is not None else None,
            "selected_option_id": selected_option_id,
        })
        db.commit()
        return msg_id
    finally:
        db.close()


@pytest.fixture()
def as_user(monkeypatch):
    """Authenticate the test client as a fresh, controlled identity."""
    def _patch(user: UserContext):
        monkeypatch.setattr(
            "src.services.identity.StaticTokenUserProvider.get_user",
            lambda self, request: user,
        )
        return user
    return _patch


def _new_user(**overrides) -> UserContext:
    defaults = dict(
        user_id=f"user_{uuid.uuid4().hex[:8]}",
        email="test@example.com",
        display_name="Test User",
        tenant_code=f"TENANT_{uuid.uuid4().hex[:8]}",
    )
    defaults.update(overrides)
    return UserContext(**defaults)


def test_returns_own_conversations_newest_first_capped_at_limit(client, as_user):
    user = _new_user()
    as_user(user)

    now = datetime.now(timezone.utc)
    ids_oldest_to_newest = [
        _seed_conversation(user, f"conv {i}", now - timedelta(minutes=(6 - i)))
        for i in range(6)
    ]

    response = client.get("/api/conversations?limit=5")
    body = response.get_json()

    assert response.status_code == 200
    returned_ids = [c["id"] for c in body["conversations"]]
    assert len(returned_ids) == 5
    # newest first -> the 5 most recently seeded, in reverse seed order
    expected = [str(i) for i in reversed(ids_oldest_to_newest[1:])]
    assert returned_ids == expected


def test_title_falls_back_when_null(client, as_user):
    user = _new_user()
    as_user(user)
    _seed_conversation(user, None, datetime.now(timezone.utc))

    body = client.get("/api/conversations").get_json()

    assert body["conversations"][0]["title"] == "New conversation"


def test_does_not_leak_another_users_conversations(client, as_user):
    owner = _new_user()
    other = _new_user()
    _seed_conversation(owner, "owner's conversation", datetime.now(timezone.utc))

    as_user(other)
    body = client.get("/api/conversations").get_json()

    assert body["conversations"] == []


def test_archived_conversations_are_excluded(client, as_user):
    user = _new_user()
    as_user(user)
    conv_id = _seed_conversation(user, "will be archived", datetime.now(timezone.utc))

    db = SessionLocal()
    try:
        db.execute(text("UPDATE conversations SET status = 'archived' WHERE id = :id"), {"id": conv_id})
        db.commit()
    finally:
        db.close()

    body = client.get("/api/conversations").get_json()

    assert body["conversations"] == []


# ---------------------------------------------------------------------------
# GET /api/conversations/<id>/messages
# ---------------------------------------------------------------------------


def test_messages_returned_in_order_with_agent_name_and_options(client, as_user):
    user = _new_user()
    as_user(user)
    conv_id = _seed_conversation(user, "hello there", datetime.now(timezone.utc))
    agent_id, agent_name = _general_support_agent()

    _seed_message(conv_id, 1, "user", "hello there")
    _seed_message(
        conv_id, 2, "assistant", "Pick one",
        agent_id=agent_id,
        options=[{"id": "opt_1", "label": "English", "value": "en"},
                 {"id": "opt_2", "label": "Hindi", "value": "hi"}],
        selected_option_id="opt_2",
    )
    _seed_message(conv_id, 3, "user", "Hindi please")

    response = client.get(f"/api/conversations/{conv_id}/messages")
    body = response.get_json()

    assert response.status_code == 200
    assert body["conversation_id"] == str(conv_id)
    msgs = body["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert [m["content"] for m in msgs] == ["hello there", "Pick one", "Hindi please"]

    assistant_msg = msgs[1]
    assert assistant_msg["agent_name"] == agent_name
    assert assistant_msg["options"] == [
        {"id": "opt_1", "label": "English", "value": "en"},
        {"id": "opt_2", "label": "Hindi", "value": "hi"},
    ]
    assert assistant_msg["selected_option_id"] == "opt_2"

    user_msg = msgs[0]
    assert user_msg["agent_name"] is None
    assert user_msg["options"] is None


def test_messages_404_for_unknown_conversation(client, as_user):
    as_user(_new_user())

    response = client.get(f"/api/conversations/{uuid.uuid4()}/messages")

    assert response.status_code == 404


def test_messages_404_for_another_users_conversation(client, as_user):
    owner = _new_user()
    conv_id = _seed_conversation(owner, "owner's conversation", datetime.now(timezone.utc))
    _seed_message(conv_id, 1, "user", "secret")

    as_user(_new_user())
    response = client.get(f"/api/conversations/{conv_id}/messages")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Session replay on resume.
#
# The "story is ready + download report" notice is generated client-side from
# the /api/chat response; it is NOT a stored message. Replaying only the
# transcript therefore lost the report link on every page reload. The messages
# endpoint returns the conversation's latest session so the client can rebuild
# that state from server truth.
# ---------------------------------------------------------------------------

def _seed_session(conversation_id: uuid.UUID, agent_id, state: str, **fields) -> uuid.UUID:
    db = SessionLocal()
    try:
        sess_id = uuid.uuid4()
        terminal = state in ("completed", "failed", "abandoned")
        db.execute(text("""
            INSERT INTO agent_sessions
                (id, conversation_id, agent_id, state, step, result_ref, report_url,
                 remote_provider, remote_session_id, remote_profile_id,
                 started_at, finalized_at, ended_at)
            VALUES
                (:id, :conversation_id, :agent_id, :state, :step, :result_ref, :report_url,
                 'mitra', :remote_session_id, :remote_profile_id,
                 :started_at, :finalized_at, :ended_at)
        """), {
            # ck sess_active_has_remote: any non-pending/failed/abandoned state
            # must carry remote_session_id. ck sess_completed_has_result: a
            # completed session must carry result_ref.
            "remote_session_id": f"remote-{uuid.uuid4().hex[:8]}",
            "remote_profile_id": "380",
            "id": sess_id,
            "conversation_id": conversation_id,
            "agent_id": agent_id,
            "state": state,
            "step": fields.get("step", 0),
            "result_ref": fields.get("result_ref"),
            "report_url": fields.get("report_url"),
            "started_at": fields.get("started_at", datetime.now(timezone.utc)),
            # ck_agent_sess constraints: terminal states require ended_at.
            "finalized_at": datetime.now(timezone.utc) if terminal else None,
            "ended_at": datetime.now(timezone.utc) if terminal else None,
        })
        db.commit()
        return sess_id
    finally:
        db.close()


def test_messages_returns_completed_session_so_a_reload_keeps_the_report_link(client, as_user):
    """THE regression test: refresh must not lose the download button."""
    user = _new_user()
    as_user(user)
    conv_id = _seed_conversation(user, "my story", datetime.now(timezone.utc))
    agent_id, _ = _general_support_agent()
    _seed_message(conv_id, 1, "user", "record a story")

    url = "https://qa-mohini-static.example.org/chatbot/storymedia/4119/story.pdf"
    sess_id = _seed_session(
        conv_id, agent_id, "completed", step=16, result_ref="4119", report_url=url,
    )

    body = client.get(f"/api/conversations/{conv_id}/messages").get_json()

    assert body["session"] is not None, "a reload has no other way to recover the report link"
    assert body["session"]["id"] == str(sess_id)
    assert body["session"]["state"] == "completed"
    assert body["session"]["report_url"] == url
    assert body["session"]["result_ref"] == "4119"
    assert body["session"]["step"] == 16


def test_messages_returns_null_session_for_a_conversation_that_never_had_one(client, as_user):
    """Plain LLM conversations have no session; the key must still be present
    and null so the client can branch on it without guarding for undefined."""
    user = _new_user()
    as_user(user)
    conv_id = _seed_conversation(user, "just chatting", datetime.now(timezone.utc))
    _seed_message(conv_id, 1, "user", "hello")

    body = client.get(f"/api/conversations/{conv_id}/messages").get_json()

    assert "session" in body
    assert body["session"] is None


def test_messages_returns_the_latest_session_not_the_first(client, as_user):
    """A conversation can hold several sessions over its life; resuming must
    reflect the most recent one, not whichever was inserted first."""
    user = _new_user()
    as_user(user)
    conv_id = _seed_conversation(user, "two stories", datetime.now(timezone.utc))
    agent_id, _ = _general_support_agent()

    older = datetime.now(timezone.utc) - timedelta(hours=2)
    _seed_session(conv_id, agent_id, "abandoned", started_at=older)
    newest_id = _seed_session(
        conv_id, agent_id, "completed", result_ref="4119",
        report_url="https://static.example.org/x.pdf",
        started_at=datetime.now(timezone.utc),
    )

    body = client.get(f"/api/conversations/{conv_id}/messages").get_json()

    assert body["session"]["id"] == str(newest_id)
    assert body["session"]["result_ref"] == "4119"


def test_session_is_not_exposed_across_tenants(client, as_user):
    """The existing 404 scoping must still gate the new field -- it carries a
    report URL, so leaking it would be worse than leaking the transcript."""
    owner = _new_user()
    as_user(owner)
    conv_id = _seed_conversation(owner, "private story", datetime.now(timezone.utc))
    agent_id, _ = _general_support_agent()
    _seed_session(
        conv_id, agent_id, "completed", result_ref="4119",
        report_url="https://static.example.org/secret.pdf",
    )

    as_user(_new_user())
    response = client.get(f"/api/conversations/{conv_id}/messages")

    assert response.status_code == 404
    assert "secret.pdf" not in response.get_data(as_text=True)
