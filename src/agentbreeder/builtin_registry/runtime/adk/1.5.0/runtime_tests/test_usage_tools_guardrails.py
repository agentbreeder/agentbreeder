"""runtime/adk@1.5.0: cached prompt tokens (#1056), tool names the model invented (#1074) and
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
    # 1.5.0: a callback needs a live turn (Runtime.turn_for raises otherwise, test_turn_for.py).
    live = {"id": "t1", "memory": FakeMemory(""), "session": "s1", "customer": "cust-001"}
    monkeypatch.setitem(runtime.turns, "t1", live)
    context = SimpleNamespace(invocation_id="t1", function_call_id="c1")
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


# -- #1106: a blocked turn keeps none of its text in `turns` or in spans ------------------------


@pytest.fixture
def exported():
    """What reaches the collector: the runtime's processor chain (`TurnSpans`, then the
    redacting exporter) in front of an in-memory exporter, on the global provider."""
    import telemetry
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memory = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    turns = telemetry.TurnSpans(SimpleSpanProcessor(telemetry.RedactingExporter(memory)))
    provider.add_span_processor(turns)
    telemetry._TURNS = turns
    yield memory
    telemetry._TURNS = None
    turns.inner = SimpleSpanProcessor(InMemorySpanExporter())  # detached from `memory`


def everything(span) -> str:
    """Every piece of text an exported span carries."""
    return json.dumps(
        [
            span.name,
            dict(span.attributes or {}),
            span.status.description,
            [[event.name, dict(event.attributes or {})] for event in span.events],
        ],
        default=str,
    )


def blocked_turn(runtime, message=f"my ssn is {SSN}, please update my account"):
    import asyncio

    with pytest.raises(agent_module.ModelError) as caught:
        asyncio.run(runtime.chat_async("s1", "cust-001", message))
    return caught.value


def test_a_blocked_turns_record_keeps_none_of_its_text(gateway):  # noqa: F811
    runtime, fake = gateway
    fake.script = [error(400, "invalid_request_error", BLOCK)]
    blocked_turn(runtime)
    row = FakeMemory.last_turn
    assert row["message"] == agent_module.BLOCKED_MESSAGE == "[blocked by a gateway guardrail]"
    assert row["reply"] == "" and row["evidence"] == [] and row["sources"] == []
    assert SSN not in json.dumps(row, default=str)
    assert row["error"] == "MODEL_BLOCKED: US_SSN by guardrail pii-block"


def test_text_the_gateway_adds_after_the_block_is_not_stored_or_logged(gateway, capsys):  # noqa: F811
    """Only the entity type and guardrail name are parsed out; the rest is not kept anywhere."""
    runtime, fake = gateway
    fake.script = [error(400, "invalid_request_error", f"{BLOCK} Offending text: {SSN}")]
    blocked_turn(runtime)
    assert FakeMemory.last_turn["error"] == "MODEL_BLOCKED: US_SSN by guardrail pii-block"
    assert events("turn_failed") == [{"error": "MODEL_BLOCKED: US_SSN by guardrail pii-block"}]
    log = capsys.readouterr().out
    assert SSN not in log and "Offending" not in log
    assert "detail=MODEL_BLOCKED: US_SSN by guardrail pii-block" in log
    assert SSN not in json.dumps(FakeMemory.events, default=str)


def test_a_block_after_a_tool_call_drops_what_the_turn_gathered(gateway):  # noqa: F811
    """The gateway does not say what matched: a tool result is as likely as the message."""
    runtime, fake = gateway
    fake.script = [tool_call("whoami"), error(400, "invalid_request_error", BLOCK)]
    blocked_turn(runtime, "who am I?")
    row = FakeMemory.last_turn
    assert row["message"] == agent_module.BLOCKED_MESSAGE
    assert row["evidence"] == [] and row["sources"] == []
    assert row["tools"] == ["whoami"] and row["model_calls"] == 2  # names and counts stay


def test_a_turn_that_is_not_blocked_keeps_its_message(gateway):  # noqa: F811
    runtime, fake = gateway
    fake.script = [ok("Hello.")]
    turn(runtime, message="where is my order?")
    assert FakeMemory.last_turn["message"] == "where is my order?"
    fake.script = [error(400, "invalid_request_error", "some other refusal")]
    assert failed_turn(runtime).code == "MODEL_REQUEST_REFUSED"
    assert FakeMemory.last_turn["message"] == "hi"  # a failed turn that is not a block


@pytest.mark.parametrize(
    "message",
    [f"invalid content: {BLOCK} {SSN}", f"x\nlitellm.BadRequestError: OpenAIException - {BLOCK}"],
)
def test_a_block_behind_another_prefix_keeps_its_code_but_none_of_its_text(
    gateway,  # noqa: F811
    exported,
    capsys,
    message,
):
    """The phrase not at the start BLOCKED expects (#1106 review): the code stays
    MODEL_REQUEST_REFUSED, and the row, the event, the log and the spans are stored as a block."""
    runtime, fake = gateway
    fake.script = [error(400, "invalid_request_error", message)]
    assert blocked_turn(runtime).code == "MODEL_REQUEST_REFUSED"
    row = FakeMemory.last_turn
    assert row["message"] == agent_module.BLOCKED_MESSAGE
    assert row["error"] == (
        "MODEL_REQUEST_REFUSED: gateway text withheld (it names a guardrail block)"
    )
    assert events("guardrail_blocked") == []
    stored = json.dumps([row, events("turn_failed")], default=str) + capsys.readouterr().out
    stored += "".join(everything(span) for span in exported.get_finished_spans())
    for absent in (SSN, "my ssn", "Blocked entity"):
        assert absent not in stored


def test_mentions_block_needs_a_400():
    assert agent_module.mentions_block(Exception(BLOCK)) is False  # no upstream status


def test_a_blocked_turns_spans_are_exported_without_content(gateway, exported):  # noqa: F811
    runtime, fake = gateway
    fake.script = [tool_call("whoami"), error(400, "invalid_request_error", BLOCK)]
    failure = blocked_turn(runtime)
    spans = [
        s for s in exported.get_finished_spans() if s.context.trace_id == int(failure.trace_id, 16)
    ]
    names = {span.name for span in spans}
    assert {"agent_turn", "retrieve_knowledge", "execute_tool", "call_llm"} <= names
    for span in spans:
        text = everything(span)
        for absent in (SSN, "my ssn", "please update", "whoami", "Blocked entity"):
            assert absent not in text, (span.name, absent)
        assert set(span.attributes) <= SKELETON, (span.name, sorted(span.attributes))
        for key, value in span.attributes.items():
            if key.startswith(("llm.token_count.", "gen_ai.usage.")):
                assert not isinstance(value, str)
        assert all(set(event.attributes) <= {"exception.type"} for event in span.events)
    (root,) = [span for span in spans if span.name == "agent_turn"]
    assert root.attributes["agentbreeder.guardrail.action"] == "blocked"
    assert root.attributes["agentbreeder.guardrail.entity"] == "US_SSN"
    assert root.attributes["agentbreeder.error_code"] == "MODEL_BLOCKED"
    assert root.attributes["agent.turn_id"] == failure.turn_id
    assert root.attributes["user.id"] == "cust-001" and root.attributes["session.id"] == "s1"
    assert "input.value" not in root.attributes and root.status.description is None


def telemetry_module():
    import telemetry

    return telemetry


# Every attribute a blocked turn's spans may carry, written out (not read from the runtime).
SKELETON = {
    "openinference.span.kind", "session.id", "user.id", "agent.turn_id", "agent.framework",
    "agent.name", "llm.model_name", "retrieval.mode", "gen_ai.operation.name", "gen_ai.system",
    "gen_ai.request.model", "agentbreeder.deployment_id", "agentbreeder.error_code",
    "agentbreeder.fallbacks", "agentbreeder.cached_prompt_tokens", "agentbreeder.subagent",
    "agentbreeder.guardrail.action", "agentbreeder.guardrail.name",
    "agentbreeder.guardrail.entity", "agentbreeder.skill.name", "agentbreeder.skill.version",
    "agentbreeder.skill.sha256", "agentbreeder.skill.resource",
    "llm.token_count.prompt", "llm.token_count.completion", "llm.token_count.total",
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
    "gen_ai.usage.cache_read.input_tokens",
}  # fmt: skip


def test_a_tool_name_the_model_made_up_is_not_on_a_blocked_turns_spans(gateway, exported):  # noqa: F811
    """A model can copy what matched into a tool name; the turn is blocked on the next call."""
    runtime, fake = gateway
    fake.script = [tool_call("jane.doe.536-22-8174"), error(400, "invalid_request_error", BLOCK)]
    failure = blocked_turn(runtime, "who am I?")
    spans = [
        s for s in exported.get_finished_spans() if s.context.trace_id == int(failure.trace_id, 16)
    ]
    assert len(spans) >= 4
    for span in spans:
        assert "536-22" not in everything(span) and "jane" not in everything(span), span.name
        assert set(span.attributes) <= SKELETON, (span.name, sorted(span.attributes))


def test_an_answered_turns_spans_keep_their_content(gateway, exported):  # noqa: F811
    runtime, fake = gateway
    fake.script = [ok("Hello there.")]
    reply = turn(runtime, message="where is my order?")
    spans = [
        s
        for s in exported.get_finished_spans()
        if s.context.trace_id == int(reply["trace_id"], 16)
    ]
    (root,) = [span for span in spans if span.name == "agent_turn"]
    assert root.attributes["input.value"] == "where is my order?"
    assert root.attributes["output.value"] == "Hello there."
    assert telemetry_module()._TURNS.held == {}  # nothing is kept after a turn


def test_a_turns_spans_are_held_until_the_turn_ends(gateway, exported, monkeypatch):  # noqa: F811
    """A span that ended before the block must not already be on its way out."""
    runtime, fake = gateway
    fake.script = [error(400, "invalid_request_error", BLOCK)]
    seen_before_the_end = []
    release = telemetry_module().release

    def watched(trace_id, blocked=False):
        seen_before_the_end.extend(exported.get_finished_spans())
        release(trace_id, blocked)

    monkeypatch.setattr(agent_module.telemetry, "release", watched)
    failure = blocked_turn(runtime)
    mine = int(failure.trace_id, 16)
    assert [s for s in seen_before_the_end if s.context.trace_id == mine] == []
    assert [s for s in exported.get_finished_spans() if s.context.trace_id == mine]


def _finished(provider, name, trace_of=None, **attributes):
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            current.set_attribute(key, value)
    return current


def _chain():
    import telemetry
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memory = InMemorySpanExporter()
    turns = telemetry.TurnSpans(SimpleSpanProcessor(memory))
    provider = TracerProvider()
    provider.add_span_processor(turns)
    return telemetry, provider, turns, memory


def test_bare_keeps_only_listed_attributes_and_the_first_word_of_the_name():
    from opentelemetry.trace import Status, StatusCode

    telemetry, provider, turns, memory = _chain()
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("execute_tool made_up_536") as current:
        trace_id = current.get_span_context().trace_id
        turns.hold(trace_id)
        current.set_attribute("openinference.span.kind", "TOOL")
        current.set_attribute("input.value", SSN)
        current.set_attribute("tool.name", "made_up_536")
        current.set_attribute("llm.token_count.prompt", 12)
        current.set_attribute("agentbreeder.subagent", "billing")
        current.set_attribute("agentbreeder.unknown_tools", ("made_up_536",))
        current.set_attribute("agentbreeder.unknown_tool", "made_up_536")
        current.set_attribute("agentbreeder.something_new", "not listed, so not kept")
        current.set_attribute("agentbreeder.served_by", ("a", "b"))
        current.set_attribute("llm.token_count.note", "text under a number prefix")
        current.set_status(Status(StatusCode.ERROR, f"failed with {SSN}"))
        current.add_event("exception", {"exception.type": "ValueError", "exception.message": SSN})
    assert memory.get_finished_spans() == ()
    turns.release(trace_id, blocked=True)
    (span,) = memory.get_finished_spans()
    assert span.name == "execute_tool"
    assert dict(span.attributes) == {
        "openinference.span.kind": "TOOL",
        "llm.token_count.prompt": 12,
        "agentbreeder.subagent": "billing",
    }
    assert span.status.status_code is StatusCode.ERROR and span.status.description is None
    assert [dict(e.attributes) for e in span.events] == [{"exception.type": "ValueError"}]
    assert span.context.span_id == current.get_span_context().span_id
    assert SSN not in everything(span)


def test_a_span_that_ends_after_its_blocked_turn_is_still_bare():
    telemetry, provider, turns, memory = _chain()
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("late") as late:
        trace_id = late.get_span_context().trace_id
        turns.hold(trace_id)
        late.set_attribute("input.value", SSN)
        turns.release(trace_id, blocked=True)
    (span,) = memory.get_finished_spans()
    assert "input.value" not in span.attributes
    for _ in range(telemetry.MAX_BLOCKED + 1):  # the memory of blocked traces is bounded
        with tracer.start_as_current_span("other") as other:
            turns.release(other.get_span_context().trace_id, blocked=True)
    assert len(turns.blocked) == telemetry.MAX_BLOCKED and trace_id not in turns.blocked


def test_spans_outside_a_turn_and_after_an_answered_turn_pass_unchanged():
    telemetry, provider, turns, memory = _chain()
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("warm up") as free:
        free.set_attribute("input.value", "kept")
    (span,) = memory.get_finished_spans()
    assert span.name == "warm up" and span.attributes["input.value"] == "kept"
    memory.clear()
    with tracer.start_as_current_span("agent_turn") as root:
        trace_id = root.get_span_context().trace_id
        turns.hold(trace_id)
        with tracer.start_as_current_span("child") as child:
            child.set_attribute("input.value", "kept")
    assert len(turns.held[trace_id]) == 2 and memory.get_finished_spans() == ()
    turns.release(trace_id, blocked=False)
    assert [s.attributes.get("input.value") for s in memory.get_finished_spans()] == ["kept", None]
    assert turns.held == {} and turns.blocked == {}


def test_spans_past_the_cap_go_out_bare(monkeypatch):
    telemetry, provider, turns, memory = _chain()
    monkeypatch.setattr(telemetry, "MAX_HELD", 2)
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("agent_turn") as root:
        trace_id = root.get_span_context().trace_id
        turns.hold(trace_id)
        for _ in range(3):
            with tracer.start_as_current_span("child") as child:
                child.set_attribute("input.value", SSN)
        (early,) = memory.get_finished_spans()  # the third: nobody knows the outcome yet
        assert "input.value" not in early.attributes
    turns.release(trace_id, blocked=False)
    assert len(memory.get_finished_spans()) == 4


def test_shutdown_exports_an_unfinished_turns_spans_bare():
    telemetry, provider, turns, memory = _chain()
    tracer = provider.get_tracer("t")
    with tracer.start_as_current_span("agent_turn") as root:
        turns.hold(root.get_span_context().trace_id)
        with tracer.start_as_current_span("child") as child:
            child.set_attribute("input.value", SSN)
        seen = []
        turns.inner.on_end = seen.append
        turns.shutdown()
        root.set_attribute("input.value", SSN)
    # The child at shutdown and the root after it: the turn's outcome is unknown, so both bare.
    assert [span.name for span in seen] == ["child", "agent_turn"] and turns.held == {}
    assert all("input.value" not in span.attributes for span in seen)
    assert turns.force_flush() is True


def test_hold_and_release_do_nothing_without_tracing(monkeypatch):
    import telemetry

    monkeypatch.setattr(telemetry, "_TURNS", None)
    telemetry.hold("0" * 31 + "1")
    telemetry.release("0" * 31 + "1", True)
    telemetry.hold(None)
    telemetry.release(None)


def test_setup_puts_the_turn_processor_in_front_of_the_exporter(monkeypatch):
    import telemetry
    from opentelemetry import trace

    installed = []
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    monkeypatch.setattr(telemetry, "_TURNS", None)
    telemetry.setup("p", "", enabled=True)
    assert telemetry._TURNS is None  # no endpoint, no export
    telemetry.setup("p", "http://127.0.0.1:9/v1/traces")
    try:
        assert isinstance(telemetry._TURNS, telemetry.TurnSpans)
        assert isinstance(telemetry._TURNS.inner, telemetry.BatchSpanProcessor)
        telemetry.hold("0" * 31 + "1")
        assert 1 in telemetry._TURNS.held
        telemetry.release("0" * 31 + "1", True)
        assert telemetry._TURNS.held == {} and 1 in telemetry._TURNS.blocked
    finally:
        telemetry._TURNS.inner.shutdown()


def test_a_failed_span_release_still_records_the_turn(gateway, monkeypatch, capsys):  # noqa: F811
    runtime, fake = gateway
    fake.script = [error(400, "invalid_request_error", BLOCK)]

    def broken(trace_id, blocked=False):
        raise OSError("exporter gone")

    monkeypatch.setattr(agent_module.telemetry, "release", broken)
    FakeMemory.last_turn = None
    blocked_turn(runtime)
    assert FakeMemory.last_turn["message"] == agent_module.BLOCKED_MESSAGE
    assert "span release failed: OSError" in capsys.readouterr().out
