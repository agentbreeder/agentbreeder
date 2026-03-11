"""Model registry service — manages LLM model entries."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Agent, Model

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
        context_window: int | None = None,
        max_output_tokens: int | None = None,
        input_price_per_million: float | None = None,
        output_price_per_million: float | None = None,
        capabilities: list[str] | None = None,
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
            model.context_window = context_window
            model.max_output_tokens = max_output_tokens
            model.input_price_per_million = input_price_per_million
            model.output_price_per_million = output_price_per_million
            model.capabilities = capabilities
            logger.info("Updated model '%s' in registry", name)
        else:
            model = Model(
                name=name,
                provider=provider,
                description=description,
                config=config or {},
                source=source,
                context_window=context_window,
                max_output_tokens=max_output_tokens,
                input_price_per_million=input_price_per_million,
                output_price_per_million=output_price_per_million,
                capabilities=capabilities,
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
    async def get_by_id(session: AsyncSession, model_id: str) -> Model | None:
        """Get a model by UUID."""
        try:
            uid = uuid.UUID(model_id)
        except ValueError:
            return None
        stmt = select(Model).where(Model.id == uid)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_ids(session: AsyncSession, model_ids: list[str]) -> list[Model]:
        """Get multiple models by UUID list."""
        uids: list[uuid.UUID] = []
        for mid in model_ids:
            try:
                uids.append(uuid.UUID(mid))
            except ValueError:
                continue
        if not uids:
            return []
        stmt = select(Model).where(Model.id.in_(uids))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_usage(
        session: AsyncSession, model_id: str
    ) -> list[tuple[Agent, str]]:
        """Find agents that use this model as primary or fallback.

        Returns list of (agent, usage_type) tuples.
        """
        model = await ModelRegistry.get_by_id(session, model_id)
        if not model:
            return []
        stmt = select(Agent).where(
            or_(
                Agent.model_primary == model.name,
                Agent.model_fallback == model.name,
            )
        )
        result = await session.execute(stmt)
        agents = list(result.scalars().all())
        out: list[tuple[Agent, str]] = []
        for agent in agents:
            if agent.model_primary == model.name:
                out.append((agent, "primary"))
            elif agent.model_fallback == model.name:
                out.append((agent, "fallback"))
        return out

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
