"""Deploy job API routes.

Provides endpoints for:
- POST /api/v1/deploys        — trigger a new deployment
- GET  /api/v1/deploys        — list deploy jobs
- GET  /api/v1/deploys/{id}   — get deploy job details (with logs)
- DELETE /api/v1/deploys/{id} — cancel a deployment
- POST /api/v1/deploys/{id}/rollback — rollback a failed deployment
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.middleware.rbac import enforce_team_role, require_role
from api.models.database import Agent, DeployJob, User
from api.models.enums import DeployJobStatus
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    DeployJobDetailResponse,
    DeployJobResponse,
    DeployRequest,
)
from api.services.audit_service import AuditService
from api.services.deploy_service import DeployService
from registry.deploys import DeployRegistry

router = APIRouter(prefix="/api/v1/deploys", tags=["deploys"])


def _enrich(job) -> DeployJobResponse:
    """Build a DeployJobResponse, injecting agent_name from the relationship."""
    resp = DeployJobResponse.model_validate(job)
    if job.agent:
        resp.agent_name = job.agent.name
    return resp


def _team_from_yaml(yaml_content: str) -> str | None:
    """Pull the top-level ``team:`` slug out of an agent.yaml.

    Used to enforce team-scoped RBAC on the builder-mode POST (``config_yaml``
    path) before the deploy pipeline runs. Mirrors the parser in
    ``DeployService._parse_yaml`` but only extracts the one field we need —
    no point parsing the whole config just to fail at the gate.
    """
    for raw_line in yaml_content.splitlines():
        line = raw_line.rstrip()
        if not line.startswith(" ") and not line.startswith("\t"):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if stripped.startswith("team:"):
                val = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                return val or None
    return None


async def _resolve_deploy_team(body: DeployRequest, db: AsyncSession) -> tuple[str, Agent | None]:
    """Return the (team_id, agent_or_none) implied by a DeployRequest.

    Raises 400 if neither ``agent_id`` nor ``config_yaml`` is supplied, 404
    if ``agent_id`` doesn't resolve, and 400 if a builder-mode payload omits
    the required ``team:`` field. Returning the Agent (when known) lets the
    caller skip a second DB round-trip in the success path.
    """
    if body.agent_id:
        agent = await db.get(Agent, body.agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent.team, agent
    if body.config_yaml:
        team = _team_from_yaml(body.config_yaml)
        if not team:
            raise HTTPException(
                status_code=400,
                detail="agent.yaml must include a top-level 'team:' field",
            )
        return team, None
    raise HTTPException(status_code=400, detail="Either agent_id or config_yaml is required")


async def _resolve_job_team(job_id: uuid.UUID, db: AsyncSession) -> str:
    """Return the team that owns ``job_id`` via its agent relationship.

    Used by the lifecycle routes (cancel, rollback) so they can run the
    same team-scoped check as create. 404 if the job (or its agent) is
    missing — the same shape the existing handlers already return.
    """
    job = await db.get(DeployJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Deploy job not found")
    agent = await db.get(Agent, job.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Deploy job's agent not found")
    return agent.team


@router.post("", response_model=ApiResponse[DeployJobResponse])
async def create_deploy(
    body: DeployRequest,
    request: Request,
    user: User = Depends(require_role("deployer")),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DeployJobResponse]:
    """Trigger a new deployment.

    Accepts either an existing agent_id or raw config_yaml (from the builder).
    Starts the 8-step deploy pipeline asynchronously. The deployer role is
    required **for the agent's team specifically** (HR-1 analogue, #414) — a
    user with deployer in team A cannot deploy an agent owned by team B.

    Two-layer gate: ``require_role("deployer")`` fast-rejects users who
    aren't a deployer in *any* team; then ``enforce_team_role`` adds the
    team-specific refinement after the agent's team is known. Defense in
    depth and back-compat with the v2.3 viewer-rejection behavior.
    """
    team_id, _agent = await _resolve_deploy_team(body, db)
    await enforce_team_role(user, team_id, "deployer")

    try:
        if body.agent_id:
            job = await DeployService.create_deploy(
                db,
                agent_id=body.agent_id,
                target=body.target,
                config_yaml=body.config_yaml,
            )
        else:
            _new_agent, job = await DeployService.create_agent_and_deploy(
                db,
                yaml_content=body.config_yaml or "",
                target=body.target,
            )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    await AuditService.log_event(
        actor=user.email,
        action="deploy.create",
        resource_type="deploy_job",
        resource_name=str(job.id),
        resource_id=str(job.id),
        team=team_id,
        details={"agent_id": str(job.agent_id), "target": body.target},
        ip_address=request.client.host if request.client else None,
    )
    return ApiResponse(data=_enrich(job))


@router.get("", response_model=ApiResponse[list[DeployJobResponse]])
async def list_deploys(
    agent_id: uuid.UUID | None = Query(None),
    status: DeployJobStatus | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[DeployJobResponse]]:
    """List deploy jobs, optionally filtered by agent or status."""
    jobs, total = await DeployRegistry.list(
        db, agent_id=agent_id, status=status, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[_enrich(j) for j in jobs],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/{job_id}", response_model=ApiResponse[DeployJobDetailResponse])
async def get_deploy(
    job_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DeployJobDetailResponse]:
    """Get deploy job details by ID, including streaming logs."""
    result = await DeployService.get_deploy_status(db, job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Deploy job not found")
    return ApiResponse(data=DeployJobDetailResponse(**result))


@router.delete("/{job_id}", response_model=ApiResponse[dict])
async def cancel_deploy(
    job_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_role("deployer")),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Cancel an in-progress deployment. Requires deployer role for the job's team."""
    team_id = await _resolve_job_team(job_id, db)
    await enforce_team_role(user, team_id, "deployer")

    success = await DeployService.cancel_deploy(db, job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Deploy job not found or not active")
    await AuditService.log_event(
        actor=user.email,
        action="deploy.cancel",
        resource_type="deploy_job",
        resource_name=str(job_id),
        resource_id=str(job_id),
        team=team_id,
        ip_address=request.client.host if request.client else None,
    )
    return ApiResponse(data={"cancelled": True, "job_id": str(job_id)})


@router.post("/{job_id}/rollback", response_model=ApiResponse[dict])
async def rollback_deploy(
    job_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_role("deployer")),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Rollback a failed deployment. Requires deployer role for the job's team."""
    team_id = await _resolve_job_team(job_id, db)
    await enforce_team_role(user, team_id, "deployer")

    success = await DeployService.rollback_deploy(db, job_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Deploy job not found or not in failed state",
        )
    await AuditService.log_event(
        actor=user.email,
        action="deploy.rollback",
        resource_type="deploy_job",
        resource_name=str(job_id),
        resource_id=str(job_id),
        team=team_id,
        ip_address=request.client.host if request.client else None,
    )
    return ApiResponse(data={"rolled_back": True, "job_id": str(job_id)})
