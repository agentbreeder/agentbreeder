"""Default-deny egress for kit deployments on local Docker (#1083 F1b, multi-framework spec
§13.4, owner decision D11).

A manifest with `runtime.profile: kit` is deployed without the default bridge. The agent joins
only internal networks: one of its own, shared with a forwarding sidecar, and the
environment's network when there is one (the stack's agents network, where the gateway, the
relays, the trace collector, the database and the tools proxy are). The sidecar publishes the
agent's port on host loopback and, when the operator's gateway is on the host, forwards one
port to it (`forwarder.py`).

This reuses the phase-4 boundary (`boundary.py`): the same pinned base image, network and
container flags, ownership labels, teardown, and the probe run from inside the agent's network
namespace. It adds no policy decision: which destinations exist is the operator config's, and
the manifest field is a signal written by `build`, not a security boundary (a hand-written
manifest can leave it out).

A manifest with `runtime.isolation: two-uid` (#1089, owner decision D15) also gets a CLI
container: the agent's own image, run as CLI_USER with `AGENTBREEDER_ROLE=cli-host`, no secret
mount, no network at all (`--network none`) and the agent container's other flags. The two share
only a small tmpfs volume (SHARED_DIR) that only the agent's uid may write; the runtime checks
every peer's kernel credentials over it (the runtime's `link_protocol.py`).
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from agentbreeder.egress.boundary import (
    DNS_MUST_FAIL,
    GATEWAY_PROBE_PORTS,
    INGRESS_PORT,
    ISOLATED_GATEWAY,
    PUBLIC_PROBES,
    Boundary,
    BoundaryUnverified,
    build_context,
)
from agentbreeder.manifest import Manifest
from agentbreeder.store import DeploymentError

SIDECAR_HOST = "kit-egress.internal"
HEALTH_PORT = 8080
GATEWAY_FORWARD_PORT = 8082
HOST_NAME = "host.docker.internal"
GATEWAY_URL_ENV = "AGENTBREEDER_GATEWAY_URL"
CONFIG_ENV = "AGENTBREEDER_FORWARDER_CONFIG"
ROLE = "forwarder"
CLI_ROLE = "cli"
CLI_USER = "65533:65533"
KIT_UID = 65532
SHARED_DIR = "/run/agentbreeder-cli"
ROLE_ENV = "AGENTBREEDER_ROLE=cli-host"
UNSUPPORTED = "KIT_EGRESS_UNSUPPORTED"
_CODE_FILES = (
    ("agentbreeder/__init__.py", ("agentbreeder", "__init__.py")),
    ("agentbreeder/egress/__init__.py", ("agentbreeder.egress", "__init__.py")),
    ("agentbreeder/egress/forwarder.py", ("agentbreeder.egress", "forwarder.py")),
    ("agentbreeder/egress/probe.py", ("agentbreeder.egress", "probe.py")),
)


def is_kit(manifest: Manifest) -> bool:
    return manifest.runtime is not None and manifest.runtime.profile == "kit"


def two_uid(manifest: Manifest) -> bool:
    return is_kit(manifest) and manifest.runtime.isolation == "two-uid"


VOLUME_OPTIONS = f"size=1m,uid={KIT_UID},gid={KIT_UID},mode=0755,noexec,nosuid,nodev"


def shared_mount(volume: str, readonly: bool = False) -> str:
    """The `--mount` of the shared directory: a named volume, never a host path; `volume-nocopy`
    so the image's mount point cannot set its owner or content. Read-only in the CLI container:
    it only connects to the kit's sockets (a Unix connect works on a read-only mount)."""
    return f"type=volume,src={volume},dst={SHARED_DIR},volume-nocopy" + (
        ",readonly" if readonly else ""
    )


def applies(manifest: Manifest) -> bool:
    """Whether this module deploys the manifest: a kit runtime on local Docker. (On Kubernetes
    the same manifests get a default-deny NetworkPolicy, in `kubernetes.py`.)"""
    return manifest.target == "local" and is_kit(manifest)


def forwarder_context() -> tuple[bytes, str]:
    return build_context(_CODE_FILES, "agentbreeder.egress.forwarder")


