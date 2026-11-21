"""runtime/deepagents@1.0.0 against real Deep Agents (#1083 F5, multi-framework spec §8.3).

These need the runtime's locked packages, so they run apart from the repo suite (CI job
`runtime-deepagents`):

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

from deepagents_adapter import (  # noqa: E402
    BUILTIN_TOOLS,
    TASK_TOOL,
    DeepAgentsAdapter,
    as_tool,
    reply_text,
    tool_name,
    turn_system_message,
)
from kit import config as cfg  # noqa: E402
from kit import telemetry  # noqa: E402
from kit.errors import ModelError  # noqa: E402
from kit.proxy import ModelProxy  # noqa: E402
from kit.turn import FALLBACK_REPLY, Kit  # noqa: E402

KEY = "gateway-key-0123456789abcdef"
PROMPT = "You are Northwind's support agent."


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


def answer(text=None, tool=None, args=None, calls=None):
    """A chat completion: text, one tool call, or several (`calls`: [(name, args), ...])."""
    message = {"role": "assistant", "content": text}
    calls = calls or ([(tool, args or {})] if tool else [])
    if calls:
        message["tool_calls"] = [
            {
                "id": f"call_{index}_{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
            for index, (name, arguments) in enumerate(calls)
        ]
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gemini",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2600, "completion_tokens": 9},
        },
        headers={"x-litellm-response-cost": "0.0004", "x-litellm-model-group": "support-model"},
    )


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    (tmp_path / "prompt").mkdir()
    (tmp_path / "prompt" / "prompt.md").write_text(PROMPT + "\n")
    tool = tmp_path / "tool" / "orders"
    (tool / "src").mkdir(parents=True)
    (tool / "tool.yaml").write_text(
        "functions:\n"
        "  - name: lookup_order\n"
        "    module: da_test_orders\n"
        "    description: Look up an order by id.\n"
        "    parameters: {type: object, properties: {order_id: {type: string},"
        " detail: {type: boolean}}, required: [order_id]}\n"
    )
    (tool / "src" / "da_test_orders.py").write_text(
        "def lookup_order(rt, order_id, detail=False):\n"
        "    return {'order': order_id, 'customer': rt.customer, 'status': 'shipped'}\n"
    )
    FakeMemory.HISTORY, FakeMemory.log = [], []
    telemetry.setup("da-test", "", enabled=False)
    return tmp_path


CONFIG = {
    "name": "northwind-support-deepagents",
    "model": {"alias": "support-model", "reasoning": "low", "max_model_calls": 4},
    "prompt": {"dir": "prompt"},
    "knowledge": [],
    "tools": [{"name": "orders", "version": "1.0.0", "dir": "tool/orders"}],
    "memory": {"sessions": "postgres", "notes": True},
}


def run(handler, *turns, config=None):
    """Turns through one Kit with the Deep Agents adapter; each is (session, customer,
    message). Turns given as a list run concurrently."""

    async def main():
        proxy = ModelProxy("http://gateway:4000", KEY, httpx.MockTransport(handler))
        kit = Kit(
            config or CONFIG,
            "postgresql://unused",
            KEY,
            DeepAgentsAdapter(),
            runtime_ref="runtime/deepagents@1.0.0",
            framework="deepagents",
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


def names(body) -> set[str]:
    return {t["function"]["name"] for t in body.get("tools") or []}


def is_main(body) -> bool:
    """The main agent is offered `task`; the sub-agent is not."""
    return TASK_TOOL in names(body)


class Scripted:
    """Answers by caller (main agent or sub-agent) and by step within that caller's
    conversation; records each upstream body."""

    def __init__(self, main, sub=None):
        self.main, self.sub = main, sub or [answer("Sub-agent done.")]
        self.bodies = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        script = self.main if is_main(body) else self.sub
        step = sum(1 for m in body["messages"] if m["role"] == "assistant" and m.get("tool_calls"))
        return script[min(step, len(script) - 1)]


def model_rows():
    return [entry[2] for entry in FakeMemory.log if entry[0] == "model_called"]


def test_a_northwind_shaped_turn(app):
    gateway = Scripted(
        [answer(tool="lookup_order", args={"order_id": "1042"}), answer("It shipped.")]
    )
    FakeMemory.HISTORY = [("Hi", "Hello! How can I help?")]
    (reply,) = run(gateway, ("s-1", "c-1", "Where is order 1042?"))
    assert reply["reply"] == "It shipped."
    assert reply["framework"] == "deepagents" and reply["runtime"] == "runtime/deepagents@1.0.0"
    assert reply["tools"] == ["lookup_order"] and reply["model_calls"] == 2
    assert reply["cost_usd"] == 0.0008 and reply["served_by"] == ["support-model"]
    first, second = gateway.bodies
    assert [m["role"] for m in first["messages"]] == ["system", "user", "assistant", "user"]
    system = first["messages"][0]["content"]
    # The agent's prompt and the customer's notes, exactly: Deep Agents 0.7.32 adds no system
    # prompt section of its own with this set-up (its tools carry their own descriptions).
    assert system.startswith(PROMPT) and system.endswith("</customer_memory>")
    assert first["model"] == "support-model" and first["reasoning_effort"] == "low"
    # The agent's tool plus exactly Deep Agents' built-ins (the manifest's reserved names).
    assert names(first) == {"lookup_order", TASK_TOOL} | BUILTIN_TOOLS
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
    # Both calls are the agent's own: the proxy's main-loop test matches Deep Agents' prompt.
    assert [row["main_loop"] for row in model_rows()] == [True, True]


def test_the_task_sub_agent_counts_against_the_cap_and_its_tools_are_audited(app):
    """D10: `task` is kept. Its sub-agent's calls go through the same proxy and turn token,
    count in `model_calls`, are hidden calls (not main loop), and its tool calls are the
    agent's, with the server's customer."""
    gateway = Scripted(
        main=[
            answer(tool=TASK_TOOL, args={"description": "Look up order 7",
                                          "subagent_type": "general-purpose"}),
            answer("Order 7 shipped."),
        ],
        sub=[answer(tool="lookup_order", args={"order_id": "7"}), answer("It shipped.")],
    )  # fmt: skip
    (reply,) = run(gateway, ("s-1", "c-9", "Where is order 7?"))
    assert reply["reply"] == "Order 7 shipped."
    assert reply["model_calls"] == 4 and reply["tools"] == ["lookup_order"]
    assert [is_main(b) for b in gateway.bodies] == [True, False, False, True]
    sub = gateway.bodies[1]
    assert names(sub) == {"lookup_order"} | BUILTIN_TOOLS  # no `task` for the sub-agent
    assert PROMPT not in sub["messages"][0]["content"]  # nor the agent's prompt or notes
    assert "<customer_memory>" not in json.dumps(sub["messages"])
    assert [row["main_loop"] for row in model_rows()] == [True, False, False, True]
    (tool_row,) = [entry for entry in FakeMemory.log if entry[0] == "tool:lookup_order"]
    assert tool_row[1] == "c-9" and tool_row[2]["ok"] is True
    assert json.loads(gateway.bodies[2]["messages"][-1]["content"])["customer"] == "c-9"


def test_a_cap_reached_inside_the_sub_agent_fails_the_turn(app):
    """The Nth call is the sub-agent's (a hidden call): its text is not an answer to the
    customer, so when the main agent's next call is refused the turn fails MODEL_STEP_LIMIT."""
    gateway = Scripted(
        main=[
            answer(tool=TASK_TOOL, args={"description": "Look up order 7",
                                          "subagent_type": "general-purpose"}),
            answer("Order 7 shipped."),
        ],
        sub=[answer(tool="lookup_order", args={"order_id": "7"}), answer("It shipped.")],
    )  # fmt: skip
    config = {**CONFIG, "model": {**CONFIG["model"], "max_model_calls": 3}}
    (error,) = run(gateway, ("s-1", "c-1", "Where is order 7?"), config=config)
    assert isinstance(error, ModelError) and error.code == "MODEL_STEP_LIMIT"
    assert len(gateway.bodies) == 3  # the 4th call was refused by the proxy, never sent
    assert gateway.bodies[2]["tool_choice"] == "none"
    assert [row["main_loop"] for row in model_rows()] == [True, False, False]


