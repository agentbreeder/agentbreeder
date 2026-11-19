"""Default-deny egress for `runtime.profile: kit` manifests (#1083 F1b, spec §13.4, D11).

Scripted Docker and kubectl only: the forwarder runs here on loopback sockets, the topology,
verification and deploy paths against fakes, and the goldens show that a manifest without the
field deploys exactly as before. `tests/test_kit_egress_docker.py` is the boundary evidence, on
a real engine.
"""

from __future__ import annotations

import hashlib
import io
import json
import socket
import tarfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from importlib import resources
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import actor, v2_manifest_data, write_private

from agentbreeder import cluster, invoke, kubernetes
from agentbreeder.deployment import DeploymentService
from agentbreeder.egress import boundary as boundary_module
from agentbreeder.egress import forwarder
from agentbreeder.egress import kit as kit_egress
from agentbreeder.egress.boundary import BoundaryUnverified, sidecar_context
from agentbreeder.egress.kit import KitBoundary
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import OperatorConfigError
from agentbreeder.store import DeploymentError, Store

RECORD = {"id": "d" * 32, "container": "agentbreeder-" + "d" * 32, "digest": "spec"}
NAMES = KitBoundary.names(RECORD)
HOST_GATEWAY = "http://host.docker.internal:4000"
STACK_GATEWAY = "http://litellm:4000"
KIT = {"ref": "runtime/langgraph@1.0.0", "profile": "kit"}
ACCESS = {"gateway": "litellm-local", "aliases": ["support-model"]}


def kit_manifest(**overrides) -> Manifest:
    return Manifest.model_validate(
        v2_manifest_data(model_access=dict(ACCESS), runtime=dict(KIT), **overrides)
    )


def plain_manifest(**overrides) -> Manifest:
    return Manifest.model_validate(v2_manifest_data(model_access=dict(ACCESS), **overrides))


# -- the forwarder (runs in the sidecar; here on loopback) -------------------------------------


class Echo:
    """Reads until the client half-closes, then answers once and closes."""

    def __init__(self):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.seen: list[bytes] = []
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            with connection:
                data = b""
                while chunk := connection.recv(65536):
                    data += chunk
                self.seen.append(data)
                connection.sendall(b"reply:" + data)

    def close(self):
        self.server.close()


def _exchange(port: int, payload: bytes, timeout: float = 5) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
        data = b""
        try:
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)
            while chunk := connection.recv(65536):
                data += chunk
        except (ConnectionError, OSError) as error:
            if isinstance(error, TimeoutError):
                raise
        return data  # a connection the forwarder already closed reads as empty


@pytest.fixture
def echo():
    server = Echo()
    yield server
    server.close()


def test_forward_copies_both_ways_and_passes_the_half_close(echo):
    forward = forwarder.Forward(0, "127.0.0.1", echo.port, bind="127.0.0.1").start()
    try:
        payload = b"x" * 200_000  # larger than one chunk
        assert _exchange(forward.listen, payload) == b"reply:" + payload
        assert echo.seen == [payload]
    finally:
        forward.close()


def test_forward_goes_only_to_its_fixed_destination(echo):
    """No proxy protocol: a CONNECT line is bytes for the one configured destination."""
    forward = forwarder.Forward(0, "127.0.0.1", echo.port, bind="127.0.0.1").start()
    try:
        request = b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
        assert _exchange(forward.listen, request) == b"reply:" + request
    finally:
        forward.close()


def test_forward_closes_the_caller_when_the_destination_is_down():
    with socket.create_server(("127.0.0.1", 0)) as unused:
        dead = unused.getsockname()[1]
    forward = forwarder.Forward(0, "127.0.0.1", dead, bind="127.0.0.1").start()
    try:
        assert _exchange(forward.listen, b"hello") == b""
    finally:
        forward.close()


def test_forward_refuses_connections_over_its_cap(echo):
    forward = forwarder.Forward(0, "127.0.0.1", echo.port, bind="127.0.0.1", limit=1).start()
    try:
        held = socket.create_connection(("127.0.0.1", forward.listen), timeout=5)
        held.sendall(b"a")  # the echo server now waits for this connection's end
        deadline = time.monotonic() + 5
        while forward.slots._value and time.monotonic() < deadline:
            time.sleep(0.01)
        assert forward.slots._value == 0  # the first connection holds the only slot
        assert _exchange(forward.listen, b"second") == b""
        held.shutdown(socket.SHUT_WR)
        assert held.recv(64) == b"reply:a"
        held.close()
    finally:
        forward.close()


def test_the_accept_loop_survives_a_failed_accept_and_a_thread_it_cannot_start(echo, monkeypatch):
    forward = forwarder.Forward(0, "127.0.0.1", echo.port, bind="127.0.0.1", limit=2)
    real_accept, failures = forward.server.accept, [OSError("aborted")]

    class Flaky:
        def accept(self):
            if failures:
                raise failures.pop()
            return real_accept()

        def close(self):
            forward_socket.close()

    forward_socket, forward.server = forward.server, Flaky()
    real_thread, refused = threading.Thread, []

    def thread(*args, **kwargs):
        if kwargs.get("target") == forward.handle and not refused:
            refused.append(True)
            raise RuntimeError("can't start new thread")
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(forwarder.threading, "Thread", thread)
    forward.start()
    try:
        assert _exchange(forward.listen, b"first") == b""  # refused, and its slot given back
        assert _exchange(forward.listen, b"second") == b"reply:second"
        assert _exchange(forward.listen, b"third") == b"reply:third"
        assert failures == [] and refused == [True]
    finally:
        forward.close()


