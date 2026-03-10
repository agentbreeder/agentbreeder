"""Registry API routes — tools, models, prompts, knowledge bases."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    ToolCreate,
    ToolResponse,
    SearchResult,
)
from registry.agents import AgentRegistry
from registry.tools import ToolRegistry

router = APIRouter(prefix="/api/v1/registry", tags=["registry"])


@router.get("/tools", response_model=ApiResponse[list[ToolResponse]])
async def list_tools(
    tool_type: str | None = Query(None),
    source: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ToolResponse]]:
    """List tools and MCP servers from the registry."""
    tools, total = await ToolRegistry.list(
        db, tool_type=tool_type, source=source, page=page, per_page=per_page
    )
    return ApiResponse(
        data=[ToolResponse.model_validate(t) for t in tools],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.post("/tools", response_model=ApiResponse[ToolResponse], status_code=201)
async def register_tool(
    body: ToolCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ToolResponse]:
    """Register a tool or MCP server."""
    tool = await ToolRegistry.register(
        db,
        name=body.name,
        description=body.description,
        tool_type=body.tool_type,
        schema_definition=body.schema_definition,
        endpoint=body.endpoint,
        source=body.source,
    )
    return ApiResponse(data=ToolResponse.model_validate(tool))


@router.get("/search", response_model=ApiResponse[list[SearchResult]])
async def search_registry(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[SearchResult]]:
    """Search across all registry entities (agents, tools)."""
    results: list[SearchResult] = []

    # Search agents
    agents, _ = await AgentRegistry.search(db, query=q, page=1, per_page=per_page)
    for agent in agents:
        results.append(
            SearchResult(
                entity_type="agent",
                id=agent.id,
                name=agent.name,
                description=agent.description,
                team=agent.team,
            )
        )

    # Search tools
    tools, _ = await ToolRegistry.search(db, query=q, page=1, per_page=per_page)
    for tool in tools:
        results.append(
            SearchResult(
                entity_type="tool",
                id=tool.id,
                name=tool.name,
                description=tool.description,
            )
        )

    return ApiResponse(
        data=results[:per_page],
        meta=ApiMeta(page=page, per_page=per_page, total=len(results)),
    )
