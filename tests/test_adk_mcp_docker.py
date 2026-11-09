"""Opt-in real Docker run (#746): an agent built from agent.yaml on runtime/adk@1.1.0 calls an MCP
tool through the built-in relay.

    AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_adk_mcp_docker.py

`services up` starts postgres, a fake OpenAI-compatible gateway (tests/fixtures/mcp/
fake_gateway.py: it asks for `mock_list_issues`, then answers with the tool's result), the mock
MCP service and its relay. `agentbreeder build` builds the agent image. The agent container runs
on the stack's agents network with the relay token, URL and database URL mounted the way deploy
mounts them, and the grant written with `mcp.write_grant`, as deploy does. The full `deploy`
path (identity gate, `_grant_mcp`) is covered by unit tests (#744) and live checks (#748).
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
from test_services_engine import FAKEGW, PY, STACK, write_project

from agentbreeder import mcp, registry
from agentbreeder.build import build
from agentbreeder.operator_config import load_operator_config
from agentbreeder.services import operator as operator_writer
from agentbreeder.services.engine import Engine
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)
CANARY = "adk-mcp-docker-canary-41c7e0d2b6"
FIXTURES = Path(__file__).parent / "fixtures" / "mcp"
INVOKE = r"""
import json, sys, time, urllib.request, urllib.error
for _ in range(120):
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2).read()
        break
    except Exception:
        time.sleep(1)
body = json.dumps({"message": "List my open issues", "session_id": "s1",
                   "customer_id": "cust-001"}).encode()
