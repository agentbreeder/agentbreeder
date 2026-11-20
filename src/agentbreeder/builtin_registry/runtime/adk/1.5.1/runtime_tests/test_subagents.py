"""runtime/adk@1.5.1: sub-agents as tools (#1058 RT-2), with stub models and no Docker.

A parent with two sub-agents: `billing` (a write tool the parent lacks) and `returns` (the
knowledge search and a tool the parent also has). Most tests script each agent's model; the
cost and tag tests use the runtime's real model client against a fake gateway that answers per
alias and records each request body.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent as agent_module  # noqa: E402
import config as cfg  # noqa: E402
import subagents  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402
from test_model_errors import KEY, error, ok  # noqa: E402
from test_runtime import FakeMemory, ScriptedModel, app, call, text  # noqa: E402, F401
from turns import TURN_KEY  # noqa: E402

REFUND_SRC = """
def file_refund(rt, order: str = "") -> str:
    rt.record("requests", f"refund:{order}:{rt.customer}:{rt.turn}")
    return f"filed for {rt.customer}"
"""
REFUND_YAML = """
functions:
  - name: file_refund
    module: refunds
    description: File a refund request for a person to decide.
    writes_requests: true
    parameters:
      type: object
      properties:
        order: {type: string}
"""
BILLING_PROMPT = "You are the billing specialist. {agentbreeder:turn} {secret?}"


def subagent_config(app, **changes):  # noqa: F811
    """The test agent with two sub-agents; `changes` replace top-level keys."""
    (app / "artifacts/prompt/billing").mkdir(parents=True, exist_ok=True)
    (app / "artifacts/prompt/billing/prompt.md").write_text(BILLING_PROMPT + "\n")
    (app / "artifacts/prompt/returns").mkdir(parents=True, exist_ok=True)
    (app / "artifacts/prompt/returns/prompt.md").write_text("You are the returns specialist.\n")
    tdir = app / "artifacts/tool/refund"
    (tdir / "src").mkdir(parents=True, exist_ok=True)
    (tdir / "tool.yaml").write_text(REFUND_YAML)
    (tdir / "src/refunds.py").write_text(REFUND_SRC)
    config = json.loads((app / "agent.config.json").read_text())
    config["model"]["max_model_calls"] = 6
    config["subagents"] = [
        {
            "name": "billing",
            "description": "Refunds and invoices. Send the question and the order id.",
            "model": {"alias": "pro", "reasoning": "medium", "max_model_calls": 3},
            "max_calls_per_turn": 2,
            "prompt": {"dir": "artifacts/prompt/billing"},
            "tools": [{"name": "refund", "version": "1.0.0", "dir": "artifacts/tool/refund"}],
            "mcp": [],
            "knowledge": False,
        },
        {
            "name": "returns",
            "description": "Return eligibility from the articles.",
            "model": {"alias": "m", "reasoning": "none", "max_model_calls": 4},
            "max_calls_per_turn": 2,
            "prompt": {"dir": "artifacts/prompt/returns"},
            "tools": [{"name": "probe", "version": "1.0.0", "dir": "artifacts/tool/probe"}],
            "mcp": [],
            "knowledge": True,
        },
    ]
    config["limits"] = {"turn_model_calls": 12}
    config.update(changes)
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    sys.modules.pop("refunds", None)
    return config


class Parent(ScriptedModel):
    script: list = []
    requests: list = []


class Billing(ScriptedModel):
    script: list = []
    requests: list = []


class Returns(ScriptedModel):
    script: list = []
    requests: list = []


def patch(monkeypatch):
    monkeypatch.setattr(agent_module, "Memory", FakeMemory)
    monkeypatch.setattr(
        agent_module, "DatabaseSessionService", lambda **_: InMemorySessionService()
    )
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
    FakeMemory.events = []
    for model in (Parent, Billing, Returns):
        model.script, model.requests = [], []


def build(monkeypatch, parent=None, billing=None, returns=None):
    patch(monkeypatch)
    return agent_module.Runtime(
        cfg.config(),
        "postgresql://u:p@db:5432/agent",
        "gateway-key",
        model=parent or Parent(),
        child_models={"billing": billing or Billing(), "returns": returns or Returns()},
    )


@pytest.fixture
def team(app, monkeypatch):  # noqa: F811
    subagent_config(app)
    return build(monkeypatch)


def chat(runtime, message="hi", session="s1", customer="cust-001"):
    return asyncio.run(runtime.chat_async(session, customer, message))


def events(action=None):
    return [event for event in FakeMemory.events if action is None or event[4] == action]


def texts(request):
    """Every text part a model request carried, in order."""
    return [p.text for c in request.contents for p in (c.parts or []) if p.text]


def tools_off(request) -> bool:
    config = request.config.tool_config
    return bool(config and config.function_calling_config.mode == "NONE")


def calls(*pairs):
    """One model response asking for several tools at once."""
    parts = [
        types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in pairs
    ]
    return agent_module.types.Content(role="model", parts=parts)


def many(*pairs):
    from google.adk.models.llm_response import LlmResponse

    return LlmResponse(content=calls(*pairs))


# -- construction ---------------------------------------------------------------------------


def test_the_parent_gains_one_tool_per_sub_agent_after_its_own(team):
    names = [tool.name for tool in team.agent.tools]
    assert names == ["search_knowledge_base", "whoami", "boom", "ask_billing", "ask_returns"]
    declaration = team.agent.tools[3]._get_declaration()
    assert declaration.name == "ask_billing"
    assert declaration.description.startswith("Refunds and invoices")
    schema = declaration.parameters_json_schema
    assert schema["required"] == ["request"] and list(schema["properties"]) == ["request"]


def test_each_sub_agent_has_only_its_own_tools(team):
    billing, returns = team.children["ask_billing"], team.children["ask_returns"]
    assert [t.name for t in billing.agent.tools] == ["file_refund"]
    assert [t.name for t in returns.agent.tools] == ["search_knowledge_base", "whoami", "boom"]
    assert billing.agent.name == "ask_billing" and billing.alias == "pro"
    assert team.turn_cap == 12
    # A tool the parent and a sub-agent share is imported once; each agent has its own tool.
    parent_tool, child_tool = team.agent.tools[1], returns.agent.tools[1]
    assert parent_tool is not child_tool and parent_tool.spec is child_tool.spec


def test_an_agent_without_sub_agents_is_as_before(app, monkeypatch):  # noqa: F811
    patch(monkeypatch)
    runtime = agent_module.Runtime(cfg.config(), "postgresql://u:p@db/a", "k", model=Parent())
    assert runtime.children == {} and runtime.turn_cap is None
    Parent.script = [text("Hi.")]
    result = chat(runtime)
    assert "subagents" not in result
    assert events("model_called")[0][5]["agent"] == "test-agent"
    assert "subagent" not in events("model_called")[0][5]


def test_the_turn_cap_defaults_to_the_parents_calls_plus_six(app, monkeypatch):  # noqa: F811
    config = subagent_config(app)
    del config["limits"]
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    assert build(monkeypatch).turn_cap == 12  # min(40, 6 + 6)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c["subagents"][0].update(name="Bad-Name"), "name must match"),
        (lambda c: c["subagents"][0].update(name=7), "name must match"),
        (lambda c: c["subagents"][0].update(max_calls_per_turn=4), "max_calls_per_turn"),
        (lambda c: c["subagents"][1].update(name="billing"), "already a name"),
        (lambda c: c["subagents"][0].update(name="test_agent"), None),  # fine: ask_test_agent
        (lambda c: c.update(knowledge=[]), "needs a knowledge set"),
        (
            lambda c: c["subagents"][0].update(
                mcp=[{"name": "github", "version": "1.0.0", "tools": ["list_issues"]}]
            ),
            "is not the agent's",
        ),
        (
            lambda c: c["subagents"].extend(
                dict(c["subagents"][0], name=f"extra{i}") for i in range(4)
            ),
            "at most 5",
        ),
    ],
)
def test_a_bad_sub_agent_config_fails_start(app, monkeypatch, change, message):  # noqa: F811
    config = subagent_config(app)
    change(config)
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    if message is None:
        assert "ask_test_agent" in build(monkeypatch).children
        return
    with pytest.raises(cfg.ConfigError, match=message):
        build(monkeypatch)


def test_a_sub_agent_named_like_a_parent_tool_fails_start(app, monkeypatch):  # noqa: F811
    config = subagent_config(app)
    tool_yaml = app / "artifacts/tool/probe/tool.yaml"
    tool_yaml.write_text(
        tool_yaml.read_text().replace("name: whoami", "name: ask_billing\n    function: whoami")
    )
    del config["subagents"][1]
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    with pytest.raises(cfg.ConfigError, match="ask_billing is already a name"):
        build(monkeypatch)


# -- a turn ---------------------------------------------------------------------------------


def routed(team, request="Refund order A-17 for the customer.", **chat_args):
    """The parent asks billing, billing files a refund, both answer."""
    Parent.script = [call("ask_billing", request=request), text("Your refund is requested.")]
    Billing.script = [call("file_refund", order="A-17"), text("Refund request filed.")]
    return chat(team, **chat_args)


def test_the_parent_keeps_the_turn_and_the_reply_names_the_sub_agent(team):
    result = routed(team)
    assert result["reply"] == "Your refund is requested."
    # Every tool any agent called, unqualified: an eval's tools_exclude still catches a
    # sub-agent's tool.
    assert result["tools"] == ["ask_billing", "file_refund"]
    assert result["subagents"] == [
        {
            "name": "billing",
            "calls": 1,
            "model_calls": 2,
            "tools": ["file_refund"],
            "cost_usd": None,
            "cost_unknown": 0,
            "served_by": [],
        }
    ]
    assert result["model_calls"] == 4 and result["model"] == "m"
    # The parent's model got the sub-agent's final text as the tool result.
    response = Parent.requests[1].contents[-1].parts[0].function_response
    assert response.name == "ask_billing" and "Refund request filed." in json.dumps(
        response.response
    )


def test_every_row_a_sub_agent_writes_has_the_parents_turn_id(team):
    result = routed(team)
    assert {event[0] for event in events()} == {result["turn_id"]}
    assert {(event[1], event[2]) for event in events()} == {("s1", "cust-001")}
    # The tool's own record (ToolRuntime.turn) too, not the child's invocation id.
    assert result["requests"] == [f"refund:A-17:cust-001:{result['turn_id']}"]
    assert FakeMemory.last_turn["turn"] == result["turn_id"]
    assert FakeMemory.last_turn["tools"] == ["ask_billing", "file_refund"]


def test_events_name_the_sub_agent_and_its_alias(team):
    request = "Refund order A-17 for the customer."
    routed(team, request)
    called = [event[5] for event in events("model_called")]
    assert [(c["agent"], c.get("subagent"), c["model"], c["step"]) for c in called] == [
        ("test-agent", None, "m", 1),
        ("test-agent", "billing", "pro", 2),
        ("test-agent", "billing", "pro", 3),
        ("test-agent", None, "m", 4),
    ]
    (refund,) = [event[5] for event in events("tool:file_refund")]
    assert refund["subagent"] == "billing" and refund["agent"] == "test-agent"
    assert refund["writes_request"] is True
    assert refund["request_sha256"] == hashlib.sha256(request.encode()).hexdigest()
    assert request not in json.dumps(refund)
    assert refund["requests"] == [f"refund:A-17:cust-001:{events()[0][0]}"]
    (ask,) = [event[5] for event in events("tool:ask_billing")]
    # What the sub-agent's tool recorded is on its own row, not the parent's call.
    assert "requests" not in ask and "subagent" not in ask and ask["ok"] is True
    assert ask["args"] == {"request": request}


def test_a_sub_agent_sees_its_request_and_nothing_else(team):
    FakeMemory.saved_notes = ["Customer uses macOS 14."]
    routed(team, message="My card ends 4242, refund order A-17.")
    first = Billing.requests[0]
    assert texts(first) == ["Refund order A-17 for the customer."]
    system = first.config.system_instruction
    # Its own prompt, verbatim: no customer notes, and no `{...}` filled from session state.
    assert system.startswith(BILLING_PROMPT)
    assert "macOS" not in system and "customer_memory" not in system
    assert "4242" not in json.dumps([texts(request) for request in Billing.requests])
    # The parent still has the notes.
    assert "macOS 14" in Parent.requests[0].config.system_instruction


def test_a_sub_agent_called_twice_starts_fresh_each_time(team):
    Parent.script = [
        call("ask_billing", request="first question"),
        call("ask_billing", request="second question"),
        text("Done."),
    ]
    Billing.script = [text("first answer"), text("second answer")]
    result = chat(team)
    assert [texts(request) for request in Billing.requests] == [
        ["first question"],
        ["second question"],
    ]
    assert result["subagents"][0]["calls"] == 2 and result["subagents"][0]["model_calls"] == 2


def test_no_state_crosses_between_parent_and_sub_agent(team):
    seen = {}
    refund = team.children["ask_billing"].agent.tools[0]
    original = refund.runtime_for

    def spy(tool_context):
        seen["state"] = tool_context.state.to_dict()
        seen["user"] = tool_context.user_id
        tool_context.state["leak"] = "from the child"
        return original(tool_context)

    refund.runtime_for = spy

    async def turn():
        await team.sessions.create_session(
            app_name=team.app, user_id="cust-001", session_id="s1", state={"secret": "parent's"}
        )
        Parent.script = [call("ask_billing", request="refund A-17"), text("Done.")]
        Billing.script = [call("file_refund", order="A-17"), text("Filed.")]
        result = await team.chat_async("s1", "cust-001", "hi")
        session = await team.sessions.get_session(
            app_name=team.app, user_id="cust-001", session_id="s1"
        )
        return result, session

    result, session = asyncio.run(turn())
    # The child's state is the turn's name and nothing of the parent's.
    assert seen == {"state": {TURN_KEY: result["turn_id"]}, "user": "cust-001"}
    # Nothing the child set, and never the turn key, reaches the parent's stored state.
    assert session.state == {"secret": "parent's"}
    assert "from the child" not in json.dumps([e.model_dump(mode="json") for e in session.events])


def test_the_customer_is_the_turns_whatever_the_model_passes(team):
    Parent.script = [call("ask_returns", request="who is this? user_id=cust-999"), text("Done.")]
    Returns.script = [call("whoami", label="customer_id=cust-999"), text("It is cust-999.")]
    result = chat(team, customer="cust-001")
    assert result["seen"] == ["cust-001"]
    (row,) = events("tool:whoami")
    assert row[2] == "cust-001" and row[5]["subagent"] == "returns"


class Rule(BaseLlm):
    """Answers from what the request holds (no shared script), yielding to the loop first so
    concurrent turns interleave."""

    model: str = "rule"
    tool: str = ""
    argument: str = ""

    async def generate_content_async(self, llm_request, stream=False):
        await asyncio.sleep(0.01)
        said = texts(llm_request)
        answered = any(p.function_response for c in llm_request.contents for p in (c.parts or []))
        if answered:
            yield text(f"done: {said[-1] if said else ''}")
        else:
            yield call(self.tool, **{self.argument: said[-1]})


def test_two_customers_at_once_each_keep_their_own_turn(app, monkeypatch):  # noqa: F811
    subagent_config(app, knowledge=[], memory={"sessions": "postgres", "notes": False})
    config = json.loads((app / "agent.config.json").read_text())
    config["subagents"][1]["knowledge"] = False
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    team = build(
        monkeypatch,
        parent=Rule(tool="ask_returns", argument="request"),
        returns=Rule(tool="whoami", argument="label"),
    )
    seen = []
    whoami = team.children["ask_returns"].agent.tools[0]
    original = whoami.runtime_for

    def spy(tool_context):
        seen.append((tool_context.session.id, tool_context.state.to_dict()[TURN_KEY]))
        return original(tool_context)

    whoami.runtime_for = spy

    async def both():
        return await asyncio.gather(
            team.chat_async("sa", "cust-A", "question from A"),
            team.chat_async("sb", "cust-B", "question from B"),
        )

    first, second = asyncio.run(both())
    rows = {row[2]: row for row in events("tool:whoami")}
    assert set(rows) == {"cust-A", "cust-B"}
    for result, customer, message in ((first, "cust-A", "A"), (second, "cust-B", "B")):
        row = rows[customer]
        assert row[0] == result["turn_id"] and row[5]["args"] == {
            "label": f"question from {message}"
        }
        # What the sub-agent's tool recorded is on that customer's row and reply only.
        assert row[5]["seen"] == [customer] and result["seen"] == [customer]
        assert result["subagents"][0]["tools"] == ["whoami"]
        assert result["tools"] == ["ask_returns", "whoami"]
    # A fresh child session per call, each naming its own turn; none shared.
    assert len({session for session, _ in seen}) == 2
    assert {turn for _, turn in seen} == {first["turn_id"], second["turn_id"]}
    assert team.turns == {}


def test_the_turn_is_found_in_a_task_without_any_context_variable(team, monkeypatch):
    original = subagents.SubAgentTool.run_async

    async def elsewhere(self, *, args, tool_context):
        # A task whose context holds none of the turn's variables.
        work = original(self, args=args, tool_context=tool_context)
        return await asyncio.create_task(work, context=contextvars.Context())

    monkeypatch.setattr(subagents.SubAgentTool, "run_async", elsewhere)
    result = routed(team)
    assert result["reply"] == "Your refund is requested."
    assert result["subagents"][0]["model_calls"] == 2
    assert {event[0] for event in events()} == {result["turn_id"]}


def test_a_lost_turn_in_a_sub_agent_fails_only_that_call(team, monkeypatch):
    monkeypatch.setattr(subagents, "TURN_KEY", "not-the-key")  # the child's session names nothing
    Parent.script = [call("ask_billing", request="refund A-17"), text("I could not check.")]
    Billing.script = [text("never trusted")]
    result = chat(team)
    assert result["reply"] == "I could not check."
    response = Parent.requests[1].contents[-1].parts[0].function_response.response
    assert response == {"error": subagents.COULD_NOT_RUN}
    (lost,) = events("subagent_turn_unknown")
    assert lost[0] == result["turn_id"] and lost[5] == {"subagent": "billing"}
    (row,) = events("tool:ask_billing")
    assert row[5]["ok"] is False
    # Nothing of the child was counted or audited.
    assert [event[5].get("subagent") for event in events("model_called")] == [None, None]
    assert result["subagents"][0]["model_calls"] == 0


def test_an_empty_answer_is_a_fixed_result(team):
    Parent.script = [call("ask_billing", request="refund A-17"), text("Done.")]
    Billing.script = [text("")]
    chat(team)
    response = Parent.requests[1].contents[-1].parts[0].function_response.response
    assert subagents.COULD_NOT_FINISH in json.dumps(response)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"request": "x" * 4001}, "longer than 4000"),
        ({"request": ""}, "missing arguments: request"),
        ({"request": 7}, "missing arguments: request"),
        ({}, "missing arguments: request"),
        ({"request": "ok", "user_id": "cust-999"}, "unexpected arguments: user_id"),
    ],
)
def test_a_bad_request_is_a_tool_error_and_the_sub_agent_does_not_run(team, args, message):
    Parent.script = [call("ask_billing", **args), text("Done.")]
    result = chat(team)
    response = Parent.requests[1].contents[-1].parts[0].function_response.response
    assert message in response["error"]
    assert Billing.requests == [] and result["subagents"] == []


def test_a_request_of_exactly_the_limit_runs(team):
    Parent.script = [call("ask_billing", request="x" * 4000), text("Done.")]
    Billing.script = [text("ok")]
    assert chat(team)["subagents"][0]["calls"] == 1


def test_a_sub_agent_searches_the_knowledge_once_itself(team):
    Parent.script = [call("ask_returns", request="refund window?"), text("30 days.")]
    Returns.script = [
        call("search_knowledge_base", query="refund"),
        call("search_knowledge_base", query="refund again"),
        text("Refunds within 30 days."),
    ]
    result = chat(team)
    assert result["kb_searches"] == 1  # the config's extra_searches default allows one each
    answers = [
        p.function_response.response
        for c in Returns.requests[2].contents
        for p in (c.parts or [])
        if p.function_response
    ]
    assert "Refund policy" in json.dumps(answers[0]) and "Search limit" in json.dumps(answers[1])
    # The parent's pre-retrieved passages are not passed to the child.
    assert texts(Returns.requests[0]) == ["refund window?"]


# -- caps -----------------------------------------------------------------------------------


def test_a_sub_agent_counts_its_own_model_calls(app, monkeypatch):  # noqa: F811
    config = subagent_config(app)
    config["model"]["max_model_calls"] = 2  # below the child's 3
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    team = build(monkeypatch)
    Parent.script = [call("ask_billing", request="refund"), text("Done.")]
    Billing.script = [
        call("file_refund", order="1"),
        call("file_refund", order="2"),
        text("Both filed."),
    ]
    result = chat(team)
    assert result["reply"] == "Done." and result["subagents"][0]["model_calls"] == 3
    # Tools go off at each agent's own last call, not at the parent's number.
    assert [tools_off(r) for r in Billing.requests] == [False, False, True]
    assert [tools_off(r) for r in Parent.requests] == [False, True]


def test_a_sub_agents_calls_count_across_its_calls_in_a_turn(team):
    Parent.script = [
        call("ask_billing", request="one"),
        call("ask_billing", request="two"),
        text("Done."),
    ]
    Billing.script = [call("file_refund", order="1"), text("a"), text("b")]
    chat(team)
    # max_model_calls 3 per turn: the second call starts at its third, with tools off.
    assert [tools_off(r) for r in Billing.requests] == [False, False, True]


def test_the_turn_cap_turns_tools_off_for_every_agent(app, monkeypatch):  # noqa: F811
    subagent_config(app, limits={"turn_model_calls": 3})
    team = build(monkeypatch)
    Parent.script = [call("ask_billing", request="refund"), text("Sorry, out of steps.")]
    Billing.script = [call("file_refund", order="1"), text("Filed one.")]
    result = chat(team)
    # Calls 1 (parent), 2 and 3 (billing): at the third the cap is reached, so billing must
    # answer; the parent's next call is its last. Within turn_model_calls + 2.
    assert [tools_off(r) for r in Billing.requests] == [False, True]
    assert [tools_off(r) for r in Parent.requests] == [False, True]
    assert result["model_calls"] == 4 <= 3 + 2
    assert result["reply"] == "Sorry, out of steps."


def test_a_third_call_is_already_consulted_without_running_the_sub_agent(team):
    Parent.script = [
        call("ask_billing", request="one"),
        call("ask_billing", request="two"),
        call("ask_billing", request="three"),
        text("Done."),
    ]
    Billing.script = [text("a"), text("b"), text("never")]
    result = chat(team)
    assert len(Billing.requests) == 2 and result["subagents"][0]["calls"] == 2
    response = Parent.requests[3].contents[-1].parts[0].function_response.response
    assert response == {"error": subagents.ALREADY_CONSULTED}
    assert [event[5]["ok"] for event in events("tool:ask_billing")] == [True, True, False]
    assert result["tools"].count("ask_billing") == 3


class Slow(ScriptedModel):
    script: list = []
    requests: list = []
    log: list = []

    async def generate_content_async(self, llm_request, stream=False):
        type(self).log.append(("start", llm_request.config.system_instruction[:20]))
        await asyncio.sleep(0.05)
        type(self).log.append(("end", llm_request.config.system_instruction[:20]))
        yield text("answer")


def test_two_sub_agents_asked_at_once_run_one_after_the_other(app, monkeypatch):  # noqa: F811
    subagent_config(app)
    Slow.log = []
    team = build(monkeypatch, billing=Slow(), returns=Slow())
    Parent.script = [
        many(("ask_billing", {"request": "one"}), ("ask_returns", {"request": "two"})),
        text("Done."),
    ]
    result = chat(team)
    assert [step for step, _ in Slow.log] == ["start", "end", "start", "end"]
    assert [entry["name"] for entry in result["subagents"]] == ["billing", "returns"]


def test_the_reply_lists_sub_agents_in_first_call_order(team):
    Parent.script = [
        call("ask_returns", request="one"),
        call("ask_billing", request="two"),
        call("ask_returns", request="three"),
        text("Done."),
    ]
    Returns.script = [text("a"), text("c")]
    Billing.script = [text("b")]
    result = chat(team)
    assert [(e["name"], e["calls"]) for e in result["subagents"]] == [
        ("returns", 2),
        ("billing", 1),
    ]


def test_asks_in_one_response_cannot_run_past_the_turn_cap(app, monkeypatch):  # noqa: F811
    # Review r1 (A-M1): three asks arrive together when one step is left.
    subagent_config(app, limits={"turn_model_calls": 3})
    team = build(monkeypatch)
    Parent.script = [
        text(""),  # replaced below: the first response is the three asks
        text("Out of steps."),
    ]
    Parent.script[0] = many(
        ("ask_billing", {"request": "one"}),
        ("ask_returns", {"request": "two"}),
        ("ask_billing", {"request": "three"}),
    )
    Billing.script = [call("file_refund", order="1"), text("filed"), text("never")]
    Returns.script = [text("never")]
    result = chat(team)
    # Parent 1, billing 2 and 3 (the cap): the other two asks never start a model call.
    assert result["model_calls"] == 4 <= 3 + 2
    assert len(Billing.requests) == 2 and Returns.requests == []
    replies = [
        p.function_response.response
        for c in Parent.requests[1].contents
        for p in (c.parts or [])
        if p.function_response
    ]
    assert replies[1] == {"error": subagents.OUT_OF_STEPS} == replies[2]
    # Only a sub-agent that ran is listed: an eval's subagents_include must not pass for one
    # that was asked and refused (review r1, M2).
    assert [(e["name"], e["calls"]) for e in result["subagents"]] == [("billing", 1)]
    assert [event[5]["ok"] for event in events("tool:ask_returns")] == [False]


def test_a_sub_agent_out_of_its_own_calls_does_not_start_again(team):
    # Review r1 (A-L1): billing's three model calls are spent in its first call.
    Parent.script = [
        call("ask_billing", request="one"),
        call("ask_billing", request="two"),
        text("Done."),
    ]
    Billing.script = [
        call("file_refund", order="1"),
        call("file_refund", order="2"),
        text("both filed"),
        text("never"),
    ]
    result = chat(team)
    assert len(Billing.requests) == 3 and result["subagents"][0]["model_calls"] == 3
    response = Parent.requests[2].contents[-1].parts[0].function_response.response
    assert response == {"error": subagents.OUT_OF_STEPS}
    assert result["subagents"][0]["calls"] == 1


def test_a_sibling_tools_row_holds_only_what_it_recorded_itself(team):
    # Review r1 (A-M2): the parent's whoami runs while billing's file_refund records.
    Parent.script = [
        many(("ask_billing", {"request": "refund"}), ("whoami", {"label": "x"})),
        text("Done."),
    ]
    Billing.script = [call("file_refund", order="A-17"), text("filed")]
    result = chat(team)
    (whoami,) = [event[5] for event in events("tool:whoami")]
    (refund,) = [event[5] for event in events("tool:file_refund")]
    (ask,) = [event[5] for event in events("tool:ask_billing")]
    assert whoami["seen"] == ["cust-001"] and "requests" not in whoami
    assert refund["requests"] == result["requests"] and "seen" not in refund
    assert "requests" not in ask and "seen" not in ask
    assert "subagent" not in whoami and refund["subagent"] == "billing"


def test_another_failure_in_a_sub_agent_is_a_fixed_tool_error(team, capsys):
    class Broken(ScriptedModel):
        async def generate_content_async(self, llm_request, stream=False):
            raise RuntimeError("internal detail sk-secretsecret12345 /srv/app/agent.py")
            yield  # pragma: no cover

    team.children["ask_billing"].agent.model = Broken()
    Parent.script = [call("ask_billing", request="refund"), text("I could not check.")]
    result = chat(team)
    assert result["reply"] == "I could not check."
    response = Parent.requests[1].contents[-1].parts[0].function_response.response
    assert response == {"error": subagents.COULD_NOT_RUN}
    (row,) = events("tool:ask_billing")
    assert row[5]["error"] == subagents.COULD_NOT_RUN and "internal detail" not in json.dumps(row)
    line = capsys.readouterr().out
    assert "sub-agent billing failed" in line and "internal detail" in line
    assert "secretsecret" not in line


def test_a_turn_cancelled_inside_a_sub_agent_leaves_nothing_behind(app, monkeypatch):  # noqa: F811
    subagent_config(app)
    started = asyncio.Event()

    class Stuck(ScriptedModel):
        async def generate_content_async(self, llm_request, stream=False):
            started.set()
            await asyncio.sleep(30)
            yield text("never")

    team = build(monkeypatch, billing=Stuck())
    Parent.script = [call("ask_billing", request="refund"), text("never")]
    locks = []

    async def cancelled():
        turn = asyncio.create_task(team.chat_async("s1", "cust-001", "hi"))
        await started.wait()
        locks.append(next(iter(team.turns.values()))["subagent_lock"])
        assert locks[0].locked()
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn
        before = len(FakeMemory.events)
        await asyncio.sleep(0.05)
        return before

    before = asyncio.run(cancelled())
    assert team.turns == {} and not locks[0].locked()
    assert len(FakeMemory.events) == before  # nothing is written after the turn ended


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c.update(limits={"turn_model_calls": 0}), "turn_model_calls must be 1 to 40"),
        (lambda c: c.update(limits={"turn_model_calls": -1}), "turn_model_calls must be 1 to 40"),
        (lambda c: c.update(limits={"turn_model_calls": 41}), "turn_model_calls must be 1 to 40"),
        (lambda c: c["subagents"][0]["model"].update(max_model_calls=0), "max_model_calls"),
        (lambda c: c["subagents"][0]["model"].update(max_model_calls=21), "max_model_calls"),
        (lambda c: c["model"].update(max_model_calls=0), "at least 1"),
    ],
)
def test_caps_out_of_range_fail_start(app, monkeypatch, change, message):  # noqa: F811
    config = subagent_config(app)
    change(config)
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    with pytest.raises(cfg.ConfigError, match=message):
        build(monkeypatch)


# -- cost and tags, through the real model client -------------------------------------------


class Gateway:
    """A fake LiteLLM proxy that answers per alias (the request's `model`) from a script and
    records every request body."""

    def __init__(self):
        self.scripts: dict[str, list] = {}
        self.bodies: list[dict] = []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                gateway.bodies.append(body)
                script = gateway.scripts[body["model"]]
                # A function answers from the request itself, so concurrent turns cannot take
                # each other's scripted replies.
                status, headers, answer = script(body) if callable(script) else script.pop(0)
                data = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def asks(name, request, cost="0.001"):
    """A gateway reply in which the model calls one tool."""
    status, headers, body = ok(headers={"x-litellm-response-cost": cost})
    body["choices"][0]["message"] = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_0",  # the same id from every agent's model, on purpose
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(request)},
            }
        ],
    }
    body["choices"][0]["finish_reason"] = "tool_calls"
    return status, headers, body


def says(content, cost="0.001", **headers):
    return ok(content, {"x-litellm-response-cost": cost, **headers})


@pytest.fixture
def gateway(app, monkeypatch):  # noqa: F811
    fake = Gateway()
    monkeypatch.setenv("AGENTBREEDER_GATEWAY_URL", fake.url)
    monkeypatch.setattr(agent_module, "RETRY_BACKOFF", 0.01)
    yield fake
    fake.close()


def real_team(app, monkeypatch):  # noqa: F811
    subagent_config(app, knowledge=[])
    config = json.loads((app / "agent.config.json").read_text())
    config["subagents"][1]["knowledge"] = False
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    patch(monkeypatch)
    return agent_module.Runtime(cfg.config(), "postgresql://u:p@db/agent", KEY)


def test_cost_is_split_by_agent_and_sums_to_the_turn(app, monkeypatch, gateway):  # noqa: F811
    team = real_team(app, monkeypatch)
    gateway.scripts = {
        "m": [asks("ask_billing", {"request": "refund A-17"}, "0.002"), says("Done.", "0.003")],
        "pro": [
            asks("file_refund", {"order": "A-17"}, "0.010"),
            says("Filed.", "0.020", **{"x-litellm-model-group": "pro"}),
        ],
    }
    seen = {}
    original = team.subagent_reply

    def spy(used):
        seen["by_agent"] = dict(team.turns[next(iter(team.turns))]["cost"]["by_agent"])
        return original(used)

    team.subagent_reply = spy
    result = chat(team)
    assert result["reply"] == "Done." and result["cost_usd"] == 0.035
    (billing,) = result["subagents"]
    assert billing["cost_usd"] == 0.03 and billing["cost_unknown"] == 0
    assert billing["served_by"] == ["pro"] and billing["model_calls"] == 2
    by_agent = seen["by_agent"]
    assert set(by_agent) == {"", "billing"}
    assert round(sum(bucket["usd"] for bucket in by_agent.values()), 10) == result["cost_usd"]
    assert sum(bucket["calls"] for bucket in by_agent.values()) == 4
    # Each model_called row has its own call's cost, whichever agent made it.
    assert [event[5]["cost_usd"] for event in events("model_called")] == [
        0.002,
        0.010,
        0.020,
        0.003,
    ]
    # The same function call id from two agents' models does not mix their tool rows.
    assert all("latency_ms" in event[5] for event in events() if event[4].startswith("tool:"))


def test_cost_is_counted_in_a_task_without_the_turns_variable(
    app,  # noqa: F811
    monkeypatch,
    gateway,
):
    team = real_team(app, monkeypatch)
    original = subagents.SubAgentTool.run_async

    async def elsewhere(self, *, args, tool_context):
        work = original(self, args=args, tool_context=tool_context)
        return await asyncio.create_task(work, context=contextvars.Context())

    monkeypatch.setattr(subagents.SubAgentTool, "run_async", elsewhere)
    gateway.scripts = {
        "m": [asks("ask_billing", {"request": "refund"}, "0.002"), says("Done.", "0.003")],
        "pro": [says("Filed.", "0.020")],
    }
    result = chat(team)
    # The model client finds the turn's tally through the callback, not an inherited variable.
    assert result["subagents"][0]["cost_usd"] == 0.02 and result["cost_usd"] == 0.025


def test_two_customers_costs_stay_in_their_own_turns(app, monkeypatch, gateway):  # noqa: F811
    team = real_team(app, monkeypatch)

    def parent(body):
        answered = any(message["role"] == "tool" for message in body["messages"])
        if answered:
            return says("Done.", "0.003")
        return asks("ask_billing", {"request": "refund"}, "0.002")

    gateway.scripts = {"m": parent, "pro": lambda body: says("Filed.", "0.020")}

    async def both():
        return await asyncio.gather(
            team.chat_async("sa", "cust-A", "refund please"),
            team.chat_async("sb", "cust-B", "refund please"),
        )

    for result in asyncio.run(both()):
        assert result["cost_usd"] == 0.025 and result["subagents"][0]["cost_usd"] == 0.02
        assert result["model_calls"] == 3


def test_a_call_without_a_cost_makes_that_agent_unknown(app, monkeypatch, gateway):  # noqa: F811
    team = real_team(app, monkeypatch)
    status, headers, body = ok("Filed.")
    gateway.scripts = {
        "m": [asks("ask_billing", {"request": "refund"}), says("Done.")],
        "pro": [asks("file_refund", {"order": "1"}, "0.010"), (status, {}, body)],
    }
    result = chat(team)
    (billing,) = result["subagents"]
    # The priced call's cost and a count of the unpriced ones (an integer, not a flag).
    assert billing["cost_usd"] == 0.01 and billing["cost_unknown"] == 1
    assert result["cost_usd"] is None


def test_each_agents_requests_carry_its_tag_and_nothing_else(app, monkeypatch, gateway):  # noqa: F811
    team = real_team(app, monkeypatch)
    gateway.scripts = {
        "m": [asks("ask_billing", {"request": "refund for cust-001"}), says("Done.")],
        "pro": [says("Filed.")],
    }
    chat(team, message="I am cust-001, refund please")
    tags = [(body["model"], body["metadata"]) for body in gateway.bodies]
    assert tags == [
        ("m", {"tags": ["agentbreeder-subagent:-"]}),
        ("pro", {"tags": ["agentbreeder-subagent:billing"]}),
        ("m", {"tags": ["agentbreeder-subagent:-"]}),
    ]
    # Not streamed: a streamed response has no cost header (the S0 record).
    assert not any(body.get("stream") for body in gateway.bodies)
    assert gateway.bodies[1]["reasoning_effort"] == "medium"


def test_an_agent_without_sub_agents_sends_no_tags(app, monkeypatch, gateway):  # noqa: F811
    patch(monkeypatch)
    runtime = agent_module.Runtime(cfg.config(), "postgresql://u:p@db/agent", KEY)
    gateway.scripts = {"m": [says("Hi.")]}
    chat(runtime)
    assert "metadata" not in gateway.bodies[0]


def test_the_tag_is_the_validated_name_only():
    assert subagents.request_tags("billing") == {
        "extra_body": {"metadata": {"tags": ["agentbreeder-subagent:billing"]}}
    }
    assert subagents.request_tags(None) == {
        "extra_body": {"metadata": {"tags": ["agentbreeder-subagent:-"]}}
    }


def test_a_sub_agents_failed_model_call_ends_the_turn_with_its_code(
    app,  # noqa: F811
    monkeypatch,
    gateway,
):
    team = real_team(app, monkeypatch)
    gateway.scripts = {
        "m": [asks("ask_billing", {"request": "refund"}), says("never")],
        "pro": [error(401, "auth_error", "Invalid key sk-secret-detail")],
    }
    with pytest.raises(agent_module.ModelError) as caught:
        chat(team)
    assert caught.value.code == "MODEL_ACCESS_DENIED"
    (row,) = events("tool:ask_billing")
    assert row[5]["ok"] is False and "sk-secret" not in json.dumps(row[5])
    assert len(events("turn_failed")) == 1 and team.turns == {}


# -- spans ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def exporter():
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memory = InMemorySpanExporter()
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    provider.add_span_processor(SimpleSpanProcessor(memory))
    return memory


def test_a_sub_agents_spans_reach_the_root_span_and_name_it(team, exporter):
    exporter.clear()
    routed(team)
    spans = exporter.get_finished_spans()
    by_id = {span.context.span_id: span for span in spans}
    (root,) = [span for span in spans if span.name == "agent_turn"]

    def reaches_root(span):
        while span.parent is not None:
            span = by_id.get(span.parent.span_id)
            if span is None:
                return False
        return span is root

    assert all(reaches_root(span) for span in spans)
    marked = [span for span in spans if span.attributes.get("agentbreeder.subagent")]
    assert {span.attributes["agentbreeder.subagent"] for span in marked} == {"billing"}
    kinds = [span.attributes.get("openinference.span.kind") for span in marked]
    # The parent's ask_billing call and the child's file_refund call; the child's agent span;
    # its two model calls.
    assert kinds.count("TOOL") == 2 and kinds.count("AGENT") >= 1 and kinds.count("CHAIN") == 2
    ask = next(span for span in marked if span.attributes.get("tool.name") == "ask_billing")
    child_agent = next(
        span for span in marked if span.attributes.get("openinference.span.kind") == "AGENT"
    )
    # The child's agent span is under the parent's tool call.
    span, under = child_agent, False
    while span.parent is not None and span.parent.span_id in by_id:
        span = by_id[span.parent.span_id]
        under = under or span is ask
    assert under
    # The parent's own spans are not marked.
    parent_calls = [
        span
        for span in spans
        if span.attributes.get("openinference.span.kind") == "CHAIN" and span not in marked
    ]
    assert len(parent_calls) == 2


# -- MCP ------------------------------------------------------------------------------------


def test_a_sub_agent_reaches_only_its_listed_mcp_tools_through_its_own_session(
    app,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    from relay_harness import TOKEN, Relay, Upstream

    upstream = Upstream()
    relay = Relay(tmp_path / "relay", upstream)
    try:
        config = subagent_config(app)
        config["mcp"] = [
            {"name": "github", "version": "1.0.0", "tools": ["list_issues", "create_issue"]}
        ]
        config["subagents"][0]["mcp"] = [
            {"name": "github", "version": "1.0.0", "tools": ["list_issues"]}
        ]
        (app / "agent.config.json").write_text(json.dumps(config))
        cfg.config.cache_clear()
        secret = app / "mcp-github-token"
        secret.write_text(TOKEN + "\n")
        monkeypatch.setenv("AGENTBREEDER_MCP_GITHUB_TOKEN_FILE", str(secret))
        monkeypatch.setenv("AGENTBREEDER_MCP_GITHUB_URL", relay.url)
        team = build(monkeypatch)
        billing = team.children["ask_billing"]
        assert [toolset.server.tools for toolset in billing.toolsets] == [("list_issues",)]
        assert len(team.mcp_toolsets) == 2  # the parent's session and the sub-agent's
        assert billing.toolsets[0] is not team.agent.tools[-1]
        Parent.script = [call("ask_billing", request="open issues?"), text("Two issues.")]
        Billing.script = [call("github_list_issues", q="open"), text("Two are open.")]

        async def turn_then_close():
            result = await team.chat_async("s1", "cust-001", "hi")
            sessions = len(relay.relay.sessions)
            await team.close_mcp(5)
            return result, sessions

        result, sessions = asyncio.run(turn_then_close())
        assert result["reply"] == "Two issues." and upstream.calls == {
            "list_issues": [{"q": "open"}]
        }
        declared = sorted(
            d.name for tool in Billing.requests[0].config.tools for d in tool.function_declarations
        )
        assert declared == ["file_refund", "github_list_issues"]  # never create_issue
        (row,) = events("tool:github_list_issues")
        assert row[5]["subagent"] == "billing" and row[5]["mcp"] == "mcp/github@1.0.0"
        assert row[0] == result["turn_id"] and row[5]["ok"] is True
        assert sessions >= 1
    finally:
        relay.stop()
        upstream.stop()


def test_a_sub_agents_empty_mcp_tool_list_fails_start(app, monkeypatch, tmp_path):  # noqa: F811
    from relay_harness import TOKEN

    config = subagent_config(app)
    config["mcp"] = [{"name": "github", "version": "1.0.0", "tools": ["list_issues"]}]
    config["subagents"][0]["mcp"] = [{"name": "github", "version": "1.0.0", "tools": []}]
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    secret = app / "mcp-github-token"
    secret.write_text(TOKEN + "\n")
    monkeypatch.setenv("AGENTBREEDER_MCP_GITHUB_TOKEN_FILE", str(secret))
    monkeypatch.setenv("AGENTBREEDER_MCP_GITHUB_URL", "http://127.0.0.1:9/mcp")
    with pytest.raises(cfg.ConfigError, match="must list tools the agent has"):
        build(monkeypatch)
