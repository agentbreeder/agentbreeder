"""DeployEventBus — per-job asyncio fan-out + ring buffer + TTL eviction.

One singleton per FastAPI app; mounted at app.state.deploy_event_bus.
Publishers (the orchestrator) call publish(event). Subscribers (the SSE
endpoint) use `async with bus.subscribe(job_id) as queue` — they receive
the full ring-buffer replay then live events. The buffer caps at 200
events per job and is evicted 30 min after the last publish.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from api.models.deploy_events import DeployEvent

logger = logging.getLogger(__name__)


class _JobState:
    __slots__ = ("ring", "subscribers", "last_publish_at")

    def __init__(self, ring_size: int) -> None:
        self.ring: deque[DeployEvent] = deque(maxlen=ring_size)
        self.subscribers: list[asyncio.Queue[DeployEvent]] = []
        self.last_publish_at: datetime = datetime.now(UTC)


class DeployEventBus:
    """Lock-free per-job event bus with ring-buffer replay and TTL eviction."""

    def __init__(self, ring_size: int = 200, ttl: timedelta = timedelta(minutes=30)) -> None:
        self._jobs: dict[str, _JobState] = {}
        self._ring_size = ring_size
        self._ttl = ttl
        self._lock = asyncio.Lock()

    async def publish(self, event: DeployEvent) -> None:
        async with self._lock:
            state = self._jobs.setdefault(event.job_id, _JobState(self._ring_size))
            state.ring.append(event)
            state.last_publish_at = datetime.now(UTC)
            subs = list(state.subscribers)
        for queue in subs:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover
                logger.warning("subscriber queue full for job %s", event.job_id)

    @asynccontextmanager
    async def subscribe(self, job_id: str):
        queue: asyncio.Queue[DeployEvent] = asyncio.Queue()
        async with self._lock:
            state = self._jobs.setdefault(job_id, _JobState(self._ring_size))
            for buffered in state.ring:
                queue.put_nowait(buffered)
            state.subscribers.append(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                state = self._jobs.get(job_id)
                if state and queue in state.subscribers:
                    state.subscribers.remove(queue)

    def cleanup_expired(self) -> int:
        """Evict jobs with no publish in the last `ttl`. Called by a periodic task."""
        cutoff = datetime.now(UTC) - self._ttl
        expired = [
            jid for jid, st in self._jobs.items()
            if st.last_publish_at < cutoff and not st.subscribers
        ]
        for jid in expired:
            self._jobs.pop(jid, None)
        return len(expired)
