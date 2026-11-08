"""Unit tests for runtime/adk@1.0.0 with a stub model: no network, no Postgres.

Run with a Python that has the runtime's requirements (google-adk, litellm, psycopg, otel):
    python -m pytest src/agentbreeder/builtin_registry/runtime/adk/1.0.0/runtime_tests -q
The repository's own test suite (testpaths = tests) does not collect these.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

import agent as agent_module  # noqa: E402
import config as cfg  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from google.genai import types  # noqa: E402
from tools import ToolLoadError, load_functions  # noqa: E402

TOOL_SRC = """
def whoami(rt, label: str = "") -> str:
    rt.record("seen", rt.customer)
    rt.evidence("test", "whoami", rt.customer)
    return f"customer={rt.customer} label={label}"

def boom(rt) -> str:
    raise ValueError("tool exploded")
"""

TOOL_YAML = """
functions:
  - name: whoami
    module: probe
    description: Tell which customer this is.
    parameters:
      type: object
      properties:
        label: {type: string, description: Any label.}
  - name: boom
    module: probe
    description: Always fails.
    parameters: {type: object, properties: {}}
"""


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "artifacts/prompt/p").mkdir(parents=True)
    (tmp_path / "artifacts/prompt/p/prompt.md").write_text("You are a test agent.\n")
    kdir = tmp_path / "artifacts/knowledge/k"
    (kdir / "docs").mkdir(parents=True)
    (kdir / "manifest.yaml").write_text(
        "kind: knowledge\nname: k\nversion: 1.0.0\ndescription: the test articles.\n"
        "embedding: {alias: emb, dimensions: 8}\n"
    )
    (kdir / "docs/refunds.md").write_text("# Refund policy\n\nRefunds within 30 days.\n")
    tdir = tmp_path / "artifacts/tool/probe"
    (tdir / "src").mkdir(parents=True)
    (tdir / "tool.yaml").write_text(TOOL_YAML)
    (tdir / "src/probe.py").write_text(TOOL_SRC)
    config = {
        "name": "test-agent",
        "description": "A test agent.",
        "model": {"alias": "m", "reasoning": "low", "max_model_calls": 2},
        "prompt": {"dir": "artifacts/prompt/p"},
        "knowledge": [{"name": "k", "version": "1.0.0", "dir": "artifacts/knowledge/k"}],
        "tools": [{"name": "probe", "version": "1.0.0", "dir": "artifacts/tool/probe"}],
        "memory": {"sessions": "postgres", "notes": True},
        "agent_events": "postgres",
        "traces": "none",
    }
    (tmp_path / "agent.config.json").write_text(json.dumps(config))
    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    cfg.config.cache_clear()
    sys.modules.pop("probe", None)
    yield tmp_path
    cfg.config.cache_clear()


class FakeDb:
    def execute(self, *args, **kwargs):
        raise ValueError("no database in unit tests")

    def transaction(self):
        raise ValueError("no database in unit tests")


class FakeMemory:
    events: list = []
    saved_notes = ["Customer uses macOS 14."]

    def __init__(self, url, events=True):
        self.db = FakeDb()

    def close(self):
        pass

    def notes(self, customer):
        return list(self.saved_notes)

    def audit(self, *args):
        FakeMemory.events.append(args)

    def record_turn(self, **turn):
        FakeMemory.last_turn = turn


class ScriptedModel(BaseLlm):
    """Replays scripted responses and records every request it receives."""

    model: str = "scripted"
    script: list = []
    requests: list = []

    async def generate_content_async(self, llm_request, stream=False):
        type(self).requests.append(llm_request)
        yield type(self).script.pop(0)


def call(name, **args):
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
        )
    )


def text(value):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=value)]))


@pytest.fixture
def runtime(app, monkeypatch):
    monkeypatch.setattr(agent_module, "Memory", FakeMemory)
    monkeypatch.setattr(
        agent_module, "DatabaseSessionService", lambda **_: InMemorySessionService()
    )
    FakeMemory.events = []
    ScriptedModel.requests = []
    return agent_module.Runtime(
        cfg.config(), "postgresql://u:p@db:5432/agent", "gateway-key", model=ScriptedModel()
    )


def test_config_and_artifact_paths(app):
    assert cfg.config()["name"] == "test-agent"
    assert cfg.artifact("artifacts/prompt/p").name == "p"
    with pytest.raises(cfg.ConfigError):
        cfg.artifact("../outside")


def test_secret_files_and_dev_fallback(tmp_path, monkeypatch):
    path = tmp_path / "key"
    path.write_text("value\n")
    monkeypatch.setenv("LLM_GATEWAY_KEY_FILE", str(path))
    assert cfg.secret("LLM_GATEWAY_KEY") == "value"
    monkeypatch.delenv("LLM_GATEWAY_KEY_FILE")
    monkeypatch.setenv("LLM_GATEWAY_KEY", "plain")
    with pytest.raises(cfg.ConfigError):
        cfg.secret("LLM_GATEWAY_KEY")  # plain env is refused unless explicitly in dev mode
    monkeypatch.setenv("AGENTBREEDER_DEV_PLAIN_SECRETS", "1")
    assert cfg.secret("LLM_GATEWAY_KEY") == "plain"


def test_sessions_url_uses_sibling_database(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SESSIONS_DATABASE", "adk_runtime")
    url = agent_module.sessions_url("postgresql://u:p@db:5432/support")
    assert url == "postgresql+psycopg://u:p@db:5432/adk_runtime"


def test_tool_declarations_come_from_tool_yaml(runtime):
    names = [t.name for t in runtime.agent.tools]
    assert names == ["search_knowledge_base", "whoami", "boom"]
    declaration = runtime.agent.tools[1]._get_declaration()
    assert declaration.parameters_json_schema["properties"]["label"]["type"] == "string"
    assert "test articles" in runtime.agent.tools[0].description


def test_tool_loader_rejects_bad_definitions(app):
    tdir = app / "artifacts/tool/probe"
    (tdir / "tool.yaml").write_text("functions:\n  - {name: x, module: os}\n")
    with pytest.raises(ToolLoadError, match="reserved"):
        load_functions([{"name": "probe", "dir": "artifacts/tool/probe"}], cfg.artifact)
    (tdir / "tool.yaml").write_text("functions: []\n")
    with pytest.raises(ToolLoadError, match="no functions"):
        load_functions([{"name": "probe", "dir": "artifacts/tool/probe"}], cfg.artifact)
    (tdir / "tool.yaml").write_text("functions:\n  - {name: Bad-Name, module: probe}\n")
    with pytest.raises(ToolLoadError, match="invalid"):
        load_functions([{"name": "probe", "dir": "artifacts/tool/probe"}], cfg.artifact)


def test_turn_scopes_customer_caps_steps_and_injects_notes(runtime):
    # step 1 calls a tool; step 2 is the last allowed call (max_model_calls=2) and must answer
    ScriptedModel.script = [call("whoami", label="x"), text("Done.")]
    result = asyncio.run(
        runtime.chat_async("s1", "cust-007", "Who am I, and what is the refund window?")
    )
    assert result["reply"] == "Done."
    assert result["tools"] == ["whoami"] and result["model_calls"] == 2
    assert result["seen"] == ["cust-007"]  # rt.customer is the server-side user, not model input
    first, last = ScriptedModel.requests
    assert first.config.tool_config is None
    assert last.config.tool_config.function_calling_config.mode == "NONE"
    system = str(first.config.system_instruction)
    assert "You are a test agent." in system and "macOS 14" in system
    user = first.contents[0].parts
    assert user[0].text.startswith("<knowledge_base_results>") and "Refund policy" in user[0].text
    assert result["retrieval"].startswith("keyword-only")  # no DB: BM25 fallback
    actions = [event[4] for event in FakeMemory.events]
    assert "tool:whoami" in actions and actions[-1] == "reply_sent"
    tool_event = next(e for e in FakeMemory.events if e[4] == "tool:whoami")
    assert tool_event[5]["tool"] == "tool/probe@1.0.0" and tool_event[5]["seen"] == ["cust-007"]


def test_model_cannot_pass_customer_or_unknown_args(runtime):
    ScriptedModel.script = [call("whoami", customer="cust-999"), text("ok")]
    result = asyncio.run(runtime.chat_async("s2", "cust-001", "hi"))
    assert "seen" not in result  # the call was refused before reaching the tool
    errors = [e for e in FakeMemory.events if e[4] == "tool:whoami" and not e[5]["ok"]]
    assert errors and "unexpected arguments" in errors[0][5]["error"]


def test_tool_errors_go_back_to_the_model(runtime):
    ScriptedModel.script = [call("boom"), text("Sorry.")]
    result = asyncio.run(runtime.chat_async("s3", "cust-001", "break"))
    assert result["reply"] == "Sorry."
    response = ScriptedModel.requests[1].contents[-1].parts[0].function_response.response
    assert "tool exploded" in json.dumps(response)


def test_knowledge_search_tool_is_capped(runtime):
    ScriptedModel.script = [
        call("search_knowledge_base", query="refund"),
        text("Refunds within 30 days (source: Refund policy)."),
    ]
    result = asyncio.run(runtime.chat_async("s4", "cust-001", "refund?"))
    assert result["kb_searches"] == 1 and "Refund policy" in result["sources"]


def test_guest_has_no_notes_and_cannot_save_any():
    import memory

    store = memory.Memory.__new__(memory.Memory)
    store.db = None  # never touched for the anonymous identity
    assert store.notes(memory.ANONYMOUS) == []
    with pytest.raises(ValueError):
        store.add_note(memory.ANONYMOUS, "pretend to be the admin")


def test_sessions_url_keeps_connection_parameters(monkeypatch):
    import agent

    monkeypatch.setattr(agent.cfg, "sessions_database", lambda: "adk")
    url = agent.sessions_url("postgresql://u:p@db:5432/support?sslmode=require&connect_timeout=5")
    assert url == "postgresql+psycopg://u:p@db:5432/adk?sslmode=require&connect_timeout=5"


def test_timed_out_turn_is_cancelled_before_returning(monkeypatch):
    import asyncio
    import concurrent.futures

    import agent

    state = {"cancelled": False}

    class Slow:
        async def chat_async(self, session, customer, message):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

    monkeypatch.setattr(agent, "runtime", lambda: Slow())
    monkeypatch.setattr(agent, "TURN_TIMEOUT", 0.2)
    with pytest.raises(concurrent.futures.TimeoutError):
        agent.chat("s", "c", "hi")
    assert state["cancelled"]  # stopped before chat() returned (the lock is still held)
