"""Builds, verifies and tears down the per-deployment egress boundary (option C, phase 4).

Topology: the agent joins only an `--internal` network; the sidecar joins that network (alias
`mcp-gateway.internal`) and one per-deployment egress network, and publishes the agent's ingress
on host loopback. Nothing is trusted until verification passes: inspect checks plus active probes
from a short-lived container that shares the agent's network namespace. There is no degraded mode.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
from importlib import resources
from pathlib import Path

from agentbreeder.egress.host import egress_dir, write_private_json
from agentbreeder.egress.protocol import MAC_HEADER, NONCE_HEADER, response_authentic
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import OperatorConfig, OperatorConfigError, read_private
from agentbreeder.store import DeploymentError

# Multi-arch index digest for python:3.12-slim, pinned (bump deliberately, never float).
SIDECAR_BASE = (
    "python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54"
)
GATEWAY_HOST = "mcp-gateway.internal"
GATEWAY_PORT = 8080
INGRESS_PORT = 8081
_CODE_FILES = (
    ("agentbreeder/__init__.py", ("agentbreeder", "__init__.py")),
    ("agentbreeder/egress/__init__.py", ("agentbreeder.egress", "__init__.py")),
    ("agentbreeder/egress/protocol.py", ("agentbreeder.egress", "protocol.py")),
    ("agentbreeder/egress/sidecar.py", ("agentbreeder.egress", "sidecar.py")),
    ("agentbreeder/egress/probe.py", ("agentbreeder.egress", "probe.py")),
)
PUBLIC_PROBES = (
    ("1.1.1.1", 443),
    ("1.1.1.1", 80),
    ("8.8.8.8", 53),
    ("2606:4700:4700::1111", 443),
)
GATEWAY_PROBE_PORTS = (22, 53, 80, 443, 2375, 2376, 3128, 5000, 8000, 8080, 8443, 9090)
# Linux engines give the host an address on every bridge, internal or not; "isolated" removes it
# so no host service is reachable from the agent. Docker Desktop keeps the gateway inside its VM.
ISOLATED_GATEWAY = (
    "com.docker.network.bridge.gateway_mode_ipv4=isolated",
    "com.docker.network.bridge.gateway_mode_ipv6=isolated",
)
DNS_MUST_FAIL = ("example.com", "host.docker.internal", "gateway.docker.internal")


class BoundaryUnverified(DeploymentError):
    code = "RUNTIME_BOUNDARY_UNVERIFIED"


def sidecar_context() -> tuple[bytes, str]:
    """A deterministic build context: the stdlib-only sidecar code plus a Dockerfile."""
    files = {}
    for target, (package, name) in _CODE_FILES:
        files[target] = resources.files(package).joinpath(name).read_bytes()
    digest = hashlib.sha256()
    for target in sorted(files):
        digest.update(target.encode() + b"\0" + files[target] + b"\0")
    code_digest = "sha256:" + digest.hexdigest()
    files["Dockerfile"] = (
        f"FROM {SIDECAR_BASE}\n"
        "COPY agentbreeder /opt/agentbreeder/agentbreeder\n"
        "ENV PYTHONPATH=/opt/agentbreeder PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
        f"LABEL io.agentbreeder.sidecar-code={code_digest}\n"
        "USER 65532:65532\n"
        'ENTRYPOINT ["python", "-m", "agentbreeder.egress.sidecar"]\n'
    ).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name in sorted(files):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(files[name]), 0o644, 0
            archive.addfile(info, io.BytesIO(files[name]))
    return buffer.getvalue(), code_digest


def _service_environment() -> dict:
    """Inherit the operator's environment (credential `env:` references resolve there) and make
    sure the service imports this same AgentBreeder, installed or from a checkout."""
    package_root = str(Path(resources.files("agentbreeder")).parent)
    existing = os.environ.get("PYTHONPATH")
    return {
        **os.environ,
        "PYTHONPATH": package_root + (os.pathsep + existing if existing else ""),
    }


def free_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Boundary:
    def __init__(self, runtime, state_dir: Path, operator: OperatorConfig, spawn=subprocess.Popen):
        self.runtime = runtime
        self.state_dir = state_dir
        self.operator = operator
        self.spawn = spawn

    # -- naming ------------------------------------------------------------------------------

    @staticmethod
    def names(record: dict) -> dict:
        base = record["container"]
        return {
            "internal": base + "-int",
            "egress": base + "-egress",
            "sidecar": base + "-pep",
        }

    def directory(self, record: dict) -> Path:
        return egress_dir(self.state_dir, record["id"])

    def enforced(self, record: dict) -> bool:
        return (self.directory(record) / "pin.json").exists()

    def _labels(self, record: dict, role: str) -> list[str]:
        return [
            "--label",
            "io.agentbreeder.deployment=" + record["id"],
            "--label",
            "io.agentbreeder.spec=" + record["digest"],
            "--label",
            "io.agentbreeder.role=" + role,
        ]

    def _inspect(self, kind: str, name: str) -> dict:
        try:
            return json.loads(self.runtime.command(kind, "inspect", name))[0]
        except (ValueError, IndexError, TypeError) as error:
            raise BoundaryUnverified(f"Cannot inspect {kind}") from error

    # -- sidecar image -----------------------------------------------------------------------

    def image(self) -> tuple[str, str]:
        context, code_digest = sidecar_context()
        tag = "agentbreeder-sidecar:" + code_digest[7:23]
        try:
            info = json.loads(self.runtime.command("image", "inspect", tag))[0]
            if (info["Config"]["Labels"] or {}).get("io.agentbreeder.sidecar-code") == code_digest:
                return tag, code_digest
        except (DeploymentError, ValueError, KeyError, IndexError, TypeError):
            pass
        self.runtime.command("build", "--quiet", "--tag", tag, "-", timeout=300, stdin=context)
        return tag, code_digest

    # -- decision service process ----------------------------------------------------------

    def listen_mode(self) -> str:
        """Docker Desktop cannot bind-mount host Unix sockets (EOPNOTSUPP), so it reaches the
        host service over loopback TCP with a per-deployment token; Linux engines use a socket."""
        system = self.runtime.command("info", "--format", "{{.OperatingSystem}}", timeout=10)
        return "tcp" if "Docker Desktop" in system else "unix"

    def require_isolated_gateway_support(self) -> None:
        """Older engines may accept and echo unknown bridge options without enforcing them, so
        the isolated gateway (and thus the boundary) is only trusted on Docker Engine 28+."""
        version = self.runtime.command("version", "--format", "{{.Server.Version}}", timeout=10)
        try:
            major = int(version.split(".", 1)[0])
        except ValueError as error:
            raise DeploymentError("Cannot determine the Docker Engine version") from error
        if major < 28:
            raise DeploymentError("Runtime enforcement on Linux needs Docker Engine 28 or later")

    def write_pin(self, record: dict, pin: dict) -> str:
        directory = self.directory(record)
        directory.parent.mkdir(mode=0o700, exist_ok=True)
        directory.mkdir(mode=0o700, exist_ok=True)
        token = secrets.token_urlsafe(32)
        mode = self.listen_mode()
        if mode == "unix":
            self.require_isolated_gateway_support()
        full = {
            **pin,
            "deployment": record["id"],
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "listen": mode,
        }
        if mode == "tcp":
            full["port"] = free_loopback_port()
        else:
            if len(str(directory / "sock" / "pep.sock").encode()) > 100:
                raise DeploymentError("State directory path is too long for a Unix socket")
            (directory / "sock").mkdir(mode=0o755, exist_ok=True)
        write_private_json(directory / "pin.json", full)
        return token

    def pin(self, record: dict) -> dict:
        return json.loads((self.directory(record) / "pin.json").read_text())

    def set_serving(self, record: dict, serving: bool) -> None:
        """Open (after verification) or close (before a restore) the decision-service latch."""
        path = self.directory(record) / "pin.json"
        write_private_json(path, {**self.pin(record), "serving": serving})

    def sidecar_address(self, record: dict) -> str:
        pin = self.pin(record)
        if pin["listen"] == "tcp":
            return f"http://host.docker.internal:{pin['port']}"
        return "unix:/run/agentbreeder/pep.sock"

    def service_pid(self, record: dict) -> int | None:
        try:
            endpoint = json.loads((self.directory(record) / "endpoint.json").read_text())
            pid = int(endpoint["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        try:
            command = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        if "agentbreeder.egress.host" in command and record["id"] in command:
            return pid
        return None

    def service_healthy(self, record: dict) -> bool:
        pin = self.pin(record)
        if pin["listen"] != "tcp":
            return self.service_pid(record) is not None
        # Loopback ports are shared with every local user: the process we started must still be
        # alive, and the reply must carry a MAC only the pin holder can compute.
        if self.service_pid(record) is None:
            return False
        nonce = secrets.token_hex(16)
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{pin['port']}/healthz", headers={NONCE_HEADER: nonce}
            )
            with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - loopback
                data = response.read(65536)
                status, mac = response.status, response.headers.get(MAC_HEADER)
            if not response_authentic(pin["token_sha256"], nonce, status, data, mac):
                return False
            return json.loads(data).get("revision") == pin["revision"]
        except (OSError, ValueError, KeyError):
            return False

    def start_service(self, record: dict, timeout: float = 15) -> None:
        self.stop_service(record)
        directory = self.directory(record)
        (directory / "endpoint.json").unlink(missing_ok=True)
        log = os.open(directory / "service.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            self.spawn(
                [
                    sys.executable,
                    "-m",
                    "agentbreeder.egress.host",
                    "--state-dir",
                    str(self.state_dir),
                    "--deployment",
                    record["id"],
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
                env=_service_environment(),
            )
        finally:
            os.close(log)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (directory / "endpoint.json").exists() and self.service_healthy(record):
                return
            time.sleep(0.2)
        raise DeploymentError("Decision service did not start; see the egress service.log")

    def stop_service(self, record: dict) -> None:
        pid = self.service_pid(record)
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        (self.directory(record) / "endpoint.json").unlink(missing_ok=True)

    # -- topology ----------------------------------------------------------------------------

    def destinations(self, manifest: Manifest) -> dict:
        declared = {t.mcp for t in manifest.capabilities.tools} if manifest.capabilities else set()
        base = self.operator.path.parent if self.operator.path else None
        result = {}
        for name, destination in self.operator.model.runtime_enforcement.destinations.items():
            if destination.url not in declared:
                continue
            entry = {
                "url": destination.url,
                "allow_private": destination.allow_private,
                "tools": sorted(
                    {
                        action
                        for tool in manifest.capabilities.tools
                        if tool.mcp == destination.url
                        for action in tool.actions
                    }
                ),
            }
            if destination.ca_file:
                path = Path(destination.ca_file)
                path = path if path.is_absolute() or base is None else base / path
                try:
                    entry["ca_pem"] = read_private(path, "CA file", 256 * 1024).decode()
                except (OperatorConfigError, UnicodeDecodeError) as error:
                    raise DeploymentError("Cannot read a destination ca_file") from error
            result[name] = entry
        return result

    def create(self, record: dict, manifest: Manifest, token: str) -> None:
        names = self.names(record)
        self.teardown_containers(record)
        image, code_digest = self.image()
        pin = self.pin(record)
        isolated = [] if pin["listen"] == "tcp" else [f"--opt={o}" for o in ISOLATED_GATEWAY]
        for key, internal in (("internal", True), ("egress", False)):
            self.runtime.command(
                "network",
                "create",
                *(["--internal", *isolated] if internal else []),
                "--label",
                "io.agentbreeder.deployment=" + record["id"],
                names[key],
            )
        destinations = self.destinations(manifest)
        limits = self.operator.model.runtime_enforcement.limits
        config = {
            "deployment": record["id"],
            "code_digest": code_digest,
            "gateway_port": GATEWAY_PORT,
            "ingress_port": INGRESS_PORT,
            "agent_host": "agent",
            "agent_port": manifest.port,
            "host_address": self.sidecar_address(record),
            "destinations": destinations,
            "limits": limits.model_dump(),
        }
        mounts = []
        if pin["listen"] == "unix":
            mounts = ["--volume", f"{self.directory(record) / 'sock'}:/run/agentbreeder:ro"]
        self.runtime.command(
            "container",
            "create",
            "--name",
            names["sidecar"],
            "--pull",
            "never",
            *self._labels(record, "sidecar"),
            "--network",
            names["egress"],
            "--publish",
            f"127.0.0.1::{INGRESS_PORT}",
            "--user",
            "65532:65532",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "128m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "64",
            "--log-driver",
            "local",
            "--env",
            "AGENTBREEDER_SIDECAR_CONFIG=" + json.dumps(config, separators=(",", ":")),
            "--env",
            "AGENTBREEDER_PEP_TOKEN",  # value from the CLI's environment, not argv
            *mounts,
            image,
            timeout=60,
            env={"AGENTBREEDER_PEP_TOKEN": token},
        )
        self.runtime.command(
            "network", "connect", "--alias", GATEWAY_HOST, names["internal"], names["sidecar"]
        )
        gateway = f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/mcp"
        self.runtime.create(
            record,
            manifest,
            network=names["internal"],
            environment={
                "AGENTBREEDER_MCP_GATEWAY": gateway,
                "AGENTBREEDER_MCP_SERVERS": json.dumps(
                    {name: f"{gateway}/{name}" for name in destinations}, separators=(",", ":")
                ),
            },
        )

    def start(self, record: dict) -> None:
        names = self.names(record)
        self.runtime.command("container", "start", names["sidecar"])
        self.runtime.command("container", "start", record["container"])

    def endpoint(self, record: dict) -> str:
        info = self._inspect("container", self.names(record)["sidecar"])
        try:
            binding = info["NetworkSettings"]["Ports"][f"{INGRESS_PORT}/tcp"][0]
            port = int(binding["HostPort"])
            if binding["HostIp"] != "127.0.0.1" or not 1 <= port <= 65535:
                raise ValueError
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise DeploymentError("Sidecar has no single loopback ingress binding") from error
        return f"http://127.0.0.1:{port}"

    # -- verification ------------------------------------------------------------------------

    def verify(self, record: dict) -> dict:
        """Fail closed unless the topology and the active probes prove the boundary."""
        names = self.names(record)
        pin = self.pin(record)
        if pin.get("listen") != "tcp":
            try:
                self.require_isolated_gateway_support()
            except DeploymentError as error:
                raise BoundaryUnverified(str(error)) from error
        _, code_digest = sidecar_context()
        network = self._inspect("network", names["internal"])
        agent = self._inspect("container", record["container"])
        sidecar = self._inspect("container", names["sidecar"])
        if network.get("Internal") is not True:
            raise BoundaryUnverified("Agent network is not internal")
        if pin.get("listen") != "tcp":
            options = network.get("Options") or {}
            if any(options.get(k) != v for k, v in (o.split("=", 1) for o in ISOLATED_GATEWAY)):
                raise BoundaryUnverified("Agent network gateway is not isolated from the host")
        if (network.get("Labels") or {}).get("io.agentbreeder.deployment") != record["id"]:
            raise BoundaryUnverified("Agent network ownership does not match")
        attached = set((network.get("Containers") or {}).keys())
        if attached != {agent.get("Id"), sidecar.get("Id")}:
            raise BoundaryUnverified("Agent network has unexpected members")
        for info, role in ((agent, "agent"), (sidecar, "sidecar")):
            labels = (info.get("Config") or {}).get("Labels") or {}
            if (
                labels.get("io.agentbreeder.deployment") != record["id"]
                or labels.get("io.agentbreeder.role") != role
            ):
                raise BoundaryUnverified(f"{role} ownership does not match")
            host = info.get("HostConfig") or {}
            if host.get("Privileged") or host.get("CapAdd") or not info["State"]["Running"]:
                raise BoundaryUnverified(f"{role} is privileged or not running")
        agent_host = agent.get("HostConfig") or {}
        agent_networks = set(((agent.get("NetworkSettings") or {}).get("Networks") or {}).keys())
        if agent_host.get("NetworkMode") != names["internal"] or agent_networks != {
            names["internal"]
        }:
            raise BoundaryUnverified("Agent is attached to a network other than its boundary")
        if any((agent_host.get("PortBindings") or {}).values()):
            raise BoundaryUnverified("Agent publishes ports")
        if "NET_ADMIN" in str(agent_host.get("CapAdd")) or agent_host.get("CapDrop") != ["ALL"]:
            raise BoundaryUnverified("Agent capabilities are not dropped")
        sidecar_networks = set(
            ((sidecar.get("NetworkSettings") or {}).get("Networks") or {}).keys()
        )
        if sidecar_networks != {names["internal"], names["egress"]}:
            raise BoundaryUnverified("Sidecar networks do not match the boundary")
        if (sidecar["Config"].get("Labels") or {}).get("io.agentbreeder.role") != "sidecar":
            raise BoundaryUnverified("Sidecar ownership does not match")
        image = self._inspect("image", sidecar["Image"])
        if (image["Config"].get("Labels") or {}).get(
            "io.agentbreeder.sidecar-code"
        ) != code_digest:
            raise BoundaryUnverified("Sidecar image does not carry the expected code")
        gateways = [c.get("Gateway") for c in (network.get("IPAM") or {}).get("Config") or []]
        report = self.probe(record, [g for g in gateways if g])
        if report.get("tcp_open"):
            raise BoundaryUnverified("Agent reached a network address outside its boundary")
        if report.get("dns_resolved"):
            raise BoundaryUnverified("Agent resolved a public or host name")
        gateway = report.get("gateway") or {}
        body = gateway.get("body") or {}
        host = body.get("host") or {}
        if (
            gateway.get("status") != 200
            or body.get("deployment") != record["id"]
            or body.get("code_digest") != code_digest
            or host.get("revision") != pin["revision"]
            or host.get("operator_config_digest") != pin["operator_digest"]
        ):
            raise BoundaryUnverified("Gateway health does not match the recorded deployment")
        return report

    def probe(self, record: dict, gateways: list[str]) -> dict:
        image, _ = self.image()
        ports = list(GATEWAY_PROBE_PORTS)
        pin = self.pin(record)
        if pin.get("listen") == "tcp" and pin.get("port"):
            ports.append(int(pin["port"]))  # the decision service must be sidecar-only too
        plan = {
            "tcp": [list(t) for t in PUBLIC_PROBES] + [[g, p] for g in gateways for p in ports],
            "dns_must_fail": list(DNS_MUST_FAIL),
            "gateway": f"http://{GATEWAY_HOST}:{GATEWAY_PORT}/healthz",
        }
        output = self.runtime.command(
            "container",
            "run",
            "--rm",
            "--pull",
            "never",
            *self._labels(record, "probe"),
            "--network",
            "container:" + record["container"],
            "--user",
            "65532:65532",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--entrypoint",
            "python",
            image,
            "-m",
            "agentbreeder.egress.probe",
            json.dumps(plan),
            timeout=120,
        )
        try:
            report = json.loads(output.splitlines()[-1])
            if not isinstance(report, dict):
                raise ValueError
            return report
        except (ValueError, IndexError) as error:
            raise BoundaryUnverified("Boundary probe returned no report") from error

    # -- teardown ----------------------------------------------------------------------------

    def _owned(self, kind: str, record: dict) -> list[str]:
        listing = ["container", "ls", "--all"] if kind == "container" else ["network", "ls"]
        output = self.runtime.command(
            *listing,
            "--filter",
            "label=io.agentbreeder.deployment=" + record["id"],
            "--format",
            "{{.ID}}",
        )
        return [line for line in output.splitlines() if line.strip()]

    def teardown_containers(self, record: dict) -> None:
        containers = self._owned("container", record)
        if containers:
            self.runtime.command("container", "rm", "--force", "--volumes", *containers)
        networks = self._owned("network", record)
        if networks:
            self.runtime.command("network", "rm", *networks)

    def teardown(self, record: dict) -> None:
        """Remove every container and network labelled for this deployment and stop the
        decision service. Verified: anything left behind raises."""
        self.teardown_containers(record)
        self.stop_service(record)
        if self._owned("container", record) or self._owned("network", record):
            raise DeploymentError("Boundary teardown could not be verified; retry")
        (self.directory(record) / "pin.json").unlink(missing_ok=True)
