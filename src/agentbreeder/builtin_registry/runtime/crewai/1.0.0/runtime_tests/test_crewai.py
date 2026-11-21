"""runtime/crewai@1.0.0 against real CrewAI (#1090, multi-framework spec §8.5).

These need the runtime's locked packages, so they run apart from the repo suite (CI job
`runtime-crewai`):

    pip install --require-hashes --only-binary=:all: -r runtime/requirements.lock
    pip install pytest==8.4.2
    pytest runtime_tests

The gateway is a scripted httpx MockTransport behind the kit's real loopback proxy; memory is an
in-process fake. Nothing here reaches a network or a database. `HOME` is a fresh directory for
the whole module, so the storage tests can look at every file CrewAI writes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HOME = Path(tempfile.mkdtemp(prefix="crewai-home-"))
os.environ["HOME"] = str(HOME)
# Its own temp dir too, so the storage test can read every file written there.
TMP = HOME / "tmp"
TMP.mkdir()
os.environ["TMPDIR"] = str(TMP)
tempfile.tempdir = None
os.environ.pop("XDG_DATA_HOME", None)
RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
# The entry point's environment and storage confinement, before CrewAI is imported.
_source = (RUNTIME / "server.py").read_text()
_entry: dict = {"__name__": "not_main", "__file__": str(RUNTIME / "server.py")}
exec(compile(_source, "server.py", "exec"), _entry)  # noqa: S102 - our own entry point

import httpx  # noqa: E402
import pytest  # noqa: E402
from crewai_adapter import CrewAIAdapter, as_tool, reply_text  # noqa: E402
from kit import config as cfg  # noqa: E402
from kit import telemetry  # noqa: E402
from kit.errors import ModelError  # noqa: E402
from kit.proxy import ModelProxy  # noqa: E402
from kit.turn import Kit  # noqa: E402

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


def answer(text=None, tool=None, args=None, raw_args=None):
    message = {"role": "assistant", "content": text}
    if tool:
        message["tool_calls"] = [
            {
                "id": f"call_{tool}",
                "type": "function",
                "function": {
                    "name": tool,
                    "arguments": raw_args if raw_args is not None else json.dumps(args or {}),
                },
            }
        ]
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": "gemini",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if tool else "stop",
                }
            ],
            "usage": {"prompt_tokens": 176, "completion_tokens": 9, "total_tokens": 185},
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
        "    module: crew_test_orders\n"
        "    description: Look up an order by id.\n"
        "    parameters: {type: object, properties: {order_id: {type: string},"
        " detail: {type: boolean}}, required: [order_id]}\n"
    )
    (tool / "src" / "crew_test_orders.py").write_text(
        "def lookup_order(rt, order_id, detail=False):\n"
        "    return {'order': order_id, 'customer': rt.customer, 'status': 'shipped',"
        " 'detail': detail}\n"
    )
    FakeMemory.HISTORY, FakeMemory.log = [], []
    telemetry.setup("crew-test", "", enabled=False)
    return tmp_path


CONFIG = {
    "name": "northwind-support-crewai",
    "description": "Customer support for Northwind Notes and the Northwind Pen.",
    "model": {"alias": "support-model", "reasoning": "low", "max_model_calls": 4},
    "prompt": {"dir": "prompt"},
    "knowledge": [],
    "tools": [{"name": "orders", "version": "1.0.0", "dir": "tool/orders"}],
    "memory": {"sessions": "postgres", "notes": True},
}


def run(handler, *turns, config=None):
    """Turns through one Kit with the CrewAI adapter; each is (session, customer, message).
    Turns given as a list run concurrently."""

    async def main():
        proxy = ModelProxy("http://gateway:4000", KEY, httpx.MockTransport(handler))
        kit = Kit(
            config or CONFIG,
            "postgresql://unused",
            KEY,
            CrewAIAdapter(),
            runtime_ref="runtime/crewai@1.0.0",
            framework="crewai",
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
    """Answers by call position within a turn (tool results so far); records each body."""

    def __init__(self, *answers):
        self.answers = answers
        self.bodies = []

    def __call__(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        step = sum(1 for m in body["messages"] if m["role"] == "assistant" and m.get("tool_calls"))
        return self.answers[min(step, len(self.answers) - 1)]


def model_rows():
    return [entry[2] for entry in FakeMemory.log if entry[0] == "model_called"]


def tool_rows(name="tool:lookup_order"):
    return [entry for entry in FakeMemory.log if entry[0] == name]


def files_under(*roots, since: float = 0.0):
    """Files under the roots (modified at or after `since`); others' files may come and go."""
    found = []
    for root in roots:
        for path in Path(root).rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime >= since:
                    found.append(path)
            except OSError:
                continue
    return sorted(found)


