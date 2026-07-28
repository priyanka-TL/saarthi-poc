from flask import Blueprint, jsonify
from src.state import orchestrator

agent_bp = Blueprint("agent_routes", __name__)

@agent_bp.route("/api/agents", methods=["GET"])
def get_agents():
    """API endpoint to get the list of available agents."""
    agents_list = [
        {"name": "Saarthi", "description": "Automatically routes your request to the best agent"}
    ]
    
    for name, agent in orchestrator.agents.items():
        agents_list.append({
            "name": name,
            "description": agent.description
        })
        
    return jsonify(agents_list)
