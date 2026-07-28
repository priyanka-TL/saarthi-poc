from flask import Blueprint, render_template, request, jsonify, current_app, g
import uuid
from src.state import orchestrator
import src.state as global_state
from src.services.conversations import ConversationService
from src.domain.core import MemorySpec
from src.settings import settings

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
    # New fields (optional)
    target_agent = data.get("agent_key") or data.get("agent_name")
    conversation_id_str = data.get("conversation_id")
    option_id = data.get("option_id")
    # locale defaults to user.locale in the DB logic

    is_postgres = settings.saarthi_persistence == "postgres"

    if is_postgres:
        svc = ConversationService(g.db_session)
        
        # 1. Resolve conversation
        req_conv_id = uuid.UUID(conversation_id_str) if conversation_id_str else None
        conv = svc.resolve(req_conv_id, g.user)
        
        # 2. Persist user message (does not lock row across network anymore, just reserves seq)
        # Note: the prompt says teardown_request commits on success.
        # But allocate_and_persist_user_message needs to release the lock before network call?
        # Actually, "Route handlers must NEVER call commit() — that is the point, so nobody can forget one."
        # If the lock is held across the network call, that's what we have to do because we can't call commit().
        # Wait, the prompt for §10.1 specifically says "teardown_request commits on success... Route handlers must NEVER call commit()".
        # This implies we DON'T release the row lock before the network call in this route!
        svc.allocate_and_persist_user_message(conv.id, user_message)
        
        # 3. Fetch history
        memory_spec = MemorySpec(max_messages=10)
        history = svc.history(conv.id, memory_spec)
    else:
        # Memory mode
        history = global_state.chat_history

    # Pass the message to our Orchestrator or a specific agent
    try:
        if target_agent and target_agent != "Saarthi":
            if target_agent in orchestrator.agents:
                agent = orchestrator.agents[target_agent]
                response = agent.process(user_message, history)
                result = {"agent_name": agent.name, "response": response}
            else:
                # In postgres mode with target_agent != Saarthi, we can look up by key or name.
                # Actually, orchestrator.agents is keyed by agent.name or agent_key?
                # The existing codebase keys by agent.name.
                return jsonify({"error": "Agent not found"}), 404
        else:
            result = orchestrator.handle_request(user_message, history)
        
        if is_postgres:
            # 4. Persist assistant response
            svc.persist_assistant_message(conv.id, result["response"], result["agent_name"])
            
            return jsonify({
                "agent_name": result["agent_name"],
                "response": result["response"],
                "status": "success",
                "flow": svc.flow_payload(conv.id),
                "conversation_id": str(conv.id),
                "agent_key": target_agent,
                "agent_type": "remote_flow", # Dummy for POC
            })
        else:
            global_state.chat_history.append({"role": "user", "content": user_message})
            global_state.chat_history.append({"role": "assistant", "content": result["response"]})
            global_state._record_flow_stop(result["agent_name"], user_message)

            return jsonify({
                "agent_name": result["agent_name"],
                "response": result["response"],
                "status": "success",
                "flow": global_state._flow_payload()
            })
    except Exception as e:
        current_app.logger.error(f"Error handling request: {e}")
        return jsonify({
            "error": "An internal error occurred."
        }), 500

@chat_bp.route("/api/reset", methods=["POST"])
def reset():
    """API endpoint to clear the conversation and start a new flow."""
    is_postgres = settings.saarthi_persistence == "postgres"
    
    if is_postgres:
        data = request.get_json(silent=True) or {}
        conversation_id_str = data.get("conversation_id")
        req_conv_id = uuid.UUID(conversation_id_str) if conversation_id_str else None
        
        svc = ConversationService(g.db_session)
        # 1. Resolve current active (or specified) conversation
        conv = svc.resolve(req_conv_id, g.user)
        
        # 2. Archive it
        svc.reset(conv.id)
        
        # 3. Create a new one
        new_conv = svc.resolve(None, g.user) # Since we just archived the old one, this creates a new active one
        
        return jsonify({
            "status": "success",
            "conversation_id": str(new_conv.id)
        })
    else:
        # Memory mode
        global_state._reset_flow()
        return jsonify({"status": "success"})