def test_the_health_server_caps_and_times_out_idle_connections():
    config = {"deployment": "d1", "code_digest": "x", "health_port": 0, "forwards": []}
    server = forwarder.HealthServer(("127.0.0.1", 0), forwarder.health_handler(config), limit=1)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        idle = socket.create_connection(("127.0.0.1", port), timeout=5)
        deadline = time.monotonic() + 5
        while server.slots._value and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.slots._value == 0
        assert _exchange(port, b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n") == b""  # over the cap
        assert forwarder.health_handler(config).timeout == forwarder.HEALTH_TIMEOUT <= 10
        idle.close()
        deadline = time.monotonic() + 5
        while not server.slots._value and time.monotonic() < deadline:
            time.sleep(0.01)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
            assert response.status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_the_sidecars_threads_fit_its_pid_limit():
    """Two forwards at their cap, the health server at its cap, three accept loops and the
    main thread must stay under the `--pids-limit` the sidecar is created with."""
    docker = FakeDocker({("image", "inspect"): _labelled()})
    _created(docker, environment={"AGENTBREEDER_GATEWAY_URL": HOST_GATEWAY})
    sidecar = next(c for c in docker.calls if c[:2] == ("container", "create"))
    limit = int(sidecar[sidecar.index("--pids-limit") + 1])
    threads = 2 * forwarder.MAX_CONNECTIONS + forwarder.HEALTH_CONNECTIONS + 4
    assert threads + 16 <= limit


def test_pump_ends_a_silent_connection():
    left, left_peer = socket.socketpair()
    right, right_peer = socket.socketpair()
    try:
        forwarder.pump(left_peer, right_peer, idle=0.05)  # returns instead of waiting forever
    finally:
        for connection in (left, left_peer, right, right_peer):
            connection.close()


def test_pump_stops_on_a_reset_peer():
    left, left_peer = socket.socketpair()
    right, right_peer = socket.socketpair()
    right_peer.close()
    left.sendall(b"data")
    try:
        forwarder.pump(left_peer, right_peer, idle=1)
    finally:
        for connection in (left, left_peer, right):
            connection.close()


@pytest.mark.parametrize(
    "forwards",
    [
        [{"listen": 0, "host": "agent", "port": 8080}],
        [{"listen": 8081, "host": "", "port": 8080}],
        [{"listen": 8081, "host": "agent", "port": 70000}],
        [{"listen": 8081, "host": "agent", "port": True}],
        [{"listen": 8081, "host": "agent", "port": "8080"}],
        [{"listen": 8081, "host": "a", "port": 1}, {"listen": 8081, "host": "b", "port": 2}],
    ],
)
def test_malformed_forwards_stop_the_sidecar(forwards):
    with pytest.raises(ValueError):
        forwarder.parse_forwards({"forwards": forwards})


def test_health_reports_the_configuration_and_nothing_else():
    config = {
        "deployment": "d1",
        "code_digest": "sha256:x",
        "health_port": 8080,
        "forwards": [{"listen": 8081, "host": "agent", "port": 8080}],
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), forwarder.health_handler(config))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/healthz", timeout=5) as response:
            assert json.load(response) == {
                "deployment": "d1",
                "code_digest": "sha256:x",
                "forwards": [[8081, "agent", 8080]],
            }
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(base + "/anything", timeout=5)
        assert refused.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


# -- images: the phase-4 sidecar is unchanged, the forwarder is its own image ------------------


def _legacy_context() -> tuple[bytes, str]:
    """`sidecar_context` as it was before F1b, written out: the phase-4 image must not change
    (a changed code digest fails verification of every running mcp_http boundary)."""
    files = {
        f"agentbreeder/{path}": resources.files(package).joinpath(name).read_bytes()
        for path, package, name in (
            ("__init__.py", "agentbreeder", "__init__.py"),
            ("egress/__init__.py", "agentbreeder.egress", "__init__.py"),
            ("egress/protocol.py", "agentbreeder.egress", "protocol.py"),
            ("egress/sidecar.py", "agentbreeder.egress", "sidecar.py"),
            ("egress/probe.py", "agentbreeder.egress", "probe.py"),
        )
    }
    digest = hashlib.sha256()
    for target in sorted(files):
        digest.update(target.encode() + b"\0" + files[target] + b"\0")
    code_digest = "sha256:" + digest.hexdigest()
    files["Dockerfile"] = (
        f"FROM {boundary_module.SIDECAR_BASE}\n"
        "COPY agentbreeder /opt/agentbreeder/agentbreeder\n"
        "ENV PYTHONPATH=/opt/agentbreeder PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
        f"LABEL io.agentbreeder.sidecar-code={code_digest}\n"
        "USER 65532:65532\n"
        'ENTRYPOINT ["python", "-m", "agentbreeder.egress.sidecar"]\n'
    ).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(files[name]), 0o644, 0
            archive.addfile(info, io.BytesIO(files[name]))
    return buffer.getvalue(), code_digest


def test_the_phase4_sidecar_image_is_byte_identical():
    assert sidecar_context() == _legacy_context()


def test_forwarder_context_holds_only_the_forwarder_and_the_probe():
    context, digest = kit_egress.forwarder_context()
    assert (context, digest) == kit_egress.forwarder_context()
    assert digest != sidecar_context()[1]
    archive = tarfile.open(fileobj=io.BytesIO(context))
    assert sorted(archive.getnames()) == [
        "Dockerfile",
        "agentbreeder/__init__.py",
        "agentbreeder/egress/__init__.py",
        "agentbreeder/egress/forwarder.py",
        "agentbreeder/egress/probe.py",
    ]
    dockerfile = archive.extractfile("Dockerfile").read().decode()
    assert boundary_module.SIDECAR_BASE in dockerfile and "USER 65532:65532" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "agentbreeder.egress.forwarder"]' in dockerfile


