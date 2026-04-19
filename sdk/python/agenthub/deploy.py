"""Deploy and prompt configuration dataclasses for AgentBreeder agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeployConfig:
    """Deployment target configuration."""

    cloud: str = "local"
    runtime: str | None = None
    region: str | None = None
    scaling: dict[str, Any] | None = None
    resources: dict[str, str] | None = None
    env_vars: dict[str, str] = field(default_factory=dict)
    secrets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML output."""
        d: dict[str, Any] = {"cloud": self.cloud}
        if self.runtime is not None:
            d["runtime"] = self.runtime
        if self.region is not None:
            d["region"] = self.region
        if self.scaling is not None:
            d["scaling"] = self.scaling
        if self.resources is not None:
            d["resources"] = self.resources
        if self.env_vars:
            d["env_vars"] = self.env_vars
        if self.secrets:
            d["secrets"] = self.secrets
        return d


@dataclass
class PromptConfig:
    """Prompt configuration for an agent."""

    system: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML output."""
        return {"system": self.system}
