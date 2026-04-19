"""Unit tests for the OTel sidecar injector.

Issue #73: Auto-inject OTel observability sidecar.
"""

from __future__ import annotations

import pytest

from engine.sidecar import SidecarConfig, inject_cloudrun_sidecar, inject_sidecar
from engine.sidecar.config import SidecarConfig as SidecarConfigDirect

SIDECAR_NAME = "agentbreeder-sidecar"


# ---------------------------------------------------------------------------
# inject_sidecar (ECS task definition)
# ---------------------------------------------------------------------------


def test_inject_sidecar_adds_container() -> None:
    """Sidecar container should be appended to containerDefinitions."""
    task_def = {"containerDefinitions": [{"name": "agent", "image": "my-agent:latest"}]}
    config = SidecarConfig(enabled=True)
    result = inject_sidecar(task_def, config)

    names = [c["name"] for c in result["containerDefinitions"]]
    assert SIDECAR_NAME in names
    assert "agent" in names
    assert len(names) == 2


def test_inject_sidecar_disabled() -> None:
    """When enabled=False the task definition is returned unchanged."""
    task_def = {"containerDefinitions": [{"name": "agent", "image": "my-agent:latest"}]}
    config = SidecarConfig(enabled=False)
    result = inject_sidecar(task_def, config)

    assert len(result["containerDefinitions"]) == 1
    assert result["containerDefinitions"][0]["name"] == "agent"


def test_inject_sidecar_idempotent() -> None:
    """Calling inject_sidecar twice should not duplicate the sidecar."""
    task_def: dict = {"containerDefinitions": []}
    config = SidecarConfig(enabled=True)
    result = inject_sidecar(inject_sidecar(task_def, config), config)

    sidecar_containers = [
        c for c in result["containerDefinitions"] if c["name"] == SIDECAR_NAME
    ]
    assert len(sidecar_containers) == 1


def test_inject_sidecar_does_not_mutate_input() -> None:
    """inject_sidecar must not mutate the original task definition."""
    task_def = {"containerDefinitions": [{"name": "agent", "image": "my-agent:latest"}]}
    original_len = len(task_def["containerDefinitions"])
    inject_sidecar(task_def, SidecarConfig(enabled=True))

    assert len(task_def["containerDefinitions"]) == original_len


def test_inject_sidecar_sidecar_is_non_essential() -> None:
    """The sidecar should be non-essential so it can't kill the agent container."""
    task_def: dict = {"containerDefinitions": []}
    result = inject_sidecar(task_def, SidecarConfig(enabled=True))
    sidecar = next(c for c in result["containerDefinitions"] if c["name"] == SIDECAR_NAME)
    assert sidecar["essential"] is False


def test_inject_sidecar_otel_env_forwarded() -> None:
    """OTEL endpoint should be set in the sidecar environment."""
    task_def: dict = {"containerDefinitions": []}
    config = SidecarConfig(enabled=True, otel_endpoint="http://collector:4317")
    result = inject_sidecar(task_def, config)
    sidecar = next(c for c in result["containerDefinitions"] if c["name"] == SIDECAR_NAME)
    env_map = {e["name"]: e["value"] for e in sidecar["environment"]}
    assert env_map["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://collector:4317"


def test_inject_sidecar_guardrails_forwarded() -> None:
    """Guardrails list should be comma-joined into AB_GUARDRAILS env var."""
    task_def: dict = {"containerDefinitions": []}
    config = SidecarConfig(enabled=True, guardrails=["pii_detection", "content_filter"])
    result = inject_sidecar(task_def, config)
    sidecar = next(c for c in result["containerDefinitions"] if c["name"] == SIDECAR_NAME)
    env_map = {e["name"]: e["value"] for e in sidecar["environment"]}
    assert env_map["AB_GUARDRAILS"] == "pii_detection,content_filter"


def test_inject_sidecar_empty_task_def() -> None:
    """inject_sidecar should handle task definitions without containerDefinitions."""
    result = inject_sidecar({}, SidecarConfig(enabled=True))
    assert SIDECAR_NAME in [c["name"] for c in result["containerDefinitions"]]


# ---------------------------------------------------------------------------
# inject_cloudrun_sidecar (GCP Cloud Run service spec)
# ---------------------------------------------------------------------------


def test_inject_cloudrun_sidecar() -> None:
    """Sidecar container should be appended to the Cloud Run containers list."""
    service_spec: dict = {}
    config = SidecarConfig(enabled=True)
    result = inject_cloudrun_sidecar(service_spec, config)

    containers = result["spec"]["template"]["spec"]["containers"]
    assert any(c["name"] == SIDECAR_NAME for c in containers)


def test_inject_cloudrun_sidecar_disabled() -> None:
    """When enabled=False the spec is returned unchanged."""
    service_spec: dict = {}
    result = inject_cloudrun_sidecar(service_spec, SidecarConfig(enabled=False))
    assert result == {}


def test_inject_cloudrun_sidecar_idempotent() -> None:
    """Calling inject_cloudrun_sidecar twice should not duplicate the sidecar."""
    service_spec: dict = {}
    config = SidecarConfig(enabled=True)
    result = inject_cloudrun_sidecar(inject_cloudrun_sidecar(service_spec, config), config)
    containers = result["spec"]["template"]["spec"]["containers"]
    sidecar_containers = [c for c in containers if c["name"] == SIDECAR_NAME]
    assert len(sidecar_containers) == 1


def test_inject_cloudrun_sidecar_does_not_mutate_input() -> None:
    """inject_cloudrun_sidecar must not mutate the original spec."""
    service_spec: dict = {"spec": {"template": {"spec": {"containers": []}}}}
    inject_cloudrun_sidecar(service_spec, SidecarConfig(enabled=True))
    assert service_spec["spec"]["template"]["spec"]["containers"] == []


def test_inject_cloudrun_preserves_existing_containers() -> None:
    """Existing containers should not be removed when the sidecar is injected."""
    service_spec = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": "agent", "image": "my-agent:latest"}]
                }
            }
        }
    }
    result = inject_cloudrun_sidecar(service_spec, SidecarConfig(enabled=True))
    names = [c["name"] for c in result["spec"]["template"]["spec"]["containers"]]
    assert "agent" in names
    assert SIDECAR_NAME in names


# ---------------------------------------------------------------------------
# SidecarConfig.from_deploy_config
# ---------------------------------------------------------------------------


def test_sidecar_config_from_none() -> None:
    """from_deploy_config(None) should return default enabled config."""
    cfg = SidecarConfigDirect.from_deploy_config(None)
    assert cfg.enabled is True
    assert cfg.cost_tracking is True


def test_sidecar_config_from_dict() -> None:
    cfg = SidecarConfigDirect.from_deploy_config(
        {
            "enabled": False,
            "guardrails": ["pii_detection"],
            "otel_endpoint": "http://custom:4317",
            "cost_tracking": False,
        }
    )
    assert cfg.enabled is False
    assert cfg.guardrails == ["pii_detection"]
    assert cfg.otel_endpoint == "http://custom:4317"
    assert cfg.cost_tracking is False
