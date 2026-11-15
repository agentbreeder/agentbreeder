"""#978, #991: a started container's published port can be `[]` for a moment on Docker Desktop.
The deploy re-inspects for a bounded time before `endpoint` decides, for the agent container
and for the egress sidecar's ingress port."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import Mock

import pytest
from conftest import v2_manifest_data

from agentbreeder import runtime as runtime_module
from agentbreeder.deployment import DeploymentService
from agentbreeder.egress.boundary import Boundary, BoundaryUnverified
from agentbreeder.manifest import Manifest
from agentbreeder.runtime import Docker, binding_pending
from agentbreeder.store import DeploymentError

RECORD = {"id": "d" * 32, "container": "agentbreeder-" + "d" * 32, "digest": "spec"}
MANIFEST = Manifest.model_validate(v2_manifest_data())


def info(ports, running=True):
    data = {"Id": "container-id", "State": {"Running": running}}
    if ports is not ...:
        data["NetworkSettings"] = {"Ports": ports}
    return data


PENDING = info({"8080/tcp": []})
BOUND = info({"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "54597"}]})


@pytest.fixture
def clock(monkeypatch):
    """A fake clock for this test's thread: `sleep` advances `monotonic`, and the sleeps are
    recorded. `time` is process-wide, so other threads (left over from earlier tests) keep the
    real clock and are not recorded."""
    now = [100.0]
    sleeps = []
    owner = threading.get_ident()
    real_sleep, real_monotonic = time.sleep, time.monotonic

    def sleep(seconds):
        if threading.get_ident() != owner:
            return real_sleep(seconds)
        sleeps.append(seconds)
        now[0] += seconds

    def monotonic():
        return now[0] if threading.get_ident() == owner else real_monotonic()

    monkeypatch.setattr(runtime_module.time, "monotonic", monotonic)
    monkeypatch.setattr(runtime_module.time, "sleep", sleep)
    return sleeps


@pytest.fixture
def docker_endpoint(runtime):
    """The fake runtime judges bindings with the real Docker rule."""
    runtime.endpoint.side_effect = lambda data, manifest: Docker.endpoint(None, data, manifest)
    return runtime


def deploy(env, state, runtime, **overrides):
    service = DeploymentService(state, runtime, env.load())
    return service.deploy(Manifest.model_validate(v2_manifest_data(**overrides)), local=True)


@pytest.mark.parametrize(
    "data, pending",
    [
        (info({"8080/tcp": []}), True),
        (info({"8080/tcp": None}), True),
        (info({}), True),
        (info(None), True),
        (BOUND, False),
        (info({"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "1"}]}), False),
        (info({"8080/tcp": []}, running=False), False),
        (info(...), False),  # no NetworkSettings: not Docker's shape, leave it to endpoint
        ({"Id": "x"}, False),
        (info({"8080/tcp": []}, running="yes"), False),
    ],
)
def test_binding_pending(data, pending):
    assert binding_pending(data, MANIFEST.port) is pending


def test_empty_binding_then_filled_deploys(identity_env, state, docker_endpoint, clock):
    runtime = docker_endpoint
    # create's inspect, then the post-start inspect (empty), then two re-inspects
    runtime.inspect.side_effect = [None, info({}, running=False), PENDING, PENDING, BOUND]
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "ready", result["error"]
    assert result["endpoint"] == "http://127.0.0.1:54597"
    assert clock == [runtime_module.BINDING_POLL_SECONDS] * 2
    runtime.start.assert_called_once()


def test_binding_never_filled_fails_after_the_cap(identity_env, state, docker_endpoint, clock):
    runtime = docker_endpoint
    runtime.inspect.side_effect = [None, info({}, running=False)] + [PENDING] * 100
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "failed"
    assert "one loopback HTTP binding" in result["error"]
    assert sum(clock) == pytest.approx(runtime_module.BINDING_WAIT_SECONDS)
    runtime.healthy.assert_not_called()


def test_non_loopback_binding_fails_without_waiting(identity_env, state, docker_endpoint, clock):
    runtime = docker_endpoint
    wrong = info({"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "54597"}]})
    runtime.inspect.side_effect = [None, info({}, running=False), wrong]
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "failed"
    assert "one loopback HTTP binding" in result["error"]
    assert clock == []


def test_stopped_container_fails_without_waiting(identity_env, state, docker_endpoint, clock):
    runtime = docker_endpoint
    stopped = info({"8080/tcp": []}, running=False)
    runtime.inspect.side_effect = [None, info({}, running=False), stopped]
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "failed"
    assert clock == []


def test_container_gone_while_waiting_fails(identity_env, state, docker_endpoint, clock):
    runtime = docker_endpoint
    runtime.inspect.side_effect = [None, info({}, running=False), PENDING, None]
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "failed"
    assert "disappeared before health verification" in result["error"]
    assert clock == [runtime_module.BINDING_POLL_SECONDS]


# -- #991: the egress sidecar's ingress binding, read by Boundary.endpoint ------------------


class SidecarDocker:
    """Answers `container inspect` for the sidecar from a script of inspect results."""

    def __init__(self, script):
        self.script = list(script)
        self.inspects = 0

    def command(self, *args, **_):
        assert args[:2] == ("container", "inspect"), args
        self.inspects += 1
        return self.script.pop(0)


def sidecar(ports, running=True):
    return json.dumps([info(ports, running)])


SIDECAR_PENDING = sidecar({"8081/tcp": []})
SIDECAR_BOUND = sidecar({"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49200"}]})


def make_boundary(tmp_path, script):
    docker = SidecarDocker(script)
    boundary = Boundary(docker, tmp_path, operator=Mock(), spawn=Mock())
    return boundary, docker


def test_sidecar_empty_binding_then_filled(tmp_path, clock):
    boundary, docker = make_boundary(
        tmp_path, [SIDECAR_PENDING, sidecar({"8081/tcp": None}), sidecar({}), SIDECAR_BOUND]
    )
    assert boundary.endpoint(RECORD) == "http://127.0.0.1:49200"
    assert docker.inspects == 4
    assert clock == [runtime_module.BINDING_POLL_SECONDS] * 3


def test_sidecar_binding_never_filled_fails_after_the_cap(tmp_path, clock):
    boundary, docker = make_boundary(tmp_path, [SIDECAR_PENDING] * 100)
    with pytest.raises(DeploymentError, match="no single loopback ingress binding"):
        boundary.endpoint(RECORD)
    assert sum(clock) == pytest.approx(runtime_module.BINDING_WAIT_SECONDS)
    assert docker.inspects == 1 + len(clock)


def test_sidecar_stopped_fails_without_waiting(tmp_path, clock):
    boundary, docker = make_boundary(tmp_path, [sidecar({"8081/tcp": []}, running=False)])
    with pytest.raises(DeploymentError, match="no single loopback ingress binding"):
        boundary.endpoint(RECORD)
    assert clock == [] and docker.inspects == 1


def test_sidecar_non_loopback_fails_without_waiting(tmp_path, clock):
    wrong = sidecar({"8081/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49200"}]})
    boundary, docker = make_boundary(tmp_path, [wrong])
    with pytest.raises(DeploymentError, match="no single loopback ingress binding"):
        boundary.endpoint(RECORD)
    assert clock == [] and docker.inspects == 1


def test_sidecar_gone_while_waiting_fails_closed(tmp_path, clock):
    boundary, _ = make_boundary(tmp_path, [SIDECAR_PENDING, "[]"])
    with pytest.raises(BoundaryUnverified, match="Cannot inspect container"):
        boundary.endpoint(RECORD)
    assert clock == [runtime_module.BINDING_POLL_SECONDS]
