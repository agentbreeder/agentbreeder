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


class DataBackendRequest(BaseModel):
    """Input to :meth:`InfraProvisioner.provision_data_backend`.

    Provisions ONE managed data store (Postgres for pgvector, Redis for
    memory) INTO the deploy's existing BYO network — it does NOT create a
    greenfield VPC the way :meth:`provision` does. The auto-provision deploy
    hook builds this from the agent's ``deploy.env_vars`` after the deployer
    has provisioned (and recorded) the agent's own network.

    ``network`` carries the existing, cloud-specific network identifiers the
    new data store must join so the agent container can reach it privately:

    * AWS  → ``vpc_id``, ``subnet_ids``, ``agent_security_group_ids``
    * GCP  → ``vpc_network`` (and ``project``/``region`` via ``fields``)
    * Azure→ ``vnet_name``, ``db_subnet_id`` (and resource-group via ``fields``)
    """

    cloud: Literal["aws", "gcp", "azure"]
    region: str
    agent_name: str
    agent_version: str = "0.0.0"
    engine: Literal["postgres", "redis"] = "postgres"
    network: dict[str, Any] = Field(default_factory=dict)
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

    async def provision_data_backend(  # noqa: ARG002 - stable signature
        self,
        request: DataBackendRequest,
        progress: ProgressCallback | None = None,
    ) -> InfraState:
        """Provision a single managed data store into an existing BYO network.

        Used by the deploy pipeline to auto-provision a managed pgvector
        (Postgres) or memory (Redis) backend when the agent declares a
        knowledge base / memory WITHOUT an explicit ``backend_url``. Returns an
        :class:`InfraState` whose ``resources`` carries the cloud-specific
        store descriptor (``rds`` / ``cloud_sql`` / ``postgres`` / ``redis``)
        that the DSN builders in ``engine/deployers/_pgvector_dsn.py`` consume.

        Concrete subclasses override this per cloud.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement provision_data_backend yet."
        )

    async def destroy_data_backend(self, state: InfraState) -> None:
        """Tear down a data store created by :meth:`provision_data_backend`.

        Default: delegate to :meth:`destroy`, which is safe for clouds whose
        focused state records ONLY the resources AgentBreeder created (AWS:
        ``rds`` + DB security group; GCP: ``cloud_sql``) and skips absent keys.
        Clouds whose :meth:`destroy` would over-reach on a BYO footprint (e.g.
        Azure deletes the whole resource group) override this to remove only the
        data store.
        """
        await self.destroy(state)
