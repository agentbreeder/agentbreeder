"""#978: a started container's published port can be `[]` for a moment on Docker Desktop.
The deploy re-inspects for a bounded time before `endpoint` decides."""

from __future__ import annotations

import pytest
from conftest import v2_manifest_data

from agentbreeder import deployment
from agentbreeder.deployment import DeploymentService, _binding_pending
from agentbreeder.manifest import Manifest
from agentbreeder.runtime import Docker

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
    """A fake clock: `sleep` advances `monotonic`, and the sleeps are recorded."""
    now = [100.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(deployment.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(deployment.time, "sleep", sleep)
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
    assert _binding_pending(data, MANIFEST) is pending


def test_empty_binding_then_filled_deploys(identity_env, state, docker_endpoint, clock):
    runtime = docker_endpoint
    # create's inspect, then the post-start inspect (empty), then two re-inspects
    runtime.inspect.side_effect = [None, info({}, running=False), PENDING, PENDING, BOUND]
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "ready", result["error"]
    assert result["endpoint"] == "http://127.0.0.1:54597"
    assert clock == [deployment.BINDING_POLL_SECONDS] * 2
    runtime.start.assert_called_once()


def test_binding_never_filled_fails_after_the_cap(identity_env, state, docker_endpoint, clock):
    runtime = docker_endpoint
    runtime.inspect.side_effect = [None, info({}, running=False)] + [PENDING] * 100
    result = deploy(identity_env, state, runtime)
    assert result["state"] == "failed"
    assert "one loopback HTTP binding" in result["error"]
    assert sum(clock) == pytest.approx(deployment.BINDING_WAIT_SECONDS)
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
    assert clock == [deployment.BINDING_POLL_SECONDS]
