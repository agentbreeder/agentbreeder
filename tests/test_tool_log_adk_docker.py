"""Opt-in real Docker run (#828 M12 §4): an agent built on runtime/adk@1.2.0 calls a registry tool
twice (one success, one failure) against service/postgres-pgvector@1.2.0, and `tool log` lists
exactly those two calls: one row per call, the failure included once (#922 fixed in 1.2.0), with
the agent, the version, the latency and the trace-less turn.

    AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_tool_log_adk_docker.py

The model is the fake OpenAI-compatible gateway of tests/fixtures/mcp (scripted tool calls, no
provider keys). The agent container is run the way tests/test_adk_mcp_docker.py runs it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
import yaml
from test_services_engine import FAKEGW, STACK, write_project

from agentbreeder import cli, registry
from agentbreeder.build import build
from agentbreeder.operator_config import load_operator_config
from agentbreeder.services import operator as operator_writer
from agentbreeder.services.engine import Engine
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)
FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
INVOKE = r"""
import json, time, urllib.request, urllib.error
for _ in range(120):
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2).read()
        break
    except Exception:
        time.sleep(1)
body = json.dumps({"message": "Check both", "session_id": "s1",
                   "customer_id": "cust-001"}).encode()
request = urllib.request.Request("http://127.0.0.1:8080/invoke", body,
                                 {"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=200) as response:
        print(json.dumps({"status": response.status, "body": json.loads(response.read())}))
except urllib.error.HTTPError as error:
    print(json.dumps({"status": error.code, "body": error.read().decode()}))
"""
TOOL_YAML = {
    "functions": [
        {
            "name": "probe_ok",
            "module": "probe",
            "description": "Always works.",
            "parameters": {"type": "object", "properties": {"label": {"type": "string"}}},
        },
        {
            "name": "probe_boom",
            "module": "probe",
            "description": "Always fails.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]
}
TOOL_SRC = """
def probe_ok(rt, label: str = "") -> str:
    return f"fine {label}"


def probe_boom(rt) -> str:
    raise ValueError("probe exploded")
"""
AGENT_YAML = {
    "kind": "agent",
    "schema_version": 1,
    "name": "demo-agent",
    "runtime": "runtime/adk@1.2.0",
    "model": {"alias": "m1", "reasoning": "none", "max_model_calls": 4},
    "prompt": {"path": "prompt.md"},
    "tools": [{"ref": "tool/probe@1.0.0"}],
    "agent_events": "postgres",
    "traces": "none",
}


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=900)
    if check and result.returncode:
        raise AssertionError(f"docker {args[0]} failed: {result.stderr[-800:]}")
    return result.stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    data = json.loads(json.dumps(STACK))
    del data["services"]["echo"]
    data["services"]["postgres"] = {
        "ref": "service/postgres-pgvector@1.2.0",
        "with": {"databases": ["agent"]},
    }
    env = data["environment"]
    env.pop("services", None)
    env.pop("eval_database", None)
    env["bindings"] = {"DATABASE_URL": {"ref": "postgres.databases.agent"}}
    gateway = {**FAKEGW, "command": ["python3", "/files/gw.py"]}
    root = write_project(tmp_path / "project", stack=data, gateway=gateway)
    files = root / "registry/service/fakegw/1.0.0/files"
    shutil.copyfile(FIXTURES / "fake_gateway.py", files / "gw.py")
    (files / "script.json").write_text(
        json.dumps([{"tool": "probe_ok", "arguments": {"label": "a"}}, {"tool": "probe_boom"}])
    )
    tool = root / "registry" / "tool" / "probe" / "1.0.0"
    (tool / "src").mkdir(parents=True)
    (tool / "manifest.yaml").write_text("kind: tool\nname: probe\nversion: 1.0.0\n")
    (tool / "tool.yaml").write_text(yaml.safe_dump(TOOL_YAML, sort_keys=False))
    (tool / "src" / "probe.py").write_text(TOOL_SRC)
    (root / "agent.yaml").write_text(yaml.safe_dump(AGENT_YAML))
    (root / "prompt.md").write_text("You check things. Use the tools you are given.\n")
    engine = Engine(root)
    yield engine
    try:
        engine.active_deployments = lambda: []
        engine.down(purge=True)
    finally:
        engine.close()


def test_runtime_rows_reach_tool_log(project, tmp_path, capsys):
    engine = project
    result = engine.up("dev", confirm=lambda grant: True, confirm_pins=lambda report: True)
    operator_path = Path(result["operator_config"])
    operator = yaml.safe_load(operator_path.read_text())
    gateway_url = operator["gateways"]["gateway"]["agent_url"]
    secret_store = engine.state.deploy_state("dev") / "secrets"
    database_url = (secret_store / operator_writer.binding_secret("DATABASE_URL")).read_text()
    built = build(engine.project / "agent.yaml", load_operator_config(operator_path), "dev")
    secrets = tmp_path / "agent-secrets"
    secrets.mkdir()
    for name, value in {"DATABASE_URL": database_url.strip(), "LLM_GATEWAY_KEY": "fake"}.items():
        (secrets / name).write_text(value + "\n")
        os.chmod(secrets / name, 0o444)
    os.chmod(secrets, 0o755)
    name = f"ab-tool-log-{uuid.uuid4().hex[:8]}"
    network = f"{engine.state.project}-agents"
    docker("run", "-d", "--name", name, "--network", network,
           "-v", f"{secrets}:/run/secrets:ro",
           "-e", "DATABASE_URL_FILE=/run/secrets/DATABASE_URL",
           "-e", "LLM_GATEWAY_KEY_FILE=/run/secrets/LLM_GATEWAY_KEY",
           "-e", f"AGENTBREEDER_GATEWAY_URL={gateway_url}",
           built.image)  # fmt: skip
    try:
        out = json.loads(docker("exec", name, "python", "-c", INVOKE).strip().splitlines()[-1])
        assert out["status"] == 200, out
        reply = out["body"]
        assert reply["runtime"] == "runtime/adk@1.2.0"
        assert reply["tools"] == ["probe_ok", "probe_boom"], reply
        turn = reply["turn_id"] if "turn_id" in reply else None
    finally:
        docker("rm", "-f", name, check=False)
        docker("image", "rm", "-f", built.image, check=False)

    code = cli.main(
        ["tool", "log", "tool/probe", "--project", str(engine.project), "--json", "--content"]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    body = json.loads(captured.out)
    assert body["credential"] == "agent" and body["phantom_rows"] == 0
    calls = body["calls"]
    assert [(c["function"], c["status"]) for c in calls] == [
        ("probe_boom", "error"),
        ("probe_ok", "ok"),
    ]
    for call in calls:
        assert call["agent"] == "demo-agent" and call["tool"] == "tool/probe@1.0.0"
        assert isinstance(call["latency_ms"], int) and call["turn_id"]
        if turn:
            assert call["turn_id"] == turn
    assert calls[1]["args"] == {"label": "a"}
    assert "probe exploded" in calls[0]["error"]
    assert body["notes"] == [
        "Read with the agent's own credential (the agent owns these tables)",
        "db_role is not verified: no owner trigger in force now",
    ]