def test_forwarder_uses_the_standard_library_only():
    """It runs in the pinned base image, which has no AgentBreeder dependencies."""
    import ast
    import sys

    source = resources.files("agentbreeder.egress").joinpath("forwarder.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported - {"__future__"} <= set(sys.stdlib_module_names)


# -- routing and preflight ----------------------------------------------------------------------


def test_only_a_plain_http_gateway_on_the_host_is_forwarded():
    assert kit_egress.route(None) == (None, None)
    assert kit_egress.route(STACK_GATEWAY) == (STACK_GATEWAY, None)
    assert kit_egress.route("https://gateway.example.com/v1") == (
        "https://gateway.example.com/v1",
        None,
    )
    assert kit_egress.route(HOST_GATEWAY + "/v1?x=1") == (
        "http://kit-egress.internal:8082/v1?x=1",
        {"listen": 8082, "host": "host.docker.internal", "port": 4000},
    )
    assert kit_egress.route("http://HOST.docker.internal")[1]["port"] == 80
    with pytest.raises(DeploymentError, match="KIT_EGRESS_UNSUPPORTED"):
        kit_egress.route("https://host.docker.internal:4000")
    for bad in ("http://host.docker.internal:99999", "http://host.docker.internal:0"):
        with pytest.raises(DeploymentError, match="KIT_EGRESS_UNSUPPORTED.*invalid port"):
            kit_egress.route(bad)


def test_forwards_are_the_ingress_and_at_most_the_gateway():
    manifest = kit_manifest(port=9000)
    assert kit_egress.forwards(manifest, STACK_GATEWAY) == [
        {"listen": 8081, "host": "agent", "port": 9000}
    ]
    assert kit_egress.forwards(manifest, HOST_GATEWAY)[1] == {
        "listen": 8082,
        "host": "host.docker.internal",
        "port": 4000,
    }


def test_applies_only_to_kit_manifests_on_local_docker():
    assert kit_egress.applies(kit_manifest())
    assert not kit_egress.applies(plain_manifest())
    assert not kit_egress.applies(Manifest.model_validate(v2_manifest_data()))
    cluster_manifest = kit_manifest(
        target="kubernetes", image="registry.example.com/a@sha256:" + "a" * 64
    )
    assert kit_egress.is_kit(cluster_manifest) and not kit_egress.applies(cluster_manifest)


def grant(identity_env, agent_url=HOST_GATEWAY, **environment):
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


@pytest.mark.parametrize(
    ("agent_url", "environment", "message"),
    [
        (HOST_GATEWAY, {}, None),
        (STACK_GATEWAY, {"network": "stack-agents"}, None),
        (
            STACK_GATEWAY,
            {"network": "stack-agents", "services": {"traces_url": "http://phoenix:6006/v1"}},
            None,
        ),
        ("https://host.docker.internal:4000", {}, "over http only"),
        ("http://host.docker.internal:99999", {}, "invalid port"),
        (STACK_GATEWAY, {}, "no route to this gateway"),
        (
            HOST_GATEWAY,
            {"services": {"traces_url": "http://host.docker.internal:8787/v1/traces"}},
            "Service traces_url is on the host",
        ),
    ],
)
def test_preflight_refuses_what_a_kit_runtime_cannot_reach(
    identity_env, state, runtime, agent_url, environment, message
):
    grant(identity_env, agent_url, **environment)
    service = DeploymentService(state, runtime, identity_env.load())
    decision = service.preflight(kit_manifest(), "dev", actor("dave"))
    if message is None:
        assert decision.allowed, decision
    else:
        assert not decision.allowed and decision.code == "KIT_EGRESS_UNSUPPORTED"
        assert message in decision.message
    # The same environment still takes a manifest without the field.
    assert service.preflight(plain_manifest(), "dev", actor("dave")).allowed


def test_preflight_refuses_a_relay_on_the_host(identity_env, tmp_path):
    grants = tmp_path / "grants"
    grants.mkdir(mode=0o700)
    grant(
        identity_env,
        mcp_relays={
            "github": {
                "ref": "mcp/github@1.0.0",
                "url": "http://host.docker.internal:9000/mcp",
                "grants_dir": str(grants),
            }
        },
    )
    model = identity_env.load().model
    manifest = kit_manifest(mcp_access=[{"ref": "mcp/github@1.0.0", "tools": ["search"]}])
    code, message = kit_egress.requirements(manifest, model, "dev")
    assert code == "KIT_EGRESS_UNSUPPORTED" and "MCP relay github" in message
    assert kit_egress.requirements(kit_manifest(), model, "dev") is None
    assert kit_egress.requirements(kit_manifest(), model, "missing") is None


# -- topology (scripted Docker) -----------------------------------------------------------------


class FakeDocker:
    def __init__(self, responses=None, system="Docker Desktop", fail=()):
        self.calls: list[tuple] = []
        self.responses = responses or {}
        self.system = system
        self.fail = fail
        self.create = Mock()

    def command(self, *args, timeout=30, stdin=None, env=None):
        self.calls.append(args)
        if args[:1] == ("info",):
            return self.system
        if any(args[: len(prefix)] == prefix for prefix in self.fail):
            raise DeploymentError("docker failed")
        for prefix, value in self.responses.items():
            if args[: len(prefix)] == prefix:
                return value(args) if callable(value) else value
        return ""


INTERNAL = json.dumps([{"Internal": True}])


def _labelled() -> str:
    digest = kit_egress.forwarder_context()[1]
    return json.dumps([{"Config": {"Labels": {"io.agentbreeder.sidecar-code": digest}}}])


def _created(docker, manifest=None, **options):
    boundary = KitBoundary(docker)
    boundary.create(RECORD, manifest or kit_manifest(), **options)
    return boundary


def test_create_puts_the_agent_on_internal_networks_only():
    docker = FakeDocker({("image", "inspect"): _labelled(), ("network", "inspect"): INTERNAL})
    environment = {"AGENTBREEDER_GATEWAY_URL": HOST_GATEWAY, "AGENTBREEDER_DEPLOYMENT_ID": "x"}
    mounts = [object()]
    _created(docker, environment=environment, mounts=mounts, agents_network="stack-agents")
    networks = [c for c in docker.calls if c[:2] == ("network", "create")]
    assert "--internal" in networks[0] and networks[0][-1] == NAMES["internal"]
    assert "--internal" not in networks[1] and networks[1][-1] == NAMES["egress"]
    assert all("io.agentbreeder.deployment=" + RECORD["id"] in call for call in networks)
    assert not any(a.startswith("--opt=") for call in networks for a in call)  # Docker Desktop
    sidecar = next(c for c in docker.calls if c[:2] == ("container", "create"))
    assert sidecar[sidecar.index("--name") + 1] == NAMES["sidecar"]
    assert sidecar[sidecar.index("--network") + 1] == NAMES["egress"]
    for flag in ("--read-only", "no-new-privileges", "65532:65532", "127.0.0.1::8081"):
        assert flag in sidecar
    assert sidecar[sidecar.index("--cap-drop") + 1] == "ALL"
    assert "io.agentbreeder.role=forwarder" in sidecar and "--add-host" not in sidecar
    config = json.loads(
        next(a for a in sidecar if a.startswith("AGENTBREEDER_FORWARDER_CONFIG=")).split("=", 1)[1]
    )
    assert config == {
        "deployment": RECORD["id"],
        "code_digest": kit_egress.forwarder_context()[1],
        "health_port": 8080,
        "forwards": [
            {"listen": 8081, "host": "agent", "port": 8080},
            {"listen": 8082, "host": "host.docker.internal", "port": 4000},
        ],
    }
    connects = [c for c in docker.calls if c[:2] == ("network", "connect")]
    assert connects == [
        (
            "network",
            "connect",
            "--alias",
            "kit-egress.internal",
            NAMES["internal"],
            NAMES["sidecar"],
        ),
        ("network", "connect", "stack-agents", RECORD["container"]),
    ]
    # The agent: its own internal network, no published port, no host mapping, and the
    # gateway URL of the sidecar.
    assert docker.create.call_args.kwargs == {
        "network": NAMES["internal"],
        "environment": {
            "AGENTBREEDER_GATEWAY_URL": "http://kit-egress.internal:8082",
            "AGENTBREEDER_DEPLOYMENT_ID": "x",
        },
        "mounts": mounts,
    }
    assert environment["AGENTBREEDER_GATEWAY_URL"] == HOST_GATEWAY  # the caller's is untouched
    # Stale containers and networks of the deployment go first.
    assert docker.calls.index(networks[0]) > next(
        i for i, c in enumerate(docker.calls) if c[:2] == ("container", "ls")
    )


def test_a_gateway_on_the_stack_network_needs_no_forward():
    docker = FakeDocker({("image", "inspect"): _labelled()})
    _created(docker, environment={"AGENTBREEDER_GATEWAY_URL": STACK_GATEWAY})
    sidecar = next(c for c in docker.calls if c[:2] == ("container", "create"))
    config = next(a for a in sidecar if a.startswith("AGENTBREEDER_FORWARDER_CONFIG="))
    assert json.loads(config.split("=", 1)[1])["forwards"] == [
        {"listen": 8081, "host": "agent", "port": 8080}
    ]
    assert docker.create.call_args.kwargs["environment"] == {
        "AGENTBREEDER_GATEWAY_URL": STACK_GATEWAY
    }
    assert len([c for c in docker.calls if c[:2] == ("network", "connect")]) == 1


def test_a_linux_engine_isolates_the_gateway_and_maps_the_host_for_the_sidecar_only():
    docker = FakeDocker({("image", "inspect"): _labelled(), ("version",): "28.1.1"}, "Ubuntu")
    _created(docker, environment={"AGENTBREEDER_GATEWAY_URL": HOST_GATEWAY})
    internal = next(c for c in docker.calls if c[:2] == ("network", "create"))
    for option in boundary_module.ISOLATED_GATEWAY:
        assert f"--opt={option}" in internal
    sidecar = next(c for c in docker.calls if c[:2] == ("container", "create"))
    assert sidecar[sidecar.index("--add-host") + 1] == "host.docker.internal:host-gateway"
    assert "host_gateway" not in docker.create.call_args.kwargs
    old = FakeDocker({("image", "inspect"): _labelled(), ("version",): "27.5.1"}, "Ubuntu")
    with pytest.raises(DeploymentError, match="Docker Engine 28"):
        _created(old, environment={})
    assert not any(c[:2] == ("network", "create") for c in old.calls)


def test_an_open_stack_network_is_refused_before_anything_is_created():
    docker = FakeDocker(
        {
            ("image", "inspect"): _labelled(),
            ("network", "inspect"): json.dumps([{"Internal": False}]),
        }
    )
    with pytest.raises(DeploymentError, match=r"\[RUNTIME_BOUNDARY_UNVERIFIED\].*not internal"):
        _created(docker, environment={}, agents_network="open-bridge")
    assert not any(c[:2] in (("network", "create"), ("container", "create")) for c in docker.calls)
    docker.create.assert_not_called()


def test_a_missing_stack_network_is_reported_as_such():
    docker = FakeDocker(
        {("image", "inspect"): _labelled(), ("network", "inspect"): INTERNAL},
        fail=[("network", "connect", "stack-agents")],
    )
    with pytest.raises(DeploymentError, match="services up"):
        _created(docker, environment={}, agents_network="stack-agents")


def test_the_forwarder_image_is_built_once_per_code_digest():
    built = FakeDocker({("image", "inspect"): json.dumps([{"Config": {"Labels": {}}}])})
    tag, digest = KitBoundary(built).image()
    assert tag == "agentbreeder-forwarder:" + digest[7:23]
    assert any(c[:2] == ("build", "--quiet") for c in built.calls)
    reused = FakeDocker({("image", "inspect"): _labelled()})
    KitBoundary(reused).image()
    assert not any(c[0] == "build" for c in reused.calls)


# -- verification -------------------------------------------------------------------------------


def _world(gateway_url=HOST_GATEWAY, agents_network="stack-agents", **changes):
    digest = kit_egress.forwarder_context()[1]
    labels = {"io.agentbreeder.deployment": RECORD["id"]}
    config = "AGENTBREEDER_FORWARDER_CONFIG=" + KitBoundary(FakeDocker()).config(
        RECORD, kit_manifest(), gateway_url
    )
    agent_networks = {NAMES["internal"]: {}}
    if agents_network:
        agent_networks[agents_network] = {}
    world = {
        "internal": {
            "Internal": True,
            "Labels": dict(labels),
            "Containers": {"agent-id": {}, "fwd-id": {}},
            "IPAM": {"Config": [{"Gateway": "172.30.0.1"}]},
        },
        "stack": {
            "Internal": True,
            "Containers": {"agent-id": {}, "litellm-id": {}},
            "IPAM": {"Config": [{"Gateway": "172.31.0.1"}, {"Subnet": "fd00::/64"}]},
        },
        "egress": {"Labels": dict(labels), "Containers": {"fwd-id": {}}},
        "agent": {
            "Id": "agent-id",
            "Config": {"Labels": {**labels, "io.agentbreeder.role": "agent"}},
            "State": {"Running": True},
            "HostConfig": {"NetworkMode": NAMES["internal"], "CapDrop": ["ALL"]},
            "NetworkSettings": {"Networks": agent_networks},
        },
        "sidecar": {
            "Id": "fwd-id",
            "Image": "sha256:img",
            "Config": {"Labels": {**labels, "io.agentbreeder.role": "forwarder"}, "Env": [config]},
            "State": {"Running": True},
            "HostConfig": {
                "CapDrop": ["ALL"],
                "PortBindings": {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]},
            },
            "NetworkSettings": {
                "Networks": {NAMES["internal"]: {}, NAMES["egress"]: {}},
                "Ports": {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49200"}]},
            },
        },
        "image": {"Config": {"Labels": {"io.agentbreeder.sidecar-code": digest}}},
        "probe": {
            "tcp_open": [],
            "dns_resolved": [],
            "gateway": {
                "status": 200,
                "body": {"deployment": RECORD["id"], "code_digest": digest, "forwards": []},
            },
        },
    }
    for path, value in changes.items():
        target = world
        *parents, leaf = path.split(".")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return world


def _scripted(world, system="Docker Desktop", version="28.1.1"):
    def inspect(args):
        name = args[-1]
        if args[0] == "network":
            key = {NAMES["internal"]: "internal", NAMES["egress"]: "egress"}.get(name, "stack")
            return json.dumps([world[key]])
        if args[0] == "image":
            return json.dumps([world["image"]])
        return json.dumps([world["agent" if name == RECORD["container"] else "sidecar"]])

    return FakeDocker(
        {
            ("network", "inspect"): inspect,
            ("container", "inspect"): inspect,
            ("image", "inspect"): inspect,
            ("container", "run"): "noise\n" + json.dumps(world["probe"]),
            ("version",): version,
        },
        system,
    )


def _verify(world, gateway_url=HOST_GATEWAY, agents_network="stack-agents", **docker):
    scripted = _scripted(world, **docker)
    report = KitBoundary(scripted).verify(
        RECORD, kit_manifest(), gateway_url=gateway_url, agents_network=agents_network
    )
    return report, scripted


def test_verify_passes_and_probes_from_the_agents_namespace():
    report, docker = _verify(_world())
    assert report["tcp_open"] == []
    probe = next(c for c in docker.calls if c[:2] == ("container", "run"))
    assert probe[probe.index("--network") + 1] == "container:" + RECORD["container"]
    assert probe[probe.index("--entrypoint") + 1] == "python"
    assert "agentbreeder.egress.probe" in probe
    plan = json.loads(probe[-1])
    assert ["1.1.1.1", 443] in plan["tcp"] and ["2606:4700:4700::1111", 443] in plan["tcp"]
    # The host must be out of reach at the gateway of every network the agent is on,
    # including on the port the sidecar forwards to.
    for gateway in ("172.30.0.1", "172.31.0.1"):
        assert [gateway, 2375] in plan["tcp"] and [gateway, 4000] in plan["tcp"]
    assert {"example.com", "host.docker.internal"} <= set(plan["dns_must_fail"])
    assert plan["gateway"] == "http://kit-egress.internal:8080/healthz"
    assert KitBoundary(docker).endpoint(RECORD) == "http://127.0.0.1:49200"


def test_verify_without_a_stack_network_expects_only_the_deployments_own():
    world = _world(agents_network=None)
    report, docker = _verify(world, agents_network=None)
    assert report["dns_resolved"] == []
    inspected = [c[-1] for c in docker.calls if c[:2] == ("network", "inspect")]
    assert inspected == [NAMES["internal"], NAMES["egress"]]


AGENT_HOST = {"NetworkMode": NAMES["internal"], "CapDrop": ["ALL"]}
SIDECAR_HOST = {
    "CapDrop": ["ALL"],
    "PortBindings": {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}]},
}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"internal.Internal": False}, "is not internal"),
        ({"stack.Internal": False}, "stack-agents is not internal"),
        ({"internal.Labels": {}}, "Agent network ownership"),
        ({"internal.Containers": {"agent-id": {}, "fwd-id": {}, "x": {}}}, "unexpected members"),
        ({"egress.Labels": {}}, "Forwarder network ownership"),
        ({"egress.Containers": {"fwd-id": {}, "x": {}}}, "Forwarder network has unexpected"),
        ({"agent.Config": {"Labels": {}}}, "agent ownership"),
        ({"sidecar.Config.Labels": {}}, "forwarder ownership"),
        ({"agent.HostConfig": {**AGENT_HOST, "Privileged": True}}, "privileged"),
        ({"agent.HostConfig": {**AGENT_HOST, "CapAdd": ["NET_ADMIN"]}}, "privileged"),
        ({"sidecar.State": {"Running": False}}, "not running"),
        ({"agent.HostConfig": {"NetworkMode": NAMES["internal"]}}, "capabilities"),
        ({"sidecar.HostConfig": {"PortBindings": SIDECAR_HOST["PortBindings"]}}, "capabilities"),
        (
            {"agent.NetworkSettings": {"Networks": {NAMES["internal"]: {}, "bridge": {}}}},
            "other than its own",
        ),
        ({"agent.NetworkSettings": {"Networks": {NAMES["internal"]: {}}}}, "other than its own"),
        ({"agent.HostConfig": {**AGENT_HOST, "NetworkMode": "bridge"}}, "other than its own"),
        (
            {"agent.HostConfig": {**AGENT_HOST, "PortBindings": {"8080/tcp": [{}]}}},
            "Agent publishes ports",
        ),
        (
            {"agent.HostConfig": {**AGENT_HOST, "ExtraHosts": ["host.docker.internal:1.2.3.4"]}},
            "host mapping",
        ),
        (
            {"sidecar.NetworkSettings": {"Networks": {NAMES["internal"]: {}, "bridge": {}}}},
            "Forwarder networks",
        ),
        (
            {
                "sidecar.HostConfig": {
                    "CapDrop": ["ALL"],
                    "PortBindings": {"8081/tcp": [{"HostIp": "0.0.0.0", "HostPort": ""}]},
                }
            },
            "more than the loopback ingress",
        ),
        (
            {
                "sidecar.HostConfig": {
                    "CapDrop": ["ALL"],
                    "PortBindings": {
                        **SIDECAR_HOST["PortBindings"],
                        "8082/tcp": [{"HostIp": "127.0.0.1", "HostPort": ""}],
                    },
                }
            },
            "more than the loopback ingress",
        ),
        ({"sidecar.HostConfig": {"CapDrop": ["ALL"]}}, "more than the loopback ingress"),
        ({"sidecar.Config.Env": ["AGENTBREEDER_FORWARDER_CONFIG={}"]}, "configuration"),
        ({"image.Config": {"Labels": {}}}, "expected code"),
        ({"probe.tcp_open": [["1.1.1.1", 443]]}, "outside its boundary"),
        ({"probe.dns_resolved": ["example.com"]}, "resolved"),
        ({"probe.gateway": {"status": 502}}, "health"),
        ({"probe.gateway": None}, "health"),
        (
            {"probe.gateway": {"status": 200, "body": {"deployment": "other"}}},
            "health",
        ),
    ],
)
def test_verify_fails_closed(changes, message):
    with pytest.raises(BoundaryUnverified, match=message):
        _verify(_world(**changes))


