"""Sidecar configuration dataclass.

Track J: cross-cutting concerns layer (tracing, cost, guardrails, A2A, MCP).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

# Single source of truth for the sidecar image — kept as a constant so deployers
# never accidentally diverge. Pinned to a concrete version (never ":latest") so
# a deploy is reproducible and a re-pulled image can't silently change the
# guardrail/auth behaviour fronting an agent. Bump this in lockstep with the
# release that publishes the matching agentbreeder-sidecar tag.
DEFAULT_SIDECAR_IMAGE = "agentbreeder/agentbreeder-sidecar:2.5.1"

# Valid port range for sidecar port-number fields.
_MIN_PORT = 1
_MAX_PORT = 65535

logger = logging.getLogger(__name__)


class SidecarConfigError(ValueError):
    """Raised when a sidecar config block is malformed.

    Surfaced at the start of ``deploy()`` so broken sidecar configs fail at
    submit time (clear error) instead of at health-check time (cryptic timeout).
    """


@dataclass
class SidecarConfig:
    """Runtime configuration for the AgentBreeder sidecar container.

    Populated either from the deploy.sidecar dict in agent.yaml, or
    auto-derived from the top-level agent config when guardrails / A2A / MCP
    are declared.
    """

    enabled: bool = True
    image: str = DEFAULT_SIDECAR_IMAGE
    otel_endpoint: str = field(default_factory=lambda: os.getenv("OPENTELEMETRY_ENDPOINT", ""))
    guardrails: list[str] = field(default_factory=list)
    cost_tracking: bool = True
    health_port: int = 8080
    # When sidecar is injected the agent listens on this internal port and
    # the sidecar forwards public traffic from health_port.
    agent_port: int = 8081
    auth_token_env: str = "AGENT_AUTH_TOKEN"
    api_url_env: str = "AGENTBREEDER_API_URL"
    # MCP server forwarding map for the Go sidecar: {name: {transport, url}}.
    mcp_servers: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_deploy_config(cls, deploy_sidecar: dict[str, Any] | None) -> SidecarConfig:
        """Build a SidecarConfig from the deploy.sidecar dict in agent.yaml.

        Returns a default (enabled) config if deploy_sidecar is None.
        """
        if deploy_sidecar is None:
            return cls()
        return cls(
            enabled=deploy_sidecar.get("enabled", True),
            image=deploy_sidecar.get("image", DEFAULT_SIDECAR_IMAGE),
            guardrails=deploy_sidecar.get("guardrails", []),
            otel_endpoint=deploy_sidecar.get(
                "otel_endpoint",
                os.getenv("OPENTELEMETRY_ENDPOINT", ""),
            ),
            cost_tracking=deploy_sidecar.get("cost_tracking", True),
            agent_port=int(deploy_sidecar.get("agent_port", 8081)),
        )

    @classmethod
    def from_agent_config(cls, agent_config: Any) -> SidecarConfig:
        """Build a SidecarConfig from a parsed AgentConfig.

        Reads top-level guardrails and resolves any ``mcp_servers`` into the
        forwarding map the Go sidecar consumes. MCP resolution here is offline
        (inline url/image + convention) — registry enrichment happens earlier
        in the deploy pipeline when a session is available.
        """
        guardrails = _normalise_guardrails(getattr(agent_config, "guardrails", []) or [])
        mcp_refs = getattr(agent_config, "mcp_servers", None) or []
        mcp_map: dict[str, dict[str, str]] = {}
        if mcp_refs:
            from engine.deployers.mcp_sidecar import build_sidecar_env_map, resolve_mcp_servers

            mcp_map = build_sidecar_env_map(resolve_mcp_servers(mcp_refs))
        return cls(
            enabled=True,
            guardrails=guardrails,
            mcp_servers=mcp_map,
        )


def _normalise_guardrails(raw: list[Any]) -> list[str]:
    """Reduce mixed (str | GuardrailConfig) lists down to a list of names."""
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            out.append(entry)
        else:
            name = getattr(entry, "name", None)
            if name:
                out.append(str(name))
    return out


def _is_valid_port(value: Any) -> bool:
    """Return True if ``value`` is an int (or int-str) in the valid TCP range.

    Floats — even integer-valued floats like ``8080.0`` — are rejected so the
    schema stays strict: YAML port fields should be integers.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return False
    try:
        port = int(value)
    except (TypeError, ValueError):
        return False
    return _MIN_PORT <= port <= _MAX_PORT


