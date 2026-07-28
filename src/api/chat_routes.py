from flask import Blueprint, render_template, request, jsonify, current_app, g
import uuid
from src.state import orchestrator
import src.state as global_state
from src.services.conversations import ConversationService
from src.domain.core import MemorySpec

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
        return jsonify({"error": "No message provided"}), 400
        
    user_message = data["message"]
    target_agent = data.get("agent_name")
    
    svc = ConversationService(g.db_session)
    
    # 1. Resolve conversation
    if global_state.CURRENT_CONVERSATION_ID is None:
        global_state.CURRENT_CONVERSATION_ID = uuid.uuid4()
    
    conv = svc.resolve(global_state.CURRENT_CONVERSATION_ID, g.user)
    
    # 2. Persist user message and release lock
    svc.allocate_and_persist_user_message(conv.id, user_message)
    g.db_session.commit()
    
    # 3. Fetch history for the agent
    # Legacy memory didn't have a strict bound, we use a sensible default MemorySpec for now
    memory_spec = MemorySpec(max_messages=10)
    history = svc.history(conv.id, memory_spec)
    
    # Pass the message to our Orchestrator or a specific agent
    try:
        if target_agent and target_agent != "Saarthi":
            if target_agent in orchestrator.agents:
                agent = orchestrator.agents[target_agent]
                response = agent.process(user_message, history)
                result = {"agent_name": agent.name, "response": response}
            else:
                return jsonify({"error": "Agent not found"}), 404
        else:
            result = orchestrator.handle_request(user_message, history)
        
        # 4. Persist assistant response
        svc.persist_assistant_message(conv.id, result["response"], result["agent_name"])
        g.db_session.commit()

        return jsonify({
            "agent_name": result["agent_name"],
            "response": result["response"],
            "status": "success",
            "flow": svc.flow_payload(conv.id)
        })
    except Exception as e:
        g.db_session.rollback()
        current_app.logger.error(f"Error handling request: {e}")
        return jsonify({
            "error": "An internal error occurred."
        }), 500

@chat_bp.route("/api/reset", methods=["POST"])
def reset():
    """API endpoint to clear the conversation and start a new flow."""
    if global_state.CURRENT_CONVERSATION_ID:
        svc = ConversationService(g.db_session)
        svc.reset(global_state.CURRENT_CONVERSATION_ID)
        g.db_session.commit()
        global_state.CURRENT_CONVERSATION_ID = None
    return jsonify({"status": "success"})
