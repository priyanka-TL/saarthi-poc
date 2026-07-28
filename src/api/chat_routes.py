from flask import Blueprint, render_template, request, jsonify, current_app
from src.state import orchestrator, chat_history, _record_flow_stop, _flow_payload, _reset_flow

chat_bp = Blueprint("chat_routes", __name__)

@chat_bp.route("/")
def index():
    """Serves the main chat interface."""
    return render_template("index.html")

@chat_bp.route("/api/chat", methods=["POST"])
def chat():
    """API endpoint to handle incoming chat messages."""
    # chat_history is imported directly from state
    data = request.get_json()
    
    if not data or "message" not in data:
        return jsonify({"error": "No message provided"}), 400
        
    user_message = data["message"]
    target_agent = data.get("agent_name")
    
    # Pass the message to our Orchestrator or a specific agent
    try:
        # If the user selected a specific agent from the sidebar (not the main Saarthi orchestrator),
        # we bypass the router and send the message directly to that agent.
        if target_agent and target_agent != "Saarthi":
            if target_agent in orchestrator.agents:
                agent = orchestrator.agents[target_agent]
                response = agent.process(user_message, chat_history)
                result = {"agent_name": agent.name, "response": response}
            else:
                return jsonify({"error": "Agent not found"}), 404
        else:
            # If the user selected 'Saarthi', we let the OrchestratorAgent decide
            # which sub-agent is best suited to answer the question.
            result = orchestrator.handle_request(user_message, chat_history)
        
        # Append the exchange to the in-memory chat history so the agents have context for future questions
        chat_history.append({"role": "user", "content": user_message})
        chat_history.append({"role": "assistant", "content": result["response"]})

        _record_flow_stop(result["agent_name"], user_message)

        return jsonify({
            "agent_name": result["agent_name"],
            "response": result["response"],
            "status": "success",
            "flow": _flow_payload()
        })
    except Exception as e:
        current_app.logger.error(f"Error handling request: {e}")
        return jsonify({
            "error": "An internal error occurred."
        }), 500

@chat_bp.route("/api/reset", methods=["POST"])
def reset():
    """API endpoint to clear the conversation and start a new flow."""
    _reset_flow()
    return jsonify({"status": "success"})
