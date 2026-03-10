"""Agent registry service — the ONLY place that writes to the agents table."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Agent
from api.models.enums import AgentStatus
from engine.config_parser import AgentConfig

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Service class for agent CRUD operations in the registry."""

    @staticmethod
    async def register(
        session: AsyncSession,
        config: AgentConfig,
        endpoint_url: str,
    ) -> Agent:
        """Register or update an agent after successful deployment."""
        # Check if agent already exists
        stmt = select(Agent).where(Agent.name == config.name)
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()

        if agent:
            agent.version = config.version
            agent.description = config.description
            agent.team = config.team
            agent.owner = config.owner
            agent.framework = config.framework.value
            agent.model_primary = config.model.primary
            agent.model_fallback = config.model.fallback
            agent.endpoint_url = endpoint_url
            agent.status = AgentStatus.running
            agent.tags = config.tags
            agent.config_snapshot = config.model_dump(mode="json")
            logger.info("Updated agent '%s' in registry", config.name)
        else:
            agent = Agent(
                name=config.name,
                version=config.version,
                description=config.description,
                team=config.team,
                owner=config.owner,
                framework=config.framework.value,
                model_primary=config.model.primary,
                model_fallback=config.model.fallback,
                endpoint_url=endpoint_url,
                status=AgentStatus.running,
                tags=config.tags,
                config_snapshot=config.model_dump(mode="json"),
            )
            session.add(agent)
            logger.info("Registered new agent '%s' in registry", config.name)

        await session.flush()
        return agent

    @staticmethod
    async def get(session: AsyncSession, name: str) -> Agent | None:
        """Get an agent by name."""
        stmt = select(Agent).where(Agent.name == name)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(session: AsyncSession, agent_id: uuid.UUID) -> Agent | None:
        """Get an agent by ID."""
        stmt = select(Agent).where(Agent.id == agent_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list(
        session: AsyncSession,
        team: str | None = None,
        framework: str | None = None,
        status: AgentStatus | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Agent], int]:
        """List agents with optional filters. Returns (agents, total_count)."""
        stmt = select(Agent)

        if team:
            stmt = stmt.where(Agent.team == team)
        if framework:
            stmt = stmt.where(Agent.framework == framework)
        if status:
            stmt = stmt.where(Agent.status == status)

        # Count total
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        # Paginate
        stmt = stmt.order_by(Agent.created_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        agents = list(result.scalars().all())

        return agents, total

    @staticmethod
    async def update_status(
        session: AsyncSession, agent_id: uuid.UUID, status: AgentStatus
    ) -> None:
        """Update an agent's status."""
        stmt = select(Agent).where(Agent.id == agent_id)
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()
        if agent:
            agent.status = status
            await session.flush()

    @staticmethod
    async def search(
        session: AsyncSession,
        query: str,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Agent], int]:
        """Search agents by name, description, team, or tags."""
        pattern = f"%{query}%"
        stmt = select(Agent).where(
            or_(
                Agent.name.ilike(pattern),
                Agent.description.ilike(pattern),
                Agent.team.ilike(pattern),
                Agent.framework.ilike(pattern),
            )
        )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Agent.created_at.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        agents = list(result.scalars().all())

        return agents, total

    @staticmethod
    async def delete(session: AsyncSession, name: str) -> bool:
        """Soft-delete (archive) an agent by setting status to stopped."""
        stmt = select(Agent).where(Agent.name == name)
        result = await session.execute(stmt)
        agent = result.scalar_one_or_none()
        if agent:
            agent.status = AgentStatus.stopped
            await session.flush()
            return True
        return False
