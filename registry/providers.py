"""Provider registry service — manages LLM provider configurations."""

from __future__ import annotations

import logging
import random
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Provider
from api.models.enums import ProviderStatus, ProviderType
from registry.models import ModelRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rich model discovery data per provider type
# ---------------------------------------------------------------------------

PROVIDER_MODELS: dict[str, list[dict]] = {
    "openai": [
        {
            "id": "gpt-4o",
            "name": "GPT-4o",
            "context_window": 128_000,
            "max_output_tokens": 16_384,
            "input_price_per_million": 2.50,
            "output_price_per_million": 10.00,
            "capabilities": ["chat", "vision", "function_calling", "json_mode"],
        },
        {
            "id": "gpt-4o-mini",
            "name": "GPT-4o Mini",
            "context_window": 128_000,
            "max_output_tokens": 16_384,
            "input_price_per_million": 0.15,
            "output_price_per_million": 0.60,
            "capabilities": ["chat", "vision", "function_calling", "json_mode"],
        },
        {
            "id": "gpt-4-turbo",
            "name": "GPT-4 Turbo",
            "context_window": 128_000,
            "max_output_tokens": 4_096,
            "input_price_per_million": 10.00,
            "output_price_per_million": 30.00,
            "capabilities": ["chat", "vision", "function_calling", "json_mode"],
        },
        {
            "id": "o3-mini",
            "name": "o3-mini",
            "context_window": 200_000,
            "max_output_tokens": 100_000,
            "input_price_per_million": 1.10,
            "output_price_per_million": 4.40,
            "capabilities": ["chat", "reasoning", "function_calling"],
        },
        {
            "id": "text-embedding-3-small",
            "name": "Text Embedding 3 Small",
            "context_window": 8_191,
            "max_output_tokens": None,
            "input_price_per_million": 0.02,
            "output_price_per_million": None,
            "capabilities": ["embedding"],
        },
        {
            "id": "text-embedding-3-large",
            "name": "Text Embedding 3 Large",
            "context_window": 8_191,
            "max_output_tokens": None,
            "input_price_per_million": 0.13,
            "output_price_per_million": None,
            "capabilities": ["embedding"],
        },
    ],
    "anthropic": [
        {
            "id": "claude-opus-4-6",
            "name": "Claude Opus 4.6",
            "context_window": 200_000,
            "max_output_tokens": 32_000,
            "input_price_per_million": 15.00,
            "output_price_per_million": 75.00,
            "capabilities": ["chat", "vision", "function_calling", "extended_thinking"],
        },
        {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "context_window": 200_000,
            "max_output_tokens": 16_000,
            "input_price_per_million": 3.00,
            "output_price_per_million": 15.00,
            "capabilities": ["chat", "vision", "function_calling", "extended_thinking"],
        },
        {
            "id": "claude-haiku-4-5",
            "name": "Claude Haiku 4.5",
            "context_window": 200_000,
            "max_output_tokens": 8_192,
            "input_price_per_million": 0.80,
            "output_price_per_million": 4.00,
            "capabilities": ["chat", "vision", "function_calling"],
        },
    ],
    "google": [
        {
            "id": "gemini-2.5-pro",
            "name": "Gemini 2.5 Pro",
            "context_window": 1_000_000,
            "max_output_tokens": 65_536,
            "input_price_per_million": 1.25,
            "output_price_per_million": 10.00,
            "capabilities": ["chat", "vision", "function_calling", "grounding"],
        },
        {
            "id": "gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "context_window": 1_000_000,
            "max_output_tokens": 65_536,
            "input_price_per_million": 0.15,
            "output_price_per_million": 0.60,
            "capabilities": ["chat", "vision", "function_calling", "grounding"],
        },
    ],
    "ollama": [
        {
            "id": "llama3.2",
            "name": "Llama 3.2",
            "context_window": 128_000,
            "max_output_tokens": 4_096,
            "input_price_per_million": None,
            "output_price_per_million": None,
            "capabilities": ["chat", "function_calling"],
        },
        {
            "id": "mistral",
            "name": "Mistral 7B",
            "context_window": 32_000,
            "max_output_tokens": 4_096,
            "input_price_per_million": None,
            "output_price_per_million": None,
            "capabilities": ["chat"],
        },
        {
            "id": "codellama",
            "name": "Code Llama",
            "context_window": 16_000,
            "max_output_tokens": 4_096,
            "input_price_per_million": None,
            "output_price_per_million": None,
            "capabilities": ["chat", "code"],
        },
        {
            "id": "nomic-embed-text",
            "name": "Nomic Embed Text",
            "context_window": 8_192,
            "max_output_tokens": None,
            "input_price_per_million": None,
            "output_price_per_million": None,
            "capabilities": ["embedding"],
        },
    ],
    "litellm": [
        {
            "id": "gpt-4o",
            "name": "GPT-4o (via LiteLLM)",
            "context_window": 128_000,
            "max_output_tokens": 16_384,
            "input_price_per_million": 2.50,
            "output_price_per_million": 10.00,
            "capabilities": ["chat", "vision", "function_calling"],
        },
        {
            "id": "claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6 (via LiteLLM)",
            "context_window": 200_000,
            "max_output_tokens": 16_000,
            "input_price_per_million": 3.00,
            "output_price_per_million": 15.00,
            "capabilities": ["chat", "vision", "function_calling"],
        },
        {
            "id": "gemini-2.5-flash",
            "name": "Gemini 2.5 Flash (via LiteLLM)",
            "context_window": 1_000_000,
            "max_output_tokens": 65_536,
            "input_price_per_million": 0.15,
            "output_price_per_million": 0.60,
            "capabilities": ["chat", "vision", "function_calling"],
        },
    ],
    "openrouter": [
        {
            "id": "openai/gpt-4o",
            "name": "GPT-4o (OpenRouter)",
            "context_window": 128_000,
            "max_output_tokens": 16_384,
            "input_price_per_million": 2.50,
            "output_price_per_million": 10.00,
            "capabilities": ["chat", "vision", "function_calling"],
        },
        {
            "id": "anthropic/claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6 (OpenRouter)",
            "context_window": 200_000,
            "max_output_tokens": 16_000,
            "input_price_per_million": 3.00,
            "output_price_per_million": 15.00,
            "capabilities": ["chat", "vision", "function_calling"],
        },
        {
            "id": "google/gemini-2.5-pro",
            "name": "Gemini 2.5 Pro (OpenRouter)",
            "context_window": 1_000_000,
            "max_output_tokens": 65_536,
            "input_price_per_million": 1.25,
            "output_price_per_million": 10.00,
            "capabilities": ["chat", "vision", "function_calling"],
        },
        {
            "id": "meta-llama/llama-3.1-70b",
            "name": "Llama 3.1 70B (OpenRouter)",
            "context_window": 128_000,
            "max_output_tokens": 4_096,
            "input_price_per_million": 0.52,
            "output_price_per_million": 0.75,
            "capabilities": ["chat", "function_calling"],
        },
    ],
}


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
    async def test_connection(
        session: AsyncSession,
        provider: Provider,
    ) -> dict:
        """Test a provider connection and return latency + model count.

        NOTE: This is a simulated test. In production, this would actually
        call the provider API to verify connectivity.
        """
        start = time.monotonic()

        models = PROVIDER_MODELS.get(provider.provider_type.value, [])
        elapsed_ms = int((time.monotonic() - start) * 1000) + random.randint(150, 300)

        # Update provider record
        provider.last_verified = datetime.now(UTC)
        provider.latency_ms = elapsed_ms
        provider.avg_latency_ms = elapsed_ms  # first test sets avg equal to reading
        provider.model_count = len(models)
        provider.status = ProviderStatus.active
        await session.flush()

        return {
            "success": True,
            "latency_ms": elapsed_ms,
            "models_found": len(models),
            "error": None,
        }

    @staticmethod
    async def discover_models(
        session: AsyncSession,
        provider: Provider,
    ) -> list[dict]:
        """Discover available models from a provider.

        Returns a list of dicts with id, name, context_window,
        max_output_tokens, pricing, and capabilities.

        NOTE: This is a simulated discovery. In production, this would call
        the provider's models API endpoint.
        """
        models = PROVIDER_MODELS.get(provider.provider_type.value, [])
        provider.model_count = len(models)
        await session.flush()

        return models

    @staticmethod
    async def auto_register_models(
        session: AsyncSession,
        provider: Provider,
        discovered_models: list[dict],
    ) -> int:
        """Register discovered models into the Model Registry.

        For each discovered model, check if it already exists (by name).
        If not, create it with metadata from the discovery result.

        Returns the number of newly registered models.
        """
        registered_count = 0
        provider_name = provider.provider_type.value

        for model_data in discovered_models:
            model_name = model_data["id"]
            existing = await ModelRegistry.get(session, model_name)
            if existing is not None:
                logger.debug(
                    "Model '%s' already exists in registry, skipping", model_name
                )
                continue

            await ModelRegistry.register(
                session,
                name=model_name,
                provider=provider_name,
                description=model_data.get("name", ""),
                source="discovery",
                context_window=model_data.get("context_window"),
                max_output_tokens=model_data.get("max_output_tokens"),
                input_price_per_million=model_data.get("input_price_per_million"),
                output_price_per_million=model_data.get("output_price_per_million"),
                capabilities=model_data.get("capabilities"),
            )
            registered_count += 1
            logger.info(
                "Auto-registered model '%s' from provider '%s'",
                model_name,
                provider.name,
            )

        # Update provider model_count to reflect total discovered
        provider.model_count = len(discovered_models)
        await session.flush()

        return registered_count

    @staticmethod
    async def get_by_type(
        session: AsyncSession, provider_type: ProviderType
    ) -> Provider | None:
        """Get first provider matching the given type."""
        stmt = select(Provider).where(Provider.provider_type == provider_type).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
