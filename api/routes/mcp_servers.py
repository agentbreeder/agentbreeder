"""MCP Server API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.middleware.rbac import require_role
from api.models.database import User
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


# ---------------------------------------------------------------------------
# ACL enforcement helper
# ---------------------------------------------------------------------------


async def _enforce_acl(
    db: AsyncSession,
    user_email: str,
    resource_id: uuid.UUID,
    action: str,
) -> None:
    """Check ACL for an MCP server. Raises 403 if explicitly denied.

    Passes silently if: no ACL rows exist, DB unavailable, or permission granted.
    """
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from api.models.database import ResourcePermission  # noqa: PLC0415
        from api.services.rbac_service import check_permission  # noqa: PLC0415

        allowed, reason = await check_permission(
            db,
            user_email=user_email,
            resource_type="mcp_server",
            resource_id=resource_id,
            action=action,
        )
        result = await db.execute(
            select(ResourcePermission)
            .where(
                ResourcePermission.resource_type == "mcp_server",
                ResourcePermission.resource_id == resource_id,
            )
            .limit(1)
        )
        has_acl = result.scalar_one_or_none() is not None
        if has_acl and not allowed:
            from fastapi import HTTPException  # noqa: PLC0415

            raise HTTPException(status_code=403, detail=f"Access denied: {reason}")
    except HTTPException:
        raise
    except Exception:
        pass  # Fail open if DB unavailable


@router.get("", response_model=ApiResponse[list[McpServerResponse]])
async def list_mcp_servers(
    _user: User = Depends(get_current_user),
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
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[McpServerResponse]:
    """Get a single MCP server by ID."""
    try:
        await _enforce_acl(db, _user.email, uuid.UUID(server_id), "read")
    except HTTPException:
        raise
    except Exception:
        pass
    server = await McpServerRegistry.get_by_id(db, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    return ApiResponse(data=McpServerResponse.model_validate(server))


@router.post("", response_model=ApiResponse[McpServerResponse], status_code=201)
async def create_mcp_server(
    body: McpServerCreate,
    _user: User = Depends(require_role("deployer")),
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
    _user: User = Depends(require_role("deployer")),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[McpServerResponse]:
    """Update an MCP server."""
    try:
        await _enforce_acl(db, _user.email, uuid.UUID(server_id), "write")
    except HTTPException:
        raise
    except Exception:
        pass
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
    _user: User = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Delete an MCP server."""
    try:
        await _enforce_acl(db, _user.email, uuid.UUID(server_id), "delete")
    except HTTPException:
        raise
    except Exception:
        pass
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
    _user: User = Depends(require_role("deployer")),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[McpServerTestResult]:
    """Test connectivity to an MCP server."""
    try:
        await _enforce_acl(db, _user.email, uuid.UUID(server_id), "use")
    except HTTPException:
        raise
    except Exception:
        pass
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
    _user: User = Depends(require_role("deployer")),
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


@router.post(
    "/{server_id}/execute",
    response_model=ApiResponse[dict],
)
async def execute_mcp_tool(
    server_id: str,
    _user: User = Depends(require_role("deployer")),
    tool_name: str = Query(..., description="Name of the tool to execute"),
    arguments: dict | None = None,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Execute a tool on an MCP server."""
    try:
        await _enforce_acl(db, _user.email, uuid.UUID(server_id), "use")
    except HTTPException:
        raise
    except Exception:
        pass
    server = await McpServerRegistry.get_by_id(db, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    result = await McpServerRegistry.execute_tool(db, server_id, tool_name, arguments or {})
    return ApiResponse(data=result)