def read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def test_a_northwind_shaped_turn(app):
    gateway = Scripted(
        answer(tool="lookup_order", args={"order_id": "1042", "detail": None}),
        answer("It shipped."),
    )
    FakeMemory.HISTORY = [("Hi", "Hello! How can I help?")]
    (reply,) = run(gateway, ("s-1", "c-1", "Where is order 1042?"))
    assert reply["reply"] == "It shipped."
    assert reply["framework"] == "crewai" and reply["runtime"] == "runtime/crewai@1.0.0"
    assert reply["tools"] == ["lookup_order"] and reply["model_calls"] == 2
    assert reply["cost_usd"] == 0.0008 and reply["served_by"] == ["support-model"]
    assert (reply["prompt_tokens"], reply["output_tokens"]) == (352, 18)
    first, second = gateway.bodies
    # History as structured messages (spec §7.5), never pasted into the prompt.
    assert [m["role"] for m in first["messages"]] == ["system", "user", "assistant", "user"]
    assert first["messages"][1:3] == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello! How can I help?"},
    ]
    system = first["messages"][0]["content"]
    assert "You are Northwind's support agent." in system and "<customer_memory>" in system
    assert first["model"] == "support-model" and first["reasoning_effort"] == "low"
    assert first["tool_choice"] == "auto" and "stream" not in first
    (tool,) = first["tools"]
    assert tool["function"]["name"] == "lookup_order"
    assert tool["function"]["description"] == "Look up an order by id."
    assert set(tool["function"]["parameters"]["properties"]) == {"order_id", "detail"}
    # The kit ran the tool with the server-side customer; the null for the optional argument
    # (strict mode's "not given") was dropped, so the function's default applied.
    tool_result = json.loads(second["messages"][-1]["content"])
    expected = {"order": "1042", "customer": "c-1", "status": "shipped", "detail": False}
    assert tool_result == expected
    (row,) = tool_rows()
    assert row[2]["args"] == {"order_id": "1042"} and row[2]["ok"] is True
    actions = [entry[0] for entry in FakeMemory.log]
    assert actions[:5] == [
        "message_received",
        "model_called",
        "tool:lookup_order",
        "model_called",
        "reply_sent",
    ]


def test_crewais_own_steps_are_main_loop_calls(app):
    """§16.1 (F6), stub level: the proxy's main-loop test (system prompt hash plus tool set)
    recognises CrewAI's own reasoning steps, though CrewAI wraps the prompt in its own."""
    gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}), answer("Done."))
    run(gateway, ("s-1", "c-1", "order 1?"))
    rows = model_rows()
    assert [r["main_loop"] for r in rows] == [True, True]


def test_the_step_cap_holds_when_the_model_ignores_tool_choice_none(app):
    """Gemini ignores `tool_choice: none` (#1111): the scripted gateway keeps asking for tools,
    CrewAI never runs the Nth call's tool, and the turn fails MODEL_STEP_LIMIT."""
    gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}))
    config = {**CONFIG, "model": {**CONFIG["model"], "max_model_calls": 2}}
    (error,) = run(gateway, ("s-1", "c-1", "Loop forever"), config=config)
    assert isinstance(error, ModelError) and error.code == "MODEL_STEP_LIMIT"
    assert len(gateway.bodies) == 2 and gateway.bodies[1]["tool_choice"] == "none"
    assert len(tool_rows()) == 1  # the first call's tool ran; the withheld one never did
    (limited,) = [r for r in model_rows() if r.get("step_limited")]
    assert limited["step"] == 2


def test_the_last_allowed_call_answering_with_text_is_the_reply(app):
    """With max_model_calls 2, the Nth call goes with function calling off and answers with
    text: CrewAI finishes normally and that text is the reply. (The proxy's refused-(N+1)th
    path is the kit's own test, `tests/test_runtime_kit_proxy.py`.)"""
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return answer(tool="lookup_order", args={"order_id": "1"})
        return answer("It shipped yesterday.")

    config = {**CONFIG, "model": {**CONFIG["model"], "max_model_calls": 2}}
    (reply,) = run(handler, ("s-1", "c-1", "order 1?"), config=config)
    assert reply["reply"] == "It shipped yesterday." and reply["model_calls"] == 2


def test_a_failing_gateway_is_a_coded_error_tried_three_times_by_the_proxy_only(app):
    """§7.2.7: the proxy tries the gateway 3 times for the one logical call; neither the OpenAI
    SDK (`max_retries=0`) nor CrewAI adds an attempt."""
    upstream = []

    def handler(request):
        upstream.append(1)
        return httpx.Response(503, json={"error": {"type": "api_error", "message": "SECRET"}})

    served = []
    original = ModelProxy.chat

    async def counting(self, state, body):
        served.append(1)
        return await original(self, state, body)

    ModelProxy.chat = counting
    try:
        (error,) = run(handler, ("s-1", "c-1", "hi"))
    finally:
        ModelProxy.chat = original
    assert isinstance(error, ModelError) and error.code == "MODEL_UNAVAILABLE"
    assert len(upstream) == 3  # the proxy's three tries for the one logical call
    # On `Agent.kickoff_async`, CrewAI made no further attempt (F0's 3 were a Crew's); any it
    # made would get the stored status without an upstream call (the kit's proxy tests).
    assert len(served) == 1
    assert "SECRET" not in str(error)