request = urllib.request.Request("http://127.0.0.1:8080/invoke", body,
                                 {"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=200) as response:
        print(json.dumps({"status": response.status, "body": json.loads(response.read())}))
except urllib.error.HTTPError as error:
    print(json.dumps({"status": error.code, "body": error.read().decode()}))
"""
AGENT_YAML = {
    "kind": "agent",
    "schema_version": 1,
    "name": "demo-agent",
    "runtime": "runtime/adk@1.1.0",
    "model": {"alias": "m1", "reasoning": "none"},
    "prompt": {"path": "prompt.md"},
    "mcp": [{"ref": "mcp/mock@1.0.0", "tools": ["list_issues"]}],
    "agent_events": "none",
    "traces": "none",
}


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=900)
    if check and result.returncode:
        raise AssertionError(f"docker {args[0]} failed: {result.stderr[-800:]}")
    return result.stdout


def logs(container: str) -> str:
    """A container's whole log: `docker logs` replays its stderr on stderr, not stdout."""
    result = subprocess.run(
        ["docker", "logs", container], capture_output=True, text=True, timeout=300
    )
    if result.returncode:
        raise AssertionError(f"docker logs failed: {result.stderr[-500:]}")
    return result.stdout + result.stderr


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("MOCK_MCP_TOKEN", CANARY)
    data = json.loads(json.dumps(STACK))
    del data["services"]["echo"]
    data["services"]["postgres"] = {
        "ref": "service/postgres-pgvector@1.1.0",
        "with": {"databases": ["agent"]},
    }
    data["services"]["browser"] = {"ref": "service/mock-mcp@1.0.0"}
    data["mcp"] = {"mock": {"ref": "mcp/mock@1.0.0"}}
    env = data["environment"]
    env.pop("services", None)
    env.pop("eval_database", None)
    env["bindings"] = {"DATABASE_URL": {"ref": "postgres.databases.agent"}}
    env["agents"]["demo-agent"]["mcp"] = {"mock": ["list_issues"]}
    gateway = {**FAKEGW, "command": ["python3", "/files/gw.py"]}
    root = write_project(tmp_path / "project", stack=data, gateway=gateway)
    shutil.copyfile(
        FIXTURES / "fake_gateway.py", root / "registry/service/fakegw/1.0.0/files/gw.py"
    )
    service = root / "registry" / "service" / "mock-mcp" / "1.0.0"
    (service / "files").mkdir(parents=True)
    (service / "manifest.yaml").write_text("kind: service\nname: mock-mcp\nversion: 1.0.0\n")
    shutil.copyfile(FIXTURES / "mock_server.py", service / "files" / "server.py")
    (service / "service.yaml").write_text(
        yaml.safe_dump(
            {
                "image": PY,
                "command": ["python3", "/files/server.py"],
                "user": "65532:65532",
                "ports": {"http": {"port": 9000}},
                "secrets": {"token": {"import": "MOCK_MCP_TOKEN"}},
                "health": {"http": "/healthz"},
            }
        )
    )
    version = root / "registry" / "mcp" / "mock" / "1.0.0"
    version.mkdir(parents=True)
    (version / "manifest.yaml").write_text("kind: mcp\nname: mock\nversion: 1.0.0\n")
    (version / "mcp.yaml").write_text(
        yaml.safe_dump(
            {
                "upstream": {"service": {"ref": "service/mock-mcp@1.0.0", "port": "http"}},
                "auth": {"type": "bearer", "secret": "token"},
                "secrets": {"token": {"import": "MOCK_MCP_TOKEN"}},
                "tools": {"list_issues": {"effect": "read"}, "delete_repo": {"effect": "write"}},
            }
        )
    )
    (root / "agent.yaml").write_text(yaml.safe_dump(AGENT_YAML))
    (root / "prompt.md").write_text("You help with issues. Use the tools you are given.\n")
    engine = Engine(root)
    yield engine
    try:
        engine.active_deployments = lambda: []
        engine.down(purge=True)
    finally:
        engine.close()


def test_built_agent_calls_an_mcp_tool_through_the_relay(project, tmp_path):
    engine = project
    result = engine.up("dev", confirm=lambda grant: True, confirm_pins=lambda report: True)
    assert result["mcp"]["mock"]["state"] == "ok"
    operator_path = Path(result["operator_config"])
    operator = yaml.safe_load(operator_path.read_text())
    gateway_url = operator["gateways"]["gateway"]["agent_url"]
    relay_url = operator["environments"]["dev"]["mcp_relays"]["mock"]["url"]
    # The agents-network URL `up` stored as the deployment secret for the DATABASE_URL binding.
    secret_store = engine.state.deploy_state("dev") / "secrets"
    database_url = (secret_store / operator_writer.binding_secret("DATABASE_URL")).read_text()

    built = build(engine.project / "agent.yaml", load_operator_config(operator_path), "dev")

    # The grant and the token file, as deploy writes them.
    token = mcp.mint_token()
    grants = operator_writer.grants_dir(engine.state, "dev", "mock")
    mcp.write_grant(grants, token, {"deployment": uuid.uuid4().hex, "agent": "demo-agent",
                                    "environment": "dev", "server": "mock",
                                    "tools": ["list_issues"]})  # fmt: skip
    secrets = tmp_path / "agent-secrets"
    secrets.mkdir()
    for name, value in {"DATABASE_URL": database_url.strip(), "LLM_GATEWAY_KEY": "fake-gw-key",
                        "mcp-mock-token": token}.items():  # fmt: skip
        (secrets / name).write_text(value + "\n")
        os.chmod(secrets / name, 0o444)
    os.chmod(secrets, 0o755)

    name = f"ab-adk-mcp-{uuid.uuid4().hex[:8]}"
    network = f"{engine.state.project}-agents"
    docker("run", "-d", "--name", name, "--network", network,
           "-v", f"{secrets}:/run/secrets:ro",
           "-e", "DATABASE_URL_FILE=/run/secrets/DATABASE_URL",
           "-e", "LLM_GATEWAY_KEY_FILE=/run/secrets/LLM_GATEWAY_KEY",
           "-e", f"AGENTBREEDER_GATEWAY_URL={gateway_url}",
           "-e", f"AGENTBREEDER_MCP_MOCK_URL={relay_url}",
           "-e", "AGENTBREEDER_MCP_MOCK_TOKEN_FILE=/run/secrets/mcp-mock-token",
           built.image)  # fmt: skip
    try:
        out = json.loads(docker("exec", name, "python", "-c", INVOKE).strip().splitlines()[-1])
        assert out["status"] == 200, out
        reply = out["body"]
        assert reply["runtime"] == "runtime/adk@1.1.0"
        assert reply["tools"] == ["mock_list_issues"]
        assert "ran list_issues" in reply["reply"]
        assert reply["mcp_unavailable"] == []

        # Upstream saw exactly one call, with the relay's credential.
        calls = json.loads(
            docker("exec", f"{engine.state.project}-browser-1", "cat", "/tmp/calls.json")
        )
        assert calls["calls"] == {"list_issues": 1} and calls["unauthorized"] == 0
        # The model was offered only the granted tool.
        requests = [
            json.loads(line)
            for line in docker(
                "exec", f"{engine.state.project}-gateway-1", "cat", "/tmp/requests.jsonl"
            ).splitlines()
        ]
        assert "mock_list_issues" in requests[0]["tools"]
        assert "mock_delete_repo" not in requests[0]["tools"]

        # The upstream credential never reaches the agent; the agent token never the relay log.
        assert CANARY not in docker("inspect", name) + logs(name)
        relay = f"{engine.state.project}-mcp-mock-1"
        relay_log = logs(relay)
        assert token not in relay_log
        decisions = [json.loads(line) for line in relay_log.splitlines()
                     if line.startswith("{")]  # fmt: skip
        assert any(d.get("tool") == "list_issues" and d.get("decision") == "allow"
                   for d in decisions)  # fmt: skip
    finally:
        docker("rm", "-f", name, check=False)
        docker("image", "rm", "-f", built.image, check=False)
