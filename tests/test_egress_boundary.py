"""Boundary orchestration against a scripted Docker: topology flags, verification failures,
teardown and decision-service process handling. The real-Docker test is the boundary evidence."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from unittest.mock import Mock

import pytest
from conftest import v2_manifest_data
from egress_support import enable_boundary, tool_capabilities

from agentbreeder.egress import boundary as boundary_module
from agentbreeder.egress.boundary import Boundary, BoundaryUnverified, sidecar_context
from agentbreeder.egress.host import egress_dir, write_private_json
from agentbreeder.manifest import Manifest
from agentbreeder.store import DeploymentError

URL = "https://mcp.example.com/mcp"
PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
RECORD = {"id": "d" * 32, "container": "agentbreeder-" + "d" * 32, "digest": "spec"}


class FakeDocker:
    def __init__(self, responses=None, system="Docker Desktop"):
        self.calls: list[tuple] = []
        self.envs: list[dict | None] = []
        self.responses = responses or {}
        self.system = system
        self.create = Mock()

    def command(self, *args, timeout=30, stdin=None, env=None):
        self.calls.append(args)
        self.envs.append(env)
        if args[:1] == ("info",):
            return self.system
        for prefix, value in self.responses.items():
            if args[: len(prefix)] == prefix:
                return value(args) if callable(value) else value
        return ""


@pytest.fixture
def operator(identity_env, monkeypatch):
    enable_boundary(identity_env, URL, PEM, monkeypatch)
    return identity_env.load()


def _manifest():
    return Manifest.model_validate(v2_manifest_data(capabilities=tool_capabilities(URL)))


def _boundary(tmp_path, operator, docker=None, spawn=None):
    state = tmp_path / "state"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    return Boundary(docker or FakeDocker(), state, operator, spawn=spawn or Mock())


def test_sidecar_context_is_deterministic_stdlib_code():
    first, digest = sidecar_context()
    second, again = sidecar_context()
    assert first == second and digest == again and digest.startswith("sha256:")
    names = tarfile.open(fileobj=io.BytesIO(first)).getnames()
    assert "Dockerfile" in names and "agentbreeder/egress/sidecar.py" in names
    assert "agentbreeder/egress/host.py" not in names  # host code never enters the image
    dockerfile = tarfile.open(fileobj=io.BytesIO(first)).extractfile("Dockerfile").read()
    assert boundary_module.SIDECAR_BASE.encode() in dockerfile
    assert b"@sha256:" in dockerfile and b"USER 65532:65532" in dockerfile


def test_image_reused_when_label_matches_else_built(tmp_path, operator):
    _, digest = sidecar_context()
    labelled = json.dumps([{"Config": {"Labels": {"io.agentbreeder.sidecar-code": digest}}}])
    docker = FakeDocker({("image", "inspect"): labelled})
    tag, code = _boundary(tmp_path, operator, docker).image()
    assert code == digest and tag.startswith("agentbreeder-sidecar:")
    assert not any(c[0] == "build" for c in docker.calls)
    stale = FakeDocker({("image", "inspect"): json.dumps([{"Config": {"Labels": {}}}])})
    _boundary(tmp_path, operator, stale).image()
    assert any(c[:2] == ("build", "--quiet") for c in stale.calls)


def test_pin_listen_modes(tmp_path, operator):
    desktop = _boundary(tmp_path, operator)
    token = desktop.write_pin(RECORD, {"revision": "r"})
    pin = desktop.pin(RECORD)
    assert pin["listen"] == "tcp" and isinstance(pin["port"], int) and token not in json.dumps(pin)
    assert desktop.sidecar_address(RECORD).startswith("http://host.docker.internal:")
    directory = egress_dir(desktop.state_dir, RECORD["id"])
    assert oct(directory.stat().st_mode & 0o777) == "0o700"
    assert oct((directory / "pin.json").stat().st_mode & 0o777) == "0o600"
    assert desktop.enforced(RECORD)

    (tmp_path / "l").mkdir()
    linux = _boundary(
        tmp_path / "l", operator, FakeDocker({("version",): "28.1.1"}, system="Ubuntu 24.04")
    )
    long_record = {**RECORD, "id": "x" * 90}
    with pytest.raises(DeploymentError, match="too long"):
        linux.write_pin(long_record, {"revision": "r"})


def test_create_builds_internal_topology_and_hardened_sidecar(tmp_path, operator):
    _, digest = sidecar_context()
    labelled = json.dumps([{"Config": {"Labels": {"io.agentbreeder.sidecar-code": digest}}}])
    docker = FakeDocker({("image", "inspect"): labelled})
    boundary = _boundary(tmp_path, operator, docker)
    token = boundary.write_pin(RECORD, {"revision": "r", "operator_digest": "o"})
    boundary.create(RECORD, _manifest(), token)
    networks = [c for c in docker.calls if c[:2] == ("network", "create")]
    assert "--internal" in networks[0] and networks[0][-1].endswith("-int")
    assert "--internal" not in networks[1] and networks[1][-1].endswith("-egress")
    sidecar = next(c for c in docker.calls if c[:2] == ("container", "create"))
    for flag in ("--read-only", "no-new-privileges", "65532:65532", "127.0.0.1::8081"):
        assert flag in sidecar
    assert sidecar[sidecar.index("--cap-drop") + 1] == "ALL"
    config = json.loads(
        next(a for a in sidecar if a.startswith("AGENTBREEDER_SIDECAR_CONFIG=")).split("=", 1)[1]
    )
    assert config["destinations"] == {
        "tools": {
            "url": URL,
            "allow_private": True,
            "ca_pem": PEM,
            "tools": ["delete", "lookup", "refund"],
        }
    }
    assert config["limits"] == {"max_body_bytes": 1048576, "calls_per_minute": 60}
    # The token reaches the docker CLI through its environment, never argv (visible to `ps`).
    assert "AGENTBREEDER_PEP_TOKEN" in sidecar
    assert not any(token in argument for call in docker.calls for argument in call)
    index = docker.calls.index(sidecar)
    assert docker.envs[index] == {"AGENTBREEDER_PEP_TOKEN": token}
    assert ("network", "connect", "--alias", "mcp-gateway.internal") == next(
        c for c in docker.calls if c[:2] == ("network", "connect")
    )[:4]
    kwargs = docker.create.call_args.kwargs
    assert kwargs["network"].endswith("-int")
    assert json.loads(kwargs["environment"]["AGENTBREEDER_MCP_SERVERS"]) == {
        "tools": "http://mcp-gateway.internal:8080/mcp/tools"
    }
    boundary.start(RECORD)
    assert docker.calls[-1] == ("container", "start", RECORD["container"])


def test_unix_mode_mounts_only_the_socket_directory(tmp_path, operator):
    _, digest = sidecar_context()
    labelled = json.dumps([{"Config": {"Labels": {"io.agentbreeder.sidecar-code": digest}}}])
    docker = FakeDocker({("image", "inspect"): labelled, ("version",): "28.1.1"}, system="Ubuntu")
    import shutil
    import tempfile
    from pathlib import Path

    short = Path(tempfile.mkdtemp(dir="/tmp"))  # AF_UNIX path limit
    try:
        boundary = Boundary(docker, short, operator, spawn=Mock())
        record = {**RECORD, "id": "short"}
        token = boundary.write_pin(record, {"revision": "r"})
        boundary.create(record, _manifest(), token)
        sidecar = next(c for c in docker.calls if c[:2] == ("container", "create"))
        volume = sidecar[sidecar.index("--volume") + 1]
        assert volume.endswith("/sock:/run/agentbreeder:ro")
        assert boundary.sidecar_address(record) == "unix:/run/agentbreeder/pep.sock"
    finally:
        shutil.rmtree(short)


def test_destination_ca_file_must_be_private(tmp_path, operator, identity_env):
    (identity_env.directory / "ca.pem").chmod(0o644)
    with pytest.raises(DeploymentError, match="ca_file"):
        _boundary(tmp_path, operator).destinations(_manifest())


# -- verification ----------------------------------------------------------------------------


def _world(**changes):
    _, digest = sidecar_context()
    names = Boundary.names(RECORD)
    labels = {"io.agentbreeder.deployment": RECORD["id"]}
    world = {
        "network": {
            "Internal": True,
            "Labels": dict(labels),
            "Containers": {"agent-id": {}, "pep-id": {}},
            "IPAM": {"Config": [{"Gateway": "172.30.0.1"}]},
        },
        "agent": {
            "Id": "agent-id",
            "Config": {"Labels": {**labels, "io.agentbreeder.role": "agent"}},
            "State": {"Running": True},
            "HostConfig": {"NetworkMode": names["internal"], "CapDrop": ["ALL"]},
            "NetworkSettings": {"Networks": {names["internal"]: {}}},
        },
        "sidecar": {
            "Id": "pep-id",
            "Image": "sha256:img",
            "Config": {"Labels": {**labels, "io.agentbreeder.role": "sidecar"}},
            "State": {"Running": True},
            "HostConfig": {},
            "NetworkSettings": {
                "Networks": {names["internal"]: {}, names["egress"]: {}},
                "Ports": {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49200"}]},
            },
        },
        "image": {"Config": {"Labels": {"io.agentbreeder.sidecar-code": digest}}},
        "probe": {
            "tcp_open": [],
            "dns_resolved": [],
            "gateway": {
                "status": 200,
                "body": {
                    "deployment": RECORD["id"],
                    "code_digest": digest,
                    "host": {"revision": "r", "operator_config_digest": "o"},
                },
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


def _scripted(world):
    names = Boundary.names(RECORD)

    def inspect(args):
        name = args[-1]
        if args[0] == "network":
            return json.dumps([world["network"]])
        if args[0] == "image":
            return json.dumps([world["image"]])
        return json.dumps([world["agent" if name == RECORD["container"] else "sidecar"]])

    return FakeDocker(
        {
            ("network", "inspect"): inspect,
            ("container", "inspect"): inspect,
            ("image", "inspect", names["sidecar"]): inspect,
            ("image", "inspect", "sha256:img"): inspect,
            ("image", "inspect"): json.dumps([world["image"]]),
            ("container", "run"): "noise\n" + json.dumps(world["probe"]),
            ("version",): "28.1.1",
        }
    )


def _verifier(tmp_path, operator, world, listen="tcp"):
    docker = _scripted(world)
    boundary = _boundary(tmp_path, operator, docker)
    directory = egress_dir(boundary.state_dir, RECORD["id"])
    directory.mkdir(parents=True, mode=0o700)
    pin = {"revision": "r", "operator_digest": "o", "listen": listen}
    if listen == "tcp":
        pin["port"] = 49311
    write_private_json(directory / "pin.json", pin)
    return boundary, docker


def test_verify_passes_and_probes_from_agent_namespace(tmp_path, operator):
    boundary, docker = _verifier(tmp_path, operator, _world())
    assert boundary.verify(RECORD)["tcp_open"] == []
    probe = next(c for c in docker.calls if c[:2] == ("container", "run"))
    assert probe[probe.index("--network") + 1] == "container:" + RECORD["container"]
    plan = json.loads(probe[-1])
    assert ["172.30.0.1", 2375] in plan["tcp"] and ["1.1.1.1", 443] in plan["tcp"]
    # The agent must not reach the decision service directly either (sidecar-only).
    assert ["172.30.0.1", 49311] in plan["tcp"]
    assert "example.com" in plan["dns_must_fail"]
    assert boundary.endpoint(RECORD) == "http://127.0.0.1:49200"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"network.Internal": False}, "not internal"),
        ({"network.Labels": {}}, "ownership"),
        ({"network.Containers": {"agent-id": {}, "pep-id": {}, "x": {}}}, "unexpected members"),
        ({"agent.Config": {"Labels": {}}}, "agent ownership"),
        ({"agent.HostConfig": {"Privileged": True}}, "privileged"),
        ({"sidecar.State": {"Running": False}}, "not running"),
        ({"agent.NetworkSettings": {"Networks": {"bridge": {}}}}, "other than its boundary"),
        (
            {
                "agent.HostConfig": {
                    "NetworkMode": Boundary.names(RECORD)["internal"],
                    "CapDrop": ["ALL"],
                    "PortBindings": {"8080/tcp": [{}]},
                }
            },
            "publishes ports",
        ),
        (
            {"agent.HostConfig": {"NetworkMode": Boundary.names(RECORD)["internal"]}},
            "capabilities",
        ),
        ({"sidecar.NetworkSettings": {"Networks": {}}}, "Sidecar networks"),
        ({"image.Config": {"Labels": {}}}, "expected code"),
        ({"probe.tcp_open": [["1.1.1.1", 443]]}, "outside its boundary"),
        ({"probe.dns_resolved": ["example.com"]}, "resolved"),
        ({"probe.gateway": {"status": None}}, "Gateway health"),
    ],
)
def test_verify_fails_closed(tmp_path, operator, changes, message):
    boundary, _ = _verifier(tmp_path, operator, _world(**changes))
    with pytest.raises(BoundaryUnverified, match=message):
        boundary.verify(RECORD)


def test_verify_rejects_unparseable_inspect_and_probe(tmp_path, operator):
    boundary, docker = _verifier(tmp_path, operator, _world())
    docker.responses[("container", "run")] = "not json"
    with pytest.raises(BoundaryUnverified, match="no report"):
        boundary.verify(RECORD)
    docker.responses[("network", "inspect")] = "[]"
    with pytest.raises(BoundaryUnverified, match="Cannot inspect"):
        boundary.verify(RECORD)


def test_endpoint_requires_single_loopback_binding(tmp_path, operator):
    world = _world()
    world["sidecar"]["NetworkSettings"]["Ports"] = {
        "8081/tcp": [{"HostIp": "0.0.0.0", "HostPort": "1"}]
    }
    boundary, _ = _verifier(tmp_path, operator, world)
    with pytest.raises(DeploymentError, match="loopback"):
        boundary.endpoint(RECORD)


# -- teardown and process handling -----------------------------------------------------------


def test_teardown_removes_labelled_resources_and_verifies(tmp_path, operator):
    remaining = {"container": ["c1\n", ""], "network": ["n1\n", ""]}

    def listing(kind):
        return lambda args: remaining[kind].pop(0) if remaining[kind] else ""

    docker = FakeDocker(
        {("container", "ls"): listing("container"), ("network", "ls"): listing("network")}
    )
    boundary = _boundary(tmp_path, operator, docker)
    boundary.teardown(RECORD)
    assert ("container", "rm", "--force", "--volumes", "c1") in docker.calls
    assert ("network", "rm", "n1") in docker.calls
    stuck = FakeDocker({("container", "ls"): "c1"})
    with pytest.raises(DeploymentError, match="could not be verified"):
        _boundary(tmp_path, operator, stuck).teardown(RECORD)


def test_service_start_waits_for_healthy_endpoint(tmp_path, operator, monkeypatch):
    boundary = _boundary(tmp_path, operator)
    boundary.write_pin(RECORD, {"revision": "r"})
    directory = egress_dir(boundary.state_dir, RECORD["id"])

    def spawn(command, **kwargs):
        assert command[1:3] == ["-m", "agentbreeder.egress.host"]
        assert kwargs["start_new_session"] and "PYTHONPATH" in kwargs["env"]
        write_private_json(directory / "endpoint.json", {"pid": 1, "address": "tcp:1"})

    boundary.spawn = spawn
    monkeypatch.setattr(boundary, "service_healthy", lambda record: True)
    boundary.start_service(RECORD)
    monkeypatch.setattr(boundary, "service_healthy", lambda record: False)
    with pytest.raises(DeploymentError, match="did not start"):
        boundary.start_service(RECORD, timeout=0.3)


def test_service_pid_requires_matching_command(tmp_path, operator, monkeypatch):
    boundary = _boundary(tmp_path, operator)
    boundary.write_pin(RECORD, {"revision": "r"})
    directory = egress_dir(boundary.state_dir, RECORD["id"])
    assert boundary.service_pid(RECORD) is None
    write_private_json(directory / "endpoint.json", {"pid": os.getpid(), "address": "tcp:1"})
    assert boundary.service_pid(RECORD) is None  # pytest is not the decision service
    run = Mock(return_value=Mock(stdout=f"python -m agentbreeder.egress.host {RECORD['id']}"))
    monkeypatch.setattr(boundary_module.subprocess, "run", run)
    assert boundary.service_pid(RECORD) == os.getpid()
    killed = []
    monkeypatch.setattr(boundary_module.os, "kill", lambda pid, sig: killed.append(pid))
    boundary.stop_service(RECORD)
    assert killed == [os.getpid()] and not (directory / "endpoint.json").exists()


def test_service_healthy_requires_authenticated_reply_and_live_pid(
    tmp_path, operator, monkeypatch
):
    """A process squatting the loopback port (any local user) must not pass for the service:
    reconcile would otherwise skip the restart and verify against it."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from agentbreeder.egress.protocol import MAC_HEADER, NONCE_HEADER, response_mac

    key = hashlib.sha256(b"tok").hexdigest()
    seen = {"key": key}

    class Health(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            data = json.dumps({"revision": "r"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            if seen["key"]:
                nonce = self.headers[NONCE_HEADER]
                self.send_header(MAC_HEADER, response_mac(seen["key"], nonce, 200, data))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    boundary = _boundary(tmp_path, operator)
    monkeypatch.setattr(boundary, "service_pid", lambda record: 4242)
    directory = egress_dir(boundary.state_dir, RECORD["id"])
    directory.mkdir(parents=True, mode=0o700)
    port = server.server_address[1]

    def pin(**extra):
        write_private_json(
            directory / "pin.json",
            {"listen": "tcp", "port": port, "revision": "r", "token_sha256": key, **extra},
        )

    try:
        pin()
        assert boundary.service_healthy(RECORD)
        pin(revision="other")
        assert not boundary.service_healthy(RECORD)
        pin()
        seen["key"] = None  # squatter: right revision, no MAC
        assert not boundary.service_healthy(RECORD)
        seen["key"] = hashlib.sha256(b"guess").hexdigest()  # squatter: wrong key
        assert not boundary.service_healthy(RECORD)
        seen["key"] = key
        monkeypatch.setattr(boundary, "service_pid", lambda record: None)  # our process died
        assert not boundary.service_healthy(RECORD)
        write_private_json(directory / "pin.json", {"listen": "unix", "revision": "r"})
        assert not boundary.service_healthy(RECORD)
    finally:
        server.shutdown()


# -- Codex review regressions ----------------------------------------------------------------


def test_linux_internal_network_isolates_the_host_gateway(tmp_path, operator):
    import shutil
    import tempfile
    from pathlib import Path

    _, digest = sidecar_context()
    labelled = json.dumps([{"Config": {"Labels": {"io.agentbreeder.sidecar-code": digest}}}])
    docker = FakeDocker({("image", "inspect"): labelled, ("version",): "28.1.1"}, system="Ubuntu")
    short = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        boundary = Boundary(docker, short, operator, spawn=Mock())
        record = {**RECORD, "id": "short"}
        boundary.create(record, _manifest(), boundary.write_pin(record, {"revision": "r"}))
        internal = next(c for c in docker.calls if c[:2] == ("network", "create"))
        assert "--opt=com.docker.network.bridge.gateway_mode_ipv4=isolated" in internal
        assert "--opt=com.docker.network.bridge.gateway_mode_ipv6=isolated" in internal
    finally:
        shutil.rmtree(short)


def test_linux_verify_requires_isolated_gateway(tmp_path, operator):
    boundary, _ = _verifier(tmp_path, operator, _world(), listen="unix")
    with pytest.raises(BoundaryUnverified, match="not isolated"):
        boundary.verify(RECORD)
    options = dict(o.split("=", 1) for o in boundary_module.ISOLATED_GATEWAY)
    boundary, _ = _verifier(
        tmp_path / "ok", operator, _world(**{"network.Options": options}), listen="unix"
    )
    assert boundary.verify(RECORD)["tcp_open"] == []


def test_serving_latch_and_pin_removed_after_verified_teardown(tmp_path, operator):
    boundary = _boundary(tmp_path, operator)
    boundary.write_pin(RECORD, {"revision": "r"})
    boundary.set_serving(RECORD, True)
    assert boundary.pin(RECORD)["serving"] is True
    boundary.teardown(RECORD)
    assert not boundary.enforced(RECORD)


@pytest.mark.parametrize(("version", "ok"), [("28.1.1", True), ("27.5.1", False), ("dev", False)])
def test_linux_mode_requires_engine_28(tmp_path, operator, version, ok):
    import shutil
    import tempfile
    from pathlib import Path

    docker = FakeDocker({("version",): version}, system="Ubuntu")
    short = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        boundary = Boundary(docker, short, operator, spawn=Mock())
        record = {**RECORD, "id": "short"}
        if ok:
            boundary.write_pin(record, {"revision": "r"})
            assert boundary.pin(record)["listen"] == "unix"
        else:
            with pytest.raises(DeploymentError, match="Docker Engine"):
                boundary.write_pin(record, {"revision": "r"})
    finally:
        shutil.rmtree(short)


def test_verify_rechecks_engine_version_in_linux_mode(tmp_path, operator):
    options = dict(o.split("=", 1) for o in boundary_module.ISOLATED_GATEWAY)
    boundary, docker = _verifier(
        tmp_path, operator, _world(**{"network.Options": options}), listen="unix"
    )
    docker.responses[("version",)] = "27.5.1"
    with pytest.raises(BoundaryUnverified, match="Docker Engine 28"):
        boundary.verify(RECORD)