def test_the_step_cap_holds_when_the_model_ignores_tool_choice_none(app):
    """Gemini ignores `tool_choice: none` (#1111): the scripted gateway keeps asking for tools,
    Deep Agents never runs the Nth call's tool, and the turn fails MODEL_STEP_LIMIT."""
    gateway = Scripted([answer(tool="lookup_order", args={"order_id": "1"})])
    config = {**CONFIG, "model": {**CONFIG["model"], "max_model_calls": 2}}
    (error,) = run(gateway, ("s-1", "c-1", "Loop forever"), config=config)
    assert isinstance(error, ModelError) and error.code == "MODEL_STEP_LIMIT"
    assert len(gateway.bodies) == 2 and gateway.bodies[1]["tool_choice"] == "none"
    tool_rows = [entry for entry in FakeMemory.log if entry[0] == "tool:lookup_order"]
    assert len(tool_rows) == 1  # the first call's tool ran; the withheld one never did
    (limited,) = [row for row in model_rows() if row.get("step_limited")]
    assert limited["step"] == 2


def test_a_main_loop_nth_answer_is_the_reply(app):
    """The Nth call is the agent's own and answers in text: that is the reply."""
    gateway = Scripted(
        [answer(tool="lookup_order", args={"order_id": "1"}), answer("It shipped.")]
    )
    config = {**CONFIG, "model": {**CONFIG["model"], "max_model_calls": 2}}
    (reply,) = run(gateway, ("s-1", "c-1", "order 1?"), config=config)
    assert reply["reply"] == "It shipped." and reply["model_calls"] == 2
    assert gateway.bodies[1]["tool_choice"] == "none"


