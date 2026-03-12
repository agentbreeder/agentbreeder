"""Tests for provider backend features: auto-register, health check, Ollama detect, status."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Base, Model
from api.models.enums import ProviderType
from api.tasks.provider_health import check_all_providers
from registry.models import ModelRegistry
from registry.providers import ProviderRegistry

_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionFactory() as s:
        yield s
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ─── Auto-register models ──────────────────────────────────────────────────


class TestAutoRegisterModels:
    @pytest.mark.asyncio
    async def test_creates_new_models(self, session: AsyncSession) -> None:
        """Auto-register should create models that don't exist in the registry."""
        provider = await ProviderRegistry.create(
            session, name="openai-prod", provider_type=ProviderType.openai
        )
        discovered = [
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "context_window": 128_000,
                "max_output_tokens": 16_384,
                "input_price_per_million": 2.50,
                "output_price_per_million": 10.00,
                "capabilities": ["chat", "vision"],
            },
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4o Mini",
                "context_window": 128_000,
                "max_output_tokens": 16_384,
                "input_price_per_million": 0.15,
                "output_price_per_million": 0.60,
                "capabilities": ["chat"],
            },
        ]

        count = await ProviderRegistry.auto_register_models(session, provider, discovered)
        assert count == 2

        # Verify models exist in registry
        model = await ModelRegistry.get(session, "gpt-4o")
        assert model is not None
        assert model.provider == "openai"
        assert model.source == "discovery"
        assert model.context_window == 128_000
        assert model.capabilities == ["chat", "vision"]

    @pytest.mark.asyncio
    async def test_skips_existing_models(self, session: AsyncSession) -> None:
        """Auto-register should skip models that already exist."""
        # Pre-register a model
        await ModelRegistry.register(
            session, name="gpt-4o", provider="openai", description="Already here"
        )

        provider = await ProviderRegistry.create(
            session, name="openai-prod", provider_type=ProviderType.openai
        )
        discovered = [
            {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "context_window": 128_000,
                "max_output_tokens": 16_384,
                "input_price_per_million": 2.50,
                "output_price_per_million": 10.00,
                "capabilities": ["chat"],
            },
            {
                "id": "gpt-4o-mini",
                "name": "GPT-4o Mini",
                "context_window": 128_000,
                "max_output_tokens": 16_384,
                "input_price_per_million": 0.15,
                "output_price_per_million": 0.60,
                "capabilities": ["chat"],
            },
        ]

        count = await ProviderRegistry.auto_register_models(session, provider, discovered)
        assert count == 1  # only gpt-4o-mini was new

        # Existing model should not have been overwritten
        model = await ModelRegistry.get(session, "gpt-4o")
        assert model.description == "Already here"

    @pytest.mark.asyncio
    async def test_updates_provider_model_count(self, session: AsyncSession) -> None:
        """Auto-register should update the provider's model_count."""
        provider = await ProviderRegistry.create(
            session, name="p1", provider_type=ProviderType.anthropic
        )
        discovered = [
            {"id": "claude-sonnet", "name": "Sonnet", "capabilities": []},
            {"id": "claude-haiku", "name": "Haiku", "capabilities": []},
        ]
        await ProviderRegistry.auto_register_models(session, provider, discovered)
        assert provider.model_count == 2

    @pytest.mark.asyncio
    async def test_empty_discovery_list(self, session: AsyncSession) -> None:
        """Auto-register with empty list should return 0."""
        provider = await ProviderRegistry.create(
            session, name="p1", provider_type=ProviderType.openai
        )
        count = await ProviderRegistry.auto_register_models(session, provider, [])
        assert count == 0


