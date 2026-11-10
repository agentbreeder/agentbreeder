"""Read-only local prerequisites. No installation or host configuration changes."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    message: str
    required: bool = True  # a failed optional check is reported but doesn't fail doctor
    info: bool = False  # informational: printed as INFO, never fails doctor


# Names that AgentBreeder 2.x (up to the v2.7.3 tag) wrote directly under ~/.agentbreeder. 3.x
# uses only local/, stacks/, operator.yaml and oidc-token there, so none of these collide
# (tests/test_legacy_notice.py scans the source). 2.x's infra-state.json and layout.json lived in
# each project's .agentbreeder/, not in the home directory. .env is 2.x's fallback secrets file.
LEGACY_MARKERS = (
    ".env",
    "builders/",
    "context.json",
    "docker-compose*.yml",
    "evals/",
    "providers.json",
    "providers.local.yaml",
    "quickstart-runtime.json",
    "registry/",
    "state.json",
    "workspace.yaml",
)


def check_legacy() -> list[Check]:
    """Report 2.x state in ~/.agentbreeder: names are lstat-checked, never read or followed."""
    try:
        base = Path.home() / ".agentbreeder"
        if not stat.S_ISDIR(base.lstat().st_mode):  # a symlinked home state dir is not followed
            return []
        found = []
        for marker in LEGACY_MARKERS:
            name = marker.rstrip("/")
            if "*" in name:  # report the pattern, never raw file names, to the terminal
                if next(base.glob(name), None) is not None:
                    found.append(marker)
            elif os.path.lexists(base / name):
                found.append(marker)
    except (OSError, RuntimeError):
        return []
    if not found:
        return []
    return [
        Check(
            "2.x state",
            True,
            f"2.x state found in ~/.agentbreeder ({', '.join(sorted(found))}); "
            "3.x does not read or change it.",
            required=False,
            info=True,
        )
    ]


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
