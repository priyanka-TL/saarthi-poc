"""Pinned defects -- behaviour that is WRONG, asserted as-is.

Every test here documents a real flaw and asserts the CURRENT, INCORRECT
behaviour. That is deliberate. When a later module fixes the flaw, the
corresponding test fails loudly and the fix becomes visible in review rather
than slipping through as an incidental change.

    DO NOT "fix" these tests to make them pass.

When a module below lands, delete or invert the matching test as part of that
module's diff, and say so in the commit message.

    #1  errors returned as successful answers      -> fixed by module 3.5 / 5.7
    #2  unknown tool name silently dropped         -> fixed by module 3.5
    #3  conversation state shared by all users     -> fixed by module 2.6
    #4  unbounded history resent every turn        -> fixed by module 3.1
    #5  no per-agent model configuration           -> fixed by module 3.4
"""

from __future__ import annotations

from tests.characterisation.conftest import DEFAULT_AGENT, TOOL_AGENT, chat


# ---------------------------------------------------------------------------
# DEFECT 1 -- an exception is returned to the user as a successful answer.
#            src/agents/base.py:120-123
# ---------------------------------------------------------------------------


def test_defect_llm_exception_is_returned_as_a_successful_answer(client, script):
    """base.py:120-123 catches EVERY exception and returns the message text.

    The result is HTTP 200 with `status: "success"` and an apologetic sentence
    in `response`. The frontend renders it as a normal agent reply, and once a
    database exists it would be STORED as one.

    A user cannot tell an upstream failure from an answer, and neither can any
    later analysis.

    Fixed by module 3.5 (AgentTurn.error as a first-class field) and module 5.7
    (typed HTTP error codes; a timeout becomes 504, and the retry is safe).
    """
    script.queue_error(RuntimeError("upstream exploded"))

    status, body = chat(client, "hello", DEFAULT_AGENT)

    assert status == 200, "a failure currently returns 200, not 5xx"
    assert body["status"] == "success", "a failure is currently reported as success"
    assert body["response"].startswith("I encountered an error processing your request:")
    assert "upstream exploded" in body["response"], "internal detail leaks to the user"


def test_defect_failed_turn_still_appends_a_breadcrumb_stop(client, script):
    """Because the failure is indistinguishable from an answer, the breadcrumb
    records the agent as having successfully participated."""
    script.queue_error(RuntimeError("boom"))

    _, body = chat(client, "hello", DEFAULT_AGENT)

    assert body["flow"]["stops"] == [DEFAULT_AGENT]


def test_defect_failed_turn_is_written_into_conversation_history(client, script):
    """The error sentence is appended to `chat_history` (app.py:92-93) and is
    therefore resent to the model as if it were a genuine prior answer."""
    script.queue_error(RuntimeError("boom"))
    chat(client, "hello", DEFAULT_AGENT)

    script.queue("second reply")
    chat(client, "again", DEFAULT_AGENT)

    second_call = script.calls[1]
    assert any(
        "I encountered an error" in str(m.content) for m in second_call
    ), "the error text is fed back to the model as conversation history"


# ---------------------------------------------------------------------------
# DEFECT 2 -- a tool call naming an unknown tool is silently discarded.
#            src/agents/base.py:84
# ---------------------------------------------------------------------------


def test_defect_unknown_tool_name_is_silently_dropped(client, script, fake_ddgs):
    """base.py:84 uses `next((t for t in self.tools if ...), None)`.

    On a miss it appends NO ToolMessage, so the next invocation carries an
    AIMessage with a dangling tool_call and no corresponding result. Some
    providers reject that outright; others loop. Here the loop simply burns its
    three iterations and degrades.

    Note the degradation text below: `last_tool_result` was never assigned, so
    the user is shown "Here is the raw data I found:" followed by NOTHING.

    Fixed by module 3.5 -- registry lookup plus an explicit error ToolMessage.
    """
    for i in range(3):
        script.queue_tool_call("no_such_tool", {"query": "x"}, call_id=f"c{i}")
    script.queue("")

    status, body = chat(client, "search", TOOL_AGENT)

    assert status == 200
    assert fake_ddgs.calls == [], "no real tool ran"
    assert body["response"] == "Here is the raw data I found:\n", (
        "the fallback exposes an empty result because last_tool_result was "
        "never assigned"
    )


def test_defect_dangling_tool_call_is_resent_to_the_model(client, script, fake_ddgs):
    """The unanswered tool_call stays in the message list on every retry."""
    for i in range(3):
        script.queue_tool_call("no_such_tool", {}, call_id=f"c{i}")
    script.queue("")

    chat(client, "search", TOOL_AGENT)

    second_call = script.calls[1]
    assert not any(m.type == "tool" for m in second_call), (
        "no ToolMessage was appended for the unknown tool"
    )
    assert any(getattr(m, "tool_calls", None) for m in second_call), (
        "the unanswered tool_call is still present"
    )


