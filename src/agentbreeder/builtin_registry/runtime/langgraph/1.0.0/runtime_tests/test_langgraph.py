"""runtime/langgraph@1.0.0 against real LangGraph (#1083 F3, multi-framework spec §8.2).

These need the runtime's locked packages, so they run apart from the repo suite (CI job
`runtime-langgraph`):

    pip install --require-hashes --only-binary=:all: -r runtime/requirements.lock
    pip install pytest==8.4.2
    pytest runtime_tests

The gateway is a scripted httpx MockTransport behind the kit's real loopback proxy; memory is an
in-process fake. Nothing here reaches a network or a database.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

from kit import config as cfg  # noqa: E402
from kit import telemetry  # noqa: E402
from kit.errors import ModelError  # noqa: E402
from kit.proxy import ModelProxy  # noqa: E402
from kit.turn import Kit  # noqa: E402
from langgraph_adapter import LangGraphAdapter, as_tool, reply_text  # noqa: E402

KEY = "gateway-key-0123456789abcdef"


class FakeMemory:
    db = object()
    HISTORY: list = []
    log: list = []

    def __init__(self, database_url, events=True):
        pass

    def audit(self, turn, session, customer, actor, action, detail):
        FakeMemory.log.append((action, customer, detail))

    def record_turn(self, **turn):
        FakeMemory.log.append(("turn", turn["customer"], turn))

    def history(self, customer, session):
        return list(FakeMemory.HISTORY)

    def notes(self, customer):
        return ["Prefers email"]

    def close(self):
        pass


def answer(text=None, tool=None, args=None):
    message = {"role": "assistant", "content": text}
    if tool:
        message["tool_calls"] = [
            {
                "id": f"call_{tool}",
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args or {})},
            }
        ]
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gemini",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 176, "completion_tokens": 9},
        },
        headers={"x-litellm-response-cost": "0.0004", "x-litellm-model-group": "support-model"},
    )


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    (tmp_path / "prompt").mkdir()
    (tmp_path / "prompt" / "prompt.md").write_text("You are Northwind's support agent.\n")
    tool = tmp_path / "tool" / "orders"
    (tool / "src").mkdir(parents=True)
    (tool / "tool.yaml").write_text(
        "functions:\n"
        "  - name: lookup_order\n"
        "    module: lg_test_orders\n"
        "    description: Look up an order by id.\n"
        "    parameters: {type: object, properties: {order_id: {type: string},"
        " detail: {type: boolean}}, required: [order_id]}\n"
    )
    (tool / "src" / "lg_test_orders.py").write_text(
        "def lookup_order(rt, order_id, detail=False):\n"
        "    return {'order': order_id, 'customer': rt.customer, 'status': 'shipped'}\n"
    )
    FakeMemory.HISTORY, FakeMemory.log = [], []
    telemetry.setup("lg-test", "", enabled=False)
    return tmp_path


CONFIG = {
    "name": "northwind-support-langgraph",
    "model": {"alias": "support-model", "reasoning": "low", "max_model_calls": 4},
    "prompt": {"dir": "prompt"},
    "knowledge": [],
    "tools": [{"name": "orders", "version": "1.0.0", "dir": "tool/orders"}],
    "memory": {"sessions": "postgres", "notes": True},
}


def run(handler, *turns, config=None):
    """Turns through one Kit with the LangGraph adapter; each is (session, customer, message).
    Turns given as a list run concurrently."""

    async def main():
        proxy = ModelProxy("http://gateway:4000", KEY, httpx.MockTransport(handler))
        kit = Kit(
            config or CONFIG,
            "postgresql://unused",
            KEY,
            LangGraphAdapter(),
            runtime_ref="runtime/langgraph@1.0.0",
            framework="langgraph",
            proxy=proxy,
            memory_factory=FakeMemory,
        )
        await kit.start()

        async def one(session, customer, message):
            try:
                return await kit.chat_async(session, customer, message)
            except ModelError as error:
                return error

        try:
            results = []
            for item in turns:
                if isinstance(item, list):
                    results.append(await asyncio.gather(*(one(*t) for t in item)))
                else:
                    results.append(await one(*item))
            return results
        finally:
            await kit.close()

    return asyncio.run(main())


class Scripted:
    """Answers by call position within a session's turn; records each upstream body."""

    def __init__(self, *answers):
        self.answers = answers
        self.bodies = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        step = sum(1 for m in body["messages"] if m["role"] == "assistant" and m.get("tool_calls"))
        return self.answers[min(step, len(self.answers) - 1)]


