"""Typed error response envelope, shared by every API blueprint (design doc
§10.1). Every failure carries `status`, `error`, `error_code`, and
`request_id` -- before this, error bodies had no `status` key at all, so a
client could only detect failure by its absence, never distinguish *which*
failure occurred.
"""
from __future__ import annotations

from flask import g, jsonify

from src.integrations.mitra.exceptions import MitraError, MitraTurnTimeout


def error_response(message: str, code: str, status: int):
    return jsonify({
        "status": "error",
        "error": message,
        "error_code": code,
        "request_id": getattr(g, "request_id", None),
    }), status


def mitra_error_response(exc: MitraError):
    """504 UPSTREAM_TIMEOUT for a turn timeout -- the session survives, a
    retry is safe. Every other Mitra-side failure collapses to 502
    UPSTREAM_UNAVAILABLE."""
    if isinstance(exc, MitraTurnTimeout):
        return error_response(str(exc), "UPSTREAM_TIMEOUT", 504)
    return error_response(str(exc), "UPSTREAM_UNAVAILABLE", 502)
