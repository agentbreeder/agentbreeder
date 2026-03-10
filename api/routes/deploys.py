"""Deploy job API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.enums import DeployJobStatus
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    DeployJobResponse,
)
from registry.deploys import DeployRegistry

router = APIRouter(prefix="/api/v1/deploys", tags=["deploys"])


def _enrich(job) -> DeployJobResponse:
    """Build a DeployJobResponse, injecting agent_name from the relationship."""
    resp = DeployJobResponse.model_validate(job)
    if job.agent:
        resp.agent_name = job.agent.name
    return resp


@router.get("", response_model=ApiResponse[list[DeployJobResponse]])
async def list_deploys(
    agent_id: uuid.UUID | None = Query(None),
    status: DeployJobStatus | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
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


@router.get("/{job_id}", response_model=ApiResponse[DeployJobResponse])
async def get_deploy(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DeployJobResponse]:
    """Get deploy job details by ID."""
    job = await DeployRegistry.get(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Deploy job not found")
    return ApiResponse(data=_enrich(job))
