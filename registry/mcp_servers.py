"""MCP Server registry service — manages MCP server connections."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import McpServer

logger = logging.getLogger(__name__)


class McpServerRegistry:
    """Service class for MCP server CRUD operations."""

    @staticmethod
    async def create(
        session: AsyncSession,
        name: str,
        endpoint: str,
        transport: str = "stdio",
    ) -> McpServer:
        """Register a new MCP server."""
        server = McpServer(
            name=name,
            endpoint=endpoint,
            transport=transport,
        )
        session.add(server)
        await session.flush()
        logger.info("Registered new MCP server '%s'", name)
        return server

    @staticmethod
    async def list(
        session: AsyncSession,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[McpServer], int]:
        """List MCP servers with pagination."""
        stmt = select(McpServer)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(McpServer.name)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        servers = list(result.scalars().all())

        return servers, total

    @staticmethod
    async def get_by_id(session: AsyncSession, server_id: str) -> McpServer | None:
        """Get an MCP server by UUID."""
        try:
            uid = uuid.UUID(server_id)
        except ValueError:
            return None
        stmt = select(McpServer).where(McpServer.id == uid)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        session: AsyncSession,
        server_id: str,
        name: str | None = None,
        endpoint: str | None = None,
        transport: str | None = None,
        status: str | None = None,
    ) -> McpServer | None:
        """Update an MCP server."""
        server = await McpServerRegistry.get_by_id(session, server_id)
        if not server:
            return None

        if name is not None:
            server.name = name
        if endpoint is not None:
            server.endpoint = endpoint
        if transport is not None:
            server.transport = transport
        if status is not None:
            server.status = status

        await session.flush()
        logger.info("Updated MCP server '%s'", server.name)
        return server

    @staticmethod
    async def delete(session: AsyncSession, server_id: str) -> bool:
        """Delete an MCP server."""
        server = await McpServerRegistry.get_by_id(session, server_id)
        if not server:
            return False
        await session.delete(server)
        await session.flush()
        logger.info("Deleted MCP server '%s'", server.name)
        return True

    @staticmethod
    async def test_connection(
        session: AsyncSession, server_id: str
    ) -> dict[str, object]:
        """Test connectivity to an MCP server (mock implementation)."""
        server = await McpServerRegistry.get_by_id(session, server_id)
        if not server:
            return {"success": False, "error": "Server not found"}

        # Mock: simulate a successful ping
        server.last_ping_at = datetime.now(UTC)
        server.status = "active"
        await session.flush()

        return {"success": True, "latency_ms": 42}

    @staticmethod
    async def discover_tools(
        session: AsyncSession, server_id: str
    ) -> dict[str, object]:
        """Discover tools exposed by an MCP server (mock implementation)."""
        server = await McpServerRegistry.get_by_id(session, server_id)
        if not server:
            return {"tools": [], "total": 0}

        # Mock: return sample tools based on server name
        mock_tools = [
            {
                "name": f"{server.name}-search",
                "description": f"Search tool provided by {server.name}",
                "schema_definition": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "limit": {
                            "type": "integer",
                            "description": "Max results",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": f"{server.name}-execute",
                "description": f"Execute action via {server.name}",
                "schema_definition": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "Action name"},
                        "params": {
                            "type": "object",
                            "description": "Action parameters",
                        },
                    },
                    "required": ["action"],
                },
            },
        ]

        server.tool_count = len(mock_tools)
        await session.flush()

        return {"tools": mock_tools, "total": len(mock_tools)}
