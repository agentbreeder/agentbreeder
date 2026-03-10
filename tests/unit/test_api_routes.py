"""Tests for API routes (agents + registry) using TestClient with mocked registry services."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.models.enums import AgentStatus
from api.main import app

client = TestClient(app)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_agent(name: str = "test-agent", **kwargs):
    """Create a mock Agent-like object."""
    defaults = dict(
        id=kwargs.pop("id", uuid.uuid4()),
        name=name,
        version="1.0.0",
        description="A test agent",
        team="engineering",
        owner="test@example.com",
        framework="langgraph",
        model_primary="gpt-4o",
        model_fallback=None,
        endpoint_url="http://localhost:8080",
        status=AgentStatus.running,
        tags=[],
        config_snapshot={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(kwargs)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _make_tool(name: str = "test-tool", **kwargs):
    """Create a mock Tool-like object."""
    defaults = dict(
        id=kwargs.pop("id", uuid.uuid4()),
        name=name,
        description="A test tool",
        tool_type="mcp_server",
        schema_definition={},
        endpoint="http://localhost:3000",
        status="active",
        source="manual",
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(kwargs)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ─── Agent Routes ─────────────────────────────────────────────────────────────


class TestListAgents:
    @patch("api.routes.agents.AgentRegistry.list", new_callable=AsyncMock)
    def test_list_empty(self, mock_list: AsyncMock) -> None:
        mock_list.return_value = ([], 0)
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    @patch("api.routes.agents.AgentRegistry.list", new_callable=AsyncMock)
    def test_list_returns_agents(self, mock_list: AsyncMock) -> None:
        agents = [_make_agent("a1"), _make_agent("a2")]
        mock_list.return_value = (agents, 2)
        resp = client.get("/api/v1/agents")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    @patch("api.routes.agents.AgentRegistry.list", new_callable=AsyncMock)
    def test_list_passes_filters(self, mock_list: AsyncMock) -> None:
        mock_list.return_value = ([], 0)
        client.get("/api/v1/agents", params={"team": "alpha", "framework": "crewai", "status": "running"})
        call_kwargs = mock_list.call_args
        assert call_kwargs[1]["team"] == "alpha"
        assert call_kwargs[1]["framework"] == "crewai"

    @patch("api.routes.agents.AgentRegistry.list", new_callable=AsyncMock)
    def test_list_pagination(self, mock_list: AsyncMock) -> None:
        mock_list.return_value = ([_make_agent()], 5)
        resp = client.get("/api/v1/agents", params={"page": 2, "per_page": 3})
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 5
        assert body["meta"]["page"] == 2
        assert body["meta"]["per_page"] == 3


class TestSearchAgents:
    @patch("api.routes.agents.AgentRegistry.search", new_callable=AsyncMock)
    def test_search(self, mock_search: AsyncMock) -> None:
        mock_search.return_value = ([_make_agent("customer-bot")], 1)
        resp = client.get("/api/v1/agents/search", params={"q": "customer"})
        assert resp.status_code == 200
        assert resp.json()["data"][0]["name"] == "customer-bot"

    def test_search_requires_query(self) -> None:
        resp = client.get("/api/v1/agents/search")
        assert resp.status_code == 422


class TestGetAgent:
    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_get_existing(self, mock_get: AsyncMock) -> None:
        agent = _make_agent()
        mock_get.return_value = agent
        resp = client.get(f"/api/v1/agents/{agent.id}")
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "test-agent"

    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_get_not_found(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = None
        resp = client.get(f"/api/v1/agents/{uuid.uuid4()}")
        assert resp.status_code == 404


class TestCreateAgent:
    @patch("api.routes.agents.AgentRegistry.register", new_callable=AsyncMock)
    def test_create_agent(self, mock_register: AsyncMock) -> None:
        mock_register.return_value = _make_agent("new-agent")
        resp = client.post(
            "/api/v1/agents",
            json={
                "name": "new-agent",
                "version": "1.0.0",
                "team": "eng",
                "owner": "a@b.com",
                "framework": "langgraph",
                "model_primary": "gpt-4o",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "new-agent"
        mock_register.assert_called_once()

    @patch("api.routes.agents.AgentRegistry.register", new_callable=AsyncMock)
    def test_create_agent_with_all_fields(self, mock_register: AsyncMock) -> None:
        mock_register.return_value = _make_agent(
            "full-agent",
            framework="crewai",
            model_fallback="gpt-4o",
            tags=["prod"],
        )
        resp = client.post(
            "/api/v1/agents",
            json={
                "name": "full-agent",
                "version": "2.0.0",
                "description": "Full",
                "team": "platform",
                "owner": "b@c.com",
                "framework": "crewai",
                "model_primary": "claude-sonnet-4",
                "model_fallback": "gpt-4o",
                "endpoint_url": "http://localhost:9090",
                "tags": ["prod"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["framework"] == "crewai"
        assert data["model_fallback"] == "gpt-4o"


class TestUpdateAgent:
    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_update_version(self, mock_get: AsyncMock) -> None:
        agent = _make_agent()
        mock_get.return_value = agent
        resp = client.put(
            f"/api/v1/agents/{agent.id}",
            json={"version": "2.0.0"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["version"] == "2.0.0"

    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_update_multiple_fields(self, mock_get: AsyncMock) -> None:
        agent = _make_agent()
        mock_get.return_value = agent
        resp = client.put(
            f"/api/v1/agents/{agent.id}",
            json={
                "description": "Updated",
                "endpoint_url": "http://new:9090",
                "status": "stopped",
                "tags": ["updated"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["description"] == "Updated"
        assert data["status"] == "stopped"
        assert data["tags"] == ["updated"]

    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_update_not_found(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = None
        resp = client.put(
            f"/api/v1/agents/{uuid.uuid4()}",
            json={"version": "2.0.0"},
        )
        assert resp.status_code == 404


class TestDeleteAgent:
    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_delete_existing(self, mock_get: AsyncMock) -> None:
        agent = _make_agent()
        mock_get.return_value = agent
        resp = client.delete(f"/api/v1/agents/{agent.id}")
        assert resp.status_code == 200
        assert "archived" in resp.json()["data"]["message"]

    @patch("api.routes.agents.AgentRegistry.get_by_id", new_callable=AsyncMock)
    def test_delete_not_found(self, mock_get: AsyncMock) -> None:
        mock_get.return_value = None
        resp = client.delete(f"/api/v1/agents/{uuid.uuid4()}")
        assert resp.status_code == 404


# ─── Registry Routes (Tools) ──────────────────────────────────────────────────


class TestListTools:
    @patch("api.routes.registry.ToolRegistry.list", new_callable=AsyncMock)
    def test_list_empty(self, mock_list: AsyncMock) -> None:
        mock_list.return_value = ([], 0)
        resp = client.get("/api/v1/registry/tools")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    @patch("api.routes.registry.ToolRegistry.list", new_callable=AsyncMock)
    def test_list_returns_tools(self, mock_list: AsyncMock) -> None:
        mock_list.return_value = ([_make_tool("t1"), _make_tool("t2")], 2)
        resp = client.get("/api/v1/registry/tools")
        assert len(resp.json()["data"]) == 2

    @patch("api.routes.registry.ToolRegistry.list", new_callable=AsyncMock)
    def test_list_passes_filters(self, mock_list: AsyncMock) -> None:
        mock_list.return_value = ([], 0)
        client.get("/api/v1/registry/tools", params={"tool_type": "function", "source": "scanner"})
        call_kwargs = mock_list.call_args
        assert call_kwargs[1]["tool_type"] == "function"
        assert call_kwargs[1]["source"] == "scanner"


class TestRegisterTool:
    @patch("api.routes.registry.ToolRegistry.register", new_callable=AsyncMock)
    def test_register_tool(self, mock_reg: AsyncMock) -> None:
        mock_reg.return_value = _make_tool("zendesk-mcp")
        resp = client.post(
            "/api/v1/registry/tools",
            json={"name": "zendesk-mcp", "description": "Zendesk", "endpoint": "http://z:3000"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "zendesk-mcp"

    @patch("api.routes.registry.ToolRegistry.register", new_callable=AsyncMock)
    def test_register_custom_type(self, mock_reg: AsyncMock) -> None:
        mock_reg.return_value = _make_tool("search-fn", tool_type="function")
        resp = client.post(
            "/api/v1/registry/tools",
            json={"name": "search-fn", "tool_type": "function", "source": "scanner"},
        )
        assert resp.status_code == 201
        assert resp.json()["data"]["tool_type"] == "function"


class TestSearchRegistry:
    @patch("api.routes.registry.ToolRegistry.search", new_callable=AsyncMock)
    @patch("api.routes.registry.AgentRegistry.search", new_callable=AsyncMock)
    def test_search_across_entities(self, mock_agent_search: AsyncMock, mock_tool_search: AsyncMock) -> None:
        mock_agent_search.return_value = ([_make_agent("customer-bot")], 1)
        mock_tool_search.return_value = ([_make_tool("zendesk-mcp")], 1)
        resp = client.get("/api/v1/registry/search", params={"q": "customer"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        types = {r["entity_type"] for r in data}
        assert "agent" in types
        assert "tool" in types

    def test_search_requires_query(self) -> None:
        resp = client.get("/api/v1/registry/search")
        assert resp.status_code == 422

    @patch("api.routes.registry.ToolRegistry.search", new_callable=AsyncMock)
    @patch("api.routes.registry.AgentRegistry.search", new_callable=AsyncMock)
    def test_search_no_results(self, mock_agent: AsyncMock, mock_tool: AsyncMock) -> None:
        mock_agent.return_value = ([], 0)
        mock_tool.return_value = ([], 0)
        resp = client.get("/api/v1/registry/search", params={"q": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json()["data"] == []
