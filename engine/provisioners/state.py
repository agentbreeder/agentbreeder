"""InfraState — persistent record of what cloud resources AgentBreeder is using.

Written by the provisioner on successful validate or provision; consumed by
teardown and re-deploy flows. Lives at .agentbreeder/infra-state.json in the
agent project directory.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_STATE_PATH = Path(".agentbreeder/infra-state.json")

Cloud = Literal["aws", "gcp", "azure", "local"]


class InfraState(BaseModel):
    """Snapshot of cloud resources AgentBreeder knows about for this project."""

    cloud: Cloud
    region: str
    provisioned_by: str
    provisioned_at: datetime
    mode: Literal["validated", "provisioned"]
    resources: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = DEFAULT_STATE_PATH) -> InfraState:
        """Read state from disk. Raises FileNotFoundError if absent."""
        raw = json.loads(path.read_text())
        return cls.model_validate(raw)

    @classmethod
    def load_or_none(cls, path: Path = DEFAULT_STATE_PATH) -> InfraState | None:
        try:
            return cls.load(path)
        except FileNotFoundError:
            return None

    def save(self, path: Path = DEFAULT_STATE_PATH) -> None:
        """Write state to disk, creating parent directory if needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))
