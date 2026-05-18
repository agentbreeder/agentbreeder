"""Tool registry service — manages tools and MCP servers."""

from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Agent, Tool

logger = logging.getLogger(__name__)


_VALID_JSON_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)


class ToolRegistryMetadata(BaseModel):
    """Validation model for tool registry entries.

    Mirrors the persistence shape of :class:`api.models.database.Tool` but adds
    strict validation: non-empty name + description, well-formed JSON-Schema
    shape, and a parseable endpoint URL when provided.
    """

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    tool_type: str = Field(default="mcp_server", max_length=50)
    schema_definition: dict[str, Any] | None = None
    endpoint: str | None = None
    source: str = Field(default="manual", max_length=50)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must be non-empty")
        return v

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        # Required field — must be a string. Empty strings are permitted for
        # backward compatibility with pre-W4 callers, but logged as a warning
        # at registration time (see ToolRegistry.register).
        if not isinstance(v, str):
            raise ValueError("description must be a string")
        return v

    @field_validator("schema_definition")
    @classmethod
    def _validate_schema(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None or v == {}:
            return v
        if not isinstance(v, dict):
            raise ValueError("schema_definition must be a dict")
        # Optional type field — when present, must be a known JSON Schema type.
        if "type" in v:
            t = v["type"]
            if isinstance(t, str) and t not in _VALID_JSON_SCHEMA_TYPES:
                raise ValueError(f"schema_definition.type '{t}' is not a valid JSON Schema type")
            if isinstance(t, list):
                bad = [x for x in t if x not in _VALID_JSON_SCHEMA_TYPES]
                if bad:
                    raise ValueError(
                        f"schema_definition.type contains invalid JSON Schema types: {bad}"
                    )
        # Properties, if present, must be a dict.
        if "properties" in v and not isinstance(v["properties"], dict):
            raise ValueError("schema_definition.properties must be a dict")
        # Required, if present, must be a list of strings.
        if "required" in v:
            req = v["required"]
            if not isinstance(req, list) or not all(isinstance(x, str) for x in req):
                raise ValueError("schema_definition.required must be a list of strings")
        return v

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return v
        parsed = urlparse(v)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"endpoint '{v}' is not a valid URL (must include scheme and host)")
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"endpoint scheme '{parsed.scheme}' is not supported (use http/https)"
            )
        return v


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
        # Validate inputs through ToolRegistryMetadata before touching the DB.
        validated = ToolRegistryMetadata(
            name=name,
            description=description,
            tool_type=tool_type,
            schema_definition=schema_definition,
            endpoint=endpoint,
            source=source,
        )
        if not validated.description.strip():
            logger.warning(
                "Tool '%s' registered with empty description — please provide one",
                validated.name,
            )
        name = validated.name
        description = validated.description
        tool_type = validated.tool_type
        schema_definition = validated.schema_definition
        endpoint = validated.endpoint
        source = validated.source

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
    async def get_by_id(session: AsyncSession, tool_id: str) -> Tool | None:
        """Get a tool by UUID."""
        try:
            uid = uuid.UUID(tool_id)
        except ValueError:
            return None
        stmt = select(Tool).where(Tool.id == uid)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_usage(session: AsyncSession, tool_id: str) -> list[Agent]:
        """Find agents that reference this tool in their config_snapshot."""
        tool = await ToolRegistry.get_by_id(session, tool_id)
        if not tool:
            return []
        # Search agents whose config_snapshot contains a tools list referencing this tool
        stmt = select(Agent).where(Agent.status != "archived")
        result = await session.execute(stmt)
        agents = list(result.scalars().all())
        matching: list[Agent] = []
        for agent in agents:
            config = agent.config_snapshot or {}
            tools_list = config.get("tools", [])
            for t in tools_list:
                ref = t.get("ref", "") if isinstance(t, dict) else ""
                name_val = t.get("name", "") if isinstance(t, dict) else ""
                if tool.name in ref or tool.name == name_val:
                    matching.append(agent)
                    break
        return matching

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
