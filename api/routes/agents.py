"""Agent API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.enums import AgentStatus
from api.models.schemas import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    ApiMeta,
    ApiResponse,
)
from registry.agents import AgentRegistry

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


@router.get("", response_model=ApiResponse[list[AgentResponse]])
async def list_agents(
    team: str | None = Query(None),
    framework: str | None = Query(None),
    status: AgentStatus | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[AgentResponse]]:
    """List agents from the registry."""
    agents, total = await AgentRegistry.list(
        db, team=team, framework=framework, status=status, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[AgentResponse.model_validate(a) for a in agents],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/search", response_model=ApiResponse[list[AgentResponse]])
async def search_agents(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[AgentResponse]]:
    """Search agents by name, description, team, or framework."""
    agents, total = await AgentRegistry.search(db, query=q, page=page, per_page=per_page)
    return ApiResponse(
        data=[AgentResponse.model_validate(a) for a in agents],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/{agent_id}", response_model=ApiResponse[AgentResponse])
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AgentResponse]:
    """Get agent details by ID."""
    agent = await AgentRegistry.get_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return ApiResponse(data=AgentResponse.model_validate(agent))


@router.post("", response_model=ApiResponse[AgentResponse], status_code=201)
async def create_agent(
    body: AgentCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AgentResponse]:
    """Manually register an agent in the registry."""
    from engine.config_parser import AgentConfig, FrameworkType

    # Build a minimal AgentConfig for registry
    config = AgentConfig(
        name=body.name,
        version=body.version,
        description=body.description,
        team=body.team,
        owner=body.owner,
        framework=FrameworkType(body.framework),
        model={"primary": body.model_primary, "fallback": body.model_fallback},
        deploy={"cloud": "local"},
        tags=body.tags,
    )
    agent = await AgentRegistry.register(
        db, config, endpoint_url=body.endpoint_url or ""
    )
    return ApiResponse(data=AgentResponse.model_validate(agent))


@router.put("/{agent_id}", response_model=ApiResponse[AgentResponse])
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AgentResponse]:
    """Update an agent's metadata."""
    agent = await AgentRegistry.get_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if body.version is not None:
        agent.version = body.version
    if body.description is not None:
        agent.description = body.description
    if body.endpoint_url is not None:
        agent.endpoint_url = body.endpoint_url
    if body.status is not None:
        agent.status = body.status
    if body.tags is not None:
        agent.tags = body.tags

    await db.flush()
    return ApiResponse(data=AgentResponse.model_validate(agent))


@router.delete("/{agent_id}", response_model=ApiResponse[dict])
async def delete_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Soft-delete (archive) an agent."""
    agent = await AgentRegistry.get_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.status = AgentStatus.stopped
    await db.flush()
    return ApiResponse(data={"message": f"Agent '{agent.name}' archived"})
