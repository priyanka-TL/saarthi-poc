"""Item 2 -- POST /api/chat, per agent, via explicit selection.

Asserts the response KEY SET and TYPES rather than the LLM text: the text is not
a contract, but the shape is. `agent_name` is asserted exactly, because
static/js/main.js:139 round-trips it as a selector.

Two execution paths exist in BaseAgent.process and module 3.5 reimplements both,
so both are covered here:

  * LCEL chain   (has_tools=False) -- prompt | llm | StrOutputParser, base.py:40
  * tool loop    (has_tools=True)  -- bounded 3-iteration loop,      base.py:71
"""

from __future__ import annotations

import pytest

from tests.characterisation.conftest import (
    AGENT_NAMES,
    TOOL_AGENT,
    TOOLLESS_AGENTS,
    chat,
)


def assert_matches_shape(body: dict, golden_shape: dict) -> None:
    """Assert a chat response against the goldened shape."""
    assert set(body) == set(golden_shape["top_level_keys"])
    assert isinstance(body["agent_name"], str)
    assert isinstance(body["response"], str)
    assert body["status"] == golden_shape["constants"]["status"]

    # Additive module 5.7 keys: always [] / null for every agent exercised in
    # this file (all agent_type: llm -- pin_session is false, so
    # SessionService.open_for never creates a session for them).
    assert body["options"] == []
    assert body["session"] is None

    flow = body["flow"]
    assert set(flow) == set(golden_shape["flow_keys"])
    assert isinstance(flow["current_index"], int)
    assert isinstance(flow["stops"], list)
    assert all(isinstance(stop, str) for stop in flow["stops"])
    assert isinstance(flow["title"], str)


@pytest.mark.parametrize("agent_name", TOOLLESS_AGENTS)
def test_toolless_agent_returns_the_documented_shape(
    client, script, golden, agent_name
):
    script.queue("A scripted reply.")

    status, body = chat(client, "hello", agent_name)

    assert status == 200
    assert_matches_shape(body, golden("chat_response_shape"))
    assert body["agent_name"] == agent_name
    assert body["response"] == "A scripted reply."


@pytest.mark.parametrize("agent_name", TOOLLESS_AGENTS)
def test_toolless_agent_makes_exactly_one_llm_call(client, script, agent_name):
    """Explicit selection bypasses the router entirely (app.py:79-83).

    One call, not two. Module 4.3 preserves this as routing gate 1.
    """
    script.queue("reply")

    chat(client, "hello", agent_name)

    assert len(script.calls) == 1


@pytest.mark.parametrize("agent_name", TOOLLESS_AGENTS)
def test_toolless_agent_sends_its_system_prompt(client, script, agent_name):
    """The agent's system_prompt leads the message list.

    Module 3.8 transcribes these prompts into YAML byte-for-byte, so pin that
    they are actually sent.
    """
    script.queue("reply")

    chat(client, "hello", agent_name)

    system = script.system_prompt(0)
    assert system
    assert "Assistant" in system


def test_tool_agent_runs_the_tool_and_returns_the_final_answer(
    client, script, golden, fake_ddgs
):
    """The tool loop: model asks for a tool, tool runs, model answers."""
    script.queue_tool_call("web_search_tool", {"query": "python"})
    script.queue("Here are the links.")

    status, body = chat(client, "find python docs", TOOL_AGENT)

    assert status == 200
    assert_matches_shape(body, golden("chat_response_shape"))
    assert body["agent_name"] == TOOL_AGENT
    assert body["response"] == "Here are the links."
    assert fake_ddgs.calls == [("text", "python")]


def test_tool_agent_binds_tools_in_declaration_order(client, script, fake_ddgs):
    """Order is significant -- it can influence which tool a model picks.

    specialized.py:62 declares [youtube_search_tool, web_search_tool]; module 3.8
    must preserve that order in YAML.
    """
    script.queue("no tools needed")

    chat(client, "hello", TOOL_AGENT)

    assert [t.name for t in script.bound_tools[0]] == [
        "youtube_search_tool",
        "web_search_tool",
    ]


def test_tool_agent_can_answer_without_calling_a_tool(client, script, fake_ddgs):
    """base.py:76-77 -- no tool_calls means the first reply is final."""
    script.queue("Direct answer, no search needed.")

    status, body = chat(client, "hello", TOOL_AGENT)

    assert status == 200
    assert body["response"] == "Direct answer, no search needed."
    assert fake_ddgs.calls == []
    assert len(script.calls) == 1


def test_tool_loop_is_bounded_and_degrades_to_raw_output(client, script, fake_ddgs):
    """base.py:69 caps the loop at 3 iterations; base.py:98-100 is the fallback.

    Three consecutive tool calls exhaust the bound. The loop then makes ONE more
    invocation, and if that yields empty content the app returns the raw tool
    result rather than nothing. Four scripted responses, therefore.
    """
    for i in range(3):
        script.queue_tool_call("web_search_tool", {"query": f"q{i}"}, call_id=f"c{i}")
    script.queue("")  # empty final content triggers the degradation

    status, body = chat(client, "search repeatedly", TOOL_AGENT)

    assert status == 200
    assert len(script.calls) == 4, "3 loop iterations + 1 forced final call"
    assert body["response"].startswith("Here is the raw data I found:")
    assert len(fake_ddgs.calls) == 3


def test_tool_result_is_fed_back_to_the_model(client, script, fake_ddgs):
    """base.py:94 appends a ToolMessage so the model can read the result."""
    script.queue_tool_call("youtube_search_tool", {"query": "tutorial"})
    script.queue("Final.")

    chat(client, "find a tutorial", TOOL_AGENT)

    second_call = script.calls[1]
    tool_messages = [m for m in second_call if m.type == "tool"]
    assert len(tool_messages) == 1
    assert "Fake Video One" in tool_messages[0].content


@pytest.mark.parametrize("agent_name", AGENT_NAMES)
def test_flow_records_the_responding_agent(client, script, agent_name):
    script.queue("reply")

    _, body = chat(client, "hello", agent_name)

    assert body["flow"]["stops"] == [agent_name]
    assert body["flow"]["current_index"] == 0
