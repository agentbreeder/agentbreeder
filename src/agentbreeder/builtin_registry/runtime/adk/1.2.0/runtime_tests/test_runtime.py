"""Unit tests for runtime/adk@1.2.0 with a stub model: no network, no Postgres.

Run with a Python that has the runtime's requirements (google-adk, litellm, psycopg, otel):
    python -m pytest src/agentbreeder/builtin_registry/runtime/adk/1.2.0/runtime_tests -q
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
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
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


def test_sessions_live_in_a_schema_of_the_agents_own_database():
    # One database credential per agent: ADK's tables go in their own schema, not a sibling
    # database the agent's role may not be allowed to open.
    url = agent_module.sessions_url("postgresql://u:p@db:5432/support")
    assert url == "postgresql+psycopg://u:p@db:5432/support?options=-csearch_path%3Dadk_sessions"


def test_sessions_schema_is_a_plain_identifier(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SESSIONS_SCHEMA", "my_sessions")
    assert cfg.sessions_schema() == "my_sessions"
    for bad in ("Bad", "a b", "x;drop", "", "a" * 64, "pg_temp"):
        monkeypatch.setenv("AGENTBREEDER_SESSIONS_SCHEMA", bad)
        with pytest.raises(cfg.ConfigError):
            cfg.sessions_schema()


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

    url = agent.sessions_url("postgresql://u:p@db:5432/support?sslmode=require&connect_timeout=5")
    assert url == (
        "postgresql+psycopg://u:p@db:5432/support?sslmode=require&connect_timeout=5"
        "&options=-csearch_path%3Dadk_sessions"
    )
    # Operator-set server options are kept; the search path is added to them.
    url = agent.sessions_url("postgresql://u:p@db/support?options=-cstatement_timeout%3D5000")
    assert url.endswith("?options=-cstatement_timeout%3D5000+-csearch_path%3Dadk_sessions")


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
                await asyncio.sleep(0.5)  # unwinding takes time (e.g. closing a span)
                state["cancelled"] = True
                raise

    monkeypatch.setattr(agent, "runtime", lambda: Slow())
    monkeypatch.setattr(agent, "TURN_TIMEOUT", 0.2)
    with pytest.raises(concurrent.futures.TimeoutError):
        agent.chat("s", "c", "hi")
    assert state["cancelled"]  # stopped before chat() returned (the lock is still held)


def test_timed_out_turn_waits_for_in_flight_tool_thread(monkeypatch):
    import concurrent.futures
    import time

    import agent
    from tools import run_to_completion

    writes = []

    def slow_write():
        time.sleep(0.6)
        writes.append("done")

    class Turn:
        async def chat_async(self, session, customer, message):
            await run_to_completion(slow_write)

    monkeypatch.setattr(agent, "runtime", lambda: Turn())
    monkeypatch.setattr(agent, "TURN_TIMEOUT", 0.1)
    with pytest.raises(concurrent.futures.TimeoutError):
        agent.chat("s", "c", "hi")
    assert writes == ["done"]  # the write finished before chat() returned and freed the lock


def test_session_stays_busy_until_a_slow_cancelled_turn_finishes(monkeypatch):
    import concurrent.futures
    import time

    import agent
    from tools import run_to_completion

    def slow_write():
        time.sleep(1.0)

    class Turn:
        async def chat_async(self, session, customer, message):
            await run_to_completion(slow_write)
            return {"reply": "ok"}

    monkeypatch.setattr(agent, "runtime", lambda: Turn())
    monkeypatch.setattr(agent, "TURN_TIMEOUT", 0.1)
    monkeypatch.setattr(agent, "CANCEL_GRACE", 0.1)  # grace shorter than the in-flight write
    with pytest.raises(concurrent.futures.TimeoutError):
        agent.chat("busy-session", "c", "hi")
    with pytest.raises(agent.SessionBusy):  # the write is still running: no overlapping retry
        agent.chat("busy-session", "c", "again")
    time.sleep(1.2)
    monkeypatch.setattr(agent, "TURN_TIMEOUT", 5)
    assert agent.chat("busy-session", "c", "after")["reply"] == "ok"


def test_runtime_reports_its_own_version():
    assert agent_module.RUNTIME_REF == "runtime/adk@1.2.0"
    manifest = (RUNTIME.parent / "manifest.yaml").read_text()
    assert "version: 1.2.0" in manifest


def test_session_locks_are_evicted_when_idle(app):
    import server  # renders its page from the app's config at import

    server.LOCKS.clear()
    with server.session_lock("s1"):
        assert "s1" in server.LOCKS
        with pytest.raises(agent_module.SessionBusy):
            with server.session_lock("s1", blocking=False):
                pass
    assert "s1" not in server.LOCKS  # a stream of new session ids can't grow it without bound
    for number in range(100):
        with server.session_lock(f"x{number}"):
            pass
    assert not server.LOCKS


def test_model_errors_reach_callers_without_upstream_detail(app):
    import server

    leaky = agent_module.ModelError(401, "Incorrect API key sk-abcdef1234567890 at http://gw:4000")
    for status, expected in ((429, 429), (401, 502), (500, 502)):
        leaky.status = status
        code, body = server.model_error_response(leaky)
        assert code == expected
        assert "sk-" not in body["error"] and "gw:4000" not in body["error"]


def test_stored_failure_detail_redacts_key_shaped_strings():
    text = agent_module.redact(
        "AuthenticationError: bad key sk-abcdef1234567890, Bearer eyJhbGciOiJIUzI1NiJ9.x.y and "
        "AIzaSyA1234567890abcdefghij"
    )
    assert "abcdef1234567890" not in text and "eyJhbGci" not in text and "SyA12345" not in text
    assert text.startswith("AuthenticationError: bad key sk-[redacted]")


class CountDb:
    """Answers the runtime's existence queries; records every statement."""

    def __init__(self, answer):
        self.answer, self.statements = answer, []

    def execute(self, sql, params=None):
        self.statements.append(str(sql))
        answer = self.answer

        class Result:
            def fetchone(self):
                return answer

        return Result()

    def transaction(self):
        import contextlib

        return contextlib.nullcontext()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize(("found", "managed"), [(0, False), (4, True)])
