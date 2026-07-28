"""Contract tests for MitraRestClient against recorded JSON fixtures.

No live network calls are made. The `responses` library intercepts every
HTTP request at the transport level before it leaves the process. This
verifies:

  1. Correct HTTP method and URL for each endpoint
  2. Correct request headers (Origin present on every call)
  3. Correct request body shape (finalize: token in header, NOT body)
  4. Correct parsing of each response shape
  5. SSRF guard: non-https or non-allowlisted URL → MitraSSRFError
  6. Redirect guard: 3xx → MitraRedirectError
  7. HTTP error guard: non-2xx → MitraHTTPError
  8. Empty results branches (is_session_completed, get_report)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import responses as resp_lib  # the `responses` library, not stdlib

from src.integrations.mitra.rest_client import MitraRestClient
from src.integrations.mitra.exceptions import (
    MitraHTTPError,
    MitraRedirectError,
    MitraSSRFError,
    MitraError,
)

# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Shared client factory
# ---------------------------------------------------------------------------

BASE_URL = "https://mitra.example.com"
ORIGIN_URL = "https://mitra.example.com"
USER_AGENT = "Mozilla/5.0 TestAgent"


def _client(**kwargs) -> MitraRestClient:
    defaults = dict(
        base_url=BASE_URL,
        origin_url=ORIGIN_URL,
        user_agent=USER_AGENT,
        allowed_hosts=[],
        connect_timeout=5.0,
        read_timeout=10.0,
    )
    defaults.update(kwargs)
    return MitraRestClient(**defaults)


# ---------------------------------------------------------------------------
# Header assertion helper
# ---------------------------------------------------------------------------

def _assert_origin_sent(call: resp_lib.calls.Call) -> None:
    """Every request must carry the Origin header."""
    assert "Origin" in call.request.headers, (
        "Origin header missing — Mitra gates admission on it (§13.2)"
    )
    assert call.request.headers["Origin"] == ORIGIN_URL


# ---------------------------------------------------------------------------
# 1. upsert_profile
# ---------------------------------------------------------------------------

@resp_lib.activate
def test_upsert_profile_returns_existing_profile_id():
    """The static-token POC always finds an existing profile.

    Mitra upserts: the same (email, company) pair returns the existing row's
    id. profile_upsert.json models this — there is no separate 'create' case
    in the POC because we always use the same hardcoded user. §1.7 trap 1.
    """
    fixture = _load("profile_upsert.json")
    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/profile/",
        json=fixture,
        status=200,
    )

    client = _client()
    profile_id = client.upsert_profile(
        email="1355@shikshalokam.org",
        latest_flow_used="guest-mi-story",
        company="shikshalokamstaging",
    )

    assert profile_id == str(fixture["id"])
    assert len(resp_lib.calls) == 1
    _assert_origin_sent(resp_lib.calls[0])


@resp_lib.activate
def test_upsert_profile_uses_profileid_fallback():
    """If 'id' is absent, fall back to 'profileid' (legacy response key)."""
    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/profile/",
        json={"profileid": 9999, "email": "test@example.com"},
        status=200,
    )

    client = _client()
    result = client.upsert_profile("test@example.com", "guest-mi-story", "testco")
    assert result == "9999"


@resp_lib.activate
def test_upsert_profile_sends_correct_payload():
    """Verify the exact JSON body sent to /api/profile/."""
    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/profile/",
        json=_load("profile_upsert.json"),
        status=200,
    )

    _client().upsert_profile(
        email="1355@shikshalokam.org",
        latest_flow_used="guest-mi-story",
        company="shikshalokamstaging",
    )

    sent_body = json.loads(resp_lib.calls[0].request.body)
    assert sent_body["email"] == "1355@shikshalokam.org"
    assert sent_body["latest_flow_used"] == "guest-mi-story"
    assert sent_body["company"] == "shikshalokamstaging"


# ---------------------------------------------------------------------------
# 2. generate_session
# ---------------------------------------------------------------------------

@resp_lib.activate
def test_generate_session_returns_session_id():
    fixture = _load("generate_session.json")
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/generate-session/",
        json=fixture,
        status=200,
    )

    session_id = _client().generate_session()

    assert session_id == fixture["sessionid"]
    assert len(resp_lib.calls) == 1
    _assert_origin_sent(resp_lib.calls[0])


# ---------------------------------------------------------------------------
# 3. is_session_completed
# ---------------------------------------------------------------------------

@resp_lib.activate
def test_is_session_completed_false_when_in_progress():
    """Per §1.3 / §1.8: finish_reason on WS is NOT the completion signal.
    Only results[-1].status == 'COMPLETED' counts.
    """
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/companychat/",
        json=_load("companychat_in_progress.json"),
        status=200,
    )

    result = _client().is_session_completed("mitra-session-abc123")

    assert result is False
    # Verify correct query param was sent
    assert "session=mitra-session-abc123" in resp_lib.calls[0].request.url
    _assert_origin_sent(resp_lib.calls[0])


@resp_lib.activate
def test_is_session_completed_true_when_completed():
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/companychat/",
        json=_load("companychat_completed.json"),
        status=200,
    )

    result = _client().is_session_completed("mitra-session-abc123")

    assert result is True


@resp_lib.activate
def test_is_session_completed_false_on_empty_results():
    """When results is empty, return False (not yet polled, not an error)."""
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/companychat/",
        json={"count": 0, "results": []},
        status=200,
    )

    result = _client().is_session_completed("mitra-session-abc123")

    assert result is False


# ---------------------------------------------------------------------------
# 4. finalize — the critical v2 test
# ---------------------------------------------------------------------------

@resp_lib.activate
def test_finalize_sends_token_in_authorization_header_not_body():
    """§1.5 / §1.8 step 8: v2 endpoint reads token from Authorization header.

    v1 read it from the request body (still used by Node bot).
    This test is the primary acceptance criterion for the finalize method.
    """
    fixture = _load("end_story_v2.json")
    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/end-story/v2/",
        json=fixture,
        status=200,
    )

    story_id, content = _client().finalize(
        session_id="mitra-session-abc123",
        profile_id="1355",
        flow="guest-mi-story",
        language="en",
        token="test-bearer-token",
    )

    assert story_id == fixture["id"]
    assert content == fixture["content"]

    call = resp_lib.calls[0]
    _assert_origin_sent(call)

    # Token MUST be in the Authorization header
    assert call.request.headers.get("Authorization") == "Bearer test-bearer-token", (
        "finalize must send token in Authorization header (v2 endpoint, not v1 body)"
    )

    # Token must NOT be in the request body
    body = json.loads(call.request.body)
    assert "access_token" not in body, "token must not appear in request body (that is the v1 shape)"
    assert "token" not in body, "token must not appear in request body"

    # Correct body fields for v2
    assert body["session"] == "mitra-session-abc123"
    assert body["profile_id"] == "1355"
    assert body["stage"] == "COMPLETED"
    assert body["flow"] == "guest-mi-story"
    assert body["language"] == "en"


@resp_lib.activate
def test_finalize_raises_on_missing_id():
    """If Mitra omits 'id' from the response, raise MitraError."""
    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/end-story/v2/",
        json={"status": "ok", "message": "Story created"},  # missing id
        status=200,
    )

    with pytest.raises(MitraError, match="missing 'id'"):
        _client().finalize("sess", "prof", "guest-mi-story", "en", "tok")


# ---------------------------------------------------------------------------
# 5. get_report
# ---------------------------------------------------------------------------

@resp_lib.activate
def test_get_report_returns_pdf_url():
    fixture = _load("get_story_with_pdf.json")
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/get-story/",
        json=fixture,
        status=200,
    )

    url = _client().get_report("mitra-session-abc123", media_type="application/pdf")

    assert url == "https://mitra.example.com/media/stories/story-uuid-789xyz.pdf"
    assert "session=mitra-session-abc123" in resp_lib.calls[0].request.url
    _assert_origin_sent(resp_lib.calls[0])


@resp_lib.activate
def test_get_report_returns_none_when_no_pdf_yet():
    """Empty story_media → None (report generation lags; caller should poll)."""
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/get-story/",
        json=_load("get_story_no_pdf.json"),
        status=200,
    )

    url = _client().get_report("mitra-session-abc123")

    assert url is None


@resp_lib.activate
def test_get_report_returns_none_on_empty_results():
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/get-story/",
        json={"count": 0, "results": []},
        status=200,
    )

    assert _client().get_report("mitra-session-abc123") is None


# ---------------------------------------------------------------------------
# 6. SSRF guard
# ---------------------------------------------------------------------------

@resp_lib.activate
def test_get_report_raises_ssrf_error_for_http_url():
    """A returned URL with scheme=http must be rejected (SSRF / downgrade)."""
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/get-story/",
        json={
            "count": 1,
            "results": [{
                "story_media": [{
                    "media_type": "application/pdf",
                    "public_url": "http://mitra.example.com/evil.pdf",  # http, not https
                }]
            }]
        },
        status=200,
    )

    with pytest.raises(MitraSSRFError):
        _client().get_report("mitra-session-abc123")


@resp_lib.activate
def test_get_report_raises_ssrf_error_for_non_allowlisted_host():
    """A returned URL pointing at an unknown host must be rejected."""
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/get-story/",
        json={
            "count": 1,
            "results": [{
                "story_media": [{
                    "media_type": "application/pdf",
                    "public_url": "https://evil-bucket.s3.amazonaws.com/exfil.pdf",
                }]
            }]
        },
        status=200,
    )

    with pytest.raises(MitraSSRFError):
        _client().get_report("mitra-session-abc123")


@resp_lib.activate
def test_get_report_allows_extra_allowed_host():
    """A CDN host explicitly in allowed_hosts is permitted."""
    cdn_host = "cdn.shikshalokam.org"
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/get-story/",
        json={
            "count": 1,
            "results": [{
                "story_media": [{
                    "media_type": "application/pdf",
                    "public_url": f"https://{cdn_host}/stories/xyz.pdf",
                }]
            }]
        },
        status=200,
    )

    client = _client(allowed_hosts=[cdn_host])
    url = client.get_report("mitra-session-abc123")

    assert url == f"https://{cdn_host}/stories/xyz.pdf"


# ---------------------------------------------------------------------------
# 7. Redirect guard
# ---------------------------------------------------------------------------

@resp_lib.activate
def test_redirect_raises_mitra_redirect_error():
    """Any 3xx is unexpected and must raise MitraRedirectError.

    A redirect from Mitra's API indicates MITRA_BASE_URL is misconfigured
    (e.g. pointing at an old non-redirected URL). Raise loudly rather than
    silently following to an unknown destination.
    """
    resp_lib.add(
        resp_lib.GET,
        f"{BASE_URL}/api/generate-session/",
        status=301,
        headers={"Location": "https://mitra.example.com/api/generate-session/v2/"},
    )

    with pytest.raises(MitraRedirectError) as exc_info:
        _client().generate_session()

    assert exc_info.value.status == 301


# ---------------------------------------------------------------------------
# 8. HTTP error guard
# ---------------------------------------------------------------------------

@resp_lib.activate
def test_http_error_raises_mitra_http_error():
    """Non-2xx response (e.g. 401, 500) raises MitraHTTPError."""
    resp_lib.add(
        resp_lib.POST,
        f"{BASE_URL}/api/profile/",
        json={"detail": "Unauthorized"},
        status=401,
    )

    with pytest.raises(MitraHTTPError) as exc_info:
        _client().upsert_profile("a@b.com", "guest-mi-story", "co")

    assert exc_info.value.status == 401
    # Error message must NOT contain the Origin value
    assert ORIGIN_URL not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 9. repr must not leak Origin
# ---------------------------------------------------------------------------

def test_repr_does_not_expose_origin():
    """__repr__ must not leak the Origin credential into logs."""
    client = _client(origin_url="https://secret-origin.example.com")
    r = repr(client)
    assert "secret-origin.example.com" not in r, (
        "Origin credential must not appear in repr (it would leak into logs)"
    )
    assert "MitraRestClient" in r
