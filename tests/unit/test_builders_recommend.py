"""Tests for POST /api/v1/builders/recommend endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_recommend_endpoint_returns_stack() -> None:
    """Full-input recommendation returns the expected stack."""
    resp = client.post(
        "/api/v1/builders/recommend",
        json={
            "business_goal": "reduce tier-1 support tickets",
            "technical_use_case": "search KB then look up order then escalate",
            "state_flags": ["b", "c"],
            "cloud_preference": "aws",
            "language_preference": "python",
            "data_flags": ["a"],
            "scale_profile": "realtime",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["framework"] == "langgraph"
    assert data["rag"] == "vector"
    assert data["deploy_target"] == "ecs_fargate"
    assert "deflection_rate" in data["eval_dimensions"]
    assert data["code_tier"] == "full_code"


def test_recommend_endpoint_defaults() -> None:
    """Empty body returns a valid default recommendation."""
    resp = client.post("/api/v1/builders/recommend", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["framework"] == "langgraph"
    assert body["data"]["deploy_target"] == "docker_compose"
    # reasoning dict must be present
    assert isinstance(body["data"]["reasoning"], dict)
    assert body["data"]["reasoning"]


def test_recommend_endpoint_typescript_use_case() -> None:
    """TypeScript preference routes to openai_agents."""
    resp = client.post(
        "/api/v1/builders/recommend",
        json={"language_preference": "typescript"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["framework"] == "openai_agents"


def test_recommend_endpoint_gcp() -> None:
    """GCP cloud preference routes to google_adk and cloud_run."""
    resp = client.post(
        "/api/v1/builders/recommend",
        json={"cloud_preference": "gcp"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["framework"] == "google_adk"
    assert data["deploy_target"] == "cloud_run"
    assert data["model_primary"] == "gemini-2.5-flash"


def test_recommend_endpoint_response_shape() -> None:
    """Response is wrapped in the standard ApiResponse envelope."""
    resp = client.post("/api/v1/builders/recommend", json={})
    assert resp.status_code == 200
    body = resp.json()
    # Standard ApiResponse envelope
    assert "data" in body
    assert "meta" in body
    assert "errors" in body
    # All expected Recommendation fields present
    data = body["data"]
    for field in (
        "framework",
        "code_tier",
        "model_primary",
        "rag",
        "memory",
        "mcp_a2a",
        "deploy_target",
        "eval_dimensions",
        "reasoning",
    ):
        assert field in data, f"missing field: {field}"
