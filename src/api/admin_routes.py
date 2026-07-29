import time
from typing import Dict, Any

from flask import Blueprint, jsonify, request, g, current_app
from sqlalchemy import text
from pydantic import TypeAdapter, ValidationError

from src.domain.agent_spec import AgentSpec, canonical_json
from src.repositories.audit import AuditLogRepository

admin_bp = Blueprint("admin_routes", __name__)

@admin_bp.before_request
def check_admin_access():
    container = current_app.config["CONTAINER"]
    if not container.settings.saarthi_admin_enabled:
        return jsonify({"error": "Not Found"}), 404
        
    if not hasattr(g, "user") or "admin" not in getattr(g.user, "roles", []):
        return jsonify({"error": "Forbidden"}), 403

def _redact_secrets(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: _redact_secrets(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_redact_secrets(i) for i in data]
    elif isinstance(data, str) and "${" in data:
        return "<REDACTED>"
    return data

@admin_bp.route("/api/agents/<key>", methods=["GET"])
def get_agent_detail(key: str):
    db_session = g.db_session
    row = db_session.execute(
        text("""
            SELECT a.id, a.name, a.description, a.agent_type, a.status, 
                   c.config, c.version, c.source, c.created_at
            FROM agents a
            LEFT JOIN agent_configurations c ON a.id = c.agent_id AND c.is_active = TRUE
            WHERE a.key = :key
        """),
        {"key": key}
    ).fetchone()
    
    if not row:
        return jsonify({"error": "AGENT_NOT_FOUND"}), 404

    config = row.config if row.config else {}
    redacted_config = _redact_secrets(config)
    
    return jsonify({
        "id": str(row.id),
        "key": key,
        "name": row.name,
        "description": row.description,
        "agent_type": row.agent_type,
        "status": row.status,
        "active_version": row.version,
        "origin": row.source,
        "activated_at": row.created_at.isoformat() if row.created_at else None,
        "config": redacted_config
    })

@admin_bp.route("/api/agents/<key>", methods=["PATCH"])
def update_agent_status(key: str):
    db_session = g.db_session
    agent_row = db_session.execute(text("SELECT id FROM agents WHERE key = :key"), {"key": key}).fetchone()
    if not agent_row:
        return jsonify({"error": "AGENT_NOT_FOUND"}), 404
        
    body = request.get_json() or {}
    status = body.get("status")
    if status not in ("enabled", "disabled"):
        return jsonify({"error": "INVALID_REQUEST"}), 400
        
    db_session.execute(
        text("UPDATE agents SET status = :status, updated_at = now() WHERE id = :id"),
        {"status": status, "id": agent_row.id}
    )
    
    action = "agent_enable" if status == "enabled" else "agent_disable"
    audit_repo = AuditLogRepository(db_session)
    actor = g.user.user_id if hasattr(g, "user") else "system"
    audit_repo.insert(
        action=action,
        entity_type="agent",
        entity_id=agent_row.id,
        actor=actor,
        note=f"Status set to {status}"
    )
    
    db_session.commit()
    container = current_app.config["CONTAINER"]
    new_version = container.agent_registry.reload(db_session)
    
    return jsonify({"id": str(agent_row.id), "key": key, "status": status, "registry_version": new_version})

@admin_bp.route("/api/agents/<key>/config", methods=["POST"])
def create_config_version(key: str):
    db_session = g.db_session
    agent_row = db_session.execute(text("SELECT id, agent_type FROM agents WHERE key = :key"), {"key": key}).fetchone()
    if not agent_row:
        return jsonify({"error": "AGENT_NOT_FOUND"}), 404
        
    container = current_app.config["CONTAINER"]
        
    body = request.get_json()
    if not body:
        return jsonify({"error": "CONFIG_INVALID", "path": []}), 422
        
    # Validate
    try:
        spec = TypeAdapter(AgentSpec).validate_python(body)
    except ValidationError as e:
        err = e.errors()[0]
        return jsonify({"error": "CONFIG_INVALID", "path": err["loc"], "msg": err["msg"]}), 422
        
    if spec.agent_type != agent_row.agent_type:
        return jsonify({"error": "CONFIG_INVALID", "path": ["agent_type"], "msg": "Cannot change agent type"}), 422
        
    if spec.agent_type == "llm" and getattr(spec, "tools", None):
        try:
            container.tool_registry.assert_all_known(spec.tools)
        except Exception as e:
            return jsonify({"error": "CONFIG_INVALID", "path": ["tools"], "msg": str(e)}), 422
            
    canonical, checksum = canonical_json(spec)
    
    # Deactivate current
    db_session.execute(
        text("UPDATE agent_configurations SET is_active = FALSE WHERE agent_id = :agent_id"),
        {"agent_id": agent_row.id}
    )
    
    # Insert new
    new_version = db_session.execute(
        text("SELECT COALESCE(MAX(version), 0) + 1 FROM agent_configurations WHERE agent_id = :agent_id"),
        {"agent_id": agent_row.id}
    ).scalar()
    
    row = db_session.execute(
        text("""
            INSERT INTO agent_configurations (agent_id, version, config, checksum, source, is_active, activated_at)
            VALUES (:agent_id, :version, :config, :checksum, 'db', TRUE, now())
            RETURNING created_at
        """),
        {
            "agent_id": agent_row.id,
            "version": new_version,
            "config": canonical,  # a JSON string -- psycopg can't adapt a raw dict to jsonb here
            "checksum": checksum
        }
    ).fetchone()
    
    audit_repo = AuditLogRepository(db_session)
    actor = g.user.user_id if hasattr(g, "user") else "system"
    audit_repo.insert(
        action="config_create",
        entity_type="agent_configuration",
        entity_id=agent_row.id,
        actor=actor,
        note=f"Created version {new_version} for {key}"
    )
    
    db_session.commit()
    registry_version = container.agent_registry.reload(db_session)
    
    return jsonify({
        "version": new_version,
        "checksum": checksum,
        "activated_at": row.created_at.isoformat() if row.created_at else None,
        "registry_version": registry_version
    })

@admin_bp.route("/api/agents/<key>/config/versions", methods=["GET"])
def list_config_versions(key: str):
    db_session = g.db_session
    agent_row = db_session.execute(text("SELECT id FROM agents WHERE key = :key"), {"key": key}).fetchone()
    if not agent_row:
        return jsonify({"error": "AGENT_NOT_FOUND"}), 404
        
    container = current_app.config["CONTAINER"]
    rows = db_session.execute(
        text("""
            SELECT version, source, checksum, is_active, created_at
            FROM agent_configurations 
            WHERE agent_id = :agent_id
            ORDER BY version DESC
        """),
        {"agent_id": agent_row.id}
    ).fetchall()
    
    versions = []
    for r in rows:
        versions.append({
            "version": r.version,
            "origin": r.source,
            "checksum": r.checksum,
            "is_active": r.is_active,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
        
    return jsonify(versions)

@admin_bp.route("/api/agents/<key>/config/<int:version>/activate", methods=["POST"])
def activate_config_version(key: str, version: int):
    db_session = g.db_session
    agent_row = db_session.execute(text("SELECT id FROM agents WHERE key = :key"), {"key": key}).fetchone()
    if not agent_row:
        return jsonify({"error": "AGENT_NOT_FOUND"}), 404
        
    container = current_app.config["CONTAINER"]
    
    # Check if version exists
    exists = db_session.execute(
        text("SELECT 1 FROM agent_configurations WHERE agent_id = :agent_id AND version = :version"),
        {"agent_id": agent_row.id, "version": version}
    ).scalar()
    
    if not exists:
        return jsonify({"error": "INVALID_REQUEST", "msg": "Version not found"}), 400
        
    db_session.execute(
        text("UPDATE agent_configurations SET is_active = FALSE WHERE agent_id = :agent_id"),
        {"agent_id": agent_row.id}
    )
    
    db_session.execute(
        text("UPDATE agent_configurations SET is_active = TRUE, activated_at = now() WHERE agent_id = :agent_id AND version = :version"),
        {"agent_id": agent_row.id, "version": version}
    )
    
    audit_repo = AuditLogRepository(db_session)
    actor = g.user.user_id if hasattr(g, "user") else "system"
    audit_repo.insert(
        action="config_activate",
        entity_type="agent_configuration",
        entity_id=agent_row.id,
        actor=actor,
        note=f"Activated version {version} for {key}"
    )
    
    db_session.commit()
    new_version = container.agent_registry.reload(db_session)
    
    return jsonify({"version": version, "registry_version": new_version})

@admin_bp.route("/api/agents/reload", methods=["POST"])
def reload_agents():
    container = current_app.config["CONTAINER"]
    db_session = g.db_session
    new_version = container.agent_registry.reload(db_session)
    return jsonify({"registry_version": new_version})

@admin_bp.route("/api/tools", methods=["GET"])
def list_tools():
    container = current_app.config["CONTAINER"]
    return jsonify(container.tool_registry.catalogue())
