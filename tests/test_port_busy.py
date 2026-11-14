"""PORT_BUSY: a host port another process or container holds, named by number instead of a
DOCKER_FAILED stderr tail (#822). `services up|start|restart` (DockerCLI.run) and `deploy
--local` (runtime.Docker.command). Docker is faked with stderr in the formats Docker, Compose
and podman print; the real-Docker check is not run here."""

# Fixtures imported from the services tests are redefined as parameters on purpose.
# ruff: noqa: F811

from __future__ import annotations

import json
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_services_control import ControlDocker, docker, stack  # noqa: F401
from test_services_engine import env, fake_docker  # noqa: F401

from agentbreeder import cli
from agentbreeder.deployment import DeploymentService
from agentbreeder.runtime import Docker, PortBusy, busy_port, port_busy_message
from agentbreeder.services.compose import DockerCLI
from agentbreeder.services.schema import ServiceError
from agentbreeder.store import DeploymentError, Store

CANARY = "seeded-marker-" + "q" * 20

# What a failed bind looks like, per source. The text after the port is fixed in each tool;
# the prefix varies with the engine version.
SAMPLES = [
    pytest.param(
        " Container agentbreeder-ab12cd34-postgres-1  Starting\n"
        "Error response from daemon: driver failed programming external connectivity on "
        "endpoint agentbreeder-ab12cd34-postgres-1 (9f1c2b): Bind for 127.0.0.1:54321 failed: "
        "port is already allocated\n",
        54321,
        id="compose-allocator",
    ),
    pytest.param(
        "Error response from daemon: failed to set up container networking: driver failed "
        "programming external connectivity on endpoint web (abc): Bind for 0.0.0.0:8080 "
        "failed: port is already allocated",
        8080,
        id="engine-28",
    ),
    pytest.param("Bind for :::5432 failed: port is already allocated", 5432, id="ipv6-bare"),
    pytest.param("Bind for [::]:5432 failed: port is already allocated", 5432, id="ipv6-bracket"),
    pytest.param(
        "Error response from daemon: Ports are not available: exposing port TCP "
        "127.0.0.1:4000 -> 0.0.0.0:0: listen tcp 127.0.0.1:4000: bind: address already in use",
        4000,
        id="desktop-host-process",
    ),
    pytest.param(
        "Error response from daemon: driver failed programming external connectivity on "
        "endpoint web (deadbeef): Error starting userland proxy: listen tcp4 0.0.0.0:6006: "
        "bind: address already in use",
        6006,
        id="linux-userland-proxy",
    ),
    pytest.param(
        "Error: rootlessport listen tcp 0.0.0.0:8081: bind: address already in use",
        8081,
        id="podman-rootlessport",
    ),
    pytest.param(
        "Error: cannot listen on the TCP port: listen tcp4 :8082: bind: address already in use",
        8082,
        id="podman-no-host",
    ),
    pytest.param(
        "listen tcp6 [::1]:8083: bind: address already in use", 8083, id="listen-ipv6-bracket"
    ),
    pytest.param(
        "Error response from daemon: failed to set up container networking: driver failed "
        "programming external connectivity on endpoint web (abc): failed to bind host port for "
        "0.0.0.0:8084:172.17.0.2:80/tcp: address already in use",
        8084,
        id="engine-28-bind-host-port",
    ),
    pytest.param(
        "failed to bind host port for [::]:8085:[fd00::2]:80/tcp: address already in use",
        8085,
        id="engine-28-ipv6",
    ),
    pytest.param(
        "Bind for [fe80::1%eth0]:8086 failed: port is already allocated", 8086, id="ipv6-scoped"
    ),
]

NOT_BUSY = [
    "",
    "Error response from daemon: No such container: abc",
    "port is already allocated",
    "Bind for 0.0.0.0:99999 failed: port is already allocated",
    "Bind for 0.0.0.0:0 failed: port is already allocated",
    "listen tcp 127.0.0.1:4000: bind: permission denied",
]


@pytest.mark.parametrize("stderr,port", SAMPLES)
def test_busy_port_reads_each_format(stderr, port):
    assert busy_port(stderr) == port
    assert busy_port(stderr.encode()) == port


@pytest.mark.parametrize("stderr", NOT_BUSY)
def test_other_failures_are_not_port_busy(stderr):
    assert busy_port(stderr) is None
    assert busy_port(None) is None


@pytest.mark.parametrize(
    "crafted",
    [
        "Bind for " + "0" * 70_000,
        "failed to bind host port for " + "0:" * 35_000,
        "failed to bind host port for 0:1:" + "x" * 70_000,
        "failed to bind host port for 0:1: " * 2_000,
        "listen tcp " + ":" * 70_000,
        "listen tcp4 [::" * 5_000,
    ],
)
def test_crafted_stderr_is_fast(crafted):
    started = time.monotonic()
    assert busy_port(crafted) is None
    assert time.monotonic() - started < 1


def test_only_the_stderr_tail_is_searched():
    tail = "Bind for 127.0.0.1:5000 failed: port is already allocated"
    assert busy_port("x" * 200_000 + tail) == 5000
    assert busy_port(tail + "x" * 200_000) is None
    assert busy_port((tail + "x" * 200_000).encode()) is None


# -- services: DockerCLI.run ----------------------------------------------------------------------


def _result(code, out, err):
    return SimpleNamespace(returncode=code, stdout=out.encode(), stderr=err.encode())


@pytest.mark.parametrize("stderr,port", SAMPLES)
def test_docker_cli_raises_port_busy_with_only_the_number(stderr, port):
    docker = DockerCLI(run=lambda argv, **kw: _result(1, CANARY, stderr + CANARY))
    with pytest.raises(ServiceError) as raised:
        docker.run("compose", "up", what="starting service postgres", binds=True)
    assert raised.value.code == "PORT_BUSY"
    assert str(raised.value) == (
        f"[PORT_BUSY] port {port} is already in use on this machine (starting service "
        "postgres); stop what uses it, then run the command again"
    )
    assert CANARY not in str(raised.value)


