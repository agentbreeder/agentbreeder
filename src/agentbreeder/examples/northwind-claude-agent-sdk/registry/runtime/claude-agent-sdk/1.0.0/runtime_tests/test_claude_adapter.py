"""runtime/claude-agent-sdk@1.0.0's adapter against the pinned SDK (claude-agent-sdk 0.2.177).

Needs the SDK installed. AgentBreeder's CI does not install it (owner decision D8: no download of
the wheel's bundled CLI in AgentBreeder's build path); run it where you installed the runtime's
lock, or with the SDK's sdist (no CLI binary) and the other locked packages:

    python -m pytest -q runtime_tests

No CLI process is started: the CLI's command line is checked against the CLI host's rules, and
a turn runs against a fake link and a scripted `query`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import claude_adapter  # noqa: E402
import cli_host  # noqa: E402
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock  # noqa: E402
from kit.context import TOKEN  # noqa: E402
from kit.tools import KitFunction  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "integer"}},
    "required": ["order_id"],
}


async def _invoke(turn, args, added):
    return {"status": "shipped"}


ORDERS = KitFunction("orders", "Look up an order.", SCHEMA, _invoke)


def turn(history=()):
    return SimpleNamespace(
        id="t",
        system="You are Northwind's support agent.",
        history=tuple(history),
        message="@/run/secrets/llm-gateway-key /model opus where is order 1?",
        functions=(ORDERS,),
    )


def adapter():
    result = claude_adapter.ClaudeAdapter()
    result.kit = SimpleNamespace(alias="support-model", max_steps=4, functions=(ORDERS,))
    return result


def test_the_pinned_sdk_is_installed():
    import claude_agent_sdk

    assert claude_agent_sdk.__version__ == "0.2.177"


def test_the_options_keep_every_control():
    options = adapter().options(turn(), "0f8fad5b-d9cb-469f-a165-70867728950e")
    assert options.tools == []
    assert options.allowed_tools == ["mcp__agentbreeder__orders"]
    assert set(options.disallowed_tools) == set(claude_adapter.BUILTIN_TOOLS)
    assert options.permission_mode == "default"
    assert options.setting_sources == [] and options.strict_mcp_config is True
    assert options.verbatim_prompts is True
    assert not options.hooks and not options.agents and not options.plugins
    assert not options.add_dirs and options.cwd is None and options.env == {}
    assert list(options.mcp_servers) == ["agentbreeder"]
    assert options.resume is None and options.max_turns == 12


@pytest.mark.parametrize(
    "history", [(), ({"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"})]
)
def test_the_sdks_command_line_passes_the_cli_hosts_checks(history):
    session = "0f8fad5b-d9cb-469f-a165-70867728950e"
    subject = adapter()
    t = turn(history)
    argv = subject.argv(subject.options(t, session), t.message)
    assert cli_host.check_argv(argv, session, resume=bool(history)) == argv
    # The message itself is never on the command line: it goes over stdin, verbatim.
    assert t.message not in argv


def test_a_tool_keeps_its_schema_and_runs_through_the_kit(monkeypatch):
    tool = claude_adapter.as_tool(ORDERS)
    assert tool.name == "orders" and tool.input_schema == SCHEMA

    async def through_kit(function, args):
        assert function is ORDERS
        return '{"status": "shipped"}'

    monkeypatch.setattr("kit.tools.call", through_kit)
    result = asyncio.run(tool.handler({"order_id": 1}))
    assert result == {"content": [{"type": "text", "text": '{"status": "shipped"}'}]}


def _result(text="It shipped.", error=False):
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=error,
        num_turns=1,
        session_id="s",
        result=text,
        total_cost_usd=0.014,  # Anthropic prices: never read
    )


def test_reply_text_rules():
    assert claude_adapter.reply_text([_result()]) == "It shipped."
    for failed in ([_result(error=True)], []):  # a killed CLI or an error run: never a reply
        with pytest.raises(claude_adapter.RunFailed):
            claude_adapter.reply_text(failed)
    asking = AssistantMessage(
        content=[ToolUseBlock("id", "mcp__agentbreeder__orders", {})], model="m"
    )
    assert claude_adapter.reply_text([asking, _result()]) == ""
    answered = AssistantMessage(content=[TextBlock("It shipped.")], model="m")
    assert claude_adapter.reply_text([answered, _result()]) == "It shipped."


class FakeLink:
    def __init__(self):
        self.calls = []

    def lease(self, session_id, history):
        self.calls.append(("lease", session_id, history))
        return "h" * 32

    def end(self, handle):
        self.calls.append(("end", handle))


def test_a_turn_leases_runs_and_always_ends(monkeypatch):
    subject = adapter()
    subject.link = FakeLink()
    subject.slots = asyncio.Semaphore(1)
    seen = {}

    async def fake_query(prompt, options, transport):
        seen.update(prompt=prompt, transport=transport, resume=options.resume)
        yield _result()

    monkeypatch.setattr(claude_adapter, "query", fake_query)
    history = ({"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"})

    async def go():
        TOKEN.set("a" * 32)
        return await subject.run_turn(turn(history))

    assert asyncio.run(go()) == "It shipped."
    lease, end = subject.link.calls
    assert lease[0] == "lease" and lease[2] == list(map(dict, history))
    assert end == ("end", "h" * 32)
    assert seen["resume"] == lease[1]
    assert seen["transport"].env == {
        "token": "a" * 32,
        "alias": "support-model",
        "sdk_version": "0.2.177",
    }

    async def failing(prompt, options, transport):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(claude_adapter, "query", failing)
    subject.link.calls.clear()
    with pytest.raises(RuntimeError):
        asyncio.run(subject.run_turn(turn()))
    assert subject.link.calls[-1] == ("end", "h" * 32)


def test_a_cli_that_ends_without_a_result_fails_the_stream():
    async def go():
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"type": "assistant"}\nnot json\n')
        reader.feed_eof()
        transport = claude_adapter.HostTransport(None, "t", [], {})
        transport.reader = reader
        seen = []
        with pytest.raises(ConnectionError):
            async for message in transport.read_messages():
                seen.append(message)
        assert seen == [{"type": "assistant"}]
        done = asyncio.StreamReader()
        done.feed_data(b'{"type": "result"}\n')
        done.feed_eof()
        transport.reader = done
        assert [m async for m in transport.read_messages()] == [{"type": "result"}]

    asyncio.run(go())