def test_owner_created_tables_mean_no_ddl(monkeypatch, found, managed):
    import memory

    db = CountDb((found,))
    monkeypatch.setattr(memory.psycopg, "connect", lambda *a, **k: db)
    monkeypatch.setattr(memory, "_schema_ready", False)
    memory.Memory("postgresql://agent@db/support")
    assert memory.owner_managed(db) is managed
    assert any("CREATE TABLE" in s for s in db.statements) is not managed


def test_partly_created_tables_are_refused():
    import memory

    with pytest.raises(RuntimeError, match="all or none"):
        memory.owner_managed(CountDb((2,)))


@pytest.mark.parametrize(("exists", "creates"), [((1,), False), (None, True)])
def test_sessions_schema_is_created_only_when_missing(monkeypatch, exists, creates):
    import agent

    db = CountDb(exists)
    monkeypatch.setattr(agent.psycopg, "connect", lambda *a, **k: db)
    agent.ensure_sessions_schema("postgresql://agent@db/support")
    assert any("CREATE SCHEMA" in s for s in db.statements) is creates


# -- runtime/adk@1.2.0: tool row detail, shadowing, per-turn cost ---------------------------


def tool_rows(name):
    return [e[5] for e in FakeMemory.events if e[4] == f"tool:{name}"]


def test_tool_rows_carry_agent_tool_and_latency_on_success_and_error(runtime):
    ScriptedModel.script = [call("whoami", label="x"), text("ok")]
    asyncio.run(runtime.chat_async("s5", "cust-001", "hi"))
    ScriptedModel.script = [call("boom"), text("Sorry.")]
    asyncio.run(runtime.chat_async("s6", "cust-001", "break"))
    (good,), (bad,) = tool_rows("whoami"), tool_rows("boom")
    for row in (good, bad):
        assert row["agent"] == "test-agent"
        assert row["tool"] == "tool/probe@1.0.0"
        assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0
    assert good["ok"] is True and bad["ok"] is False and "tool exploded" in bad["error"]


