"""DeployJobService — orchestrator-facing job lifecycle + idempotency.

Owns:
- create() — checks team auth + approval flag, dedupes via Idempotency-Key,
  records the job, kicks off the orchestrator (unless approval required).
- get() — returns a job's current status (team-scoped).
- destroy_partial() — delegates to orchestrator.destroy_partial(job_id).

Persistence is delegated to ``IdempotencyStore`` + ``JobStore`` (see
``deploy_stores.py``). In production both are Redis-backed (multi-replica
+ restart-safe); tests use in-memory implementations.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from api.models.deploy_events import DeployJobStatus
from api.services.deploy_stores import IdempotencyStore, JobStore


class EnvVar(BaseModel):
    key: str
    value: str


class Scaling(BaseModel):
    min: int = 1
    max: int = 3
    cpu_target_pct: int = 70


class DeployJobCreate(BaseModel):
    agent_id: str
    cloud: str
    region: str
    infra_mode: str  # "byo" | "provision"
    byo_fields: dict[str, str] = Field(default_factory=dict)
    env_vars: list[EnvVar] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    scaling: Scaling = Field(default_factory=Scaling)
    db_tier: str | None = None


class DeployJobRecord(BaseModel):
    job_id: str
    team_id: str
    agent_id: str
    cloud: str
    region: str
    status: DeployJobStatus
    pending_approval: bool
    endpoint_url: str | None = None
    created_at: datetime
    payload: DeployJobCreate
    # JSON-serialised ``InfraState`` populated by the orchestrator after
    # provisioner.provision() returns. ``destroy_partial`` reads this to
    # decide what to tear down. Stored as a dict (not the Pydantic model
    # directly) so the Redis JSON round-trip is loss-free and there's no
    # circular import with engine.provisioners.state.
    infra_state: dict[str, Any] | None = None


class DeployJobCreateResult(BaseModel):
    job_id: str
    pending_approval: bool


class DeployJobService:
    def __init__(
        self,
        *,
        event_bus: Any,
        orchestrator: Any,
        idempotency_store: IdempotencyStore,
        job_store: JobStore,
        agent_repo: Any,
    ) -> None:
        self._event_bus = event_bus
        self._orchestrator = orchestrator
        self._idempotency_store = idempotency_store
        self._job_store = job_store
        self._agent_repo = agent_repo

    async def create(
        self,
        payload: DeployJobCreate,
        *,
        team_id: str,
        idempotency_key: str,
    ) -> DeployJobCreateResult:
        """Create a deployment job, handling idempotency and approval gating."""
        # Idempotency: same (team, key) -> same job_id.
        existing_id = await self._idempotency_store.get(team_id, idempotency_key)
        if existing_id is not None:
            existing = await self._job_store.get(existing_id)
            if existing is not None:
                return DeployJobCreateResult(
                    job_id=existing.job_id,
                    pending_approval=existing.pending_approval,
                )
            # Idempotency entry points at a job that's been TTL-evicted from the
            # job store. Treat as expired: fall through to create a fresh job.

        # Fetch agent and validate team ownership.
        agent = await self._agent_repo.get(payload.agent_id)
        if agent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
        if agent.team != team_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Agent belongs to another team")

        requires_approval = bool(getattr(agent, "access", {}).get("require_approval"))
        job = await self._record(
            job_id=str(uuid4()),
            payload=payload,
            team_id=team_id,
            status=(
                DeployJobStatus.pending_approval if requires_approval else DeployJobStatus.pending
            ),
            pending_approval=requires_approval,
        )
        await self._idempotency_store.set(team_id, idempotency_key, job.job_id)

        if not requires_approval:
            await self._orchestrator.start(job=job, event_bus=self._event_bus)

        return DeployJobCreateResult(
            job_id=job.job_id,
            pending_approval=requires_approval,
        )

    async def get(self, job_id: str, *, team_id: str) -> DeployJobRecord:
        """Get a deployment job by ID. Raises 403/404."""
        job = await self._job_store.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        if job.team_id != team_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Job belongs to another team")
        return job

    async def destroy_partial(self, job_id: str, *, team_id: str) -> None:
        """Destroy partially-provisioned infrastructure for a job. Raises 403/404."""
        await self.get(job_id, team_id=team_id)
        await self._orchestrator.destroy_partial(job_id)

    async def _record(
        self,
        *,
        job_id: str,
        payload: DeployJobCreate,
        team_id: str,
        status: DeployJobStatus,
        pending_approval: bool = False,
    ) -> DeployJobRecord:
        """Record a job in the job store and return it."""
        job = DeployJobRecord(
            job_id=job_id,
            team_id=team_id,
            agent_id=payload.agent_id,
            cloud=payload.cloud,
            region=payload.region,
            status=status,
            pending_approval=pending_approval,
            created_at=datetime.now(UTC),
            payload=payload,
        )
        await self._job_store.put(job)
        return job
