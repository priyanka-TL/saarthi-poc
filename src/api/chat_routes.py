from flask import Blueprint, render_template, request, jsonify, current_app, g
import uuid
from src.state import orchestrator
import src.state as global_state
from src.services.conversations import ConversationService
from src.domain.core import MemorySpec
from src.settings import settings

chat_bp = Blueprint("chat_routes", __name__)


def _history_turns(history_dicts):
    from src.agents.protocol import HistoryTurn
    return [
        HistoryTurn(role=h.get("role"), content=h.get("content", ""), agent_key=None)
        for h in history_dicts
    ]


def _execute_llm_agent(container, reg, user_message, history, conversation_id):
    """Config-mode execution backend: builds a TurnContext, dispatches through
    HandlerFactory/LlmAgentHandler, and adapts the result back into the same
    {"agent_name", "response"} shape orchestrator.handle_request() / a direct
    agent.process() call already produce -- so nothing downstream of this
    function needs to change."""
    from src.agents.protocol import TurnContext

    handler = container.handler_factory.build(reg.spec, reg.checksum)
    ctx = TurnContext(
        request_id=g.request_id,
        conversation_id=conversation_id or uuid.uuid4(),
        user=g.user,
        text=user_message,
        option_id=None,
        history=_history_turns(history),
        session=None,
        locale=getattr(g.user, "locale", "en"),
    )
    turn = handler.handle(ctx)
    return {"agent_name": reg.name, "response": turn.text}


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

    try:
        if is_postgres:
            if settings.saarthi_registry == "code":
                svc = ConversationService(g.db_session)
                req_conv_id = uuid.UUID(conversation_id_str) if conversation_id_str else None
                conv = svc.resolve(req_conv_id, g.user)
                svc.allocate_and_persist_user_message(conv.id, user_message)
                memory_spec = MemorySpec(max_messages=10)
                history = svc.history(conv.id, memory_spec)
                
                if target_agent and target_agent != "Saarthi":
                    if target_agent in orchestrator.agents:
                        agent = orchestrator.agents[target_agent]
                        response = agent.process(user_message, history)
                        result = {"agent_name": agent.name, "response": response}
                    else:
                        return jsonify({"error": "Agent not found"}), 404
                else:
                    result = orchestrator.handle_request(user_message, history)
                    
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
                # New OrchestrationService path
                from src.services.orchestration import OrchestrationService, TurnInput
                container = current_app.config["CONTAINER"]
                orch = OrchestrationService(
                    session=g.db_session,
                    registry=container.agent_registry,
                    handler_factory=container.handler_factory,
                    llm_factory=container.llm_factory
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
                
                return jsonify({
                    "agent_name": res.agent.name,
                    "response": res.turn.text,
                    "status": "success",
                    "flow": svc.flow_payload(res.conversation.id),
                    "conversation_id": str(res.conversation.id),
                    "agent_key": res.agent.key,
                    "agent_type": res.agent.spec.agent_type,
                })
        else:
            # Memory mode
            history = global_state.chat_history
            conv_id = None
            if target_agent and target_agent != "Saarthi":
                if settings.saarthi_registry == "code":
                    if target_agent in orchestrator.agents:
                        agent = orchestrator.agents[target_agent]
                        response = agent.process(user_message, history)
                        result = {"agent_name": agent.name, "response": response}
                    else:
                        return jsonify({"error": "Agent not found"}), 404
                else:
                    container = current_app.config["CONTAINER"]
                    reg = container.agent_registry.get(target_agent)
                    if reg is None:
                        return jsonify({"error": "Agent not found"}), 404
                    result = _execute_llm_agent(container, reg, user_message, history, conv_id)
            else:
                if settings.saarthi_registry == "code":
                    result = orchestrator.handle_request(user_message, history)
                else:
                    container = current_app.config["CONTAINER"]
                    from src.services.config_mode_router import decide_sub_agent
                    reg = decide_sub_agent(
                        container.llm_factory,
                        container.agent_registry.routable(),
                        container.agent_registry.default(),
                        user_message,
                    )
                    result = _execute_llm_agent(container, reg, user_message, history, conv_id)

            global_state.chat_history.append({"role": "user", "content": user_message})
            # Only append assistant if not error? 
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
