"""The runtime kit's turn, tools and knowledge function (#1083 F2b, spec §7.1, §7.3, §7.5).

A scripted "framework" adapter calls the kit's proxy through `kit.http_client()` (so the turn
token travels by context variable, as with a real framework) and calls the kit's functions; the
gateway is an httpx MockTransport; memory is an in-process fake.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from agentbreeder.runtime_kit import config as cfg
from agentbreeder.runtime_kit import redaction, telemetry
from agentbreeder.runtime_kit import tools as kit_tools
from agentbreeder.runtime_kit.context import CURRENT, TOKEN, TurnContext, current
from agentbreeder.runtime_kit.errors import ModelError
from agentbreeder.runtime_kit.proxy import ModelProxy
from agentbreeder.runtime_kit.turn import FALLBACK_REPLY, Kit, knowledge_function, new_trace

KEY = "gateway-key-0123456789abcdef"


class FakeMemory:
    """Memory's interface, in-process. `HISTORY`/`NOTES` are per test."""

    db = object()
    HISTORY: list = []
    NOTES: list = []
    log: list = []

    def __init__(self, database_url, events=True):
        self.closed = False

    def audit(self, turn, session, customer, actor, action, detail):
        FakeMemory.log.append(("audit", action, actor, customer, detail))

    def record_turn(self, **turn):
        FakeMemory.log.append(("turn", turn))

    def history(self, customer, session):
        return list(FakeMemory.HISTORY)

    def notes(self, customer):
        return list(FakeMemory.NOTES)

    def close(self):
        self.closed = True


def gateway_answer(text=None, tool=None, args=None, cost="0.002"):
    message = {"role": "assistant", "content": text}
    if tool:
        message["tool_calls"] = [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args or {})},
            }
        ]
    return httpx.Response(
        200,
        json={
            "id": "x",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 4},
        },
        headers={"x-litellm-response-cost": cost} if cost else {},
    )


class ScriptedFramework:
    """Calls the model until it answers without tools, running the tools it asks for: the
    loop every framework runs, without a framework."""

    framework = "scripted"

    def __init__(self, swallow=False):
        self.swallow = swallow
        self.seen = []

    def build(self, kit):
        self.kit = kit
        self.client = kit.http_client()

    def close(self):
        pass

    async def run_turn(self, turn):
        self.seen.append(turn)
        functions = {f.name: f for f in turn.functions}
        messages = [{"role": "system", "content": turn.system}, *turn.history]
        messages.append({"role": "user", "content": turn.message})
        tools = [
            {"type": "function", "function": {"name": f.name, "parameters": f.parameters}}
            for f in turn.functions
        ]
        while True:
            response = await self.client.post(
                f"{self.kit.base_url}/chat/completions",
                json={"model": self.kit.alias, "messages": messages, "tools": tools},
            )
            if response.status_code != 200:
                if self.swallow:
                    return "I could not reach the model, sorry."
                raise RuntimeError(f"model call failed: {response.status_code}")
            message = response.json()["choices"][0]["message"]
            messages.append(message)
            if not message.get("tool_calls"):
                return message["content"]
            for call in message["tool_calls"]:
                result = await functions[call["function"]["name"]](
                    **json.loads(call["function"]["arguments"])
                )
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    (tmp_path / "prompt").mkdir()
    (tmp_path / "prompt" / "prompt.md").write_text("You help Northwind customers.\n")
    tool = tmp_path / "tool" / "orders"
    (tool / "src").mkdir(parents=True)
    (tool / "tool.yaml").write_text(
        "functions:\n"
        "  - name: get_order\n"
        "    module: kit_test_orders\n"
        "    description: Look up an order.\n"
        "    parameters: {type: object, properties: {order_id: {type: string}},"
        " required: [order_id]}\n"
        "  - name: cancel_order\n"
        "    module: kit_test_orders\n"
        "    writes_requests: true\n"
        "    parameters: {type: object, properties: {order_id: {type: string}}}\n"
    )
    (tool / "src" / "kit_test_orders.py").write_text(
        "def get_order(rt, order_id):\n"
        "    if order_id == 'boom':\n"
        "        raise ValueError('lookup failed for key sk-abcdefghijklmnop')\n"
        "    rt.record('tickets', {'order': order_id})\n"
        "    rt.record('tools', 'forged')\n"
        "    return {'order': order_id, 'customer': rt.customer, 'status': 'shipped'}\n"
        "def cancel_order(rt, order_id=None):\n"
        "    return 'filed'\n"
    )
    FakeMemory.HISTORY, FakeMemory.NOTES, FakeMemory.log = [], [], []
    telemetry.setup("kit-test", "", enabled=False)
    return tmp_path


