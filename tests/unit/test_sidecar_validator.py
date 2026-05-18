"""Unit tests for engine.sidecar.validate_sidecar_config (W4-37).

The validator runs at the start of every deployer's deploy() so broken
sidecar configs fail at submit (clear error) instead of at health-check
(cryptic timeout).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from engine.sidecar import SidecarConfigError, validate_sidecar_config


@dataclass
class _Deploy:
    sidecar: dict[str, Any] | None = None


@dataclass
class _Cfg:
    name: str = "demo"
    deploy: _Deploy | None = None


def _cfg(sidecar: dict[str, Any] | None) -> _Cfg:
    return _Cfg(deploy=_Deploy(sidecar=sidecar))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_validate_noop_when_deploy_missing() -> None:
    """An agent config with no deploy attribute is a no-op (orchestration etc.)."""
    validate_sidecar_config(_Cfg(deploy=None))


def test_validate_noop_when_sidecar_absent() -> None:
    """No deploy.sidecar block → no-op."""
    validate_sidecar_config(_cfg(None))


def test_validate_accepts_minimal_valid_config() -> None:
    validate_sidecar_config(
        _cfg(
            {
                "enabled": True,
                "image": "rajits/agentbreeder-sidecar:latest",
                "guardrails": ["pii_detection"],
            }
        )
    )


def test_validate_accepts_full_valid_config() -> None:
    validate_sidecar_config(
        _cfg(
            {
                "enabled": False,
                "image": "rajits/agentbreeder-sidecar:v2",
                "guardrails": ["pii_detection", "content_filter"],
                "otel_endpoint": "http://otel:4318",
                "cost_tracking": True,
                "health_port": 8080,
                "agent_port": 8081,
            }
        )
    )


# ---------------------------------------------------------------------------
# Field-level errors
# ---------------------------------------------------------------------------


def test_validate_rejects_non_bool_enabled() -> None:
    with pytest.raises(SidecarConfigError, match="enabled"):
        validate_sidecar_config(_cfg({"enabled": "yes"}))


def test_validate_rejects_empty_image() -> None:
    with pytest.raises(SidecarConfigError, match="image"):
        validate_sidecar_config(_cfg({"image": ""}))


def test_validate_rejects_non_string_image() -> None:
    with pytest.raises(SidecarConfigError, match="image"):
        validate_sidecar_config(_cfg({"image": 42}))


def test_validate_rejects_non_string_otel_endpoint() -> None:
    with pytest.raises(SidecarConfigError, match="otel_endpoint"):
        validate_sidecar_config(_cfg({"otel_endpoint": 4318}))


def test_validate_rejects_non_bool_cost_tracking() -> None:
    with pytest.raises(SidecarConfigError, match="cost_tracking"):
        validate_sidecar_config(_cfg({"cost_tracking": "true"}))


@pytest.mark.parametrize("port", [0, -1, 70000, "not-a-port", 1.5])
def test_validate_rejects_invalid_health_port(port: Any) -> None:
    with pytest.raises(SidecarConfigError, match="health_port"):
        validate_sidecar_config(_cfg({"health_port": port}))


@pytest.mark.parametrize("port", [0, -1, 65536, "abc"])
def test_validate_rejects_invalid_agent_port(port: Any) -> None:
    with pytest.raises(SidecarConfigError, match="agent_port"):
        validate_sidecar_config(_cfg({"agent_port": port}))


def test_validate_rejects_non_list_guardrails() -> None:
    with pytest.raises(SidecarConfigError, match="guardrails"):
        validate_sidecar_config(_cfg({"guardrails": "pii_detection"}))


def test_validate_rejects_empty_guardrail_entry() -> None:
    with pytest.raises(SidecarConfigError, match=r"guardrails\[1\]"):
        validate_sidecar_config(_cfg({"guardrails": ["pii_detection", ""]}))


def test_validate_rejects_non_string_guardrail_entry() -> None:
    with pytest.raises(SidecarConfigError, match=r"guardrails\[0\]"):
        validate_sidecar_config(_cfg({"guardrails": [{"name": "x"}]}))


def test_validate_accepts_none_guardrails() -> None:
    """Explicit `guardrails: null` is treated as empty list."""
    validate_sidecar_config(_cfg({"guardrails": None}))


# ---------------------------------------------------------------------------
# SidecarConfigError is a ValueError (so callers can catch it generically)
# ---------------------------------------------------------------------------


def test_sidecar_config_error_is_value_error() -> None:
    assert issubclass(SidecarConfigError, ValueError)
