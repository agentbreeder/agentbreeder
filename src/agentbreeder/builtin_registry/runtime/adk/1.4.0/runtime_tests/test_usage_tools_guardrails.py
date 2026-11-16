"""runtime/adk@1.4.0: cached prompt tokens (#1056), tool names the model invented (#1074) and
gateway guardrail blocks as MODEL_BLOCKED (#1024 G2).

Same harness as test_model_errors: the runtime's real model client (ADK's LiteLlm, LiteLLM's SDK,
the OpenAI client) against a fake LiteLLM gateway on loopback answering from a script.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent as agent_module  # noqa: E402
from test_model_errors import (  # noqa: E402, F401  (app and gateway are fixtures)
    KEY,
    assert_secretless,
    error,
    failed_turn,
    gateway,
    ok,
    reply_for,
    turn,
)
from test_runtime import FakeMemory, app  # noqa: E402, F401

SSN = "536-22-8174"  # synthetic, as in the G0 run
# The block LiteLLM's Presidio hook returned on the pinned image (spec §12.6), verbatim.
BLOCK = (
    "Blocked entity detected: US_SSN by Guardrail: pii-block. This entity is not allowed to be "
    "used in this request."
)


def cached(prompt=1200, hit=1024, content="Done.", headers=None):
    """A reply whose usage reports cached prompt tokens the way LiteLLM passes them on."""
    status, sent, body = ok(content, headers)
    body["usage"] = {
        "prompt_tokens": prompt,
        "completion_tokens": 2,
        "total_tokens": prompt + 2,
        "prompt_tokens_details": {"cached_tokens": hit},
    }
    return status, sent, body


def tool_call(*names):
    """A reply asking for these tools, in parallel, with no arguments."""
    status, headers, body = ok()
    body["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": "{}"}}
            for i, n in enumerate(names)
        ],
    }
    body["choices"][0]["finish_reason"] = "tool_calls"
    return status, headers, body


class Recorder:
    """Stands in for a span: records set_attribute calls."""

    def __init__(self, log):
        self.log = log

    def set_attribute(self, key, value):
        self.log.append((key, value))

    def get_span_context(self):
        return SimpleNamespace(trace_id=1, span_id=1)

    def set_status(self, *args):
        pass

    def add_event(self, *args):
        pass


@pytest.fixture
def spans(monkeypatch):
    """Root span attributes (the runtime's own `span`) and callback span attributes
    (`otel.get_current_span()`), as two lists of (key, value)."""
    root, current = [], []

    @contextmanager
    def span(name, kind, input=None, **attributes):
        log = root if name == "agent_turn" else []
        recorder = Recorder(log)
        for key, value in attributes.items():
            recorder.set_attribute(key, value)
        yield recorder

    monkeypatch.setattr(agent_module, "span", span)
    monkeypatch.setattr(
        agent_module, "otel", SimpleNamespace(get_current_span=lambda: Recorder(current))
    )
    return SimpleNamespace(root=root, current=current)


def events(action):
    return [e[5] for e in FakeMemory.events if e[4] == action]


# -- #1056: cached prompt tokens ---------------------------------------------------------------


def test_cached_prompt_tokens_reach_the_reply_event_and_root_span(gateway, spans):  # noqa: F811
    runtime, fake = gateway
    fake.script = [cached(prompt=1200, hit=1024)]
    result = turn(runtime)
    assert result["cached_prompt_tokens"] == 1024
    assert result["prompt_tokens"] == 1200  # cached tokens are part of the prompt, not added
    (called,) = events("model_called")
    assert called["cached_prompt_tokens"] == 1024 and called["prompt_tokens"] == 1200
    assert ("agentbreeder.cached_prompt_tokens", 1024) in spans.root


def test_cached_prompt_tokens_add_up_over_a_turns_model_calls(gateway):  # noqa: F811
    runtime, fake = gateway
    first = cached(prompt=1100, hit=0)
    first[2]["choices"] = tool_call("whoami")[2]["choices"]
    fake.script = [first, cached(prompt=1300, hit=1024)]
    result = turn(runtime)
    assert [e["cached_prompt_tokens"] for e in events("model_called")] == [0, 1024]
    assert result["cached_prompt_tokens"] == 1024 and result["tools"] == ["whoami"]


def test_no_cache_report_is_zero(gateway, spans):  # noqa: F811
    runtime, fake = gateway
    fake.script = [ok()]
    result = turn(runtime)
    assert result["cached_prompt_tokens"] == 0
    assert events("model_called")[0]["cached_prompt_tokens"] == 0
    assert ("agentbreeder.cached_prompt_tokens", 0) in spans.root


# -- #1074: tool names the model invented ------------------------------------------------------


def test_an_invented_tool_is_refused_and_not_listed_as_a_tool_call(gateway, spans):  # noqa: F811
    runtime, fake = gateway
    fake.script = [tool_call("delete_everything"), ok("I can't do that.")]
    result = turn(runtime)
    assert result["reply"] == "I can't do that."
    assert result["tools"] == [] and result["unknown_tools"] == ["delete_everything"]
    assert not [e for e in FakeMemory.events if str(e[4]).startswith("tool:")]
    (refused,) = events("tool_unknown")
    assert refused == {
        "name": "delete_everything",
        "agent": "test-agent",
        "unknown": True,
        "ok": False,
        "error": agent_module.UNKNOWN_TOOL_ERROR,
    }
    assert ("openinference.span.kind", "TOOL") not in spans.current
    assert ("agentbreeder.unknown_tool", "delete_everything") in spans.current
    assert ("agentbreeder.unknown_tools", ("delete_everything",)) in spans.root
    assert FakeMemory.last_turn["tools"] == []


def test_the_model_gets_a_fixed_answer_without_the_agents_tool_list(gateway, monkeypatch):  # noqa: F811
    runtime, fake = gateway
    plugin = runtime.runner.plugin_manager.plugins[0]
    tool = agent_module.BaseTool(name="nope", description=agent_module.TOOL_NOT_FOUND)
    context = SimpleNamespace(invocation_id="none", function_call_id="c1")
    answer = __import__("asyncio").run(
        plugin.on_tool_error_callback(
            tool=tool, tool_args={}, tool_context=context, error=ValueError("whoami, boom")
        )
    )
    assert answer == {"error": agent_module.UNKNOWN_TOOL_REPLY}
    assert "whoami" not in json.dumps(answer)


def test_a_real_tool_next_to_an_invented_one_is_still_a_tool_call(gateway):  # noqa: F811
    runtime, fake = gateway
    fake.script = [tool_call("whoami", "made_up"), ok()]
    result = turn(runtime)
    assert result["tools"] == ["whoami"] and result["unknown_tools"] == ["made_up"]
    assert len(events("tool:whoami")) == 1 and len(events("tool_unknown")) == 1
    assert "unknown_tools" not in events("tool:whoami")[0]  # a runtime list, never copied (r1)


def test_unknown_tools_is_a_runtime_key():
    assert "unknown_tools" in agent_module.RUNTIME_KEYS


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("drop table; --", "drop?table;?--".replace(";", "?")),
        ("x" * 200, "x" * 64),
        ("", "?"),
        (None, "?"),
        ("ok_name.v2-x", "ok_name.v2-x"),
        ("café\n", "caf??"),
    ],
)
def test_shown_tool_names_are_bounded_and_plain(name, shown):
    assert agent_module.shown_tool_name(name) == shown


def test_a_key_shaped_tool_name_is_redacted():
    assert KEY not in agent_module.shown_tool_name(KEY)


def test_a_real_tool_object_is_never_taken_for_the_stub(gateway):  # noqa: F811
    runtime, _ = gateway
    for tool in runtime.agent.tools:
        assert not agent_module.unknown_tool(tool)
    lookalike = type("Sub", (agent_module.BaseTool,), {})(
        name="x", description=agent_module.TOOL_NOT_FOUND
    )
    assert not agent_module.unknown_tool(lookalike)


# -- G2: a gateway guardrail block -------------------------------------------------------------


def test_a_guardrail_block_is_model_blocked_with_an_event_and_span(gateway, spans, capsys):  # noqa: F811
    runtime, fake = gateway
    fake.script = [error(400, "invalid_request_error", BLOCK)]
    failure = failed_turn(runtime)
    assert failure.code == "MODEL_BLOCKED" and fake.calls == 1  # never retried
    assert failure.guardrail == ("US_SSN", "pii-block")
    status, body, headers = reply_for(failure)
    assert status == 422 and body["code"] == "MODEL_BLOCKED" and headers == {}
    assert body["correlation_id"] == failure.turn_id
    shown = json.dumps(body)
    for absent in ("US_SSN", "pii-block", "Blocked entity", SSN):
        assert absent not in shown
    (event,) = events("guardrail_blocked")
    assert event == {
        "guardrail": "pii-block",
        "entity": "US_SSN",
        "action": "blocked",
        "code": "MODEL_BLOCKED",
        "model": "m",
    }
    assert ("agentbreeder.guardrail.action", "blocked") in spans.root
    assert ("agentbreeder.guardrail.name", "pii-block") in spans.root
    assert ("agentbreeder.guardrail.entity", "US_SSN") in spans.root
    assert ("agentbreeder.error_code", "MODEL_BLOCKED") in spans.root
    log = capsys.readouterr().out
    assert "code=MODEL_BLOCKED" in log and len(log.splitlines()) == 1
    assert_secretless(log)


def test_the_customers_message_never_reaches_the_reply_event_or_log(gateway, capsys):  # noqa: F811
    runtime, fake = gateway
    fake.script = [error(400, "invalid_request_error", BLOCK)]
    import asyncio

    with pytest.raises(agent_module.ModelError) as caught:
        asyncio.run(runtime.chat_async("s1", "cust-001", f"my ssn is {SSN}"))
    _, body, _ = reply_for(caught.value)
    assert SSN not in json.dumps(body)
    assert SSN not in json.dumps(events("guardrail_blocked"))
    assert SSN not in capsys.readouterr().out


@pytest.mark.parametrize(
    "message",
    [
        # Presidio fail-closed (spec §12.5): not a block, stays a refused request.
        "Presidio analyzer returned invalid response; cannot verify PII when PII protection is "
        "configured: 500",
        # Another 400 that only quotes the phrase, also behind LiteLLM's own prefix (F1, r1).
        f"invalid content: {BLOCK}",
        f"invalid content: OpenAIException - {BLOCK}",
        f"x\nlitellm.BadRequestError: OpenAIException - {BLOCK}",
        "Blocked entity detected: us_ssn by Guardrail: pii-block. lower case entity",
        "Blocked entity detected: US_SSN by Guardrail: pii block. spaced name",
    ],
)
def test_other_400s_are_not_blocks(gateway, message):  # noqa: F811
    runtime, fake = gateway
    fake.script = [error(400, "invalid_request_error", message)]
    failure = failed_turn(runtime)
    assert failure.code == "MODEL_REQUEST_REFUSED" and failure.guardrail is None
    assert events("guardrail_blocked") == []


def test_a_failed_guardrail_event_still_answers_model_blocked(gateway, monkeypatch, capsys):  # noqa: F811
    runtime, fake = gateway
    fake.script = [error(400, "invalid_request_error", BLOCK)]

    async def broken(*args):
        raise OSError("db down")

    monkeypatch.setattr(agent_module.Runtime, "blocked", broken)
    assert failed_turn(runtime).code == "MODEL_BLOCKED"
    log = capsys.readouterr().out
    assert "guardrail event failed: OSError" in log and "code=MODEL_BLOCKED" in log


def test_cached_tokens_never_exceed_the_prompt(gateway):  # noqa: F811
    runtime, fake = gateway
    fake.script = [cached(prompt=100, hit=500)]
    assert turn(runtime)["cached_prompt_tokens"] == 100


def test_a_block_text_on_another_status_is_not_a_block(gateway):  # noqa: F811
    runtime, fake = gateway
    fake.script = [error(403, "auth_error", BLOCK)]
    assert failed_turn(runtime).code == "MODEL_ACCESS_DENIED"


def test_guardrail_block_parsing():
    parse = agent_module.guardrail_block
    assert parse(Exception(BLOCK)) == ("US_SSN", "pii-block")
    assert parse(Exception(f"litellm.BadRequestError: OpenAIException - {BLOCK}")) == (
        "US_SSN",
        "pii-block",
    )
    assert parse(Exception("Blocked entity detected: EMAIL_ADDRESS by Guardrail: g.v1.")) == (
        "EMAIL_ADDRESS",
        "g.v1",
    )
    assert parse(Exception("Blocked entity detected: X by Guardrail: " + "a" * 65 + ".")) is None

    class Broken(Exception):
        def __str__(self):
            raise RuntimeError

    assert parse(Broken()) is None
