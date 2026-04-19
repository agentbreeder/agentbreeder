"""Injects the observability sidecar into deploy configurations.

Issue #73: Auto-inject OTel observability sidecar.

Two injection targets are supported:
  - inject_sidecar()           — AWS ECS task definition dict
  - inject_cloudrun_sidecar()  — GCP Cloud Run service spec dict

Both functions are idempotent: if a container named 'agentbreeder-sidecar' is
already present the function returns the input unchanged.
"""

from __future__ import annotations

import copy
import logging

from .config import SidecarConfig

logger = logging.getLogger(__name__)

SIDECAR_NAME = "agentbreeder-sidecar"


def inject_sidecar(task_definition: dict, config: SidecarConfig) -> dict:
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
    containers: list[dict] = result.setdefault("containerDefinitions", [])

    # Idempotency guard
    if any(c.get("name") == SIDECAR_NAME for c in containers):
        logger.debug("Sidecar already present in task definition — skipping injection")
        return result

    sidecar_container: dict = {
        "name": SIDECAR_NAME,
        "image": config.image,
        "essential": False,
        "portMappings": [{"containerPort": config.health_port, "protocol": "tcp"}],
        "environment": [
            {"name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": config.otel_endpoint},
            {"name": "AB_COST_TRACKING", "value": str(config.cost_tracking).lower()},
            {"name": "AB_GUARDRAILS", "value": ",".join(config.guardrails)},
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

    containers.append(sidecar_container)
    logger.info("Injected observability sidecar into ECS task definition")
    return result


def inject_cloudrun_sidecar(service_spec: dict, config: SidecarConfig) -> dict:
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
    containers: list[dict] = tmpl_spec.setdefault("containers", [])

    # Idempotency guard
    if any(c.get("name") == SIDECAR_NAME for c in containers):
        logger.debug("Sidecar already present in Cloud Run spec — skipping injection")
        return result

    sidecar: dict = {
        "name": SIDECAR_NAME,
        "image": config.image,
        "env": [
            {"name": "OTEL_EXPORTER_OTLP_ENDPOINT", "value": config.otel_endpoint},
            {"name": "AB_COST_TRACKING", "value": str(config.cost_tracking).lower()},
            {"name": "AB_GUARDRAILS", "value": ",".join(config.guardrails)},
        ],
        "ports": [{"containerPort": config.health_port}],
    }

    containers.append(sidecar)
    logger.info("Injected observability sidecar into Cloud Run service spec")
    return result
