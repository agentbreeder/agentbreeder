"""Injects the cross-cutting-concerns sidecar into deploy configurations.

Track J: tracing, cost, guardrails, A2A, MCP.

Three injection targets are supported here:
  - inject_sidecar()                 — AWS ECS task definition dict
  - inject_cloudrun_sidecar()        — GCP Cloud Run service spec dict
  - inject_compose_sidecar()         — docker-compose service dict
                                       (used by engine.deployers.docker_compose)

All functions are idempotent: if a container named 'agentbreeder-sidecar' is
already present the function returns the input unchanged.

Auto-injection helper:
  - should_inject(agent_config)      — returns True when the agent config
                                       declares guardrails, MCP tools, or A2A
                                       and AGENTBREEDER_SIDECAR != disabled
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

from .config import SidecarConfig

logger = logging.getLogger(__name__)

SIDECAR_NAME = "agentbreeder-sidecar"

# Env-var values that disable the sidecar (case-insensitive).
_DISABLED_VALUES = {"disabled", "off", "0", "false", "no"}


def inject_sidecar(task_definition: dict[str, Any], config: SidecarConfig) -> dict[str, Any]:
    """Inject the sidecar container into an ECS task definition dict.

    The sidecar is appended to containerDefinitions with essential=False so that
    a sidecar crash does not bring down the agent container.

    Args:
        task_definition: An ECS TaskDefinition dict (may or may not have
                         containerDefinitions already).
        config:          SidecarConfig controlling the sidecar image, OTel
                         endpoint, guardrails, etc.

    Returns:
        A new dict — the original is never mutated.
    """
    if not config.enabled:
        logger.debug("Sidecar injection disabled — skipping")
        return task_definition

    result = copy.deepcopy(task_definition)
    containers: list[dict[str, Any]] = result.setdefault("containerDefinitions", [])

    # Idempotency guard
    if any(c.get("name") == SIDECAR_NAME for c in containers):
        logger.debug("Sidecar already present in task definition — skipping injection")
        return result

    sidecar_container: dict[str, Any] = {
        "name": SIDECAR_NAME,
        "image": config.image,
        "essential": False,
        # #500: least-privilege hardening — non-root, read-only root fs, no caps.
        "user": "65532",
        "readonlyRootFilesystem": True,
        "linuxParameters": {"capabilities": {"drop": ["ALL"]}},
        "portMappings": [{"containerPort": config.health_port, "protocol": "tcp"}],
        "environment": [
            {"name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": config.otel_endpoint},
            {"name": "AB_COST_TRACKING", "value": str(config.cost_tracking).lower()},
            {"name": "AB_GUARDRAILS", "value": ",".join(config.guardrails)},
            {
                "name": "AGENTBREEDER_SIDECAR_AGENT_URL",
                "value": f"http://localhost:{config.agent_port}",
            },
        ],
        "healthCheck": {
            "command": [
                "CMD-SHELL",
                f"curl -f http://localhost:{config.health_port}/health || exit 1",
            ],
            "interval": 30,
            "timeout": 5,
            "retries": 3,
        },
    }

    if config.mcp_servers:
        sidecar_container["environment"].append(
            {
                "name": "AGENTBREEDER_SIDECAR_MCP_SERVERS",
                "value": json.dumps(config.mcp_servers),
            }
        )

    containers.append(sidecar_container)
    logger.info("Injected observability sidecar into ECS task definition")
    return result


def inject_cloudrun_sidecar(service_spec: dict[str, Any], config: SidecarConfig) -> dict[str, Any]:
    """Inject the sidecar container into a Cloud Run service spec dict.

    The sidecar is appended to spec.template.spec.containers.  Cloud Run
    multi-container support (sidecars) requires the Cloud Run v2 API.

    Args:
        service_spec: A Cloud Run Service resource dict.
        config:       SidecarConfig controlling the sidecar image, OTel
                      endpoint, etc.

    Returns:
        A new dict — the original is never mutated.
    """
    if not config.enabled:
        logger.debug("Sidecar injection disabled — skipping")
        return service_spec

    result = copy.deepcopy(service_spec)

    # Navigate / create the nested path
    spec = result.setdefault("spec", {})
    template = spec.setdefault("template", {})
    tmpl_spec = template.setdefault("spec", {})
    containers: list[dict[str, Any]] = tmpl_spec.setdefault("containers", [])

    # Idempotency guard
    if any(c.get("name") == SIDECAR_NAME for c in containers):
        logger.debug("Sidecar already present in Cloud Run spec — skipping injection")
        return result

    sidecar: dict[str, Any] = {
        "name": SIDECAR_NAME,
        "image": config.image,
        "env": [
            {"name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": config.otel_endpoint},
            {"name": "AB_COST_TRACKING", "value": str(config.cost_tracking).lower()},
            {"name": "AB_GUARDRAILS", "value": ",".join(config.guardrails)},
            {
                "name": "AGENTBREEDER_SIDECAR_AGENT_URL",
                "value": f"http://localhost:{config.agent_port}",
            },
        ],
        "ports": [{"containerPort": config.health_port}],
    }

    if config.mcp_servers:
        sidecar["env"].append(
            {
                "name": "AGENTBREEDER_SIDECAR_MCP_SERVERS",
                "value": json.dumps(config.mcp_servers),
            }
        )

    containers.append(sidecar)
    logger.info("Injected observability sidecar into Cloud Run service spec")
    return result


def inject_compose_sidecar(
    services: dict[str, Any],
    config: SidecarConfig,
    *,
    agent_service: str = "agent",
) -> dict[str, Any]:
    """Inject the sidecar service into a docker-compose `services` dict.

    Args:
        services:        Map of service name → service dict.
        config:          SidecarConfig controlling the sidecar image / env.
        agent_service:   The service name to forward inbound traffic to.

    Returns:
        A new dict — the original is never mutated.
    """
    if not config.enabled:
        logger.debug("Sidecar injection disabled — skipping (compose)")
        return services

    result = copy.deepcopy(services)
    if SIDECAR_NAME in result:
        logger.debug("Sidecar already present in compose services — skipping")
        return result

    result[SIDECAR_NAME] = {
        "image": config.image,
        "environment": {
            "AGENT_NAME": "${AGENT_NAME:-agent}",
            "AGENT_VERSION": "${AGENT_VERSION:-}",
            "AGENT_AUTH_TOKEN": "${AGENT_AUTH_TOKEN:-}",
            "AGENTBREEDER_SIDECAR_AGENT_URL": (f"http://{agent_service}:{config.agent_port}"),
            "OTEL_EXPORTER_OTLP_ENDPOINT": config.otel_endpoint,
            "AGENTBREEDER_API_URL": "${AGENTBREEDER_API_URL:-}",
            "AGENTBREEDER_API_TOKEN": "${AGENTBREEDER_API_TOKEN:-}",
            "AB_GUARDRAILS": ",".join(config.guardrails),
        },
        "ports": [f"{config.health_port}:{config.health_port}"],
        "depends_on": [agent_service],
    }
    if config.mcp_servers:
        result[SIDECAR_NAME]["environment"]["AGENTBREEDER_SIDECAR_MCP_SERVERS"] = json.dumps(
            config.mcp_servers
        )
    logger.info("Injected sidecar into docker-compose services")
    return result


def should_inject(agent_config: Any) -> bool:
    """Return True when an agent's config requires sidecar injection.

    Triggers on any of:
      - guardrails declared at the top level of agent.yaml
      - tools that are MCP servers (engine treats `mcp_servers` as a separate
        list, but tools entries with `type=mcp` also qualify)
      - an a2a: block on the agent

    The env-var bypass `AGENTBREEDER_SIDECAR=disabled` short-circuits to False
    so local dev never has to fight the deployer.
    """
    if _is_env_disabled():
        logger.debug("AGENTBREEDER_SIDECAR=disabled — skipping injection")
        return False

    guardrails = getattr(agent_config, "guardrails", None) or []
    if guardrails:
        return True

    mcp_servers = getattr(agent_config, "mcp_servers", None) or []
    if mcp_servers:
        return True

    tools = getattr(agent_config, "tools", None) or []
    for tool in tools:
        # Either a registry ref (str-like with `mcp/` prefix) or an inline dict.
        ref = getattr(tool, "ref", None)
        if isinstance(ref, str) and ref.startswith(("tools/mcp", "mcp/")):
            return True
        ttype = getattr(tool, "type", None)
        if ttype and str(ttype).lower() == "mcp":
            return True

    a2a = getattr(agent_config, "a2a", None)
    if a2a:
        return True

    return False


def _is_env_disabled() -> bool:
    return os.getenv("AGENTBREEDER_SIDECAR", "").strip().lower() in _DISABLED_VALUES
