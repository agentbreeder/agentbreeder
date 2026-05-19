"""InfraProvisioner — abstract base for cloud infrastructure validators.

Greenfield provisioning (provision/destroy) is deferred to #382/#383/#384;
concrete subclasses raise NotImplementedError for those methods today.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from engine.provisioners.state import InfraState

CheckStatus = Literal["found", "missing", "forbidden", "error"]


class ValidationCheck(BaseModel):
    """One per-resource result from validate_existing."""

    resource: str
    status: CheckStatus
    detail: str


class ValidationResult(BaseModel):
    """Aggregate output of validate_existing."""

    valid: bool
    cloud: str
    region: str
    checks: list[ValidationCheck] = Field(default_factory=list)


class InfraValidationInput(BaseModel):
    """Per-cloud input to validate_existing. Keys vary by cloud."""

    cloud: Literal["aws", "gcp", "azure"]
    region: str
    mode: Literal["simple", "full"] = "simple"
    fields: dict[str, Any] = Field(default_factory=dict)


ProgressCallback = Callable[[str], Awaitable[None]]


class InfraProvisioner(ABC):
    """Abstract base for cloud-specific infrastructure provisioners."""

    @abstractmethod
    async def validate_existing(self, payload: InfraValidationInput) -> ValidationResult:
        """Read-only check that every referenced cloud resource exists."""
        ...

    async def provision(  # noqa: ARG002 - kept for stable signature
        self,
        payload: InfraValidationInput,
        progress: ProgressCallback | None = None,
    ) -> InfraState:
        """Greenfield create-if-not-exists. Pending #382/#383/#384."""
        raise NotImplementedError(
            "Greenfield provisioning is tracked under epic #378 (#382 GCP / #383 AWS / #384 Azure). "
            "For now AgentBreeder only validates existing infrastructure."
        )

    async def destroy(self, state: InfraState) -> None:  # noqa: ARG002
        """Tear down provisioned resources. Pending #382/#383/#384."""
        raise NotImplementedError(
            "Greenfield teardown is tracked under epic #378. "
            "Use cloud-native tools to delete resources you supplied to AgentBreeder."
        )
