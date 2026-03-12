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

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.enums import DeployJobStatus
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    DeployJobDetailResponse,
    DeployJobResponse,
    DeployRequest,
)
from api.services.deploy_service import DeployService
from registry.deploys import DeployRegistry

router = APIRouter(prefix="/api/v1/deploys", tags=["deploys"])


def _enrich(job) -> DeployJobResponse:
    """Build a DeployJobResponse, injecting agent_name from the relationship."""
    resp = DeployJobResponse.model_validate(job)
    if job.agent:
        resp.agent_name = job.agent.name
    return resp


@router.post("", response_model=ApiResponse[DeployJobResponse])
async def create_deploy(
    body: DeployRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[DeployJobResponse]:
    """Trigger a new deployment.

    Accepts either an existing agent_id or raw config_yaml (from the builder).
    Starts the 8-step deploy pipeline asynchronously.
    """
    try:
        if body.agent_id:
            job = await DeployService.create_deploy(
                db,
                agent_id=body.agent_id,
                target=body.target,
                config_yaml=body.config_yaml,
            )
            return ApiResponse(data=_enrich(job))
        elif body.config_yaml:
            _agent, job = await DeployService.create_agent_and_deploy(
                db,
                yaml_content=body.config_yaml,
                target=body.target,
            )
            return ApiResponse(data=_enrich(job))
        else:
            raise HTTPException(
                status_code=400,
                detail="Either agent_id or config_yaml is required",
            )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


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


@router.get("/{job_id}", response_model=ApiResponse[DeployJobDetailResponse])
async def get_deploy(
    job_id: uuid.UUID,
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
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Cancel an in-progress deployment."""
    success = await DeployService.cancel_deploy(db, job_id)
    if not success:
        raise HTTPException(status_code=404, detail="Deploy job not found or not active")
    return ApiResponse(data={"cancelled": True, "job_id": str(job_id)})


@router.post("/{job_id}/rollback", response_model=ApiResponse[dict])
async def rollback_deploy(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Rollback a failed deployment."""
    success = await DeployService.rollback_deploy(db, job_id)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Deploy job not found or not in failed state",
        )
    return ApiResponse(data={"rolled_back": True, "job_id": str(job_id)})
