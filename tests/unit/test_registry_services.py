"""Tests for async registry services (AgentRegistry + ToolRegistry) using async SQLite."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Agent, Base, Tool
from api.models.enums import AgentStatus
from engine.config_parser import AgentConfig, FrameworkType
from registry.agents import AgentRegistry
from registry.tools import ToolRegistry

# Async SQLite engine for testing
_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
async def session():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionFactory() as s:
        yield s
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _config(**overrides) -> AgentConfig:
    defaults = {
        "name": "test-agent",
        "version": "1.0.0",
        "team": "engineering",
        "owner": "test@example.com",
        "framework": FrameworkType.langgraph,
        "model": {"primary": "gpt-4o"},
        "deploy": {"cloud": "local"},
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


# ─── AgentRegistry ────────────────────────────────────────────────────────────


class TestAgentRegistryRegister:
    @pytest.mark.asyncio
    async def test_register_new_agent(self, session: AsyncSession) -> None:
        agent = await AgentRegistry.register(session, _config(), "http://localhost:8080")
        assert agent.name == "test-agent"
        assert agent.version == "1.0.0"
        assert agent.status == AgentStatus.running

    @pytest.mark.asyncio
    async def test_register_updates_existing(self, session: AsyncSession) -> None:
        await AgentRegistry.register(session, _config(version="1.0.0"), "http://a:8080")
        agent = await AgentRegistry.register(
            session, _config(version="2.0.0"), "http://b:9090"
        )
        assert agent.version == "2.0.0"
        assert agent.endpoint_url == "http://b:9090"


class TestAgentRegistryGet:
    @pytest.mark.asyncio
    async def test_get_by_name(self, session: AsyncSession) -> None:
        await AgentRegistry.register(session, _config(), "http://a:8080")
        agent = await AgentRegistry.get(session, "test-agent")
        assert agent is not None
        assert agent.name == "test-agent"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, session: AsyncSession) -> None:
        result = await AgentRegistry.get(session, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id(self, session: AsyncSession) -> None:
        agent = await AgentRegistry.register(session, _config(), "http://a:8080")
        found = await AgentRegistry.get_by_id(session, agent.id)
        assert found is not None
        assert found.name == "test-agent"

    @pytest.mark.asyncio
    async def test_get_by_id_missing(self, session: AsyncSession) -> None:
        result = await AgentRegistry.get_by_id(session, uuid.uuid4())
        assert result is None


class TestAgentRegistryList:
    @pytest.mark.asyncio
    async def test_list_all(self, session: AsyncSession) -> None:
        for i in range(3):
            await AgentRegistry.register(
                session, _config(name=f"agent-{i}"), f"http://a:{8080 + i}"
            )
        agents, total = await AgentRegistry.list(session)
        assert total == 3
        assert len(agents) == 3

    @pytest.mark.asyncio
    async def test_list_filter_by_team(self, session: AsyncSession) -> None:
        await AgentRegistry.register(session, _config(name="a1", team="alpha"), "http://a")
        await AgentRegistry.register(session, _config(name="a2", team="beta"), "http://b")
        agents, total = await AgentRegistry.list(session, team="alpha")
        assert total == 1
        assert agents[0].name == "a1"

    @pytest.mark.asyncio
    async def test_list_filter_by_framework(self, session: AsyncSession) -> None:
        await AgentRegistry.register(
            session, _config(name="a1", framework=FrameworkType.langgraph), "http://a"
        )
        await AgentRegistry.register(
            session, _config(name="a2", framework=FrameworkType.crewai), "http://b"
        )
        agents, total = await AgentRegistry.list(session, framework="crewai")
        assert total == 1
        assert agents[0].name == "a2"

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, session: AsyncSession) -> None:
        agent = await AgentRegistry.register(session, _config(name="a1"), "http://a")
        agent.status = AgentStatus.stopped
        await session.flush()
        await AgentRegistry.register(session, _config(name="a2"), "http://b")

        agents, total = await AgentRegistry.list(session, status=AgentStatus.running)
        assert total == 1
        assert agents[0].name == "a2"

    @pytest.mark.asyncio
    async def test_list_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await AgentRegistry.register(
                session, _config(name=f"agent-{i}"), f"http://a:{i}"
            )
        agents, total = await AgentRegistry.list(session, page=2, per_page=2)
        assert total == 5
        assert len(agents) == 2


class TestAgentRegistryUpdateStatus:
    @pytest.mark.asyncio
    async def test_update_status(self, session: AsyncSession) -> None:
        agent = await AgentRegistry.register(session, _config(), "http://a:8080")
        await AgentRegistry.update_status(session, agent.id, AgentStatus.stopped)
        updated = await AgentRegistry.get_by_id(session, agent.id)
        assert updated.status == AgentStatus.stopped

    @pytest.mark.asyncio
    async def test_update_status_missing_agent(self, session: AsyncSession) -> None:
        # Should not raise — just silently no-op
        await AgentRegistry.update_status(session, uuid.uuid4(), AgentStatus.stopped)


class TestAgentRegistrySearch:
    @pytest.mark.asyncio
    async def test_search_by_name(self, session: AsyncSession) -> None:
        await AgentRegistry.register(session, _config(name="customer-bot"), "http://a")
        await AgentRegistry.register(session, _config(name="data-agent"), "http://b")
        agents, total = await AgentRegistry.search(session, "customer")
        assert total == 1
        assert agents[0].name == "customer-bot"

    @pytest.mark.asyncio
    async def test_search_by_description(self, session: AsyncSession) -> None:
        await AgentRegistry.register(
            session, _config(name="a1", description="Handles ETL pipelines"), "http://a"
        )
        agents, total = await AgentRegistry.search(session, "ETL")
        assert total == 1
        assert agents[0].name == "a1"

    @pytest.mark.asyncio
    async def test_search_by_team(self, session: AsyncSession) -> None:
        await AgentRegistry.register(session, _config(name="a1", team="platform"), "http://a")
        agents, total = await AgentRegistry.search(session, "platform")
        assert total == 1

    @pytest.mark.asyncio
    async def test_search_by_framework(self, session: AsyncSession) -> None:
        await AgentRegistry.register(
            session, _config(name="a1", framework=FrameworkType.crewai), "http://a"
        )
        agents, total = await AgentRegistry.search(session, "crewai")
        assert total == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self, session: AsyncSession) -> None:
        agents, total = await AgentRegistry.search(session, "nonexistent")
        assert total == 0
        assert agents == []

    @pytest.mark.asyncio
    async def test_search_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await AgentRegistry.register(
                session, _config(name=f"bot-{i}"), f"http://a:{i}"
            )
        agents, total = await AgentRegistry.search(session, "bot", page=2, per_page=2)
        assert total == 5
        assert len(agents) == 2


class TestAgentRegistryDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self, session: AsyncSession) -> None:
        await AgentRegistry.register(session, _config(), "http://a:8080")
        result = await AgentRegistry.delete(session, "test-agent")
        assert result is True
        agent = await AgentRegistry.get(session, "test-agent")
        assert agent.status == AgentStatus.stopped

    @pytest.mark.asyncio
    async def test_delete_missing(self, session: AsyncSession) -> None:
        result = await AgentRegistry.delete(session, "nonexistent")
        assert result is False


# ─── ToolRegistry ─────────────────────────────────────────────────────────────


class TestToolRegistryRegister:
    @pytest.mark.asyncio
    async def test_register_new_tool(self, session: AsyncSession) -> None:
        tool = await ToolRegistry.register(
            session, name="zendesk-mcp", description="Zendesk", endpoint="http://z:3000"
        )
        assert tool.name == "zendesk-mcp"
        assert tool.tool_type == "mcp_server"
        assert tool.status == "active"

    @pytest.mark.asyncio
    async def test_register_updates_existing(self, session: AsyncSession) -> None:
        await ToolRegistry.register(session, name="tool-a", description="v1")
        tool = await ToolRegistry.register(
            session, name="tool-a", description="v2", endpoint="http://new"
        )
        assert tool.description == "v2"
        assert tool.endpoint == "http://new"

    @pytest.mark.asyncio
    async def test_register_with_schema(self, session: AsyncSession) -> None:
        schema = {"type": "object", "properties": {"query": {"type": "string"}}}
        tool = await ToolRegistry.register(
            session, name="search", tool_type="function", schema_definition=schema
        )
        assert tool.schema_definition == schema


class TestToolRegistryList:
    @pytest.mark.asyncio
    async def test_list_all(self, session: AsyncSession) -> None:
        for name in ["a", "b", "c"]:
            await ToolRegistry.register(session, name=name)
        tools, total = await ToolRegistry.list(session)
        assert total == 3

    @pytest.mark.asyncio
    async def test_list_filter_by_type(self, session: AsyncSession) -> None:
        await ToolRegistry.register(session, name="t1", tool_type="mcp_server")
        await ToolRegistry.register(session, name="t2", tool_type="function")
        tools, total = await ToolRegistry.list(session, tool_type="function")
        assert total == 1
        assert tools[0].name == "t2"

    @pytest.mark.asyncio
    async def test_list_filter_by_source(self, session: AsyncSession) -> None:
        await ToolRegistry.register(session, name="t1", source="scanner")
        await ToolRegistry.register(session, name="t2", source="manual")
        tools, total = await ToolRegistry.list(session, source="scanner")
        assert total == 1
        assert tools[0].name == "t1"

    @pytest.mark.asyncio
    async def test_list_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await ToolRegistry.register(session, name=f"tool-{i}")
        tools, total = await ToolRegistry.list(session, page=2, per_page=2)
        assert total == 5
        assert len(tools) == 2


class TestToolRegistryGet:
    @pytest.mark.asyncio
    async def test_get_existing(self, session: AsyncSession) -> None:
        await ToolRegistry.register(session, name="slack-mcp")
        tool = await ToolRegistry.get(session, "slack-mcp")
        assert tool is not None
        assert tool.name == "slack-mcp"

    @pytest.mark.asyncio
    async def test_get_missing(self, session: AsyncSession) -> None:
        result = await ToolRegistry.get(session, "nonexistent")
        assert result is None


class TestToolRegistrySearch:
    @pytest.mark.asyncio
    async def test_search_by_name(self, session: AsyncSession) -> None:
        await ToolRegistry.register(session, name="zendesk-mcp", description="Zendesk API")
        await ToolRegistry.register(session, name="slack-mcp", description="Slack API")
        tools, total = await ToolRegistry.search(session, "zendesk")
        assert total == 1
        assert tools[0].name == "zendesk-mcp"

    @pytest.mark.asyncio
    async def test_search_by_description(self, session: AsyncSession) -> None:
        await ToolRegistry.register(session, name="t1", description="Customer ticketing")
        tools, total = await ToolRegistry.search(session, "ticketing")
        assert total == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self, session: AsyncSession) -> None:
        tools, total = await ToolRegistry.search(session, "nonexistent")
        assert total == 0

    @pytest.mark.asyncio
    async def test_search_excludes_inactive(self, session: AsyncSession) -> None:
        tool = await ToolRegistry.register(session, name="old-tool")
        tool.status = "deprecated"
        await session.flush()
        tools, total = await ToolRegistry.search(session, "old-tool")
        assert total == 0

    @pytest.mark.asyncio
    async def test_search_pagination(self, session: AsyncSession) -> None:
        for i in range(5):
            await ToolRegistry.register(session, name=f"mcp-{i}", description="MCP server")
        tools, total = await ToolRegistry.search(session, "mcp", page=2, per_page=2)
        assert total == 5
        assert len(tools) == 2