def test_a_changed_gateway_no_longer_verifies():
    """The sidecar's forwards are fixed at deploy; the operator's gateway moving is a redeploy."""
    with pytest.raises(BoundaryUnverified, match="configuration"):
        _verify(_world(), gateway_url="http://host.docker.internal:4999")


def test_a_linux_engine_must_isolate_every_agent_network():
    isolated = dict(o.split("=", 1) for o in boundary_module.ISOLATED_GATEWAY)
    with pytest.raises(BoundaryUnverified, match="not isolated from the host"):
        _verify(_world(), system="Ubuntu")
    world = _world(**{"internal.Options": isolated})
    with pytest.raises(BoundaryUnverified, match="stack-agents's gateway is not isolated"):
        _verify(world, system="Ubuntu")
    world = _world(**{"internal.Options": isolated, "stack.Options": isolated})
    assert _verify(world, system="Ubuntu")[0]["tcp_open"] == []
    with pytest.raises(BoundaryUnverified, match="Docker Engine 28"):
        _verify(world, system="Ubuntu", version="27.5.1")


def test_a_probe_without_a_report_is_unverified():
    scripted = _scripted(_world())
    scripted.responses[("container", "run")] = "not json"
    with pytest.raises(BoundaryUnverified, match="no report"):
        KitBoundary(scripted).verify(
            RECORD, kit_manifest(), gateway_url=HOST_GATEWAY, agents_network="stack-agents"
        )


