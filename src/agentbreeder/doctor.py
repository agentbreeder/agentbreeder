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
