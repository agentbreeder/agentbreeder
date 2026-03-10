"""Prompt registry service — manages versioned prompt templates."""

from __future__ import annotations

import logging

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Prompt

logger = logging.getLogger(__name__)


class PromptRegistry:
    """Service class for prompt CRUD operations."""

    @staticmethod
    async def register(
        session: AsyncSession,
        name: str,
        version: str,
        content: str,
        description: str = "",
        team: str = "",
    ) -> Prompt:
        """Register or update a prompt in the registry."""
        stmt = select(Prompt).where(Prompt.name == name, Prompt.version == version)
        result = await session.execute(stmt)
        prompt = result.scalar_one_or_none()

        if prompt:
            prompt.content = content
            prompt.description = description
            prompt.team = team
            logger.info("Updated prompt '%s' v%s in registry", name, version)
        else:
            prompt = Prompt(
                name=name,
                version=version,
                content=content,
                description=description,
                team=team,
            )
            session.add(prompt)
            logger.info("Registered new prompt '%s' v%s in registry", name, version)

        await session.flush()
        return prompt

    @staticmethod
    async def list(
        session: AsyncSession,
        team: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Prompt], int]:
        """List prompts with optional filters."""
        stmt = select(Prompt)

        if team:
            stmt = stmt.where(Prompt.team == team)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Prompt.name, Prompt.version.desc())
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        prompts = list(result.scalars().all())

        return prompts, total

    @staticmethod
    async def get(session: AsyncSession, name: str, version: str | None = None) -> Prompt | None:
        """Get a prompt by name and optionally version (latest if not specified)."""
        stmt = select(Prompt).where(Prompt.name == name)
        if version:
            stmt = stmt.where(Prompt.version == version)
        else:
            stmt = stmt.order_by(Prompt.version.desc())
        result = await session.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def search(
        session: AsyncSession, query: str, page: int = 1, per_page: int = 20
    ) -> tuple[list[Prompt], int]:
        """Search prompts by name or description."""
        pattern = f"%{query}%"
        stmt = select(Prompt).where(
            or_(Prompt.name.ilike(pattern), Prompt.description.ilike(pattern)),
        )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Prompt.name)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        prompts = list(result.scalars().all())

        return prompts, total
