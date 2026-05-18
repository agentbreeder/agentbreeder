"""Unit tests for MCP discover_tools input validation (W4-09) + timeout (W4-11)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Base
from registry.mcp_servers import (
    DEFAULT_MCP_TIMEOUT_SECONDS,
    MAX_MCP_TIMEOUT_SECONDS,
    McpServerRegistry,
    _resolve_timeout,
)

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


# ---------------------------------------------------------------------------
# Timeout resolution + persistence (W4-11)
# ---------------------------------------------------------------------------


class TestTimeoutPersistence:
    @pytest.mark.asyncio
    async def test_default_timeout_when_unset(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="t1", endpoint="http://x:1", transport="sse"
        )
        assert _resolve_timeout(server) == float(DEFAULT_MCP_TIMEOUT_SECONDS)

    @pytest.mark.asyncio
    async def test_create_with_custom_timeout(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session,
            name="t2",
            endpoint="http://x:1",
            transport="sse",
            timeout_seconds=45,
        )
        assert _resolve_timeout(server) == 45.0

    @pytest.mark.asyncio
    async def test_update_changes_timeout(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="t3", endpoint="http://x:1", transport="sse"
        )
        updated = await McpServerRegistry.update(session, str(server.id), timeout_seconds=60)
        assert updated is not None
        assert _resolve_timeout(updated) == 60.0

    @pytest.mark.asyncio
    async def test_timeout_clamped_to_max(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session,
            name="t4",
            endpoint="http://x:1",
            transport="sse",
            timeout_seconds=9999,
        )
        assert _resolve_timeout(server) == float(MAX_MCP_TIMEOUT_SECONDS)

    @pytest.mark.asyncio
    async def test_timeout_clamped_to_min(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session,
            name="t5",
            endpoint="http://x:1",
            transport="sse",
            timeout_seconds=0,
        )
        assert _resolve_timeout(server) == 1.0


# ---------------------------------------------------------------------------
# Discover response validation (W4-09)
# ---------------------------------------------------------------------------


def _mock_async_client(json_payload: dict, status_code: int = 200):
    """Build a mock httpx.AsyncClient context manager."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_payload

    client = MagicMock()
    client.post = AsyncMock(return_value=response)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm, client


class TestDiscoverValidation:
    @pytest.mark.asyncio
    async def test_skips_tool_missing_name(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="d1", endpoint="http://x:1", transport="sse"
        )
        cm, _client = _mock_async_client(
            {
                "result": {
                    "tools": [
                        {"description": "no name here", "inputSchema": {}},
                        {"name": "valid", "description": "ok", "inputSchema": {}},
                    ]
                }
            }
        )
        with patch("httpx.AsyncClient", return_value=cm):
            result = await McpServerRegistry.discover_tools(session, str(server.id))
        names = [t["name"] for t in result["tools"]]
        assert names == ["valid"]
        assert result["total"] == 1

    @pytest.mark.asyncio
    async def test_skips_tool_with_empty_name(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="d2", endpoint="http://x:1", transport="sse"
        )
        cm, _client = _mock_async_client(
            {
                "result": {
                    "tools": [
                        {"name": "  ", "description": "blank", "inputSchema": {}},
                        {"name": "real", "description": "ok", "inputSchema": {}},
                    ]
                }
            }
        )
        with patch("httpx.AsyncClient", return_value=cm):
            result = await McpServerRegistry.discover_tools(session, str(server.id))
        assert [t["name"] for t in result["tools"]] == ["real"]

    @pytest.mark.asyncio
    async def test_skips_tool_with_non_dict_schema(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="d3", endpoint="http://x:1", transport="sse"
        )
        cm, _client = _mock_async_client(
            {
                "result": {
                    "tools": [
                        {"name": "bad", "description": "x", "inputSchema": "not-a-dict"},
                        {"name": "good", "description": "y", "inputSchema": {"type": "object"}},
                    ]
                }
            }
        )
        with patch("httpx.AsyncClient", return_value=cm):
            result = await McpServerRegistry.discover_tools(session, str(server.id))
        assert [t["name"] for t in result["tools"]] == ["good"]

    @pytest.mark.asyncio
    async def test_skips_non_dict_tool_entry(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="d4", endpoint="http://x:1", transport="sse"
        )
        cm, _client = _mock_async_client(
            {
                "result": {
                    "tools": [
                        "this-should-be-a-dict",
                        None,
                        {"name": "ok", "description": "y", "inputSchema": {}},
                    ]
                }
            }
        )
        with patch("httpx.AsyncClient", return_value=cm):
            result = await McpServerRegistry.discover_tools(session, str(server.id))
        assert [t["name"] for t in result["tools"]] == ["ok"]

    @pytest.mark.asyncio
    async def test_accepts_schema_alias_field(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="d5", endpoint="http://x:1", transport="sse"
        )
        cm, _client = _mock_async_client(
            {
                "result": {
                    "tools": [
                        # uses 'schema' instead of 'inputSchema'
                        {"name": "legacy", "description": "old", "schema": {"type": "object"}},
                    ]
                }
            }
        )
        with patch("httpx.AsyncClient", return_value=cm):
            result = await McpServerRegistry.discover_tools(session, str(server.id))
        assert result["tools"][0]["name"] == "legacy"
        assert result["tools"][0]["schema_definition"] == {"type": "object"}

    @pytest.mark.asyncio
    async def test_coerces_non_string_description(self, session: AsyncSession) -> None:
        server = await McpServerRegistry.create(
            session, name="d6", endpoint="http://x:1", transport="sse"
        )
        cm, _client = _mock_async_client(
            {
                "result": {
                    "tools": [
                        {"name": "x", "description": 42, "inputSchema": {}},
                    ]
                }
            }
        )
        with patch("httpx.AsyncClient", return_value=cm):
            result = await McpServerRegistry.discover_tools(session, str(server.id))
        assert result["tools"][0]["description"] == "42"
