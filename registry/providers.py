"""Provider registry service — manages LLM provider configurations."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Provider
from api.models.enums import ProviderStatus, ProviderType

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Service class for provider CRUD operations."""

    @staticmethod
    async def create(
        session: AsyncSession,
        name: str,
        provider_type: ProviderType,
        base_url: str | None = None,
        config: dict | None = None,
    ) -> Provider:
        """Create a new provider configuration."""
        provider = Provider(
            name=name,
            provider_type=provider_type,
            base_url=base_url,
            config=config,
        )
        session.add(provider)
        await session.flush()
        logger.info("Registered new provider '%s' (%s)", name, provider_type.value)
        return provider

    @staticmethod
    async def list(
        session: AsyncSession,
        provider_type: ProviderType | None = None,
        status: ProviderStatus | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[Provider], int]:
        """List providers with optional filters."""
        stmt = select(Provider)

        if provider_type:
            stmt = stmt.where(Provider.provider_type == provider_type)
        if status:
            stmt = stmt.where(Provider.status == status)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.order_by(Provider.name)
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)

        result = await session.execute(stmt)
        providers = list(result.scalars().all())

        return providers, total

    @staticmethod
    async def get(session: AsyncSession, provider_id: uuid.UUID) -> Provider | None:
        """Get a provider by ID."""
        stmt = select(Provider).where(Provider.id == provider_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update(
        session: AsyncSession,
        provider: Provider,
        name: str | None = None,
        base_url: str | None = None,
        status: ProviderStatus | None = None,
        config: dict | None = None,
    ) -> Provider:
        """Update an existing provider."""
        if name is not None:
            provider.name = name
        if base_url is not None:
            provider.base_url = base_url
        if status is not None:
            provider.status = status
        if config is not None:
            provider.config = config

        await session.flush()
        logger.info("Updated provider '%s'", provider.name)
        return provider

    @staticmethod
    async def delete(session: AsyncSession, provider: Provider) -> None:
        """Delete a provider."""
        name = provider.name
        await session.delete(provider)
        await session.flush()
        logger.info("Deleted provider '%s'", name)

    @staticmethod
    async def toggle(session: AsyncSession, provider: Provider) -> Provider:
        """Toggle a provider's is_enabled flag."""
        provider.is_enabled = not provider.is_enabled
        if provider.is_enabled:
            provider.status = ProviderStatus.active
        else:
            provider.status = ProviderStatus.disabled
        await session.flush()
        logger.info(
            "Toggled provider '%s' -> %s",
            provider.name,
            "enabled" if provider.is_enabled else "disabled",
        )
        return provider

    @staticmethod
    async def update_provider_status(
        session: AsyncSession, provider_id: uuid.UUID, status: ProviderStatus
    ) -> Provider | None:
        """Update only the health status of a provider."""
        provider = await ProviderRegistry.get(session, provider_id)
        if not provider:
            return None
        provider.status = status
        await session.flush()
        logger.info("Updated provider '%s' status -> %s", provider.name, status.value)
        return provider

    @staticmethod
    async def get_by_type(session: AsyncSession, provider_type: ProviderType) -> Provider | None:
        """Get first provider matching the given type."""
        stmt = select(Provider).where(Provider.provider_type == provider_type).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