def throttled(upstream):
    def handler(request):
        upstream.append(1)
        return httpx.Response(
            429, json={"error": {"type": "rate_limit", "message": "SECRET"}},
            headers={"retry-after": "60"},
        )  # fmt: skip

    return handler


def served_calls(monkeypatch):
    served = []
    original = ModelProxy.chat

    async def counting(self, state, body):
        result = await original(self, state, body)
        served.append(result[0])
        return result

    monkeypatch.setattr(ModelProxy, "chat", counting)
    return served


def test_crewais_own_rate_limit_retry_is_off_for_the_turn(app, monkeypatch):
    """CrewAI retries a 429 itself, 3 attempts with backoff, above the client. The adapter sets
    the loop's own guard for the turn: one call, a coded 429 at once."""
    import crewai_adapter

    assert crewai_adapter.RETRY_GUARD is not None
    upstream, served = [], served_calls(monkeypatch)
    (error,) = run(throttled(upstream), ("s-1", "c-1", "hi"))
    assert isinstance(error, ModelError) and error.code == "MODEL_RATE_LIMITED"
    assert (error.status, error.retry_after) == (429, 60)
    assert len(upstream) == 1 and served == [429]


def test_without_the_guard_the_proxys_stored_status_ends_crewais_retries(app, monkeypatch):
    """§7.2.7 and §16.1 (F6), stub level: if CrewAI's retry loop runs (F0 measured 3 attempts),
    its two extra attempts get the turn's stored status from the proxy, without an upstream
    call, and the turn's coded error is unchanged."""
    import crewai_adapter

    monkeypatch.setattr(crewai_adapter, "RETRY_GUARD", None)
    monkeypatch.setattr(crewai_adapter.CrewAIAdapter, "self_check", lambda self: None)
    monkeypatch.setattr("crewai.llms.retry.time.sleep", lambda seconds: None, raising=False)
    upstream, served = [], served_calls(monkeypatch)
    (error,) = run(throttled(upstream), ("s-1", "c-1", "hi"))
    assert isinstance(error, ModelError) and error.code == "MODEL_RATE_LIMITED"
    assert len(upstream) == 1 and served == [429, 429, 429]


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


def test_parallel_tool_calls_in_one_answer_run_on_crewais_threads(app):
    """Several tool calls in one model answer: CrewAI runs them on worker threads (the
    `_run` path into the kit's loop), each with the turn's customer and its own audit row."""
    calls = [
        {"id": f"call_{n}", "type": "function",
         "function": {"name": "lookup_order", "arguments": json.dumps({"order_id": str(n)})}}
        for n in range(3)
    ]  # fmt: skip
    first = answer().json()
    first["choices"][0]["message"]["tool_calls"] = calls
    first["choices"][0]["finish_reason"] = "tool_calls"
    gateway = Scripted(httpx.Response(200, json=first), answer("Done."))
    (reply,) = run(gateway, ("s-1", "c-1", "orders 0, 1 and 2?"))
    assert reply["reply"] == "Done." and reply["model_calls"] == 2
    rows = tool_rows()
    assert sorted(r[2]["args"]["order_id"] for r in rows) == ["0", "1", "2"]
    assert {r[1] for r in rows} == {"c-1"} and all(r[2]["ok"] for r in rows)


def test_an_empty_answer_is_the_fallback_reply_not_a_500(app):
    """The model answers with nothing: CrewAI asks once more itself (a second counted call),
    then gives up; the turn answers with the kit's fallback text, as on LangGraph."""
    from kit.turn import FALLBACK_REPLY

    gateway = Scripted(answer(""))
    (reply,) = run(gateway, ("s-1", "c-1", "hello?"))
    assert reply["reply"] == FALLBACK_REPLY and reply["model_calls"] == 2


def test_crewais_no_output_text_is_not_a_reply():
    from crewai_adapter import NO_OUTPUT

    class Output:
        raw = NO_OUTPUT

    assert reply_text(Output()) == ""


def test_a_large_tool_result_is_not_spilled_to_tmp_and_adds_no_tool(app, monkeypatch):
    """Security review: CrewAI's "better compaction" (on by default) writes a tool result over
    about 7 KB to /tmp/crewai-tool-results-* (a fixed path, not TMPDIR) and gives the model an
    undeclared `read_a_deferred_tool_result` tool, so the agent's next step read as hidden. It is
    off: no such directory, the same tool set on every call, every step main-loop."""
    big = "ZZ-BIG-" + "x" * 9000
    tool = app / "tool" / "big"
    (tool / "src").mkdir(parents=True)
    (tool / "tool.yaml").write_text(
        "functions:\n  - name: lookup_order\n    module: crew_test_big\n"
        "    description: Look up an order by id.\n"
        "    parameters: {type: object, properties: {order_id: {type: string}},"
        " required: [order_id]}\n"
    )
    (tool / "src" / "crew_test_big.py").write_text(
        f"def lookup_order(rt, order_id):\n    return {{'order': order_id, 'history': {big!r}}}\n"
    )
    config = {**CONFIG, "tools": [{"name": "big", "version": "1.0.0", "dir": "tool/big"}]}
    before = set(Path("/tmp").glob("crewai-tool-results-*"))
    gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}), answer("Done."))
    (reply,) = run(gateway, ("s-1", "c-1", "order history?"), config=config)
    assert reply["reply"] == "Done."
    assert set(Path("/tmp").glob("crewai-tool-results-*")) == before
    assert [{t["function"]["name"] for t in b["tools"]} for b in gateway.bodies] == [
        {"lookup_order"},
        {"lookup_order"},
    ]
    assert big in gateway.bodies[1]["messages"][-1]["content"]
    assert [r["main_loop"] for r in model_rows()] == [True, True]


