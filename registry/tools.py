"""Tool registry service — manages tools and MCP servers."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Service class for tool CRUD operations."""

    @staticmethod
    async def register(
        session: AsyncSession,
        name: str,
        description: str = "",
        tool_type: str = "mcp_server",
        schema_definition: dict | None = None,
        endpoint: str | None = None,
        source: str = "manual",
    ) -> Tool:
        """Register or update a tool in the registry."""
        stmt = select(Tool).where(Tool.name == name)
        result = await session.execute(stmt)
        tool = result.scalar_one_or_none()

        if tool:
            tool.description = description
            tool.tool_type = tool_type
            tool.schema_definition = schema_definition or {}
            tool.endpoint = endpoint
            tool.source = source
            tool.status = "active"
            logger.info("Updated tool '%s' in registry", name)
        else:
            tool = Tool(
                name=name,
                description=description,
                tool_type=tool_type,
                schema_definition=schema_definition or {},
                endpoint=endpoint,
                source=source,
            )
            session.add(tool)
            logger.info("Registered new tool '%s' in registry", name)

        await session.flush()
        return tool

    @staticmethod
    async def list(
        session: AsyncSession,
        tool_type: str | None = None,
        source: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Tool], int]:
        """List tools with optional filters."""
        stmt = select(Tool).where(Tool.status == "active")

        if tool_type:
            stmt = stmt.where(Tool.tool_type == tool_type)
        if source:
            stmt = stmt.where(Tool.source == source)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Tool.name)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        tools = list(result.scalars().all())

        return tools, total

    @staticmethod
    async def get(session: AsyncSession, name: str) -> Tool | None:
        """Get a tool by name."""
        stmt = select(Tool).where(Tool.name == name)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def search(
        session: AsyncSession, query: str, page: int = 1, per_page: int = 20
    ) -> tuple[list[Tool], int]:
        """Search tools by name or description."""
        pattern = f"%{query}%"
        stmt = select(Tool).where(
            Tool.status == "active",
            or_(Tool.name.ilike(pattern), Tool.description.ilike(pattern)),
        )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Tool.name)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        tools = list(result.scalars().all())

        return tools, total
