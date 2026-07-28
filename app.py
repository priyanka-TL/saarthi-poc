from flask import Flask, render_template, request, jsonify
import sys
import logging

# Ensure src modules can be loaded
from src.config import Config
from src.agents import HealthTipAgent, TechnicalAgent, GeneralSupportAgent, OrchestratorAgent, ResearchAgent

app = Flask(__name__)

# Validate configuration on startup
try:
    Config.validate()
except ValueError as e:
    print(f"Configuration Error: {e}")
    sys.exit(1)

# Initialize the Multi-Agent System
health_tip_agent = HealthTipAgent()
technical_agent = TechnicalAgent()
general_agent = GeneralSupportAgent()
research_agent = ResearchAgent()

orchestrator = OrchestratorAgent(
    agents=[health_tip_agent, technical_agent, general_agent, research_agent],
    default_agent=general_agent
)

# In-memory session for chat history (cleared on restart)
chat_history = []

@app.route("/")
def index():
    """Serves the main chat interface."""
    return render_template("index.html")

@app.route("/api/chat", methods=["POST"])
def chat():
    """API endpoint to handle incoming chat messages."""
    global chat_history
    data = request.get_json()
    
    if not data or "message" not in data:
        return jsonify({"error": "No message provided"}), 400
        
    user_message = data["message"]
    target_agent = data.get("agent_name")
    
    # Pass the message to our Orchestrator or a specific agent
    try:
        # If the user selected a specific agent from the sidebar (not the main NovaAssist orchestrator),
        # we bypass the router and send the message directly to that agent.
        if target_agent and target_agent != "NovaAssist":
            if target_agent in orchestrator.agents:
                agent = orchestrator.agents[target_agent]
                res_dict = agent.process(user_message, chat_history)
                result = {"agent_name": agent.name, "response": res_dict["content"], "sources": res_dict.get("sources", [])}
            else:
                return jsonify({"error": "Agent not found"}), 404
        else:
            # If the user selected 'NovaAssist', we let the OrchestratorAgent decide
            # which sub-agent is best suited to answer the question.
            result = orchestrator.handle_request(user_message, chat_history)
        
        # Append the exchange to the in-memory chat history so the agents have context for future questions
        chat_history.append({"role": "user", "content": user_message})
        chat_history.append({"role": "assistant", "content": result["response"]})
        
        return jsonify({
            "agent_name": result["agent_name"],
            "response": result["response"],
            "sources": result.get("sources", []),
            "status": "success"
        })
    except Exception as e:
        app.logger.error(f"Error handling request: {e}")
        return jsonify({
            "error": "An internal error occurred."
        }), 500

@app.route("/api/agents", methods=["GET"])
def get_agents():
    """API endpoint to get the list of available agents."""
    agents_list = [
        {"name": "NovaAssist", "description": "Automatically routes your request to the best agent"}
    ]
    
    for name, agent in orchestrator.agents.items():
        agents_list.append({
            "name": name,
            "description": agent.description
        })
        
    return jsonify(agents_list)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