def test_endpoint_needs_the_deployments_own_forwarder():
    world = _world(**{"sidecar.Config.Labels": {"io.agentbreeder.deployment": "other"}})
    with pytest.raises(BoundaryUnverified, match="ownership"):
        KitBoundary(_scripted(world)).endpoint(RECORD)


def test_teardown_removes_labelled_objects_and_is_verified():
    listing = iter(["c1\nc2", "n1\nn2", "", ""])
    docker = FakeDocker(
        {("container", "ls"): lambda a: next(listing), ("network", "ls"): lambda a: next(listing)}
    )
    KitBoundary(docker).teardown(RECORD)
    assert ("container", "rm", "--force", "--volumes", "c1", "c2") in docker.calls
    assert ("network", "rm", "n1", "n2") in docker.calls
    stuck = FakeDocker({("container", "ls"): "c1"})
    with pytest.raises(DeploymentError, match="could not be verified"):
        KitBoundary(stuck).teardown(RECORD)


# -- the deploy path ----------------------------------------------------------------------------

RUNNING = {"Id": "c", "State": {"Running": True}}
STOPPED = {"Id": "c", "State": {"Running": False}}


def _service(identity_env, state, runtime, **environment):
    grant(identity_env, **environment)
    service = DeploymentService(state, runtime, identity_env.load())
    service.kit = Mock()
    service.kit.endpoint.return_value = "http://127.0.0.1:4321"
    return service


