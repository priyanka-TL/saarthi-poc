"""Session polling/control endpoints (design doc §10.2): a client drives and
observes a pinned (remote_flow) interview through these once /api/chat itself
has returned a `session` payload.

Every route scopes the session to the caller's tenant/identity via the
owning conversation -- a wrong id OR someone else's session both come back
as a plain 404, never 403, so a client can't probe for the existence of a
session it doesn't own.
"""
from __future__ import annotations

import uuid

from flask import Blueprint, current_app, g, jsonify

from src.api.errors import error_response, mitra_error_response
from src.integrations.mitra.exceptions import MitraError
from src.repositories.conversations import ConversationRepository
from src.services.orchestration import OrchestrationService
from src.services.session_service import SessionService

session_bp = Blueprint("session_routes", __name__)


def _not_found():
    return error_response("Session not found", "SESSION_NOT_FOUND", 404)


def _scoped_session(db_session, session_id: uuid.UUID):
    """Returns the session's DTO only if the owning conversation belongs to
    the caller's tenant/identity. Returns None on any miss -- caller maps
    that straight to a 404."""
    dto = SessionService(db_session).get(session_id)
    if dto is None:
        return None
    conv = ConversationRepository(db_session).get_scoped(dto.conversation_id, g.user)
    if conv is None:
        return None
    return dto


def _serialize(dto, agent_key):
    return {
        "id": str(dto.id),
        "conversation_id": str(dto.conversation_id),
        "agent_key": agent_key,
        "state": dto.state,
        "step": dto.step,
        "turn_count": dto.turn_count,
        "result_ref": dto.result_ref,
        "report_url": dto.report_url,
        "error": dto.error,
        "error_code": dto.error_code,
        "started_at": dto.started_at.isoformat(),
        "last_activity_at": dto.last_activity_at.isoformat(),
        "finalized_at": dto.finalized_at.isoformat() if dto.finalized_at else None,
        "ended_at": dto.ended_at.isoformat() if dto.ended_at else None,
    }


def _agent_key_for(container, dto):
    agent = container.agent_registry.get_by_id(str(dto.agent_id))
    return agent.key if agent is not None else None


@session_bp.route("/api/sessions/<uuid:session_id>", methods=["GET"])
def get_session(session_id):
    container = current_app.config["CONTAINER"]
    dto = _scoped_session(g.db_session, session_id)
    if dto is None:
        return _not_found()
    return jsonify(_serialize(dto, _agent_key_for(container, dto)))


@session_bp.route("/api/sessions/<uuid:session_id>/finalize", methods=["POST"])
def finalize_session(session_id):
    container = current_app.config["CONTAINER"]
    dto = _scoped_session(g.db_session, session_id)
    if dto is None:
        return _not_found()

    orch = OrchestrationService(
        session=g.db_session,
        registry=container.agent_registry,
        handler_factory=container.handler_factory,
        llm_factory=container.llm_factory,
        mitra_rest=container.mitra_rest,
        mitra_sessions=container.mitra_sessions,
    )
    try:
        result = orch.finalize_now(session_id, g.user)
    except MitraError as e:
        return mitra_error_response(e)

    if result is None:
        return _not_found()
    return jsonify(_serialize(result, _agent_key_for(container, result)))


@session_bp.route("/api/sessions/<uuid:session_id>/abandon", methods=["POST"])
def abandon_session(session_id):
    container = current_app.config["CONTAINER"]
    dto = _scoped_session(g.db_session, session_id)
    if dto is None:
        return _not_found()

    updated = SessionService(g.db_session).abandon(
        dto.conversation_id, reason="user_requested", actor=g.user.user_id,
    )
    if updated is not None and container.mitra_sessions is not None:
        container.mitra_sessions.close(dto.conversation_id)

    # Idempotent: if there was no open session to abandon (already terminal),
    # fall back to returning the cached row rather than a spurious 404.
    final_dto = updated if updated is not None else dto
    return jsonify(_serialize(final_dto, _agent_key_for(container, final_dto)))


@session_bp.route("/api/sessions/<uuid:session_id>/report", methods=["GET"])
def get_report(session_id):
    container = current_app.config["CONTAINER"]
    dto = _scoped_session(g.db_session, session_id)
    if dto is None:
        return _not_found()

    agent = container.agent_registry.get_by_id(str(dto.agent_id))
    media_type = agent.spec.remote.report_media_type if agent is not None else "application/pdf"

    if dto.report_url:
        return jsonify({"report_url": dto.report_url, "media_type": media_type, "story_id": dto.result_ref})

    if dto.state == "completed" and container.mitra_rest is not None and dto.remote_session_id:
        try:
            url = container.mitra_rest.get_report(dto.remote_session_id, media_type=media_type)
        except MitraError:
            url = None
        if url:
            return jsonify({"report_url": url, "media_type": media_type, "story_id": dto.result_ref})

    return jsonify({"retry_after": 5}), 202
