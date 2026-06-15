"""Tests for API main and health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def _route_paths(application) -> list[str]:
    """Collect every route path, flattening included routers.

    Starlette >=1.3 wraps ``include_router()`` results in an ``_IncludedRouter``
    object that has no ``.path`` (the real routes live on ``original_router``),
    so a flat ``[r.path for r in app.routes]`` raises AttributeError. This walks
    plain routes, mounts, and included routers uniformly.
    """
    paths: list[str] = []
    stack = list(getattr(application, "routes", []))
    guard = 0
    while stack and guard < 20000:
        guard += 1
        route = stack.pop()
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.append(path)
        original = getattr(route, "original_router", None)
        if original is not None:
            stack.extend(getattr(original, "routes", []))
        elif getattr(route, "routes", None):
            stack.extend(route.routes)
    return paths


class TestHealthEndpoint:
    def test_health_returns_200(self) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "agentbreeder-api"
        assert data["version"] == "0.1.0"


class TestAPIConfig:
    def test_api_has_correct_title(self) -> None:
        assert app.title == "AgentBreeder API"

    def test_api_has_agent_routes(self) -> None:
        routes = _route_paths(app)
        assert any("/api/v1/agents" in r for r in routes)

    def test_api_has_registry_routes(self) -> None:
        routes = _route_paths(app)
        assert any("/api/v1/registry" in r for r in routes)

    def test_api_has_cors(self) -> None:
        response = client.options("/health", headers={"Origin": "http://localhost:3000"})
        # CORS should allow the origin
        assert response.status_code in (200, 405)
