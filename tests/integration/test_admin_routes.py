import pytest
import uuid
import json
from unittest.mock import patch
from flask import Flask, g
from sqlalchemy import text

from src.app_factory import create_app
from src.settings import Settings
from src.domain.core import UserContext, OrgMembership

@pytest.fixture
def db(flask_app):
    from src.db.engine import SessionLocal
    session = SessionLocal()
    yield session
    session.close()

def _make_agent_and_config(db, name_prefix="TestAdminAgent"):
    agent_id = uuid.uuid4()
    agent_key = f"test_agent_{agent_id.hex[:6]}"
    
    db.execute(
        text("INSERT INTO agents (id, key, name, description, agent_type, status) VALUES (:id, :key, :name, 'desc', 'llm', 'enabled')"),
        {"id": agent_id, "key": agent_key, "name": f"{name_prefix}_{agent_id.hex[:6]}"}
    )
    
    config = {
        "key": agent_key,
        "name": "Test",
        "description": "Test",
        "agent_type": "llm",
        "model": {"name": "gpt-4o"},
        "prompt": "You are a test. Secret: ${SUPER_SECRET_KEY}",
        "tools": []
    }
    
    db.execute(
        text("INSERT INTO agent_configurations (agent_id, version, config, checksum, source, is_active, activated_at) VALUES (:id, 1, :config, 'chk', 'db', TRUE, now())"),
        {"id": agent_id, "config": json.dumps(config)}
    )
    db.commit()
    return agent_id, agent_key

def test_admin_gate(flask_app, client):
    flask_app.config["CONTAINER"].settings.saarthi_admin_enabled = 1
    # Without setting g.user to an admin, it should return 403 (since saarthi_admin_enabled=1)
    response = client.get("/api/tools")
    assert response.status_code == 403

def test_get_agent_redacts_secrets(flask_app, client, db):
    flask_app.config["CONTAINER"].settings.saarthi_admin_enabled = 1
    agent_id, key = _make_agent_and_config(db, "Redact")
    
    # Reload registry
    container = flask_app.config["CONTAINER"]
    container.agent_registry.reload(db)

@patch("src.services.identity.StaticTokenUserProvider.get_user")
def test_admin_endpoints(mock_get_user, flask_app, db):
    flask_app.config["CONTAINER"].settings.saarthi_admin_enabled = 1
    
    mock_user = UserContext(
        user_id="u", email="e", display_name="n", tenant_code="t",
        orgs=(OrgMembership(org_id="o", org_code="o", roles=("admin",)),),
        active_org_id="o"
    )
    mock_get_user.return_value = mock_user
    
    client = flask_app.test_client()
    agent_id, key = _make_agent_and_config(db, "End")
    container = flask_app.config["CONTAINER"]
    container.agent_registry.reload(db)
    
    # 1. Redaction of ${...}
    res = client.get(f"/api/agents/{key}")
    assert res.status_code == 200
    data = res.get_json()
    assert "${SUPER_SECRET_KEY}" not in data["config"]["prompt"]
    assert data["config"]["prompt"] == "<REDACTED>" # Since _redact_secrets replaces the whole string

    # 2. Disabling an agent takes effect and bumps version
    initial_version = container.agent_registry.version
    res = client.patch(f"/api/agents/{key}", json={"status": "disabled"})
    assert res.status_code == 200
    assert res.get_json()["registry_version"] > initial_version
    
    # Check DB
    row = db.execute(text("SELECT status FROM agents WHERE id = :id"), {"id": agent_id}).fetchone()
    assert row.status == "disabled"

    # 3. Invalid config returns 422 with field path
    invalid_config = {
        "key": key,
        "name": "Updated",
        "description": "Updated",
        "agent_type": "llm",
        "model": {"name": "gpt-4o"},
        "prompt": "valid",
        "routing": {
            "router_selectable": "NOT_A_BOOLEAN"  # invalid!
        }
    }
    res = client.post(f"/api/agents/{key}/config", json=invalid_config)
    assert res.status_code == 422
    err_data = res.get_json()
    assert err_data["error"] == "CONFIG_INVALID"
    assert err_data["path"] == ["llm", "routing", "router_selectable"]
