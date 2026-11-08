"""Read-only local prerequisites. No installation or host configuration changes."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    message: str
    required: bool = True  # a failed optional check is reported but doesn't fail doctor


def check_docker() -> list[Check]:
    docker = shutil.which("docker")
    if docker is None:
        return [Check("Docker", False, "Install a Docker-compatible CLI and start its engine.")]
    try:
        result = subprocess.run(
            # `docker version`, not `docker info`: `info --format` exits 0 even when the
            # engine is unreachable (Docker CLI 27), which would report a false pass.
            [docker, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [
            Check("Docker", False, "Engine check failed or timed out; check your Docker context.")
        ]
    if result.returncode != 0 or not result.stdout.strip():
        return [
            Check("Docker", False, "Cannot reach the engine; start it and check docker context.")
        ]
    return [Check("Docker", True, "Client and engine are reachable.")]


def check_compose() -> list[Check]:
    """`agentbreeder services` needs Docker Compose v2 with `up --wait`."""
    name = "Docker Compose (services)"
    docker = shutil.which("docker")
    if docker is None:
        return [Check(name, False, "Install Docker Compose v2 to use `services`.", False)]
    try:
        result = subprocess.run(
            [docker, "compose", "up", "--help"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [Check(name, False, "`docker compose` failed or timed out.", False)]
    if result.returncode != 0:
        return [Check(name, False, "Docker Compose v2 is not installed.", False)]
    if "--wait" not in result.stdout:
        return [Check(name, False, "Upgrade Docker Compose: `up --wait` is required.", False)]
    return [Check(name, True, "Compose v2 with `up --wait` is available.", False)]