def _on_host(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() == HOST_NAME


def gateway_on_host(manifest: Manifest, model) -> bool:
    gateway = model.gateways.get(manifest.model_access.gateway) if manifest.model_access else None
    return gateway is not None and _on_host(gateway.agent_url)


def route(gateway_url: str | None) -> tuple[str | None, dict | None]:
    """The gateway URL the agent is given, and the forward the sidecar needs for it. Only a
    plain-HTTP gateway on the host is forwarded; any other URL is passed through unchanged
    and must be on the environment's network."""
    if gateway_url is None or not _on_host(gateway_url):
        return gateway_url, None
    parts = urlsplit(gateway_url)
    if parts.scheme != "http":
        raise DeploymentError(
            f"[{UNSUPPORTED}] A kit runtime reaches a gateway on the host over http only"
        )
    try:
        port = 80 if parts.port is None else parts.port
        if port == 0:
            raise ValueError
    except ValueError:
        raise DeploymentError(f"[{UNSUPPORTED}] The gateway URL has an invalid port") from None
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    forward = {"listen": GATEWAY_FORWARD_PORT, "host": HOST_NAME, "port": port}
    return f"http://{SIDECAR_HOST}:{GATEWAY_FORWARD_PORT}{path}", forward


def forwards(manifest: Manifest, gateway_url: str | None) -> list[dict]:
    result = [{"listen": INGRESS_PORT, "host": "agent", "port": manifest.port}]
    forward = route(gateway_url)[1]
    return result + ([forward] if forward else [])


def requirements(manifest: Manifest, model, environment: str | None) -> tuple[str, str] | None:
    """What a kit deployment cannot reach from its internal networks is refused before
    anything is created, never left to fail at the first call."""
    config = model.environments.get(environment or "")
    gateway = model.gateways.get(manifest.model_access.gateway) if manifest.model_access else None
    if gateway is not None:
        try:
            forward = route(gateway.agent_url)[1]
        except DeploymentError as error:
            return UNSUPPORTED, str(error).removeprefix(f"[{UNSUPPORTED}] ")
        if forward is None and (config is None or config.network is None):
            return (
                UNSUPPORTED,
                "A kit runtime has no route to this gateway: put it on the environment's "
                f"network or serve it at http://{HOST_NAME}",
            )
    if config is None:
        return None
    for name, url in sorted(config.services.items()):
        if _on_host(url):
            return (
                UNSUPPORTED,
                f"Service {name} is on the host; a kit runtime reaches only the gateway "
                "there. Put the service on the environment's network",
            )
    if manifest.mcp_access is not None:
        wanted = {access.ref for access in manifest.mcp_access}
        for name, relay in sorted(config.mcp_relays.items()):
            if relay.ref in wanted and _on_host(relay.url):
                return (
                    UNSUPPORTED,
                    f"MCP relay {name} is on the host; a kit runtime reaches only the "
                    "gateway there",
                )
    return None


def _isolated(network: dict) -> bool:
    options = network.get("Options") or {}
    return all(options.get(k) == v for k, v in (o.split("=", 1) for o in ISOLATED_GATEWAY))


class KitBoundary(Boundary):
    """The internal networks and forwarding sidecar of one kit deployment. No decision
    service, pin or token: the sidecar holds no secret and decides nothing."""

    def __init__(self, runtime):
        super().__init__(runtime, None, None)

    @staticmethod
    def names(record: dict) -> dict:
        base = record["container"]
        return {
            "internal": base + "-int",
            "egress": base + "-egress",
            "sidecar": base + "-fwd",
            "cli": base + "-cli",
            "shared": base + "-shared",
        }

    def image(self) -> tuple[str, str]:
        return self.built("agentbreeder-forwarder", *forwarder_context())

    def desktop(self) -> bool:
        """Docker Desktop keeps a network's gateway inside its VM. A Linux engine gives the
        host an address on every bridge, so there the gateway must be isolated (Engine 28+)."""
        return self.listen_mode() == "tcp"

    def config(self, record: dict, manifest: Manifest, gateway_url: str | None) -> str:
        return json.dumps(
            {
                "deployment": record["id"],
                "code_digest": forwarder_context()[1],
                "health_port": HEALTH_PORT,
                "forwards": forwards(manifest, gateway_url),
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    # -- topology ----------------------------------------------------------------------------

    def create(
        self,
        record: dict,
        manifest: Manifest,
        *,
        environment: dict[str, str] | None = None,
        mounts=(),
        agents_network: str | None = None,
    ) -> None:
        names = self.names(record)
        environment = dict(environment or {})
        gateway_url = environment.get(GATEWAY_URL_ENV)
        agent_url, forward = route(gateway_url)
        if agent_url is not None:
            environment[GATEWAY_URL_ENV] = agent_url
        self.teardown_containers(record)
        image, _ = self.image()
        desktop = self.desktop()
        if not desktop:
            self.require_isolated_gateway_support()
        if agents_network:
            # Before anything is created: an agent is never started on a network with a
            # route out, not even until verification.
            try:
                self._internal(agents_network, desktop)
            except BoundaryUnverified as error:
                raise DeploymentError(f"[{error.code}] {error}") from error
        isolated = [] if desktop else [f"--opt={option}" for option in ISOLATED_GATEWAY]
        for key, internal in (("internal", True), ("egress", False)):
            self.runtime.command(
                "network",
                "create",
                *(["--internal", *isolated] if internal else []),
                "--label",
                "io.agentbreeder.deployment=" + record["id"],
                names[key],
            )
        self.runtime.command(
            "container",
            "create",
            "--name",
            names["sidecar"],
            "--pull",
            "never",
            *self._labels(record, ROLE),
            "--network",
            names["egress"],
            "--publish",
            f"127.0.0.1::{INGRESS_PORT}",
            # A Linux engine has no host.docker.internal by default. Only the sidecar gets it.
            *(["--add-host", f"{HOST_NAME}:host-gateway"] if forward and not desktop else []),
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
            "128",
            "--log-driver",
            "local",
            "--env",
            f"{CONFIG_ENV}=" + self.config(record, manifest, gateway_url),
            image,
            timeout=60,
        )
        self.runtime.command(
            "network", "connect", "--alias", SIDECAR_HOST, names["internal"], names["sidecar"]
        )
        extra: dict = {}
        if two_uid(manifest):
            self.runtime.command(
                "volume",
                "create",
                "--label",
                "io.agentbreeder.deployment=" + record["id"],
                "--driver",
                "local",
                "--opt",
                "type=tmpfs",
                "--opt",
                "device=tmpfs",
                "--opt",
                "o=" + VOLUME_OPTIONS,
                names["shared"],
            )
            extra["volumes"] = (shared_mount(names["shared"]),)
        # No host mapping and no published port: the agent's only networks are internal.
        self.runtime.create(
            record,
            manifest,
            network=names["internal"],
            environment=environment,
            mounts=mounts,
            **extra,
        )
        if two_uid(manifest):
            self._create_cli(record, manifest, names)
        if agents_network:
            try:
                self.runtime.command("network", "connect", agents_network, record["container"])
            except DeploymentError:
                raise DeploymentError(
                    "Cannot join the environment's network; is its stack up "
                    "(agentbreeder services up)?"
                ) from None

    def _create_cli(self, record: dict, manifest: Manifest, names: dict) -> None:
        """The CLI container: the agent's image and flags, its own uid, no secret, no
        network, no environment but its role."""
        self.runtime.command(
            "container",
            "create",
            "--name",
            names["cli"],
            "--pull",
            "never",
            *self._labels(record, CLI_ROLE),
            "--network",
            "none",
            "--env",
            ROLE_ENV,
            "--mount",
            shared_mount(names["shared"], readonly=True),
            "--user",
            CLI_USER,
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
            timeout=60,
        )

    def _verify_cli(self, record: dict, manifest: Manifest, agent: dict) -> None:
        """Fail closed unless the CLI container is the one `_create_cli` made: running, its own
        uid, no network, no capability, no secret, and only the shared volume mounted (which
        the agent mounts too)."""
        names = self.names(record)
        cli = self._inspect("container", names["cli"])
        labels = (cli.get("Config") or {}).get("Labels") or {}
        if (
            labels.get("io.agentbreeder.deployment") != record["id"]
            or labels.get("io.agentbreeder.role") != CLI_ROLE
        ):
            raise BoundaryUnverified("cli ownership does not match")
        config = cli.get("Config") or {}
        host = cli.get("HostConfig") or {}
        if (cli.get("State") or {}).get("Running") is not True:
            raise BoundaryUnverified("cli is not running")
        if config.get("User") != CLI_USER or host.get("NetworkMode") != "none":
            raise BoundaryUnverified("cli does not run as its own uid without a network")
        if host.get("Privileged") or host.get("CapAdd") or host.get("CapDrop") != ["ALL"]:
            raise BoundaryUnverified("cli capabilities are not dropped")
        if not host.get("ReadonlyRootfs") or host.get("Binds"):
            raise BoundaryUnverified("cli has a writable root or a host path")
        if cli.get("Image") != agent.get("Image"):
            raise BoundaryUnverified("cli does not run the agent's image")
        environment = config.get("Env") or []
        if ROLE_ENV not in environment or any(
            item.split("=", 1)[0].endswith("_FILE") or item.startswith("AGENTBREEDER_GATEWAY")
            for item in environment
        ):
            raise BoundaryUnverified("cli environment does not match")

        def volumes(info: dict) -> list:
            return [
                (m.get("Type"), m.get("Name"), m.get("Destination"), m.get("RW"))
                for m in info.get("Mounts") or []
            ]

        if volumes(cli) != [("volume", names["shared"], SHARED_DIR, False)] or (
            "volume",
            names["shared"],
            SHARED_DIR,
            True,
        ) not in volumes(agent):
            raise BoundaryUnverified("cli mounts more than the shared directory, read-only")
        if not (host.get("SecurityOpt") or []) or "no-new-privileges" not in " ".join(
            host.get("SecurityOpt") or []
        ):
            raise BoundaryUnverified("cli capabilities are not dropped (no-new-privileges)")
        volume = self._inspect("volume", names["shared"])
        if (volume.get("Labels") or {}).get("io.agentbreeder.deployment") != record["id"]:
            raise BoundaryUnverified("shared volume ownership does not match")
        # The options are the control that keeps the CLI's uid from writing the kit's sockets.
        if (volume.get("Options") or {}) != {
            "type": "tmpfs",
            "device": "tmpfs",
            "o": VOLUME_OPTIONS,
        }:
            raise BoundaryUnverified("shared volume options do not match")

    def teardown_containers(self, record: dict) -> None:
        super().teardown_containers(record)
        volumes = self._owned_volumes(record)
        if volumes:
            self.runtime.command("volume", "rm", "--force", *volumes)

    def _owned_volumes(self, record: dict) -> list[str]:
        output = self.runtime.command(
            "volume",
            "ls",
            "--filter",
            "label=io.agentbreeder.deployment=" + record["id"],
            "--format",
            "{{.Name}}",
        )
        return [line for line in output.splitlines() if line.strip()]

    def _internal(self, name: str, desktop: bool) -> dict:
        """The network, which must be internal and, on a Linux engine, keep the host off its
        gateway address."""
        network = self._inspect("network", name)
        if network.get("Internal") is not True:
            raise BoundaryUnverified(f"Network {name} is not internal")
        if not desktop and not _isolated(network):
            raise BoundaryUnverified(f"Network {name}'s gateway is not isolated from the host")
        return network

    def start_sidecar(self, record: dict, manifest: Manifest | None = None) -> None:
        """Idempotent: starting a running container is a no-op. A two-uid deployment's CLI
        container starts with it (it has no network, so it needs nothing from the agent)."""
        names = self.names(record)
        extra = [names["cli"]] if manifest is not None and two_uid(manifest) else []
        self.runtime.command("container", "start", names["sidecar"], *extra)

    def _sidecar(self, record: dict) -> dict:
        info = self._inspect("container", self.names(record)["sidecar"])
        labels = (info.get("Config") or {}).get("Labels") or {}
        if (
            labels.get("io.agentbreeder.deployment") != record["id"]
            or labels.get("io.agentbreeder.role") != ROLE
        ):
            raise BoundaryUnverified("forwarder ownership does not match")
        return info

    def endpoint(self, record: dict) -> str:
        self._sidecar(record)
        return super().endpoint(record)

    # -- verification ------------------------------------------------------------------------

    def verify(
        self,
        record: dict,
        manifest: Manifest,
        *,
        gateway_url: str | None = None,
        agents_network: str | None = None,
    ) -> dict:
        """Fail closed unless the topology and the active probes show that the agent has no
        route but its internal networks and the sidecar's fixed forwards."""
        names = self.names(record)
        desktop = self.desktop()
        if not desktop:
            try:
                self.require_isolated_gateway_support()
            except DeploymentError as error:
                raise BoundaryUnverified(str(error)) from error
        _, code_digest = forwarder_context()
        agent = self._inspect("container", record["container"])
        sidecar = self._sidecar(record)
        expected = [names["internal"], *([agents_network] if agents_network else [])]
        gateways = []
        for name in expected:
            network = self._internal(name, desktop)
            if name == names["internal"]:
                if (network.get("Labels") or {}).get("io.agentbreeder.deployment") != record["id"]:
                    raise BoundaryUnverified("Agent network ownership does not match")
                members = set((network.get("Containers") or {}).keys())
                if members != {agent.get("Id"), sidecar.get("Id")}:
                    raise BoundaryUnverified("Agent network has unexpected members")
            for item in (network.get("IPAM") or {}).get("Config") or []:
                if item.get("Gateway"):
                    gateways.append(item["Gateway"])
        egress = self._inspect("network", names["egress"])
        if (egress.get("Labels") or {}).get("io.agentbreeder.deployment") != record["id"]:
            raise BoundaryUnverified("Forwarder network ownership does not match")
        if set((egress.get("Containers") or {}).keys()) != {sidecar.get("Id")}:
            raise BoundaryUnverified("Forwarder network has unexpected members")
        for info, role in ((agent, "agent"), (sidecar, ROLE)):
            labels = (info.get("Config") or {}).get("Labels") or {}
            if (
                labels.get("io.agentbreeder.deployment") != record["id"]
                or labels.get("io.agentbreeder.role") != role
            ):
                raise BoundaryUnverified(f"{role} ownership does not match")
            host = info.get("HostConfig") or {}
            running = (info.get("State") or {}).get("Running") is True
            if host.get("Privileged") or host.get("CapAdd") or not running:
                raise BoundaryUnverified(f"{role} is privileged or not running")
            if host.get("CapDrop") != ["ALL"]:
                raise BoundaryUnverified(f"{role} capabilities are not dropped")
        if two_uid(manifest):
            self._verify_cli(record, manifest, agent)
        agent_host = agent.get("HostConfig") or {}
        agent_networks = set(((agent.get("NetworkSettings") or {}).get("Networks") or {}).keys())
        if agent_host.get("NetworkMode") != names["internal"] or agent_networks != set(expected):
            raise BoundaryUnverified("Agent is attached to a network other than its own")
        if any((agent_host.get("PortBindings") or {}).values()):
            raise BoundaryUnverified("Agent publishes ports")
        if agent_host.get("ExtraHosts"):
            raise BoundaryUnverified("Agent has a host mapping")
        sidecar_networks = set(
            ((sidecar.get("NetworkSettings") or {}).get("Networks") or {}).keys()
        )
        if sidecar_networks != {names["internal"], names["egress"]}:
            raise BoundaryUnverified("Forwarder networks do not match")
        published = {
            port: bindings
            for port, bindings in (
                (sidecar.get("HostConfig") or {}).get("PortBindings") or {}
            ).items()
            if bindings
        }
        if set(published) != {f"{INGRESS_PORT}/tcp"} or any(
            binding.get("HostIp") != "127.0.0.1" for binding in published[f"{INGRESS_PORT}/tcp"]
        ):
            raise BoundaryUnverified("Forwarder publishes more than the loopback ingress port")
        config = f"{CONFIG_ENV}=" + self.config(record, manifest, gateway_url)
        if config not in ((sidecar.get("Config") or {}).get("Env") or []):
            raise BoundaryUnverified("Forwarder configuration does not match the deployment")
        image = self._inspect("image", sidecar.get("Image") or "")
        labels = (image.get("Config") or {}).get("Labels") or {}
        if labels.get("io.agentbreeder.sidecar-code") != code_digest:
            raise BoundaryUnverified("Forwarder image does not carry the expected code")
        ports = list(GATEWAY_PROBE_PORTS)
        ports += [f["port"] for f in forwards(manifest, gateway_url) if f["host"] == HOST_NAME]
        plan = {
            "tcp": [list(t) for t in PUBLIC_PROBES]
            + [[gateway, port] for gateway in gateways for port in dict.fromkeys(ports)],
            "dns_must_fail": list(DNS_MUST_FAIL),
            "gateway": f"http://{SIDECAR_HOST}:{HEALTH_PORT}/healthz",
        }
        report = self.run_probe(record, self.image()[0], plan)
        if report.get("tcp_open"):
            raise BoundaryUnverified("Agent reached a network address outside its boundary")
        if report.get("dns_resolved"):
            raise BoundaryUnverified("Agent resolved a public or host name")
        health = report.get("gateway") or {}
        body = health.get("body") or {}
        if (
            health.get("status") != 200
            or body.get("deployment") != record["id"]
            or body.get("code_digest") != code_digest
        ):
            raise BoundaryUnverified("Forwarder health does not match the deployment")
        return report

    # -- teardown ----------------------------------------------------------------------------

    def teardown(self, record: dict) -> None:
        """Remove every container and network labelled for this deployment. Verified:
        anything left behind raises."""
        self.teardown_containers(record)
        if (
            self._owned("container", record)
            or self._owned("network", record)
            or self._owned_volumes(record)
        ):
            raise DeploymentError("Kit egress teardown could not be verified; retry")
