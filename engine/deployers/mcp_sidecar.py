"""MCP sidecar deployer — resolves MCP server refs and co-deploys them.

When an agent references MCP servers in its config this module:
  * resolves each ``McpServerRef`` into a :class:`ResolvedMcpServer`
    (a remote HTTP/SSE URL, or a co-deployed container image on a localhost
    port), and
  * injects co-deployed MCP server containers into each cloud deployer's
    container set, alongside the AgentBreeder sidecar.

The AgentBreeder Go sidecar forwards ``POST localhost:9091/mcp/<server>`` to the
resolved URL; :func:`build_sidecar_env_map` produces the ``{name:{transport,url}}``
map handed to it via ``AGENTBREEDER_SIDECAR_MCP_SERVERS``.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from engine.config_parser import McpServerRef
from engine.mcp.packager import build_image_tag, generate_sidecar_config

logger = logging.getLogger(__name__)

# Co-deployed MCP sidecar containers get localhost ports starting here. 3100 sits
# above the agent (8080/8081) and AgentBreeder sidecar localhost ports (9090-9092).
DEFAULT_BASE_PORT = 3100


@dataclass
class McpServerInfo:
    """Registry-sourced facts about an MCP server (best-effort enrichment)."""

    name: str
    transport: str = "stdio"
    endpoint: str | None = None
    image_uri: str | None = None


@dataclass
class ResolvedMcpServer:
    """A fully-resolved MCP server ready to wire into a deploy."""

    name: str
    transport: str
    url: str
    co_deploy: bool
    image: str | None = None
    port: int | None = None
    ref: str = ""


def resolve_mcp_servers(
    mcp_servers: list[McpServerRef],
    *,
    registry_lookup: Callable[[str], McpServerInfo | None] | None = None,
    base_port: int = DEFAULT_BASE_PORT,
    registry_prefix: str = "agentbreeder",
) -> list[ResolvedMcpServer]:
    """Resolve agent.yaml MCP refs into concrete, deployable servers.

    Resolution order per ref: inline ``url`` (remote) → inline ``image``
    (co-deploy) → best-effort ``registry_lookup`` (endpoint or image_uri) →
    convention co-deploy (``build_image_tag``).
    """
    resolved: list[ResolvedMcpServer] = []
    for i, mcp in enumerate(mcp_servers):
        name = mcp.ref.split("/")[-1]
        transport = mcp.transport
        port = mcp.port or (base_port + i)

        if mcp.url:
            resolved.append(ResolvedMcpServer(name, transport, mcp.url, False, ref=mcp.ref))
            continue
        if mcp.image:
            resolved.append(
                ResolvedMcpServer(
                    name,
                    transport,
                    f"http://localhost:{port}",
                    True,
                    image=mcp.image,
                    port=port,
                    ref=mcp.ref,
                )
            )
            continue

        info = registry_lookup(name) if registry_lookup else None
        if info and info.endpoint:
            resolved.append(
                ResolvedMcpServer(
                    name, info.transport or transport, info.endpoint, False, ref=mcp.ref
                )
            )
            continue
        if info and info.image_uri:
            resolved.append(
                ResolvedMcpServer(
                    name,
                    info.transport or transport,
                    f"http://localhost:{port}",
                    True,
                    image=info.image_uri,
                    port=port,
                    ref=mcp.ref,
                )
            )
            continue

        # Convention fallback: co-deploy a conventionally-named image.
        image = build_image_tag(name, "latest", registry_prefix)
        resolved.append(
            ResolvedMcpServer(
                name,
                transport,
                f"http://localhost:{port}",
                True,
                image=image,
                port=port,
                ref=mcp.ref,
            )
        )
        logger.info("Resolved MCP '%s' by convention → %s", name, image)

    return resolved


def build_sidecar_env_map(resolved: list[ResolvedMcpServer]) -> dict[str, dict[str, str]]:
    """Build the ``{name:{transport,url}}`` map for the Go sidecar.

    The Go sidecar can only forward HTTP/SSE. Co-deployed servers always speak
    HTTP on their localhost port (we set ``MCP_TRANSPORT`` on the container), so a
    co-deployed ``stdio`` transport is normalised to ``http``. A *remote* ``stdio``
    server cannot be forwarded and is skipped with a warning.
    """
    out: dict[str, dict[str, str]] = {}
    for r in resolved:
        transport = r.transport
        if transport == "stdio":
            if r.co_deploy:
                transport = "http"
            else:
                logger.warning(
                    "MCP '%s' uses remote stdio transport — the sidecar cannot "
                    "forward it; skipping. Use http/sse or co-deploy an image.",
                    r.name,
                )
                continue
        out[r.name] = {"transport": transport, "url": r.url}
    return out


# --------------------------------------------------------------------------- #
# Container co-deploy injection — one helper per multi-container deployer.
# All are idempotent (skip a server whose container already exists) and never
# mutate their input.
# --------------------------------------------------------------------------- #


def _codeploy(resolved: list[ResolvedMcpServer]) -> list[ResolvedMcpServer]:
    return [r for r in resolved if r.co_deploy and r.image]


def inject_mcp_containers_ecs(
    task_definition: dict[str, Any], resolved: list[ResolvedMcpServer]
) -> dict[str, Any]:
    """Append co-deployed MCP server containers to an ECS task definition."""
    result = copy.deepcopy(task_definition)
    containers: list[dict[str, Any]] = result.setdefault("containerDefinitions", [])
    existing = {c.get("name") for c in containers}
    for r in _codeploy(resolved):
        cname = f"mcp-{r.name}"
        if cname in existing:
            continue
        containers.append(
            {
                "name": cname,
                "image": r.image,
                "essential": False,
                "portMappings": [{"containerPort": r.port, "protocol": "tcp"}],
                "environment": [
                    {"name": "MCP_TRANSPORT", "value": "http"},
                    {"name": "PORT", "value": str(r.port)},
                ],
            }
        )
    return result


def inject_mcp_containers_cloudrun(
    containers: list[dict[str, Any]], resolved: list[ResolvedMcpServer]
) -> list[dict[str, Any]]:
    """Append co-deployed MCP server containers to a Cloud Run v2 container list.

    Cloud Run revisions share a network namespace, so a co-deployed MCP server is
    reachable by the sidecar over ``localhost:<port>``. Returns a new list.
    """
    result = copy.deepcopy(containers)
    existing = {c.get("name") for c in result}
    for r in _codeploy(resolved):
        cname = f"mcp-{r.name}"
        if cname in existing:
            continue
        result.append(
            {
                "name": cname,
                "image": r.image,
                "env": [
                    {"name": "MCP_TRANSPORT", "value": "http"},
                    {"name": "PORT", "value": str(r.port)},
                ],
            }
        )
    return result


def inject_mcp_containers_azure(
    containers: list[dict[str, Any]], resolved: list[ResolvedMcpServer]
) -> list[dict[str, Any]]:
    """Append co-deployed MCP containers to an Azure Container Apps container list."""
    result = copy.deepcopy(containers)
    existing = {c.get("name") for c in result}
    for r in _codeploy(resolved):
        cname = f"mcp-{r.name}"
        if cname in existing:
            continue
        result.append(
            {
                "name": cname,
                "image": r.image,
                "env": [
                    {"name": "MCP_TRANSPORT", "value": "http"},
                    {"name": "PORT", "value": str(r.port)},
                ],
            }
        )
    return result


def inject_mcp_containers_compose(
    services: dict[str, Any], resolved: list[ResolvedMcpServer]
) -> dict[str, Any]:
    """Append co-deployed MCP server services to a docker-compose services dict."""
    result = copy.deepcopy(services)
    for r in _codeploy(resolved):
        sname = f"mcp-{r.name}"
        if sname in result:
            continue
        result[sname] = {
            "image": r.image,
            "environment": {"MCP_TRANSPORT": "http", "PORT": str(r.port)},
            "restart": "unless-stopped",
        }
    return result


# --------------------------------------------------------------------------- #
# Back-compat shims — keep the original McpSidecarDeployer surface so existing
# tests (tests/unit/test_mcp_packager.py) and any callers keep working.
# --------------------------------------------------------------------------- #


class McpSidecarDeployer:
    """Legacy helper retained for compose generation + tests."""

    def generate_sidecars(
        self,
        mcp_servers: list[McpServerRef],
        agent_name: str,
        registry_prefix: str = "agentbreeder",
    ) -> list[dict[str, Any]]:
        sidecars: list[dict[str, Any]] = []
        for i, mcp in enumerate(mcp_servers):
            server_name = mcp.ref.split("/")[-1]
            image_uri = build_image_tag(server_name, "latest", registry_prefix)
            sidecar = generate_sidecar_config(
                name=server_name,
                image_uri=image_uri,
                transport=mcp.transport,
                port=3000 + i,
            )
            sidecar["labels"] = {
                "agentbreeder.agent": agent_name,
                "agentbreeder.mcp-ref": mcp.ref,
            }
            sidecars.append(sidecar)
        return sidecars

    def inject_into_compose(
        self, compose_config: dict[str, Any], sidecars: list[dict[str, Any]]
    ) -> dict[str, Any]:
        services = compose_config.setdefault("services", {})
        for sidecar in sidecars:
            services[sidecar["name"]] = {
                "image": sidecar["image"],
                "environment": sidecar.get("environment", {}),
                "ports": [f"{sidecar['port']}:{sidecar['port']}"],
                "labels": sidecar.get("labels", {}),
                "restart": "unless-stopped",
            }
        return compose_config