def test_a_northwind_shaped_turn(app):
    gateway = Scripted(
        answer(tool="lookup_order", args={"order_id": "1042"}), answer("It shipped.")
    )
    FakeMemory.HISTORY = [("Hi", "Hello! How can I help?")]
    (reply,) = run(gateway, ("s-1", "c-1", "Where is order 1042?"))
    assert reply["reply"] == "It shipped."
    assert reply["framework"] == "langgraph" and reply["runtime"] == "runtime/langgraph@1.0.0"
    assert reply["tools"] == ["lookup_order"] and reply["model_calls"] == 2
    assert reply["cost_usd"] == 0.0008 and reply["served_by"] == ["support-model"]
    assert (reply["prompt_tokens"], reply["output_tokens"]) == (352, 18)
    first, second = gateway.bodies
    assert [m["role"] for m in first["messages"]] == ["system", "user", "assistant", "user"]
    assert "<customer_memory>" in first["messages"][0]["content"]
    assert first["model"] == "support-model" and first["reasoning_effort"] == "low"
    (tool,) = first["tools"]
    assert tool["function"]["name"] == "lookup_order"
    assert tool["function"]["parameters"]["required"] == ["order_id"]
    tool_result = json.loads(second["messages"][-1]["content"])
    assert tool_result == {"order": "1042", "customer": "c-1", "status": "shipped"}
    actions = [entry[0] for entry in FakeMemory.log]
    assert actions[:5] == [
        "message_received",
        "model_called",
        "tool:lookup_order",
        "model_called",
        "reply_sent",
    ]


def test_the_step_cap_holds_when_the_model_ignores_tool_choice_none(app):
    """Gemini ignores `tool_choice: none` (#1111): the scripted gateway keeps asking for tools,
    LangGraph never runs the Nth call's tool, and the turn fails MODEL_STEP_LIMIT."""
    gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}))
    config = {**CONFIG, "model": {**CONFIG["model"], "max_model_calls": 2}}
    (error,) = run(gateway, ("s-1", "c-1", "Loop forever"), config=config)
    assert isinstance(error, ModelError) and error.code == "MODEL_STEP_LIMIT"
    assert len(gateway.bodies) == 2 and gateway.bodies[1]["tool_choice"] == "none"
    tool_rows = [entry for entry in FakeMemory.log if entry[0] == "tool:lookup_order"]
    assert len(tool_rows) == 1  # the first call's tool ran; the withheld one never did
    (limited,) = [e for e in FakeMemory.log if e[0] == "model_called" and e[2].get("step_limited")]
    assert limited[2]["step"] == 2


def test_a_failing_gateway_is_a_coded_error_and_not_retried_by_langchain(app):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"error": {"type": "api_error", "message": "SECRET"}})

    (error,) = run(handler, ("s-1", "c-1", "hi"))
    assert isinstance(error, ModelError) and error.code == "MODEL_UNAVAILABLE"
    assert len(calls) == 3  # the proxy's three tries; LangChain's own retries are off


def test_concurrent_sessions_keep_their_own_customer_in_tools(app):
    gateway = Scripted(answer(tool="lookup_order", args={"order_id": "7"}), answer("Done."))
    (pair,) = run(gateway, [("s-a", "alice", "order 7?"), ("s-b", "bob", "order 7?")])
    assert [r["customer_id"] for r in pair] == ["alice", "bob"]
    seen = sorted(
        json.loads(b["messages"][-1]["content"])["customer"]
        for b in gateway.bodies
        if b["messages"][-1]["role"] == "tool"
    )
    assert seen == ["alice", "bob"]


def test_a_tool_with_bad_arguments_is_reported_to_the_model(app):
    gateway = Scripted(answer(tool="lookup_order", args={"detail": True}), answer("Need an id."))
    (reply,) = run(gateway, ("s-1", "c-1", "order?"))
    assert reply["reply"] == "Need an id."
    rows = [entry for entry in FakeMemory.log if entry[0] == "tool:lookup_order"]
    assert rows and rows[0][2]["ok"] is False