def test_a_kit_manifest_deploys_behind_the_forwarder(identity_env, state, runtime):
    service = _service(identity_env, state, runtime, network="stack-agents")
    runtime.inspect.side_effect = [None, STOPPED, RUNNING]
    order = Mock()
    order.attach_mock(service.kit.start_sidecar, "sidecar")
    order.attach_mock(runtime.start, "agent")
    order.attach_mock(service.kit.verify, "verify")
    order.attach_mock(runtime.healthy, "healthy")
    result = service.deploy(kit_manifest(), local=True)
    assert result["state"] == "ready" and result["endpoint"] == "http://127.0.0.1:4321"
    runtime.create.assert_not_called()
    runtime.endpoint.assert_not_called()
    created = service.kit.create.call_args
    assert created.args[0]["id"] == result["id"]
    assert created.kwargs == {
        "environment": {
            "AGENTBREEDER_GATEWAY_URL": HOST_GATEWAY,
            "AGENTBREEDER_DEPLOYMENT_ID": result["id"],
        },
        "mounts": [],
        "agents_network": "stack-agents",
    }
    # Sidecar first, then the agent, and nothing is health-checked before verification.
    assert [call[0] for call in order.mock_calls] == ["sidecar", "agent", "verify", "healthy"]
    assert service.kit.verify.call_args.kwargs == {
        "gateway_url": HOST_GATEWAY,
        "agents_network": "stack-agents",
    }
    runtime.healthy.assert_called_once_with("http://127.0.0.1:4321", "/health")
    service.kit.teardown.assert_not_called()


def test_an_unverified_boundary_fails_the_deploy_and_leaves_nothing(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING]
    service.kit.verify.side_effect = BoundaryUnverified("Agent publishes ports")
    result = service.deploy(kit_manifest(), local=True)
    assert result["state"] == "failed"
    assert result["error"] == "[RUNTIME_BOUNDARY_UNVERIFIED] Agent publishes ports"
    service.kit.teardown.assert_called_once()
    runtime.healthy.assert_not_called()


def test_a_stopped_kit_container_is_recreated_never_started_as_found(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [STOPPED, RUNNING, RUNNING]
    result = service.deploy(kit_manifest(), local=True)
    assert result["state"] == "ready"
    runtime.remove.assert_called_once_with(STOPPED)
    service.kit.create.assert_called_once()
    runtime.start.assert_not_called()  # the scripted new container already reports running


def test_a_failed_create_is_torn_down(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None]
    service.kit.create.side_effect = DeploymentError("docker failed")
    service.kit.teardown.side_effect = DeploymentError("still there")  # reported, not raised
    result = service.deploy(kit_manifest(), local=True)
    assert result["state"] == "failed" and result["error"] == "docker failed"
    service.kit.teardown.assert_called_once()


def test_an_unhealthy_agent_behind_a_verified_boundary_stays_for_its_log(
    identity_env, state, runtime
):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING]
    runtime.healthy.return_value = False
    result = service.deploy(kit_manifest(), local=True)
    assert result["state"] == "failed" and "Health check failed" in result["error"]
    service.kit.teardown.assert_not_called()


def test_a_manifest_without_the_field_takes_the_existing_path(identity_env, state, runtime):
    service = _service(identity_env, state, runtime, network="stack-agents")
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING]
    result = service.deploy(plain_manifest(), local=True)
    assert result["state"] == "ready" and result["endpoint"] == "http://127.0.0.1:1234"
    assert runtime.create.call_args.kwargs == {
        "mounts": [],
        "environment": {
            "AGENTBREEDER_GATEWAY_URL": HOST_GATEWAY,
            "AGENTBREEDER_DEPLOYMENT_ID": result["id"],
        },
        "host_gateway": True,
        "agents_network": "stack-agents",
    }
    assert service.kit.mock_calls == []
    service.reconcile(actor("ops"))
    runtime.inspect.side_effect = [RUNNING, None]
    assert service.remove(result["id"], local=True)["state"] == "removed"
    assert service.kit.mock_calls == []


def test_remove_takes_the_forwarder_and_networks_too(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING, None]
    result = service.deploy(kit_manifest(), local=True)
    assert service.remove(result["id"], local=True)["state"] == "removed"
    runtime.remove.assert_called_once()
    assert service.kit.teardown.call_args.args[0]["id"] == result["id"]


def test_disable_stops_the_forwarder_too(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING, None]
    result = service.deploy(kit_manifest(), local=True)
    service.disable(result["id"], actor("admin"))
    assert state.get(result["id"])["state"] == "disabled"
    service.kit.teardown.assert_called_once()


def test_reconcile_reverifies_a_ready_kit_deployment(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING]
    result = service.deploy(kit_manifest(), local=True)
    service.kit.reset_mock()
    assert service.reconcile(actor("ops")) == [
        {"id": result["id"], "name": "sample", "compliance": "compliant"}
    ]
    service.kit.start_sidecar.assert_called_once()  # a stopped sidecar is started again
    service.kit.verify.assert_called_once()
    service.kit.teardown.assert_not_called()
    # A restarted sidecar publishes another loopback port: the record follows it.
    service.kit.endpoint.return_value = "http://127.0.0.1:5555"
    service.reconcile(actor("ops"))
    assert state.get(result["id"])["endpoint"] == "http://127.0.0.1:5555"
    assert state.get(result["id"])["state"] == "ready"

    service.kit.verify.side_effect = BoundaryUnverified("Agent has a host mapping")
    assert service.reconcile(actor("ops"))[0]["compliance"] == (
        "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"
    )
    service.kit.teardown.assert_called_once()
    record = state.get(result["id"])
    assert record["state"] == "failed"
    assert record["error"] == "[RUNTIME_BOUNDARY_UNVERIFIED] Agent has a host mapping"


def test_reconcile_with_the_engine_down_reports_and_changes_nothing(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING]
    result = service.deploy(kit_manifest(), local=True)
    service.kit.reset_mock()
    runtime.authorize.side_effect = DeploymentError("Cannot reach the Docker engine")
    assert service.reconcile(actor("ops"))[0]["compliance"] == (
        "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"
    )
    assert state.get(result["id"])["state"] == "ready"
    assert service.kit.mock_calls == []


def test_reconcile_reports_a_teardown_that_failed(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING]
    result = service.deploy(kit_manifest(), local=True)
    service.kit.start_sidecar.side_effect = DeploymentError("No such container")
    service.kit.teardown.side_effect = DeploymentError("still there")
    assert service.reconcile(actor("ops"))[0]["compliance"] == (
        "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED:cleanup_failed"
    )
    assert state.get(result["id"])["error"] == ("[RUNTIME_BOUNDARY_UNVERIFIED] No such container")


