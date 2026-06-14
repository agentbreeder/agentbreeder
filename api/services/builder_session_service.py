"""BuilderSession persistence, the per-session SSE event bus, and (in later
C-tasks) orchestration of interview / eject / deploy turns. Mirrors the
deploy event-bus pattern.

Governance: the coding agent writes into a sandbox; nothing is auto-deployed.
Deploy still flows through the existing /deploys pipeline (Parse -> RBAC ->
Resolve -> Build -> Provision -> Deploy -> Health -> Register)."""

from __future__ import annotations

import asyncio
import contextlib
import json as _jsonlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import BuilderSession
from engine.agent_chat_builder import run_chat_turn_stream


def _json(obj: Any) -> str:
    return _jsonlib.dumps(obj)


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

    async def run_interview_turn(self, sess, provider, user_text: str):
        """Async-generate SSE frames for one interview turn; persists history."""
        state = dict(sess.state or {})
        history = list(state.get("history", []))
        history.append({"role": "user", "content": user_text})

        async for evt in run_chat_turn_stream(provider, history):
            if evt.type == "token":
                yield {"event": "token", "data": _json({"text": evt.text})}
            elif evt.type == "setup_request" and evt.setup is not None:
                yield {"event": "setup_request", "data": _json(asdict(evt.setup))}
            elif evt.type == "done" and evt.result is not None:
                r = evt.result
                if r.agent_yaml:
                    state["agent_yaml"] = r.agent_yaml
                    yield {
                        "event": "spec_update",
                        "data": _json(
                            {"agent_yaml": r.agent_yaml, "valid": r.valid, "errors": r.errors}
                        ),
                    }
                history.append({"role": "assistant", "content": r.assistant_message})
                yield {"event": "done", "data": _json(asdict(r))}

        state["history"] = history
        await self.save_state(sess, state)
        # Durable commit: the streaming response can outlive get_db's commit
        # timing, so flush alone is not enough — persist explicitly here.
        await self._db.commit()
