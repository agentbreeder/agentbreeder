"""Real-engine evidence for kit egress (#1083 F1b), enabled explicitly.

    AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_kit_egress_docker.py

Local Docker: deploys the sample agent with `runtime.profile: kit` and checks, from inside the
agent container, that the gateway on the host is reached through the forwarding sidecar and
nothing else is; that a stack network's services are reached directly; that killing the sidecar
or attaching the agent to the default bridge is caught by `reconcile`; and that removal leaves
nothing. Mock tests are not boundary evidence.

`kind`: the last test also needs a stack whose cluster was recorded by this version's
`agentbreeder cluster up` (so its operator config has `kubernetes.agents_cidrs`):

    AGENTBREEDER_DOCKER_TEST=1 \
    AGENTBREEDER_KIT_KIND_OPERATOR=<stack state>/environments/<env>/operator.yaml \
    AGENTBREEDER_KIT_KIND_ENVIRONMENT=<env> \
    pytest tests/test_kit_egress_docker.py -k kind
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest
from conftest import actor, v2_manifest_data

from agentbreeder import invoke, operator_config, promote
from agentbreeder.deployment import DeploymentService
from agentbreeder.egress.kit import KitBoundary
from agentbreeder.kubernetes import Kubernetes
from agentbreeder.manifest import Manifest
from agentbreeder.runtime import Docker
from agentbreeder.store import Store

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)

SAMPLE = Path(__file__).parents[1] / "src/agentbreeder/examples/http-agent"
KIT = {"ref": "runtime/langgraph@1.0.0", "profile": "kit"}
ACCESS = {"gateway": "litellm-local", "aliases": ["support-model"]}
GATEWAY_ENV = "AGENTBREEDER_GATEWAY_URL"
CALLER = r"""
import json, os, socket, sys, urllib.request, urllib.error
def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return [r.status, r.read(4096).decode()]
    except urllib.error.HTTPError as e:
        return [e.code, ""]
    except Exception as e:
        return [None, type(e).__name__]
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
out = {}
for key, step in json.loads(sys.argv[1]).items():
    kind = step["kind"]
    if kind == "env":
        out[key] = os.environ.get(step["name"])
    elif kind == "fetch":
        out[key] = fetch(step["url"])
    elif kind == "fetch_env":
        out[key] = fetch(os.environ[step["name"]] + step.get("path", ""))
    elif kind == "reach":
        out[key] = reach(step["host"], step["port"])
    else:
        out[key] = resolves(step["name"])
print(json.dumps(out))
"""
BYPASS = {
    "public_ip": {"kind": "reach", "host": "1.1.1.1", "port": 443},
    "public_ip_http": {"kind": "reach", "host": "1.1.1.1", "port": 80},
    "public_dns_server": {"kind": "reach", "host": "8.8.8.8", "port": 53},
    "public_ipv6": {"kind": "reach", "host": "2606:4700:4700::1111", "port": 443},
    "public_dns": {"kind": "resolve", "name": "example.com"},
    "host_dns": {"kind": "resolve", "name": "host.docker.internal"},
}


class HostServer:
    """A stand-in for a service on the host (the gateway, or something that is not it)."""

    def __init__(self, name: str):
        self.requests: list[str] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                server.requests.append(self.path)
                body = json.dumps({"served_by": name}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        # Docker Desktop reaches host loopback through host.docker.internal; a Linux engine's
        # host-gateway address is on a bridge.
        bind = "0.0.0.0" if sys.platform.startswith("linux") else "127.0.0.1"  # noqa: S104
        self.server = ThreadingHTTPServer((bind, 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _exec(runtime, container, plan) -> dict:
    output = runtime.command(
        "container", "exec", container, "python", "-c", CALLER, json.dumps(plan), timeout=120
    )
    return json.loads(output.splitlines()[-1])


def _build(runtime, tmp_path) -> tuple[str, str]:
    """The sample agent, removed later by a unique tag (other runs may share the image ID)."""
    image_file = tmp_path / "image-id"
    tag = "agentbreeder-test:" + uuid.uuid4().hex[:12]
    subprocess.run(
        ["docker", "--context", runtime.context, "build", "--iidfile", str(image_file),
         "--tag", tag, str(SAMPLE)],
        check=True, timeout=180, capture_output=True,
    )  # fmt: skip
    return image_file.read_text().strip(), tag


def _grant(identity_env, agent_url: str, **environment) -> None:
    identity_env.config["gateways"] = {
        "litellm-local": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": agent_url,
        }
    }
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm-local",
            "models": ["support-model"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
            **environment,
        }
    )


def _manifest(image: str, **overrides) -> Manifest:
    return Manifest.model_validate(
        v2_manifest_data(
            name="kit-" + uuid.uuid4().hex[:10],
            image=image,
            model_access=dict(ACCESS),
            runtime=dict(KIT),
            **overrides,
        )
    )


def _quietly(runtime, *arguments) -> None:
    """Cleanup that must not hide the test's own failure."""
    try:
        runtime.command(*arguments)
    except Exception:  # noqa: BLE001, S110
        pass


