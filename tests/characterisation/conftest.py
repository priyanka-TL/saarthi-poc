"""Shared helpers for the characterisation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Display names, in orchestrator dict-insertion order (app.py:25).
# These strings are a wire contract: static/js/main.js:139 stores `agent.name`
# and posts it straight back as `agent_name`.
AGENT_NAMES = [
    "Health & Wellness Agent",
    "Technical Support Agent",
    "General Support Agent",
    "Research & Web Agent",
]

TOOLLESS_AGENTS = AGENT_NAMES[:3]
TOOL_AGENT = "Research & Web Agent"
DEFAULT_AGENT = "General Support Agent"  # app.py:26, the router fallback


@pytest.fixture()
def golden():
    """Load a golden fixture by stem."""

    def _load(name: str):
        return json.loads((FIXTURES / f"{name}.json").read_text())

    return _load


def chat(client, message: str, agent_name: str | None = None):
    """POST one turn and return (status_code, body)."""
    payload: dict = {"message": message}
    if agent_name is not None:
        payload["agent_name"] = agent_name
    response = client.post("/api/chat", json=payload)
    return response.status_code, response.get_json()
