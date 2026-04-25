"""MemoryManager — conversation history persistence for AgentBreeder server templates.

This module is copied into agent containers at build time alongside the server templates.
It provides a backend-agnostic interface for loading and saving conversation turns, backed
by either Redis or PostgreSQL depending on the MEMORY_BACKEND environment variable.

Key prefix used for all stored data:
    agentbreeder:memory:{agent_name}:{session_id}

Environment variables consumed:
    MEMORY_BACKEND   — "redis" | "postgresql" | "none"  (default: "none")
    REDIS_URL        — Required when MEMORY_BACKEND=redis
    DATABASE_URL     — Required when MEMORY_BACKEND=postgresql
    AGENT_NAME       — Used as part of the storage key (default: "agent")
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("agentbreeder.memory")

# Type alias for a conversation turn list
MessageList = list[dict[str, Any]]


class MemoryManager:
    """Load and save conversation history for a deployed agent.

    Instances are created once at server startup and shared across requests.
    All public methods are async to keep the server template non-blocking.

    Usage in a server template::

        manager = MemoryManager()
        await manager.connect()

        # Before invoking the agent:
        history = await manager.load(session_id)

        # After the agent responds:
        await manager.save(session_id, updated_messages)
    """

    _KEY_PREFIX = "agentbreeder:memory"

    def __init__(self) -> None:
        self._backend: str = os.getenv("MEMORY_BACKEND", "none").lower()
        self._agent_name: str = os.getenv("AGENT_NAME", "agent")
        ttl_raw = os.getenv("MEMORY_TTL_SECONDS", "0")
        self._ttl_seconds: int = int(ttl_raw) if ttl_raw.isdigit() else 0
        self._redis: Any = None  # aioredis / redis.asyncio client
        self._pg_pool: Any = None  # asyncpg connection pool

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open connections to the configured backend.

        Safe to call even when backend is "none" — does nothing in that case.
        """
        if self._backend == "redis":
            await self._connect_redis()
        elif self._backend == "postgresql":
            await self._connect_postgresql()
        else:
            logger.info("MemoryManager: backend=none — conversation history disabled")

    async def close(self) -> None:
        """Close all open connections."""
        try:
            if self._redis is not None:
                await self._redis.aclose()
                self._redis = None
            if self._pg_pool is not None:
                await self._pg_pool.close()
                self._pg_pool = None
        except Exception:
            logger.exception("MemoryManager: error closing connections")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load(self, session_id: str) -> MessageList:
        """Return prior conversation turns for *session_id*.

        Returns an empty list for an unknown session or when the backend is
        "none".  Never raises — on any backend error it logs and returns [].
        """
        key = self._make_key(session_id)
        try:
            if self._backend == "redis" and self._redis is not None:
                return await self._redis_load(key)
            if self._backend == "postgresql" and self._pg_pool is not None:
                return await self._pg_load(key)
        except Exception:
            logger.exception("MemoryManager.load failed for key=%s", key)
        return []

    async def save(self, session_id: str, messages: MessageList) -> None:
        """Persist *messages* for *session_id*.

        Silently no-ops when the backend is "none" or on any backend error
        (logs the error but does not re-raise, so a persistence failure never
        kills an agent response).
        """
        key = self._make_key(session_id)
        try:
            if self._backend == "redis" and self._redis is not None:
                await self._redis_save(key, messages)
            elif self._backend == "postgresql" and self._pg_pool is not None:
                await self._pg_save(key, messages)
            else:
                logger.debug("MemoryManager.save: no-op (backend=%s)", self._backend)
        except Exception:
            logger.exception("MemoryManager.save failed for key=%s", key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_key(self, session_id: str) -> str:
        return f"{self._KEY_PREFIX}:{self._agent_name}:{session_id}"

    # Redis implementation ------------------------------------------------

    async def _connect_redis(self) -> None:
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            logger.warning(
                "MemoryManager: MEMORY_BACKEND=redis but REDIS_URL is not set — "
                "conversation history disabled"
            )
            return
        try:
            import redis.asyncio as aioredis  # type: ignore[import-untyped]

            self._redis = await aioredis.from_url(redis_url, decode_responses=True)
            logger.info("MemoryManager: connected to Redis at %s", redis_url)
        except ImportError:
            logger.warning(
                "MemoryManager: redis[asyncio] is not installed — conversation history disabled"
            )
        except Exception:
            logger.exception("MemoryManager: failed to connect to Redis")

    async def _redis_load(self, key: str) -> MessageList:
        raw = await self._redis.get(key)
        if raw is None:
            return []
        return json.loads(raw)

    async def _redis_save(self, key: str, messages: MessageList) -> None:
        if self._ttl_seconds > 0:
            await self._redis.set(key, json.dumps(messages), ex=self._ttl_seconds)
        else:
            await self._redis.set(key, json.dumps(messages))

    # PostgreSQL implementation -------------------------------------------

    async def _connect_postgresql(self) -> None:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            logger.warning(
                "MemoryManager: MEMORY_BACKEND=postgresql but DATABASE_URL is not set — "
                "conversation history disabled"
            )
            return
        try:
            import asyncpg  # type: ignore[import-untyped]

            self._pg_pool = await asyncpg.create_pool(dsn=db_url)
            await self._pg_ensure_table()
            logger.info("MemoryManager: connected to PostgreSQL")
        except ImportError:
            logger.warning(
                "MemoryManager: asyncpg is not installed — conversation history disabled"
            )
        except Exception:
            logger.exception("MemoryManager: failed to connect to PostgreSQL")

    async def _pg_ensure_table(self) -> None:
        """Create the conversation history table if it does not exist."""
        async with self._pg_pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agentbreeder_memory (
                    key     TEXT PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

    async def _pg_load(self, key: str) -> MessageList:
        async with self._pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT payload FROM agentbreeder_memory WHERE key = $1", key
            )
            if row is None:
                return []
            payload = row["payload"]
            # asyncpg returns JSONB as a Python object already
            if isinstance(payload, str):
                return json.loads(payload)
            return list(payload)

    async def _pg_save(self, key: str, messages: MessageList) -> None:
        async with self._pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agentbreeder_memory (key, payload, updated_at)
                VALUES ($1, $2::jsonb, NOW())
                ON CONFLICT (key) DO UPDATE
                    SET payload = EXCLUDED.payload,
                        updated_at = NOW()
                """,
                key,
                json.dumps(messages),
            )
            if self._ttl_seconds > 0:
                await conn.execute(
                    "DELETE FROM agentbreeder_memory WHERE key = $1 AND updated_at < NOW() - ($2 || ' seconds')::INTERVAL",
                    key,
                    str(self._ttl_seconds),
                )