def test_reconcile_tears_down_an_interrupted_kit_deploy(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING]
    service.kit.verify.side_effect = KeyboardInterrupt  # not a DeploymentError: no cleanup
    with pytest.raises(KeyboardInterrupt):
        service.deploy(kit_manifest(), local=True)
    (record,) = state.active_all()
    assert record["state"] == "starting"
    service.kit.teardown.assert_not_called()
    assert service.reconcile(actor("ops")) == [
        {
            "id": record["id"],
            "name": "sample",
            "compliance": "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED",
        }
    ]
    service.kit.teardown.assert_called_once()
    record = state.get(record["id"])
    assert record["state"] == "failed"
    assert record["error"] == "[RUNTIME_BOUNDARY_UNVERIFIED] Interrupted before verification"


def test_reconcile_retries_the_teardown_of_a_failed_kit_deploy(identity_env, state, runtime):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING]
    service.kit.verify.side_effect = BoundaryUnverified("Agent publishes ports")
    service.kit.teardown.side_effect = DeploymentError("still there")
    result = service.deploy(kit_manifest(), local=True)
    assert result["state"] == "failed"
    assert service.reconcile(actor("ops"))[0]["compliance"] == (
        "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED:cleanup_failed"
    )
    service.kit.teardown.side_effect = None
    assert service.reconcile(actor("ops"))[0]["compliance"] == (
        "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"
    )
    assert service.kit.teardown.call_count == 3  # deploy, then each reconcile
    assert state.get(result["id"])["error"] == (
        "[RUNTIME_BOUNDARY_UNVERIFIED] Agent publishes ports"
    )


def test_remove_tears_down_kit_objects_when_the_manifest_cannot_be_read(
    identity_env, state, runtime
):
    service = _service(identity_env, state, runtime)
    runtime.inspect.side_effect = [None, RUNNING, RUNNING, RUNNING, None]
    result = service.deploy(kit_manifest(), local=True)
    service.kit.reset_mock()
    real = state.manifest

    def manifest(identifier):
        data = real(identifier)
        return {**data, "port": "not a port"} if identifier == result["id"] else data

    state.manifest = manifest
    assert service.remove(result["id"], local=True)["state"] == "removed"
    service.kit.teardown.assert_called_once()


def test_start_sidecar_starts_the_deployments_own_forwarder():
    docker = FakeDocker()
    KitBoundary(docker).start_sidecar({"id": "d1", "container": "agentbreeder-sample-d1"})
    assert docker.calls[-1] == ("container", "start", "agentbreeder-sample-d1-fwd")


# -- invoke -------------------------------------------------------------------------------------


def _stored(tmp_path, manifest: Manifest, endpoint: str) -> tuple[Store, dict]:
    store = Store(tmp_path / "invoke-state")
    identifier = "e" * 32
    store.create(identifier, manifest.model_dump(mode="json"), "sha256:" + "d" * 64, "local-test")
    store.transition(identifier, "ready", endpoint=endpoint)
    return store, store.get(identifier)


def test_invoke_checks_the_forwarders_port_for_a_kit_deployment(tmp_path):
    store, record = _stored(tmp_path, kit_manifest(), "http://127.0.0.1:49200")
    names = KitBoundary.names(record)
    labels = {"io.agentbreeder.deployment": record["id"], "io.agentbreeder.role": "forwarder"}
    sidecar = {
        "Config": {"Labels": labels},
        "State": {"Running": True},
        "NetworkSettings": {"Ports": {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49200"}]}},
    }
    docker = FakeDocker({("container", "inspect", names["sidecar"]): json.dumps([sidecar])})
    docker.authorize = Mock()
    docker.inspect = Mock(return_value=RUNNING)
    docker.endpoint = Mock()
    try:
        invoke.verify(docker, store, record)
        docker.endpoint.assert_not_called()
        docker.inspect.return_value = STOPPED
        with pytest.raises(invoke.InvokeError) as stopped:
            invoke.verify(docker, store, record)
        assert stopped.value.code == "CONTAINER_STOPPED"
        docker.inspect.return_value = RUNNING
        sidecar["NetworkSettings"]["Ports"]["8081/tcp"][0]["HostPort"] = "49300"
        docker.responses[("container", "inspect", names["sidecar"])] = json.dumps([sidecar])
        with pytest.raises(invoke.InvokeError) as moved:
            invoke.verify(docker, store, record)
        assert moved.value.code == "PORT_CHANGED"
    finally:
        store.close()


def test_invoke_is_unchanged_for_other_deployments(tmp_path):
    store, record = _stored(tmp_path, plain_manifest(), "http://127.0.0.1:1234")
    docker = Mock()
    docker.inspect.return_value = RUNNING
    docker.endpoint.return_value = "http://127.0.0.1:1234"
    try:
        invoke.verify(docker, store, record)
        docker.endpoint.assert_called_once()
        docker.command.assert_not_called()
    finally:
        store.close()


# -- Kubernetes: a default-deny NetworkPolicy ---------------------------------------------------

IMAGE = "registry.example.com/a@sha256:" + "a" * 64
BLOCKED = ("10.244.0.0/16", "172.31.0.6/32")
# sha256 of json.dumps(_objects(...), sort_keys=True) for the input below, taken from `main` at
# 3f13edb4, before F1b: a manifest without `runtime` gets byte-identical objects.
OBJECTS_GOLDEN = "8e3af3c307ff80914a7e931c3fb08eb5d82be199f8ba0c27110dee876972319a"


def _cluster_manifest(**overrides) -> Manifest:
    return Manifest.model_validate(
        v2_manifest_data(target="kubernetes", image=IMAGE, model_access=dict(ACCESS), **overrides)
    )


def _policy(objects: list[dict]) -> dict:
    return next(item for item in objects if item["kind"] == "NetworkPolicy")["spec"]


def test_objects_for_a_manifest_without_the_field_are_byte_identical():
    objects = kubernetes._objects(RECORD, _cluster_manifest(), "agents", {"A": "1"}, [], BLOCKED)
    encoded = json.dumps(objects, sort_keys=True).encode()
    assert hashlib.sha256(encoded).hexdigest() == OBJECTS_GOLDEN
    assert _policy(objects)["egress"][1] == {
        "to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": list(BLOCKED)}}]
    }


