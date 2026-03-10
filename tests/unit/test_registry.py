"""Tests for registry service layer (agents + tools) using SQLite in-memory."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from api.models.database import Agent, Base, Tool
from api.models.enums import AgentStatus
from engine.config_parser import AgentConfig, FrameworkType


# Use synchronous SQLite for unit tests (no need for async in unit tests)
engine = create_engine("sqlite:///:memory:")
TestSession = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


def _make_config(**overrides) -> AgentConfig:
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


class TestAgentRegistrySync:
    """Test agent registry using synchronous SQLite for speed."""

    def test_register_new_agent(self) -> None:
        with TestSession() as session:
            config = _make_config()
            agent = Agent(
                name=config.name,
                version=config.version,
                description=config.description,
                team=config.team,
                owner=config.owner,
                framework=config.framework.value,
                model_primary=config.model.primary,
                model_fallback=config.model.fallback,
                endpoint_url="http://localhost:8080",
                status=AgentStatus.running,
                tags=config.tags,
                config_snapshot=config.model_dump(mode="json"),
            )
            session.add(agent)
            session.commit()

            # Verify
            result = session.query(Agent).filter_by(name="test-agent").first()
            assert result is not None
            assert result.name == "test-agent"
            assert result.version == "1.0.0"
            assert result.team == "engineering"
            assert result.framework == "langgraph"
            assert result.model_primary == "gpt-4o"
            assert result.status == AgentStatus.running

    def test_update_existing_agent(self) -> None:
        with TestSession() as session:
            agent = Agent(
                name="test-agent",
                version="1.0.0",
                team="eng",
                owner="a@b.com",
                framework="langgraph",
                model_primary="gpt-4o",
                status=AgentStatus.running,
            )
            session.add(agent)
            session.commit()

            # Update
            agent.version = "2.0.0"
            agent.endpoint_url = "http://localhost:9090"
            session.commit()

            result = session.query(Agent).filter_by(name="test-agent").first()
            assert result.version == "2.0.0"
            assert result.endpoint_url == "http://localhost:9090"

    def test_list_agents_with_filters(self) -> None:
        with TestSession() as session:
            for i, team in enumerate(["alpha", "alpha", "beta"]):
                session.add(Agent(
                    name=f"agent-{i}",
                    version="1.0.0",
                    team=team,
                    owner="a@b.com",
                    framework="langgraph",
                    model_primary="gpt-4o",
                    status=AgentStatus.running,
                ))
            session.commit()

            alpha_agents = session.query(Agent).filter_by(team="alpha").all()
            assert len(alpha_agents) == 2

            beta_agents = session.query(Agent).filter_by(team="beta").all()
            assert len(beta_agents) == 1

    def test_search_agents(self) -> None:
        with TestSession() as session:
            session.add(Agent(
                name="customer-support",
                version="1.0.0",
                description="Handles customer tickets",
                team="support",
                owner="a@b.com",
                framework="langgraph",
                model_primary="gpt-4o",
                status=AgentStatus.running,
            ))
            session.add(Agent(
                name="data-pipeline",
                version="1.0.0",
                description="ETL processing",
                team="data",
                owner="a@b.com",
                framework="crewai",
                model_primary="gpt-4o",
                status=AgentStatus.running,
            ))
            session.commit()

            # Search by name
            results = session.query(Agent).filter(
                Agent.name.like("%customer%")
            ).all()
            assert len(results) == 1
            assert results[0].name == "customer-support"

            # Search by description
            results = session.query(Agent).filter(
                Agent.description.like("%ETL%")
            ).all()
            assert len(results) == 1
            assert results[0].name == "data-pipeline"

    def test_soft_delete_agent(self) -> None:
        with TestSession() as session:
            agent = Agent(
                name="to-delete",
                version="1.0.0",
                team="eng",
                owner="a@b.com",
                framework="langgraph",
                model_primary="gpt-4o",
                status=AgentStatus.running,
            )
            session.add(agent)
            session.commit()

            agent.status = AgentStatus.stopped
            session.commit()

            result = session.query(Agent).filter_by(name="to-delete").first()
            assert result.status == AgentStatus.stopped

    def test_agent_unique_name(self) -> None:
        with TestSession() as session:
            session.add(Agent(
                name="unique-agent",
                version="1.0.0",
                team="eng",
                owner="a@b.com",
                framework="langgraph",
                model_primary="gpt-4o",
                status=AgentStatus.running,
            ))
            session.commit()

            session.add(Agent(
                name="unique-agent",
                version="2.0.0",
                team="eng",
                owner="a@b.com",
                framework="langgraph",
                model_primary="gpt-4o",
                status=AgentStatus.running,
            ))
            with pytest.raises(Exception):
                session.commit()


class TestToolRegistrySync:
    def test_register_tool(self) -> None:
        with TestSession() as session:
            tool = Tool(
                name="zendesk-mcp",
                description="Zendesk MCP server",
                tool_type="mcp_server",
                endpoint="http://localhost:3000",
                source="manual",
            )
            session.add(tool)
            session.commit()

            result = session.query(Tool).filter_by(name="zendesk-mcp").first()
            assert result is not None
            assert result.tool_type == "mcp_server"
            assert result.endpoint == "http://localhost:3000"

    def test_list_tools(self) -> None:
        with TestSession() as session:
            for name in ["tool-a", "tool-b", "tool-c"]:
                session.add(Tool(
                    name=name,
                    description=f"Description for {name}",
                    tool_type="mcp_server",
                    source="scanner",
                ))
            session.commit()

            tools = session.query(Tool).all()
            assert len(tools) == 3

    def test_search_tools(self) -> None:
        with TestSession() as session:
            session.add(Tool(name="zendesk-mcp", description="Zendesk integration"))
            session.add(Tool(name="slack-mcp", description="Slack messaging"))
            session.commit()

            results = session.query(Tool).filter(
                Tool.name.like("%slack%")
            ).all()
            assert len(results) == 1
            assert results[0].name == "slack-mcp"
