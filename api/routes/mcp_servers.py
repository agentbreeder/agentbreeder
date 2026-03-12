"""MCP Server API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    McpServerCreate,
    McpServerDiscoveredTool,
    McpServerDiscoverResult,
    McpServerResponse,
    McpServerTestResult,
    McpServerUpdate,
)
from registry.mcp_servers import McpServerRegistry

router = APIRouter(prefix="/api/v1/mcp-servers", tags=["mcp-servers"])


@router.get("", response_model=ApiResponse[list[McpServerResponse]])
async def list_mcp_servers(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[McpServerResponse]]:
    """List all MCP servers."""
    servers, total = await McpServerRegistry.list(db, page=page, per_page=per_page)
    return ApiResponse(
        data=[McpServerResponse.model_validate(s) for s in servers],
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/{server_id}", response_model=ApiResponse[McpServerResponse])
async def get_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[McpServerResponse]:
    """Get a single MCP server by ID."""
    server = await McpServerRegistry.get_by_id(db, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return ApiResponse(data=McpServerResponse.model_validate(server))


@router.post("", response_model=ApiResponse[McpServerResponse], status_code=201)
async def create_mcp_server(
    body: McpServerCreate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[McpServerResponse]:
    """Register a new MCP server."""
    server = await McpServerRegistry.create(
        db,
        name=body.name,
        endpoint=body.endpoint,
        transport=body.transport,
    )
    return ApiResponse(data=McpServerResponse.model_validate(server))


@router.put("/{server_id}", response_model=ApiResponse[McpServerResponse])
async def update_mcp_server(
    server_id: str,
    body: McpServerUpdate,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[McpServerResponse]:
    """Update an MCP server."""
    server = await McpServerRegistry.update(
        db,
        server_id,
        name=body.name,
        endpoint=body.endpoint,
        transport=body.transport,
        status=body.status,
    )
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return ApiResponse(data=McpServerResponse.model_validate(server))


@router.delete("/{server_id}", response_model=ApiResponse[dict])
async def delete_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Delete an MCP server."""
    deleted = await McpServerRegistry.delete(db, server_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return ApiResponse(data={"deleted": True})


@router.post(
    "/{server_id}/test",
    response_model=ApiResponse[McpServerTestResult],
)
async def test_mcp_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[McpServerTestResult]:
    """Test connectivity to an MCP server."""
    server = await McpServerRegistry.get_by_id(db, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    result = await McpServerRegistry.test_connection(db, server_id)
    return ApiResponse(data=McpServerTestResult(**result))


@router.post(
    "/{server_id}/discover",
    response_model=ApiResponse[McpServerDiscoverResult],
)
async def discover_mcp_server_tools(
    server_id: str,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[McpServerDiscoverResult]:
    """Discover tools exposed by an MCP server."""
    server = await McpServerRegistry.get_by_id(db, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    result = await McpServerRegistry.discover_tools(db, server_id)
    return ApiResponse(
        data=McpServerDiscoverResult(
            tools=[McpServerDiscoveredTool(**t) for t in result["tools"]],
            total=result["total"],
        )
    )