def test_file_tools_act_on_the_turns_state_only(app, tmp_path):
    """Spec §8.3's framework surface, with a scripted model: no `execute` tool is offered, a
    host path read is not found, listing and searching the host's directories find nothing,
    and a write lands in graph state, not on disk."""
    target = tmp_path / "written-by-model.txt"
    gateway = Scripted(
        [
            answer(calls=[("read_file", {"file_path": "/etc/passwd"}),
                          ("ls", {"path": "/"}),
                          ("glob", {"pattern": "/etc/*"}),
                          ("grep", {"pattern": "root", "path": "/etc"}),
                          ("write_file", {"file_path": str(target), "content": "x"})]),
            answer("Done."),
        ]
    )  # fmt: skip
    (reply,) = run(gateway, ("s-1", "c-1", "poke around"))
    assert reply["reply"] == "Done." and reply["tools"] == []
    assert "execute" not in names(gateway.bodies[0])
    results = {m["tool_call_id"]: m["content"] for m in gateway.bodies[1]["messages"]
               if m["role"] == "tool"}  # fmt: skip
    assert "root:" not in json.dumps(results)
    assert not target.exists()


def test_files_written_in_one_turn_are_gone_in_the_next(app):
    """The state backend lives in the turn's graph state, and there is no checkpointer: a file
    the model wrote in one turn is not there in the next turn of the same session."""
    writer = Scripted(
        [answer(tool="write_file", args={"file_path": "/notes.txt", "content": "secret"}),
         answer("Saved.")]
    )  # fmt: skip
    reader = Scripted([answer(tool="read_file", args={"file_path": "/notes.txt"}),
                       answer("Read.")])  # fmt: skip

    def handler(request):
        last_user = [m for m in json.loads(request.content)["messages"] if m["role"] == "user"]
        return (reader if "read it" in last_user[-1]["content"] else writer)(request)

    first, second = run(handler, ("s-1", "c-1", "save a note"), ("s-1", "c-1", "read it"))
    assert (first["reply"], second["reply"]) == ("Saved.", "Read.")
    (result,) = [m["content"] for m in reader.bodies[1]["messages"] if m["role"] == "tool"]
    assert "secret" not in result


def test_a_failing_gateway_is_a_coded_error_and_not_retried_by_langchain(app):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"error": {"type": "api_error", "message": "SECRET"}})

    (error,) = run(handler, ("s-1", "c-1", "hi"))
    assert isinstance(error, ModelError) and error.code == "MODEL_UNAVAILABLE"
    assert len(calls) == 3  # the proxy's three tries; LangChain's own retries are off


