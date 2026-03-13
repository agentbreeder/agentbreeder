"""Tests for registry/a2a_agents.py and api/routes/a2a.py."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api.models.database import Base
from registry.a2a_agents import A2AAgentRegistry


@pytest_asyncio.fixture
async def async_session():
    """Create an async in-memory SQLite session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    # Enable foreign keys for SQLite
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# A2A Registry tests
# ---------------------------------------------------------------------------


class TestA2AAgentRegistry:
    """Test A2A agent CRUD operations."""

    @pytest.mark.asyncio
    async def test_create(self, async_session):
        agent = await A2AAgentRegistry.create(
            async_session,
            name="test-agent",
            endpoint_url="http://localhost:9000",
            team="eng",
        )
        assert agent.name == "test-agent"
        assert agent.endpoint_url == "http://localhost:9000"
        assert agent.team == "eng"
        assert agent.id is not None

    @pytest.mark.asyncio
    async def test_create_with_agent_card(self, async_session):
        card = {"name": "agent", "version": "1.0.0"}
        agent = await A2AAgentRegistry.create(
            async_session,
            name="card-agent",
            endpoint_url="http://localhost:9001",
            agent_card=card,
            capabilities=["streaming"],
            auth_scheme="bearer",
        )
        assert agent.agent_card == card
        assert agent.capabilities == ["streaming"]
        assert agent.auth_scheme == "bearer"

    @pytest.mark.asyncio
    async def test_list_empty(self, async_session):
        agents, total = await A2AAgentRegistry.list(async_session)
        assert agents == []
        assert total == 0

    @pytest.mark.asyncio
    async def test_list_with_agents(self, async_session):
        await A2AAgentRegistry.create(async_session, name="a1", endpoint_url="http://a1")
        await A2AAgentRegistry.create(async_session, name="a2", endpoint_url="http://a2")

        agents, total = await A2AAgentRegistry.list(async_session)
        assert total == 2
        assert len(agents) == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_team(self, async_session):
        await A2AAgentRegistry.create(
            async_session, name="eng1", endpoint_url="http://e1", team="eng"
        )
        await A2AAgentRegistry.create(
            async_session, name="ops1", endpoint_url="http://o1", team="ops"
        )

        agents, total = await A2AAgentRegistry.list(async_session, team="eng")
        assert total == 1
        assert agents[0].name == "eng1"

    @pytest.mark.asyncio
    async def test_list_pagination(self, async_session):
        for i in range(5):
            await A2AAgentRegistry.create(
                async_session, name=f"agent-{i}", endpoint_url=f"http://a{i}"
            )

        agents, total = await A2AAgentRegistry.list(async_session, page=1, per_page=2)
        assert total == 5
        assert len(agents) == 2

    @pytest.mark.asyncio
    async def test_get_by_id(self, async_session):
        agent = await A2AAgentRegistry.create(
            async_session, name="find-me", endpoint_url="http://fm"
        )
        found = await A2AAgentRegistry.get_by_id(async_session, str(agent.id))
        assert found is not None
        assert found.name == "find-me"

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, async_session):
        found = await A2AAgentRegistry.get_by_id(async_session, str(uuid.uuid4()))
        assert found is None

    @pytest.mark.asyncio
    async def test_get_by_id_invalid_uuid(self, async_session):
        found = await A2AAgentRegistry.get_by_id(async_session, "not-a-uuid")
        assert found is None

    @pytest.mark.asyncio
    async def test_get_by_name(self, async_session):
        await A2AAgentRegistry.create(async_session, name="named-agent", endpoint_url="http://na")
        found = await A2AAgentRegistry.get_by_name(async_session, "named-agent")
        assert found is not None
        assert found.name == "named-agent"

    @pytest.mark.asyncio
    async def test_get_by_name_not_found(self, async_session):
        found = await A2AAgentRegistry.get_by_name(async_session, "no-such-agent")
        assert found is None

    @pytest.mark.asyncio
    async def test_update(self, async_session):
        agent = await A2AAgentRegistry.create(
            async_session, name="update-me", endpoint_url="http://old"
        )
        updated = await A2AAgentRegistry.update(
            async_session,
            str(agent.id),
            endpoint_url="http://new",
            auth_scheme="bearer",
        )
        assert updated is not None
        assert updated.endpoint_url == "http://new"
        assert updated.auth_scheme == "bearer"

    @pytest.mark.asyncio
    async def test_update_not_found(self, async_session):
        result = await A2AAgentRegistry.update(
            async_session, str(uuid.uuid4()), endpoint_url="http://x"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, async_session):
        agent = await A2AAgentRegistry.create(
            async_session, name="delete-me", endpoint_url="http://dm"
        )
        deleted = await A2AAgentRegistry.delete(async_session, str(agent.id))
        assert deleted is True

        found = await A2AAgentRegistry.get_by_id(async_session, str(agent.id))
        assert found is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, async_session):
        deleted = await A2AAgentRegistry.delete(async_session, str(uuid.uuid4()))
        assert deleted is False