def test_refused_arguments_still_name_the_tool(runtime):
    ScriptedModel.script = [call("whoami", customer="cust-999"), text("ok")]
    asyncio.run(runtime.chat_async("s7", "cust-001", "hi"))
    (row,) = tool_rows("whoami")
    assert row["ok"] is False and row["tool"] == "tool/probe@1.0.0" and "latency_ms" in row
    # one row per call: ADK's after-tool callback, which follows the error callback, adds none


def test_an_unknown_tool_is_one_failed_row(runtime):
    # ADK runs only the error callback here (no after-tool callback follows)
    ScriptedModel.script = [call("no_such_tool"), text("Sorry.")]
    asyncio.run(runtime.chat_async("s10", "cust-001", "hi"))
    rows = tool_rows("no_such_tool")
    assert [row["ok"] for row in rows] == [False] and rows[0]["agent"] == "test-agent"


def test_a_tool_error_is_one_failed_row(runtime):
    ScriptedModel.script = [call("boom"), text("Sorry.")]
    asyncio.run(runtime.chat_async("s9", "cust-001", "break"))
    assert [row["ok"] for row in tool_rows("boom")] == [False]


def test_a_tool_cannot_overwrite_the_runtimes_row_fields(app, monkeypatch):
    tdir = app / "artifacts/tool/probe"
    (tdir / "src/probe.py").write_text(
        "def forge(rt) -> str:\n"
        "    for key in ('agent', 'ok', 'tool', 'latency_ms', 'args', 'mcp', 'writes_request',\n"
        "                'cost_usd', 'runtime', 'turn_id', 'reply'):\n"
        "        rt.record(key, 'forged')\n"
        "    rt.record('tickets', 'T-1')\n"
        "    return 'x'\n"
    )
    (tdir / "tool.yaml").write_text(
        "functions:\n  - {name: forge, module: probe, parameters: {type: object}}\n"
    )
    monkeypatch.setattr(agent_module, "Memory", FakeMemory)
    monkeypatch.setattr(
        agent_module, "DatabaseSessionService", lambda **_: InMemorySessionService()
    )
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
    FakeMemory.events = []
    runtime = agent_module.Runtime(
        cfg.config(), "postgresql://u:p@db/agent", "k", model=ScriptedModel()
    )
    ScriptedModel.script = [call("forge"), text("ok")]
    result = asyncio.run(runtime.chat_async("s8", "cust-001", "hi"))
    (row,) = tool_rows("forge")
    assert row["agent"] == "test-agent" and row["ok"] is True
    assert row["tool"] == "tool/probe@1.0.0" and isinstance(row["latency_ms"], int)
    assert row["args"] == {} and row["tickets"] == ["T-1"]
    assert "mcp" not in row and "writes_request" not in row
    # nor the reply's own fields
    assert result["reply"] == "ok" and result["cost_usd"] is None
    assert result["runtime"] == agent_module.RUNTIME_REF and result["turn_id"] != ["forged"]


def make_tool(app, name, files, function_module="mod"):
    tdir = app / f"artifacts/tool/{name}"
    (tdir / "src").mkdir(parents=True)
    (tdir / "tool.yaml").write_text(
        f"functions:\n  - {{name: fn_{name.replace('-', '_')}, module: {function_module}, "
        "function: fn}\n"
    )
    for relative, body in files.items():
        path = tdir / "src" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    return {"name": name, "version": "1.0.0", "dir": f"artifacts/tool/{name}"}


@pytest.mark.parametrize(
    "files",
    [
        {"yaml.py": ""},  # a locked package (PyYAML)
        {"pydantic/__init__.py": ""},  # a package directory shadowing a locked package
        {"json.py": ""},  # the standard library
        {"sys.py": ""},  # a built-in module
        {"agent.py": ""},  # a runtime module
        {"__main__.py": ""},  # dunder names
        {"pip/__init__.py": ""},  # installed, but not in the lock
        {"google/x.py": ""},  # a namespace package
        {"yaml.cpython-312-x86_64-linux-gnu.so": ""},  # an extension module name
        {"mcp_client.py": ""},  # runtime modules
        {"redaction.py": ""},
    ],
)
def test_tool_src_that_shadows_an_installed_module_is_refused(app, files):
    entry = make_tool(app, "shady", {"mod.py": "def fn(rt):\n    return 1\n", **files})
    before = list(sys.path)
    with pytest.raises(ToolLoadError, match="shadows an installed module"):
        load_functions([entry], cfg.artifact)
    assert sys.path == before  # refused before its src/ went on sys.path


