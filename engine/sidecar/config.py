"""Sidecar configuration dataclass.

Issue #73: Auto-inject OTel observability sidecar.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SidecarConfig:
    """Configuration for the AgentBreeder observability sidecar container.

    Populated from deploy.sidecar in agent.yaml or from environment defaults.
    """

    enabled: bool = True
    image: str = "rajits/agentbreeder-sidecar:latest"
    otel_endpoint: str = field(
        default_factory=lambda: os.getenv(
            "OPENTELEMETRY_ENDPOINT", "http://localhost:4317"
        )
    )
    guardrails: list[str] = field(default_factory=list)
    cost_tracking: bool = True
    health_port: int = 8090

    @classmethod
    def from_deploy_config(cls, deploy_sidecar: Optional[dict]) -> "SidecarConfig":
        """Build a SidecarConfig from the deploy.sidecar dict in agent.yaml.

        Returns a default (enabled) config if deploy_sidecar is None.
        """
        if deploy_sidecar is None:
            return cls()
        return cls(
            enabled=deploy_sidecar.get("enabled", True),
            guardrails=deploy_sidecar.get("guardrails", []),
            otel_endpoint=deploy_sidecar.get(
                "otel_endpoint",
                os.getenv("OPENTELEMETRY_ENDPOINT", "http://localhost:4317"),
            ),
            cost_tracking=deploy_sidecar.get("cost_tracking", True),
        )
