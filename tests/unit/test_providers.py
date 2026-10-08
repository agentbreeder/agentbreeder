"""Tests for provider backend features: provider lookup and status."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Base, Model
from api.models.enums import ProviderType
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


# ─── Ollama provider lookup ──────────────────────────────────────────────────────────


class TestOllamaProviderLookup:
    @pytest.mark.asyncio
    async def test_returns_existing_ollama_provider(self, session: AsyncSession) -> None:
        """get_by_type returns the existing Ollama provider."""
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
        await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        await ProviderRegistry.create(session, name="p2", provider_type=ProviderType.anthropic)

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
        await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.ollama)
        found = await ProviderRegistry.get_by_type(session, ProviderType.ollama)
        assert found is not None
        assert found.provider_type == ProviderType.ollama

    @pytest.mark.asyncio
    async def test_get_by_type_not_found(self, session: AsyncSession) -> None:
        found = await ProviderRegistry.get_by_type(session, ProviderType.ollama)
        assert found is None
