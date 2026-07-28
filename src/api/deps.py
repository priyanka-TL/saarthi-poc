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

def after_request(response):
    """
    Echoes the request ID back in the X-Request-ID header.
    """
    if hasattr(g, 'request_id'):
        response.headers['X-Request-ID'] = g.request_id
    return response
