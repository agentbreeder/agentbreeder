"""DeployJobService — orchestrator-facing job lifecycle + idempotency.

Owns:
- create() — checks team auth + approval flag, dedupes via Idempotency-Key,
  records the job, kicks off the orchestrator (unless approval required).
- get() — returns a job's current status (team-scoped).
- destroy_partial() — delegates to orchestrator.destroy_partial(job_id).

Persistence is in-memory for this PR; swap for SQLAlchemy in a follow-up.
The idempotency store is a dict[(team_id, key), job_id] — operators wire
this to Redis in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from api.models.deploy_events import DeployJobStatus


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


class DeployJobCreateResult(BaseModel):
    job_id: str
    pending_approval: bool


class DeployJobService:
    def __init__(
        self,
        *,
        event_bus: Any,
        orchestrator: Any,
        idempotency_store: dict[tuple[str, str], str],
        agent_repo: Any,
    ) -> None:
        self._event_bus = event_bus
        self._orchestrator = orchestrator
        self._idempotency_store = idempotency_store
        self._agent_repo = agent_repo
        self._jobs: dict[str, DeployJobRecord] = {}

    async def create(
        self,
        payload: DeployJobCreate,
        *,
        team_id: str,
        idempotency_key: str,
    ) -> DeployJobCreateResult:
        """Create a deployment job, handling idempotency and approval gating.

        Args:
            payload: Deployment configuration
            team_id: Team creating the job
            idempotency_key: Uniqueness key (team_id + key → deduped)

        Returns:
            DeployJobCreateResult with job_id and pending_approval flag

        Raises:
            HTTPException 404: Agent not found
            HTTPException 403: Agent belongs to another team
        """
        # Check idempotency store first
        existing_id = self._idempotency_store.get((team_id, idempotency_key))
        if existing_id is not None:
            existing = self._jobs[existing_id]
            return DeployJobCreateResult(
                job_id=existing.job_id,
                pending_approval=existing.pending_approval,
            )

        # Fetch agent and validate team ownership
        agent = await self._agent_repo.get(payload.agent_id)
        if agent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
        if agent.team != team_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Agent belongs to another team")

        # Check if approval is required
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
        self._idempotency_store[(team_id, idempotency_key)] = job.job_id

        # Start orchestrator if no approval required
        if not requires_approval:
            await self._orchestrator.start(job=job, event_bus=self._event_bus)

        return DeployJobCreateResult(
            job_id=job.job_id,
            pending_approval=requires_approval,
        )

    async def get(self, job_id: str, *, team_id: str) -> DeployJobRecord:
        """Get a deployment job by ID.

        Args:
            job_id: The job ID to fetch
            team_id: Team requesting access

        Returns:
            DeployJobRecord with current status

        Raises:
            HTTPException 404: Job not found
            HTTPException 403: Job belongs to another team
        """
        job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        if job.team_id != team_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Job belongs to another team")
        return job

    async def destroy_partial(self, job_id: str, *, team_id: str) -> None:
        """Destroy partially-provisioned infrastructure for a job.

        Args:
            job_id: The job ID to clean up
            team_id: Team requesting cleanup

        Raises:
            HTTPException 403, 404: From get()
        """
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
        """Record a job in the in-memory store.

        Args:
            job_id: Unique job identifier
            payload: Deployment configuration
            team_id: Owning team
            status: Initial status
            pending_approval: Whether approval is pending

        Returns:
            DeployJobRecord
        """
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
        self._jobs[job_id] = job
        return job
