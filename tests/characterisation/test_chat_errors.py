"""Items 4 and 5 -- error responses from POST /api/chat.

Only two error paths return a non-200 today. Everything else -- including an LLM
that raises -- comes back as a 200 with a friendly sentence; that is pinned
separately in test_known_defects.py.

Module 5.7 introduces a typed error model with stable codes and a `status` key
on failures.
"""

from __future__ import annotations

from tests.characterisation.conftest import chat


def test_unknown_agent_returns_404(client, golden, script):
    """app.py:84-85. The frontend surfaces this as a bare 'An error occurred.'"""
    expected = golden("error_shapes")["agent_not_found"]

    status, body = chat(client, "hello", "No Such Agent")

    assert status == expected["status_code"]
    assert body == expected["body"]
    assert script.calls == [], "no LLM call should be made for an unknown agent"


def test_missing_message_returns_400(client, golden, script):
    """app.py:69-70."""
    expected = golden("error_shapes")["no_message"]

    response = client.post("/api/chat", json={"agent_name": "General Support Agent"})

    assert response.status_code == expected["status_code"]
    assert response.get_json() == expected["body"]
    assert script.calls == []


def test_empty_json_body_returns_400(client, golden, script):
    expected = golden("error_shapes")["empty_body"]

    response = client.post("/api/chat", json={})

    assert response.status_code == expected["status_code"]
    assert response.get_json() == expected["body"]
    assert script.calls == []


def test_empty_string_message_returns_400(client, script):
    """app.py:69 tests `"message" not in data`, so an EMPTY STRING passes that
    check -- but then falls through to the agent.

    Pinning the actual behaviour: an empty message is accepted, not rejected.
    """
    script.queue("reply to nothing")

    status, body = chat(client, "", "General Support Agent")

    assert status == 200
    assert body["status"] == "success"


def test_error_bodies_carry_no_status_key(client):
    """DEFECT (pinned): success responses have `status`, error responses do not.

    static/js/main.js checks `data.status === 'success'` and treats anything
    else as failure, so errors are detected by ABSENCE rather than by a code.
    That works, but it means the client cannot distinguish a 404 from a 500 or
    from an upstream timeout.

    Module 5.7 adds `status: "error"` plus a stable `error_code`. When it does,
    this test SHOULD fail.
    """
    not_found = client.post(
        "/api/chat", json={"message": "x", "agent_name": "Nope"}
    ).get_json()
    bad_request = client.post("/api/chat", json={}).get_json()

    assert set(not_found) == {"error"}
    assert set(bad_request) == {"error"}
    assert "status" not in not_found
    assert "status" not in bad_request


def test_saarthi_sentinel_is_never_treated_as_an_unknown_agent(client, script):
    """app.py:79 -- "Saarthi" is a sentinel, not a registered agent.

    It is present in GET /api/agents but absent from `orchestrator.agents`, so
    the 404 branch must not fire for it.
    """
    script.queue("General Support Agent")
    script.queue("reply")

    status, _ = chat(client, "hello", "Saarthi")

    assert status == 200
