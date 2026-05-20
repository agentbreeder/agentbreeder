"""DeployStores: in-memory + Redis idempotency / job stores (#449)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.models.deploy_events import DeployJobStatus
from api.services.deploy_jobs import DeployJobCreate, DeployJobRecord
from api.services.deploy_stores import (
    InMemoryIdempotencyStore,
    InMemoryJobStore,
    RedisIdempotencyStore,
    RedisJobStore,
)


def _record(job_id: str = "j-1", team_id: str = "t1") -> DeployJobRecord:
    return DeployJobRecord(
        job_id=job_id,
        team_id=team_id,
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


# -- In-memory ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_inmemory_idempotency_get_set_roundtrip() -> None:
    store = InMemoryIdempotencyStore()
    assert await store.get("t1", "k1") is None
    await store.set("t1", "k1", "j-1")
    assert await store.get("t1", "k1") == "j-1"


@pytest.mark.asyncio
async def test_inmemory_idempotency_per_team_isolation() -> None:
    store = InMemoryIdempotencyStore()
    await store.set("t1", "k1", "j-1")
    await store.set("t2", "k1", "j-2")
    assert await store.get("t1", "k1") == "j-1"
    assert await store.get("t2", "k1") == "j-2"


@pytest.mark.asyncio
async def test_inmemory_job_store_put_get() -> None:
    store = InMemoryJobStore()
    assert await store.get("j-1") is None
    job = _record()
    await store.put(job)
    fetched = await store.get("j-1")
    assert fetched is not None
    assert fetched.job_id == "j-1"
    assert fetched.team_id == "t1"


# -- Redis-backed (via fakeredis) -------------------------------------------


@pytest.fixture
def fake_redis():
    fakeredis = pytest.importorskip("fakeredis.aioredis")
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_redis_idempotency_get_set_roundtrip(fake_redis) -> None:
    store = RedisIdempotencyStore(fake_redis)
    assert await store.get("t1", "k1") is None
    await store.set("t1", "k1", "j-1")
    assert await store.get("t1", "k1") == "j-1"


@pytest.mark.asyncio
async def test_redis_idempotency_24h_ttl(fake_redis) -> None:
    store = RedisIdempotencyStore(fake_redis)
    await store.set("t1", "k1", "j-1")
    # Confirm the TTL was set (not -1 = no TTL, not -2 = key absent).
    ttl = await fake_redis.ttl("deploy_idempotency:t1:k1")
    # Allow ±60s wiggle for fakeredis clock; 24h = 86400s.
    assert 86000 < ttl <= 86400


@pytest.mark.asyncio
async def test_redis_job_store_put_get(fake_redis) -> None:
    store = RedisJobStore(fake_redis)
    assert await store.get("j-1") is None
    await store.put(_record())
    fetched = await store.get("j-1")
    assert fetched is not None
    assert fetched.job_id == "j-1"
    assert fetched.status == DeployJobStatus.pending


@pytest.mark.asyncio
async def test_redis_job_store_30d_ttl(fake_redis) -> None:
    store = RedisJobStore(fake_redis)
    await store.put(_record())
    ttl = await fake_redis.ttl("deploy_job:j-1")
    # 30d = 2_592_000s
    assert 2_591_000 < ttl <= 2_592_000


@pytest.mark.asyncio
async def test_redis_stores_survive_service_restart_simulation(fake_redis) -> None:
    """Round-trip across two 'instances' of the store backed by the same Redis."""
    write_store = RedisJobStore(fake_redis)
    await write_store.put(_record(job_id="j-survive"))

    # New store instance — simulates a freshly-restarted API replica.
    read_store = RedisJobStore(fake_redis)
    fetched = await read_store.get("j-survive")
    assert fetched is not None
    assert fetched.job_id == "j-survive"


@pytest.mark.asyncio
async def test_redis_idempotency_survives_restart(fake_redis) -> None:
    write_store = RedisIdempotencyStore(fake_redis)
    await write_store.set("t1", "k-survive", "j-survive")

    read_store = RedisIdempotencyStore(fake_redis)
    assert await read_store.get("t1", "k-survive") == "j-survive"
