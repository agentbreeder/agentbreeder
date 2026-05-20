"""DeployEventBus: per-job asyncio queue + 200-event ring buffer + 30-min TTL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from api.models.deploy_events import DeployEvent
from api.services.deploy_event_bus import DeployEventBus


def _evt(job_id: str = "job-1", n: int = 0) -> DeployEvent:
    return DeployEvent(
        type="log",
        job_id=job_id,
        timestamp=datetime.now(UTC),
        level="info",
        message=f"event-{n}",
    )


@pytest.mark.asyncio
async def test_subscriber_receives_published_event() -> None:
    bus = DeployEventBus()
    async with bus.subscribe("job-1") as queue:
        await bus.publish(_evt(n=1))
        evt = await asyncio.wait_for(queue.get(), timeout=1)
        assert evt.message == "event-1"


@pytest.mark.asyncio
async def test_replay_buffer_for_late_subscriber() -> None:
    bus = DeployEventBus()
    for i in range(5):
        await bus.publish(_evt(n=i))
    async with bus.subscribe("job-1") as queue:
        received = [await queue.get() for _ in range(5)]
    assert [e.message for e in received] == [f"event-{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_ring_buffer_caps_at_200_events() -> None:
    bus = DeployEventBus(ring_size=200)
    for i in range(250):
        await bus.publish(_evt(n=i))
    async with bus.subscribe("job-1") as queue:
        received = []
        while not queue.empty():
            received.append(queue.get_nowait())
    assert len(received) == 200
    assert received[0].message == "event-50"


@pytest.mark.asyncio
async def test_per_job_isolation() -> None:
    bus = DeployEventBus()
    await bus.publish(_evt(job_id="job-a", n=1))
    await bus.publish(_evt(job_id="job-b", n=2))
    async with bus.subscribe("job-a") as queue:
        evts = []
        while not queue.empty():
            evts.append(queue.get_nowait())
    assert len(evts) == 1
    assert evts[0].message == "event-1"


@pytest.mark.asyncio
async def test_job_expires_after_ttl() -> None:
    bus = DeployEventBus(ttl=timedelta(seconds=0))
    await bus.publish(_evt(n=1))
    bus.cleanup_expired()
    async with bus.subscribe("job-1") as queue:
        assert queue.empty()


@pytest.mark.asyncio
async def test_multiple_concurrent_subscribers() -> None:
    bus = DeployEventBus()
    received_a: list[str] = []
    received_b: list[str] = []

    async def consume(out: list[str]) -> None:
        async with bus.subscribe("job-1") as queue:
            for _ in range(3):
                evt = await asyncio.wait_for(queue.get(), timeout=1)
                out.append(evt.message)

    task_a = asyncio.create_task(consume(received_a))
    task_b = asyncio.create_task(consume(received_b))
    await asyncio.sleep(0.05)
    for i in range(3):
        await bus.publish(_evt(n=i))
    await asyncio.gather(task_a, task_b)
    assert received_a == received_b == ["event-0", "event-1", "event-2"]
