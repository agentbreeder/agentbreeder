"""runtime/adk@1.1.0's MCP client (#746) against the real built-in relay, in process.

The ADK McpToolset talks Streamable HTTP to service/mcp-relay@1.0.1's relay.py, which talks to a
mock upstream (relay_harness.py). The model is scripted. Run with the runtime's requirements:
    python -m pytest src/agentbreeder/builtin_registry/runtime/adk/1.1.0/runtime_tests -q
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

import agent as agent_module  # noqa: E402
import config as cfg  # noqa: E402
import mcp_client  # noqa: E402
from google.adk.sessions import InMemorySessionService  # noqa: E402
from relay_harness import CANARY, TOKEN, Relay, Upstream  # noqa: E402
from test_runtime import FakeMemory, ScriptedModel, app, call, text  # noqa: E402,F401

LOOP = asyncio.new_event_loop()  # one loop for every turn, as agent.LOOP in production


def turn(runtime, message="list my issues", session="s1"):
    return LOOP.run_until_complete(runtime.chat_async(session, "cust-001", message))


@pytest.fixture
def upstream():
    mock = Upstream()
    yield mock
    mock.stop()


@pytest.fixture
def relay(tmp_path, upstream):
    server = Relay(tmp_path / "relay", upstream)
    yield server
    server.stop()


def use_mcp(app, monkeypatch, relay, entries=None, token=TOKEN):  # noqa: F811
    """agent.config.json with an mcp: section, and the env and token file deploy provides."""
    config = json.loads((app / "agent.config.json").read_text())
    config["mcp"] = entries or [
        {"name": "github", "version": "1.0.0", "tools": ["list_issues", "create_issue"]}
    ]
    (app / "agent.config.json").write_text(json.dumps(config))
    cfg.config.cache_clear()
    for entry in config["mcp"]:
        name = entry["name"].upper().replace("-", "_")
        secret = app / f"mcp-{entry['name']}-token"
        secret.write_text(token + "\n")
        monkeypatch.setenv(f"AGENTBREEDER_MCP_{name}_TOKEN_FILE", str(secret))
        monkeypatch.setenv(f"AGENTBREEDER_MCP_{name}_URL", relay.url)


def build(monkeypatch):
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


@pytest.fixture
def runtime(app, monkeypatch, relay):  # noqa: F811
    use_mcp(app, monkeypatch, relay)
    rt = build(monkeypatch)
    yield rt
    LOOP.run_until_complete(rt.close_mcp(5))


def declared(request) -> list[str]:
    return sorted(d.name for t in request.config.tools or [] for d in t.function_declarations)


def events(action):
    return [e[5] for e in FakeMemory.events if e[4] == action]


def test_granted_call_goes_through_the_relay_with_its_credential(runtime, upstream, relay):
    ScriptedModel.script = [call("github_list_issues", q="open"), text("Two issues.")]
    result = turn(runtime)
    assert result["reply"] == "Two issues."
    assert result["tools"] == ["github_list_issues"]
    assert upstream.calls == {"list_issues": [{"q": "open"}]}
    assert upstream.authorized and all(upstream.authorized)  # the relay's credential, always
    response = ScriptedModel.requests[1].contents[-1].parts[0].function_response.response
    assert "ran list_issues" in json.dumps(response)
    detail = events("tool:github_list_issues")[0]
    assert detail["ok"] is True and detail["error"] is None
    assert detail["mcp"] == "mcp/github@1.0.0"


def test_only_granted_tools_are_declared(app, monkeypatch, relay):  # noqa: F811
    # agent.yaml asks for list_issues only; the relay would also grant create_issue.
    use_mcp(app, monkeypatch, relay,
            [{"name": "github", "version": "1.0.0", "tools": ["list_issues"]}])  # fmt: skip
    rt = build(monkeypatch)
    ScriptedModel.script = [text("ok")]
    turn(rt)
    LOOP.run_until_complete(rt.close_mcp(5))
    assert "github_list_issues" in declared(ScriptedModel.requests[0])
    assert "github_create_issue" not in declared(ScriptedModel.requests[0])


def test_a_narrowed_grant_hides_the_tool_and_a_refused_call_is_audited_as_failed(
    runtime, upstream, relay
):
    relay.grant(["list_issues"])  # the relay re-reads the grant file on every request
    ScriptedModel.script = [text("ok")]
    turn(runtime)
    mcp_names = [n for n in declared(ScriptedModel.requests[0]) if n.startswith("github_")]
    assert mcp_names == ["github_list_issues"]
    # A call the relay refuses comes back to the model as an error, and the audit says so.
    relay.grant([])
    ScriptedModel.script = [call("github_list_issues"), text("Sorry.")]
    ScriptedModel.requests = []
    result = turn(runtime, session="s2")
    assert result["reply"] == "Sorry." and upstream.calls == {}
    detail = events("tool:github_list_issues")[-1]
    assert detail["ok"] is False and detail["error"]


def test_an_is_error_result_is_audited_as_failed(runtime, upstream):
    upstream.is_error = True
    ScriptedModel.script = [call("github_list_issues"), text("Sorry.")]
    turn(runtime)
    detail = events("tool:github_list_issues")[-1]
    assert detail["ok"] is False and "ran list_issues" in detail["error"]


def test_relay_restart_costs_one_failed_call_then_recovers(runtime, upstream, relay):
    ScriptedModel.script = [call("github_list_issues"), text("ok")]
    turn(runtime)
    relay.restart()  # the relay's sessions are gone: the next call gets 404
    ScriptedModel.script = [call("github_list_issues"), text("ok")]
    turn(runtime, session="s2")
    ScriptedModel.script = [call("github_list_issues"), text("ok")]
    turn(runtime, session="s3")
    outcomes = [d["ok"] for d in events("tool:github_list_issues")]
    assert outcomes == [True, False, True]
    assert len(upstream.calls["list_issues"]) == 2


def test_relay_down_runs_the_turn_without_its_tools_and_says_so(runtime, relay, capfd):
    relay.stop()
    ScriptedModel.script = [text("I can't reach GitHub right now.")]
    result = turn(runtime)
    assert result["reply"].startswith("I can't")
    assert "github_list_issues" not in declared(ScriptedModel.requests[0])
    assert result["mcp_unavailable"] == ["github"]
    assert events("mcp_unavailable") == [{"server": "github", "mcp": "mcp/github@1.0.0"}]
    err = capfd.readouterr().err
    assert "Traceback" not in err
    relay.start()  # so the fixture's close can reach it


def test_the_token_never_leaves_the_runtime(runtime, upstream, relay, capfd):
    relay.grant([])
    ScriptedModel.script = [call("github_list_issues"), text("ok")]
    result = turn(runtime)
    captured = capfd.readouterr()
    for place in (json.dumps(result), json.dumps(FakeMemory.events, default=str),
                  captured.out, captured.err, relay.log.getvalue()):  # fmt: skip
        assert TOKEN not in place
        assert CANARY not in place
    assert agent_module.redact(f"boom {TOKEN} boom") == "boom [redacted] boom"


def test_closing_the_runtime_ends_the_relay_session(runtime, upstream, relay):
    ScriptedModel.script = [call("github_list_issues"), text("ok")]
    turn(runtime)
    assert relay.relay.sessions
    LOOP.run_until_complete(runtime.close_mcp(5))
    deadline = time.monotonic() + 5
    while relay.relay.sessions and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not relay.relay.sessions  # DELETE reached the relay, which closed it upstream too


@pytest.mark.parametrize(
    "change, message",
    [
        ("no_url", "AGENTBREEDER_MCP_GITHUB_URL"),
        ("bad_url", "AGENTBREEDER_MCP_GITHUB_URL"),
        ("bad_token", "must be 64 lowercase hex"),
        ("reserved", "reserved"),
        ("clash_function", "whoami"),
        ("clash_servers", "a_b_c"),
    ],
)
def test_startup_refusals(app, monkeypatch, relay, change, message):  # noqa: F811
    entries = [{"name": "github", "version": "1.0.0", "tools": ["list_issues"]}]
    token = TOKEN
    if change == "reserved":
        entries[0]["tools"] = ["transfer_to_agent"]
    if change == "clash_function":
        # A tool module defines `whoami`; a server named `who` with tool `ami` would clash.
        entries = [{"name": "who", "version": "1.0.0", "tools": ["ami"]}]
    if change == "clash_servers":
        entries = [{"name": "a", "version": "1.0.0", "tools": ["b_c"]},
                   {"name": "a_b", "version": "1.0.0", "tools": ["c"]}]  # fmt: skip
    if change == "bad_token":
        token = "not-a-token-" + "x" * 52
    use_mcp(app, monkeypatch, relay, entries, token)
    if change == "no_url":
        monkeypatch.delenv("AGENTBREEDER_MCP_GITHUB_URL")
    if change == "bad_url":
        monkeypatch.setenv("AGENTBREEDER_MCP_GITHUB_URL", "http://user:pw@relay:8080/mcp")
    if change == "clash_function":
        # the function tool is named whoami: rename so the clash is with the prefixed MCP name
        tool_yaml = app / "artifacts/tool/probe/tool.yaml"
        tool_yaml.write_text(tool_yaml.read_text().replace("name: whoami", "name: who_ami"))
        src = app / "artifacts/tool/probe/src/probe.py"
        src.write_text(src.read_text().replace("def whoami", "def who_ami"))
        message = "who_ami"
    with pytest.raises(cfg.ConfigError, match=message) as error:
        build(monkeypatch)
    assert token not in str(error.value)


def test_relay_logs_never_hold_the_agent_token(runtime, relay):
    ScriptedModel.script = [call("github_list_issues"), text("ok")]
    turn(runtime)
    assert TOKEN not in relay.log.getvalue()
    assert mcp_client.redaction_values() >= {TOKEN}


def test_sigterm_closes_relay_sessions_then_stops_serving(runtime, relay, monkeypatch):
    import server as server_module

    ScriptedModel.script = [call("github_list_issues"), text("ok")]
    runtime_loop_turn = asyncio.run_coroutine_threadsafe(
        runtime.chat_async("s9", "cust-001", "list"), agent_module.LOOP
    )
    runtime_loop_turn.result(30)
    assert relay.relay.sessions
    monkeypatch.setattr(agent_module, "_RUNTIME", runtime)

    class Server:
        stopped = False

        def shutdown(self):
            Server.stopped = True

    server_module.stop(Server())
    assert Server.stopped
    deadline = time.monotonic() + 5
    while relay.relay.sessions and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not relay.relay.sessions


def test_failed_spans_never_export_a_secret():
    """Exception text in a failed span (status and events) is redacted like stored text."""
    import telemetry
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    secret = "e" * 64
    mcp_client.remember_secret(secret)
    original = telemetry.tracer
    telemetry.tracer = provider.get_tracer("test")
    try:
        with pytest.raises(RuntimeError):
            with telemetry.span("turn", "AGENT"):
                raise RuntimeError(f"relay said Bearer {secret} was bad: {secret}")
    finally:
        telemetry.tracer = original
    (finished,) = exporter.get_finished_spans()
    exported = json.dumps(
        [finished.status.description, [dict(e.attributes) for e in finished.events]]
    )
    assert secret not in exported and "[redacted]" in exported
    assert "RuntimeError" in exported
