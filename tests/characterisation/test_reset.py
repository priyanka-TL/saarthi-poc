"""Item 6 -- POST /api/reset.

Module 2.6 replaces the global reset with conversation archival plus a successor
conversation, and module 5.7 additionally makes it abandon any open session and
close its channel. The response shape must survive both.
"""

from __future__ import annotations

from tests.characterisation.conftest import DEFAULT_AGENT, chat


def test_reset_returns_success(client):
    """app.py:109-113."""
    response = client.post("/api/reset")

    assert response.status_code == 200
    assert response.get_json() == {"status": "success"}


def test_reset_accepts_an_empty_body(client):
    """main.js posts no body at all. Keep that valid."""
    response = client.post("/api/reset")

    assert response.status_code == 200





def test_reset_clears_the_flow_title(client, script):
    """After a reset the NEXT message becomes the new title."""
    script.queue("first")
    _, first = chat(client, "the original question", DEFAULT_AGENT)
    assert first["flow"]["title"] == "the original question"

    client.post("/api/reset")

    script.queue("second")
    _, second = chat(client, "a brand new question", DEFAULT_AGENT)
    assert second["flow"]["title"] == "a brand new question"


def test_reset_clears_the_breadcrumb_stops(client, script):
    script.queue("a")
    chat(client, "one", "Health & Wellness Agent")
    script.queue("b")
    _, before = chat(client, "two", "Technical Support Agent")
    assert len(before["flow"]["stops"]) == 2

    client.post("/api/reset")

    script.queue("c")
    _, after = chat(client, "three", "Technical Support Agent")
    assert after["flow"]["stops"] == ["Technical Support Agent"]
    assert after["flow"]["current_index"] == 0


def test_reset_drops_history_from_subsequent_llm_calls(client, script):
    """The cleared history must actually stop being sent to the model."""
    script.queue("first")
    chat(client, "remember this", DEFAULT_AGENT)

    client.post("/api/reset")

    script.queue("second")
    chat(client, "fresh start", DEFAULT_AGENT)

    second_call = script.calls[1]
    assert not any("remember this" in str(m.content) for m in second_call)


def test_reset_is_idempotent(client):
    for _ in range(3):
        response = client.post("/api/reset")
        assert response.status_code == 200
        assert response.get_json() == {"status": "success"}
