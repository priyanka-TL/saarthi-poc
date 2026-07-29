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
