"""A2A Agent registry service — manages A2A agent registrations."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import A2AAgent
from api.models.enums import A2AStatus

logger = logging.getLogger(__name__)


class A2AAgentRegistry:
    """Service class for A2A agent CRUD operations."""

    @staticmethod
    async def create(
        session: AsyncSession,
        name: str,
        endpoint_url: str,
        agent_id: uuid.UUID | None = None,
        agent_card: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        auth_scheme: str = "none",
        team: str | None = None,
    ) -> A2AAgent:
        """Register a new A2A agent."""
        agent = A2AAgent(
            name=name,
            endpoint_url=endpoint_url,
            agent_id=agent_id,
            agent_card=agent_card or {},
            capabilities=capabilities or [],
            auth_scheme=auth_scheme,
            team=team,
        )
        session.add(agent)
        await session.flush()
        logger.info("Registered A2A agent '%s'", name)
        return agent

    @staticmethod
    async def list(
        session: AsyncSession,
        team: str | None = None,
        status: A2AStatus | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[A2AAgent], int]:
        """List A2A agents with optional filters."""
        stmt = select(A2AAgent)
        if team:
            stmt = stmt.where(A2AAgent.team == team)
        if status:
            stmt = stmt.where(A2AAgent.status == status)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(A2AAgent.name)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        agents = list(result.scalars().all())
        return agents, total

    @staticmethod
    async def get_by_id(session: AsyncSession, a2a_id: str) -> A2AAgent | None:
        """Get an A2A agent by UUID."""
        try:
            uid = uuid.UUID(a2a_id)
        except ValueError:
            return None
        stmt = select(A2AAgent).where(A2AAgent.id == uid)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_name(session: AsyncSession, name: str) -> A2AAgent | None:
        """Get an A2A agent by name."""
        stmt = select(A2AAgent).where(A2AAgent.name == name)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        session: AsyncSession,
        a2a_id: str,
        endpoint_url: str | None = None,
        agent_card: dict[str, Any] | None = None,
        capabilities: list[str] | None = None,
        auth_scheme: str | None = None,
        status: A2AStatus | None = None,
    ) -> A2AAgent | None:
        """Update an A2A agent."""
        agent = await A2AAgentRegistry.get_by_id(session, a2a_id)
        if not agent:
            return None

        if endpoint_url is not None:
            agent.endpoint_url = endpoint_url
        if agent_card is not None:
            agent.agent_card = agent_card
        if capabilities is not None:
            agent.capabilities = capabilities
        if auth_scheme is not None:
            agent.auth_scheme = auth_scheme
        if status is not None:
            agent.status = status

        await session.flush()
        logger.info("Updated A2A agent '%s'", agent.name)
        return agent

    @staticmethod
    async def delete(session: AsyncSession, a2a_id: str) -> bool:
        """Delete an A2A agent."""
        agent = await A2AAgentRegistry.get_by_id(session, a2a_id)
        if not agent:
            return False
        await session.delete(agent)
        await session.flush()
        logger.info("Deleted A2A agent '%s'", agent.name)
        return True
