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
    assert captured_callbacks and captured_callbacks[0] is not None
