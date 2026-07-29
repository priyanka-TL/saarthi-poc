"""MitraRestClient — typed REST adapter for the five Mitra HTTP endpoints.

SECURITY NOTICE — THE ORIGIN HEADER IS A CREDENTIAL
====================================================
Mitra's Django backend (chatbot/consumers/async_consumer.py) gates WebSocket
admission on the HTTP `Origin` header. The REST endpoints share the same
admission check. Sending `Origin: <MITRA_ORIGIN_URL>` plus a browser
User-Agent is *required* for any request to succeed, and is technically
*impersonation of a trusted browser client*.

This is a known workaround, not a correct server-to-server credential. It is
treated as a secret:

  - Lives ONLY in MITRA_ORIGIN_URL env var (never in YAML, never in DB)
  - Built into ``_fixed_headers`` once at construction time
  - ``_fixed_headers`` is NEVER printed, logged, or included in exceptions
  - ``__repr__`` is overridden to omit it

The correct long-term fix is a server-to-server credential from the Mitra
team. File that as a follow-up. DO NOT remove this header "to clean up" —
without it every request returns 403.

See also: design doc §13.2.

ENDPOINT CONTRACT (§1.8)
========================
All five methods in this module correspond to steps in the §1.8 table:

  upsert_profile       POST /api/profile/              step 1
  generate_session     GET  /api/generate-session/     step 2
  is_session_completed GET  /api/companychat/          step 7
  finalize             POST <spec.remote.finalize_path> step 8
  get_report           GET  /api/get-story/            step 9

WHICH FINALIZE ENDPOINT (v1 vs v2)
==================================
The two end-story endpoints resolve the story bot by DIFFERENT mechanisms,
and that -- not the token placement -- is what decides which one a flow may
use (verified against mitra-service source):

  /api/end-story/v2/  generate_story() -> get_story_company_bot_simple(flow)
                      -> Flow.objects.get(flow_route=flow)
                      Requires a row in Mitra's Flow table keyed on
                      flow_route == flow.

  /api/end-story/     create_story_object() -> get_story_company_bot(profile, flow)
                      -> CompanyBot.objects.get(route='/guest-story') etc.,
                      branching on the SessionFlowName enum. No Flow row
                      needed.

A flow with no Flow row 500s on v2 -- `Flow.objects.get` raises
Flow.DoesNotExist, which get_story_company_bot_simple re-raises as DRF
NotFound, which end_story_v2's `except Flow.DoesNotExist` does NOT match, so
it lands in the view's generic `except Exception` -> HTTP 500. It is a
CONFIGURATION error reported as a server error; it is deterministic, not a
race. Which endpoint each agent uses is therefore per-agent config
(``spec.remote.finalize_path``), not a global constant.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

import requests
from requests import Session as HTTPSession

from src.integrations.mitra.exceptions import (
    MitraError,
    MitraHTTPError,
    MitraRedirectError,
    MitraSSRFError,
)

FINALIZE_V1_PATH = "/api/end-story/"
FINALIZE_V2_PATH = "/api/end-story/v2/"


def _is_v2_finalize(path: str) -> bool:
    """Trailing slashes vary between the YAML and the constants; compare on
    the normalised path so `/api/end-story/v2` and `/api/end-story/v2/` do
    not disagree about where the token goes."""
    return path.strip("/") == FINALIZE_V2_PATH.strip("/")


class MitraRestClient:
    """Thin, typed HTTP client for the Mitra REST surface.

    Instantiate once per process (or once per request — it is stateless).
    All I/O is synchronous; the class has no background threads.

    Args:
        base_url:        MITRA_BASE_URL — e.g. "https://mitra.example.com"
        origin_url:      MITRA_ORIGIN_URL — sent as ``Origin`` on every call.
                         **Credential: never log, never print, never raise.**
        user_agent:      MITRA_USER_AGENT — browser UA string.
        allowed_hosts:   Extra FQDNs whose presigned URLs may be fetched.
                         The hostname from ``base_url`` is always included.
        connect_timeout: Seconds to wait for TCP connection.
        read_timeout:    Seconds to wait for the first response byte.
    """

    def __init__(
        self,
        base_url: str,
        origin_url: str,
        user_agent: str,
        allowed_hosts: list[str],
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = (connect_timeout, read_timeout)

        # The base URL's own hostname is always trusted.
        base_host = urlparse(base_url).hostname or ""
        self._allowed_hosts: frozenset[str] = frozenset(
            h.strip().lower() for h in [base_host] + allowed_hosts if h.strip()
        )

        # ----------------------------------------------------------------
        # SECURITY: _fixed_headers contains the Origin credential.
        # This attribute must NEVER appear in logs, __repr__, or exceptions.
        # See the module-level docstring for full rationale.
        # ----------------------------------------------------------------
        self._fixed_headers: dict[str, str] = {
            "Origin": origin_url,
            "User-Agent": user_agent,
            "Accept": "application/json",
        }

        # One reusable session — shares connection pool, applies fixed headers.
        self._session: HTTPSession = requests.Session()
        self._session.headers.update(self._fixed_headers)

    def __repr__(self) -> str:
        # Intentionally omits _fixed_headers to prevent the Origin credential
        # from leaking into log messages that repr objects.
        return f"MitraRestClient(base_url={self._base_url!r})"

    # ------------------------------------------------------------------
    # Public API — five methods, matching §1.8 steps 1, 2, 7, 8, 9
    # ------------------------------------------------------------------

    def upsert_profile(self, email: str, latest_flow_used: str, company: str) -> str:
        """Create or retrieve the Mitra profile for this user.

        POST /api/profile/ → returns the profile id.

        ``company`` comes from the AGENT's ``remote.company_env``, not a
        global. ``email`` must be the *derived* email from module 2.4
        (``user_id + JWT_EMAIL_SUFFIX``). A different derivation produces a
        second profile and splits the user's story history. §1.7 trap 1.

        In the static-token POC the same user is always looked up and Mitra
        returns their existing profile id — this is idempotent.

        Returns:
            profile_id (str)
        """
        data = self._request(
            "POST",
            "/api/profile/",
            json={"email": email, "latest_flow_used": latest_flow_used, "company": company},
        )
        # §1.8 — response has ``id``; ``profileid`` is a legacy fallback key.
        profile_id = data.get("id") or data.get("profileid")
        if not profile_id:
            raise MitraError("upsert_profile: response missing both 'id' and 'profileid' keys")
        return str(profile_id)

    def generate_session(self) -> str:
        """Allocate a new Mitra session id.

        GET /api/generate-session/ → returns ``sessionid``.

        Returns:
            session_id (str)
        """
        data = self._request("GET", "/api/generate-session/")
        session_id = data.get("sessionid")
        if not session_id:
            raise MitraError("generate_session: response missing 'sessionid' key")
        return str(session_id)

    def is_session_completed(self, session_id: str) -> bool:
        """Poll whether the Mitra session has reached COMPLETED state.

        GET /api/companychat/?session=<id> → inspects results[-1].status.

        IMPORTANT: The per-turn ``finish_reason`` on the WebSocket is NOT a
        completion signal — it fires at the end of every bot turn (§1.3).
        Only this REST poll is authoritative for interview completion (§1.8
        step 7).

        Returns:
            True if the last CompanyChat row has status == "COMPLETED".
        """
        data = self._request("GET", "/api/companychat/", params={"session": session_id})
        results = data.get("results", [])
        if not results:
            return False
        return results[-1].get("status") == "COMPLETED"

    def finalize(
        self,
        session_id: str,
        profile_id: str,
        flow: str,
        language: str,
        token: str,
        path: str = FINALIZE_V2_PATH,
    ) -> tuple[str, str]:
        """Submit the completed session for story synthesis.

        ``path`` comes from ``spec.remote.finalize_path`` and selects the
        endpoint — see "WHICH FINALIZE ENDPOINT" in the module docstring for
        why that is per-agent and not a constant.

        IMPORTANT — v2 vs v1 difference (§1.5):
          v2 reads the token from the ``Authorization: Bearer`` header.
          v1 reads it from the request body (``access_token``).
        The token placement follows ``path`` automatically. Sending the v1
        body shape to v2 leaves the call unauthenticated (v2 never reads the
        body key) and vice versa, so these two must never be set separately.

        Returns:
            (story_id, content) where story_id is the Mitra Story.id.

        Note on idempotency (§1.5): ``Story.session`` is UNIQUE in Mitra's
        database, so calling this twice for the same session will fail on
        Mitra's side. The ``agent_sessions.state = 'finalizing'`` claim in
        ``SessionService`` is Saarthi's guard that prevents double-submission.
        """
        payload = {
            "session": session_id,
            "profile_id": profile_id,
            "stage": "COMPLETED",
            "flow": flow,
            "language": language,
        }
        extra_headers = None
        if _is_v2_finalize(path):
            extra_headers = {"Authorization": f"Bearer {token}"}
        else:
            payload["access_token"] = token

        data = self._request("POST", path, json=payload, extra_headers=extra_headers)
        story_id = str(data.get("id", ""))
        content = str(data.get("content", ""))
        if not story_id:
            raise MitraError("finalize: response missing 'id' (story id) key")
        return story_id, content

    def get_report(self, session_id: str, media_type: str = "application/pdf") -> Optional[str]:
        """Fetch the generated report URL for a completed session.

        GET /api/get-story/?session=<id> → walks results[0].story_media[],
        finds the entry matching ``media_type``, and returns ``public_url``.

        The URL is validated (https + allowlist) before being returned.
        Returns None if the story_media list is empty or no matching entry
        exists (report generation may lag — poll until non-None).

        Returns:
            Validated public_url (str), or None if not yet generated.
        """
        data = self._request("GET", "/api/get-story/", params={"session": session_id})
        results = data.get("results", [])
        if not results:
            return None
        story_media = results[0].get("story_media", [])
        for entry in story_media:
            if entry.get("media_type") == media_type:
                url = entry.get("public_url")
                if url:
                    self._validate_url(url)  # raises MitraSSRFError on failure
                    return url
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> dict:
        """Execute one HTTP request and return the parsed JSON body.

        Raises:
            MitraRedirectError: on any 3xx (redirects disabled by design).
            MitraHTTPError:     on any non-2xx, non-3xx status.
            MitraError:         on network errors or non-JSON responses.
        """
        url = f"{self._base_url}{path}"
        headers = {}
        if extra_headers:
            headers.update(extra_headers)

        try:
            response = self._session.request(
                method,
                url,
                json=json,
                params=params,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,  # §13.2 — redirects are unexpected; raise loudly
            )
        except requests.exceptions.RequestException as exc:
            # Do NOT include `url` in the message — it could contain query
            # params that echo a session_id, though not the Origin credential.
            raise MitraError(f"Network error calling Mitra {method} {path}") from exc

        if 300 <= response.status_code < 400:
            raise MitraRedirectError(response.status_code)

        if not response.ok:
            raise MitraHTTPError(
                method, path, response.status_code,
                detail=self._extract_error_detail(response),
            )

        try:
            return response.json()
        except Exception as exc:
            raise MitraError(f"Mitra {method} {path} returned non-JSON body") from exc

    def _extract_error_detail(self, response) -> Optional[str]:
        """Pull Mitra's own error text out of a failed response, safely.

        SECURITY (§13.2): the raw body is NEVER returned. A 403 from the edge
        can reflect the ``Origin`` credential back in its body, so this reads
        only the three known keys of Mitra's JSON error envelope, truncates
        them, and drops the result entirely if the credential appears in it
        anyway. Non-JSON bodies (HTML error pages, proxy output) yield None.
        """
        try:
            body = response.json()
        except Exception:
            return None
        if not isinstance(body, dict):
            return None

        parts = [
            str(body[key])
            for key in ("error_message", "error_type", "detail")
            if body.get(key)
        ]
        if not parts:
            return None

        detail = " | ".join(parts)[:300]
        # Read the credential from its single home rather than keeping a
        # second copy of it on the instance.
        origin = self._fixed_headers.get("Origin")
        if origin and origin in detail:
            return None
        return detail

    def _validate_url(self, url: str) -> None:
        """Assert that a URL returned by Mitra is safe to use.

        Checks:
          1. Scheme must be ``https`` (prevents http downgrade).
          2. Hostname must be in the allowlist (prevents SSRF via a
             compromised Mitra response redirecting Saarthi to an
             internal host).

        Does NOT include the URL in the raised exception (§13.2 SSRF note).
        """
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise MitraSSRFError()
        host = (parsed.hostname or "").lower()
        if host not in self._allowed_hosts:
            raise MitraSSRFError()


def from_settings(settings) -> "MitraRestClient":
    """Construct a MitraRestClient from the application Settings object.

    Convenience factory used by the DI container. Resolves the comma-separated
    ``mitra_allowed_hosts`` string into a list.
    """
    extra_hosts = [
        h.strip()
        for h in settings.mitra_allowed_hosts.split(",")
        if h.strip()
    ]
    return MitraRestClient(
        base_url=settings.mitra_base_url,
        origin_url=settings.mitra_origin_url,
        user_agent=settings.mitra_user_agent,
        allowed_hosts=extra_hosts,
        connect_timeout=settings.mitra_connect_timeout_s,
        read_timeout=settings.mitra_read_timeout_s,
    )
