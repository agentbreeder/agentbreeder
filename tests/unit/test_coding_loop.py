import json

import pytest

from engine.coding_agent.base import AgentBounds
from engine.coding_agent.loop import _apply_tool, run_coding_loop
from engine.providers.models import ToolCall
from tests.unit.fakes.fake_provider import FakeProvider, call, text
from tests.unit.fakes.fake_sandbox import FakeSandbox


@pytest.mark.asyncio
async def test_apply_tool_unknown_name_returns_error():
    sandbox = FakeSandbox()
    tc = ToolCall(id="x", function_name="mystery", function_arguments="{}")
    result, event = await _apply_tool(sandbox, tc)
    assert "unknown tool mystery" in result
    assert event is None


@pytest.mark.asyncio
async def test_loop_writes_file_and_emits_diff():
    sandbox = FakeSandbox()
    provider = FakeProvider(
        [
            [
                text("Creating agent.py"),
                call(
                    "c1",
                    "write_file",
                    json.dumps({"path": "agent.py", "content": "print(1)\n"}),
                ),
            ],
            [text("Done.")],
        ]
    )

    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="sys",
            instruction="build it",
            history=[],
            sandbox=sandbox,
            bounds=AgentBounds(max_turns=5),
        )
    ]

    types = [e.type for e in events]
    assert "token" in types
    assert "file_change" in types
    assert types[-1] == "done"
    fc = next(e for e in events if e.type == "file_change")
    assert fc.path == "agent.py"
    assert "+print(1)" in fc.diff
    assert sandbox.files["agent.py"] == "print(1)\n"


@pytest.mark.asyncio
async def test_loop_respects_max_turns():
    sandbox = FakeSandbox()
    provider = FakeProvider([[call(f"c{i}", "list_files", "{}")] for i in range(20)])
    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="s",
            instruction="x",
            history=[],
            sandbox=sandbox,
            bounds=AgentBounds(max_turns=3),
        )
    ]
    assert events[-1].type == "done"
    assert len([e for e in events if e.type == "tool_call"]) == 3


@pytest.mark.asyncio
async def test_loop_run_command_feeds_result_back():
    sandbox = FakeSandbox()
    provider = FakeProvider(
        [
            [call("c1", "run_command", json.dumps({"cmd": ["pytest"], "timeout": 5}))],
            [text("tests pass")],
        ]
    )
    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="s",
            instruction="run tests",
            history=[],
            sandbox=sandbox,
        )
    ]
    assert sandbox.exec_calls == [["pytest"]]
    assert events[-1].type == "done"
    assert any(m.get("role") == "tool" for m in provider.calls[1])


@pytest.mark.asyncio
async def test_loop_malformed_tool_args_feed_error_back():
    sandbox = FakeSandbox()
    provider = FakeProvider(
        [
            [call("c1", "write_file", "{not json")],
            [text("ok")],
        ]
    )
    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="s",
            instruction="x",
            history=[],
            sandbox=sandbox,
        )
    ]
    assert events[-1].type == "done"
    tool_msg = next(m for m in provider.calls[1] if m.get("role") == "tool")
    assert "malformed" in tool_msg["content"]
    assert sandbox.files == {}


@pytest.mark.asyncio
async def test_loop_read_missing_file_feeds_error_back():
    sandbox = FakeSandbox()
    provider = FakeProvider(
        [
            [call("c1", "read_file", json.dumps({"path": "nope.py"}))],
            [text("done")],
        ]
    )
    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="s",
            instruction="x",
            history=[],
            sandbox=sandbox,
        )
    ]
    assert events[-1].type == "done"
    tool_msg = next(m for m in provider.calls[1] if m.get("role") == "tool")
    assert "file not found" in tool_msg["content"]


@pytest.mark.asyncio
async def test_loop_read_existing_file_returns_content():
    sandbox = FakeSandbox()
    sandbox.files["a.py"] = "x = 1\n"
    provider = FakeProvider(
        [
            [call("c1", "read_file", json.dumps({"path": "a.py"}))],
            [text("done")],
        ]
    )
    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="s",
            instruction="x",
            history=[],
            sandbox=sandbox,
        )
    ]
    assert events[-1].type == "done"
    tool_msg = next(m for m in provider.calls[1] if m.get("role") == "tool")
    assert tool_msg["content"] == "x = 1\n"


@pytest.mark.asyncio
async def test_loop_list_files_returns_listing():
    sandbox = FakeSandbox()
    sandbox.files["a.py"] = ""
    sandbox.files["b.py"] = ""
    provider = FakeProvider(
        [
            [call("c1", "list_files", "{}")],
            [text("done")],
        ]
    )
    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="s",
            instruction="x",
            history=[],
            sandbox=sandbox,
        )
    ]
    assert events[-1].type == "done"
    tool_msg = next(m for m in provider.calls[1] if m.get("role") == "tool")
    assert "a.py" in tool_msg["content"]
    assert "b.py" in tool_msg["content"]


@pytest.mark.asyncio
async def test_loop_unavailable_tool_feeds_error_without_executing():
    sandbox = FakeSandbox()
    provider = FakeProvider(
        [
            [call("c1", "delete_everything", "{}")],
            [text("done")],
        ]
    )
    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="s",
            instruction="x",
            history=[],
            sandbox=sandbox,
        )
    ]
    assert events[-1].type == "done"
    assert not any(e.type == "tool_call" for e in events)
    tool_msg = next(m for m in provider.calls[1] if m.get("role") == "tool")
    assert "not available" in tool_msg["content"]


@pytest.mark.asyncio
async def test_loop_stops_on_wall_clock_bound():
    sandbox = FakeSandbox()
    provider = FakeProvider([[call("c1", "list_files", "{}")]])
    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="s",
            instruction="x",
            history=[],
            sandbox=sandbox,
            bounds=AgentBounds(wall_clock_s=-1.0),
        )
    ]
    assert events[-1].type == "done"
    assert "wall-clock" in events[-1].text
    assert provider.calls == []


@pytest.mark.asyncio
async def test_loop_stops_on_token_bound():
    sandbox = FakeSandbox()
    provider = FakeProvider(
        [
            [
                text("a lot of tokens here"),
                call("c1", "list_files", "{}"),
            ],
            [text("should not reach")],
        ]
    )
    events = [
        e
        async for e in run_coding_loop(
            provider=provider,
            model="m",
            system_prompt="s",
            instruction="x",
            history=[],
            sandbox=sandbox,
            bounds=AgentBounds(max_tokens=1),
        )
    ]
    assert events[-1].type == "done"
    assert "token bound" in events[-1].text
    assert len(provider.calls) == 1
