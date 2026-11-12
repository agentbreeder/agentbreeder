"""Real Docker: `Docker.logs` reads a container by ID, stdout and stderr merged, timestamped.

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_logs_docker.py   (without --cov)
"""

from __future__ import annotations

import os
import subprocess
import uuid

import pytest

from agentbreeder.egress.boundary import SIDECAR_BASE
from agentbreeder.runtime import Docker

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)


def test_logs_merge_streams_by_container_id():
    docker = Docker()
    docker.authorize()
    name = f"agentbreeder-logs-test-{uuid.uuid4().hex[:8]}"
    script = "import sys; print('to-stdout', flush=True); print('to-stderr', file=sys.stderr)"
    container = docker.command(
        "container", "run", "--detach", "--name", name, SIDECAR_BASE, "python", "-c", script,
        timeout=300,
    )  # fmt: skip
    try:
        subprocess.run(["docker", "container", "wait", container], capture_output=True, timeout=60)
        info = {"Id": container}
        text, truncated = docker.logs(info, tail=10)
        lines = text.splitlines()
        assert not truncated and len(lines) == 2
        assert {line.split(" ", 1)[1] for line in lines} == {"to-stdout", "to-stderr"}
        assert all(line[:4].isdigit() and "T" in line.split(" ", 1)[0] for line in lines)
        only_last, _ = docker.logs(info, tail=1)
        assert len(only_last.splitlines()) == 1
    finally:
        docker.command("container", "rm", "--force", container)
