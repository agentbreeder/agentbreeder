"""Opt-in real Docker run of `services up/down` with the built-in echo service (#738).

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_services_docker.py
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from test_services_engine import CANARY, PY, write_project

from agentbreeder import registry
from agentbreeder.services.engine import Engine
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300)
    if check and result.returncode:
        raise AssertionError(f"docker {args[0]} failed: {result.stderr[-500:]}")
    return result.stdout


@pytest.fixture
def projects(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", CANARY)
    first = write_project(tmp_path / "one")
    second = write_project(tmp_path / "two")
    engines = []

    def make(project: Path) -> Engine:
        instance = Engine(project)
        engines.append(instance)
        return instance

    yield first, second, make
    for instance in engines:
        try:
            instance.active_deployments = lambda: []
            instance.down(volumes=True)
        finally:
            instance.close()


def up(instance: Engine) -> dict:
    return instance.up("dev", confirm=lambda grant: True)


def test_echo_stack_lifecycle(projects):
    first, second, make = projects
    one = make(first)
    result = up(one)
    assert result["services"] == {"echo": "created", "gateway": "created"}
    assert up(one)["services"] == {"echo": "unchanged", "gateway": "unchanged"}

    # No secret in container config or labels.
    secrets = one.state.known_secrets()
    assert len(secrets) >= 4
    for name in ("echo", "gateway"):
        inspect = docker("inspect", f"{one.state.project}-{name}-1")
        for value in [*secrets, CANARY.encode()]:
            assert value.decode() not in inspect

    # The agents network reaches agents-exposed services; the default bridge stays for publish.
    agents = f"{one.state.project}-agents"
    probe = (
        "import urllib.request;"
        "print(urllib.request.urlopen('http://echo:8080/index.html', timeout=5).status)"
    )
    # Same order as runtime.create: create on the default bridge, connect, then start.
    agent = f"{one.state.project}-probe-agent"
    docker("create", "--name", agent, "-p", "127.0.0.1::8000", PY, "python3", "-c", probe)
    try:
        docker("network", "connect", agents, agent)
        assert docker("start", "--attach", agent).strip() == "200"
    finally:
        docker("rm", "--force", agent, check=False)

    # Host port on loopback, from the operator config's eval secret.
    status = one.status()
    port = status["services"]["echo"]["host_ports"]["http"]
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/index.html", timeout=5) as response:
        assert response.status == 200

    # down keeps data and secrets; up again recreates nothing new in the volume.
    minted = one.state.read_secret("echo", "minted")
    one.down()
    assert (
        docker("ps", "--all", "--quiet", "--filter", f"label=io.agentbreeder.stack={one.state.id}")
        == ""
    )
    assert up(one)["services"]["echo"] == "created"
    assert one.state.read_secret("echo", "minted") == minted  # same volume: no new bootstrap

    # A second checkout with the same stack name runs alongside.
    two = make(second)
    other = up(two)
    assert other["stack"] != result["stack"]
    assert two.status()["services"]["echo"]["state"] == "running"

    # down --volumes removes only this stack's volumes.
    removed = one.down(volumes=True)
    assert removed["volumes_removed"] == [f"{one.state.project}-echo-data"]
    volumes = json.loads(json.dumps(docker("volume", "ls", "--quiet").split()))
    assert f"{two.state.project}-echo-data" in volumes
