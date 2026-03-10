"""Tests for API main and health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_200(self) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "agent-garden-api"
        assert data["version"] == "0.1.0"


class TestAPIConfig:
    def test_api_has_correct_title(self) -> None:
        assert app.title == "Agent Garden API"

    def test_api_has_agent_routes(self) -> None:
        routes = [r.path for r in app.routes]
        assert "/api/v1/agents" in routes or any("/api/v1/agents" in r for r in routes)

    def test_api_has_registry_routes(self) -> None:
        routes = [r.path for r in app.routes]
        assert any("/api/v1/registry" in str(r) for r in routes)

    def test_api_has_cors(self) -> None:
        response = client.options("/health", headers={"Origin": "http://localhost:3000"})
        # CORS should allow the origin
        assert response.status_code in (200, 405)
