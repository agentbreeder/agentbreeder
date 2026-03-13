"""Tests for /api/v2/agents endpoints using FastAPI TestClient with mocked DB."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.v2.agents import _v2_envelope, router

# ── _v2_envelope helper ───────────────────────────────────────────────────────


class TestV2Envelope:
    def test_basic_envelope(self):
        result = _v2_envelope({"key": "val"})
        assert result["data"] == {"key": "val"}
        assert result["errors"] == []
        assert result["meta"]["api_version"] == "v2"
        assert "request_id" in result["meta"]

    def test_no_next_cursor_by_default(self):
        result = _v2_envelope([])
        assert "next_cursor" not in result["meta"]

    def test_with_next_cursor(self):
        result = _v2_envelope([], next_cursor="2026-03-13T12:00:00")
        assert result["meta"]["next_cursor"] == "2026-03-13T12:00:00"

    def test_request_id_is_unique(self):
        r1 = _v2_envelope({})
        r2 = _v2_envelope({})
        assert r1["meta"]["request_id"] != r2["meta"]["request_id"]


# ── test app fixture ──────────────────────────────────────────────────────────


def _make_mock_agent(
    id_: str = "00000000-0000-0000-0000-000000000001",
    name: str = "test-agent",
    version: str = "1.0.0",
    team: str = "eng",
    created_at: datetime | None = None,
) -> MagicMock:
    agent = MagicMock()
    agent.id = id_
    agent.name = name
    agent.version = version
    agent.team = team
    agent.owner = "owner@example.com"
    agent.description = "A test agent"
    agent.framework = "langgraph"
    agent.model_primary = "gpt-4o"
    agent.model_fallback = None
    agent.model_gateway = None
    agent.model_temperature = None
    agent.model_max_tokens = None
    agent.endpoint_url = "http://localhost:8080"
    agent.status = "running"
    agent.tags = []
    agent.created_at = created_at or datetime(2026, 3, 13, tzinfo=UTC)
    agent.updated_at = datetime(2026, 3, 13, tzinfo=UTC)
    agent.config_snapshot = {}
    return agent


def _make_test_app() -> tuple[FastAPI, MagicMock]:
    """Create a FastAPI test app with mocked auth and DB dependencies."""
    from api.auth import get_current_user
    from api.database import get_db

    mock_user = MagicMock()
    mock_user.id = "user-123"
    mock_user.email = "test@example.com"

    mock_db = AsyncMock()

    app = FastAPI()

    async def override_get_db():
        yield mock_db

    async def override_get_current_user():
        return mock_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.include_router(router)
    return app, mock_db


# ── GET /api/v2/agents ────────────────────────────────────────────────────────


class TestListAgentsV2:
    @pytest.fixture
    def client_and_db(self):
        app, mock_db = _make_test_app()
        return TestClient(app), mock_db

    def _setup_db_result(self, mock_db, agents: list) -> None:
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = agents
        mock_db.execute = AsyncMock(return_value=mock_result)

    def test_list_returns_v2_envelope(self, client_and_db):
        client, mock_db = client_and_db
        self._setup_db_result(mock_db, [_make_mock_agent()])
        resp = client.get("/api/v2/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["api_version"] == "v2"
        assert "request_id" in body["meta"]
        assert isinstance(body["data"], list)
        assert body["errors"] == []

    def test_list_empty(self, client_and_db):
        client, mock_db = client_and_db
        self._setup_db_result(mock_db, [])
        resp = client.get("/api/v2/agents")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_list_next_cursor_when_more_results(self, client_and_db):
        """When result count > limit, next_cursor should be set."""
        client, mock_db = client_and_db
        # Return limit+1 agents to trigger cursor
        agents = [
            _make_mock_agent(
                id_=f"00000000-0000-0000-0000-{i:012}",
                name=f"agent-{i}",
                created_at=datetime(2026, 3, i + 1, tzinfo=UTC),
            )
            for i in range(1, 4)  # limit=2 → 3 > 2 triggers cursor
        ]
        self._setup_db_result(mock_db, agents)
        resp = client.get("/api/v2/agents?limit=2")
        assert resp.status_code == 200
        body = resp.json()
        assert "next_cursor" in body["meta"]
        assert len(body["data"]) == 2

    def test_list_no_cursor_when_at_limit(self, client_and_db):
        client, mock_db = client_and_db
        self._setup_db_result(mock_db, [_make_mock_agent()])
        resp = client.get("/api/v2/agents?limit=5")
        assert resp.status_code == 200
        assert "next_cursor" not in resp.json()["meta"]

    def test_list_invalid_cursor_returns_400(self, client_and_db):
        client, mock_db = client_and_db
        self._setup_db_result(mock_db, [])
        resp = client.get("/api/v2/agents?cursor=not-a-date")
        assert resp.status_code == 400

    def test_list_team_filter(self, client_and_db):
        client, mock_db = client_and_db
        self._setup_db_result(mock_db, [_make_mock_agent(team="platform")])
        resp = client.get("/api/v2/agents?team=platform")
        assert resp.status_code == 200

    def test_list_with_valid_cursor(self, client_and_db):
        client, mock_db = client_and_db
        self._setup_db_result(mock_db, [])
        resp = client.get("/api/v2/agents?cursor=2026-03-13T00:00:00")
        assert resp.status_code == 200


# ── GET /api/v2/agents/{agent_id} ────────────────────────────────────────────


class TestGetAgentV2:
    @pytest.fixture
    def client_and_db(self):
        app, mock_db = _make_test_app()
        return TestClient(app), mock_db

    def test_get_existing_agent(self, client_and_db):
        client, mock_db = client_and_db
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = _make_mock_agent(
            id_="00000000-0000-0000-0000-000000000123"
        )
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/api/v2/agents/00000000-0000-0000-0000-000000000123")
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["api_version"] == "v2"
        assert body["data"]["name"] == "test-agent"

    def test_get_missing_agent_returns_404(self, client_and_db):
        client, mock_db = client_and_db
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        resp = client.get("/api/v2/agents/does-not-exist")
        assert resp.status_code == 404


# ── POST /api/v2/agents/batch ─────────────────────────────────────────────────


class TestBatchRegisterAgentsV2:
    @pytest.fixture
    def client_and_db(self):
        app, mock_db = _make_test_app()
        return TestClient(app), mock_db

    def _agent_payload(self, name: str = "batch-agent") -> dict:
        return {
            "name": name,
            "version": "1.0.0",
            "team": "eng",
            "owner": "test@example.com",
            "framework": "langgraph",
            "model_primary": "gpt-4o",
        }

    def test_batch_limit_exceeded_returns_400(self, client_and_db):
        client, _ = client_and_db
        payload = [self._agent_payload(f"agent-{i}") for i in range(51)]
        resp = client.post("/api/v2/agents/batch", json=payload)
        assert resp.status_code == 400
        assert "50" in resp.json()["detail"]

    def test_batch_partial_success(self, client_and_db):
        """One agent succeeds, one fails — both results returned."""
        client, mock_db = client_and_db

        created_agent = _make_mock_agent(name="success-agent")

        call_count = [0]

        async def mock_create(agent_create, created_by):
            call_count[0] += 1
            if call_count[0] == 1:
                return created_agent
            raise ValueError("Name already taken")

        with patch("registry.agents.AgentRegistry") as MockRegistry:
            instance = MockRegistry.return_value
            instance.create = mock_create
            mock_db.commit = AsyncMock()

            payload = [
                self._agent_payload("success-agent"),
                self._agent_payload("fail-agent"),
            ]
            resp = client.post("/api/v2/agents/batch", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        results = body["data"]
        assert len(results) == 2
        # First succeeded
        assert results[0]["data"] is not None
        assert results[0]["error"] is None
        # Second failed
        assert results[1]["data"] is None
        assert results[1]["error"] is not None

    def test_batch_empty_list(self, client_and_db):
        client, mock_db = client_and_db
        mock_db.commit = AsyncMock()
        resp = client.post("/api/v2/agents/batch", json=[])
        assert resp.status_code == 200
        assert resp.json()["data"] == []
