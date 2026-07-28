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


def after_request(response):
    """
    Echoes the request ID back in the X-Request-ID header.
    Closes the DB session.
    """
    if hasattr(g, 'db_session'):
        g.db_session.close()

    if hasattr(g, 'request_id'):
        response.headers['X-Request-ID'] = g.request_id
    return response
