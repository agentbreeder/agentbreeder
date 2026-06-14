"""BuilderSession persistence, the per-session SSE event bus, and (in later
C-tasks) orchestration of interview / eject / deploy turns. Mirrors the
deploy event-bus pattern.

Governance: the coding agent writes into a sandbox; nothing is auto-deployed.
Deploy still flows through the existing /deploys pipeline (Parse -> RBAC ->
Resolve -> Build -> Provision -> Deploy -> Health -> Register)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import BuilderSession


class SessionEventBus:
    """In-process pub/sub keyed by session id (swap for Redis in cloud, W4)."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, str]]]] = {}

    @contextlib.asynccontextmanager
    async def subscribe(self, sid: str) -> AsyncIterator[asyncio.Queue[dict[str, str]]]:
        q: asyncio.Queue[dict[str, str]] = asyncio.Queue()
        self._subs.setdefault(sid, set()).add(q)
        try:
            yield q
        finally:
            self._subs.get(sid, set()).discard(q)

    async def publish(self, sid: str, event: dict[str, str]) -> None:
        for q in list(self._subs.get(sid, set())):
            await q.put(event)


class BuilderSessionService:
    def __init__(self, db: AsyncSession, bus: SessionEventBus) -> None:
        self._db = db
        self._bus = bus

    async def create(self, *, team: str, user_id: uuid.UUID, engine: str) -> BuilderSession:
        sess = BuilderSession(
            team=team,
            user_id=user_id,
            engine=engine,
            state={
                "history": [],
                "agent_yaml": None,
                "files": {},
                "deploy_job_id": None,
                "satisfied": [],
            },
        )
        self._db.add(sess)
        await self._db.flush()
        return sess

    async def get(self, sid: uuid.UUID, *, team: str) -> BuilderSession | None:
        row = await self._db.get(BuilderSession, sid)
        if row is None or row.team != team:
            return None
        return row

    async def list_for_team(self, team: str) -> list[BuilderSession]:
        res = await self._db.execute(select(BuilderSession).where(BuilderSession.team == team))
        return list(res.scalars().all())

    async def save_state(self, sess: BuilderSession, state: dict[str, Any]) -> None:
        sess.state = state
        await self._db.flush()
