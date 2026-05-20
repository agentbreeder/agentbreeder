"""POST /api/v1/deployments/: create-job contract (idempotency + approval gating)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_services():
    """Wire fake agent_repo + orchestrator onto app.state for the duration of one test."""
    from api.services.deploy_event_bus import DeployEventBus
    from api.services.deploy_jobs import DeployJobService

    orchestrator = MagicMock()
    orchestrator.start = AsyncMock(return_value=None)
    orchestrator.destroy_partial = AsyncMock(return_value=None)

    agent_repo = MagicMock()
    agent_repo.get = AsyncMock(
        return_value=MagicMock(
            team="engineering",  # match the default user's team
            access={"require_approval": False},
        )
    )

    bus = DeployEventBus()
    svc = DeployJobService(
        event_bus=bus,
        orchestrator=orchestrator,
        idempotency_store={},
        agent_repo=agent_repo,
    )
    app.state.deploy_event_bus = bus
    app.state.deploy_job_service = svc
    app.state.deploy_orchestrator = orchestrator
    return
    # Tests run in isolation; no teardown needed.


def _body(**overrides) -> dict:
    base = {
        "agent_id": "agent-1",
        "cloud": "gcp",
        "region": "us-central1",
        "infra_mode": "provision",
        "byo_fields": {},
        "env_vars": [],
        "secrets": [],
        "scaling": {"min": 1, "max": 3, "cpu_target_pct": 70},
    }
    base.update(overrides)
    return base


def test_create_returns_202_with_job_id(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/deployments/",
        json=_body(),
        headers={
            "Authorization": "Bearer valid-token",
            "Idempotency-Key": "key-1",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["data"]["job_id"]
    assert body["data"]["pending_approval"] is False


def test_create_with_approval_required_sets_pending(client: TestClient) -> None:
    # Patch the agent_repo fixture to return an agent requiring approval
    app.state.deploy_job_service._agent_repo.get = AsyncMock(
        return_value=MagicMock(
            team="engineering",  # match the default user's team
            access={"require_approval": True},
        )
    )
    resp = client.post(
        "/api/v1/deployments/",
        json=_body(agent_id="agent-needs-approval"),
        headers={
            "Authorization": "Bearer valid-token",
            "Idempotency-Key": "key-2",
        },
    )
    assert resp.status_code == 202
    assert resp.json()["data"]["pending_approval"] is True


def test_create_without_idempotency_key_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/deployments/",
        json=_body(),
        headers={"Authorization": "Bearer valid-token"},
    )
    assert resp.status_code == 400
    assert "Idempotency-Key" in resp.text


@pytest.mark.no_auto_auth
def test_create_unauthorized_returns_401(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/deployments/",
        json=_body(),
    )
    assert resp.status_code == 401