def test_the_adapter_refuses_to_start_without_crewais_retry_guard(app, monkeypatch):
    import crewai_adapter

    monkeypatch.setattr(crewai_adapter, "RETRY_GUARD", None)
    with pytest.raises(RuntimeError, match="retry guard"):
        run(Scripted(answer("x")), ("s-1", "c-1", "hi"))


def test_no_tool_result_is_cached_across_calls_or_customers(app):
    """CrewAI caches tool results by arguments by default; a cached answer would skip the audit
    row and could hand one customer another's result. The cache is off."""
    gateway = Scripted(answer(tool="lookup_order", args={"order_id": "7"}), answer("Done."))
    run(gateway, ("s-a", "alice", "order 7?"), ("s-b", "bob", "order 7?"))
    rows = tool_rows()
    assert [r[1] for r in rows] == ["alice", "bob"]
    results = [
        json.loads(b["messages"][-1]["content"])["customer"]
        for b in gateway.bodies
        if b["messages"][-1]["role"] == "tool"
    ]
    assert results == ["alice", "bob"]


def test_a_tool_with_bad_arguments_is_reported_to_the_model_and_audited(app):
    gateway = Scripted(answer(tool="lookup_order", args={"detail": True}), answer("Need an id."))
    (reply,) = run(gateway, ("s-1", "c-1", "order?"))
    assert reply["reply"] == "Need an id."
    (row,) = tool_rows()
    assert row[2]["ok"] is False and "missing arguments: order_id" in row[2]["error"]
    result = gateway.bodies[1]["messages"][-1]["content"]
    assert "missing arguments" in result


def test_an_undeclared_tool_is_answered_to_the_model_not_a_500(app):
    """F4's finding (#1175): an SDK's unknown-tool path can become a generic 500. CrewAI tells
    the model the tool was not found; nothing runs and the turn goes on."""
    gateway = Scripted(answer(tool="drop_tables", args={}), answer("I can't do that."))
    (reply,) = run(gateway, ("s-1", "c-1", "drop the tables"))
    assert reply["reply"] == "I can't do that." and reply["tools"] == []
    result = gateway.bodies[1]["messages"][-1]
    assert result["role"] == "tool" and "not found" in result["content"].lower()


def test_an_unparseable_tool_call_runs_nothing_and_the_turn_goes_on(app):
    """A tool call whose arguments are not JSON: CrewAI answers the model with a parse error,
    no tool runs with invented arguments, and the model's next answer is the reply."""
    bad = answer("Let me check that order.", tool="lookup_order", raw_args="{bad")
    gateway = Scripted(bad, answer("Which order?"))
    (reply,) = run(gateway, ("s-1", "c-1", "order?"))
    # CrewAI answers the model with a parse error; no tool runs, and the next answer is the reply.
    assert reply["reply"] == "Which order?" and reply["tools"] == [] and tool_rows() == []
    result = gateway.bodies[1]["messages"][-1]
    assert result["role"] == "tool" and "Failed to parse tool arguments" in result["content"]


def test_a_note_cannot_put_the_customers_message_into_the_system_prompt(app, monkeypatch):
    monkeypatch.setattr(FakeMemory, "notes", lambda self, customer: ["Says {input} a lot"])
    gateway = Scripted(answer("Hello."))
    run(gateway, ("s-1", "c-1", "SECRET-MESSAGE"))
    system = gateway.bodies[0]["messages"][0]["content"]
    assert "SECRET-MESSAGE" not in system and "｛input}" in system
    assert [r["main_loop"] for r in model_rows()] == [True]


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
    assert "<knowledge_base_results>" in gateway.bodies[0]["messages"][-1]["content"]
    names = {t["function"]["name"] for t in gateway.bodies[0]["tools"]}
    assert names == {"search_knowledge_base", "lookup_order"}
    assert [r["main_loop"] for r in model_rows()] == [True, True]