def validate_sidecar_config(agent_config: Any) -> None:
    """Pre-validate the sidecar block on an agent config.

    Call this at the start of every deployer's ``deploy()`` before any cloud
    API call. If the sidecar block is malformed, raise :class:`SidecarConfigError`
    with a clear, actionable message — so broken sidecar configs fail at submit
    (the engine surfaces this to the CLI) instead of at health-check time with a
    cryptic timeout.

    Args:
        agent_config: A parsed ``AgentConfig`` (or anything with a ``deploy``
            attribute whose ``sidecar`` field is a dict-or-None). When the field
            is absent or ``None``, validation is a no-op — the deployer can
            still auto-inject from top-level guardrails via
            :func:`SidecarConfig.from_agent_config`.

    Raises:
        SidecarConfigError: when any field violates schema constraints.
    """
    deploy = getattr(agent_config, "deploy", None)
    if deploy is None:
        return

    raw = getattr(deploy, "sidecar", None)
    if raw is None:
        return

    # Allow either a dict (typical YAML shape) or an already-parsed
    # SidecarConfig-like object that exposes the same attributes.
    if isinstance(raw, dict):
        get = raw.get
    else:

        def get(key: str, default: Any = None) -> Any:
            return getattr(raw, key, default)

    enabled = get("enabled", True)
    if not isinstance(enabled, bool):
        raise SidecarConfigError(
            f"deploy.sidecar.enabled must be a bool, got {type(enabled).__name__}: {enabled!r}",
        )

    image = get("image", DEFAULT_SIDECAR_IMAGE)
    if not isinstance(image, str) or not image.strip():
        raise SidecarConfigError(
            "deploy.sidecar.image must be a non-empty string "
            f"(got {type(image).__name__}: {image!r})",
        )

    otel_endpoint = get("otel_endpoint", "")
    if otel_endpoint is not None and not isinstance(otel_endpoint, str):
        raise SidecarConfigError(
            "deploy.sidecar.otel_endpoint must be a string "
            f"(got {type(otel_endpoint).__name__}: {otel_endpoint!r})",
        )

    cost_tracking = get("cost_tracking", True)
    if not isinstance(cost_tracking, bool):
        raise SidecarConfigError(
            "deploy.sidecar.cost_tracking must be a bool "
            f"(got {type(cost_tracking).__name__}: {cost_tracking!r})",
        )

    for port_field in ("health_port", "agent_port"):
        # Field may be absent — only validate when present.
        sentinel = object()
        value = get(port_field, sentinel)
        if value is sentinel:
            continue
        if not _is_valid_port(value):
            raise SidecarConfigError(
                f"deploy.sidecar.{port_field} must be an integer in "
                f"[{_MIN_PORT}, {_MAX_PORT}] (got {value!r})",
            )

    guardrails = get("guardrails", [])
    if guardrails is None:
        guardrails = []
    if not isinstance(guardrails, list):
        raise SidecarConfigError(
            "deploy.sidecar.guardrails must be a list of strings "
            f"(got {type(guardrails).__name__}: {guardrails!r})",
        )
    for idx, entry in enumerate(guardrails):
        if not isinstance(entry, str) or not entry.strip():
            raise SidecarConfigError(
                f"deploy.sidecar.guardrails[{idx}] must be a non-empty string "
                f"(got {type(entry).__name__}: {entry!r})",
            )

    logger.debug("Sidecar config validated for agent '%s'", getattr(agent_config, "name", "?"))
