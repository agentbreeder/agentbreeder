"""Opt-in real Docker run of the built-in MCP relay (#745), acceptance criteria 10 and 11 against
a mock MCP service upstream on its private network:

    AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_mcp_relay_docker.py

An agent-side client runs in a one-shot container on the stack's agents network. Live checks of
real built-ins with owner credentials are #748; the ADK client is #746.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from test_services_engine import PY, STACK, write_project

from agentbreeder import mcp, registry
from agentbreeder.services import operator as operator_writer
from agentbreeder.services.engine import Engine
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)
CANARY = "mcp-docker-canary-6f1d0b2e9a"
FIXTURE = Path(__file__).parent / "fixtures" / "mcp" / "mock_server.py"
CLIENT = r"""
import json, sys, urllib.request, urllib.error
url, token, other = sys.argv[1], sys.argv[2], sys.argv[3]

def post(body, token=token, session=None):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": "Bearer " + token}
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(url, json.dumps(body).encode(), headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode()

out = {}
status, headers, body = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "t", "version": "1"}}})
session = headers.get("Mcp-Session-Id")
out["init"] = [status, json.loads(body)["result"]["capabilities"]]
out["initialized"] = post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                         session=session)[0]
status, _, body = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session=session)
out["tools"] = [t["name"] for t in json.loads(body)["result"]["tools"]]
out["call"] = post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
    "params": {"name": "list_issues", "arguments": {}}}, session=session)[2]
out["ungranted"] = post({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
    "params": {"name": "delete_repo", "arguments": {}}}, session=session)[2]
out["other_token"] = post({"jsonrpc": "2.0", "id": 5, "method": "ping"}, token=other)[0]
out["resources"] = post({"jsonrpc": "2.0", "id": 6, "method": "resources/list"},
                       session=session)[0]
out["session"] = session
print(json.dumps(out))
"""
PING = r"""
import json, sys, urllib.request, urllib.error
url, token, session = sys.argv[1:4]
body = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}).encode()
request = urllib.request.Request(url, body, {"Content-Type": "application/json",
    "Authorization": "Bearer " + token, "Mcp-Session-Id": session})
try:
    print(urllib.request.urlopen(request, timeout=30).status)
except urllib.error.HTTPError as error:
    print(error.code)
