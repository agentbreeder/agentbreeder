"""Shared Docker client factory for the cloud deployers.

All three managed-cloud deployers (AWS ECS, GCP Cloud Run, Azure Container Apps)
build + push images with the Docker SDK. On multi-user machines and macOS Docker
Desktop, ``docker.from_env()`` defaults to ``/var/run/docker.sock`` — which may
symlink to a root-owned socket and fail with ``PermissionError(13)`` even though
the ``docker`` CLI (which uses the Desktop context) works fine. This factory
prefers the current user's Desktop socket so every deployer behaves identically.
"""

from __future__ import annotations

from typing import Any


def docker_client() -> Any:
    """Return a Docker SDK client that works on multi-user machines / Docker Desktop.

    Honors ``DOCKER_HOST``; otherwise prefers the current user's Docker Desktop
    socket (``~/.docker/run/docker.sock``) over ``/var/run/docker.sock``.

    Raises ``ImportError`` (with a pip hint) if the Docker SDK is not installed.
    """
    import os
    from pathlib import Path

    try:
        import docker
    except ImportError as e:  # pragma: no cover - exercised via deployer tests
        msg = "Docker SDK not installed. Run: pip install docker"
        raise ImportError(msg) from e

    if os.environ.get("DOCKER_HOST"):
        return docker.from_env()
    user_socket = Path.home() / ".docker" / "run" / "docker.sock"
    if user_socket.exists():
        return docker.DockerClient(base_url=f"unix://{user_socket}")
    return docker.from_env()
