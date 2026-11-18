"""runtime/adk@1.5.0: one way to find a callback's turn, failing closed (#1058 RT-1).

`Runtime.turn_for(context)` is the turn with the context's invocation id, else the turn the
session state names under `agentbreeder:turn` (set only by the runtime, for a sub-agent's
call), else it raises `TurnUnknown`. Every callback uses it, and every row carries the turn's
own id, never the context's invocation id.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent as agent_module  # noqa: E402
from google.genai import types  # noqa: E402
from test_runtime import (  # noqa: E402, F401  (app and runtime are fixtures)
    FakeMemory,
    ScriptedModel,
    app,
    call,
    runtime,
    text,
)

KEY = agent_module.TURN_KEY


def live(runtime, turn="t1", customer="cust-001", session="s1"):  # noqa: F811
    """A running turn, as chat_async registers one."""
    entry = {
        "id": turn,
        "memory": FakeMemory(""),
        "customer": customer,
        "session": session,
        "trace": {"tools": [], "model_calls": 0, "mcp_unavailable": [], "kb_searches": 0},
        "notes": [],
        "recorded": {},
        "started": {},
        "failed": set(),
        "cost": agent_module.new_tally(),
    }
    runtime.turns[turn] = entry
    return entry


def context(invocation="t1", state=None, **more):
    return SimpleNamespace(invocation_id=invocation, state=state or {}, **more)


def chat(runtime, message="hi", session="s1", customer="cust-001"):  # noqa: F811
    return asyncio.run(runtime.chat_async(session, customer, message))


def test_the_parent_is_found_by_its_invocation_id(runtime):  # noqa: F811
    entry = live(runtime)
    assert runtime.turn_for(context("t1")) is entry
    assert entry["id"] == "t1"


def test_a_child_is_found_by_the_state_key_its_session_carries(runtime):  # noqa: F811
    entry = live(runtime)
    assert runtime.turn_for(context("child-invocation", {KEY: "t1"})) is entry


def test_the_invocation_id_wins_over_the_state_key(runtime):  # noqa: F811
    first, second = live(runtime, "t1"), live(runtime, "t2")
    assert runtime.turn_for(context("t1", {KEY: "t2"})) is first
    assert runtime.turn_for(context("t2", {KEY: "t1"})) is second


@pytest.mark.parametrize(
    "lost",
    [
        context(None),  # no invocation id at all
        context("never-issued"),  # an id nobody issued
        context("never-issued", {KEY: "also-unknown"}),  # the state names no live turn
        context("never-issued", {KEY: None}),
        context("never-issued", {KEY: ["t1"]}),  # not a string: never coerced
        context(["t1"]),
        SimpleNamespace(invocation_id="never-issued"),  # no state attribute
        SimpleNamespace(),  # nothing at all
        None,
    ],
)
def test_an_unknown_turn_fails_closed(runtime, lost):  # noqa: F811
    live(runtime)
    with pytest.raises(agent_module.TurnUnknown):
        runtime.turn_for(lost)


def test_text_the_model_wrote_selects_nothing(runtime):  # noqa: F811
    # The key is read from session state only: a tool argument or request text that holds the
    # key or another turn's id is just text.
    live(runtime, "t1")
    args = {KEY: "t1", "request": f"{KEY}=t1", "invocation_id": "t1"}
    lost = SimpleNamespace(invocation_id="child", state={}, args=args, user_content=str(args))
    with pytest.raises(agent_module.TurnUnknown):
        runtime.turn_for(lost)


def test_a_callback_after_its_turn_ended_fails_closed(runtime):  # noqa: F811
    seen = []
    original = runtime.tool_runtime

    def spy(tool_context):
        seen.append(tool_context)
        return original(tool_context)

    for tool in runtime.agent.tools:
        if tool.name == "whoami":
            tool.runtime_for = spy
    ScriptedModel.script = [call("whoami"), text("Done.")]
    result = chat(runtime)
    assert result["tools"] == ["whoami"] and runtime.turns == {}
    assert seen[0].invocation_id == result["turn_id"]
    with pytest.raises(agent_module.TurnUnknown):  # the entry is deleted, never a recycled id
        runtime.turn_for(seen[0])
    with pytest.raises(agent_module.TurnUnknown):
        runtime.tool_runtime(seen[0])


def test_an_exception_mid_turn_still_deletes_the_entry(runtime):  # noqa: F811
    class Broken(ScriptedModel):
        async def generate_content_async(self, llm_request, stream=False):
            raise ValueError("model exploded")
            yield  # pragma: no cover

    runtime.agent.model = Broken()
    with pytest.raises(ValueError):
        chat(runtime)
    assert runtime.turns == {}


def test_every_row_carries_the_turns_own_id(runtime):  # noqa: F811
    # A sub-agent's callbacks arrive with their own invocation id; nothing may be written
    # under it.
    live(runtime, "t1", customer="cust-001", session="s1")
    child = context(
        "child-invocation", {KEY: "t1"}, user_id="cust-001", function_call_id="c1", agent_name="x"
    )
    plugin = runtime.runner.plugin_manager.plugins[0]
    FakeMemory.events = []
    asyncio.run(plugin._audit(child, "agent", "probe", {}))
    assert FakeMemory.events == [("t1", "s1", "cust-001", "agent", "probe", {})]
    tool_runtime = runtime.tool_runtime(child)
    assert (tool_runtime.turn, tool_runtime.session, tool_runtime.customer) == (
        "t1",
        "s1",
        "cust-001",
    )
    server = SimpleNamespace(name="docs", ref="mcp/docs@1.0.0")
    FakeMemory.events = []
    assert asyncio.run(runtime.mcp_unavailable(server, child, True)) is False
    assert [event[0] for event in FakeMemory.events] == ["t1"]
    assert asyncio.run(runtime.mcp_unavailable(server, child, False)) is True


def test_listing_mcp_tools_outside_an_invocation_is_not_a_turn(runtime):  # noqa: F811
    server = SimpleNamespace(name="docs", ref="mcp/docs@1.0.0")
    assert asyncio.run(runtime.mcp_unavailable(server, None, False)) is False
    with pytest.raises(agent_module.TurnUnknown):
        asyncio.run(runtime.mcp_unavailable(server, context("never-issued"), True))


def test_a_lost_turn_in_the_parent_ends_the_turn_with_the_fixed_reply(runtime):  # noqa: F811
    class Vanishing(ScriptedModel):
        async def generate_content_async(self, llm_request, stream=False):
            runtime.turns.clear()  # the turn disappears under the callbacks
            yield text("An answer nobody may trust.")

    runtime.agent.model = Vanishing()
    FakeMemory.events = []
    result = chat(runtime)
    assert result["reply"] == agent_module.NO_ANSWER
    assert "model_called" not in [event[4] for event in FakeMemory.events]
    assert runtime.turns == {}


def test_another_failure_still_propagates(runtime):  # noqa: F811
    assert agent_module.caused_by(RuntimeError("x"), agent_module.TurnUnknown) is False
    wrapped = RuntimeError("plugin failed")
    wrapped.__cause__ = agent_module.TurnUnknown()
    assert agent_module.caused_by(wrapped, agent_module.TurnUnknown) is True
    assert agent_module.caused_by(None, agent_module.TurnUnknown) is False


def test_callbacks_without_a_turn_raise_instead_of_skipping(runtime):  # noqa: F811
    # 1.4.0 skipped counting, caps and audit when the turn was missing.
    plugin = runtime.runner.plugin_manager.plugins[0]
    lost = context("never-issued", function_call_id="c1", agent_name="x")
    tool = runtime.agent.tools[1]
    request = SimpleNamespace(config=types.GenerateContentConfig(), contents=[])
    response = text("x")
    calls = [
        plugin._audit(lost, "agent", "probe", {}),
        plugin.before_model_callback(callback_context=lost, llm_request=request),
        plugin.after_model_callback(callback_context=lost, llm_response=response),
        plugin.before_tool_callback(tool=tool, tool_args={}, tool_context=lost),
        plugin.after_tool_callback(tool=tool, tool_args={}, tool_context=lost, result="r"),
        plugin.on_tool_error_callback(
            tool=tool, tool_args={}, tool_context=lost, error=ValueError("x")
        ),
        runtime.instruction(lost),
        runtime.agent.tools[0].run_async(args={"query": "q"}, tool_context=lost),
    ]
    for pending in calls:
        with pytest.raises(agent_module.TurnUnknown):
            asyncio.run(pending)
    with pytest.raises(agent_module.TurnUnknown):
        plugin._call_detail(tool, {}, lost)
    with pytest.raises(agent_module.TurnUnknown):
        plugin._recorded(lost)
    assert FakeMemory.events == []


def test_the_turn_table_is_read_only_through_turn_for():
    """The census: `.turns` is touched where a turn starts and ends, and in turn_for. A lookup
    anywhere else (a `.get()` that skips, a `[]` that guesses) fails this test."""
    allowed = {"__init__", "turn_for", "chat_async", "run_turn"}
    for path in sorted(RUNTIME.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = [
            (path.name, _owner(tree, node))
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "turns"
        ]
        assert all(owner in allowed for _, owner in found), found
        if path.name != "agent.py":
            assert found == [], found
        assert ".turns.get(" not in source.replace("self.turns.get(invocation)", "").replace(
            "self.turns.get(named)", ""
        )


def _owner(tree, target):
    """The name of the innermost function containing `target`."""
    best = None

    def visit(node, inside):
        nonlocal best
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inside = node.name
        if node is target:
            best = inside
        for child in ast.iter_child_nodes(node):
            visit(child, inside)

    visit(tree, None)
    return best
