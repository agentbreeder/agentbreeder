"""GET status + POST destroy-partial endpoints (SSE stream tested separately with async client)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.models.deploy_events import DeployJobStatus
from api.services.deploy_jobs import DeployJobCreate


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_services():
    """Wire fake DeployJobService that pre-seeds a job 'j-1' owned by team 'engineering'."""
    from api.services.deploy_event_bus import DeployEventBus
    from api.services.deploy_jobs import DeployJobService

    orchestrator = MagicMock()
    orchestrator.start = AsyncMock(return_value=None)
    orchestrator.destroy_partial = AsyncMock(return_value=None)

    agent_repo = MagicMock()
    agent_repo.get = AsyncMock(
        return_value=MagicMock(team="engineering", access={"require_approval": False})
    )

    from api.services.deploy_stores import InMemoryIdempotencyStore, InMemoryJobStore

    bus = DeployEventBus()
    svc = DeployJobService(
        event_bus=bus,
        orchestrator=orchestrator,
        idempotency_store=InMemoryIdempotencyStore(),
        job_store=InMemoryJobStore(),
        agent_repo=agent_repo,
    )

    # Pre-seed the service's job store with a test job.
    import asyncio

    async def setup():
        payload = DeployJobCreate.model_validate(
            {
                "agent_id": "agent-1",
                "cloud": "gcp",
                "region": "us-central1",
                "infra_mode": "provision",
                "byo_fields": {},
                "env_vars": [],
                "secrets": [],
                "scaling": {"min": 1, "max": 3, "cpu_target_pct": 70},
            }
        )
        await svc._record(
            job_id="j-1",
            payload=payload,
            team_id="engineering",
            status=DeployJobStatus.provisioning,
        )

    try:
        asyncio.run(setup())
    except RuntimeError:
        # Event loop already running (pytest-asyncio); use existing loop
        loop = asyncio.get_event_loop()
        loop.run_until_complete(setup())

    app.state.deploy_event_bus = bus
    app.state.deploy_job_service = svc
    app.state.deploy_orchestrator = orchestrator
    return


def test_get_status_returns_job_record(client: TestClient) -> None:
    resp = client.get("/api/v1/deployments/j-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["job_id"] == "j-1"
    assert body["data"]["status"] == "provisioning"


def test_destroy_partial_invokes_orchestrator(client: TestClient) -> None:
    resp = client.post("/api/v1/deployments/j-1/destroy-partial")
    assert resp.status_code == 202
    app.state.deploy_orchestrator.destroy_partial.assert_awaited_once_with("j-1")


def test_stream_401_without_auth() -> None:
    # Create a fresh client without the default auth override
    test_client = TestClient(app)
    # Use no_auto_auth by clearing the override
    from api.auth import get_current_user

    app.dependency_overrides.pop(get_current_user, None)
    try:
        resp = test_client.get("/api/v1/deployments/j-1/stream")
        assert resp.status_code == 401
    finally:
        # Restore the override for other tests
        async def _mock_get_current_user():
            from tests.integration.conftest import _DEFAULT_USER

            return _DEFAULT_USER

        app.dependency_overrides[get_current_user] = _mock_get_current_user


def test_get_unknown_job_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/deployments/nonexistent")
    assert resp.status_code == 404
