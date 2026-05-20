"""Deployment infrastructure API (Phase A foundation for epic #378).

Two endpoints:

- ``GET /api/v1/deployments/cloud-requirements/{cloud}`` returns the
  user-input contract for a given cloud + mode (static data). Any
  authenticated user may read this.

- ``POST /api/v1/deployments/validate-infra`` performs read-only checks
  against the user's existing cloud resources. Team-scoped: caller must
  hold ``deployer`` role in the team specified in the request body.
  Rate-limited (10 req/min/IP) and audit-logged.

Greenfield provisioning (creating resources from scratch) is deferred to
epic #378's sub-issues #382 / #383 / #384.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from api.auth import get_current_user
from api.models.database import User
from api.models.schemas import ApiResponse
from api.services.audit_service import AuditService
from api.services.team_service import ROLE_HIERARCHY, TeamService
from engine.provisioners import (
    CloudMode,
    CloudName,
    CloudRequirements,
    InfraValidationInput,
    ValidationResult,
    get_requirements,
    provisioner_for,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])

# Module-level limiter. ``api.main`` re-uses the same instance via the
# ``limiter`` attribute it sets on ``app.state``, but route decorators must
# reference an importable Limiter object so we declare it here as the source
# of truth and main.py imports it.
limiter = Limiter(key_func=get_remote_address)


class ValidateInfraRequest(BaseModel):
    """Request body for POST /validate-infra."""

    team_id: str = Field(..., description="Team that owns the cloud account (RBAC scope)")
    cloud: CloudName
    region: str
    mode: CloudMode = "simple"
    fields: dict[str, Any] = Field(default_factory=dict)


def _required_role_level(role: str) -> int:
    return ROLE_HIERARCHY.get(role, 0)


async def _require_deployer_in_team(user: User, team_id: str) -> None:
    """Enforce 'deployer' role in the named team. Platform admins always pass."""
    if getattr(user, "role", None) and str(user.role) == "admin":
        return

    user_role = await TeamService.get_user_role_in_team(str(user.id), team_id)
    if user_role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User is not a member of team {team_id!r}",
        )
    if _required_role_level(user_role) < _required_role_level("deployer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires deployer role in team {team_id!r}; you have {user_role!r}",
        )


@router.get(
    "/cloud-requirements/{cloud}",
    response_model=ApiResponse[CloudRequirements],
)
async def get_cloud_requirements(
    cloud: CloudName,
    mode: CloudMode = Query("simple"),
    _user: User = Depends(get_current_user),
) -> ApiResponse[CloudRequirements]:
    """Return the user-input contract (required + optional fields) for the cloud."""
    try:
        requirements = get_requirements(cloud, mode)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return ApiResponse(data=requirements)


@router.post(
    "/validate-infra",
    response_model=ApiResponse[ValidationResult],
)
@limiter.limit("10/minute")
async def validate_infra(
    request: Request,
    body: ValidateInfraRequest,
    user: User = Depends(get_current_user),
) -> ApiResponse[ValidationResult]:
    """Read-only check that every cloud resource referenced in the body exists."""
    await _require_deployer_in_team(user, body.team_id)

    provisioner = provisioner_for(body.cloud)
    payload = InfraValidationInput(
        cloud=body.cloud,
        region=body.region,
        mode=body.mode,
        fields=body.fields,
    )

    try:
        result = await provisioner.validate_existing(payload)
    except Exception as e:  # noqa: BLE001 - log + surface as 502 to caller
        logger.exception(
            "Cloud SDK error during validate-infra",
            extra={"cloud": body.cloud, "team": body.team_id, "user": str(user.id)},
        )
        await AuditService.log_event(
            actor=str(user.id),
            action="deployment.validate_infra",
            resource_type="deployment",
            resource_name=body.cloud,
            team=body.team_id,
            details={"mode": body.mode, "error": type(e).__name__, "valid": False},
            ip_address=request.client.host if request.client else None,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Cloud provider error: {type(e).__name__}",
        ) from e

    await AuditService.log_event(
        actor=str(user.id),
        action="deployment.validate_infra",
        resource_type="deployment",
        resource_name=body.cloud,
        team=body.team_id,
        details={
            "mode": body.mode,
            "valid": result.valid,
            "checks_count": len(result.checks),
            "region": body.region,
        },
        ip_address=request.client.host if request.client else None,
    )

    if not result.valid:
        return ApiResponse(
            data=result,
            errors=[
                f"{c.resource}: {c.status} ({c.detail})"
                for c in result.checks
                if c.status != "found"
            ],
        )
    return ApiResponse(data=result)


@router.post("/", status_code=202)
async def create_deploy_job(
    payload: dict,
    request: Request,
    user: User = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Create a deployment job with idempotency-key gating and approval support.

    Requires:
    - Idempotency-Key header (prevents duplicate job creation)
    - Authorization header (user must be authenticated)

    Workflow:
    1. Validate Idempotency-Key is present (400 if missing)
    2. Check agent exists and belongs to caller's team (403/404 if fails)
    3. Check if agent requires approval
    4. Record job in in-memory store (deduplicated by team_id + key)
    5. Return 202 with job_id + pending_approval flag
    6. Kick off orchestrator (unless approval required)

    The Idempotency-Key is team-scoped: (team_id, key) → job_id.
    If the same key is used twice in the same team, return the original job_id.
    """
    from api.services.deploy_jobs import DeployJobCreate

    # Validate Idempotency-Key is present (auth is checked first by Depends)
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header required",
        )

    # Coerce the payload dict into DeployJobCreate
    try:
        job_create = DeployJobCreate(**payload)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid deployment payload: {str(e)}",
        ) from e

    # Call the service (team_id from user.team)
    service = request.app.state.deploy_job_service
    result = await service.create(
        job_create,
        team_id=user.team,
        idempotency_key=idempotency_key,
    )
    return {"data": result.model_dump(), "meta": {}, "errors": []}