# ─── Health check ───────────────────────────────────────────────────────────


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_updates_provider_status(self, session: AsyncSession) -> None:
        """Health check should update status and latency for enabled providers."""
        await ProviderRegistry.create(
            session, name="p1", provider_type=ProviderType.openai
        )
        await ProviderRegistry.create(
            session, name="p2", provider_type=ProviderType.anthropic
        )

        # Force all checks to succeed
        with patch("api.tasks.provider_health.random") as mock_random:
            mock_random.random.return_value = 0.5  # < 0.95 => success
            mock_random.randint.return_value = 250
            results = await check_all_providers(session)

        assert len(results) == 2
        for r in results:
            assert r["checked"] is True
            assert r["success"] is True
            assert r["status"] == "active"
            assert r["latency_ms"] == 250

    @pytest.mark.asyncio
    async def test_skips_disabled_providers(self, session: AsyncSession) -> None:
        """Health check should skip disabled providers."""
        p = await ProviderRegistry.create(
            session, name="p1", provider_type=ProviderType.openai
        )
        await ProviderRegistry.toggle(session, p)  # disable

        results = await check_all_providers(session)
        assert len(results) == 1
        assert results[0]["checked"] is False
        assert results[0]["reason"] == "disabled"

    @pytest.mark.asyncio
    async def test_handles_failure(self, session: AsyncSession) -> None:
        """Health check failure should set provider status to error."""
        await ProviderRegistry.create(
            session, name="p1", provider_type=ProviderType.openai
        )

        with patch("api.tasks.provider_health.random") as mock_random:
            mock_random.random.return_value = 0.99  # > 0.95 => failure
            mock_random.randint.return_value = 300
            results = await check_all_providers(session)

        assert results[0]["success"] is False
        assert results[0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_empty_providers(self, session: AsyncSession) -> None:
        """Health check with no providers returns empty list."""
        results = await check_all_providers(session)
        assert results == []


# ─── Ollama detect ──────────────────────────────────────────────────────────


class TestOllamaDetect:
    @pytest.mark.asyncio
    async def test_creates_ollama_provider(self, session: AsyncSession) -> None:
        """detect-ollama should create an Ollama provider if none exists."""
        # Simulate what the endpoint does
        existing = await ProviderRegistry.get_by_type(session, ProviderType.ollama)
        assert existing is None

        provider = await ProviderRegistry.create(
            session,
            name="Ollama (local)",
            provider_type=ProviderType.ollama,
            base_url="http://localhost:11434",
        )
        raw_models = await ProviderRegistry.discover_models(session, provider)
        count = await ProviderRegistry.auto_register_models(session, provider, raw_models)

        assert provider.provider_type == ProviderType.ollama
        assert provider.base_url == "http://localhost:11434"
        assert len(raw_models) > 0
        assert count == len(raw_models)

    @pytest.mark.asyncio
    async def test_returns_existing_ollama_provider(self, session: AsyncSession) -> None:
        """detect-ollama should return existing provider without creating duplicate."""
        original = await ProviderRegistry.create(
            session,
            name="Ollama (local)",
            provider_type=ProviderType.ollama,
            base_url="http://localhost:11434",
        )

        existing = await ProviderRegistry.get_by_type(session, ProviderType.ollama)
        assert existing is not None
        assert existing.id == original.id


# ─── Provider status (first-run detection) ──────────────────────────────────


class TestProviderStatus:
    @pytest.mark.asyncio
    async def test_no_providers(self, session: AsyncSession) -> None:
        """Status should report no providers when none exist."""
        providers, total = await ProviderRegistry.list(session)
        assert total == 0

    @pytest.mark.asyncio
    async def test_with_providers(self, session: AsyncSession) -> None:
        """Status should report correct counts when providers exist."""
        await ProviderRegistry.create(
            session, name="p1", provider_type=ProviderType.openai
        )
        await ProviderRegistry.create(
            session, name="p2", provider_type=ProviderType.anthropic
        )

        providers, total = await ProviderRegistry.list(session)
        assert total == 2

    @pytest.mark.asyncio
    async def test_with_models(self, session: AsyncSession) -> None:
        """Status should count models from the models table."""
        await ModelRegistry.register(session, name="gpt-4o", provider="openai")
        await ModelRegistry.register(session, name="claude-sonnet", provider="anthropic")

        from sqlalchemy import func, select

        result = await session.execute(
            select(func.count()).select_from(Model).where(Model.status == "active")
        )
        total_models = result.scalar() or 0
        assert total_models == 2


# ─── get_by_type helper ────────────────────────────────────────────────────


class TestGetByType:
    @pytest.mark.asyncio
    async def test_get_by_type_found(self, session: AsyncSession) -> None:
        await ProviderRegistry.create(
            session, name="p1", provider_type=ProviderType.ollama
        )
        found = await ProviderRegistry.get_by_type(session, ProviderType.ollama)
        assert found is not None
        assert found.provider_type == ProviderType.ollama

    @pytest.mark.asyncio
    async def test_get_by_type_not_found(self, session: AsyncSession) -> None:
        found = await ProviderRegistry.get_by_type(session, ProviderType.ollama)
        assert found is None