def test_no_file_under_home_or_tmp_keeps_the_turns_text(app):
    """§8.5, §13.7 and §16.1 (F6): CrewAI's task-output SQLite (and its credentials key) are not
    written on this runtime's path; no file under HOME or the temp dir holds the customer's
    message or the reply after the turn."""
    import time

    import crewai_confine

    message, text = "Where is order ZZ-SECRET-4711?", "ZZ-REPLY-0815 shipped."
    started = time.time() - 1
    gateway = Scripted(answer(tool="lookup_order", args={"order_id": "ZZ-SECRET-4711"}),
                       answer(text))  # fmt: skip
    (reply,) = run(gateway, ("s-1", "c-1", message))
    assert reply["reply"] == text
    assert not crewai_confine.task_outputs_path().exists()
    assert not crewai_confine.credentials_dir().exists()
    # HOME is /tmp in the image; here it is its own directory, and the temp dir is checked for
    # files written since the turn started.
    assert tempfile.gettempdir() == str(TMP)
    for path in files_under(HOME) + files_under(tempfile.gettempdir(), since=started):
        if app in path.parents:
            continue  # the test's own agent files
        data = read(path)
        assert b"ZZ-SECRET-4711" not in data and b"ZZ-REPLY-0815" not in data, path


def test_a_crew_built_in_this_process_keeps_no_task_outputs(app):
    """`confine()` covers a `Crew` too (code mode could build one): its handler keeps nothing and
    no database file appears, even after a real kickoff."""
    import crewai_confine
    from crewai import Agent, Crew, Task

    gateway = Scripted(answer("Crew answer ZZ-CREW-31337."))

    async def main():
        proxy = ModelProxy("http://gateway:4000", KEY, httpx.MockTransport(gateway))
        kit = Kit(CONFIG, "postgresql://unused", KEY, CrewAIAdapter(),
                  runtime_ref="runtime/crewai@1.0.0", framework="crewai", proxy=proxy,
                  memory_factory=FakeMemory)  # fmt: skip
        await kit.start()
        from kit.context import CURRENT, TOKEN
        from kit.proxy import TurnState

        state = TurnState(turn_id="t", max_steps=4, aliases=frozenset({"support-model"}))
        token = proxy.open_turn(state)
        TOKEN.set(token)
        CURRENT.set(None)
        try:
            agent = Agent(role="r", goal="g", backstory="b", llm=kit.adapter.llm, memory=None)
            task = Task(description="Say ZZ-TASK-2718", expected_output="text", agent=agent)
            crew = Crew(agents=[agent], tasks=[task])
            result = await crew.kickoff_async()
            assert crew._task_output_handler.load() == []
            return result
        finally:
            await kit.close()

    result = asyncio.run(main())
    assert "ZZ-CREW-31337" in str(result)
    assert not crewai_confine.task_outputs_path().exists()
    for path in files_under(HOME):
        assert b"ZZ-TASK-2718" not in read(path), path


def test_the_credentials_path_is_crewais(app):
    import crewai_confine
    from crewai_core.token_manager import TokenManager

    expected = Path(TokenManager._get_secure_storage_path())  # creates it
    try:
        assert crewai_confine.credentials_dir() == expected
    finally:
        crewai_confine.sweep(credentials=True)
    assert not expected.exists()


def test_the_start_up_sweep_removes_what_an_earlier_run_left(app):
    import crewai_confine

    database = crewai_confine.task_outputs_path()
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"old customer text")
    crewai_confine.credentials_dir().mkdir(parents=True, exist_ok=True)
    (crewai_confine.credentials_dir() / "secret.key").write_bytes(b"k" * 44)
    removed = crewai_confine.sweep(credentials=True)
    assert removed[:2] == [crewai_confine.TASK_OUTPUTS, "credentials"]
    assert not database.exists() and not crewai_confine.credentials_dir().exists()


def test_the_entry_point_switches_crewai_off_and_drops_its_settings(monkeypatch):
    monkeypatch.setenv("CREWAI_TRACING_ENABLED", "true")
    monkeypatch.setenv("CREWAI_PLATFORM_TOKEN", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    namespace: dict = {"__name__": "not_main", "__file__": str(RUNTIME / "server.py")}
    exec(compile(_source, "server.py", "exec"), namespace)  # noqa: S102 - our own entry point
    assert os.environ["CREWAI_TRACING_ENABLED"] == "false"
    assert os.environ["CREWAI_DISABLE_TELEMETRY"] == "true"
    assert os.environ["CREWAI_DISABLE_TRACKING"] == "true"
    assert os.environ["CREWAI_STORAGE_DIR"] == "agentbreeder"
    assert "CREWAI_PLATFORM_TOKEN" not in os.environ and "OPENAI_API_KEY" not in os.environ
    assert "OPENAI_BASE_URL" not in os.environ and "OTEL_SDK_DISABLED" not in os.environ
    assert namespace["RUNTIME_REF"] == "runtime/crewai@1.0.0"


def test_a_turn_connects_to_nothing_but_loopback(app):
    """With the three switches set, nothing leaves the process during a turn but the kit's own
    loopback calls (the gateway here is a MockTransport, so it opens no socket)."""
    seen = []

    def hook(event, args):
        if event == "socket.connect" and AUDITING:
            seen.append(args[1])

    sys.addaudithook(hook)
    globals()["AUDITING"] = True
    try:
        gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}), answer("Done."))
        run(gateway, ("s-1", "c-1", "order 1?"))
    finally:
        globals()["AUDITING"] = False
    hosts = {address[0] for address in seen if isinstance(address, tuple)}
    assert hosts <= {"127.0.0.1"}, hosts