# ---------------------------------------------------------------------------
# DEFECT 3 -- conversation state is process-global and shared by every user.
#            app.py:30-35
# ---------------------------------------------------------------------------


def test_defect_two_clients_share_one_conversation(flask_app, script):
    """`chat_history`, `flow_stops` and `flow_title` are module globals.

    Two independent clients -- in reality, two different people -- read and
    write the same conversation. This is the defining bug of the current
    implementation.

    Fixed by module 2.6 (durable, identity-scoped conversations).
    """
    alice = flask_app.test_client()
    bob = flask_app.test_client()

    script.queue("reply to alice")
    alice.post(
        "/api/chat",
        json={"message": "alice's private question", "agent_name": DEFAULT_AGENT},
    )

    script.queue("reply to bob")
    response = bob.post(
        "/api/chat", json={"message": "bob's question", "agent_name": DEFAULT_AGENT}
    )

    assert response.get_json()["flow"]["title"] == "alice's private question", (
        "Bob sees Alice's conversation title"
    )

    bobs_call = script.calls[1]
    assert any(
        "alice's private question" in str(m.content) for m in bobs_call
    ), "Alice's message is sent to the model as part of Bob's context"


def test_defect_one_client_reset_wipes_everyones_conversation(flask_app, script):
    """`/api/reset` clears the globals, so it resets every user at once."""
    alice = flask_app.test_client()
    bob = flask_app.test_client()

    script.queue("reply")
    alice.post(
        "/api/chat", json={"message": "alice's thread", "agent_name": DEFAULT_AGENT}
    )

    bob.post("/api/reset")

    script.queue("reply")
    response = alice.post(
        "/api/chat", json={"message": "alice continues", "agent_name": DEFAULT_AGENT}
    )
    assert response.get_json()["flow"]["title"] == "alice continues", (
        "Alice's conversation was reset by Bob"
    )


# ---------------------------------------------------------------------------
# DEFECT 4 -- history grows without bound and is resent in full every turn.
# ---------------------------------------------------------------------------


def test_defect_history_grows_unbounded_across_turns(client, script):
    """Every turn appends two entries to `chat_history` (app.py:92-93), and the
    whole list is replayed to the model on the next turn.

    Cost and latency therefore grow linearly with conversation length, and a
    long conversation eventually exceeds the context window.

    Fixed by module 3.1 -- a declared per-agent memory policy with a bounded
    turn count.
    """
    message_counts = []
    for i in range(5):
        script.queue(f"reply {i}")
        chat(client, f"question {i}", DEFAULT_AGENT)
        message_counts.append(len(script.calls[-1]))

    assert message_counts == sorted(message_counts), "message count never shrinks"
    assert message_counts[-1] > message_counts[0], "history is resent and grows"
    assert message_counts[-1] - message_counts[0] == 8, (
        "4 further turns x 2 messages each -- unbounded, with no window"
    )


def test_defect_no_history_window_is_applied(client, script):
    """The very first message is still being sent on turn 10."""
    script.queue("reply 0")
    chat(client, "the very first question", DEFAULT_AGENT)

    for i in range(1, 10):
        script.queue(f"reply {i}")
        chat(client, f"question {i}", DEFAULT_AGENT)

    assert any(
        "the very first question" in str(m.content) for m in script.calls[-1]
    ), "turn 10 still replays turn 1"


# ---------------------------------------------------------------------------
# DEFECT 5 -- every agent shares one model, at one temperature.
#            src/llm.py:41, src/agents/base.py:21
# ---------------------------------------------------------------------------


def test_defect_every_agent_shares_one_model_instance(app_module):
    """`get_llm()` takes no model parameter (src/llm.py:41) and `BaseAgent`
    calls it with no arguments (base.py:21).

    No agent can be tuned independently -- not its model, not its temperature,
    not its token ceiling, not its timeout.

    Fixed by module 3.4 (LlmFactory keyed on a per-agent ModelSpec).

    NOTE: under test all agents share the stub, so this asserts the structural
    fact -- there is no per-agent model configuration to inspect at all.
    """
    agents = list(app_module.orchestrator.agents.values())

    assert all(not hasattr(agent, "model_name") for agent in agents)
    assert all(not hasattr(agent, "temperature") for agent in agents)
    assert all(not hasattr(agent, "max_tokens") for agent in agents)

    configurable = {"name", "description", "system_prompt", "tools"}
    for agent in agents:
        assert configurable.issubset(set(vars(agent))), (
            "the entire configuration surface of an agent is these four fields"
        )