def test_concurrent_sessions_keep_their_own_customer_and_notes_prompt(app):
    gateway = Scripted([answer(tool="lookup_order", args={"order_id": "7"}), answer("Done.")])
    (pair,) = run(gateway, [("s-a", "alice", "order 7?"), ("s-b", "bob", "order 7?")])
    assert [r["customer_id"] for r in pair] == ["alice", "bob"]
    seen = sorted(
        json.loads(b["messages"][-1]["content"])["customer"]
        for b in gateway.bodies
        if b["messages"][-1]["role"] == "tool"
    )
    assert seen == ["alice", "bob"]
    assert all(row["main_loop"] for row in model_rows())


def test_a_tool_with_bad_arguments_is_reported_to_the_model(app):
    gateway = Scripted([answer(tool="lookup_order", args={"detail": True}),
                        answer("Need an id.")])  # fmt: skip
    (reply,) = run(gateway, ("s-1", "c-1", "order?"))
    assert reply["reply"] == "Need an id."
    rows = [entry for entry in FakeMemory.log if entry[0] == "tool:lookup_order"]
    assert rows and rows[0][2]["ok"] is False


def test_an_unparseable_tool_call_is_not_an_answer(app):
    bad = answer("Let me check that order.")
    body = bad.json()
    body["choices"][0]["message"]["tool_calls"] = [
        {"id": "c1", "type": "function", "function": {"name": "lookup_order", "arguments": "{bad"}}
    ]
    (reply,) = run(lambda request: httpx.Response(200, json=body), ("s-1", "c-1", "order?"))
    assert reply["reply"] == FALLBACK_REPLY and reply["tools"] == []


def test_langsmith_variables_are_removed_by_the_entry_point(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    source = (RUNTIME / "server.py").read_text()
    namespace: dict = {"__name__": "not_main", "__file__": str(RUNTIME / "server.py")}
    exec(compile(source, "server.py", "exec"), namespace)  # noqa: S102 - our own entry point
    assert "LANGSMITH_API_KEY" not in os.environ and "LANGCHAIN_TRACING_V2" not in os.environ
    assert namespace["RUNTIME_REF"] == "runtime/deepagents@1.0.0"


def test_the_manifest_reserves_exactly_the_built_in_tools():
    import yaml

    manifest = yaml.safe_load((RUNTIME.parent / "manifest.yaml").read_text())
    assert set(manifest["reserved_tool_names"]) == (
        {"search_knowledge_base", TASK_TOOL} | BUILTIN_TOOLS
    )


def test_helpers():
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    assert reply_text(HumanMessage("x")) == ""
    assert reply_text(AIMessage("final")) == "final"
    assert turn_system_message("P", None).content == "P"
    blocks = [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]
    framework = SystemMessage(content=blocks)
    assert turn_system_message("P", framework).content == "P\n\nAB"
    assert tool_name({"type": "function", "function": {"name": "x"}}) == "x"
    assert tool_name({"name": "y"}) == "y" and tool_name(object()) is None

    class Function:
        name, description = "f", ""
        parameters = {"type": "object", "properties": {}}

        async def __call__(self, **kwargs):
            return "ok"

    tool = as_tool(Function())
    assert tool.name == "f" and tool.description == "f" and tool_name(tool) == "f"
    assert asyncio.run(tool.ainvoke({})) == "ok"


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
        [answer(tool="search_knowledge_base", args={"query": "sync tablet"}),
         answer("Open Settings.")]
    )  # fmt: skip
    (reply,) = run(gateway, ("s-1", "c-1", "How do I sync?"), config=config)
    assert reply["reply"] == "Open Settings." and reply["sources"] == ["Syncing"]
    assert reply["tools"] == ["search_knowledge_base"] and reply["kb_searches"] == 1
    assert gateway.bodies[0]["messages"][-1]["content"].startswith("<knowledge_base_results>")
    assert {"search_knowledge_base", "lookup_order"} <= names(gateway.bodies[0])


def test_the_instrumentor_nests_under_the_kits_spans_and_tool_spans_are_relabelled(app):
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    adapter = DeepAgentsAdapter()
    adapter.instrument(provider)
    try:
        gateway = Scripted([answer(tool="lookup_order", args={"order_id": "1"}),
                            answer("Done.")])  # fmt: skip
        run(gateway, ("s-1", "c-1", "order 1?"))
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
    assert root.attributes["agent.framework"] == "deepagents"
