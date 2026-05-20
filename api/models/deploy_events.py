"""DeployEvent — wire format for the SSE deploy progress stream (#387)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from api.models.enums import DeployJobStatus  # re-exported for convenience

__all__ = ["DeployEvent", "DeployJobStatus", "EventType", "PhaseName"]

PhaseName = Literal[
    "provisioning",
    "building",
    "pushing",
    "deploying",
    "health_checking",
    "registering",
]
EventType = Literal["phase", "log", "complete", "error"]


class DeployEvent(BaseModel):
    """One event on the per-job SSE stream. Discriminated by `type`."""

    type: EventType
    job_id: str
    timestamp: datetime
    phase: PhaseName | None = None
    step: int | None = None
    total: int | None = None
    message: str | None = None
    level: Literal["info", "warn", "error"] | None = None
    endpoint_url: str | None = None
    error_code: str | None = None

    model_config = {"frozen": True}