AUDITING = False

# Process events seen while `test_chromadb_is_a_library_only_...` watches (audit hooks are
# permanent, so the hook is installed once and reads this).
CHROMA_WATCH: list | None = None
_CHROMA_EVENTS = ("socket.bind", "subprocess.Popen", "os.posix_spawn", "os.exec", "os.system")


def _chroma_hook(event, args):
    if CHROMA_WATCH is not None and event in _CHROMA_EVENTS:
        CHROMA_WATCH.append((event, args[1] if event == "socket.bind" else args[0]))


def test_chromadb_is_a_library_only_no_server_client_or_port(app, monkeypatch):
    """The chromadb security exception (CLAUDE.md, "Known security exceptions") holds only while
    the runtime never starts ChromaDB's server: its four advisories are in that server. Across
    start-up (the entry point run again) and a turn with a tool call, `chromadb` is imported as
    a library (through `crewai`) but no ChromaDB server module is loaded, its CLI (which runs the
    server) and its clients and systems are never called, no process starts and nothing binds a
    socket but the kit's loopback proxy."""
    global CHROMA_WATCH
    import chromadb
    import chromadb_rust_bindings
    from chromadb.api.shared_system_client import SharedSystemClient
    from chromadb.config import System

    calls: list[str] = []

    def tripwire(name):
        def called(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} was called")

        return called

    # In chromadb 1.5.9 the Rust server (`chroma run`) is reached only through this function
    # (chromadb/cli/cli.py), and its sockets are invisible to Python's audit hooks; a new chromadb
    # pin means re-checking that (tests/test_crewai_chromadb_exception.py fails until then).
    monkeypatch.setattr(chromadb_rust_bindings, "cli", tripwire("chromadb_rust_bindings.cli"))
    monkeypatch.setattr(SharedSystemClient, "__init__", tripwire("SharedSystemClient"))
    monkeypatch.setattr(System, "__init__", tripwire("System"))
    monkeypatch.setattr(System, "start", tripwire("System.start"))
    for name in ("Client", "HttpClient", "AsyncHttpClient", "PersistentClient", "EphemeralClient"):
        monkeypatch.setattr(chromadb, name, tripwire(f"chromadb.{name}"))

    sys.addaudithook(_chroma_hook)
    CHROMA_WATCH = []
    try:
        namespace: dict = {"__name__": "not_main", "__file__": str(RUNTIME / "server.py")}
        exec(compile(_source, "server.py", "exec"), namespace)  # noqa: S102 - our own entry point
        gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}), answer("Done."))
        [reply] = run(gateway, ("s-1", "c-1", "order 1?"))
        seen = list(CHROMA_WATCH)
    finally:
        CHROMA_WATCH = None

    assert reply["reply"] == "Done."
    assert "chromadb" in sys.modules  # imported, as a library
    assert calls == []
    server_modules = sorted(
        name
        for name in sys.modules
        if name.startswith(("chromadb.server", "chromadb.app", "chromadb.cli", "uvicorn"))
    )
    assert server_modules == []
    # Python's `platform` module asks the host once per process (`uname -p`, `file -b python`,
    # cached; a library's user-agent probe); nothing else may start.
    started = [
        (event, detail)
        for event, detail in seen
        if event != "socket.bind"
        and not (event == "subprocess.Popen" and Path(str(detail)).name in ("uname", "file"))
    ]
    assert started == []
    binds = [address for event, address in seen if event == "socket.bind"]
    assert binds, "the kit's loopback proxy binds, so the hook must see it"
    assert {address[0] for address in binds if isinstance(address, tuple)} <= {"127.0.0.1", "::1"}
    assert all(isinstance(address, tuple) for address in binds), binds


def test_an_undeclared_model_alias_is_refused(app):
    adapter_llm = {}

    class Other(CrewAIAdapter):
        def build(self, kit):
            super().build(kit)
            from crewai import LLM

            self.llm = LLM(model="openai/other-model", base_url=kit.base_url,
                           api_key="x", max_retries=0,
                           interceptor=self.llm.interceptor)  # fmt: skip
            adapter_llm["llm"] = self.llm

    async def main():
        proxy = ModelProxy("http://gateway:4000", KEY, httpx.MockTransport(Scripted(answer("x"))))
        kit = Kit(CONFIG, "postgresql://unused", KEY, Other(), runtime_ref="runtime/crewai@1.0.0",
                  framework="crewai", proxy=proxy, memory_factory=FakeMemory)  # fmt: skip
        await kit.start()
        try:
            return await kit.chat_async("s-1", "c-1", "hi")
        except ModelError as error:
            return error
        finally:
            await kit.close()

    error = asyncio.run(main())
    assert isinstance(error, ModelError) and error.code == "MODEL_ACCESS_DENIED"