"""


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300)
    if check and result.returncode:
        raise AssertionError(f"docker {args[0]} failed: {result.stderr[-500:]}")
    return result.stdout


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", "echo-canary")
    monkeypatch.setenv("MOCK_MCP_TOKEN", CANARY)
    data = json.loads(json.dumps(STACK))
    data["services"]["browser"] = {"ref": "service/mock-mcp@1.0.0"}
    data["mcp"] = {"mock": {"ref": "mcp/mock@1.0.0"}}
    data["environment"]["agents"]["demo-agent"]["mcp"] = {"mock": ["list_issues"]}
    root = write_project(tmp_path / "project", stack=data)
    service = root / "registry" / "service" / "mock-mcp" / "1.0.0"
    (service / "files").mkdir(parents=True)
    (service / "manifest.yaml").write_text("kind: service\nname: mock-mcp\nversion: 1.0.0\n")
    shutil.copyfile(FIXTURE, service / "files" / "server.py")
    (service / "service.yaml").write_text(
        yaml.safe_dump(
            {
                "image": PY,
                "command": ["python3", "/files/server.py"],
                "user": "65532:65532",
                "ports": {"http": {"port": 9000}},
                # The relay's credential, so the mock can say whether requests carried it.
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
    engine = Engine(root)
    yield engine
    try:
        engine.active_deployments = lambda: []
        engine.down(purge=True)
    finally:
        engine.close()


def run_client(engine, script, *args) -> str:
    network = f"{engine.state.project}-agents"
    return docker("run", "--rm", "--network", network, PY, "python3", "-c", script, *args).strip()


def mock_state(engine) -> dict:
    return json.loads(
        docker("exec", f"{engine.state.project}-browser-1", "cat", "/tmp/calls.json")
    )


def test_relay_end_to_end(project):
    engine = project
    result = engine.up("dev", confirm=lambda grant: True, confirm_pins=lambda report: True)
    assert result["services"]["mcp-mock"] == "created"
    assert result["mcp"]["mock"]["state"] == "ok"
    assert result["mcp"]["mock"]["pinned"] == ["delete_repo", "list_issues"]

    grants = operator_writer.grants_dir(engine.state, "dev", "mock")
    token, other = mcp.mint_token(), mcp.mint_token()
    grant = {
        "deployment": "a" * 32,
        "agent": "demo-agent",
        "environment": "dev",
        "server": "mock",
        "tools": ["list_issues", "delete_repo"],
    }
    mcp.write_grant(grants, token, grant)
    url = "http://mcp-mock:8080/mcp"
    out = json.loads(run_client(engine, CLIENT, url, token, other))
    assert out["init"] == [200, {"tools": {}}]
    assert out["initialized"] == 202
    # The file grants delete_repo, but the stack grant doesn't: hidden and refused.
    assert out["tools"] == ["list_issues"]
    assert "ran list_issues" in out["call"]
    assert "sampling" not in out["call"] and "list_changed" not in out["call"]
    assert "notifications/progress" in out["call"]
    assert "TOOL_NOT_GRANTED" in out["ungranted"]
    assert out["other_token"] == 401
    assert out["resources"] == 403
    state = mock_state(engine)
    assert state["calls"] == {"list_issues": 1}  # the ungranted call never reached upstream
    assert state["unauthorized"] == 0 and state["authorized"] >= 4

    # The canary is only in the relay's 0400 mount: not in inspect, logs or generated files.
    relay = f"{engine.state.project}-mcp-mock-1"
    assert CANARY not in docker("inspect", relay)
    assert CANARY not in docker("logs", relay) + docker("logs", relay, check=False)
    assert token not in docker("logs", relay)
    assert CANARY not in engine.state.compose_path.read_text()
    networks = json.loads(
        docker(
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            f"{engine.state.project}-browser-1",
        )
    )
    assert list(networks) == [f"{engine.state.project}-mcp-mock"]

    # Removing the grant revokes the token at once.
    mcp.revoke(grants, deployment="a" * 32)
    assert run_client(engine, PING, url, token, out["session"]) == "401"

    # Restart mid-run: the old session is unknown (404); initialize again recovers.
    mcp.write_grant(grants, token, grant)
    docker("restart", relay)
    for _ in range(30):
        health = docker("inspect", "--format", "{{.State.Running}}", relay).strip()
        if health == "true":
            break
        time.sleep(1)
    time.sleep(2)
    assert run_client(engine, PING, url, token, out["session"]) == "404"
    again = json.loads(run_client(engine, CLIENT, url, token, other))
    assert again["tools"] == ["list_issues"]

    status = engine.status()
    assert status["mcp"]["mock"]["state"] == "ok"
    # A second up recreates nothing (the test's grant goes first: it names delete_repo, which a
    # real deploy can't be granted, so up would refuse it as a grant in use).
    mcp.revoke(grants)
    assert set(engine.up("dev", confirm=lambda grant: True)["services"].values()) == {"unchanged"}

    # Dropping the mcp: entry removes its relay, credential and all.
    data = yaml.safe_load((engine.project / "stack.yaml").read_text())
    del data["mcp"]
    data["environment"]["agents"]["demo-agent"].pop("mcp")
    (engine.project / "stack.yaml").write_text(yaml.safe_dump(data))
    dropped = Engine(engine.project)
    try:
        result = dropped.up("dev", confirm=lambda grant: True)
    finally:
        dropped.close()
    assert result["removed"] == ["mcp-mock"]
    assert docker("ps", "--all", "--quiet", "--filter", f"name=^{relay}$").strip() == ""
