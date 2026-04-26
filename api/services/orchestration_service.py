"""Orchestration Service — in-memory store for orchestration configs and execution.

Provides:
- CRUD for orchestration definitions
- Orchestration execution (via engine/orchestrator.py)
- Deploy status management
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from engine.orchestration_parser import (
    AgentRef,
    OrchestrationConfig,
    OrchestrationDeployConfig,
    OrchestrationStrategy,
    SharedStateConfig,
    SupervisorConfig,
)
from engine.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


class OrchestrationRecord:
    """In-memory orchestration record."""

    def __init__(
        self,
        *,
        orch_id: str,
        name: str,
        version: str,
        description: str,
        team: str | None,
        owner: str | None,
        strategy: str,
        agents_config: dict[str, Any],
        shared_state_config: dict[str, Any],
        deploy_config: dict[str, Any],
        status: str,
        endpoint_url: str | None,
        config_snapshot: dict[str, Any],
        tags: list[str],
        created_at: str,
        updated_at: str,
    ) -> None:
        self.id = orch_id
        self.name = name
        self.version = version
        self.description = description
        self.team = team
        self.owner = owner
        self.strategy = strategy
        self.agents_config = agents_config
        self.shared_state_config = shared_state_config
        self.deploy_config = deploy_config
        self.status = status
        self.endpoint_url = endpoint_url
        self.config_snapshot = config_snapshot
        self.tags = tags
        self.created_at = created_at
        self.updated_at = updated_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "team": self.team,
            "owner": self.owner,
            "strategy": self.strategy,
            "agents_config": self.agents_config,
            "shared_state_config": self.shared_state_config,
            "deploy_config": self.deploy_config,
            "status": self.status,
            "endpoint_url": self.endpoint_url,
            "config_snapshot": self.config_snapshot,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class OrchestrationStore:
    """In-memory store for orchestrations.

    Will be replaced by PostgreSQL when the real DB is connected.
    """

    def __init__(self) -> None:
        self._orchestrations: dict[str, OrchestrationRecord] = {}
        self._seed_demo_data()

    def _seed_demo_data(self) -> None:
        """Seed a demo orchestration for development."""
        now = datetime.now(UTC).isoformat()
        demo_agents = {
            "billing-agent": {"ref": "agents/billing-support"},
            "technical-agent": {"ref": "agents/technical-support"},
            "general-agent": {"ref": "agents/general-support"},
        }
        demo = OrchestrationRecord(
            orch_id=str(uuid.uuid4()),
            name="customer-support-pipeline",
            version="1.0.0",
            description="Routes customer queries to billing, technical, or general support agents",
            team="customer-success",
            owner="alice@company.com",
            strategy="router",
            agents_config=demo_agents,
            shared_state_config={"type": "dict", "backend": "in_memory"},
            deploy_config={"target": "local", "resources": {"cpu": "1", "memory": "2Gi"}},
            status="deployed",
            endpoint_url="http://localhost:8000/api/v1/orchestrations/execute",
            config_snapshot={
                "name": "customer-support-pipeline",
                "version": "1.0.0",
                "strategy": "router",
                "agents": demo_agents,
            },
            tags=["support", "router", "demo"],
            created_at=now,
            updated_at=now,
        )
        self._orchestrations[demo.id] = demo

    # --- CRUD ---

    def create(
        self,
        *,
        name: str,
        version: str,
        description: str = "",
        team: str | None = None,
        owner: str | None = None,
        strategy: str,
        agents: dict[str, Any],
        shared_state: dict[str, Any] | None = None,
        deploy: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new orchestration."""
        now = datetime.now(UTC).isoformat()
        orch_id = str(uuid.uuid4())

        record = OrchestrationRecord(
            orch_id=orch_id,
            name=name,
            version=version,
            description=description,
            team=team,
            owner=owner,
            strategy=strategy,
            agents_config=agents,
            shared_state_config=shared_state or {"type": "dict", "backend": "in_memory"},
            deploy_config=deploy
            or {
                "target": "local",
                "resources": {"cpu": "1", "memory": "2Gi"},
            },
            status="draft",
            endpoint_url=None,
            config_snapshot={
                "name": name,
                "version": version,
                "strategy": strategy,
                "agents": agents,
            },
            tags=tags or [],
            created_at=now,
            updated_at=now,
        )
        self._orchestrations[orch_id] = record
        logger.info("Orchestration created", extra={"resource_name": name, "id": orch_id})
        return record.to_dict()

    def list(
        self,
        *,
        team: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List orchestrations with optional filters."""
        results: list[dict[str, Any]] = []
        for record in self._orchestrations.values():
            if team and record.team != team:
                continue
            if status and record.status != status:
                continue
            results.append(record.to_dict())
        results.sort(key=lambda x: x["created_at"], reverse=True)
        return results

    def get(self, orch_id: str) -> dict[str, Any] | None:
        """Get an orchestration by ID."""
        record = self._orchestrations.get(orch_id)
        return record.to_dict() if record else None

    def get_by_name(self, name: str) -> dict[str, Any] | None:
        """Get an orchestration by name."""
        for record in self._orchestrations.values():
            if record.name == name:
                return record.to_dict()
        return None

    def update(self, orch_id: str, **kwargs: Any) -> dict[str, Any] | None:
        """Update an orchestration."""
        record = self._orchestrations.get(orch_id)
        if not record:
            return None

        now = datetime.now(UTC).isoformat()
        for key, value in kwargs.items():
            if value is not None and hasattr(record, key):
                setattr(record, key, value)
        record.updated_at = now
        return record.to_dict()

    def delete(self, orch_id: str) -> bool:
        """Delete an orchestration."""
        if orch_id in self._orchestrations:
            del self._orchestrations[orch_id]
            return True
        return False

    # --- Deploy ---

    def deploy(self, orch_id: str) -> dict[str, Any] | None:
        """Mark an orchestration as deployed."""
        record = self._orchestrations.get(orch_id)
        if not record:
            return None

        now = datetime.now(UTC).isoformat()
        record.status = "deployed"
        record.endpoint_url = f"http://localhost:8000/api/v1/orchestrations/{orch_id}/execute"
        record.updated_at = now
        logger.info("Orchestration deployed", extra={"resource_name": record.name, "id": orch_id})
        return record.to_dict()

    # --- Execute ---

    async def execute(
        self,
        orch_id: str,
        input_message: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an orchestration by ID."""
        record = self._orchestrations.get(orch_id)
        if not record:
            msg = f"Orchestration not found: {orch_id}"
            raise ValueError(msg)

        # Build OrchestrationConfig from stored data
        agents = {}
        for agent_name, agent_data in record.agents_config.items():
            agents[agent_name] = (
                AgentRef(**agent_data) if isinstance(agent_data, dict) else agent_data
            )

        # Extract supervisor_config from stored data
        supervisor_cfg_data = (
            record.config_snapshot.get("supervisor_config", {}) if record.config_snapshot else {}
        )

        config = OrchestrationConfig(
            name=record.name,
            version=record.version,
            description=record.description,
            team=record.team,
            owner=record.owner,
            strategy=OrchestrationStrategy(record.strategy),
            agents=agents,
            shared_state=SharedStateConfig(**(record.shared_state_config or {})),
            deploy=OrchestrationDeployConfig(**(record.deploy_config or {})),
            supervisor_config=SupervisorConfig(**supervisor_cfg_data),
        )

        # Resolve agent endpoints from refs
        agent_endpoints: dict[str, str] = {}
        for agent_name, agent_data in record.agents_config.items():
            if isinstance(agent_data, dict) and agent_data.get("endpoint_url"):
                agent_endpoints[agent_name] = agent_data["endpoint_url"]

        orchestrator = Orchestrator(
            config,
            agent_endpoints=agent_endpoints if agent_endpoints else None,
        )
        result = await orchestrator.execute(input_message, context or {})
        return result.model_dump()


# ---------------------------------------------------------------------------
# Global Singleton
# ---------------------------------------------------------------------------

_store: OrchestrationStore | None = None


def get_orchestration_store() -> OrchestrationStore:
    """Get the global orchestration store singleton."""
    global _store
    if _store is None:
        _store = OrchestrationStore()
    return _store
