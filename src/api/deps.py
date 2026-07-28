import uuid
from flask import request, g

def before_request():
    """
    Reads the X-Request-ID header or generates a UUID hex if missing.
    Injects it into the global request context `g`.
    """
    request_id = request.headers.get("X-Request-ID")
    if not request_id:
        request_id = uuid.uuid4().hex
    g.request_id = request_id

    # Authenticate and extract user
    from flask import current_app
    provider = current_app.config.get("USER_PROVIDER")
    if provider:
        # For POC, if auth fails due to missing header in non-static mode, it raises
        try:
            g.user = provider.get_user(request)
        except Exception as e:
            from werkzeug.exceptions import Unauthorized
            raise Unauthorized(str(e))


    # Open DB session
    from src.db.engine import SessionLocal
    g.db_session = SessionLocal()

    # TTL-gated agent registry reload, /api/ paths only.
    if request.path.startswith("/api/"):
        container = current_app.config.get("CONTAINER")
        if container is not None:
            container.agent_registry.maybe_reload(g.db_session)


def after_request(response):
    """
    Echoes the request ID back in the X-Request-ID header.
    """
    if hasattr(g, 'request_id'):
        response.headers['X-Request-ID'] = g.request_id
    return response

def teardown_request(exc):
    """
    Commits the DB session on success, rolls back on exception, and closes it.
    Route handlers must NEVER call commit().
    """
    db_session = getattr(g, 'db_session', None)
    if db_session is not None:
        try:
            if exc is None:
                db_session.commit()
            else:
                db_session.rollback()
        finally:
            db_session.close()
