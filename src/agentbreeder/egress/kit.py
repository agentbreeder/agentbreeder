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
UNSUPPORTED = "KIT_EGRESS_UNSUPPORTED"
_CODE_FILES = (
    ("agentbreeder/__init__.py", ("agentbreeder", "__init__.py")),
    ("agentbreeder/egress/__init__.py", ("agentbreeder.egress", "__init__.py")),
    ("agentbreeder/egress/forwarder.py", ("agentbreeder.egress", "forwarder.py")),
    ("agentbreeder/egress/probe.py", ("agentbreeder.egress", "probe.py")),
)


def is_kit(manifest: Manifest) -> bool:
    return manifest.runtime is not None and manifest.runtime.profile == "kit"


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
        return {"internal": base + "-int", "egress": base + "-egress", "sidecar": base + "-fwd"}

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
        # No host mapping and no published port: the agent's only networks are internal.
        self.runtime.create(
            record, manifest, network=names["internal"], environment=environment, mounts=mounts
        )
        if agents_network:
            try:
                self.runtime.command("network", "connect", agents_network, record["container"])
            except DeploymentError:
                raise DeploymentError(
                    "Cannot join the environment's network; is its stack up "
                    "(agentbreeder services up)?"
                ) from None

    def _internal(self, name: str, desktop: bool) -> dict:
        """The network, which must be internal and, on a Linux engine, keep the host off its
        gateway address."""
        network = self._inspect("network", name)
        if network.get("Internal") is not True:
            raise BoundaryUnverified(f"Network {name} is not internal")
        if not desktop and not _isolated(network):
            raise BoundaryUnverified(f"Network {name}'s gateway is not isolated from the host")
        return network

    def start_sidecar(self, record: dict) -> None:
        """Idempotent: starting a running container is a no-op."""
        self.runtime.command("container", "start", self.names(record)["sidecar"])

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
        if self._owned("container", record) or self._owned("network", record):
            raise DeploymentError("Kit egress teardown could not be verified; retry")
