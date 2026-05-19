"""Base interface for cloud-specific deployers.

Every cloud target implements this interface.
Cloud-specific logic MUST stay inside engine/deployers/ — never leak it elsewhere.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel

from engine.config_parser import AgentConfig
from engine.runtimes.base import ContainerImage


class InfraResult(BaseModel):
    """Result of provisioning cloud infrastructure."""

    endpoint_url: str
    resource_ids: dict[str, str]


class DeployResult(BaseModel):
    """Result of deploying an agent."""

    endpoint_url: str
    container_id: str
    status: str
    agent_name: str
    version: str


class HealthStatus(BaseModel):
    """Result of a health check."""

    healthy: bool
    checks: dict[str, bool]


class ExistingDeployment(BaseModel):
    """Lightweight snapshot of an existing cloud-side resource.

    Returned by :meth:`BaseDeployer._lookup_existing` so that ``deploy()`` can
    short-circuit when the target name already maps to a healthy service
    (idempotent re-deploys) or clean up before redeploying a stale one.
    """

    # Free-form status string. "healthy" short-circuits deploy(); anything else
    # triggers cleanup. Suggested values: "healthy" | "unhealthy" | "unknown".
    status: str
    # External endpoint URL exposed by the existing service, if known.
    url: str | None = None
    # Cloud-native ID (ARN / resource id / image URI / container id) used as
    # the container_id in the resulting DeployResult.
    resource_id: str | None = None


class BaseDeployer(ABC):
    """Abstract base class for cloud-specific deployers.

    Each cloud target (AWS ECS, GCP Cloud Run, local Docker Compose, etc.)
    implements this to handle infrastructure provisioning and agent deployment.
    """

    @abstractmethod
    async def provision(self, config: AgentConfig) -> InfraResult:
        """Provision cloud infrastructure for the agent."""
        ...

    @abstractmethod
    async def deploy(self, config: AgentConfig, image: ContainerImage | None) -> DeployResult:
        """Build and deploy the agent container."""
        ...

    @abstractmethod
    async def health_check(self, deploy_result: DeployResult) -> HealthStatus:
        """Verify the deployed agent is healthy."""
        ...

    @abstractmethod
    async def teardown(self, agent_name: str) -> None:
        """Remove a deployed agent and clean up resources."""
        ...

    @abstractmethod
    async def get_logs(self, agent_name: str, since: datetime | None = None) -> list[str]:
        """Retrieve logs from a deployed agent."""
        ...

    async def _lookup_existing(self, agent_name: str) -> ExistingDeployment | None:
        """Return an :class:`ExistingDeployment` snapshot for ``agent_name``, or None.

        Used by ``deploy()`` to short-circuit when the cloud already holds a
        healthy resource with the target name (idempotent re-deploy) and to
        trigger a cleanup before redeploying an unhealthy / unknown one.

        Default implementation returns ``None`` — meaning "no existing resource
        known". Concrete deployers override this to perform a cheap, read-only
        lookup against their cloud (``describe_service`` / ``get_service`` /
        ``read_namespaced_deployment`` / etc.). The override MUST swallow
        not-found exceptions and return ``None`` instead of raising.
        """
        return None

    def get_aps_env_vars(self) -> dict[str, str]:
        """Return AGENTBREEDER_URL + AGENTBREEDER_API_KEY for injection into agent containers.

        Every deployed agent container receives these so the @agentbreeder/aps-client
        can call the central AgentBreeder API for RAG, memory, cost tracking, and tracing.
        """
        return {
            "AGENTBREEDER_URL": os.environ.get("AGENTBREEDER_URL", "http://agentbreeder-api:8000"),
            "AGENTBREEDER_API_KEY": os.environ.get("AGENTBREEDER_API_KEY", ""),
        }
