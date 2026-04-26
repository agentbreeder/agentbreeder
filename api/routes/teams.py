"""Team management API routes — RBAC, members, API keys."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_user
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from api.models.team_schemas import (
    TeamApiKeyCreate,
    TeamApiKeyResponse,
    TeamCreate,
    TeamDetailResponse,
    TeamMemberAdd,
    TeamMemberResponse,
    TeamMemberUpdate,
    TeamResponse,
    TeamUpdate,
)
from api.services.team_service import TeamService

logger = logging.getLogger(__name__)

_LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-agentbreeder-quickstart")


async def _register_litellm_team(team_id: str, team_name: str) -> None:
    """Fire-and-forget: register a new team budget group in LiteLLM."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{_LITELLM_BASE_URL}/team/new",
                headers={"Authorization": f"Bearer {_LITELLM_MASTER_KEY}"},
                json={"team_id": team_id, "team_alias": team_name, "max_budget": None},
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LiteLLM team registration failed for %s: %s", team_id, exc)


router = APIRouter(prefix="/api/v1/teams", tags=["teams"])


# -- Team CRUD -------------------------------------------------------------


@router.get("", response_model=ApiResponse[list[TeamResponse]])
async def list_teams(
    _user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
) -> ApiResponse[list[TeamResponse]]:
    """List all teams."""
    teams, total = await TeamService.list_teams(page=page, per_page=per_page)
    data = []
    for t in teams:
        count = await TeamService.get_member_count(t.id)
        data.append(
            TeamResponse(
                id=t.id,
                name=t.name,
                display_name=t.display_name,
                description=t.description,
                member_count=count,
                created_at=t.created_at,
            )
        )
    return ApiResponse(
        data=data,
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("", response_model=ApiResponse[TeamResponse], status_code=201)
async def create_team(
    body: TeamCreate, _user: User = Depends(require_role("admin"))
) -> ApiResponse[TeamResponse]:
    """Create a new team."""
    try:
        team = await TeamService.create_team(
            name=body.name,
            display_name=body.display_name,
            description=body.description,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    # Register team budget group in LiteLLM — fire-and-forget, never blocks team creation
    asyncio.ensure_future(_register_litellm_team(team.id, team.name))

    return ApiResponse(
        data=TeamResponse(
            id=team.id,
            name=team.name,
            display_name=team.display_name,
            description=team.description,
            member_count=0,
            created_at=team.created_at,
        )
    )


@router.get("/{team_id}", response_model=ApiResponse[TeamDetailResponse])
async def get_team(
    team_id: str, _user: User = Depends(get_current_user)
) -> ApiResponse[TeamDetailResponse]:
    """Get team detail with members."""
    team = await TeamService.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    members = await TeamService.get_team_members(team_id)
    member_responses = [
        TeamMemberResponse(
            id=m.id,
            user_id=m.user_id,
            user_email=m.user_email,
            user_name=m.user_name,
            role=m.role,
            joined_at=m.joined_at,
        )
        for m in members
    ]
    return ApiResponse(
        data=TeamDetailResponse(
            id=team.id,
            name=team.name,
            display_name=team.display_name,
            description=team.description,
            member_count=len(members),
            members=member_responses,
            created_at=team.created_at,
            updated_at=team.updated_at,
        )
    )


@router.put("/{team_id}", response_model=ApiResponse[TeamResponse])
async def update_team(
    team_id: str, body: TeamUpdate, _user: User = Depends(require_role("admin"))
) -> ApiResponse[TeamResponse]:
    """Update a team."""
    team = await TeamService.update_team(
        team_id,
        display_name=body.display_name,
        description=body.description,
    )
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    count = await TeamService.get_member_count(team_id)
    return ApiResponse(
        data=TeamResponse(
            id=team.id,
            name=team.name,
            display_name=team.display_name,
            description=team.description,
            member_count=count,
            created_at=team.created_at,
        )
    )


@router.delete("/{team_id}", response_model=ApiResponse[dict])
async def delete_team(
    team_id: str, _user: User = Depends(require_role("admin"))
) -> ApiResponse[dict]:
    """Delete a team and all its memberships/keys."""
    deleted = await TeamService.delete_team(team_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Team not found")
    return ApiResponse(data={"deleted": True})


# -- Members ---------------------------------------------------------------


@router.post(
    "/{team_id}/members",
    response_model=ApiResponse[TeamMemberResponse],
    status_code=201,
)
async def add_member(
    team_id: str, body: TeamMemberAdd, _user: User = Depends(require_role("admin"))
) -> ApiResponse[TeamMemberResponse]:
    """Add a member to a team."""
    try:
        # Generate a user_id from email for the in-memory store
        import hashlib

        user_id = hashlib.sha256(body.user_email.encode()).hexdigest()[:32]
        user_name = body.user_email.split("@")[0]

        membership = await TeamService.add_member(
            team_id,
            user_id=user_id,
            user_email=body.user_email,
            user_name=user_name,
            role=body.role,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return ApiResponse(
        data=TeamMemberResponse(
            id=membership.id,
            user_id=membership.user_id,
            user_email=membership.user_email,
            user_name=membership.user_name,
            role=membership.role,
            joined_at=membership.joined_at,
        )
    )


@router.put(
    "/{team_id}/members/{user_id}",
    response_model=ApiResponse[TeamMemberResponse],
)
async def update_member_role(
    team_id: str,
    user_id: str,
    body: TeamMemberUpdate,
    _user: User = Depends(require_role("admin")),
) -> ApiResponse[TeamMemberResponse]:
    """Update a member's role."""
    try:
        membership = await TeamService.update_member_role(team_id, user_id, body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    if not membership:
        raise HTTPException(status_code=404, detail="Member not found")

    return ApiResponse(
        data=TeamMemberResponse(
            id=membership.id,
            user_id=membership.user_id,
            user_email=membership.user_email,
            user_name=membership.user_name,
            role=membership.role,
            joined_at=membership.joined_at,
        )
    )


@router.delete("/{team_id}/members/{user_id}", response_model=ApiResponse[dict])
async def remove_member(
    team_id: str, user_id: str, _user: User = Depends(require_role("admin"))
) -> ApiResponse[dict]:
    """Remove a member from a team."""
    removed = await TeamService.remove_member(team_id, user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    return ApiResponse(data={"removed": True})


# -- API Keys --------------------------------------------------------------


@router.get(
    "/{team_id}/api-keys",
    response_model=ApiResponse[list[TeamApiKeyResponse]],
)
async def list_api_keys(
    team_id: str, _user: User = Depends(require_role("admin"))
) -> ApiResponse[list[TeamApiKeyResponse]]:
    """List API keys for a team (hints only, never the actual key)."""
    team = await TeamService.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    keys = await TeamService.list_api_keys(team_id)
    return ApiResponse(
        data=[
            TeamApiKeyResponse(
                id=k.id,
                provider=k.provider,
                key_hint=k.key_hint,
                created_by=k.created_by,
                created_at=k.created_at,
            )
            for k in keys
        ]
    )


@router.post(
    "/{team_id}/api-keys",
    response_model=ApiResponse[TeamApiKeyResponse],
    status_code=201,
)
async def set_api_key(
    team_id: str, body: TeamApiKeyCreate, _user: User = Depends(require_role("admin"))
) -> ApiResponse[TeamApiKeyResponse]:
    """Set an API key for a provider on this team."""
    try:
        key_data = await TeamService.set_api_key(
            team_id,
            provider=body.provider,
            api_key=body.api_key,
            created_by="admin@agentbreeder.local",  # TODO: get from auth
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    return ApiResponse(
        data=TeamApiKeyResponse(
            id=key_data.id,
            provider=key_data.provider,
            key_hint=key_data.key_hint,
            created_by=key_data.created_by,
            created_at=key_data.created_at,
        )
    )


@router.delete("/{team_id}/api-keys/{key_id}", response_model=ApiResponse[dict])
async def delete_api_key(team_id: str, key_id: str) -> ApiResponse[dict]:
    """Delete an API key."""
    deleted = await TeamService.delete_api_key(key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="API key not found")
    return ApiResponse(data={"deleted": True})


@router.post(
    "/{team_id}/api-keys/{key_id}/test",
    response_model=ApiResponse[dict],
)
async def test_api_key(team_id: str, key_id: str) -> ApiResponse[dict]:
    """Test an API key by attempting a simple validation."""
    result = await TeamService.test_api_key(key_id)
    return ApiResponse(data=result)