def _labelled(runtime, kind: str, identifier: str) -> str:
    listing = ["container", "ls", "--all"] if kind == "container" else ["network", "ls"]
    return runtime.command(
        *listing, "--filter", "label=io.agentbreeder.deployment=" + identifier, "-q"
    )


def test_real_kit_egress_with_the_gateway_on_the_host(tmp_path, identity_env):
    runtime = Docker()
    runtime.authorize()
    gateway, other = HostServer("gateway"), HostServer("not-the-gateway")
    _grant(identity_env, f"http://host.docker.internal:{gateway.port}")
    store = Store(tmp_path / "state")
    service = DeploymentService(store, runtime, identity_env.load())
    image = tag = result = None
    try:
        image, tag = _build(runtime, tmp_path)
        result = service.deploy(_manifest(image), local=True, deployer=actor("dave"))
        assert result["state"] == "ready", result
        record = store.get(result["id"])
        agent, names = record["container"], KitBoundary.names(record)

        # Ingress: the sidecar's loopback port, byte for byte the agent's HTTP port.
        with urlopen(
            Request(result["endpoint"] + "/invoke", data=b'{"message":"hi"}'), timeout=5
        ) as response:
            assert json.load(response)["input"]["message"] == "hi"
        invoke.verify(runtime, store, record)

        # Docker's own view: internal networks only, nothing published, no host mapping.
        info = json.loads(runtime.command("container", "inspect", agent))[0]
        assert set(info["NetworkSettings"]["Networks"]) == {names["internal"]}
        assert not any((info["HostConfig"].get("PortBindings") or {}).values())
        assert not info["HostConfig"].get("ExtraHosts")
        network = json.loads(runtime.command("network", "inspect", names["internal"]))[0]
        assert network["Internal"] is True

        observed = _exec(
            runtime,
            agent,
            {
                "gateway_url": {"kind": "env", "name": "AGENTBREEDER_GATEWAY_URL"},
                "gateway": {"kind": "fetch_env", "name": GATEWAY_ENV, "path": "/v1/models"},
                # Another service on the host: no forward exists for it, on any name.
                "other_via_sidecar": {
                    "kind": "reach",
                    "host": "kit-egress.internal",
                    "port": other.port,
                },
                "sidecar_health": {
                    "kind": "fetch",
                    "url": "http://kit-egress.internal:8080/healthz",
                },
                **BYPASS,
            },
        )
        assert observed["gateway_url"] == "http://kit-egress.internal:8082"
        assert observed["gateway"][0] == 200 and "gateway" in observed["gateway"][1]
        assert gateway.requests == ["/v1/models"] and other.requests == []
        assert observed["other_via_sidecar"] is False
        assert observed["sidecar_health"][0] == 200
        for name in BYPASS:
            assert observed[name] is False, name
        # The host is not reachable at the network's gateway address either, on either port.
        for item in network["IPAM"]["Config"]:
            address = item.get("Gateway")
            if address:
                reached = _exec(
                    runtime,
                    agent,
                    {
                        "gateway_port": {"kind": "reach", "host": address, "port": gateway.port},
                        "other_port": {"kind": "reach", "host": address, "port": other.port},
                    },
                )
                assert reached == {"gateway_port": False, "other_port": False}

        # A dead sidecar leaves the agent with no gateway; reconcile starts it and re-verifies.
        runtime.command("container", "kill", names["sidecar"])
        down = _exec(
            runtime, agent, {"g": {"kind": "fetch_env", "name": "AGENTBREEDER_GATEWAY_URL"}}
        )
        assert down["g"][0] is None
        assert service.reconcile(actor("ops"))[0]["compliance"] == "compliant"
        up = _exec(
            runtime, agent, {"g": {"kind": "fetch_env", "name": "AGENTBREEDER_GATEWAY_URL"}}
        )
        assert up["g"][0] == 200
        # A restarted sidecar may publish another loopback port; reconcile recorded it.
        record = store.get(result["id"])
        invoke.verify(runtime, store, record)
        with urlopen(record["endpoint"] + "/health", timeout=5) as response:
            assert response.status == 200

        # An agent given a route out is no longer verified: torn down, not left running.
        runtime.command("network", "connect", "bridge", agent)
        outcome = service.reconcile(actor("ops"))[0]
        assert outcome["compliance"] == "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"
        assert store.get(result["id"])["state"] == "failed"
        assert _labelled(runtime, "container", result["id"]) == ""
        assert _labelled(runtime, "network", result["id"]) == ""

        assert service.remove(result["id"], local=True, actor=actor("dave"))["state"] == "removed"
        result = None
    finally:
        if result:
            service.remove(result["id"], local=True, actor=actor("dave"))
        store.close()
        gateway.close()
        other.close()
        if image:
            _quietly(runtime, "image", "rm", tag)