def test_a_function_module_must_be_in_the_tools_own_src(app):
    entry = make_tool(app, "lib", {"mod.py": "def fn(rt):\n    return 1\n"}, "psycopg")
    with pytest.raises(ToolLoadError, match="not in its src/"):
        load_functions([entry], cfg.artifact)


def test_a_src_that_is_a_file_is_refused(app):
    entry = make_tool(app, "flat", {})
    src = app / "artifacts/tool/flat/src"
    src.rmdir()
    src.write_text("")
    with pytest.raises(ToolLoadError, match="cannot read src/"):
        load_functions([entry], cfg.artifact)


def test_two_tools_with_the_same_module_name_are_refused(app):
    first = make_tool(app, "one", {"shared_mod.py": "def fn(rt):\n    return 1\n"}, "shared_mod")
    second = make_tool(app, "two", {"shared_mod.py": "def fn(rt):\n    return 2\n"}, "shared_mod")
    with pytest.raises(ToolLoadError, match="also in tool one"):
        load_functions([first, second], cfg.artifact)


def test_unshadowed_tool_modules_load(app):
    entry = make_tool(
        app,
        "fine",
        {
            "fine_mod_x.py": "def fn(rt):\n    return 1\n",
            "helpers_x/__init__.py": "",
            "notes.txt": "",
        },
        "fine_mod_x",
    )
    (app / "artifacts/tool/fine/src/__pycache__").mkdir()
    (spec,) = load_functions([entry], cfg.artifact)
    assert spec.name == "fn_fine"


def test_response_cost_reads_the_gateway_header_only():
    class Response:
        def __init__(self, hidden):
            self._hidden_params = hidden

    header = agent_module.COST_HEADER
    cost = agent_module.response_cost(Response({"additional_headers": {header: "0.0021"}}))
    assert cost == 0.0021
    assert agent_module.response_cost(Response({"additional_headers": {header: 0.5}})) == 0.5
    for hidden in (
        {},
        {"additional_headers": {}},
        {"additional_headers": {header: "nan"}},
        {"additional_headers": {header: "-1"}},
        {"additional_headers": {header: "inf"}},
        {"additional_headers": {header: "abc"}},
        {"additional_headers": {header: True}},
        {"additional_headers": "x"},
        {"response_cost": 3.0},  # the SDK's own price-map estimate is not the gateway's figure
        None,
    ):
        assert agent_module.response_cost(Response(hidden)) is None
    assert agent_module.response_cost(object()) is None


def model_response(cost, tool=None, content=None):
    from litellm import ModelResponse

    message = {"role": "assistant", "content": content}
    if tool:
        message["tool_calls"] = [
            {"id": "call-1", "type": "function", "function": {"name": tool, "arguments": "{}"}}
        ]
    response = ModelResponse(
        choices=[
            {"index": 0, "message": message, "finish_reason": "tool_calls" if tool else "stop"}
        ],
        usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    )
    headers = {} if cost is None else {agent_module.COST_HEADER: cost}
    response._hidden_params = {"additional_headers": headers}
    return response


@pytest.fixture
def gateway_runtime(app, monkeypatch):
    """A Runtime on ADK's real LiteLlm model and the runtime's CostClient, with LiteLLM's network
    call replaced by scripted responses."""
    from google.adk.models.lite_llm import LiteLlm, LiteLLMClient

    script = []

    async def fake_acompletion(self, model, messages, tools, **kwargs):
        return script.pop(0)

    monkeypatch.setattr(LiteLLMClient, "acompletion", fake_acompletion)
    monkeypatch.setattr(agent_module, "Memory", FakeMemory)
    monkeypatch.setattr(
        agent_module, "DatabaseSessionService", lambda **_: InMemorySessionService()
    )
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
    FakeMemory.events = []
    model = LiteLlm(model="openai/m", llm_client=agent_module.CostClient())
    runtime = agent_module.Runtime(cfg.config(), "postgresql://u:p@db/agent", "k", model=model)
    return runtime, script


