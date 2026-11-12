"""Bounded Docker operations. Uses only a pinned local context."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time

from agentbreeder.manifest import Manifest
from agentbreeder.store import DeploymentError

LOG_LIMIT = 4 * 1024 * 1024  # bytes of `docker logs` output read at most
MAX_TAIL = 10000
SINCE = re.compile(r"[1-9][0-9]{0,5}[smh]|[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
# Docker stores a line longer than PARTIAL bytes as several partial records, and
# `docker logs --timestamps` prints each record's timestamp in front of it, mid-line. A value
# straddling that boundary would escape redaction, so `join_partials` removes those timestamps.
PARTIAL = 16 * 1024
_STAMP = re.compile(
    rb"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    rb"(\.[0-9]{1,9})?(Z|[+-][0-9]{2}:[0-9]{2}) "
)


def join_partials(line: bytes) -> bytes:
    """One `docker logs --timestamps` line with the timestamps of its continuation records
    removed: after the leading timestamp, a timestamp that starts exactly PARTIAL payload bytes
    after the previous one (with no newline between) begins a continuation of the same line.
    A line without a leading timestamp is returned as it is."""
    first = _STAMP.match(line)
    if first is None:
        return line
    out = bytearray(line[: first.end()])
    rest = line[first.end() :]
    while len(rest) > PARTIAL:
        continuation = _STAMP.match(rest, PARTIAL)
        if continuation is None:
            break
        out += rest[:PARTIAL]
        rest = rest[continuation.end() :]
    out += rest
    return bytes(out)


def _read_tail(process, limit: int, timeout: float) -> tuple[bytes, bool] | None:
    """The last `limit` bytes of the child's stdout (read to the end, keeping only a bounded
    buffer) and whether earlier output was dropped; None when it does not finish in time."""
    kept = bytearray()
    dropped = [False]

    def read() -> None:
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                return
            kept.extend(chunk)
            if len(kept) > 2 * limit:
                del kept[: len(kept) - limit]
                dropped[0] = True

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        process.kill()
        reader.join(5)
        return None
    if len(kept) > limit:
        del kept[: len(kept) - limit]
        dropped[0] = True
    return bytes(kept), dropped[0]


def stop_follow(process: subprocess.Popen, grace: float = 2.0) -> None:
    """Stop a `Docker.follow` child: SIGTERM, then SIGKILL after `grace` seconds. Signals go
    through Popen, which never signals a child it has already reaped (no reused pid). Safe to
    call more than once or after it ended."""
    for stop, wait in ((process.terminate, grace), (process.kill, 5.0)):
        if process.poll() is not None:
            break
        try:
            stop()
            process.wait(wait)
        except subprocess.TimeoutExpired:
            continue
        except OSError:
            break
    if process.stdout is not None:
        process.stdout.close()


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
        environment = self._environment()
        environment.update(env or {})
        try:
            result = subprocess.run(
                [*self._prefix(), *arguments],
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

    def _prefix(self) -> list[str]:
        return ["docker"] + (["--context", self.context] if self.context else [])

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            k: v
            for k, v in os.environ.items()
            if k not in {"DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"}
        }

    def logs(self, info: dict, tail: int = 500, since: str | None = None) -> tuple[str, bool]:
        """A container's recent output, stdout and stderr merged, each line timestamped.
        `info` must come from `inspect(record)` after `authorize(record["context"])`, so the
        container is addressed by the ID whose ownership labels were checked. At most
        the newest LOG_LIMIT bytes are kept (whole lines); returns (text, truncated). The text
        is not redacted. A failing `docker logs` raises instead of returning its error."""
        argv = self._log_argv(info, tail, since, follow=False)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=self._environment(),
            )
        except OSError as error:
            raise DeploymentError("Docker operation failed; check docker context") from error
        try:
            result = _read_tail(process, LOG_LIMIT, timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        if result is None:
            raise DeploymentError("Reading the container's logs timed out")
        if process.returncode:
            # e.g. "No such container": Docker's own error, never the container's output.
            raise DeploymentError("Docker could not read the container's logs")
        data, truncated = result
        if truncated:
            # The newest output is kept; a line cut at the front is dropped whole, so a value
            # in it cannot leak as an unredactable fragment.
            data = data[data.find(b"\n") + 1 :] if b"\n" in data else b""
        # Long lines come back as timestamped partial records: join them, so redaction sees
        # each line whole (a value across a 16 KiB boundary is still found).
        data = b"\n".join(join_partials(line) for line in data.split(b"\n"))
        return data.decode("utf-8", "replace"), truncated

    def follow(self, info: dict, tail: int = 200, since: str | None = None) -> subprocess.Popen:
        """`docker container logs --follow` of a verified container (as `logs`), stdout and
        stderr merged, timestamped. The child stays in the caller's process group, so a
        terminal hangup or a supervisor's group kill (the console's) reaches it too (S-10); its
        stdin is closed. The caller reads `stdout` line by line, applies `join_partials`,
        redacts, and stops it with `stop_follow`."""
        argv = self._log_argv(info, tail, since, follow=True)
        try:
            return subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=self._environment(),
            )
        except OSError as error:
            raise DeploymentError("Docker operation failed; check docker context") from error

    def _log_argv(self, info: dict, tail: int, since: str | None, follow: bool) -> list[str]:
        if type(tail) is not int or not 1 <= tail <= MAX_TAIL:  # not a bool
            raise DeploymentError(f"--tail must be 1..{MAX_TAIL}")
        if since is not None and not SINCE.fullmatch(since):
            raise DeploymentError("--since must look like 10m, 2h or 2026-11-11T09:00:00Z")
        container = info.get("Id") if isinstance(info, dict) else None
        if not isinstance(container, str) or not re.fullmatch(r"[0-9a-f]{12,64}", container):
            raise DeploymentError("Cannot read logs: no verified container ID")
        argv = [*self._prefix(), "container", "logs", "--timestamps", "--tail", str(tail)]
        argv += ["--follow"] if follow else []
        argv += ["--since", since] if since else []
        return [*argv, container]

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

    def image_present(self, image: str) -> bool:
        """A local image ID (`sha256:…`) must still exist: it cannot be pulled back. A
        registry digest can be pulled at create time, so it counts as present."""
        if not image.startswith("sha256:"):
            return True
        try:
            self.command("image", "inspect", "--format", "{{.Id}}", image)
        except DeploymentError:
            return False
        return True

    def create(
        self,
        record: dict,
        manifest: Manifest,
        *,
        network: str | None = None,
        environment: dict[str, str] | None = None,
        mounts=(),
        host_gateway: bool = False,
        agents_network: str | None = None,
    ):
        """Create the agent container. With `network` (runtime boundary) the agent joins only
        that internal network, publishes nothing and is reached through its sidecar. With
        `agents_network` (a `services up` stack) it keeps the default bridge for its published
        port and is also connected to that network, where the stack's `expose: agents` services
        are."""
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
            # Linux Docker has no host.docker.internal by default; built agents reach the
            # operator's gateway and services through it.
            *(
                ["--add-host", "host.docker.internal:host-gateway"]
                if host_gateway and sys.platform.startswith("linux")
                else []
            ),
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
        if agents_network and not network:
            # Docker refuses the default bridge plus a user network at create; connecting
            # before start keeps the published port and adds the stack's agents network.
            try:
                self.command("network", "connect", agents_network, record["container"])
            except DeploymentError:
                self.command("container", "rm", "--force", record["container"])
                raise DeploymentError(
                    "Cannot join the environment's network; is its stack up "
                    "(agentbreeder services up)?"
                ) from None

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
        return probe_http(int(endpoint.rsplit(":", 1)[1]), path, timeout)


def probe_http(port: int, path: str, timeout: float = 30) -> bool:
    """Whether a loopback HTTP server answers `path` with 200 before the timeout."""
    deadline = time.monotonic() + timeout
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
