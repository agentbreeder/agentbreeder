"""DeployEvent shape: discriminator works for each `type` value, rejects malformed input."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from api.models.deploy_events import DeployEvent, DeployJobStatus


def _evt(**overrides) -> dict:
    base = {"type": "phase", "job_id": "job-1", "timestamp": datetime.now(UTC), "phase": "provisioning"}
    base.update(overrides)
    return base


def test_phase_event_round_trips() -> None:
    evt = DeployEvent.model_validate(_evt())
    raw = evt.model_dump_json()
    rev = DeployEvent.model_validate_json(raw)
    assert rev.type == "phase"
    assert rev.phase == "provisioning"


def test_log_event_requires_level() -> None:
    payload = _evt(type="log", phase=None, message="building image", level="info")
    evt = DeployEvent.model_validate(payload)
    assert evt.level == "info"


def test_complete_event_carries_endpoint_url() -> None:
    payload = _evt(type="complete", phase=None, endpoint_url="https://x-uc.a.run.app")
    evt = DeployEvent.model_validate(payload)
    assert evt.endpoint_url == "https://x-uc.a.run.app"


def test_error_event_carries_error_code() -> None:
    payload = _evt(type="error", phase=None, error_code="provision_failed", message="VPC quota exceeded")
    evt = DeployEvent.model_validate(payload)
    assert evt.error_code == "provision_failed"


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValidationError):
        DeployEvent.model_validate(_evt(type="bogus"))


def test_unknown_phase_rejected() -> None:
    with pytest.raises(ValidationError):
        DeployEvent.model_validate(_evt(phase="lift-off"))


def test_job_status_enum_values() -> None:
    assert set(DeployJobStatus) >= {
        DeployJobStatus.pending,
        DeployJobStatus.pending_approval,
        DeployJobStatus.provisioning,
        DeployJobStatus.building,
        DeployJobStatus.pushing,
        DeployJobStatus.deploying,
        DeployJobStatus.health_checking,
        DeployJobStatus.registering,
        DeployJobStatus.completed,
        DeployJobStatus.failed,
        DeployJobStatus.timed_out,
    }
