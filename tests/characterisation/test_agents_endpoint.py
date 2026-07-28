"""Item 1 -- GET /api/agents.

No LLM is involved, so this response is fully deterministic and is goldened in
its entirety. It is the strongest fixture in the suite: it pins the display
names, the descriptions and the ordering that module 3.8 must reproduce
byte-for-byte when the four hardcoded agents become YAML.
"""

from __future__ import annotations

from tests.characterisation.conftest import AGENT_NAMES

SYNTHETIC_ROUTER_ENTRY = {
    "name": "Saarthi",
    "description": "Automatically routes your request to the best agent",
}


def test_matches_golden_exactly(client, golden):
    """The whole response, locked. Any drift here is a breaking change."""
    response = client.get("/api/agents")
    assert response.status_code == 200
    assert response.get_json() == golden("api_agents")


def test_first_element_is_the_synthetic_router_entry(client):
    """app.py:118-120 hardcodes this entry ahead of the real registry.

    static/js/main.js treats the name "Saarthi" as the sentinel meaning
    "let the router decide", so both the name and its position matter.
    """
    body = client.get("/api/agents").get_json()
    assert body[0] == SYNTHETIC_ROUTER_ENTRY


def test_agent_order_matches_registration_order(client):
    """Order is user-visible -- it is the sidebar order.

    It comes from `orchestrator.agents` dict-insertion order, which is the
    argument order at app.py:25.
    """
    body = client.get("/api/agents").get_json()
    assert [entry["name"] for entry in body[1:]] == AGENT_NAMES


def test_entries_carry_only_name_and_description(client):
    """No `key`, no `id`, no `agent_type` yet.

    Module 3.9 adds those additively. This test proves they are absent today,
    so the addition is visible rather than assumed.
    """
    body = client.get("/api/agents").get_json()
    for entry in body:
        assert set(entry) == {"name", "description"}


def test_response_is_a_list_not_an_envelope(client):
    """The payload is a bare JSON array, not {"agents": [...]}.

    main.js iterates the response directly, so wrapping it would break the UI.
    """
    body = client.get("/api/agents").get_json()
    assert isinstance(body, list)
    assert len(body) == 5


def test_descriptions_are_non_empty(client):
    """Descriptions feed the router's classification prompt (orchestrator.py:33).

    An empty description would silently degrade routing quality, so pin that
    they are all populated.
    """
    body = client.get("/api/agents").get_json()
    for entry in body:
        assert entry["description"].strip()
