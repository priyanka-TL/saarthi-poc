"""GET /api/conversations -- powers the sidebar's recent-conversations list.

Real Flask app + real Postgres, matching this directory's established
convention (see test_session_routes.py). Identity is controlled by
monkeypatching StaticTokenUserProvider.get_user (same pattern as
tests/integration/test_config_versioning_lifecycle.py's admin_client
fixture), so tests don't depend on a real LLM call just to resolve "who am I."
"""
from __future__ import annotations

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
