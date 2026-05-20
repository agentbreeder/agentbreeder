"""DeployJobService: create, get, idempotency, approval gating, destroy-partial."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from api.models.deploy_events import DeployJobStatus
from api.services.deploy_jobs import DeployJobCreate, DeployJobService


@pytest.fixture
def event_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock(return_value=None)
    return bus


@pytest.fixture
def orchestrator() -> MagicMock:
    orch = MagicMock()
    orch.start = AsyncMock(return_value=None)
    orch.destroy_partial = AsyncMock(return_value=None)
    return orch


@pytest.fixture
def idempotency_store() -> dict:
    return {}


@pytest.fixture
def service(event_bus, orchestrator, idempotency_store) -> DeployJobService:
    return DeployJobService(
        event_bus=event_bus,
        orchestrator=orchestrator,
        idempotency_store=idempotency_store,
        agent_repo=AsyncMock(),
    )


def _payload(**overrides) -> DeployJobCreate:
    base = dict(
        agent_id=str(uuid4()),
        cloud="gcp",
        region="us-central1",
        infra_mode="provision",
        byo_fields={},
        env_vars=[],
        secrets=[],
        scaling={"min": 1, "max": 3, "cpu_target_pct": 70},
        db_tier=None,
    )
    base.update(overrides)
    return DeployJobCreate.model_validate(base)


@pytest.mark.asyncio
async def test_create_returns_job_id_and_starts_orchestrator(service, orchestrator) -> None:
    service._agent_repo.get = AsyncMock(
        return_value=MagicMock(team="t1", access={"require_approval": False})
    )
    result = await service.create(_payload(), team_id="t1", idempotency_key="k1")
    assert result.job_id
    assert result.pending_approval is False
    orchestrator.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_with_approval_required_does_not_start_orchestrator(
    service, orchestrator
) -> None:
    service._agent_repo.get = AsyncMock(
        return_value=MagicMock(team="t1", access={"require_approval": True})
    )
    result = await service.create(_payload(), team_id="t1", idempotency_key="k2")
    assert result.pending_approval is True
    orchestrator.start.assert_not_called()


@pytest.mark.asyncio
async def test_create_with_same_idempotency_key_returns_same_job(
    service, idempotency_store
) -> None:
    service._agent_repo.get = AsyncMock(
        return_value=MagicMock(team="t1", access={"require_approval": False})
    )
    r1 = await service.create(_payload(), team_id="t1", idempotency_key="k3")
    r2 = await service.create(_payload(), team_id="t1", idempotency_key="k3")
    assert r1.job_id == r2.job_id


@pytest.mark.asyncio
async def test_cross_team_create_raises_403(service) -> None:
    from fastapi import HTTPException

    service._agent_repo.get = AsyncMock(
        return_value=MagicMock(team="other-team", access={"require_approval": False})
    )
    with pytest.raises(HTTPException) as exc:
        await service.create(_payload(), team_id="t1", idempotency_key="k4")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_destroy_partial_invokes_orchestrator(service, orchestrator) -> None:
    job = await service._record(
        job_id="j-1",
        payload=_payload(),
        team_id="t1",
        status=DeployJobStatus.failed,
    )
    await service.destroy_partial(job.job_id, team_id="t1")
    orchestrator.destroy_partial.assert_awaited_once_with(job.job_id)


@pytest.mark.asyncio
async def test_get_returns_current_status(service) -> None:
    job = await service._record(
        job_id="j-2",
        payload=_payload(),
        team_id="t1",
        status=DeployJobStatus.provisioning,
    )
    found = await service.get(job.job_id, team_id="t1")
    assert found.status == DeployJobStatus.provisioning


@pytest.mark.asyncio
async def test_get_cross_team_403(service) -> None:
    from fastapi import HTTPException

    await service._record(
        job_id="j-3",
        payload=_payload(),
        team_id="t1",
        status=DeployJobStatus.provisioning,
    )
    with pytest.raises(HTTPException) as exc:
        await service.get("j-3", team_id="other")
    assert exc.value.status_code == 403
