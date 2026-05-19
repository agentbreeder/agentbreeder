"""AgentBreeder observability + cross-cutting concerns sidecar.

Originated from issue #73 (OTel observability) — generalised in Track J of the
v2 platform spec to handle tracing, cost, guardrails, A2A, and MCP in one Go
binary (rajits/agentbreeder-sidecar).

Usage from a deployer:

    from engine.sidecar import (
        SidecarConfig,
        inject_sidecar,
        should_inject,
        validate_sidecar_config,
    )

    validate_sidecar_config(agent_config)              # pre-validate
    if should_inject(agent_config):
        sidecar = SidecarConfig.from_agent_config(agent_config)
        task_def = inject_sidecar(task_def, sidecar)              # ECS
        service_spec = inject_cloudrun_sidecar(spec, sidecar)     # GCP Cloud Run
"""

from .config import SidecarConfig, SidecarConfigError, validate_sidecar_config
from .injector import inject_cloudrun_sidecar, inject_sidecar, should_inject

__all__ = [
    "SidecarConfig",
    "SidecarConfigError",
    "inject_sidecar",
    "inject_cloudrun_sidecar",
    "should_inject",
    "validate_sidecar_config",
]
