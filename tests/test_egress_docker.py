"""Real Docker phase-4 boundary test, enabled explicitly (AGENTBREEDER_DOCKER_TEST=1).

Deploys the sample agent behind its egress boundary and drives MCP calls from inside the agent
container: allowed, denied, header-spoofed, held then released once, and direct-egress bypass
attempts. Mock tests are not boundary evidence.
"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from conftest import actor, v2_manifest_data
from egress_support import Upstream, enable_boundary, tool_capabilities

from agentbreeder.deployment import DeploymentService
from agentbreeder.manifest import Manifest
from agentbreeder.runtime import Docker
from agentbreeder.store import Store

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)

CALLER = r"""
import json, socket, sys, urllib.request, urllib.error
gateway = "http://mcp-gateway.internal:8080/mcp/tools"
def call(tool, args, name=None, auth=None, ident=1):
    body = json.dumps({"jsonrpc": "2.0", "id": ident, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    headers = {"Content-Type": "application/json", "Mcp-Method": "tools/call",
               "Mcp-Name": name or tool}
    if auth:
        headers["Authorization"] = auth
    request = urllib.request.Request(gateway, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as r:
            return [r.status, json.loads(r.read())]
    except urllib.error.HTTPError as e:
        return [e.code, json.loads(e.read())]
def reach(host, port):
    try:
        socket.create_connection((host, port), timeout=3).close()
        return True
    except OSError:
        return False
def resolves(name):
    try:
        socket.getaddrinfo(name, 443)
        return True
    except OSError:
        return False
plan = json.loads(sys.argv[1])
out = {}
for key, step in plan.items():
    kind = step.pop("kind")
    out[key] = call(**step) if kind == "call" else (
        reach(step["host"], step["port"]) if kind == "reach" else resolves(step["name"]))
print(json.dumps(out))
"""


def _exec(runtime, container, plan) -> dict:
    output = runtime.command(
        "container", "exec", container, "python", "-c", CALLER, json.dumps(plan), timeout=120
    )
    return json.loads(output.splitlines()[-1])


def test_real_boundary_end_to_end(tmp_path, identity_env, monkeypatch):
    runtime = Docker()
    runtime.authorize()
    upstream = Upstream(tmp_path)
    url = f"https://host.docker.internal:{upstream.port}/mcp"
    enable_boundary(identity_env, url, upstream.pem, monkeypatch)
    operator = identity_env.load()
    store = Store(tmp_path / "state")
    service = DeploymentService(store, runtime, operator)
    image_file = tmp_path / "image-id"
    image = result = None
    try:
        subprocess.run(
            ["docker", "--context", runtime.context, "build", "--iidfile", str(image_file),
             str(Path(__file__).parents[1] / "examples/http-agent")],
            check=True, timeout=180, capture_output=True,
        )  # fmt: skip
        image = image_file.read_text().strip()
        manifest = Manifest.model_validate(
            v2_manifest_data(
                name="pep-" + uuid.uuid4().hex[:10],
                image=image,
                capabilities=tool_capabilities(url),
            )
        )
        started = time.monotonic()
        result = service.deploy(manifest, local=True, deployer=actor("dave"))
        assert result["state"] == "ready", result
        assert "runtime_boundary" in result["gate"]["checks_evaluated"]
        assert "runtime_tool_enforcement" not in result["gate"]["checks_unavailable"]
        print(f"boundary deploy+verify took {time.monotonic() - started:.1f}s")

        # Ingress reaches the agent through the sidecar's loopback binding only.
        with urlopen(
            Request(result["endpoint"] + "/invoke", data=b'{"message":"hi"}'), timeout=5
        ) as response:
            assert json.load(response)["input"]["message"] == "hi"

        record = store.get(result["id"])
        agent = record["container"]
        observed = _exec(
            runtime,
            agent,
            {
                "allowed": {"kind": "call", "tool": "lookup", "args": {"q": 1}},
                "spoofed_auth": {
                    "kind": "call", "tool": "lookup", "args": {"q": 2}, "auth": "Bearer evil",
                },
                "denied": {"kind": "call", "tool": "delete", "args": {}},
                "mismatch": {"kind": "call", "tool": "lookup", "args": {}, "name": "delete"},
                "held": {"kind": "call", "tool": "refund", "args": {"amount": 5}},
                "public_ip": {"kind": "reach", "host": "1.1.1.1", "port": 443},
                "public_dns": {"kind": "resolve", "name": "example.com"},
                "host_dns": {"kind": "resolve", "name": "host.docker.internal"},
            },
        )  # fmt: skip
        assert observed["allowed"][0] == 200, observed["allowed"]  # reply body on failure
        assert observed["allowed"][1]["result"] == {"authorized": True, "tool": "lookup"}
        assert observed["spoofed_auth"][1]["result"]["authorized"] is True
        assert all("evil" not in json.dumps(r["headers"]) for r in upstream.requests)
        assert observed["denied"][0] == 403
        assert observed["denied"][1]["error"]["data"]["reason"] == "POLICY_DENIED"
        assert observed["mismatch"][0] == 400
        assert observed["held"][1]["error"]["code"] == -32002
        assert observed["public_ip"] is False
        assert observed["public_dns"] is False and observed["host_dns"] is False
        tools_seen = [r["body"]["params"]["name"] for r in upstream.requests]
        assert tools_seen == ["lookup", "lookup"]  # denied/held/mismatched never forwarded

        held_id = observed["held"][1]["error"]["data"]["held_call_id"]
        assert service.decide_call(held_id, actor("carol"), approve=True)["state"] == "approved"
        retry = {"r": {"kind": "call", "tool": "refund", "args": {"amount": 5}}}
        released = _exec(runtime, agent, retry)["r"]
        assert released[0] == 200 and released[1]["result"]["tool"] == "refund"
        again = _exec(runtime, agent, retry)["r"]
        assert again[1]["error"]["code"] == -32002  # single-use release
        assert again[1]["error"]["data"]["held_call_id"] != held_id

        decisions = [
            (row["decision"], row["context"].get("tool"))
            for row in store.audit_rows(limit=200)
            if row["action"] == "tool_call"
        ]
        assert ("allow", "lookup") in decisions and ("deny", "delete") in decisions
        assert ("held", "refund") in decisions and ("allow", "refund") in decisions
        assert store.verify_audit()["ok"]

        removed = service.remove(result["id"], local=True, actor=actor("dave"))
        assert removed["state"] == "removed"
        label = "label=io.agentbreeder.deployment=" + result["id"]
        assert runtime.command("container", "ls", "--all", "--filter", label, "-q") == ""
        assert runtime.command("network", "ls", "--filter", label, "-q") == ""
        assert not (tmp_path / "state" / "egress" / result["id"] / "endpoint.json").exists()
        result = None
    finally:
        if result:
            service.remove(result["id"], local=True, actor=actor("dave"))
        store.close()
        upstream.close()
        if image:
            runtime.command("image", "rm", image)


def _deploy(tmp_path, identity_env, monkeypatch, runtime):
    upstream = Upstream(tmp_path)
    url = f"https://host.docker.internal:{upstream.port}/mcp"
    enable_boundary(identity_env, url, upstream.pem, monkeypatch)
    store = Store(tmp_path / "state")
    service = DeploymentService(store, runtime, identity_env.load())
    image_file = tmp_path / "image-id"
    subprocess.run(
        ["docker", "--context", runtime.context, "build", "--iidfile", str(image_file),
         str(Path(__file__).parents[1] / "examples/http-agent")],
        check=True, timeout=180, capture_output=True,
    )  # fmt: skip
    manifest = Manifest.model_validate(
        v2_manifest_data(
            name="pep-" + uuid.uuid4().hex[:10],
            image=image_file.read_text().strip(),
            capabilities=tool_capabilities(url),
        )
    )
    result = service.deploy(manifest, local=True, deployer=actor("dave"))
    assert result["state"] == "ready", result
    return upstream, store, service, result


def test_real_sidecar_kill_and_tampered_topology(tmp_path, identity_env, monkeypatch):
    runtime = Docker()
    runtime.authorize()
    upstream, store, service, result = _deploy(tmp_path, identity_env, monkeypatch, runtime)
    record = store.get(result["id"])
    names = service.boundary.names(record)
    lookup = {"r": {"kind": "call", "tool": "lookup", "args": {}}}
    try:
        # Sidecar killed: the agent has no egress at all (not even a fallback path).
        runtime.command("container", "kill", names["sidecar"])
        observed = _exec(
            runtime,
            record["container"],
            {
                "gateway": {"kind": "reach", "host": "mcp-gateway.internal", "port": 8080},
                "public_ip": {"kind": "reach", "host": "1.1.1.1", "port": 443},
            },
        )
        assert observed == {"gateway": False, "public_ip": False}
        # Reconcile restores the enforcement point and re-verifies before traffic.
        [item] = service.reconcile(actor("dave"))
        assert item["compliance"] == "compliant", store.get(result["id"])["error"]
        assert _exec(runtime, record["container"], lookup)["r"][0] == 200

        # Tampered topology: the agent gains a route out. Reconcile must catch it and tear down.
        runtime.command("network", "connect", "bridge", record["container"])
        [item] = service.reconcile(actor("dave"))
        assert item["compliance"] == "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"
        assert store.get(result["id"])["state"] == "failed"
        label = "label=io.agentbreeder.deployment=" + result["id"]
        assert runtime.command("container", "ls", "--all", "--filter", label, "-q") == ""
        failed = [
            r for r in store.audit_rows(limit=50)
            if r["action"] == "transition" and r["decision"] == "failed"
        ]  # fmt: skip
        assert failed[0]["reasons"][0]["code"] == "RUNTIME_BOUNDARY_UNVERIFIED"
    finally:
        service.remove(result["id"], local=True, actor=actor("dave"))
        store.close()
        upstream.close()
