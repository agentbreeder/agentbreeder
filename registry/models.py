"""Model registry service — manages LLM model entries."""

from __future__ import annotations

import logging

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Model

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Service class for model CRUD operations."""

    @staticmethod
    async def register(
        session: AsyncSession,
        name: str,
        provider: str,
        description: str = "",
        config: dict | None = None,
        source: str = "manual",
    ) -> Model:
        """Register or update a model in the registry."""
        stmt = select(Model).where(Model.name == name)
        result = await session.execute(stmt)
        model = result.scalar_one_or_none()

        if model:
            model.provider = provider
            model.description = description
            model.config = config or {}
            model.source = source
            model.status = "active"
            logger.info("Updated model '%s' in registry", name)
        else:
            model = Model(
                name=name,
                provider=provider,
                description=description,
                config=config or {},
                source=source,
            )
            session.add(model)
            logger.info("Registered new model '%s' in registry", name)

        await session.flush()
        return model

    @staticmethod
    async def list(
        session: AsyncSession,
        provider: str | None = None,
        source: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Model], int]:
        """List models with optional filters."""
        stmt = select(Model).where(Model.status == "active")

        if provider:
            stmt = stmt.where(Model.provider == provider)
        if source:
            stmt = stmt.where(Model.source == source)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Model.name)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        models = list(result.scalars().all())

        return models, total

    @staticmethod
    async def get(session: AsyncSession, name: str) -> Model | None:
        """Get a model by name."""
        stmt = select(Model).where(Model.name == name)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def search(
        session: AsyncSession, query: str, page: int = 1, per_page: int = 20
    ) -> tuple[list[Model], int]:
        """Search models by name or description."""
        pattern = f"%{query}%"
        stmt = select(Model).where(
            Model.status == "active",
            or_(Model.name.ilike(pattern), Model.description.ilike(pattern)),
        )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Model.name)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        models = list(result.scalars().all())

        return models, total
