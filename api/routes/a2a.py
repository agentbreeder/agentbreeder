"""A2A Agent API routes — CRUD + discovery + invoke."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.schemas import (
    A2AAgentCreate,
    A2AAgentResponse,
    A2AAgentUpdate,
    A2AInvokeRequest,
    A2AInvokeResponse,
    ApiMeta,
    ApiResponse,
)
from engine.a2a.client import AgentInvocationClient
from registry.a2a_agents import A2AAgentRegistry

router = APIRouter(prefix="/api/v1/a2a", tags=["a2a"])


@router.get("/agents", response_model=ApiResponse[list[A2AAgentResponse]])
async def list_a2a_agents(
    team: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[A2AAgentResponse]]:
    """List all A2A agents."""
    agents, total = await A2AAgentRegistry.list(db, team=team, page=page, per_page=per_page)
    return ApiResponse(
        data=[A2AAgentResponse.model_validate(a) for a in agents],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/agents/{agent_id}", response_model=ApiResponse[A2AAgentResponse])
async def get_a2a_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[A2AAgentResponse]:
    """Get a single A2A agent by ID."""
    agent = await A2AAgentRegistry.get_by_id(db, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="A2A agent not found")
    return ApiResponse(data=A2AAgentResponse.model_validate(agent))


@router.post("/agents", response_model=ApiResponse[A2AAgentResponse], status_code=201)
async def create_a2a_agent(
    body: A2AAgentCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[A2AAgentResponse]:
    """Register a new A2A agent."""
    agent = await A2AAgentRegistry.create(
        db,
        name=body.name,
        endpoint_url=body.endpoint_url,
        agent_id=body.agent_id,
        agent_card=body.agent_card.model_dump() if body.agent_card else None,
        capabilities=body.capabilities,
        auth_scheme=body.auth_scheme,
        team=body.team,
    )
    return ApiResponse(data=A2AAgentResponse.model_validate(agent))


@router.put("/agents/{agent_id}", response_model=ApiResponse[A2AAgentResponse])
async def update_a2a_agent(
    agent_id: str,
    body: A2AAgentUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[A2AAgentResponse]:
    """Update an A2A agent."""
    agent = await A2AAgentRegistry.update(
        db,
        agent_id,
        endpoint_url=body.endpoint_url,
        agent_card=body.agent_card,
        capabilities=body.capabilities,
        auth_scheme=body.auth_scheme,
        status=body.status,
    )
    if not agent:
        raise HTTPException(status_code=404, detail="A2A agent not found")
    return ApiResponse(data=A2AAgentResponse.model_validate(agent))


@router.delete("/agents/{agent_id}", response_model=ApiResponse[dict])
async def delete_a2a_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Delete an A2A agent."""
    deleted = await A2AAgentRegistry.delete(db, agent_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="A2A agent not found")
    return ApiResponse(data={"deleted": True})


@router.post("/invoke", response_model=ApiResponse[A2AInvokeResponse])
async def invoke_a2a_agent(
    body: A2AInvokeRequest,
    agent_name: str = Query(..., description="Name of the A2A agent to invoke"),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[A2AInvokeResponse]:
    """Invoke an A2A agent by name."""
    agent = await A2AAgentRegistry.get_by_name(db, agent_name)
    if not agent:
        raise HTTPException(status_code=404, detail=f"A2A agent '{agent_name}' not found")

    client = AgentInvocationClient()
    try:
        result = await client.invoke(
            agent.endpoint_url,
            body.input_message,
            body.context,
        )
        return ApiResponse(
            data=A2AInvokeResponse(
                output=result.output,
                tokens=result.tokens,
                latency_ms=result.latency_ms,
                status=result.status,
                error=result.error,
            )
        )
    finally:
        await client.close()