def test_docker_cli_names_the_command_without_what():
    docker = DockerCLI(run=lambda argv, **kw: _result(1, "", SAMPLES[0].values[0]))
    with pytest.raises(ServiceError, match=r"\(docker compose\); stop"):
        docker.run("compose", "up", binds=True)


def test_a_call_that_binds_nothing_is_never_port_busy():
    # exec and one-shot runs forward the container's own stderr, which is not a host bind
    docker = DockerCLI(run=lambda argv, **kw: _result(1, "", SAMPLES[0].values[0]))
    with pytest.raises(ServiceError) as raised:
        docker.run("compose", "exec", "-T", "relay", what="probing relay")
    assert raised.value.code == "DOCKER_FAILED"


def test_compose_stop_is_never_port_busy(tmp_path):
    from agentbreeder.services.state import StackState

    (tmp_path / "p").mkdir()
    state = StackState("demo", tmp_path / "p", tmp_path / "stacks")
    try:
        with pytest.raises(ServiceError) as raised:
            _busy(SAMPLES[0].values[0]).control(state, "stop", "gateway")
        assert raised.value.code == "DOCKER_FAILED"
        with pytest.raises(ServiceError) as raised:
            _busy(SAMPLES[0].values[0]).control(state, "start", "gateway")
        assert raised.value.code == "PORT_BUSY"
    finally:
        state.close()


def test_docker_cli_other_failures_stay_docker_failed():
    docker = DockerCLI(run=lambda argv, **kw: _result(1, "", "no such image"))
    with pytest.raises(ServiceError) as raised:
        docker.run("compose", "up", what="starting service postgres")
    assert raised.value.code == "DOCKER_FAILED"


def _busy(stderr):
    return DockerCLI(run=lambda argv, **kw: _result(1, "", stderr))


def test_services_restart_json_is_the_port_busy_object(stack, docker, capsys, monkeypatch):
    busy = _busy(SAMPLES[0].values[0])
    monkeypatch.setattr(
        docker,
        "control",
        lambda state, verb, service, timeout=180: busy.control(state, verb, service),
    )
    assert cli.main(["services", "restart", str(stack), "gateway", "--json"]) == 2
    captured = capsys.readouterr()
    message = (
        "port 54321 is already in use on this machine (docker compose restart gateway); "
        "stop what uses it, then run the command again"
    )
    assert captured.err == f"[PORT_BUSY] {message}\n"
    assert json.loads(captured.out) == {
        "schema_version": 1,
        "error": {"code": "PORT_BUSY", "message": message},
    }


def test_services_start_without_json_prints_the_line_only(stack, docker, capsys, monkeypatch):
    busy = _busy(SAMPLES[4].values[0])
    monkeypatch.setattr(
        docker,
        "control",
        lambda state, verb, service, timeout=180: busy.control(state, verb, service),
    )
    assert cli.main(["services", "start", str(stack), "gateway"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("[PORT_BUSY] port 4000 is already in use on this machine")


def test_services_up_json_is_the_port_busy_object(env, fake_docker, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    busy = _busy(SAMPLES[1].values[0])
    monkeypatch.setattr(
        fake_docker, "up", lambda state, service, timeout=300: busy.up(state, service, timeout)
    )
    code = cli.main(["services", "up", str(env), "--environment", "dev", "--yes", "--json"])
    assert code == 2
    captured = capsys.readouterr()
    error = json.loads(captured.out)["error"]
    assert error["code"] == "PORT_BUSY"
    assert error["message"].startswith("port 8080 is already in use on this machine (starting")
    assert captured.err.splitlines()[-1] == f"[PORT_BUSY] {error['message']}"


# -- deploy --local: runtime.Docker.command ------------------------------------------------------


@pytest.mark.parametrize("stderr,port", SAMPLES)
def test_runtime_command_raises_port_busy(stderr, port, monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 125, "", (stderr + CANARY).encode()))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(PortBusy) as raised:
        Docker().command("container", "start", "abc")
    assert isinstance(raised.value, DeploymentError)
    assert raised.value.code == "PORT_BUSY" and raised.value.port == port
    assert str(raised.value) == f"[PORT_BUSY] {port_busy_message(port)}"
    assert CANARY not in str(raised.value)


@pytest.mark.parametrize("arguments", [("container", "create", "x"), ("logs", "x"), ("run", "x")])
def test_runtime_only_container_start_is_port_busy(arguments, monkeypatch):
    stderr = SAMPLES[0].values[0].encode()
    run = Mock(return_value=subprocess.CompletedProcess([], 125, "", stderr))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(DeploymentError, match="Docker rejected the operation"):
        Docker().command(*arguments)


def test_runtime_other_failures_keep_the_generic_message(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 125, "", b"no such image"))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(DeploymentError, match="Docker rejected the operation") as raised:
        Docker().command("container", "start", "abc")
    assert not isinstance(raised.value, PortBusy)


def test_deploy_records_port_busy_as_the_failure(tmp_path, v2_manifest):
    runtime = Mock(context="local-test")
    runtime.inspect.return_value = {"Id": "id", "State": {"Running": False}}
    runtime.start.side_effect = PortBusy(8080)
    store = Store(tmp_path / "state")
    try:
        result = DeploymentService(store, runtime).deploy(v2_manifest, local=True)
        assert result["state"] == "failed"
        error = store.get(result["id"])["events"][-1]["error"]
        assert error == f"[PORT_BUSY] {port_busy_message(8080)}"
    finally:
        store.close()
