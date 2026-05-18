"""Unit tests for MCP route error shape standardization (W4-13)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Base
from registry.mcp_servers import McpServerRegistry

_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionFactory() as s:
        yield s
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# Test that the routes raise HTTPException on registry failure rather than
# returning success=False dicts inside ApiResponse. We do this by directly
# calling the route handlers.


class TestRouteErrorShape:
    @pytest.mark.asyncio
    async def test_test_route_raises_502_on_failure(self, session: AsyncSession) -> None:
        from api.routes.mcp_servers import test_mcp_server

        server = await McpServerRegistry.create(
            session, name="r1", endpoint="http://unreachable:9999", transport="sse"
        )

        # Mock test_connection to return failure
        with patch.object(
            McpServerRegistry,
            "test_connection",
            new=AsyncMock(return_value={"success": False, "error": "connection refused"}),
        ):
            user = MagicMock()
            user.email = "alice@example.com"
            with pytest.raises(HTTPException) as exc_info:
                await test_mcp_server(str(server.id), _user=user, db=session)
            assert exc_info.value.status_code == 502
            assert "connection refused" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_test_route_returns_success_ok(self, session: AsyncSession) -> None:
        from api.routes.mcp_servers import test_mcp_server

        server = await McpServerRegistry.create(
            session, name="r2", endpoint="http://x:1", transport="sse"
        )

        with patch.object(
            McpServerRegistry,
            "test_connection",
            new=AsyncMock(return_value={"success": True, "latency_ms": 5}),
        ):
            user = MagicMock()
            user.email = "alice@example.com"
            resp = await test_mcp_server(str(server.id), _user=user, db=session)
            assert resp.data.success is True
            assert resp.data.latency_ms == 5

    @pytest.mark.asyncio
    async def test_execute_route_raises_502_on_failure(self, session: AsyncSession) -> None:
        from api.routes.mcp_servers import execute_mcp_tool

        server = await McpServerRegistry.create(
            session, name="r3", endpoint="http://x:1", transport="sse"
        )

        with patch.object(
            McpServerRegistry,
            "execute_tool",
            new=AsyncMock(return_value={"success": False, "error": "tool crashed"}),
        ):
            user = MagicMock()
            user.email = "alice@example.com"
            with pytest.raises(HTTPException) as exc_info:
                await execute_mcp_tool(
                    str(server.id),
                    _user=user,
                    tool_name="foo",
                    arguments={},
                    db=session,
                )
            assert exc_info.value.status_code == 502
            assert "tool crashed" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_route_404_when_server_missing(self, session: AsyncSession) -> None:
        import uuid

        from api.routes.mcp_servers import test_mcp_server

        user = MagicMock()
        user.email = "alice@example.com"
        with pytest.raises(HTTPException) as exc_info:
            await test_mcp_server(str(uuid.uuid4()), _user=user, db=session)
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()
