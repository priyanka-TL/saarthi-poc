from flask import Blueprint, render_template, request, jsonify, current_app, g
import uuid
from src.services.conversations import ConversationService
from src.services.session_service import SessionService
from src.domain.core import MemorySpec
from src.settings import settings
from src.api.errors import error_response, mitra_error_response
from src.integrations.mitra.exceptions import MitraError

chat_bp = Blueprint("chat_routes", __name__)

@chat_bp.route("/")
def index():
    """Serves the main chat interface."""
    return render_template("index.html")

@chat_bp.route("/api/chat", methods=["POST"])
def chat():
    """API endpoint to handle incoming chat messages."""
    data = request.get_json()
    
    if not data or "message" not in data:
        return error_response("No message provided", "INVALID_REQUEST", 400)
        
    user_message = data["message"]
    # New fields (optional)
    target_agent = data.get("agent_key") or data.get("agent_name")
    conversation_id_str = data.get("conversation_id")
    option_id = data.get("option_id")
    # locale defaults to user.locale in the DB logic

    try:
        from src.services.orchestration import OrchestrationService, TurnInput
        container = current_app.config["CONTAINER"]
        orch = OrchestrationService(
            session=g.db_session,
            registry=container.agent_registry,
            handler_factory=container.handler_factory,
            llm_factory=container.llm_factory,
            mitra_rest=container.mitra_rest,
            mitra_sessions=container.mitra_sessions,
        )
        
        req_conv_id = uuid.UUID(conversation_id_str) if conversation_id_str else None
        ctx_in = TurnInput(
            request_id=g.request_id if hasattr(g, "request_id") else str(uuid.uuid4()),
            conversation_id=req_conv_id,
            user=g.user,
            text=user_message,
            option_id=option_id,
            agent_key=target_agent if target_agent and target_agent != "Saarthi" else None
        )
        
        res = orch.handle_turn(ctx_in)
        svc = ConversationService(g.db_session)

        session_payload = None
        if res.session is not None:
            session_payload = {
                "id": str(res.session.id),
                "state": res.session.state,
                "step": res.session.step,
                "agent_key": res.agent.key,
                "result_ref": res.session.result_ref,
                "report_url": res.session.report_url,
            }

        return jsonify({
            "agent_name": res.agent.name,
            "response": res.turn.text,
            "status": "success",
            "flow": svc.flow_payload(res.conversation.id),
            "conversation_id": str(res.conversation.id),
            "agent_key": res.agent.key,
            "agent_type": res.agent.spec.agent_type,
            "options": [{"id": o.id, "label": o.label, "value": o.value} for o in res.turn.options],
            "session": session_payload,
        })
    except MitraError as e:
        return mitra_error_response(e)
    except Exception as e:
        from src.services.router_service import AgentNotFound
        if isinstance(e, AgentNotFound):
            return error_response("Agent not found", "AGENT_NOT_FOUND", 404)
        current_app.logger.error(f"Error handling request: {e}")
        return error_response("An internal error occurred.", "INTERNAL", 500)

@chat_bp.route("/api/reset", methods=["POST"])
def reset():
    """API endpoint to clear the conversation and start a new flow."""
    data = request.get_json(silent=True) or {}
    conversation_id_str = data.get("conversation_id")
    req_conv_id = uuid.UUID(conversation_id_str) if conversation_id_str else None
    
    svc = ConversationService(g.db_session)
    # 1. Resolve current active (or specified) conversation
    conv = svc.resolve(req_conv_id, g.user)

    # Abandon any open session and close its Mitra channel BEFORE archiving --
    # otherwise a reset mid-interview orphans the socket and the story is
    # never finalized (design doc §10.2).
    container = current_app.config["CONTAINER"]
    abandoned = SessionService(g.db_session).abandon(conv.id, reason="reset", actor=g.user.user_id)
    if abandoned is not None and container.mitra_sessions is not None:
        container.mitra_sessions.close(conv.id)

    # 2. Archive it
    svc.reset(conv.id)
    
    # 3. Create a new one
    new_conv = svc.resolve(None, g.user) # Since we just archived the old one, this creates a new active one
    
    return jsonify({
        "status": "success"
    })
