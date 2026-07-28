"""Item 3 -- the router path.

Reached by `agent_name="Saarthi"` or by omitting `agent_name` entirely
(app.py:79 -- both take the same branch).

On this path the scripted queue is consumed in a fixed order:
    response 1 -> the classification string
    response 2 -> the selected agent's reply

Module 4.3 replaces this router with five ordered gates. Several behaviours
pinned here are preserved deliberately (the self-describing prompt, the
never-fail fallback); one is changed deliberately (substring matching).
"""

from __future__ import annotations

import pytest

from tests.characterisation.conftest import AGENT_NAMES, DEFAULT_AGENT, chat


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_classification_selects_the_named_agent(client, script, agent_name):
    script.queue(agent_name)  # classification
    script.queue("agent reply")  # the agent itself

    status, body = chat(client, "route me", "Saarthi")

    assert status == 200
    assert body["agent_name"] == agent_name
    assert body["response"] == "agent reply"
    assert len(script.calls) == 2, "one classification call, then one agent call"


def test_omitting_agent_name_takes_the_router_path(client, script):
    """app.py:79 -- a falsy agent_name is treated exactly like "Saarthi"."""
    script.queue(DEFAULT_AGENT)
    script.queue("reply")

    status, body = chat(client, "route me")  # no agent_name at all

    assert status == 200
    assert body["agent_name"] == DEFAULT_AGENT
    assert len(script.calls) == 2


def test_router_prompt_is_built_from_the_live_registry(client, script):
    """orchestrator.py:32-33 assembles the prompt from every agent's name AND
    description, so registering an agent updates routing with no router edit.

    This is the single best idea in the current codebase. Module 4.3 preserves
    it in concept, so pin it now.
    """
    script.queue(DEFAULT_AGENT)
    script.queue("reply")

    chat(client, "route me", "Saarthi")

    router_prompt = script.system_prompt(0)
    for name in AGENT_NAMES:
        assert name in router_prompt, f"{name!r} missing from the router prompt"
    assert "Provides general health, fitness, diet, and wellness tips." in router_prompt
    assert "Searches the web and YouTube" in router_prompt


def test_router_prompt_names_the_fallback_agent(client, script):
    """orchestrator.py:35 appends an explicit 'if none match' instruction."""
    script.queue(DEFAULT_AGENT)
    script.queue("reply")

    chat(client, "route me", "Saarthi")

    assert f"If none match, reply with '{DEFAULT_AGENT}'" in script.system_prompt(0)


def test_router_sees_only_the_current_message(client, script):
    """DEFECT (pinned): the router is history-blind -- orchestrator.py:54 passes
    only {"request": request}.

    This is why a multi-turn flow cannot survive today: on turn 2 of an
    interview the message is a bare answer like a name, which classifies as
    general chatter and the flow is lost.

    Module 4.3 fixes this with session pinning (gate 2) plus conversation
    context in the classification prompt. When it does, this test SHOULD fail.
    """
    script.queue(DEFAULT_AGENT)
    script.queue("first reply")
    chat(client, "my first message", "Saarthi")

    script.queue(DEFAULT_AGENT)
    script.queue("second reply")
    chat(client, "Priya", "Saarthi")

    router_second_call = script.calls[2]
    human_messages = [m for m in router_second_call if m.type == "human"]
    assert len(human_messages) == 1
    assert human_messages[0].content == "Priya"
    assert not any("my first message" in str(m.content) for m in router_second_call)


def test_unmatched_classification_falls_back_to_the_default_agent(client, script):
    """orchestrator.py:72-73 -- classification is never allowed to fail the
    request. Three independent paths all land on the default agent."""
    script.queue("Some Agent That Does Not Exist")
    script.queue("fallback reply")

    status, body = chat(client, "route me", "Saarthi")

    assert status == 200
    assert body["agent_name"] == DEFAULT_AGENT


def test_empty_classification_falls_back_to_the_default_agent(client, script):
    """orchestrator.py:66-67 -- the empty-output branch."""
    script.queue("")
    script.queue("fallback reply")

    status, body = chat(client, "route me", "Saarthi")

    assert status == 200
    assert body["agent_name"] == DEFAULT_AGENT


def test_classification_exception_falls_back_to_the_default_agent(client, script):
    """orchestrator.py:69-70 -- the exception branch."""
    script.queue_error(RuntimeError("classification exploded"))
    script.queue("fallback reply")

    status, body = chat(client, "route me", "Saarthi")

    assert status == 200
    assert body["agent_name"] == DEFAULT_AGENT


def test_substring_match_resolves_a_full_sentence(client, script):
    """DEFECT (pinned): orchestrator.py:62-65 uses `name.lower() in category.lower()`
    over a dict, first insertion-order match winning.

    A model that answers with a sentence rather than a bare name still resolves.
    That is tolerant, but it is also why an agent whose name is a substring of
    another would mis-route.

    Module 4.3 replaces this with exact key matching on slugs plus structured
    JSON output. When it does, this test SHOULD fail.
    """
    script.queue("I think this should go to the Technical Support Agent, definitely.")
    script.queue("reply")

    _, body = chat(client, "my app crashed", "Saarthi")

    assert body["agent_name"] == "Technical Support Agent"


def test_substring_match_is_case_insensitive(client, script):
    """orchestrator.py:63 lowercases both sides."""
    script.queue("technical support agent")
    script.queue("reply")

    _, body = chat(client, "my app crashed", "Saarthi")

    assert body["agent_name"] == "Technical Support Agent"


def test_first_insertion_order_match_wins(client, script):
    """DEFECT (pinned): when the classification text contains TWO agent names,
    the winner is whichever appears first in `orchestrator.agents` -- not the
    more specific match, and not the first mentioned in the text.

    Health & Wellness is registered first (app.py:25), so it wins even though
    Technical Support is named first in the response.
    """
    script.queue("Technical Support Agent or maybe Health & Wellness Agent")
    script.queue("reply")

    _, body = chat(client, "ambiguous", "Saarthi")

    assert body["agent_name"] == "Health & Wellness Agent"