def test_helpers():
    from crewai_adapter import inert

    class Output:
        raw = "final"

    assert reply_text(Output()) == "final" and reply_text(object()) == ""
    assert inert("a {input} b {tools}") == "a ｛input} b ｛tools}"

    class Function:
        name, description = "lookup", ""
        parameters = {"type": "object", "properties": {}}

    tool = as_tool(Function(), None)
    assert tool.name == "lookup" and "lookup" in tool.description

    class Bad(Function):
        name = "LookupOrder"

    with pytest.raises(RuntimeError, match="not usable by CrewAI"):
        as_tool(Bad(), None)


def test_a_sync_tool_call_on_the_kits_loop_refuses_instead_of_deadlocking():
    class Function:
        name, description = "lookup", "x"
        parameters = {"type": "object", "properties": {}}

        async def __call__(self, **kwargs):
            return "ok"

    async def main():
        tool = as_tool(Function(), asyncio.get_running_loop())
        with pytest.raises(RuntimeError, match="synchronously on the kit's event loop"):
            tool._run()
        return await asyncio.to_thread(tool._run)

    assert asyncio.run(main()) == "ok"


# -- the JSON Schema -> pydantic generator ---------------------------------------------------


def test_the_generator_keeps_types_descriptions_enums_and_required():
    from crewai_schema import args_model

    model = args_model("escalate_to_human", {
        "type": "object",
        "properties": {
            "priority": {"type": "string", "enum": ["low", "normal", "urgent"],
                         "description": "One of low, normal or urgent."},
            "summary": {"type": "string"},
            "count": {"type": "integer"},
            "ratio": {"type": "number"},
            "flag": {"type": "boolean", "default": True},
            "tags": {"type": "array", "items": {"type": "string"}},
            "extra": {"type": "object"},
            "maybe": {"type": ["string", "null"]},
        },
        "required": ["priority", "summary"],
    })  # fmt: skip
    schema = model.model_json_schema()
    assert schema["required"] == ["priority", "summary"]
    assert schema["properties"]["priority"]["enum"] == ["low", "normal", "urgent"]
    assert schema["properties"]["priority"]["description"] == "One of low, normal or urgent."
    assert model(priority="low", summary="s").flag is True
    with pytest.raises(ValueError):
        model(priority="never", summary="s")
    with pytest.raises(ValueError):
        model(priority="low", summary="s", unknown=1)
    assert model.__name__ == "EscalateToHumanArgs"


@pytest.mark.parametrize(
    "parameters, message",
    [
        ({"type": "string"}, "JSON Schema object"),
        ({"type": "object", "properties": {"a": {"$ref": "#/x"}}}, "`$ref`"),
        ({"type": "object", "properties": {"a": {"anyOf": []}}}, "`anyOf`"),
        ({"type": "object", "oneOf": []}, "`oneOf`"),
        ({"type": "object", "properties": {"a": {"type": "array", "items": [{}]}}}, "tuple"),
        ({"type": "object", "properties": {"a": {"type": ["string", "integer"]}}}, "type list"),
        ({"type": "object", "properties": {"a": {"type": "object",
                                                "properties": {"b": {"type": "string"}}}}},
         "nested object"),
        ({"type": "object", "properties": {"a": {"type": "date"}}}, "not supported"),
        ({"type": "object", "properties": {"class": {"type": "string"}}}, "property name"),
        ({"type": "object", "properties": {"model_dump": {"type": "string"}}}, "property name"),
        ({"type": "object", "properties": {"a": {"enum": []}}}, "`enum`"),
        ({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["b"]},
         "`required`"),
        ({"type": "object", "properties": {"a": "string"}}, "must be an object"),
    ],
)  # fmt: skip
def test_the_generator_refuses_what_it_cannot_express(parameters, message):
    from crewai_schema import SchemaError, args_model

    with pytest.raises(SchemaError, match=re.escape(message)):
        args_model("t", parameters)


def test_given_drops_strict_modes_null_for_optional_arguments_only():
    from crewai_schema import given

    parameters = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "string"},
                       "c": {"type": ["string", "null"]}},
        "required": ["a"],
    }  # fmt: skip
    assert given(parameters, {"a": None, "b": None, "c": None}) == {"a": None, "c": None}
    assert given(parameters, {"a": "x", "b": "y", "zz": None}) == {"a": "x", "b": "y", "zz": None}


def test_every_northwind_tool_schema_is_supported():
    """Northwind's tools (the port's acceptance) all convert."""
    import yaml
    from crewai_schema import args_model

    registry = Path(__file__).resolve().parents[5] / "examples" / "northwind" / "registry"
    files = sorted(registry.glob("tool/*/*/tool.yaml"))
    assert files, registry
    for path in files:
        for function in yaml.safe_load(path.read_text())["functions"]:
            args_model(function["name"], function.get("parameters") or {"type": "object"})


