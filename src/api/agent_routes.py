from flask import Blueprint, jsonify, current_app
from src.settings import settings
from src.state import orchestrator

agent_bp = Blueprint("agent_routes", __name__)

@agent_bp.route("/api/agents", methods=["GET"])
def get_agents():
    """API endpoint to get the list of available agents."""
    agents_list = [
        {"name": "Saarthi", "description": "Automatically routes your request to the best agent"}
    ]

    if settings.saarthi_registry == "code":
        for name, agent in orchestrator.agents.items():
            agents_list.append({
                "name": name,
                "description": agent.description
            })
    else:
        container = current_app.config["CONTAINER"]
        registered = sorted(container.agent_registry.routable(), key=lambda r: r.spec.sort_order)
        for reg in registered:
            spec = reg.spec
            agents_list.append({
                "name": reg.name,
                "description": reg.description,
                "key": reg.key,
                "agent_type": reg.agent_type,
                "capabilities": spec.capabilities,
                "status": spec.status,
                "sort_order": spec.sort_order,
                "supports_options": spec.features.emit_options,
                "pin_session": spec.routing.pin_session,
            })

    return jsonify(agents_list)
