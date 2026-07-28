import pytest
import jwt
import time
from unittest.mock import MagicMock
from flask import Request

from src.settings import Settings
from src.services.identity import (
    context_from_token, 
    StaticTokenUserProvider, 
    RequestTokenUserProvider,
    ExpiredTokenError,
    InvalidTokenError,
    build_provider
)

SAMPLE_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJkYXRhIjp7ImlkIjoxMzU1LCJuYW1lIjoiUHJpeWFua2EgUHJhZGVlcCIsInNlc3Npb25faWQiOjEyNTQ3LCJvcmdhbml6YXRpb25faWRzIjpbIjYyIl0sIm9yZ2FuaXphdGlvbl9jb2RlcyI6WyJzb3QiXSwidGVuYW50X2NvZGUiOiJzaGlrc2hhbG9rYW0iLCJvcmdhbml6YXRpb25zIjpbeyJpZCI6NjIsIm5hbWUiOiJTb1QiLCJjb2RlIjoic290IiwiZGVzY3JpcHRpb24iOiJTaGlrc2hhTG9rYW0gaXMgc3RyaXZpbmcgdG8gY3JlYXRlIGEgbmF0aW9uYWwgY29udmVyc2F0aW9uIGFib3V0IGVkdWNhdGlvbiBsZWFkZXJzaGlwLXRoZSB3aGF0LCB3aHkgYW5kIGhvdyBvZiBpdC4iLCJzdGF0dXMiOiJBQ1RJVkUiLCJyZWxhdGVkX29yZ3MiOltdLCJ0ZW5hbnRfY29kZSI6InNoaWtzaGFsb2thbSIsIm1ldGEiOm51bGwsImNyZWF0ZWRfYnkiOjEsInVwZGF0ZWRfYnkiOm51bGwsInJvbGVzIjpbeyJpZCI6MjMsInRpdGxlIjoibWVudGVlIiwibGFiZWwiOiJtZW50ZWUiLCJ1c2VyX3R5cGUiOjAsInN0YXR1cyI6IkFDVElWRSIsIm9yZ2FuaXphdGlvbl9pZCI6MTAsInZpc2liaWxpdHkiOiJQVUJMSUMiLCJ0ZW5hbnRfY29kZSI6InNoaWtzaGFsb2thbSIsInRyYW5zbGF0aW9ucyI6bnVsbH0seyJpZCI6NDIsInRpdGxlIjoiY3JlYXRvciIsImxhYmVsIjoiQ3JlYXRvciIsInVzZXJfdHlwZSI6MCwic3RhdHVzIjoiQUNUSVZFIiwib3JnYW5pemF0aW9uX2lkIjoxMCwidmlzaWJpbGl0eSI6IlBVQkxJQyIsInRlbmFudF9jb2RlIjoic2hpa3NoYWxva2FtIiwidHJhbnNsYXRpb25zIjpudWxsfSx7ImlkIjo0NiwidGl0bGUiOiJwcm9ncmFtX2Rlc2lnbmVyIiwibGFiZWwiOiJQcm9ncmFtIERlc2lnbmVyIiwidXNlcl90eXBlIjowLCJzdGF0dXMiOiJBQ1RJVkUiLCJvcmdhbml6YXRpb25faWQiOjEwLCJ2aXNpYmlsaXR5IjoiUFVCTElDIiwidGVuYW50X2NvZGUiOiJzaGlrc2hhbG9rYW0iLCJ0cmFuc2xhdGlvbnMiOm51bGx9XX1dfSwiaWF0IjoxNzg1MjQyNTY5LCJleHAiOjE3ODU4NDczNjl9.misOFt1cSN-smsRVy8-VzuBvbF9DO-VDVDKyEZhR6g0"

@pytest.fixture
def mock_settings():
    # Construct a Settings object using environment variables disabled so it's pure
    return Settings(
        OPENROUTER_API_KEY="test",
        saarthi_jwt_verify_exp=False,  # Don't fail the sample token if time changes
        saarthi_static_token=SAMPLE_TOKEN
    )

def test_context_from_token_derives_email_and_roles(mock_settings):
    # TRAP 1 & 3 test
    ctx = context_from_token(SAMPLE_TOKEN, mock_settings)
    
    # Assert Trap 1: Email derivation
    assert ctx.user_id == "1355"
    assert ctx.email == "1355@shikshalokam.org"
    
    # Assert other basic fields
    assert ctx.display_name == "Priyanka Pradeep"
    assert ctx.tenant_code == "shikshalokam"
    assert ctx.active_org_id == "62"
    assert len(ctx.orgs) == 1
    
    org = ctx.orgs[0]
    assert org.org_id == "62"
    assert org.org_code == "sot"
    
    # Assert Trap 3: Roles from containing org
    assert "mentee" in org.roles
    assert "creator" in org.roles
    assert "program_designer" in org.roles
    
    # Assert the active roles property
    assert set(ctx.roles) == {"mentee", "creator", "program_designer"}

def test_context_from_token_roles_not_flattened(mock_settings):
    # If the user switches active_org_id to something else, they shouldn't see org 62's roles.
    ctx = context_from_token(SAMPLE_TOKEN, mock_settings)
    
    # Manually simulate a request where the active org is different or None
    # We use a dataclass replace equivalent (object.__setattr__ since frozen, or just mock)
    object.__setattr__(ctx, 'active_org_id', "999")
    
    assert len(ctx.roles) == 0

def test_expired_token_fails_at_startup():
    # Mint an expired token
    expired_payload = {
        "data": {"id": 1},
        "exp": int(time.time()) - 3600
    }
    expired_token = jwt.encode(expired_payload, "secret", algorithm="HS256")
    
    settings = Settings(
        OPENROUTER_API_KEY="test",
        saarthi_jwt_verify_exp=True,
        saarthi_static_token=expired_token
    )
    
    # Should fail during provider init
    with pytest.raises(ExpiredTokenError, match="Token has expired"):
        StaticTokenUserProvider(settings)

def test_providers_share_decoder(mock_settings):
    static_provider = build_provider(mock_settings)
    ctx1 = static_provider.get_user(MagicMock(spec=Request))
    
    mock_settings.saarthi_user_provider = "request_token"
    request_provider = build_provider(mock_settings)
    
    req = MagicMock()
    req.headers = {"Authorization": f"Bearer {SAMPLE_TOKEN}"}
    ctx2 = request_provider.get_user(req)
    
    # Both providers must yield the exact same user_id and email using the same decoding path
    assert ctx1.user_id == ctx2.user_id == "1355"
    assert ctx1.email == ctx2.email == "1355@shikshalokam.org"