def test_the_instrumentor_nests_under_the_kits_spans_and_tool_spans_are_relabelled(app):
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    adapter = CrewAIAdapter()
    adapter.instrument(provider)
    try:
        gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}), answer("Done."))
        run(gateway, ("s-1", "c-1", "order 1?"))
        provider.force_flush()
    finally:
        from openinference.instrumentation.crewai import CrewAIInstrumentor

        CrewAIInstrumentor().uninstrument()
    exported = [telemetry.RedactingExporter.redacted(s) for s in exporter.get_finished_spans()]
    kinds = [(s.name, s.attributes.get("openinference.span.kind")) for s in exported]
    tool_spans = [name for name, kind in kinds if kind == "TOOL"]
    assert tool_spans == ["lookup_order"]  # the kit's own; CrewAI's is CHAIN
    root = [s for s in exported if s.name == "agent_turn"][0]
    assert all(s.context.trace_id == root.context.trace_id for s in exported)
    assert root.attributes["agent.framework"] == "crewai"


def test_each_model_call_is_one_llm_span_under_the_turn(app):
    """Spec §12, conformance `traces.llm_span`: CrewAI's instrumentor writes no LLM span, so the
    adapter writes one per call from its `model_called` row, as a child in the turn's trace, with
    the model, `served_by` and token counts and no message text (seen missing on the first real
    conformance run, 2026-11-20)."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    provider = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    before = len(FakeMemory.log)
    try:
        gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}), answer("Done."))
        (reply,) = run(gateway, ("s-1", "c-1", "order 1? ZZ-LLM-SPAN"))
        provider.force_flush()
        exported = exporter.get_finished_spans()
    finally:
        processor.shutdown()  # exports nothing more in later tests
    root = [s for s in exported if s.name == "agent_turn"][-1]
    llm = [
        s for s in exported
        if s.context.trace_id == root.context.trace_id
        and s.attributes.get("openinference.span.kind") == "LLM"
    ]  # fmt: skip
    assert len(llm) == reply["model_calls"] == 2
    rows = [entry[2] for entry in FakeMemory.log[before:] if entry[0] == "model_called"]
    assert len(rows) == 2  # the audit rows are still written, once per call
    ordered = sorted(llm, key=lambda s: s.attributes["agentbreeder.step"])
    for current, row in zip(ordered, rows, strict=True):
        assert current.parent is not None and current.parent.span_id == root.context.span_id
        assert current.attributes["llm.model_name"] == "support-model"
        assert current.attributes["agentbreeder.model_alias"] == "support-model"
        assert current.attributes["llm.token_count.prompt"] == row["prompt_tokens"]
        assert current.attributes["llm.token_count.completion"] == row["output_tokens"]
        assert current.attributes["agentbreeder.main_loop"] is True
        text = ("input.", "output.", "llm.input", "llm.output")
        assert not [key for key in current.attributes if key.startswith(text)]
        assert current.end_time >= current.start_time


def test_a_span_that_cannot_be_written_does_not_fail_the_turn(app, monkeypatch):
    import crewai_adapter

    def broken(*args, **kwargs):
        raise RuntimeError("tracer down")

    monkeypatch.setattr(crewai_adapter.tracer, "start_span", broken)
    gateway = Scripted(answer(tool="lookup_order", args={"order_id": "1"}), answer("Done."))
    (reply,) = run(gateway, ("s-1", "c-1", "order 1?"))
    assert reply["reply"] == "Done." and reply["model_calls"] == 2
    assert [e[0] for e in FakeMemory.log].count("model_called") == 2


def test_crewai_prints_nothing_of_the_turn(app, capfd):
    """§13.8: CrewAI's global event listener prints verbose panels whatever the agent says
    (the backstory with the customer's notes, tool arguments and output, the answer). They are
    off: nothing of the turn reaches stdout or stderr, the container's log."""
    import logging

    import crewai_confine
    from crewai_adapter import LOGGERS
    from kit import logs

    crewai_confine.check()
    logs.quiet(LOGGERS)  # what `kit.server.main` does: CrewAI's loggers, one line to stderr
    gateway = Scripted(
        answer(tool="lookup_order", args={"order_id": "ZZ-ARG-1"}),
        answer(tool="lookup_order", args={"detail": True}),  # a refused call, an error path
        answer("ZZ-ANSWER-2"),
    )
    (reply,) = run(gateway, ("s-1", "c-1", "ZZ-MESSAGE-3"))
    assert reply["reply"] == "ZZ-ANSWER-2"
    failing = Scripted(httpx.Response(503, json={"error": {"message": "ZZ-GATEWAY-4"}}))
    run(failing, ("s-2", "c-1", "ZZ-MESSAGE-5"))  # CrewAI's error path, and its event warning
    out, err = capfd.readouterr()
    for text in ("ZZ-ARG-1", "ZZ-ANSWER-2", "ZZ-MESSAGE-3", "ZZ-GATEWAY-4", "ZZ-MESSAGE-5",
                 "Prefers email", "customer", "CrewAIEventsBus", "╭"):  # fmt: skip
        assert text not in out and text not in err, text
    # Only the kit's own one-line, coded log line remains, and no CrewAI warning.
    assert [line for line in out.splitlines() if line] == [
        line for line in out.splitlines() if line.startswith("model call failed: turn=")
    ]
    assert "approximate input token count" not in err
    for name in LOGGERS:
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
