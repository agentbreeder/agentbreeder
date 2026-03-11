"""Provider registry service — manages LLM provider configurations."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, func
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
    async def test_connection(
        session: AsyncSession,
        provider: Provider,
    ) -> dict:
        """Test a provider connection and return latency + model count.

        NOTE: This is a simulated test. In production, this would actually
        call the provider API to verify connectivity.
        """
        import time
        import random

        start = time.monotonic()

        # Simulate provider-specific connection test
        simulated_models: dict[str, list[str]] = {
            "openai": [
                "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo",
                "o1", "o1-mini", "o3-mini",
            ],
            "anthropic": [
                "claude-sonnet-4-20250514", "claude-haiku-4-20250414",
                "claude-3.5-sonnet-20241022", "claude-3-haiku-20240307",
            ],
            "google": [
                "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash",
            ],
            "ollama": [
                "llama3.2", "mistral", "codellama", "phi3",
            ],
            "litellm": [
                "gpt-4o", "claude-sonnet-4-20250514", "gemini-2.0-flash",
            ],
            "openrouter": [
                "openai/gpt-4o", "anthropic/claude-sonnet-4-20250514",
                "google/gemini-2.0-flash", "meta-llama/llama-3.1-70b",
            ],
        }

        models = simulated_models.get(provider.provider_type.value, [])
        elapsed_ms = int((time.monotonic() - start) * 1000) + random.randint(20, 150)

        # Update provider record
        provider.last_verified = datetime.now(timezone.utc)
        provider.latency_ms = elapsed_ms
        provider.model_count = len(models)
        provider.status = ProviderStatus.active
        await session.flush()

        return {
            "success": True,
            "latency_ms": elapsed_ms,
            "model_count": len(models),
            "error": None,
        }

    @staticmethod
    async def discover_models(
        session: AsyncSession,
        provider: Provider,
    ) -> list[str]:
        """Discover available models from a provider.

        NOTE: This is a simulated discovery. In production, this would call
        the provider's models API endpoint.
        """
        simulated_models: dict[str, list[str]] = {
            "openai": [
                "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo",
                "o1", "o1-mini", "o3-mini",
            ],
            "anthropic": [
                "claude-sonnet-4-20250514", "claude-haiku-4-20250414",
                "claude-3.5-sonnet-20241022", "claude-3-haiku-20240307",
            ],
            "google": [
                "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash",
            ],
            "ollama": [
                "llama3.2", "mistral", "codellama", "phi3",
            ],
            "litellm": [
                "gpt-4o", "claude-sonnet-4-20250514", "gemini-2.0-flash",
            ],
            "openrouter": [
                "openai/gpt-4o", "anthropic/claude-sonnet-4-20250514",
                "google/gemini-2.0-flash", "meta-llama/llama-3.1-70b",
            ],
        }

        models = simulated_models.get(provider.provider_type.value, [])
        provider.model_count = len(models)
        await session.flush()

        return models
