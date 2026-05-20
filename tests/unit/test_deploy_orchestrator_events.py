"""DeployOrchestrator: one phase event per boundary, log events forward, terminal events end the stream."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from api.models.deploy_events import DeployEvent
from api.services.deploy_event_bus import DeployEventBus
from api.services.deploy_orchestrator import DeployOrchestrator


@pytest.fixture
def bus() -> DeployEventBus:
    return DeployEventBus()


@pytest.fixture
def orch(bus) -> DeployOrchestrator:
    return DeployOrchestrator(event_bus=bus)


@pytest.mark.asyncio
async def test_emits_phase_then_complete(orch, bus) -> None:
    fake_provisioner = MagicMock()
    fake_provisioner.provision = AsyncMock(return_value=MagicMock())
    fake_deployer = MagicMock()
    fake_deployer.build = AsyncMock(return_value=MagicMock())
    fake_deployer.deploy = AsyncMock(return_value="https://x.example.com")

    job = MagicMock(
        job_id="j-1",
        agent_id="a-1",
        cloud="gcp",
        region="us-central1",
        payload=MagicMock(infra_mode="provision", byo_fields={}),
    )

    received: list[DeployEvent] = []
    async with bus.subscribe("j-1") as queue:
        await orch.start(
            job=job,
            event_bus=bus,
            _provisioner=fake_provisioner,
            _deployer=fake_deployer,
        )
        while not queue.empty():
            received.append(queue.get_nowait())

    phases = [e.phase for e in received if e.type == "phase"]
    assert phases == [
        "provisioning",
        "building",
        "pushing",
        "deploying",
        "health_checking",
        "registering",
    ]
    assert received[-1].type == "complete"
    assert received[-1].endpoint_url == "https://x.example.com"


@pytest.mark.asyncio
async def test_provision_failure_emits_error_and_halts(orch, bus) -> None:
    fake_provisioner = MagicMock()
    fake_provisioner.provision = AsyncMock(side_effect=RuntimeError("VPC quota exceeded"))
    fake_deployer = MagicMock()
    fake_deployer.build = AsyncMock(return_value=MagicMock())
    fake_deployer.deploy = AsyncMock(return_value="https://x.example.com")

    job = MagicMock(
        job_id="j-2",
        agent_id="a-2",
        cloud="gcp",
        region="us-central1",
        payload=MagicMock(infra_mode="provision", byo_fields={}),
    )

    received: list[DeployEvent] = []
    async with bus.subscribe("j-2") as queue:
        await orch.start(
            job=job,
            event_bus=bus,
            _provisioner=fake_provisioner,
            _deployer=fake_deployer,
        )
        while not queue.empty():
            received.append(queue.get_nowait())

    assert any(e.type == "error" for e in received)
    fake_deployer.build.assert_not_called()


@pytest.mark.asyncio
async def test_byo_mode_skips_provisioner_provision_call(orch, bus) -> None:
    """In BYO mode, the provisioner is not invoked — only the deployer."""
    fake_provisioner = MagicMock()
    fake_provisioner.provision = AsyncMock(return_value=MagicMock())
    fake_deployer = MagicMock()
    fake_deployer.build = AsyncMock(return_value=MagicMock())
    fake_deployer.deploy = AsyncMock(return_value="https://x.example.com")

    job = MagicMock(
        job_id="j-3",
        agent_id="a-3",
        cloud="aws",
        region="us-east-1",
        payload=MagicMock(infra_mode="byo", byo_fields={}),
    )

    async with bus.subscribe("j-3"):
        await orch.start(
            job=job,
            event_bus=bus,
            _provisioner=fake_provisioner,
            _deployer=fake_deployer,
        )

    fake_provisioner.provision.assert_not_called()
    fake_deployer.deploy.assert_awaited_once()


@pytest.mark.asyncio
async def test_provisioner_progress_callback_forwards_to_bus(orch, bus) -> None:
    """When the provisioner calls its progress(message) callback, a log event lands on the bus."""

    captured_callbacks: list = []

    async def _fake_provision(_payload, progress=None):
        captured_callbacks.append(progress)
        if progress:
            await progress("creating VPC")
            await progress("creating Service Account")

    fake_provisioner = MagicMock()
    fake_provisioner.provision = _fake_provision
    fake_deployer = MagicMock()
    fake_deployer.build = AsyncMock(return_value=MagicMock())
    fake_deployer.deploy = AsyncMock(return_value="https://x.example.com")

    job = MagicMock(
        job_id="j-progress",
        agent_id="a-1",
        cloud="gcp",
        region="us-central1",
        payload=MagicMock(infra_mode="provision", byo_fields={}),
    )

    received: list[DeployEvent] = []
    async with bus.subscribe("j-progress") as queue:
        await orch.start(
            job=job,
            event_bus=bus,
            _provisioner=fake_provisioner,
            _deployer=fake_deployer,
        )
        while not queue.empty():
            received.append(queue.get_nowait())

    log_events = [e for e in received if e.type == "log"]
    messages = [e.message for e in log_events]
    assert "creating VPC" in messages
    assert "creating Service Account" in messages
    # The provisioner was passed a non-None callback.
    assert captured_callbacks
    assert captured_callbacks[0] is not None


# -- destroy_partial wiring (#450) -----------------------------------------


@pytest.fixture
def job_store():
    from api.services.deploy_stores import InMemoryJobStore

    return InMemoryJobStore()


@pytest.fixture
def orch_with_store(bus, job_store):
    return DeployOrchestrator(event_bus=bus, job_store=job_store)


def _real_job(job_id: str = "j-d1") -> object:
    """Build a real DeployJobRecord so the orchestrator can mutate + persist it."""
    from datetime import UTC, datetime

    from api.models.deploy_events import DeployJobStatus
    from api.services.deploy_jobs import DeployJobCreate, DeployJobRecord

    return DeployJobRecord(
        job_id=job_id,
        team_id="t1",
        agent_id="a-1",
        cloud="gcp",
        region="us-central1",
        status=DeployJobStatus.pending,
        pending_approval=False,
        created_at=datetime.now(UTC),
        payload=DeployJobCreate.model_validate(
            {
                "agent_id": "a-1",
                "cloud": "gcp",
                "region": "us-central1",
                "infra_mode": "provision",
                "byo_fields": {},
                "env_vars": [],
                "secrets": [],
                "scaling": {"min": 1, "max": 3, "cpu_target_pct": 70},
                "db_tier": None,
            }
        ),
    )


def _mock_state(cloud: str = "gcp", resources: dict | None = None):
    """Stand-in for engine.provisioners.state.InfraState that survives model_dump."""
    from datetime import UTC, datetime

    from engine.provisioners.state import InfraState

    return InfraState(
        cloud=cloud,  # type: ignore[arg-type]
        region="us-central1",
        provisioned_by="t1",
        provisioned_at=datetime.now(UTC),
        mode="provisioned",
        resources=resources
        or {"artifact_registry": {"name": "projects/p/locations/us-central1/repositories/r"}},
    )


@pytest.mark.asyncio
async def test_start_persists_infra_state_on_job_record(orch_with_store, bus, job_store) -> None:
    """After provisioner.provision() returns, the job record carries the InfraState dump."""
    state = _mock_state()
    fake_provisioner = MagicMock()
    fake_provisioner.provision = AsyncMock(return_value=state)
    fake_deployer = MagicMock()
    fake_deployer.build = AsyncMock(return_value=MagicMock())
    fake_deployer.deploy = AsyncMock(return_value="https://x.example.com")

    job = _real_job(job_id="j-persist")
    await job_store.put(job)

    await orch_with_store.start(
        job=job,
        event_bus=bus,
        _provisioner=fake_provisioner,
        _deployer=fake_deployer,
    )

    stored = await job_store.get("j-persist")
    assert stored is not None
    assert stored.infra_state is not None
    assert stored.infra_state["cloud"] == "gcp"
    assert "artifact_registry" in stored.infra_state["resources"]


@pytest.mark.asyncio
async def test_destroy_partial_calls_provisioner_destroy_with_state(
    orch_with_store, bus, job_store
) -> None:
    state = _mock_state()
    job = _real_job(job_id="j-destroy")
    job.infra_state = state.model_dump(mode="json")
    await job_store.put(job)

    fake_provisioner = MagicMock()
    fake_provisioner.destroy = AsyncMock(return_value=None)

    received: list[DeployEvent] = []
    async with bus.subscribe("j-destroy") as queue:
        await orch_with_store.destroy_partial("j-destroy", _provisioner=fake_provisioner)
        while not queue.empty():
            received.append(queue.get_nowait())

    fake_provisioner.destroy.assert_awaited_once()
    passed_state = fake_provisioner.destroy.await_args.args[0]
    assert passed_state.cloud == "gcp"
    assert passed_state.resources == state.resources

    phases = [e.phase for e in received if e.type == "phase"]
    assert phases == ["destroying"]
    assert any(e.type == "complete" for e in received)


@pytest.mark.asyncio
async def test_destroy_partial_no_op_when_no_infra_state(orch_with_store, bus, job_store) -> None:
    """If the job has no infra_state, destroy is a no-op + emits complete."""
    job = _real_job(job_id="j-empty")
    job.infra_state = None
    await job_store.put(job)

    fake_provisioner = MagicMock()
    fake_provisioner.destroy = AsyncMock(return_value=None)

    received: list[DeployEvent] = []
    async with bus.subscribe("j-empty") as queue:
        await orch_with_store.destroy_partial("j-empty", _provisioner=fake_provisioner)
        while not queue.empty():
            received.append(queue.get_nowait())

    fake_provisioner.destroy.assert_not_called()
    assert any(e.type == "complete" for e in received)
    assert any(e.type == "log" and "nothing to tear down" in (e.message or "") for e in received)


@pytest.mark.asyncio
async def test_destroy_partial_idempotent_second_call_is_noop(
    orch_with_store, bus, job_store
) -> None:
    """Second destroy_partial sees no infra_state (first call cleared it)."""
    state = _mock_state()
    job = _real_job(job_id="j-idem")
    job.infra_state = state.model_dump(mode="json")
    await job_store.put(job)

    fake_provisioner = MagicMock()
    fake_provisioner.destroy = AsyncMock(return_value=None)

    await orch_with_store.destroy_partial("j-idem", _provisioner=fake_provisioner)
    # First call: destroy was invoked.
    fake_provisioner.destroy.assert_awaited_once()

    fake_provisioner.destroy.reset_mock()
    await orch_with_store.destroy_partial("j-idem", _provisioner=fake_provisioner)
    # Second call: no provisioner.destroy invocation (state was cleared).
    fake_provisioner.destroy.assert_not_called()


@pytest.mark.asyncio
async def test_destroy_partial_unknown_job_emits_warn_and_complete(orch_with_store, bus) -> None:
    received: list[DeployEvent] = []
    async with bus.subscribe("j-unknown") as queue:
        await orch_with_store.destroy_partial("j-unknown")
        while not queue.empty():
            received.append(queue.get_nowait())

    assert any(e.type == "complete" for e in received)
    assert any(e.type == "log" and e.level == "warn" for e in received)


@pytest.mark.asyncio
async def test_destroy_partial_provisioner_error_emits_error_event(
    orch_with_store, bus, job_store
) -> None:
    state = _mock_state()
    job = _real_job(job_id="j-fail")
    job.infra_state = state.model_dump(mode="json")
    await job_store.put(job)

    fake_provisioner = MagicMock()
    fake_provisioner.destroy = AsyncMock(side_effect=RuntimeError("API quota exceeded"))

    received: list[DeployEvent] = []
    async with bus.subscribe("j-fail") as queue:
        await orch_with_store.destroy_partial("j-fail", _provisioner=fake_provisioner)
        while not queue.empty():
            received.append(queue.get_nowait())

    error_events = [e for e in received if e.type == "error"]
    assert len(error_events) == 1
    assert "quota" in (error_events[0].message or "")
    # The phase event still fired before the failure.
    assert any(e.type == "phase" and e.phase == "destroying" for e in received)