CONFIG = {
    "name": "northwind-support-test",
    "model": {"alias": "support-model", "reasoning": "low", "max_model_calls": 4},
    "prompt": {"dir": "prompt"},
    "knowledge": [],
    "tools": [{"name": "orders", "version": "1.0.0", "dir": "tool/orders"}],
    "memory": {"sessions": "postgres", "notes": True},
}


def turn(answers, *, config=None, adapter=None, message="Where is order 1042?", customer="c-1"):
    """One turn through a fresh Kit; returns (reply or the error, upstream bodies, adapter)."""
    answers = list(answers)
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        item = answers.pop(0) if len(answers) > 1 else answers[0]
        return item

    adapter = adapter or ScriptedFramework()

    async def main():
        proxy = ModelProxy("http://gw", KEY, httpx.MockTransport(handler), sleep=_no_sleep)
        kit = Kit(
            config or CONFIG,
            "postgresql://unused",
            KEY,
            adapter,
            runtime_ref="runtime/test@1.0.0",
            framework="scripted",
            proxy=proxy,
            memory_factory=FakeMemory,
        )
        await kit.start()
        try:
            return await kit.chat_async("s-1", customer, message)
        except ModelError as error:
            return error
        finally:
            await kit.close()

    return asyncio.run(main()), bodies, adapter


async def _no_sleep(seconds):
    return None


def audits(action=None):
    rows = [entry for entry in FakeMemory.log if entry[0] == "audit"]
    return [r for r in rows if action is None or r[1] == action]


def recorded_turn():
    (row,) = [entry[1] for entry in FakeMemory.log if entry[0] == "turn"]
    return row


def test_a_turn_with_a_tool_call(app):
    reply, bodies, _ = turn(
        [gateway_answer(tool="get_order", args={"order_id": "1042"}), gateway_answer("Shipped.")]
    )
    assert reply["reply"] == "Shipped."
    assert reply["tools"] == ["get_order", "forged"]  # as 1.3.0: rt.record appends
    assert reply["tickets"] == [{"order": "1042"}]
    assert reply["model_calls"] == 2 and reply["cost_usd"] == 0.004
    assert (reply["prompt_tokens"], reply["output_tokens"]) == (22, 8)
    assert reply["framework"] == "scripted" and reply["runtime"] == "runtime/test@1.0.0"
    assert reply["fallbacks"] == 0 and reply["served_by"] == []
    assert reply["mcp_unavailable"] == [] and "evidence" not in reply
    (tool_row,) = audits("tool:get_order")
    detail = tool_row[4]
    assert detail["tool"] == "tool/orders@1.0.0" and detail["ok"] is True
    assert detail["tickets"] == [{"order": "1042"}]
    assert "forged" not in json.dumps(detail.get("tools", []))  # runtime keys never copied
    assert detail["args"] == {"order_id": "1042"} and detail["agent"] == CONFIG["name"]
    actions = [row[1] for row in audits()]
    assert actions == [
        "message_received",
        "model_called",
        "tool:get_order",
        "model_called",
        "reply_sent",
    ]
    assert recorded_turn()["reply"] == "Shipped." and recorded_turn()["error"] is None
    # The tool saw the server-side customer.
    assert '"customer": "c-1"' in bodies[1]["messages"][-1]["content"]


def test_a_failing_tool_is_returned_to_the_model_and_audited(app):
    reply, bodies, _ = turn(
        [gateway_answer(tool="get_order", args={"order_id": "boom"}), gateway_answer("Sorry.")]
    )
    assert reply["reply"] == "Sorry."
    (row,) = audits("tool:get_order")
    assert row[4]["ok"] is False and "sk-[redacted]" in row[4]["error"]
    tool_message = bodies[1]["messages"][-1]["content"]
    assert json.loads(tool_message)["error"].startswith("lookup failed")


