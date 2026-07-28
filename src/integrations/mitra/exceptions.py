"""Exceptions raised by the Mitra integration layer.

These are thin wrappers so callers can catch Mitra-specific failures
without depending on `requests.exceptions` directly.
"""


class MitraError(Exception):
    """Base class for all Mitra integration errors."""


class MitraHTTPError(MitraError):
    """Raised when Mitra returns a non-2xx HTTP status code."""

    def __init__(self, method: str, path: str, status: int) -> None:
        # Deliberately DO NOT include response body — it may contain the
        # Origin header value reflected back, or other sensitive context.
        super().__init__(f"Mitra {method} {path} returned HTTP {status}")
        self.status = status


class MitraSSRFError(MitraError):
    """Raised when a URL returned by Mitra fails the allowlist check.

    The URL itself is NOT included in the message to prevent SSRF payloads
    from leaking into logs via exception formatting.
    """

    def __init__(self) -> None:
        super().__init__(
            "URL returned by Mitra failed allowlist validation "
            "(must be https and from an approved host)"
        )


class MitraRedirectError(MitraError):
    """Raised when Mitra returns a redirect (3xx).

    Redirects are disabled on the transport. Any 3xx is unexpected and
    indicates a misconfiguration of MITRA_BASE_URL.
    """

    def __init__(self, status: int) -> None:
        super().__init__(f"Unexpected redirect (HTTP {status}) from Mitra — check MITRA_BASE_URL")
        self.status = status
