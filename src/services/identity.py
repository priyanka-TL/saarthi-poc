import jwt
from typing import Optional
from flask import Request

from src.settings import Settings
from src.domain.core import UserContext, OrgMembership

class ExpiredTokenError(Exception):
    pass

class InvalidTokenError(Exception):
    pass

def context_from_token(token: str, settings: Settings) -> UserContext:
    """
    Decodes the JWT token and produces a UserContext exactly as prescribed.
    Applies TRAP 1 (email derivation) and TRAP 3 (roles from containing org).
    """
    options = {}
    if not settings.saarthi_jwt_verify_exp:
        options["verify_exp"] = False
        
    try:
        if settings.saarthi_jwt_secret:
            decoded = jwt.decode(token, settings.saarthi_jwt_secret, algorithms=["HS256", "HS512"], options=options)
        else:
            # Decode without verification (POC only)
            opts = {"verify_signature": False, "verify_exp": settings.saarthi_jwt_verify_exp}
            decoded = jwt.decode(token, options=opts)
    except jwt.ExpiredSignatureError as e:
        raise ExpiredTokenError(f"Token has expired: {e}")
    except jwt.InvalidTokenError as e:
        raise InvalidTokenError(f"Invalid token: {e}")

    data = decoded.get("data", {})
    
    # TRAP 1: email is derived
    identifier = str(data.get(settings.jwt_identifier_field, ""))
    email = identifier + settings.jwt_email_suffix
    
    # TRAP 3: roles are nested inside organizations[]. The grant scope is the CONTAINING org's id.
    orgs = []
    for org_data in data.get("organizations", []):
        org_id = str(org_data.get("id", ""))
        org_code = str(org_data.get("code", ""))
        
        # Read roles from the containing org, never role["organization_id"]
        roles = []
        for role_data in org_data.get("roles", []):
            title = role_data.get("title")
            if title:
                roles.append(title)
                
        orgs.append(OrgMembership(org_id=org_id, org_code=org_code, roles=tuple(roles)))
        
    # Find active org if there is one? The token might have organization_ids array.
    # The first one is typically the active one if not explicitly specified.
    org_ids = data.get("organization_ids", [])
    active_org_id = str(org_ids[0]) if org_ids else None
    
    return UserContext(
        user_id=identifier,
        email=email,
        display_name=data.get("name", ""),
        tenant_code=data.get("tenant_code", ""),
        orgs=tuple(orgs),
        active_org_id=active_org_id,
        locale="en", # Default, maybe from header or token if available
        token=token
    )

class StaticTokenUserProvider:
    def __init__(self, settings: Settings):
        token = settings.saarthi_static_token
        if not token:
            raise ValueError("SAARTHI_STATIC_TOKEN is missing")
        self._user_context = context_from_token(token, settings)

    def get_user(self, request: Request) -> UserContext:
        return self._user_context

class RequestTokenUserProvider:
    def __init__(self, settings: Settings):
        self._settings = settings

    def get_user(self, request: Request) -> UserContext:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise InvalidTokenError("Missing or invalid Authorization header")
        token = auth_header[len("Bearer "):].strip()
        return context_from_token(token, self._settings)

def build_provider(settings: Settings):
    if settings.saarthi_user_provider == "static_token":
        return StaticTokenUserProvider(settings)
    elif settings.saarthi_user_provider == "request_token":
        return RequestTokenUserProvider(settings)
    else:
        raise ValueError(f"Unknown user provider: {settings.saarthi_user_provider}")