@pytest.mark.skipif(
    sys.platform.startswith("linux"),
    reason="a Linux engine needs isolated-gateway stack networks, which services up does not make",
)
def test_real_kit_egress_on_a_stack_network(tmp_path, identity_env):
    """The environment's network stands in for a stack's agents network: internal, with one
    service on it. The agent reaches that service by name and still nothing outside."""
    runtime = Docker()
    runtime.authorize()
    suffix = uuid.uuid4().hex[:10]
    network, peer = f"agentbreeder-kit-test-{suffix}-agents", f"kit-test-gateway-{suffix}"
    _grant(identity_env, f"http://{peer}:8080", network=network)
    store = Store(tmp_path / "state")
    service = DeploymentService(store, runtime, identity_env.load())
    image = tag = result = None
    created = []
    try:
        image, tag = _build(runtime, tmp_path)
        runtime.command("network", "create", "--internal", network)
        created.append(("network", network))
        runtime.command(
            "container", "run", "--detach", "--name", peer, "--network", network, image
        )
        created.append(("container", peer))
        result = service.deploy(_manifest(image), local=True, deployer=actor("dave"))
        assert result["state"] == "ready", result
        record = store.get(result["id"])
        names = KitBoundary.names(record)
        info = json.loads(runtime.command("container", "inspect", record["container"]))[0]
        assert set(info["NetworkSettings"]["Networks"]) == {names["internal"], network}
        observed = _exec(
            runtime,
            record["container"],
            {
                "gateway_url": {"kind": "env", "name": "AGENTBREEDER_GATEWAY_URL"},
                "gateway": {"kind": "fetch_env", "name": GATEWAY_ENV, "path": "/health"},
                **BYPASS,
            },
        )
        assert observed["gateway_url"] == f"http://{peer}:8080"  # direct: no forward needed
        assert observed["gateway"][0] == 200
        for name in BYPASS:
            assert observed[name] is False, name
        with urlopen(result["endpoint"] + "/health", timeout=5) as response:
            assert response.status == 200
        assert service.reconcile(actor("ops"))[0]["compliance"] == "compliant"
        assert service.remove(result["id"], local=True, actor=actor("dave"))["state"] == "removed"
        assert _labelled(runtime, "container", result["id"]) == ""
        assert _labelled(runtime, "network", result["id"]) == ""
        # The environment's network is the stack's, never the deployment's to remove.
        assert (
            runtime.command("network", "inspect", "--format", "{{.Internal}}", network) == "true"
        )
        result = None
    finally:
        if result:
            service.remove(result["id"], local=True, actor=actor("dave"))
        store.close()
        for kind, name in reversed(created):
            try:
                runtime.command(kind, "rm", *(["--force"] if kind == "container" else []), name)
            except Exception:  # noqa: BLE001, S110 - best-effort cleanup of test fixtures
                pass
        if image:
            _quietly(runtime, "image", "rm", tag)