def test_turn_cost_is_the_sum_of_the_gateway_costs(gateway_runtime):
    runtime, script = gateway_runtime
    script += [model_response("0.0015", tool="whoami"), model_response("0.0005", content="Done.")]
    result = asyncio.run(runtime.chat_async("c1", "cust-001", "who am I?"))
    assert result["reply"] == "Done."
    assert result["cost_usd"] == pytest.approx(0.002)
    calls = [e[5]["cost_usd"] for e in FakeMemory.events if e[4] == "model_called"]
    assert calls == [pytest.approx(0.0015), pytest.approx(0.0005)]


def test_turn_cost_is_null_when_any_call_had_no_cost(gateway_runtime):
    runtime, script = gateway_runtime
    script += [model_response("0.0015", tool="whoami"), model_response(None, content="Done.")]
    result = asyncio.run(runtime.chat_async("c2", "cust-001", "who am I?"))
    assert result["reply"] == "Done." and result["cost_usd"] is None
    calls = [e[5]["cost_usd"] for e in FakeMemory.events if e[4] == "model_called"]
    assert calls == [pytest.approx(0.0015), None]


def test_turn_cost_does_not_leak_between_turns(gateway_runtime):
    runtime, script = gateway_runtime
    script += [model_response("0.25", content="One.")]
    assert asyncio.run(runtime.chat_async("c3", "cust-001", "a"))["cost_usd"] == 0.25
    script += [model_response("0.5", content="Two.")]
    assert asyncio.run(runtime.chat_async("c3", "cust-001", "b"))["cost_usd"] == 0.5


def test_cost_is_null_without_the_gateway_client(runtime):
    ScriptedModel.script = [text("Hi.")]
    assert asyncio.run(runtime.chat_async("c4", "cust-001", "hi"))["cost_usd"] is None


def test_the_default_model_uses_the_cost_client(app, monkeypatch):
    monkeypatch.setattr(agent_module, "Memory", FakeMemory)
    monkeypatch.setattr(
        agent_module, "DatabaseSessionService", lambda **_: InMemorySessionService()
    )
    monkeypatch.setattr(agent_module, "ensure_sessions_schema", lambda url: None)
    runtime = agent_module.Runtime(cfg.config(), "postgresql://u:p@db/agent", "k")
    assert isinstance(runtime.agent.model.llm_client, agent_module.CostClient)


def test_manifest_declares_memory_schema_2():
    import yaml

    manifest = yaml.safe_load((RUNTIME.parent / "manifest.yaml").read_text())
    assert manifest["memory_schema"] == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("dep-42_a.b:c", "dep-42_a.b:c"),
        (None, None),
        ("", None),
        ("bad id", None),
        ("x" * 129, None),
    ],
)
def test_root_span_carries_the_deployment_id_when_set(runtime, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("AGENTBREEDER_DEPLOYMENT_ID", raising=False)
    else:
        monkeypatch.setenv("AGENTBREEDER_DEPLOYMENT_ID", value)
    seen = {}
    real = agent_module.span

    def recording(name, kind, input=None, **attributes):
        seen.setdefault(name, attributes)
        return real(name, kind, input=input, **attributes)

    monkeypatch.setattr(agent_module, "span", recording)
    ScriptedModel.script = [text("Hi.")]
    asyncio.run(runtime.chat_async("d1", "cust-001", "hi"))
    assert seen["agent_turn"]["agentbreeder.deployment_id"] == expected  # None: span() omits it


def test_span_omits_none_attributes():
    import telemetry
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = telemetry.tracer
    telemetry.tracer = provider.get_tracer("test")
    try:
        with telemetry.span("agent_turn", "AGENT", **{"agentbreeder.deployment_id": None}):
            pass
        with telemetry.span("agent_turn", "AGENT", **{"agentbreeder.deployment_id": "dep-1"}):
            pass
    finally:
        telemetry.tracer = tracer
    first, second = exporter.get_finished_spans()
    assert "agentbreeder.deployment_id" not in first.attributes
    assert second.attributes["agentbreeder.deployment_id"] == "dep-1"
