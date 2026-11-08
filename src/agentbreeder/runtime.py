"""Bounded Docker operations. Uses only a pinned local context."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time

from agentbreeder.manifest import Manifest
from agentbreeder.store import DeploymentError


class Docker:
    def __init__(self):
        self.context = ""

    def command(
        self,
        *arguments: str,
        timeout: int = 30,
        stdin: bytes | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        """Run the docker CLI. Secrets go in `env` (inherited by the CLI and forwarded with a
        bare `--env NAME`), never in `arguments`: argv is visible to every local user."""
        prefix = ["docker"] + (["--context", self.context] if self.context else [])
        environment = {
            k: v
            for k, v in os.environ.items()
            if k not in {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}
        }
        environment.update(env or {})
        try:
            result = subprocess.run(
                [*prefix, *arguments],
                capture_output=True,
                input=stdin,
                timeout=timeout,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DeploymentError(
                "Docker operation failed or timed out; check docker context"
            ) from error
        if result.returncode:
            raise DeploymentError("Docker rejected the operation; check image, engine and context")
        output = result.stdout
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return output.strip()

    def authorize(self, context: str | None = None):
        # Explicit remote environment overrides are never silently redirected locally.
        if os.environ.get("DOCKER_HOST"):
            raise DeploymentError("Unset DOCKER_HOST; only local Docker contexts are supported")
        self.context = (
            context or os.environ.get("DOCKER_CONTEXT") or self.command("context", "show")
        )
        try:
            config = json.loads(self.command("context", "inspect", self.context))[0]
            host = config["Endpoints"]["docker"]["Host"]
            if not host.startswith("unix:///"):
                raise DeploymentError("Only a local Unix-socket Docker context is supported")
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise DeploymentError("Cannot verify a local Docker context") from error
        # `docker version` fails when the engine is unreachable; `docker info --format`
        # exits 0 in that case (Docker CLI 27), so it cannot prove the engine is up.
        if not self.command("version", "--format", "{{.Server.Version}}", timeout=10).strip():
            raise DeploymentError("Cannot reach the Docker engine for this context")

    def inspect(self, record: dict) -> dict | None:
        identifiers = self.command(
            "container",
            "ls",
            "--all",
            "--filter",
            "name=^/" + record["container"] + "$",
            "--format",
            "{{.ID}}",
        ).splitlines()
        if not identifiers:
            return None
        try:
            info = json.loads(self.command("container", "inspect", record["container"]))[0]
            labels = info["Config"]["Labels"] or {}
            if (
                labels.get("io.agentbreeder.deployment") != record["id"]
                or labels.get("io.agentbreeder.spec") != record["digest"]
            ):
                raise DeploymentError("Container ownership does not match; no changes made")
            return info
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise DeploymentError("Cannot verify container ownership") from error

    def create(
        self,
        record: dict,
        manifest: Manifest,
        *,
        network: str | None = None,
        environment: dict[str, str] | None = None,
        mounts=(),
    ):
        """Create the agent container. With `network` (runtime boundary) the agent joins only
        that internal network, publishes nothing and is reached through its sidecar."""
        if not manifest.image.startswith("sha256:"):
            self.command("image", "pull", manifest.image, timeout=120)
        try:
            image = json.loads(self.command("image", "inspect", manifest.image))[0]
            if image["Config"].get("Volumes"):
                raise DeploymentError(
                    "Images declaring VOLUME are unsupported; use a stateless image"
                )
        except DeploymentError:
            raise  # a specific reason (DeploymentError subclasses ValueError); keep it
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise DeploymentError("Cannot verify image configuration") from error
        self.command(
            "container",
            "create",
            "--name",
            record["container"],
            "--pull",
            "never",
            "--label",
            "io.agentbreeder.deployment=" + record["id"],
            "--label",
            "io.agentbreeder.spec=" + record["digest"],
            "--label",
            "io.agentbreeder.role=agent",
            *(
                ["--network", network, "--network-alias", "agent"]
                if network
                else ["--publish", f"127.0.0.1::{manifest.port}"]
            ),
            *[f"--env={key}={value}" for key, value in (environment or {}).items()],
            # Local secret files: read-only bind mounts; the value never enters env or labels.
            *[
                arg
                for mount in mounts
                for arg in (
                    "--mount",
                    f"type=bind,src={mount.source},dst={mount.target},readonly",
                    *[f"--env={env}_FILE={mount.target}" for env in mount.envs],
                )
            ],
            "--user",
            "65532:65532",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=10m",
            "--log-opt",
            "max-file=2",
            manifest.image,
            timeout=120,
        )

    def start(self, info: dict):
        self.command("container", "start", info["Id"])

    def remove(self, info: dict):
        self.command("container", "rm", "--force", "--volumes", info["Id"])

    def endpoint(self, info: dict, manifest: Manifest) -> str:
        try:
            bindings = info["NetworkSettings"]["Ports"][f"{manifest.port}/tcp"]
            if not info["State"]["Running"] or len(bindings) != 1:
                raise ValueError
            binding = bindings[0]
            port = binding["HostPort"]
            if binding["HostIp"] != "127.0.0.1" or not re.fullmatch(r"[0-9]{1,5}", port):
                raise ValueError
            if not 1 <= int(port) <= 65535:
                raise ValueError
            return f"http://127.0.0.1:{port}"
        except (ValueError, KeyError, TypeError) as error:
            raise DeploymentError(
                "Container is not running with one loopback HTTP binding"
            ) from error

    def healthy(self, endpoint: str, path: str, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        port = int(endpoint.rsplit(":", 1)[1])
        while time.monotonic() < deadline:
            probe_deadline = min(deadline, time.monotonic() + 2)
            try:
                with socket.create_connection(
                    ("127.0.0.1", port), timeout=max(0.001, probe_deadline - time.monotonic())
                ) as connection:
                    connection.sendall(
                        f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                        "Connection: close\r\n\r\n".encode("ascii")
                    )
                    response = b""
                    while b"\r\n\r\n" not in response and len(response) < 16384:
                        remaining = probe_deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        connection.settimeout(remaining)
                        chunk = connection.recv(min(1024, 16384 - len(response)))
                        if not chunk:
                            break
                        response += chunk
                    if b"\r\n\r\n" in response and re.fullmatch(
                        rb"HTTP/1\.[01] 200(?: [^\r\n]*)?", response.split(b"\r\n", 1)[0]
                    ):
                        return True
            except OSError:
                pass
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        return False
