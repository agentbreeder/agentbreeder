"""AgentBreeder observability sidecar.

Issue #73: Auto-inject OTel observability sidecar.

Every deployed agent gets a sidecar container injected automatically that provides:
- OpenTelemetry traces for every LLM call, tool use, and agent step
- Token counting and cost attribution
- Guardrail enforcement (PII detection, content filtering)
- Health check endpoint on port 8090

Usage from a deployer:

    from engine.sidecar import SidecarConfig, inject_sidecar

    config = SidecarConfig(enabled=True, guardrails=["pii_detection"])
    task_def = inject_sidecar(task_def, config)         # ECS
    service_spec = inject_cloudrun_sidecar(spec, config) # GCP Cloud Run
"""

from .config import SidecarConfig
from .injector import inject_cloudrun_sidecar, inject_sidecar

__all__ = ["SidecarConfig", "inject_sidecar", "inject_cloudrun_sidecar"]
