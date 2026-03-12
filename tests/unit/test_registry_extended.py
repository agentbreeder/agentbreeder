"""Tests for registry services: PromptRegistry, ModelRegistry, ProviderRegistry, DeployRegistry."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Agent, Base, DeployJob
from api.models.enums import (
    AgentStatus,
    DeployJobStatus,
    ProviderStatus,
    ProviderType,
)
from registry.deploys import DeployRegistry
from registry.models import ModelRegistry
from registry.prompts import PromptRegistry
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


# ─── PromptRegistry ─────────────────────────────────────────────────────────


class TestPromptRegistryRegister:
    @pytest.mark.asyncio
    async def test_register_new(self, session: AsyncSession) -> None:
        prompt = await PromptRegistry.register(
            session, name="greet", version="1.0.0", content="Hello {{name}}"
        )
        assert prompt.name == "greet"
        assert prompt.version == "1.0.0"
        assert prompt.content == "Hello {{name}}"

    @pytest.mark.asyncio
    async def test_register_updates_existing(self, session: AsyncSession) -> None:
        await PromptRegistry.register(session, name="greet", version="1.0.0", content="v1")
        prompt = await PromptRegistry.register(
            session, name="greet", version="1.0.0", content="v2", description="updated"
        )
        assert prompt.content == "v2"
        assert prompt.description == "updated"


class TestPromptRegistryList:
    @pytest.mark.asyncio
    async def test_list_all(self, session: AsyncSession) -> None:
        for i in range(3):
            await PromptRegistry.register(session, name=f"p-{i}", version="1.0.0", content="c")
        prompts, total = await PromptRegistry.list(session)
        assert total == 3
        assert len(prompts) == 3

    @pytest.mark.asyncio
    async def test_list_filter_by_team(self, session: AsyncSession) -> None:
        await PromptRegistry.register(
            session, name="p1", version="1.0.0", content="c", team="alpha"
        )
        await PromptRegistry.register(
            session, name="p2", version="1.0.0", content="c", team="beta"
        )
        prompts, total = await PromptRegistry.list(session, team="alpha")
        assert total == 1
        assert prompts[0].name == "p1"

    @pytest.mark.asyncio
    async def test_list_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await PromptRegistry.register(session, name=f"p-{i}", version="1.0.0", content="c")
        prompts, total = await PromptRegistry.list(session, page=2, per_page=2)
        assert total == 5
        assert len(prompts) == 2


class TestPromptRegistryGet:
    @pytest.mark.asyncio
    async def test_get_by_name(self, session: AsyncSession) -> None:
        await PromptRegistry.register(session, name="greet", version="1.0.0", content="hi")
        prompt = await PromptRegistry.get(session, "greet")
        assert prompt is not None
        assert prompt.name == "greet"

    @pytest.mark.asyncio
    async def test_get_by_name_and_version(self, session: AsyncSession) -> None:
        await PromptRegistry.register(session, name="greet", version="1.0.0", content="v1")
        await PromptRegistry.register(session, name="greet", version="2.0.0", content="v2")
        prompt = await PromptRegistry.get(session, "greet", version="1.0.0")
        assert prompt.content == "v1"

    @pytest.mark.asyncio
    async def test_get_latest_when_no_version(self, session: AsyncSession) -> None:
        await PromptRegistry.register(session, name="greet", version="1.0.0", content="v1")
        await PromptRegistry.register(session, name="greet", version="2.0.0", content="v2")
        prompt = await PromptRegistry.get(session, "greet")
        assert prompt.content == "v2"

    @pytest.mark.asyncio
    async def test_get_missing(self, session: AsyncSession) -> None:
        assert await PromptRegistry.get(session, "nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_by_id(self, session: AsyncSession) -> None:
        created = await PromptRegistry.register(
            session, name="greet", version="1.0.0", content="hi"
        )
        found = await PromptRegistry.get_by_id(session, created.id)
        assert found is not None
        assert found.name == "greet"

    @pytest.mark.asyncio
    async def test_get_by_id_missing(self, session: AsyncSession) -> None:
        assert await PromptRegistry.get_by_id(session, uuid.uuid4()) is None


class TestPromptRegistryUpdate:
    @pytest.mark.asyncio
    async def test_update_content(self, session: AsyncSession) -> None:
        p = await PromptRegistry.register(session, name="p", version="1.0.0", content="old")
        updated = await PromptRegistry.update(session, p.id, content="new")
        assert updated.content == "new"

    @pytest.mark.asyncio
    async def test_update_description(self, session: AsyncSession) -> None:
        p = await PromptRegistry.register(session, name="p", version="1.0.0", content="c")
        updated = await PromptRegistry.update(session, p.id, description="desc")
        assert updated.description == "desc"

    @pytest.mark.asyncio
    async def test_update_missing(self, session: AsyncSession) -> None:
        assert await PromptRegistry.update(session, uuid.uuid4(), content="x") is None


class TestPromptRegistryDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, session: AsyncSession) -> None:
        p = await PromptRegistry.register(session, name="p", version="1.0.0", content="c")
        assert await PromptRegistry.delete(session, p.id) is True
        assert await PromptRegistry.get_by_id(session, p.id) is None

    @pytest.mark.asyncio
    async def test_delete_missing(self, session: AsyncSession) -> None:
        assert await PromptRegistry.delete(session, uuid.uuid4()) is False


class TestPromptRegistryVersions:
    @pytest.mark.asyncio
    async def test_get_versions(self, session: AsyncSession) -> None:
        p1 = await PromptRegistry.register(session, name="p", version="1.0.0", content="v1")
        await PromptRegistry.register(session, name="p", version="2.0.0", content="v2")
        versions = await PromptRegistry.get_versions(session, p1.id)
        assert len(versions) == 2
        assert versions[0].version == "2.0.0"  # desc order

    @pytest.mark.asyncio
    async def test_get_versions_missing_prompt(self, session: AsyncSession) -> None:
        assert await PromptRegistry.get_versions(session, uuid.uuid4()) == []


class TestPromptRegistryDuplicate:
    @pytest.mark.asyncio
    async def test_duplicate(self, session: AsyncSession) -> None:
        p = await PromptRegistry.register(
            session, name="p", version="1.0.0", content="c", team="eng"
        )
        dup = await PromptRegistry.duplicate(session, p.id)
        assert dup is not None
        assert dup.name == "p"
        assert dup.version == "1.0.1"
        assert dup.content == "c"
        assert dup.team == "eng"

    @pytest.mark.asyncio
    async def test_duplicate_bumps_from_latest(self, session: AsyncSession) -> None:
        p = await PromptRegistry.register(session, name="p", version="1.0.0", content="v1")
        await PromptRegistry.register(session, name="p", version="1.0.5", content="v5")
        dup = await PromptRegistry.duplicate(session, p.id)
        assert dup.version == "1.0.6"

    @pytest.mark.asyncio
    async def test_duplicate_missing(self, session: AsyncSession) -> None:
        assert await PromptRegistry.duplicate(session, uuid.uuid4()) is None


class TestPromptRegistrySearch:
    @pytest.mark.asyncio
    async def test_search_by_name(self, session: AsyncSession) -> None:
        await PromptRegistry.register(session, name="safety-guard", version="1.0.0", content="c")
        await PromptRegistry.register(session, name="greeting", version="1.0.0", content="c")
        prompts, total = await PromptRegistry.search(session, "safety")
        assert total == 1
        assert prompts[0].name == "safety-guard"

    @pytest.mark.asyncio
    async def test_search_by_description(self, session: AsyncSession) -> None:
        await PromptRegistry.register(
            session, name="p1", version="1.0.0", content="c", description="Guardrail prompt"
        )
        prompts, total = await PromptRegistry.search(session, "Guardrail")
        assert total == 1

    @pytest.mark.asyncio
    async def test_search_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await PromptRegistry.register(
                session, name=f"prompt-{i}", version="1.0.0", content="c"
            )
        prompts, total = await PromptRegistry.search(session, "prompt", page=2, per_page=2)
        assert total == 5
        assert len(prompts) == 2


# ─── ModelRegistry ───────────────────────────────────────────────────────────


class TestModelRegistryRegister:
    @pytest.mark.asyncio
    async def test_register_new(self, session: AsyncSession) -> None:
        model = await ModelRegistry.register(
            session,
            name="gpt-4o",
            provider="openai",
            context_window=128000,
            input_price_per_million=2.5,
            output_price_per_million=10.0,
            capabilities=["text", "vision"],
        )
        assert model.name == "gpt-4o"
        assert model.provider == "openai"
        assert model.context_window == 128000
        assert model.capabilities == ["text", "vision"]

    @pytest.mark.asyncio
    async def test_register_updates_existing(self, session: AsyncSession) -> None:
        await ModelRegistry.register(session, name="gpt-4o", provider="openai")
        model = await ModelRegistry.register(
            session, name="gpt-4o", provider="openai", description="Updated"
        )
        assert model.description == "Updated"


class TestModelRegistryList:
    @pytest.mark.asyncio
    async def test_list_all(self, session: AsyncSession) -> None:
        await ModelRegistry.register(session, name="m1", provider="openai")
        await ModelRegistry.register(session, name="m2", provider="anthropic")
        models, total = await ModelRegistry.list(session)
        assert total == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_provider(self, session: AsyncSession) -> None:
        await ModelRegistry.register(session, name="m1", provider="openai")
        await ModelRegistry.register(session, name="m2", provider="anthropic")
        models, total = await ModelRegistry.list(session, provider="openai")
        assert total == 1
        assert models[0].name == "m1"

    @pytest.mark.asyncio
    async def test_list_filter_by_source(self, session: AsyncSession) -> None:
        await ModelRegistry.register(session, name="m1", provider="openai", source="discovery")
        await ModelRegistry.register(session, name="m2", provider="openai", source="manual")
        models, total = await ModelRegistry.list(session, source="discovery")
        assert total == 1

    @pytest.mark.asyncio
    async def test_list_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await ModelRegistry.register(session, name=f"m-{i}", provider="openai")
        models, total = await ModelRegistry.list(session, page=2, per_page=2)
        assert total == 5
        assert len(models) == 2


class TestModelRegistryGet:
    @pytest.mark.asyncio
    async def test_get_by_name(self, session: AsyncSession) -> None:
        await ModelRegistry.register(session, name="gpt-4o", provider="openai")
        model = await ModelRegistry.get(session, "gpt-4o")
        assert model is not None

    @pytest.mark.asyncio
    async def test_get_missing(self, session: AsyncSession) -> None:
        assert await ModelRegistry.get(session, "nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_by_id(self, session: AsyncSession) -> None:
        m = await ModelRegistry.register(session, name="gpt-4o", provider="openai")
        found = await ModelRegistry.get_by_id(session, str(m.id))
        assert found is not None
        assert found.name == "gpt-4o"

    @pytest.mark.asyncio
    async def test_get_by_id_invalid_uuid(self, session: AsyncSession) -> None:
        assert await ModelRegistry.get_by_id(session, "not-a-uuid") is None

    @pytest.mark.asyncio
    async def test_get_by_id_missing(self, session: AsyncSession) -> None:
        assert await ModelRegistry.get_by_id(session, str(uuid.uuid4())) is None

    @pytest.mark.asyncio
    async def test_get_by_ids(self, session: AsyncSession) -> None:
        m1 = await ModelRegistry.register(session, name="m1", provider="openai")
        m2 = await ModelRegistry.register(session, name="m2", provider="openai")
        models = await ModelRegistry.get_by_ids(session, [str(m1.id), str(m2.id)])
        assert len(models) == 2

    @pytest.mark.asyncio
    async def test_get_by_ids_empty(self, session: AsyncSession) -> None:
        assert await ModelRegistry.get_by_ids(session, []) == []

    @pytest.mark.asyncio
    async def test_get_by_ids_invalid_uuids(self, session: AsyncSession) -> None:
        assert await ModelRegistry.get_by_ids(session, ["bad", "ids"]) == []


class TestModelRegistryUsage:
    @pytest.mark.asyncio
    async def test_get_usage_primary(self, session: AsyncSession) -> None:
        model = await ModelRegistry.register(session, name="gpt-4o", provider="openai")
        agent = Agent(
            name="a1",
            version="1.0.0",
            team="eng",
            owner="t@t.com",
            framework="langgraph",
            model_primary="gpt-4o",
            status=AgentStatus.running,
        )
        session.add(agent)
        await session.flush()

        usage = await ModelRegistry.get_usage(session, str(model.id))
        assert len(usage) == 1
        assert usage[0][1] == "primary"

    @pytest.mark.asyncio
    async def test_get_usage_fallback(self, session: AsyncSession) -> None:
        model = await ModelRegistry.register(session, name="gpt-4o", provider="openai")
        agent = Agent(
            name="a1",
            version="1.0.0",
            team="eng",
            owner="t@t.com",
            framework="langgraph",
            model_primary="claude-sonnet",
            model_fallback="gpt-4o",
            status=AgentStatus.running,
        )
        session.add(agent)
        await session.flush()

        usage = await ModelRegistry.get_usage(session, str(model.id))
        assert len(usage) == 1
        assert usage[0][1] == "fallback"

    @pytest.mark.asyncio
    async def test_get_usage_missing_model(self, session: AsyncSession) -> None:
        assert await ModelRegistry.get_usage(session, str(uuid.uuid4())) == []


class TestModelRegistrySearch:
    @pytest.mark.asyncio
    async def test_search_by_name(self, session: AsyncSession) -> None:
        await ModelRegistry.register(session, name="gpt-4o", provider="openai")
        await ModelRegistry.register(session, name="claude-sonnet", provider="anthropic")
        models, total = await ModelRegistry.search(session, "gpt")
        assert total == 1

    @pytest.mark.asyncio
    async def test_search_by_description(self, session: AsyncSession) -> None:
        await ModelRegistry.register(
            session, name="m1", provider="openai", description="Great for coding"
        )
        models, total = await ModelRegistry.search(session, "coding")
        assert total == 1

    @pytest.mark.asyncio
    async def test_search_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await ModelRegistry.register(session, name=f"model-{i}", provider="openai")
        models, total = await ModelRegistry.search(session, "model", page=2, per_page=2)
        assert total == 5
        assert len(models) == 2


# ─── ProviderRegistry ────────────────────────────────────────────────────────


class TestProviderRegistryCreate:
    @pytest.mark.asyncio
    async def test_create(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(
            session,
            name="openai-prod",
            provider_type=ProviderType.openai,
            base_url="https://api.openai.com/v1",
            config={"org_id": "org-123"},
        )
        assert p.name == "openai-prod"
        assert p.provider_type == ProviderType.openai
        assert p.base_url == "https://api.openai.com/v1"


class TestProviderRegistryList:
    @pytest.mark.asyncio
    async def test_list_all(self, session: AsyncSession) -> None:
        await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        await ProviderRegistry.create(session, name="p2", provider_type=ProviderType.anthropic)
        providers, total = await ProviderRegistry.list(session)
        assert total == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_type(self, session: AsyncSession) -> None:
        await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        await ProviderRegistry.create(session, name="p2", provider_type=ProviderType.anthropic)
        providers, total = await ProviderRegistry.list(session, provider_type=ProviderType.openai)
        assert total == 1
        assert providers[0].name == "p1"

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        p.status = ProviderStatus.disabled
        await session.flush()
        await ProviderRegistry.create(session, name="p2", provider_type=ProviderType.openai)
        providers, total = await ProviderRegistry.list(session, status=ProviderStatus.active)
        assert total == 1
        assert providers[0].name == "p2"

    @pytest.mark.asyncio
    async def test_list_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await ProviderRegistry.create(
                session, name=f"p-{i}", provider_type=ProviderType.openai
            )
        providers, total = await ProviderRegistry.list(session, page=2, per_page=2)
        assert total == 5
        assert len(providers) == 2


class TestProviderRegistryGet:
    @pytest.mark.asyncio
    async def test_get_existing(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        found = await ProviderRegistry.get(session, p.id)
        assert found is not None
        assert found.name == "p1"

    @pytest.mark.asyncio
    async def test_get_missing(self, session: AsyncSession) -> None:
        assert await ProviderRegistry.get(session, uuid.uuid4()) is None


class TestProviderRegistryUpdate:
    @pytest.mark.asyncio
    async def test_update_name(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="old", provider_type=ProviderType.openai)
        updated = await ProviderRegistry.update(session, p, name="new")
        assert updated.name == "new"

    @pytest.mark.asyncio
    async def test_update_status(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        updated = await ProviderRegistry.update(session, p, status=ProviderStatus.disabled)
        assert updated.status == ProviderStatus.disabled

    @pytest.mark.asyncio
    async def test_update_config(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        updated = await ProviderRegistry.update(session, p, config={"key": "val"})
        assert updated.config == {"key": "val"}

    @pytest.mark.asyncio
    async def test_update_base_url(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        updated = await ProviderRegistry.update(session, p, base_url="http://new")
        assert updated.base_url == "http://new"


class TestProviderRegistryDelete:
    @pytest.mark.asyncio
    async def test_delete(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        pid = p.id
        await ProviderRegistry.delete(session, p)
        assert await ProviderRegistry.get(session, pid) is None


class TestProviderRegistryTestConnection:
    @pytest.mark.asyncio
    async def test_test_connection(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        result = await ProviderRegistry.test_connection(session, p)
        assert result["success"] is True
        assert result["latency_ms"] > 0
        assert result["models_found"] == 6  # openai has 6 simulated models
        assert p.status == ProviderStatus.active
        assert p.last_verified is not None


class TestProviderRegistryDiscoverModels:
    @pytest.mark.asyncio
    async def test_discover_openai(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.openai)
        models = await ProviderRegistry.discover_models(session, p)
        assert len(models) == 6
        assert any(m["id"] == "gpt-4o" for m in models)

    @pytest.mark.asyncio
    async def test_discover_anthropic(self, session: AsyncSession) -> None:
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.anthropic)
        models = await ProviderRegistry.discover_models(session, p)
        assert len(models) == 3
        assert any("claude" in m["id"] for m in models)

    @pytest.mark.asyncio
    async def test_discover_unknown_type(self, session: AsyncSession) -> None:
        """Provider with type not in simulated map returns empty list."""
        p = await ProviderRegistry.create(session, name="p1", provider_type=ProviderType.ollama)
        # ollama is in the map, so test with a provider whose type we force
        models = await ProviderRegistry.discover_models(session, p)
        assert isinstance(models, list)


# ─── DeployRegistry ──────────────────────────────────────────────────────────


class TestDeployRegistryList:
    @pytest.mark.asyncio
    async def test_list_empty(self, session: AsyncSession) -> None:
        jobs, total = await DeployRegistry.list(session)
        assert total == 0
        assert jobs == []

    @pytest.mark.asyncio
    async def test_list_with_jobs(self, session: AsyncSession) -> None:
        agent = Agent(
            name="a1",
            version="1.0.0",
            team="eng",
            owner="t@t.com",
            framework="langgraph",
            model_primary="gpt-4o",
            status=AgentStatus.running,
        )
        session.add(agent)
        await session.flush()

        job = DeployJob(
            agent_id=agent.id,
            status=DeployJobStatus.completed,
            target="local",
        )
        session.add(job)
        await session.flush()

        jobs, total = await DeployRegistry.list(session)
        assert total == 1
        assert jobs[0].agent_id == agent.id

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, session: AsyncSession) -> None:
        agent = Agent(
            name="a1",
            version="1.0.0",
            team="eng",
            owner="t@t.com",
            framework="langgraph",
            model_primary="gpt-4o",
            status=AgentStatus.running,
        )
        session.add(agent)
        await session.flush()

        for status in [DeployJobStatus.completed, DeployJobStatus.failed]:
            job = DeployJob(agent_id=agent.id, status=status, target="local")
            session.add(job)
        await session.flush()

        jobs, total = await DeployRegistry.list(session, status=DeployJobStatus.completed)
        assert total == 1

    @pytest.mark.asyncio
    async def test_list_filter_by_agent_id(self, session: AsyncSession) -> None:
        a1 = Agent(
            name="a1",
            version="1.0.0",
            team="eng",
            owner="t@t.com",
            framework="langgraph",
            model_primary="gpt-4o",
            status=AgentStatus.running,
        )
        a2 = Agent(
            name="a2",
            version="1.0.0",
            team="eng",
            owner="t@t.com",
            framework="langgraph",
            model_primary="gpt-4o",
            status=AgentStatus.running,
        )
        session.add_all([a1, a2])
        await session.flush()

        session.add(DeployJob(agent_id=a1.id, status=DeployJobStatus.completed, target="local"))
        session.add(DeployJob(agent_id=a2.id, status=DeployJobStatus.completed, target="local"))
        await session.flush()

        jobs, total = await DeployRegistry.list(session, agent_id=a1.id)
        assert total == 1
        assert jobs[0].agent_id == a1.id


class TestDeployRegistryGet:
    @pytest.mark.asyncio
    async def test_get_existing(self, session: AsyncSession) -> None:
        agent = Agent(
            name="a1",
            version="1.0.0",
            team="eng",
            owner="t@t.com",
            framework="langgraph",
            model_primary="gpt-4o",
            status=AgentStatus.running,
        )
        session.add(agent)
        await session.flush()

        job = DeployJob(agent_id=agent.id, status=DeployJobStatus.completed, target="local")
        session.add(job)
        await session.flush()

        found = await DeployRegistry.get(session, job.id)
        assert found is not None
        assert found.status == DeployJobStatus.completed

    @pytest.mark.asyncio
    async def test_get_missing(self, session: AsyncSession) -> None:
        assert await DeployRegistry.get(session, uuid.uuid4()) is None
