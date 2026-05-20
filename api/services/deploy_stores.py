"""Persistence stores for DeployJobService — idempotency map + job records.

Two seams so the service is testable without Redis:

- ``IdempotencyStore`` — ``(team_id, idempotency_key) -> job_id``, 24h TTL.
- ``JobStore`` — ``job_id -> DeployJobRecord``, 30d TTL.

Production uses the Redis-backed implementations (multi-replica + restart-safe).
Tests use the in-memory implementations, which preserve the same semantics in
a single-process dict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    import redis.asyncio as aioredis

    from api.services.deploy_jobs import DeployJobRecord


class IdempotencyStore(Protocol):
    """Maps (team_id, idempotency_key) -> job_id with a 24h TTL."""

    async def get(self, team_id: str, key: str) -> str | None: ...
    async def set(self, team_id: str, key: str, job_id: str) -> None: ...


class JobStore(Protocol):
    """Maps job_id -> DeployJobRecord with a 30d TTL."""

    async def get(self, job_id: str) -> DeployJobRecord | None: ...
    async def put(self, job: DeployJobRecord) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementations (tests, single-process dev)
# ---------------------------------------------------------------------------


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._d: dict[tuple[str, str], str] = {}

    async def get(self, team_id: str, key: str) -> str | None:
        return self._d.get((team_id, key))

    async def set(self, team_id: str, key: str, job_id: str) -> None:
        self._d[(team_id, key)] = job_id


class InMemoryJobStore:
    def __init__(self) -> None:
        self._d: dict[str, DeployJobRecord] = {}

    async def get(self, job_id: str) -> DeployJobRecord | None:
        return self._d.get(job_id)

    async def put(self, job: DeployJobRecord) -> None:
        self._d[job.job_id] = job


# ---------------------------------------------------------------------------
# Redis-backed implementations (production)
# ---------------------------------------------------------------------------

_IDEMPOTENCY_TTL_SECONDS = 24 * 3600  # 24h
_JOB_TTL_SECONDS = 30 * 24 * 3600  # 30d (audit-friendly window)
_IDEMPOTENCY_KEY_TPL = "deploy_idempotency:{team_id}:{key}"
_JOB_KEY_TPL = "deploy_job:{job_id}"


class RedisIdempotencyStore:
    """Stores ``(team_id, key) -> job_id`` in Redis with a 24h TTL.

    Key shape: ``deploy_idempotency:{team_id}:{key}``. The value is the raw
    ``job_id`` string. We rely on Redis's per-key TTL to expire stale entries
    — the spec calls for 24h.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def get(self, team_id: str, key: str) -> str | None:
        return await self._redis.get(_IDEMPOTENCY_KEY_TPL.format(team_id=team_id, key=key))

    async def set(self, team_id: str, key: str, job_id: str) -> None:
        await self._redis.set(
            _IDEMPOTENCY_KEY_TPL.format(team_id=team_id, key=key),
            job_id,
            ex=_IDEMPOTENCY_TTL_SECONDS,
        )


class RedisJobStore:
    """Stores DeployJobRecord JSON in Redis with a 30-day TTL.

    Key shape: ``deploy_job:{job_id}``. Value is ``model_dump_json()``. A 30d
    TTL gives operators a generous window for post-incident debugging without
    keeping records forever — Postgres persistence is a separate follow-up.
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def get(self, job_id: str) -> DeployJobRecord | None:
        # Import here to avoid a circular at module load (deploy_jobs imports
        # back into types defined elsewhere).
        from api.services.deploy_jobs import DeployJobRecord

        raw = await self._redis.get(_JOB_KEY_TPL.format(job_id=job_id))
        if raw is None:
            return None
        return DeployJobRecord.model_validate_json(raw)

    async def put(self, job: DeployJobRecord) -> None:
        await self._redis.set(
            _JOB_KEY_TPL.format(job_id=job.job_id),
            job.model_dump_json(),
            ex=_JOB_TTL_SECONDS,
        )