def test_langsmith_variables_are_removed_by_the_entry_point(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    source = (RUNTIME / "server.py").read_text()
    namespace: dict = {"__name__": "not_main", "__file__": str(RUNTIME / "server.py")}
    exec(compile(source, "server.py", "exec"), namespace)  # noqa: S102 - our own entry point
    assert "LANGSMITH_API_KEY" not in os.environ and "LANGCHAIN_TRACING_V2" not in os.environ
    assert namespace["RUNTIME_REF"] == "runtime/langgraph@1.0.0"


def test_helpers():
    from langchain_core.messages import AIMessage, HumanMessage

    assert reply_text(HumanMessage("x")) == ""
    assert reply_text(AIMessage("final")) == "final"

    class Function:
        name, description = "f", ""
        parameters = {"type": "object", "properties": {}}

        async def __call__(self, **kwargs):
            return "ok"

    tool = as_tool(Function())
    assert tool.name == "f" and tool.description == "f"
    assert asyncio.run(tool.ainvoke({})) == "ok"


def test_an_unparseable_tool_call_is_not_an_answer(app):
    """LangChain puts a tool call with arguments it cannot parse in `invalid_tool_calls` and
    runs nothing; "let me check" must not read as a successful reply."""
    bad = answer("Let me check that order.")
    body = bad.json()
    body["choices"][0]["message"]["tool_calls"] = [
        {"id": "c1", "type": "function", "function": {"name": "lookup_order", "arguments": "{bad"}}
    ]
    (reply,) = run(lambda request: httpx.Response(200, json=body), ("s-1", "c-1", "order?"))
    from kit.turn import FALLBACK_REPLY

    assert reply["reply"] == FALLBACK_REPLY and reply["tools"] == []


class FakeRetriever:
    description = "Northwind's help centre."
    sets = [object()]

    def search(self, db, query, limit):
        return [{"id": "a#1", "title": "Syncing", "text": "Open Settings.", "mode": "hybrid"}]


def test_knowledge_before_the_call_and_the_search_tool(app, monkeypatch):
    import kit.turn

    monkeypatch.setattr(kit.turn, "Retriever", lambda sets: FakeRetriever())
    monkeypatch.setattr(kit.turn, "KnowledgeSet", lambda *a: None)
    config = {
        **CONFIG,
        "knowledge": [{"name": "help", "version": "1.0.0", "dir": "kb", "top_k": 5,
                       "extra_searches": 1}],
    }  # fmt: skip
    gateway = Scripted(
        answer(tool="search_knowledge_base", args={"query": "sync tablet"}),
        answer("Open Settings."),
    )
    (reply,) = run(gateway, ("s-1", "c-1", "How do I sync?"), config=config)
    assert reply["reply"] == "Open Settings." and reply["sources"] == ["Syncing"]
    assert reply["tools"] == ["search_knowledge_base"] and reply["kb_searches"] == 1
    assert gateway.bodies[0]["messages"][-1]["content"].startswith("<knowledge_base_results>")
    names = {t["function"]["name"] for t in gateway.bodies[0]["tools"]}
    assert names == {"search_knowledge_base", "lookup_order"}


def test_the_instrumentor_nests_under_the_kits_spans_and_tool_spans_are_relabelled(app):
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    # The process's provider is set once (the fixture's); add an exporter to it.
    provider = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    adapter = LangGraphAdapter()
    adapter.instrument(provider)
    try:
        gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}), answer("Done."))
        (reply,) = run(gateway, ("s-1", "c-1", "order 1?"))
        provider.force_flush()
    finally:
        from openinference.instrumentation.langchain import LangChainInstrumentor

        LangChainInstrumentor().uninstrument()
    exported = [telemetry.RedactingExporter.redacted(s) for s in exporter.get_finished_spans()]
    kinds = [(s.name, s.attributes.get("openinference.span.kind")) for s in exported]
    tool_spans = [name for name, kind in kinds if kind == "TOOL"]
    assert tool_spans == ["lookup_order"]  # the kit's own; LangChain's is CHAIN
    assert any(kind == "LLM" for _, kind in kinds)
    root = [s for s in exported if s.name == "agent_turn"][0]
    assert all(s.context.trace_id == root.context.trace_id for s in exported)
    assert root.attributes["agent.framework"] == "langgraph"
