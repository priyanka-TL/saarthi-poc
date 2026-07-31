from flask import Blueprint, render_template, request, jsonify, current_app, g
import uuid
from src.services.conversations import ConversationService
from src.services.session_service import SessionService
from src.repositories.conversations import ConversationRepository
from src.repositories.sessions import AgentSessionRepository
from src.domain.core import MemorySpec
from src.settings import settings
from src.api.errors import error_response, mitra_error_response
from src.integrations.mitra.exceptions import MitraError
from src.services.orchestration import ConcurrentTurnError, TurnLimitExceeded

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

    # Parsed BEFORE the try, so a malformed id is the client error it is rather
    # than being swallowed by the generic handler below and reported as a 500.
    try:
        req_conv_id = uuid.UUID(conversation_id_str) if conversation_id_str else None
    except (ValueError, AttributeError, TypeError):
        return error_response("conversation_id must be a UUID", "INVALID_REQUEST", 400)

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
        
        ctx_in = TurnInput(
            request_id=g.request_id if hasattr(g, "request_id") else str(uuid.uuid4()),
            conversation_id=req_conv_id,
            user=g.user,
            text=user_message,
            option_id=option_id,
            agent_key=target_agent if target_agent and target_agent != "Saarthi" else None,
            autostart=bool(data.get("autostart")),
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
    except ConcurrentTurnError:
        # A double-submit. 409 and NOT an automatic retry: re-sending is what
        # merges two user messages into one in Mitra and destroys an answer.
        return error_response(
            "A reply is already on its way. Please wait for it before sending again.",
            "CONCURRENT_TURN", 409,
        )
    except TurnLimitExceeded as e:
        return error_response(e.detail, "RATE_LIMITED", 429)
    except MitraError as e:
        return mitra_error_response(e)
    except Exception as e:
        from src.services.router_service import AgentNotFound
        if isinstance(e, AgentNotFound):
            return error_response("Agent not found", "AGENT_NOT_FOUND", 404)
        current_app.logger.error(f"Error handling request: {e}")
        return error_response("An internal error occurred.", "INTERNAL", 500)

@chat_bp.route("/api/conversations", methods=["GET"])
def list_conversations():
    """The caller's most recently active conversations, newest first --
    powers the sidebar's recent-conversations list."""
    try:
        limit = int(request.args.get("limit", 5))
    except ValueError:
        return error_response("limit must be an integer", "INVALID_REQUEST", 400)
    limit = max(1, min(limit, 20))

    svc = ConversationService(g.db_session)
    page = svc.list_recent(g.user, limit)

    return jsonify({
        "conversations": [
            {
                "id": str(c.id),
                "title": c.title or "New conversation",
                "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
                "message_count": c.message_count,
            }
            for c in page.conversations
        ]
    })

@chat_bp.route("/api/conversations/<uuid:conversation_id>/messages", methods=["GET"])
def get_conversation_messages(conversation_id):
    """Full message history for one conversation, chronological -- powers
    resuming a conversation from the sidebar (or restoring it on reload)."""
    conv = ConversationRepository(g.db_session).get_scoped(conversation_id, g.user)
    if conv is None:
        return error_response("Conversation not found", "CONVERSATION_NOT_FOUND", 404)

    svc = ConversationService(g.db_session)
    messages = svc.list_messages(conversation_id)
    agent_names = svc.resolve_agent_names({m.agent_id for m in messages if m.agent_id})

    # The completion notice (story ready + report link) is rendered by the
    # client from the /api/chat response and is NOT a stored message, so a
    # reload replayed the transcript without it and the download link vanished.
    # Returning the sessions here lets the client rebuild that state from server
    # truth instead of persisting a synthetic message row for it.
    #
    # ALL of them, not just the latest. A conversation can hold several (the
    # router allows switching agents mid-conversation), and returning only the
    # newest meant a later agent's session masked an earlier COMPLETED one --
    # its report_url never reached the client, so reopening a finished story
    # from history showed no Download PDF button even though the report existed.
    container = current_app.config["CONTAINER"]
    session_dtos = AgentSessionRepository(g.db_session).list_for_conversation(conversation_id)
    sessions_payload = []
    for dto in session_dtos:
        agent = container.agent_registry.get_by_id(str(dto.agent_id))
        sessions_payload.append({
            "id": str(dto.id),
            "state": dto.state,
            "step": dto.step,
            "agent_key": agent.key if agent is not None else None,
            "result_ref": dto.result_ref,
            "report_url": dto.report_url,
        })

    return jsonify({
        "conversation_id": str(conversation_id),
        # The agent-journey breadcrumb, in the same shape /api/chat returns and
        # derived from the same conversation_messages.agent_id sequence. Without
        # it the client had nothing to rebuild the journey from on resume, so
        # reopening a conversation from history collapsed
        # "Capture Discussions -> Record Stories -> General Support Agent" down
        # to whichever agent happened to speak last.
        "flow": svc.flow_payload(conversation_id),
        "sessions": sessions_payload,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "agent_name": agent_names.get(m.agent_id),
                # Which session produced this message, so the client can anchor
                # per-session UI (the report link) to the right point in the
                # timeline instead of dumping it at the end.
                "agent_session_id": str(m.agent_session_id) if m.agent_session_id else None,
                "options": m.options,
                "selected_option_id": m.selected_option_id,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    })

@chat_bp.route("/api/reset", methods=["POST"])
def reset():
    """API endpoint to clear the conversation and start a new flow."""
    data = request.get_json(silent=True) or {}
    conversation_id_str = data.get("conversation_id")
    try:
        req_conv_id = uuid.UUID(conversation_id_str) if conversation_id_str else None
    except (ValueError, AttributeError, TypeError):
        # This route has no exception handler of its own, so a malformed id used
        # to escape as a bare 500.
        return error_response("conversation_id must be a UUID", "INVALID_REQUEST", 400)

    svc = ConversationService(g.db_session)
    # 1. Find the conversation being left, WITHOUT creating one. resolve() is
    #    get_or_create: on a first-ever reset it would materialise an empty
    #    conversation purely to abandon nothing, and step 2 then creates a
    #    second one -- two empty rows per reset, and the stray one competes to
    #    be "most recent active" on the next turn.
    conv = svc.find_current(req_conv_id, g.user)

    # Abandon any open session and close its Mitra channel BEFORE moving on --
    # otherwise a reset mid-interview orphans the socket and the story is
    # never finalized (design doc §10.2).
    container = current_app.config["CONTAINER"]
    if conv is not None:
        abandoned = SessionService(g.db_session).abandon(conv.id, reason="reset", actor=g.user.user_id)
        if abandoned is not None and container.mitra_sessions is not None:
            container.mitra_sessions.close(conv.id)

    # 2. Start a fresh conversation, LEAVING THE PREVIOUS ONE IN HISTORY.
    #    start_new() reuses an already-empty conversation when the user has one,
    #    so repeated resets don't accumulate dead rows. The reuse scan and the
    #    raw DELETE that used to live here are gone: the scan filtered
    #    list_recent, which requires message_count > 0 and therefore could never
    #    match an empty conversation, and the DELETE named a table (`messages`)
    #    that does not exist in this schema.
    new_conv = svc.start_new(g.user)

    # 3. Return the new id.
    return jsonify({
        "status": "success",
        "conversation_id": str(new_conv.id),
    })
