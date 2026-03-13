"""Tests for MCP server registry service."""

from __future__ import annotations

import pytest
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


class TestMcpServerCreate:
    @pytest.mark.asyncio
    async def test_create_server(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="test-mcp", endpoint="http://localhost:3000", transport="sse"
        )
        assert server.name == "test-mcp"
        assert server.endpoint == "http://localhost:3000"
        assert server.transport == "sse"
        assert server.status == "active"
        assert server.tool_count == 0

    @pytest.mark.asyncio
    async def test_create_default_transport(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="stdio-mcp", endpoint="npx @mcp/server"
        )
        assert server.transport == "stdio"


class TestMcpServerList:
    @pytest.mark.asyncio
    async def test_list_empty(self, session: AsyncSession) -> None:
        servers, total = await McpServerRegistry.list(session)
        assert servers == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_list_with_servers(self, session: AsyncSession) -> None:
        await McpServerRegistry.create(session, name="alpha", endpoint="http://a")
        await McpServerRegistry.create(session, name="beta", endpoint="http://b")
        servers, total = await McpServerRegistry.list(session)
        assert total == 2
        assert len(servers) == 2
        # Ordered by name
        assert servers[0].name == "alpha"
        assert servers[1].name == "beta"

    @pytest.mark.asyncio
    async def test_list_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await McpServerRegistry.create(session, name=f"s{i:02d}", endpoint=f"http://s{i}")
        servers, total = await McpServerRegistry.list(session, page=1, per_page=2)
        assert total == 5
        assert len(servers) == 2


class TestMcpServerGetById:
    @pytest.mark.asyncio
    async def test_get_existing(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(session, name="x", endpoint="http://x")
        found = await McpServerRegistry.get_by_id(session, str(server.id))
        assert found is not None
        assert found.name == "x"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, session: AsyncSession) -> None:
        found = await McpServerRegistry.get_by_id(session, "00000000-0000-0000-0000-000000000000")
        assert found is None

    @pytest.mark.asyncio
    async def test_get_invalid_uuid(self, session: AsyncSession) -> None:
        found = await McpServerRegistry.get_by_id(session, "not-a-uuid")
        assert found is None


class TestMcpServerUpdate:
    @pytest.mark.asyncio
    async def test_update_fields(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(session, name="old", endpoint="http://old")
        updated = await McpServerRegistry.update(
            session,
            str(server.id),
            name="new",
            endpoint="http://new",
            transport="sse",
            status="disabled",
        )
        assert updated is not None
        assert updated.name == "new"
        assert updated.endpoint == "http://new"
        assert updated.transport == "sse"
        assert updated.status == "disabled"

    @pytest.mark.asyncio
    async def test_update_partial(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(session, name="orig", endpoint="http://orig")
        updated = await McpServerRegistry.update(session, str(server.id), name="changed")
        assert updated is not None
        assert updated.name == "changed"
        assert updated.endpoint == "http://orig"  # unchanged

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, session: AsyncSession) -> None:
        result = await McpServerRegistry.update(
            session, "00000000-0000-0000-0000-000000000000", name="x"
        )
        assert result is None


class TestMcpServerDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(session, name="del", endpoint="http://del")
        result = await McpServerRegistry.delete(session, str(server.id))
        assert result is True
        # Verify it's gone
        found = await McpServerRegistry.get_by_id(session, str(server.id))
        assert found is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, session: AsyncSession) -> None:
        result = await McpServerRegistry.delete(session, "00000000-0000-0000-0000-000000000000")
        assert result is False


class TestMcpServerTestConnection:
    @pytest.mark.asyncio
    async def test_successful_ping(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(session, name="ping", endpoint="http://ping")
        result = await McpServerRegistry.test_connection(session, str(server.id))
        assert result["success"] is True
        assert isinstance(result["latency_ms"], int)
        # Verify server was updated
        refreshed = await McpServerRegistry.get_by_id(session, str(server.id))
        assert refreshed is not None
        assert refreshed.last_ping_at is not None
        assert refreshed.status == "active"

    @pytest.mark.asyncio
    async def test_ping_nonexistent(self, session: AsyncSession) -> None:
        result = await McpServerRegistry.test_connection(
            session, "00000000-0000-0000-0000-000000000000"
        )
        assert result["success"] is False
        assert "not found" in result["error"].lower()


class TestMcpServerDiscoverTools:
    @pytest.mark.asyncio
    async def test_discover(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(session, name="disc", endpoint="http://disc")
        result = await McpServerRegistry.discover_tools(session, str(server.id))
        assert result["total"] == 2
        assert len(result["tools"]) == 2
        assert result["tools"][0]["name"] == "disc-search"
        assert result["tools"][1]["name"] == "disc-execute"
        # Verify tool_count updated
        refreshed = await McpServerRegistry.get_by_id(session, str(server.id))
        assert refreshed is not None
        assert refreshed.tool_count == 2

    @pytest.mark.asyncio
    async def test_discover_nonexistent(self, session: AsyncSession) -> None:
        result = await McpServerRegistry.discover_tools(
            session, "00000000-0000-0000-0000-000000000000"
        )
        assert result["tools"] == []
        assert result["total"] == 0
