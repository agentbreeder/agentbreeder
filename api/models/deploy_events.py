"""DeployEvent — wire format for the SSE deploy progress stream (#387)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class DeployJobStatus(StrEnum):
    PENDING = "pending"
    PENDING_APPROVAL = "pending_approval"
    PROVISIONING = "provisioning"
    BUILDING = "building"
    PUSHING = "pushing"
    DEPLOYING = "deploying"
    HEALTH_CHECK = "health_check"
    REGISTERING = "registering"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


PhaseName = Literal[
    "provisioning", "building", "pushing", "deploying", "health_check", "registering",
]
EventType = Literal["phase", "log", "complete", "error"]


class DeployEvent(BaseModel):
    """One event on the per-job SSE stream. Discriminated by `type`."""

    type: EventType
    job_id: str
    timestamp: datetime
    phase: PhaseName | None = None
    step: int | None = None          # 1-based within phase
    total: int | None = None         # phase total steps
    message: str | None = None
    level: Literal["info", "warn", "error"] | None = None  # only for type="log"
    endpoint_url: str | None = None  # only for type="complete"
    error_code: str | None = None    # only for type="error"

    model_config = {"frozen": True}  # events are immutable once published