def test_kit_destinations_are_the_agents_network_minus_what_is_blocked():
    assert kubernetes.kit_destinations(("172.31.0.0/16",), BLOCKED) == [
        {"ipBlock": {"cidr": "172.31.0.0/16", "except": ["172.31.0.6/32"]}}
    ]
    assert kubernetes.kit_destinations(("172.31.0.0/16", "172.40.0.0/24"), ()) == [
        {"ipBlock": {"cidr": "172.31.0.0/16"}},
        {"ipBlock": {"cidr": "172.40.0.0/24"}},
    ]
    # A range a blocked range covers (or equals) is dropped, never allowed.
    assert kubernetes.kit_destinations(("172.31.0.0/16", "10.244.1.0/24"), BLOCKED) == [
        {"ipBlock": {"cidr": "172.31.0.0/16", "except": ["172.31.0.6/32"]}}
    ]
    for allowed, blocked in (
        ((), BLOCKED),
        (("10.244.0.0/16",), BLOCKED),
        (("172.31.0.6/16",), BLOCKED),  # host bits set: not a network
        (("172.31.0.0/16",), ("999.0.0.0/8",)),
    ):
        with pytest.raises(DeploymentError, match="KUBERNETES_KIT_EGRESS_UNCONFIGURED"):
            kubernetes.kit_destinations(allowed, blocked)


def _kube(identity_env, tmp_path, monkeypatch, **block):
    path = write_private(tmp_path / "kubeconfig", "apiVersion: v1\n")
    identity_env.config["environments"]["dev"] = {
        "targets": ["local", "kubernetes"],
        "kubernetes": {
            "kubeconfig": str(path),
            "context": "kind-x",
            "cluster_uid": "0" * 8 + "-0000-0000-0000-" + "0" * 12,
            "namespace": "agents",
            "registry": "localhost:5001/agentbreeder",
            "blocked_cidrs": list(BLOCKED),
            **block,
        },
    }
    sent = []

    def run(argv, **options):
        sent.append((argv, options.get("input")))
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(kubernetes.subprocess, "run", run)
    runtime = kubernetes.Kubernetes(identity_env.load())
    runtime.select("dev")
    return runtime, sent


def _sent_policy(sent) -> dict:
    document = next(json.loads(data) for argv, data in sent if "create" in argv)
    return _policy(document["items"])


def test_a_kit_deployment_on_the_cluster_reaches_only_dns_and_the_agents_network(
    identity_env, tmp_path, monkeypatch
):
    kube, sent = _kube(identity_env, tmp_path, monkeypatch, agents_cidrs=["172.31.0.0/16"])
    kube.create(RECORD, _cluster_manifest(runtime=dict(KIT)))
    policy = _sent_policy(sent)
    assert policy["policyTypes"] == ["Ingress", "Egress"] and policy["ingress"] == []
    dns, destinations = policy["egress"]
    assert dns["ports"] == [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]
    assert dns["to"][0]["podSelector"] == {"matchLabels": {"k8s-app": "kube-dns"}}
    assert destinations == {
        "to": [{"ipBlock": {"cidr": "172.31.0.0/16", "except": ["172.31.0.6/32"]}}]
    }
    assert "0.0.0.0/0" not in json.dumps(policy)


def test_the_same_cluster_gives_other_manifests_the_policy_they_had(
    identity_env, tmp_path, monkeypatch
):
    kube, sent = _kube(identity_env, tmp_path, monkeypatch, agents_cidrs=["172.31.0.0/16"])
    kube.create(RECORD, _cluster_manifest())
    assert _sent_policy(sent)["egress"][1] == {
        "to": [{"ipBlock": {"cidr": "0.0.0.0/0", "except": list(BLOCKED)}}]
    }


def test_a_kit_deployment_is_refused_without_the_agents_ranges(
    identity_env, tmp_path, monkeypatch, state, runtime
):
    kube, sent = _kube(identity_env, tmp_path, monkeypatch)
    with pytest.raises(DeploymentError, match="KUBERNETES_KIT_EGRESS_UNCONFIGURED"):
        kube.create(RECORD, _cluster_manifest(runtime=dict(KIT)))
    assert not any("create" in argv for argv, _ in sent)
    # The deploy path refuses it before the runtime is asked, and `preflight` sees the same.
    grant(identity_env, STACK_GATEWAY)
    service = DeploymentService(state, runtime, identity_env.load(), kubernetes=Mock())
    decision = service.preflight(_cluster_manifest(runtime=dict(KIT)), "dev", actor("dave"))
    assert not decision.allowed and decision.code == "KUBERNETES_KIT_EGRESS_UNCONFIGURED"
    assert service.preflight(_cluster_manifest(), "dev", actor("dave")).allowed
    identity_env.config["environments"]["dev"]["kubernetes"]["agents_cidrs"] = ["172.31.0.0/16"]
    service = DeploymentService(state, runtime, identity_env.load(), kubernetes=Mock())
    assert service.preflight(_cluster_manifest(runtime=dict(KIT)), "dev", actor("dave")).allowed
    # A pod has no route to a gateway on the host.
    grant(identity_env, HOST_GATEWAY)
    service = DeploymentService(state, runtime, identity_env.load(), kubernetes=Mock())
    decision = service.preflight(_cluster_manifest(runtime=dict(KIT)), "dev", actor("dave"))
    assert not decision.allowed and decision.code == "KIT_EGRESS_UNSUPPORTED"
    assert service.preflight(_cluster_manifest(), "dev", actor("dave")).allowed


@pytest.mark.parametrize(
    "ranges",
    [
        ["0.0.0.0/0"],
        ["10.0.0.0/7"],
        ["10.0.0.0/8"],
        ["172.31.0.6/16"],
        ["fd00::/64"],
        ["999.1.1.0/24"],
        ["x"],
        ["0.0.0.0/8"],
        ["127.0.0.0/16"],
        ["169.254.0.0/16"],
        ["224.0.0.0/16"],
        ["8.8.0.0/16"],
        ["240.0.0.0/16"],
        ["0.0.0.0/16"],
        ["0.1.0.0/16"],
    ],
)
def test_agents_cidrs_are_ipv4_networks_and_never_everything(
    identity_env, tmp_path, monkeypatch, ranges
):
    with pytest.raises(OperatorConfigError):
        _kube(identity_env, tmp_path, monkeypatch, agents_cidrs=ranges)


def test_too_many_agents_cidrs_are_refused(identity_env, tmp_path, monkeypatch):
    ranges = [f"172.{index}.0.0/16" for index in range(16, 25)]
    with pytest.raises(OperatorConfigError):
        _kube(identity_env, tmp_path, monkeypatch, agents_cidrs=ranges)


def test_cluster_up_records_the_agents_network_ranges(monkeypatch):
    calls = []

    def run(argv, *, stdin=None, timeout=120, what):
        calls.append(argv)
        return json.dumps(
            [{"Subnet": "172.31.0.0/16"}, {"Subnet": "fd00:1::/64"}, {"Subnet": "10.0.0.0/8"}, {}]
        )

    monkeypatch.setattr(cluster, "_run", run)
    assert cluster.agents_cidrs("stack-agents") == ["172.31.0.0/16"]
    assert calls == [
        ["docker", "network", "inspect", "stack-agents", "--format", "{{json .IPAM.Config}}"]
    ]
    monkeypatch.setattr(cluster, "_run", lambda argv, **options: "null")
    assert cluster.agents_cidrs("stack-agents") == []