def test_real_kit_refusal_leaves_nothing_when_the_stack_network_is_not_internal(
    tmp_path, identity_env
):
    runtime = Docker()
    runtime.authorize()
    network = f"agentbreeder-kit-test-{uuid.uuid4().hex[:10]}-open"
    _grant(identity_env, "http://gateway:8080", network=network)
    store = Store(tmp_path / "state")
    service = DeploymentService(store, runtime, identity_env.load())
    image = tag = None
    try:
        image, tag = _build(runtime, tmp_path)
        runtime.command("network", "create", network)  # an ordinary bridge: a route out
        result = service.deploy(_manifest(image), local=True, deployer=actor("dave"))
        assert result["state"] == "failed"
        assert result["error"].startswith("[RUNTIME_BOUNDARY_UNVERIFIED]")
        assert "is not internal" in result["error"]
        assert _labelled(runtime, "container", result["id"]) == ""
        assert _labelled(runtime, "network", result["id"]) == ""
    finally:
        store.close()
        try:
            runtime.command("network", "rm", network)
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup of test fixtures
            pass
        if image:
            _quietly(runtime, "image", "rm", tag)


@pytest.mark.skipif(
    not os.getenv("AGENTBREEDER_KIT_KIND_OPERATOR"),
    reason="needs AGENTBREEDER_KIT_KIND_OPERATOR (a stack's operator.yaml after `cluster up`)",
)
def test_real_kit_egress_on_kind(tmp_path):
    """On the stack's `kind` cluster: a kit pod reaches the stack's gateway and no public
    address, while a pod without the field (the control) keeps the open rule it always had."""
    operator = operator_config.load_operator_config(
        Path(os.environ["AGENTBREEDER_KIT_KIND_OPERATOR"])
    )
    environment = os.getenv("AGENTBREEDER_KIT_KIND_ENVIRONMENT") or (
        operator.model.default_environment or "dev"
    )
    config = operator.model.environments[environment]
    assert config.kubernetes.agents_cidrs, "run `agentbreeder cluster up` with this version first"
    gateway = urlsplit(operator.model.gateways[config.gateway].agent_url)
    docker = Docker()
    docker.authorize()
    kube = Kubernetes(operator)
    kube.select(environment)
    kube.authorize()
    image, tag = _build(docker, tmp_path)
    reference = promote.push_image(
        docker, image, f"{config.kubernetes.registry}/kit-egress-test", uuid.uuid4().hex[:12]
    )
    plan = {
        "gateway": {"kind": "reach", "host": gateway.hostname, "port": gateway.port or 80},
        "public_ip": {"kind": "reach", "host": "1.1.1.1", "port": 443},
        "public_ip_http": {"kind": "reach", "host": "1.1.1.1", "port": 80},
    }
    records = []
    try:
        observed = {}
        for name, runtime_field in (("kit", dict(KIT)), ("open", None)):
            data = v2_manifest_data(
                name=f"kit-{name}-" + uuid.uuid4().hex[:8],
                image=reference,
                target="kubernetes",
                model_access=dict(ACCESS),
            )
            if runtime_field:
                data["runtime"] = runtime_field
            manifest = Manifest.model_validate(data)
            identifier = uuid.uuid4().hex
            record = {
                "id": identifier,
                "container": "agentbreeder-" + identifier,
                "digest": manifest.digest(),
            }
            records.append(record)
            kube.create(record, manifest)
            kube.start(kube.inspect(record))
            output = kube._namespaced(
                "exec", f"deployment/{record['container']}", "--",
                "python", "-c", CALLER, json.dumps(plan), timeout=120,
            )  # fmt: skip
            observed[name] = json.loads(output.splitlines()[-1])
        assert observed["kit"] == {"gateway": True, "public_ip": False, "public_ip_http": False}
        # The control shows the public address is reachable from this cluster, so the kit
        # pod's failure is the policy and not the network.
        assert observed["open"]["gateway"] is True and observed["open"]["public_ip"] is True
    finally:
        for record in records:
            kube.sweep(record)
        _quietly(docker, "image", "rm", tag)