def test_arguments_are_checked_against_the_schema(app):
    reply, bodies, _ = turn(
        [
            gateway_answer(tool="get_order", args={"order_id": "1", "admin": True}),
            gateway_answer("ok"),
        ]
    )
    assert "unexpected arguments: admin" in bodies[1]["messages"][-1]["content"]
    turn([gateway_answer(tool="get_order", args={}), gateway_answer("ok")])
    assert audits("tool:get_order")[-1][4]["error"] == "missing arguments: order_id"


def test_a_writes_requests_tool_is_marked(app):
    turn([gateway_answer(tool="cancel_order", args={"order_id": "1"}), gateway_answer("Filed.")])
    (row,) = audits("tool:cancel_order")
    assert row[4]["writes_request"] is True


def test_history_is_structured_and_notes_are_in_the_system_prompt(app):
    FakeMemory.HISTORY = [("Hi, I'm Ana.", "Hello Ana!")]
    FakeMemory.NOTES = ["Prefers email <b>", "VIP"]
    reply, bodies, adapter = turn([gateway_answer("Welcome back.")])
    messages = bodies[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "Hi, I'm Ana." and messages[2]["content"] == "Hello Ana!"
    assert "<customer_memory>" in messages[0]["content"]
    assert "Prefers email ‹b›" in messages[0]["content"]
    assert audits("memory_recalled") == []  # not a new session
    assert adapter.seen[0].system == messages[0]["content"]


def test_notes_on_a_new_session_are_audited(app):
    FakeMemory.NOTES = ["VIP"]
    turn([gateway_answer("Hi.")])
    assert audits("memory_recalled")[0][4] == {"notes": 1}


def test_notes_off_reads_none(app):
    FakeMemory.NOTES = ["VIP"]
    config = {**CONFIG, "memory": {"sessions": "postgres", "notes": False}}
    _, bodies, _ = turn([gateway_answer("Hi.")], config=config)
    assert "customer_memory" not in bodies[0]["messages"][0]["content"]


def test_the_step_cap_fails_the_turn_when_the_model_keeps_calling_tools(app):
    config = {**CONFIG, "model": {**CONFIG["model"], "max_model_calls": 2}}
    error, bodies, _ = turn(
        [gateway_answer(tool="get_order", args={"order_id": "1"})], config=config
    )
    assert isinstance(error, ModelError) and error.code == "MODEL_STEP_LIMIT"
    assert len(bodies) == 2 and bodies[1]["tool_choice"] == "none"
    assert error.turn_id and error.trace_id
    assert recorded_turn()["error"] == "MODEL_STEP_LIMIT"
    (called,) = [r for r in audits("model_called") if r[4].get("step_limited")]
    assert called[4]["step"] == 2
    assert audits("turn_failed")[0][4] == {"error": "MODEL_STEP_LIMIT"}


def test_a_swallowed_model_error_still_gives_the_coded_error(app):
    error, _, _ = turn(
        [
            httpx.Response(
                429,
                json={"error": {"type": "throttling_error", "message": "SECRET"}},
                headers={"Retry-After": "60"},
            )
        ],
        adapter=ScriptedFramework(swallow=True),
    )
    assert isinstance(error, ModelError) and error.code == "MODEL_RATE_LIMITED"
    assert error.retry_after == 60
    assert "SECRET" in recorded_turn()["error"]  # the turn record keeps the redacted detail


def test_a_raised_model_error_is_coded_and_logged_without_its_text(app, capsys):
    error, _, _ = turn(
        [httpx.Response(400, json={"error": {"type": "budget_exceeded", "message": "SECRET"}})]
    )
    assert error.code == "MODEL_BUDGET_EXCEEDED"
    log = capsys.readouterr().out
    assert "code=MODEL_BUDGET_EXCEEDED" in log and "SECRET" not in log


def test_a_framework_failure_is_raised_as_itself(app):
    class Broken(ScriptedFramework):
        async def run_turn(self, turn):
            raise KeyError("framework bug")

    with pytest.raises(KeyError):
        turn([gateway_answer("x")], adapter=Broken())
    assert recorded_turn()["error"].startswith("KeyError")


def test_an_empty_reply_gets_the_fallback_text(app):
    reply, _, _ = turn([gateway_answer("   ")])
    assert reply["reply"] == FALLBACK_REPLY


class FakeRetriever:
    description = "Northwind's help centre."
    sets = [object()]

    def __init__(self):
        self.queries = []

    def search(self, db, query, limit):
        self.queries.append(query)
        return [
            {"id": "a#1", "title": "Shipping", "text": "Ships in 2 days.", "mode": "hybrid"},
            {"id": "b#1", "title": "Returns", "text": "30 days.", "mode": "hybrid"},
        ]


def test_knowledge_is_retrieved_before_the_model_call(app, monkeypatch):
    retriever = FakeRetriever()
    monkeypatch.setattr("agentbreeder.runtime_kit.turn.Retriever", lambda sets: retriever)
    monkeypatch.setattr("agentbreeder.runtime_kit.turn.KnowledgeSet", lambda *a: None)
    FakeMemory.HISTORY = [("my pen", "Which pen?")]
    config = {
        **CONFIG,
        "knowledge": [
            {"name": "help", "version": "1.0.0", "dir": "kb", "top_k": 5, "extra_searches": 1}
        ],
    }
    reply, bodies, adapter = turn([gateway_answer("It ships in 2 days.")], config=config)
    assert retriever.queries == ["my pen Where is order 1042?"]
    assert reply["sources"] == ["Shipping", "Returns"] and reply["retrieval"] == "hybrid"
    user = bodies[0]["messages"][-1]["content"]
    assert user.startswith("<knowledge_base_results>\n[Shipping] Ships in 2 days.")
    assert user.endswith("\n\nWhere is order 1042?")
    assert adapter.seen[0].functions[0].name == "search_knowledge_base"
    assert audits("knowledge_retrieved")[0][4] == {"mode": "hybrid", "chunks": ["a#1", "b#1"]}
    assert recorded_turn()["message"] == "Where is order 1042?"  # the customer's own text


def test_the_knowledge_tool_stops_at_extra_searches(app):
    retriever = FakeRetriever()

    class FakeKit:
        pass

    kit = FakeKit()
    kit.retriever, kit.extra_searches, kit.top_k = retriever, 1, 5
    function = knowledge_function(kit)
    context = TurnContext(
        id="t",
        session="s",
        customer="c",
        memory=FakeMemory("x"),
        trace=new_trace(),
        agent="a",
        config={},
        services={},
    )

    async def main():
        token = CURRENT.set(context)
        try:
            return [await function(query="pens"), await function(query="more")]
        finally:
            CURRENT.reset(token)

    first, second = asyncio.run(main())
    assert first.startswith("[Shipping]") and second.startswith("Search limit reached")
    assert context.trace["kb_searches"] == 1 and context.trace["sources"] == [
        "Shipping",
        "Returns",
    ]
    assert (
        function.description
        == "Search Northwind's help centre. Returns the best-matching passages with titles."
    )


def test_a_tool_outside_a_turn_fails():
    with pytest.raises(RuntimeError):
        current()


def test_the_turn_token_is_forgotten_after_the_turn(app):
    adapter = ScriptedFramework()

    class Peek(ScriptedFramework):
        async def run_turn(self, turn):
            self.token = TOKEN.get()
            return await super().run_turn(turn)

    adapter = Peek()
    turn([gateway_answer("ok")], adapter=adapter)
    assert adapter.token and adapter.token not in redaction.values()


def test_concurrent_turns_keep_their_own_tokens_and_customers(app):
    """Two sessions at once: each tool call sees its own customer (spec §7.2.2)."""
    customers = []

    def handler(request):
        body = json.loads(request.content)
        if body["messages"][-1]["role"] == "tool":
            customers.append(json.loads(body["messages"][-1]["content"])["customer"])
            return gateway_answer("done")
        return gateway_answer(tool="get_order", args={"order_id": "1"})

    async def main():
        proxy = ModelProxy("http://gw", KEY, httpx.MockTransport(handler), sleep=_no_sleep)
        kit = Kit(
            CONFIG,
            "postgresql://unused",
            KEY,
            ScriptedFramework(),
            runtime_ref="runtime/test@1.0.0",
            framework="scripted",
            proxy=proxy,
            memory_factory=FakeMemory,
        )
        await kit.start()
        try:
            return await asyncio.gather(
                kit.chat_async("s-a", "alice", "hi"), kit.chat_async("s-b", "bob", "hi")
            )
        finally:
            await kit.close()

    first, second = asyncio.run(main())
    assert sorted(customers) == ["alice", "bob"]
    assert first["customer_id"] == "alice" and second["customer_id"] == "bob"


def test_tool_loading_refuses_what_1_3_0_refuses(tmp_path):
    def tool(name, yaml_text, files=None):
        directory = tmp_path / name
        (directory / "src").mkdir(parents=True)
        (directory / "tool.yaml").write_text(yaml_text)
        for file, text in (files or {}).items():
            (directory / "src" / file).write_text(text)
        return {"name": name, "version": "1.0.0", "dir": str(directory)}

    def load(*entries):
        return kit_tools.load_functions(list(entries), Path)

    with pytest.raises(kit_tools.ToolLoadError, match="no functions"):
        load(tool("empty", "functions: []\n"))
    with pytest.raises(kit_tools.ToolLoadError, match="shadows"):
        load(tool("shadow", "functions: [{name: f, module: m}]\n", {"json.py": ""}))
    with pytest.raises(kit_tools.ToolLoadError, match="invalid function"):
        load(tool("badname", "functions: [{name: F!, module: m}]\n", {"m.py": ""}))
    with pytest.raises(kit_tools.ToolLoadError, match="reserved"):
        load(tool("reserved", "functions: [{name: f, module: kit}]\n"))
    with pytest.raises(kit_tools.ToolLoadError, match="not in its src"):
        load(tool("missing", "functions: [{name: f, module: kit_nowhere}]\n"))
    with pytest.raises(kit_tools.ToolLoadError, match="JSON Schema object"):
        load(
            tool(
                "schema",
                "functions: [{name: f, module: kit_s1, parameters: {type: array}}]\n",
                {"kit_s1.py": "def f(rt): pass\n"},
            )
        )
    with pytest.raises(kit_tools.ToolLoadError, match="cannot import"):
        load(tool("noattr", "functions: [{name: f, module: kit_s2}]\n", {"kit_s2.py": ""}))
    with pytest.raises(kit_tools.ToolLoadError, match="cannot read"):
        load({"name": "nofile", "version": "1", "dir": str(tmp_path / "nowhere")})
    one = tool(
        "one", "functions: [{name: same, module: kit_s3}]\n", {"kit_s3.py": "def same(rt): pass\n"}
    )
    two = tool(
        "two", "functions: [{name: same, module: kit_s4}]\n", {"kit_s4.py": "def same(rt): pass\n"}
    )
    with pytest.raises(kit_tools.ToolLoadError, match="declared by two tools"):
        load(one, two)
    with pytest.raises(kit_tools.ToolLoadError, match="also in tool"):
        load(
            tool(
                "a1",
                "functions: [{name: a, module: kit_shared}]\n",
                {"kit_shared.py": "def a(rt): pass\n"},
            ),
            tool(
                "a2",
                "functions: [{name: b, module: kit_shared}]\n",
                {"kit_shared.py": "def b(rt): pass\n"},
            ),
        )
    (spec,) = load(
        tool(
            "ok",
            "functions: [{name: g, module: kit_s5}]\n",
            {"kit_s5.py": "def g(rt): return 1\n"},
        )
    )
    assert spec.parameters == {"type": "object", "properties": {}}


def test_top_level_names_ignore_caches_and_report_unreadable_sources(tmp_path):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "__pycache__").mkdir()
    (tmp_path / "src" / "mod.py").write_text("")
    (tmp_path / "src" / "notes.txt").write_text("")
    assert kit_tools.top_level_names(tmp_path / "src") == {"pkg", "mod"}
    (tmp_path / "file").write_text("")
    with pytest.raises(kit_tools.ToolLoadError):
        kit_tools.top_level_names(tmp_path / "file")
    assert kit_tools.shadowed("__init__") and kit_tools.shadowed("sys")


def test_tool_runtime_records_evidence():
    runtime = kit_tools.ToolRuntime("c", "s", "t", None, None, {}, {})
    runtime.evidence("kb", "Title", "Text", url="u")
    assert runtime.trace["evidence"] == [
        {"source": "kb", "title": "Title", "text": "Text", "url": "u"}
    ]
    assert kit_tools.as_text("x") == "x" and kit_tools.as_text({"a": 1}) == '{"a": 1}'
