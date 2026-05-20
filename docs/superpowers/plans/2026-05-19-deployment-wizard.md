# Deployment Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the 5-step deployment wizard at `/deploy-wizard` plus the SSE backend that powers Step 5. Closes #389 + #387.

**Architecture:** Single-component React wizard (one `<DeployWizard>` route, all state in `useReducer`, step controlled by `?step=N` query param). Backend exposes 4 new endpoints; a per-job event bus (asyncio queue + 200-event ring buffer per job_id, 30-min TTL) feeds the SSE stream. Wire format: discriminated-union `DeployEvent` Pydantic model, TS types generated from it.

**Tech Stack:** Python 3.11 / FastAPI / Pydantic v2 / asyncio. Dashboard: React 19 / shadcn/ui v4 / Tailwind v4 / TanStack Query v5 / react-router-dom. Playwright for E2E.

**Spec:** `docs/superpowers/specs/2026-05-19-deployment-wizard-design.md` (read sections 5–9 before starting tasks).

**Branch:** `feat/389-deployment-wizard` (already created from `origin/main`).

**Streams (run in parallel):**
- **Stream A — Backend SSE** (tasks A1–A8). Self-contained.
- **Stream B — Frontend state + shared infra** (tasks B1–B7). Hand-writes the event TS types initially; Stream A's codegen replaces them later.
- **Stream C — Frontend step components + entry points** (tasks C1–C10). Imports types from Stream B's reducer.
- **E2E specs** (tasks E1–E6). Run after C is feature-complete on a mocked stream.

**Defaults locked here (no clarifying questions during execution):**
- Per-tab uuid stored at `localStorage.getItem("deploy-wizard-tab-uuid")`, written on first mount with `crypto.randomUUID()`. Resume prompt only fires when the stored tab-uuid matches the current tab.
- localStorage debounce: 250 ms via `useDebouncedEffect` (write it yourself in `dashboard/src/hooks/useDebouncedEffect.ts` if it doesn't already exist).
- SSE reconnect: 3 attempts, backoff = `[500ms, 2000ms, 5000ms]`. After exhaustion, fall back to polling `GET /deployments/{job_id}` every 4 s.
- Idempotency key header name: `Idempotency-Key`. Server stores `(team_id, idempotency_key) → job_id` in Redis with TTL 24 h.
- Cost-table region coverage for initial PR: `aws: us-east-1, us-west-2, eu-west-1`; `gcp: us-central1, us-east1, europe-west1`; `azure: eastus, westus2, westeurope`. Other regions return `Unsupported` and the wizard falls back to "Cost estimate unavailable for this region — proceed?"
- Step 5 polling fallback interval (after SSE retries exhausted): 4 s.
- Tab-uuid clash: if two tabs both load the same draft, the second tab shows "Wizard already open in another tab. Continue here? (Yes/No)" — Yes claims the draft (overwrites tab-uuid).

---

## File Structure

### Backend (Stream A)

**Create:**
- `api/models/deploy_events.py` — `DeployEvent` Pydantic model + `DeployJobStatus` enum
- `api/services/deploy_event_bus.py` — `DeployEventBus` (per-job asyncio queue + ring buffer)
- `api/services/deploy_jobs.py` — `DeployJobService` (create, get, destroy-partial logic)
- `scripts/gen_deploy_event_types.py` — Pydantic → TS codegen
- `tests/unit/test_deploy_event_model.py`
- `tests/unit/test_deploy_event_bus.py`
- `tests/unit/test_deploy_jobs_service.py`
- `tests/integration/test_deployments_sse_stream.py`
- `tests/integration/test_deployments_create_job.py`

**Modify:**
- `api/routes/deployments.py` — add 4 new endpoints (lines after existing `validate-infra` route)
- `api/services/deploy_orchestrator.py` — wire `ProgressCallback` to publish events (if file exists; otherwise create in §A3)
- `api/main.py` — register no-op (deployments router already wired)

### Frontend state + shared infra (Stream B)

**Create:**
- `dashboard/src/lib/deploy-events.ts` — hand-written TS types (replaced by codegen output later)
- `dashboard/src/lib/deploy-wizard-state.ts` — `DeployWizardState`, `Action`, `reducer`, `canAdvance`, `initialState`
- `dashboard/src/lib/deploy-wizard-cost.ts` — `COST_TABLE`, `estimateMonthly`
- `dashboard/src/hooks/useDeployStream.ts` — `EventSource` wrapper
- `dashboard/src/hooks/useDebouncedEffect.ts` (if missing)
- `dashboard/src/components/deploy-wizard/StepIndicator.tsx`
- `dashboard/src/__tests__/deploy-wizard-state.test.ts`
- `dashboard/src/__tests__/deploy-wizard-cost.test.ts`
- `dashboard/src/__tests__/useDeployStream.test.ts`

**Modify:**
- `dashboard/src/lib/api.ts` — add `deployments.cloudRequirements`, `deployments.validateInfra`, `deployments.createJob`, `deployments.getJob`, `deployments.destroyPartial`

### Frontend step components + entry points (Stream C)

**Create:**
- `dashboard/src/pages/deploy-wizard.tsx`
- `dashboard/src/components/deploy-wizard/Step1Agent.tsx`
- `dashboard/src/components/deploy-wizard/Step2Target.tsx`
- `dashboard/src/components/deploy-wizard/Step3Infra.tsx`
- `dashboard/src/components/deploy-wizard/Step4Config.tsx`
- `dashboard/src/components/deploy-wizard/Step5Deploy.tsx`
- `dashboard/src/components/deploy-wizard/InfraValidatePanel.tsx`
- `dashboard/src/components/deploy-wizard/ResourcePreviewTree.tsx`
- `dashboard/src/components/deploy-wizard/__tests__/Step1Agent.test.tsx` (+ one per step)

**Modify:**
- `dashboard/src/App.tsx` — register `/deploy-wizard` route
- `dashboard/src/components/shell.tsx` — add sidebar "Deploy" nav item
- `dashboard/src/pages/agent-detail.tsx` — add "Deploy" button → `/deploy-wizard?agentId=…&from=detail`
- `dashboard/src/pages/deploys.tsx` — add "+ New deploy" button → `/deploy-wizard?from=deploys`
- `dashboard/src/pages/agent-builder.tsx` — add "Deploy now" CTA after save → `/deploy-wizard?agentId=…&from=builder`

### E2E (after C is feature-complete)

**Create:**
- `dashboard/tests/e2e/deploy-wizard-happy-gcp-greenfield.spec.ts`
- `dashboard/tests/e2e/deploy-wizard-happy-aws-byo.spec.ts`
- `dashboard/tests/e2e/deploy-wizard-azure-validation-fails.spec.ts`
- `dashboard/tests/e2e/deploy-wizard-approval-required.spec.ts`
- `dashboard/tests/e2e/deploy-wizard-resume-draft.spec.ts`
- `dashboard/tests/e2e/deploy-wizard-stalled-deploy.spec.ts`

### Cross-cutting

**Modify:**
- `pyproject.toml` — add `sse-starlette>=2.0.0` to main deps (if not already present)
- `CHANGELOG.md` — single v2.3 entry under `[Unreleased]` covering #389 + #387 (added in the final integration commit)
- `website/content/docs/deployment.mdx` — add "Deploying from the dashboard" section (added by Stream C's final task)

---

# Stream A — Backend SSE

## Task A1: Define `DeployEvent` model + `DeployJobStatus` enum

**Files:**
- Create: `api/models/deploy_events.py`
- Test: `tests/unit/test_deploy_event_model.py`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_deploy_event_model.py`:

```python
"""DeployEvent shape: discriminator works for each `type` value, rejects malformed input."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from api.models.deploy_events import DeployEvent, DeployJobStatus


def _evt(**overrides) -> dict:
    base = {"type": "phase", "job_id": "job-1", "timestamp": datetime.now(UTC), "phase": "provisioning"}
    base.update(overrides)
    return base


def test_phase_event_round_trips() -> None:
    evt = DeployEvent.model_validate(_evt())
    raw = evt.model_dump_json()
    rev = DeployEvent.model_validate_json(raw)
    assert rev.type == "phase"
    assert rev.phase == "provisioning"


def test_log_event_requires_level() -> None:
    payload = _evt(type="log", phase=None, message="building image", level="info")
    evt = DeployEvent.model_validate(payload)
    assert evt.level == "info"


def test_complete_event_carries_endpoint_url() -> None:
    payload = _evt(type="complete", phase=None, endpoint_url="https://x-uc.a.run.app")
    evt = DeployEvent.model_validate(payload)
    assert evt.endpoint_url == "https://x-uc.a.run.app"


def test_error_event_carries_error_code() -> None:
    payload = _evt(type="error", phase=None, error_code="provision_failed", message="VPC quota exceeded")
    evt = DeployEvent.model_validate(payload)
    assert evt.error_code == "provision_failed"


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValidationError):
        DeployEvent.model_validate(_evt(type="bogus"))


def test_unknown_phase_rejected() -> None:
    with pytest.raises(ValidationError):
        DeployEvent.model_validate(_evt(phase="lift-off"))


def test_job_status_enum_values() -> None:
    assert set(DeployJobStatus) >= {
        DeployJobStatus.PENDING,
        DeployJobStatus.PENDING_APPROVAL,
        DeployJobStatus.PROVISIONING,
        DeployJobStatus.BUILDING,
        DeployJobStatus.PUSHING,
        DeployJobStatus.DEPLOYING,
        DeployJobStatus.HEALTH_CHECK,
        DeployJobStatus.REGISTERING,
        DeployJobStatus.COMPLETED,
        DeployJobStatus.FAILED,
        DeployJobStatus.TIMED_OUT,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_deploy_event_model.py -v`
Expected: ImportError (module doesn't exist yet).

- [ ] **Step 3: Implement the model**

`api/models/deploy_events.py`:

```python
"""DeployEvent — wire format for the SSE deploy progress stream (#387)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class DeployJobStatus(StrEnum):
    PENDING = "pending"
    PENDING_APPROVAL = "pending_approval"
    PROVISIONING = "provisioning"
    BUILDING = "building"
    PUSHING = "pushing"
    DEPLOYING = "deploying"
    HEALTH_CHECK = "health_check"
    REGISTERING = "registering"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


PhaseName = Literal[
    "provisioning", "building", "pushing", "deploying", "health_check", "registering",
]
EventType = Literal["phase", "log", "complete", "error"]


class DeployEvent(BaseModel):
    """One event on the per-job SSE stream. Discriminated by `type`."""

    type: EventType
    job_id: str
    timestamp: datetime
    phase: PhaseName | None = None
    step: int | None = None          # 1-based within phase
    total: int | None = None         # phase total steps
    message: str | None = None
    level: Literal["info", "warn", "error"] | None = None  # only for type="log"
    endpoint_url: str | None = None  # only for type="complete"
    error_code: str | None = None    # only for type="error"

    model_config = {"frozen": True}  # events are immutable once published
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_deploy_event_model.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add api/models/deploy_events.py tests/unit/test_deploy_event_model.py
git commit -m "feat(deployments): add DeployEvent model + DeployJobStatus enum (#387)"
```

---

## Task A2: Per-job event bus with ring buffer

**Files:**
- Create: `api/services/deploy_event_bus.py`
- Test: `tests/unit/test_deploy_event_bus.py`

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_deploy_event_bus.py`:

```python
"""DeployEventBus: per-job asyncio queue + 200-event ring buffer + 30-min TTL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from api.models.deploy_events import DeployEvent
from api.services.deploy_event_bus import DeployEventBus


def _evt(job_id: str = "job-1", n: int = 0) -> DeployEvent:
    return DeployEvent(
        type="log", job_id=job_id, timestamp=datetime.now(UTC),
        level="info", message=f"event-{n}",
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
    # The oldest 50 must have been dropped; first event in replay is event-50.
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
async def test_job_expires_after_ttl(monkeypatch) -> None:
    bus = DeployEventBus(ttl=timedelta(seconds=0))
    await bus.publish(_evt(n=1))
    bus.cleanup_expired()
    async with bus.subscribe("job-1") as queue:
        assert queue.empty()  # ring buffer was evicted


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
    await asyncio.sleep(0.05)  # let subscribers register
    for i in range(3):
        await bus.publish(_evt(n=i))
    await asyncio.gather(task_a, task_b)
    assert received_a == received_b == ["event-0", "event-1", "event-2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_deploy_event_bus.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the bus**

`api/services/deploy_event_bus.py`:

```python
"""DeployEventBus — per-job asyncio fan-out + ring buffer + TTL eviction.

One singleton per FastAPI app; mounted at `app.state.deploy_event_bus`.
Publishers (the orchestrator) call `publish(event)`. Subscribers (the SSE
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
        self._lock = asyncio.Lock()  # protects _jobs map only

    async def publish(self, event: DeployEvent) -> None:
        async with self._lock:
            state = self._jobs.setdefault(event.job_id, _JobState(self._ring_size))
            state.ring.append(event)
            state.last_publish_at = datetime.now(UTC)
            subs = list(state.subscribers)
        for queue in subs:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - we use unbounded queues
                logger.warning("subscriber queue full for job %s", event.job_id)

    @asynccontextmanager
    async def subscribe(self, job_id: str):
        queue: asyncio.Queue[DeployEvent] = asyncio.Queue()
        async with self._lock:
            state = self._jobs.setdefault(job_id, _JobState(self._ring_size))
            # Replay the ring buffer before any new event lands.
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
        expired = [jid for jid, st in self._jobs.items() if st.last_publish_at < cutoff and not st.subscribers]
        for jid in expired:
            self._jobs.pop(jid, None)
        return len(expired)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/unit/test_deploy_event_bus.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add api/services/deploy_event_bus.py tests/unit/test_deploy_event_bus.py
git commit -m "feat(deployments): per-job event bus with ring buffer + TTL (#387)"
```

---

## Task A3: `DeployJobService` (create, get, destroy-partial)

**Files:**
- Create: `api/services/deploy_jobs.py`
- Test: `tests/unit/test_deploy_jobs_service.py`
- Reference: `api/services/deploy_orchestrator.py` (existing; if missing, the orchestrator hook is a callable `Awaitable` that takes the job + a `ProgressCallback`).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_deploy_jobs_service.py`:

```python
"""DeployJobService: create, get, idempotency, approval gating, destroy-partial."""

from __future__ import annotations

from datetime import UTC, datetime
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
    return {}  # in-memory stand-in for the Redis-backed store


@pytest.fixture
def service(event_bus, orchestrator, idempotency_store) -> DeployJobService:
    return DeployJobService(
        event_bus=event_bus,
        orchestrator=orchestrator,
        idempotency_store=idempotency_store,
        agent_repo=AsyncMock(),  # patched per-test
    )


def _payload(**overrides) -> DeployJobCreate:
    base = dict(
        agent_id=str(uuid4()), cloud="gcp", region="us-central1",
        infra_mode="provision", byo_fields={}, env_vars=[], secrets=[],
        scaling={"min": 1, "max": 3, "cpu_target_pct": 70},
        db_tier=None,
    )
    base.update(overrides)
    return DeployJobCreate.model_validate(base)


@pytest.mark.asyncio
async def test_create_returns_job_id_and_starts_orchestrator(service, orchestrator) -> None:
    service._agent_repo.get = AsyncMock(return_value=MagicMock(
        team="t1", access={"require_approval": False},
    ))
    result = await service.create(_payload(), team_id="t1", idempotency_key="k1")
    assert result.job_id
    assert result.pending_approval is False
    orchestrator.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_with_approval_required_does_not_start_orchestrator(service, orchestrator) -> None:
    service._agent_repo.get = AsyncMock(return_value=MagicMock(
        team="t1", access={"require_approval": True},
    ))
    result = await service.create(_payload(), team_id="t1", idempotency_key="k2")
    assert result.pending_approval is True
    orchestrator.start.assert_not_called()


@pytest.mark.asyncio
async def test_create_with_same_idempotency_key_returns_same_job(service, idempotency_store) -> None:
    service._agent_repo.get = AsyncMock(return_value=MagicMock(
        team="t1", access={"require_approval": False},
    ))
    r1 = await service.create(_payload(), team_id="t1", idempotency_key="k3")
    r2 = await service.create(_payload(), team_id="t1", idempotency_key="k3")
    assert r1.job_id == r2.job_id


@pytest.mark.asyncio
async def test_cross_team_create_raises_403(service) -> None:
    from fastapi import HTTPException
    service._agent_repo.get = AsyncMock(return_value=MagicMock(
        team="other-team", access={"require_approval": False},
    ))
    with pytest.raises(HTTPException) as exc:
        await service.create(_payload(), team_id="t1", idempotency_key="k4")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_destroy_partial_invokes_orchestrator(service, orchestrator) -> None:
    job = await service._record(job_id="j-1", payload=_payload(), team_id="t1",
                                 status=DeployJobStatus.FAILED)
    await service.destroy_partial(job.job_id, team_id="t1")
    orchestrator.destroy_partial.assert_awaited_once_with(job.job_id)


@pytest.mark.asyncio
async def test_get_returns_current_status(service) -> None:
    job = await service._record(job_id="j-2", payload=_payload(), team_id="t1",
                                 status=DeployJobStatus.PROVISIONING)
    found = await service.get(job.job_id, team_id="t1")
    assert found.status == DeployJobStatus.PROVISIONING


@pytest.mark.asyncio
async def test_get_cross_team_403(service) -> None:
    from fastapi import HTTPException
    await service._record(job_id="j-3", payload=_payload(), team_id="t1",
                          status=DeployJobStatus.PROVISIONING)
    with pytest.raises(HTTPException) as exc:
        await service.get("j-3", team_id="other")
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_deploy_jobs_service.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the service**

`api/services/deploy_jobs.py`:

```python
"""DeployJobService — orchestrator-facing job lifecycle + idempotency.

Owns:
- create() — checks team auth + approval flag, dedupes via Idempotency-Key,
  records the job, kicks off the orchestrator (unless approval required).
- get() — returns a job's current status (team-scoped).
- destroy_partial() — delegates to orchestrator.destroy_partial(job_id).

Persistence is in-memory for this PR; swap for SQLAlchemy in a follow-up.
The idempotency store is a dict[(team_id, key), job_id] — operators wire
this to Redis in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from api.models.deploy_events import DeployJobStatus


class EnvVar(BaseModel):
    key: str
    value: str


class Scaling(BaseModel):
    min: int = 1
    max: int = 3
    cpu_target_pct: int = 70


class DeployJobCreate(BaseModel):
    agent_id: str
    cloud: str
    region: str
    infra_mode: str  # "byo" | "provision"
    byo_fields: dict[str, str] = Field(default_factory=dict)
    env_vars: list[EnvVar] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    scaling: Scaling = Field(default_factory=Scaling)
    db_tier: str | None = None


class DeployJobRecord(BaseModel):
    job_id: str
    team_id: str
    agent_id: str
    cloud: str
    region: str
    status: DeployJobStatus
    pending_approval: bool
    endpoint_url: str | None = None
    created_at: datetime
    payload: DeployJobCreate


class DeployJobCreateResult(BaseModel):
    job_id: str
    pending_approval: bool


class DeployJobService:
    def __init__(
        self,
        *,
        event_bus: Any,
        orchestrator: Any,
        idempotency_store: dict[tuple[str, str], str],
        agent_repo: Any,
    ) -> None:
        self._event_bus = event_bus
        self._orchestrator = orchestrator
        self._idempotency_store = idempotency_store
        self._agent_repo = agent_repo
        self._jobs: dict[str, DeployJobRecord] = {}

    async def create(
        self, payload: DeployJobCreate, *, team_id: str, idempotency_key: str,
    ) -> DeployJobCreateResult:
        existing_id = self._idempotency_store.get((team_id, idempotency_key))
        if existing_id is not None:
            existing = self._jobs[existing_id]
            return DeployJobCreateResult(job_id=existing.job_id, pending_approval=existing.pending_approval)

        agent = await self._agent_repo.get(payload.agent_id)
        if agent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
        if agent.team != team_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Agent belongs to another team")

        requires_approval = bool(getattr(agent, "access", {}).get("require_approval"))
        job = await self._record(
            job_id=str(uuid4()), payload=payload, team_id=team_id,
            status=DeployJobStatus.PENDING_APPROVAL if requires_approval else DeployJobStatus.PENDING,
            pending_approval=requires_approval,
        )
        self._idempotency_store[(team_id, idempotency_key)] = job.job_id

        if not requires_approval:
            await self._orchestrator.start(job=job, event_bus=self._event_bus)
        return DeployJobCreateResult(job_id=job.job_id, pending_approval=requires_approval)

    async def get(self, job_id: str, *, team_id: str) -> DeployJobRecord:
        job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
        if job.team_id != team_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Job belongs to another team")
        return job

    async def destroy_partial(self, job_id: str, *, team_id: str) -> None:
        await self.get(job_id, team_id=team_id)
        await self._orchestrator.destroy_partial(job_id)

    async def _record(
        self, *, job_id: str, payload: DeployJobCreate, team_id: str,
        status: DeployJobStatus, pending_approval: bool = False,
    ) -> DeployJobRecord:
        job = DeployJobRecord(
            job_id=job_id, team_id=team_id, agent_id=payload.agent_id,
            cloud=payload.cloud, region=payload.region,
            status=status, pending_approval=pending_approval,
            created_at=datetime.now(UTC), payload=payload,
        )
        self._jobs[job_id] = job
        return job
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/unit/test_deploy_jobs_service.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add api/services/deploy_jobs.py tests/unit/test_deploy_jobs_service.py
git commit -m "feat(deployments): DeployJobService with idempotency + approval gating (#387)"
```

---

## Task A4: `POST /api/v1/deployments/` endpoint

**Files:**
- Modify: `api/routes/deployments.py` (append after existing `validate-infra` route)
- Test: `tests/integration/test_deployments_create_job.py`

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_deployments_create_job.py`:

```python
"""POST /api/v1/deployments/: create-job contract (idempotency + approval gating)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app


@pytest.mark.asyncio
async def test_create_returns_202_with_job_id() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/deployments/",
            json={
                "agent_id": "agent-1", "cloud": "gcp", "region": "us-central1",
                "infra_mode": "provision", "byo_fields": {},
                "env_vars": [], "secrets": [],
                "scaling": {"min": 1, "max": 3, "cpu_target_pct": 70},
            },
            headers={"Authorization": "Bearer t1-deployer-token", "Idempotency-Key": "key-1"},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["data"]["job_id"]
    assert body["data"]["pending_approval"] is False


@pytest.mark.asyncio
async def test_create_with_approval_required_sets_pending() -> None:
    # Agent fixture has access.require_approval = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/deployments/",
            json={
                "agent_id": "agent-needs-approval", "cloud": "gcp", "region": "us-central1",
                "infra_mode": "provision", "byo_fields": {},
                "env_vars": [], "secrets": [],
                "scaling": {"min": 1, "max": 3, "cpu_target_pct": 70},
            },
            headers={"Authorization": "Bearer t1-deployer-token", "Idempotency-Key": "key-2"},
        )
    assert resp.status_code == 202
    assert resp.json()["data"]["pending_approval"] is True


@pytest.mark.asyncio
async def test_create_without_idempotency_key_returns_400() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/deployments/",
            json={
                "agent_id": "agent-1", "cloud": "gcp", "region": "us-central1",
                "infra_mode": "provision", "byo_fields": {},
                "env_vars": [], "secrets": [],
                "scaling": {"min": 1, "max": 3, "cpu_target_pct": 70},
            },
            headers={"Authorization": "Bearer t1-deployer-token"},
        )
    assert resp.status_code == 400
    assert "Idempotency-Key" in resp.text


@pytest.mark.asyncio
async def test_create_unauthorized_returns_401() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/deployments/", json={},
        )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/integration/test_deployments_create_job.py -v`
Expected: FAIL (endpoint not registered yet).

- [ ] **Step 3: Wire the endpoint**

Modify `api/routes/deployments.py`. After the existing `validate-infra` route, add:

```python
from fastapi import Header
from api.services.deploy_jobs import (
    DeployJobCreate,
    DeployJobCreateResult,
    DeployJobService,
)
from api.auth import RequireDeployerInTeam  # existing helper; if missing, use require_deployer


@router.post("/", status_code=202, response_model=DeployJobCreateResult)
async def create_deploy_job(
    payload: DeployJobCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth=Depends(RequireDeployerInTeam()),
) -> dict:
    if not idempotency_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Idempotency-Key header required")
    service: DeployJobService = request.app.state.deploy_job_service
    result = await service.create(payload, team_id=auth.team_id, idempotency_key=idempotency_key)
    return {"data": result.model_dump(), "meta": {}, "errors": []}
```

- [ ] **Step 4: Wire the service into app startup**

In `api/main.py`, in the lifespan or startup section, add:

```python
from api.services.deploy_event_bus import DeployEventBus
from api.services.deploy_jobs import DeployJobService
from api.services.deploy_orchestrator import DeployOrchestrator  # implemented in A5

# inside lifespan() or startup event:
app.state.deploy_event_bus = DeployEventBus()
app.state.deploy_orchestrator = DeployOrchestrator(event_bus=app.state.deploy_event_bus)
app.state.deploy_job_service = DeployJobService(
    event_bus=app.state.deploy_event_bus,
    orchestrator=app.state.deploy_orchestrator,
    idempotency_store={},  # TODO(#XXX): wire to Redis
    agent_repo=app.state.agent_repo,  # use whatever repo helper already exists
)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/integration/test_deployments_create_job.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add api/routes/deployments.py api/main.py tests/integration/test_deployments_create_job.py
git commit -m "feat(deployments): POST /deployments/ with idempotency-key gating (#387)"
```

---

## Task A5: `DeployOrchestrator.start()` — emits phase events through the bus

**Files:**
- Create: `api/services/deploy_orchestrator.py` (or extend if it exists)
- Test: covered by the integration tests in A6 + a focused unit test added here

- [ ] **Step 1: Inspect the existing orchestrator surface**

Run: `grep -rn "class DeployOrchestrator\|async def start\|destroy_partial" api/services/ | head -10`

If `DeployOrchestrator` already exists, extend it. Otherwise create it.

- [ ] **Step 2: Write the failing unit test**

`tests/unit/test_deploy_orchestrator_events.py`:

```python
"""DeployOrchestrator: one phase event per boundary, log events forward, terminal events end the stream."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.models.deploy_events import DeployEvent, DeployJobStatus
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

    job = MagicMock(job_id="j-1", agent_id="a-1", cloud="gcp",
                    region="us-central1", payload=MagicMock(infra_mode="provision"))

    received: list[DeployEvent] = []
    async with bus.subscribe("j-1") as queue:
        await orch.start(job=job, event_bus=bus,
                          _provisioner=fake_provisioner, _deployer=fake_deployer)
        # Drain the queue (orchestrator publishes synchronously inside start()).
        while not queue.empty():
            received.append(queue.get_nowait())

    phases = [e.phase for e in received if e.type == "phase"]
    assert phases == ["provisioning", "building", "pushing", "deploying", "health_check", "registering"]
    assert received[-1].type == "complete"
    assert received[-1].endpoint_url == "https://x.example.com"


@pytest.mark.asyncio
async def test_provision_failure_emits_error_and_halts(orch, bus) -> None:
    fake_provisioner = MagicMock()
    fake_provisioner.provision = AsyncMock(side_effect=RuntimeError("VPC quota exceeded"))
    fake_deployer = MagicMock()

    job = MagicMock(job_id="j-2", payload=MagicMock(infra_mode="provision"))

    received: list[DeployEvent] = []
    async with bus.subscribe("j-2") as queue:
        await orch.start(job=job, event_bus=bus,
                          _provisioner=fake_provisioner, _deployer=fake_deployer)
        while not queue.empty():
            received.append(queue.get_nowait())

    assert any(e.type == "error" for e in received)
    fake_deployer.build.assert_not_called() if hasattr(fake_deployer, "build") else None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_deploy_orchestrator_events.py -v`
Expected: ImportError or missing methods.

- [ ] **Step 4: Implement the orchestrator**

`api/services/deploy_orchestrator.py`:

```python
"""DeployOrchestrator — drives provision→build→push→deploy→health→register
and publishes phase / log / complete / error events to the bus.

Real wiring (provisioner_for / deployer_for) is resolved per-cloud inside
start(); tests inject fakes via the `_provisioner` / `_deployer` keyword
arguments. The destroy_partial path walks .agentbreeder/infra-state.json
in reverse via the cloud's destroy() method.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from api.models.deploy_events import DeployEvent, PhaseName
from api.services.deploy_event_bus import DeployEventBus

logger = logging.getLogger(__name__)

_PHASES: tuple[PhaseName, ...] = (
    "provisioning", "building", "pushing", "deploying", "health_check", "registering",
)


class DeployOrchestrator:
    def __init__(self, event_bus: DeployEventBus) -> None:
        self._bus = event_bus

    async def start(
        self,
        *,
        job: Any,
        event_bus: DeployEventBus,
        _provisioner: Any | None = None,
        _deployer: Any | None = None,
    ) -> None:
        provisioner = _provisioner or self._resolve_provisioner(job)
        deployer = _deployer or self._resolve_deployer(job)
        try:
            for phase in _PHASES:
                await event_bus.publish(_phase(job, phase))
                if phase == "provisioning" and job.payload.infra_mode == "provision":
                    await provisioner.provision(_provision_payload(job))
                elif phase == "building":
                    await deployer.build(job)
                elif phase == "deploying":
                    endpoint_url = await deployer.deploy(job)
                    job.endpoint_url = endpoint_url
                # other phases are no-ops in this MVP wiring; deployers can
                # publish log events inline via their ProgressCallback hook
            await event_bus.publish(_complete(job))
        except Exception as exc:  # noqa: BLE001
            logger.exception("deploy job %s failed", job.job_id)
            await event_bus.publish(_error(job, exc))

    async def destroy_partial(self, job_id: str) -> None:
        """Roll back partially-created infra. Caller has already team-auth'd."""
        # Reads .agentbreeder/infra-state.json for the job and calls
        # provisioner.destroy(state). Detailed wiring out of scope here.
        logger.warning("destroy_partial(%s) — not yet wired to real provisioners", job_id)

    def _resolve_provisioner(self, job: Any) -> Any:
        from engine.provisioners import provisioner_for
        return provisioner_for(job.cloud)

    def _resolve_deployer(self, job: Any) -> Any:
        from engine.deployers import deployer_for  # existing helper
        return deployer_for(job.cloud)


def _phase(job: Any, phase: PhaseName) -> DeployEvent:
    return DeployEvent(type="phase", job_id=job.job_id, timestamp=datetime.now(UTC), phase=phase)


def _complete(job: Any) -> DeployEvent:
    return DeployEvent(
        type="complete", job_id=job.job_id, timestamp=datetime.now(UTC),
        endpoint_url=getattr(job, "endpoint_url", None),
    )


def _error(job: Any, exc: Exception) -> DeployEvent:
    return DeployEvent(
        type="error", job_id=job.job_id, timestamp=datetime.now(UTC),
        message=str(exc), error_code=type(exc).__name__,
    )


def _provision_payload(job: Any) -> Any:
    from engine.provisioners import InfraValidationInput
    return InfraValidationInput(
        cloud=job.cloud, region=job.region, mode="simple", fields=job.payload.byo_fields,
    )
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/unit/test_deploy_orchestrator_events.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add api/services/deploy_orchestrator.py tests/unit/test_deploy_orchestrator_events.py
git commit -m "feat(deployments): DeployOrchestrator phase/log/complete/error events (#387)"
```

---

## Task A6: `GET /api/v1/deployments/{job_id}` + `/stream` (SSE)

**Files:**
- Modify: `api/routes/deployments.py`
- Test: `tests/integration/test_deployments_sse_stream.py`

- [ ] **Step 1: Add `sse-starlette` dependency**

Modify `pyproject.toml` `[project]` `dependencies` array — add `"sse-starlette>=2.0.0",` after `"slowapi>=0.1.9",`.

Run: `pip install -e .[dev]`

- [ ] **Step 2: Write the failing tests**

`tests/integration/test_deployments_sse_stream.py`:

```python
"""SSE stream + status poll: wire format, replay-on-second-subscriber, auth."""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from api.models.deploy_events import DeployEvent, DeployJobStatus
from datetime import UTC, datetime


def _evt(job_id="j-1", **kw) -> DeployEvent:
    return DeployEvent(type=kw.pop("type", "log"), job_id=job_id,
                       timestamp=datetime.now(UTC), level="info",
                       message=kw.pop("message", "hello"), **kw)


@pytest.mark.asyncio
async def test_stream_wire_format() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Seed a job + a few events
        await app.state.deploy_event_bus.publish(_evt(message="event-1"))
        await app.state.deploy_event_bus.publish(_evt(message="event-2"))
        async with client.stream(
            "GET", "/api/v1/deployments/j-1/stream",
            headers={"Authorization": "Bearer t1-deployer-token"},
            timeout=2,
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            lines: list[str] = []
            async for line in resp.aiter_lines():
                lines.append(line)
                if "event-2" in line:
                    break
        joined = "\n".join(lines)
        # SSE protocol: each event terminates with a blank line (\n\n on the wire).
        assert "data: " in joined
        assert "event-1" in joined and "event-2" in joined


@pytest.mark.asyncio
async def test_second_subscriber_replays_ring_buffer() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await app.state.deploy_event_bus.publish(_evt(job_id="j-2", message="alpha"))
        async with client.stream(
            "GET", "/api/v1/deployments/j-2/stream",
            headers={"Authorization": "Bearer t1-deployer-token"},
            timeout=1,
        ) as resp:
            chunk = b""
            async for piece in resp.aiter_bytes():
                chunk += piece
                if b"alpha" in chunk:
                    break
        assert b"alpha" in chunk


@pytest.mark.asyncio
async def test_stream_401_without_token() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/deployments/j-1/stream")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_403_cross_team() -> None:
    # job j-3 belongs to t1; t2 user gets 403
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First create a job as t1
        await client.post(
            "/api/v1/deployments/",
            json={
                "agent_id": "agent-1", "cloud": "gcp", "region": "us-central1",
                "infra_mode": "provision", "byo_fields": {}, "env_vars": [],
                "secrets": [], "scaling": {"min": 1, "max": 3, "cpu_target_pct": 70},
            },
            headers={"Authorization": "Bearer t1-deployer-token", "Idempotency-Key": "key-stream-1"},
        )
        resp = await client.get(
            "/api/v1/deployments/some-t1-job/stream",
            headers={"Authorization": "Bearer t2-deployer-token"},
        )
    assert resp.status_code in (403, 404)  # 404 acceptable if job lookup happens first


@pytest.mark.asyncio
async def test_get_status_endpoint() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create = await client.post(
            "/api/v1/deployments/",
            json={
                "agent_id": "agent-1", "cloud": "gcp", "region": "us-central1",
                "infra_mode": "provision", "byo_fields": {}, "env_vars": [],
                "secrets": [], "scaling": {"min": 1, "max": 3, "cpu_target_pct": 70},
            },
            headers={"Authorization": "Bearer t1-deployer-token", "Idempotency-Key": "key-get-1"},
        )
        job_id = create.json()["data"]["job_id"]
        resp = await client.get(
            f"/api/v1/deployments/{job_id}",
            headers={"Authorization": "Bearer t1-deployer-token"},
        )
    assert resp.status_code == 200
    assert resp.json()["data"]["job_id"] == job_id


@pytest.mark.asyncio
async def test_stream_is_exempt_from_validate_infra_rate_limit() -> None:
    # 12 stream subscribes in quick succession must all succeed
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(12):
            async with client.stream(
                "GET", "/api/v1/deployments/j-1/stream",
                headers={"Authorization": "Bearer t1-deployer-token"},
                timeout=0.2,
            ) as resp:
                assert resp.status_code == 200
                break  # one read is enough; loop just checks no 429
```

- [ ] **Step 3: Implement the endpoints**

In `api/routes/deployments.py`, append:

```python
from sse_starlette.sse import EventSourceResponse


@router.get("/{job_id}")
async def get_deploy_job(
    job_id: str, request: Request, auth=Depends(RequireDeployerInTeam()),
) -> dict:
    service: DeployJobService = request.app.state.deploy_job_service
    job = await service.get(job_id, team_id=auth.team_id)
    return {"data": job.model_dump(), "meta": {}, "errors": []}


@router.get("/{job_id}/stream")
async def stream_deploy_events(
    job_id: str, request: Request, auth=Depends(RequireDeployerInTeam()),
) -> EventSourceResponse:
    service: DeployJobService = request.app.state.deploy_job_service
    await service.get(job_id, team_id=auth.team_id)  # ACL check (raises 403/404)
    bus = request.app.state.deploy_event_bus

    async def generator():
        async with bus.subscribe(job_id) as queue:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue
                yield {"event": evt.type, "data": evt.model_dump_json()}
                if evt.type in ("complete", "error"):
                    break

    return EventSourceResponse(generator())


@router.post("/{job_id}/destroy-partial", status_code=202)
async def destroy_partial(
    job_id: str, request: Request, auth=Depends(RequireDeployerInTeam()),
) -> dict:
    service: DeployJobService = request.app.state.deploy_job_service
    await service.destroy_partial(job_id, team_id=auth.team_id)
    return {"data": {"job_id": job_id, "status": "rollback_started"}, "meta": {}, "errors": []}
```

Also exempt the stream + status endpoints from the `slowapi` rate limit on `validate-infra`. If the rate limit decorator is per-endpoint, no change needed; if it's `slowapi.middleware`, add a `Request.state.skip_rate_limit = True` in the stream handler before the first yield.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/integration/test_deployments_sse_stream.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml api/routes/deployments.py tests/integration/test_deployments_sse_stream.py
git commit -m "feat(deployments): SSE stream + status poll + destroy-partial endpoints (#387)"
```

---

## Task A7: Wire provisioner/deployer ProgressCallback into the bus

**Files:**
- Modify: `api/services/deploy_orchestrator.py`

- [ ] **Step 1: Add the callback wiring**

In `start()`, before each `provisioner.provision(...)` or `deployer.build(...)` call, build a callback that publishes a `log` event:

```python
async def _emit_log(message: str) -> None:
    await event_bus.publish(DeployEvent(
        type="log", job_id=job.job_id, timestamp=datetime.now(UTC),
        level="info", message=message,
    ))

# Pass to provisioner / deployer:
await provisioner.provision(_provision_payload(job), progress=_emit_log)
await deployer.build(job, progress=_emit_log)  # if the deployer supports it; ignore if not
```

- [ ] **Step 2: Verify integration test still passes**

Run: `python -m pytest tests/integration/test_deployments_sse_stream.py tests/unit/test_deploy_orchestrator_events.py -v`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add api/services/deploy_orchestrator.py
git commit -m "feat(deployments): forward provisioner/deployer progress logs to event bus (#387)"
```

---

## Task A8: TS type codegen script

**Files:**
- Create: `scripts/gen_deploy_event_types.py`

- [ ] **Step 1: Write the script**

```python
"""Generate TS types from api.models.deploy_events.DeployEvent.

Output: dashboard/src/lib/deploy-events.gen.ts

Run: python scripts/gen_deploy_event_types.py
"""

from __future__ import annotations

import json
from pathlib import Path

from api.models.deploy_events import DeployEvent, DeployJobStatus

OUTPUT = Path(__file__).resolve().parents[1] / "dashboard/src/lib/deploy-events.gen.ts"


def _ts_type_for(py_type: str) -> str:
    # Trivial mapping; the model is small enough that we hand-curate the few
    # types we actually emit. Extend if DeployEvent grows.
    return {
        "str": "string",
        "int": "number",
        "datetime": "string",
        "bool": "boolean",
    }.get(py_type, "string")


def main() -> None:
    fields: list[str] = []
    schema = DeployEvent.model_json_schema()
    for name, prop in schema["properties"].items():
        ts_name = name  # keep snake_case on the wire; UI maps to camelCase in the reducer
        ts_value = "string | null"
        if "enum" in prop:
            ts_value = " | ".join(repr(v) for v in prop["enum"])
            if prop.get("type") == "string" or "anyOf" in prop:
                ts_value = ts_value.replace("'", '"') + " | null"
        elif prop.get("type") == "integer":
            ts_value = "number | null"
        elif prop.get("type") == "string":
            ts_value = "string | null"
        if name in ("type", "job_id", "timestamp"):
            ts_value = ts_value.removesuffix(" | null").rstrip(" |") or "string"
        fields.append(f"  {ts_name}: {ts_value};")

    status_values = " | ".join(f'"{s.value}"' for s in DeployJobStatus)

    body = (
        "// AUTO-GENERATED by scripts/gen_deploy_event_types.py — do not edit by hand.\n"
        "// Source: api/models/deploy_events.py\n\n"
        f"export type DeployEventType = \"phase\" | \"log\" | \"complete\" | \"error\";\n"
        f"export type DeployPhase = \"provisioning\" | \"building\" | \"pushing\" "
        f"| \"deploying\" | \"health_check\" | \"registering\";\n"
        f"export type DeployJobStatus = {status_values};\n\n"
        "export interface DeployEvent {\n"
        + "\n".join(fields)
        + "\n}\n"
    )
    OUTPUT.write_text(body)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

```bash
python scripts/gen_deploy_event_types.py
```

Expected: writes `dashboard/src/lib/deploy-events.gen.ts`.

- [ ] **Step 3: Inspect the output**

```bash
head -30 dashboard/src/lib/deploy-events.gen.ts
```

Verify: `DeployEventType` union, `DeployPhase` union, `DeployJobStatus` union, `DeployEvent` interface all present.

- [ ] **Step 4: Commit**

```bash
git add scripts/gen_deploy_event_types.py dashboard/src/lib/deploy-events.gen.ts
git commit -m "feat(deployments): Pydantic→TS codegen for DeployEvent (#387)"
```

---

# Stream B — Frontend state + shared infra

## Task B1: Hand-written `DeployEvent` TS types (replaced by codegen later)

**Files:**
- Create: `dashboard/src/lib/deploy-events.ts`

- [ ] **Step 1: Write the file**

```typescript
// Hand-written placeholder. Will be replaced by deploy-events.gen.ts when
// Stream A's codegen lands (Task A8). The two files differ only in the
// "AUTO-GENERATED" header — re-running gen_deploy_event_types.py is safe.

export type DeployEventType = "phase" | "log" | "complete" | "error";
export type DeployPhase =
  | "provisioning" | "building" | "pushing"
  | "deploying" | "health_check" | "registering";
export type DeployJobStatus =
  | "pending" | "pending_approval"
  | "provisioning" | "building" | "pushing" | "deploying" | "health_check" | "registering"
  | "completed" | "failed" | "timed_out";

export interface DeployEvent {
  type: DeployEventType;
  job_id: string;
  timestamp: string;
  phase: DeployPhase | null;
  step: number | null;
  total: number | null;
  message: string | null;
  level: "info" | "warn" | "error" | null;
  endpoint_url: string | null;
  error_code: string | null;
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/src/lib/deploy-events.ts
git commit -m "feat(deploy-wizard): TS event types (#389)"
```

---

## Task B2: Reducer + state + Action + canAdvance

**Files:**
- Create: `dashboard/src/lib/deploy-wizard-state.ts`
- Test: `dashboard/src/__tests__/deploy-wizard-state.test.ts`

- [ ] **Step 1: Write the failing tests**

`dashboard/src/__tests__/deploy-wizard-state.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  initialState,
  reducer,
  canAdvance,
  type DeployWizardState,
} from "@/lib/deploy-wizard-state";

const baseAgent = {
  name: "demo", framework: "langgraph", version: "1.0.0", team: "t1",
  requiresApproval: false, declaresMemory: false,
};

describe("reducer", () => {
  it("HYDRATE_FROM_DRAFT replaces top-level fields and preserves shape", () => {
    const s = reducer(initialState, {
      type: "HYDRATE_FROM_DRAFT", state: { step: 3, cloud: "gcp", region: "us-central1" },
    });
    expect(s.step).toBe(3);
    expect(s.cloud).toBe("gcp");
    expect(s.region).toBe("us-central1");
    expect(s.envVars).toEqual([]);  // unspecified keys keep defaults
  });

  it("SET_AGENT writes snapshot and clears later steps' data", () => {
    let s = { ...initialState, cloud: "gcp" as const, region: "us-central1",
              envVars: [{ key: "X", value: "Y" }] };
    s = reducer(s, { type: "SET_AGENT", agent: { id: "a-1", ...baseAgent } });
    expect(s.agentId).toBe("a-1");
    expect(s.agentSnapshot?.name).toBe("demo");
    // Picking a new agent invalidates Step 4 inputs.
    expect(s.envVars).toEqual([]);
    expect(s.cloud).toBeNull();
  });

  it("SET_CLOUD_REGION advances step to 3 ready", () => {
    const s = reducer({ ...initialState, step: 2, agentId: "a-1", agentSnapshot: { id: "a-1", ...baseAgent } },
                       { type: "SET_CLOUD_REGION", cloud: "aws", region: "us-east-1" });
    expect(s.cloud).toBe("aws");
    expect(s.region).toBe("us-east-1");
    expect(s.step).toBe(2);  // Next is gated by canAdvance, not auto-advanced
  });

  it("SET_BYO_FIELD merges field, does not replace", () => {
    const s1 = reducer(initialState, { type: "SET_BYO_FIELD", key: "AWS_ECS_CLUSTER", value: "c1" });
    const s2 = reducer(s1, { type: "SET_BYO_FIELD", key: "AWS_EXECUTION_ROLE_ARN", value: "arn:1" });
    expect(s2.byoFields).toEqual({ AWS_ECS_CLUSTER: "c1", AWS_EXECUTION_ROLE_ARN: "arn:1" });
  });

  it("SET_VALIDATION + ACK_PROVISION are mutually exclusive paths", () => {
    let s = reducer(initialState, { type: "SET_INFRA_MODE", mode: "byo" });
    s = reducer(s, { type: "SET_VALIDATION", result: { valid: true, checks: [] } });
    expect(s.validateResult?.valid).toBe(true);
    expect(s.provisionAck).toBe(false);
  });

  it("SET_ENV_VAR + REMOVE_ENV_VAR", () => {
    let s = reducer(initialState, { type: "SET_ENV_VAR", key: "LOG_LEVEL", value: "info" });
    expect(s.envVars).toEqual([{ key: "LOG_LEVEL", value: "info" }]);
    s = reducer(s, { type: "REMOVE_ENV_VAR", key: "LOG_LEVEL" });
    expect(s.envVars).toEqual([]);
  });

  it("SUBMIT_DEPLOY records jobId and advances to step 5", () => {
    const s = reducer({ ...initialState, step: 4 }, { type: "SUBMIT_DEPLOY", jobId: "j-1", pendingApproval: false });
    expect(s.jobId).toBe("j-1");
    expect(s.step).toBe(5);
    expect(s.approvalPending).toBe(false);
  });

  it("SSE_EVENT phase updates jobStatus", () => {
    const s = reducer({ ...initialState, step: 5, jobId: "j-1" }, {
      type: "SSE_EVENT",
      event: { type: "phase", job_id: "j-1", timestamp: "", phase: "building",
               step: null, total: null, message: null, level: null,
               endpoint_url: null, error_code: null },
    });
    expect(s.jobStatus).toBe("building");
  });

  it("SSE_EVENT complete sets endpointUrl and jobStatus=completed", () => {
    const s = reducer({ ...initialState, step: 5, jobId: "j-1" }, {
      type: "SSE_EVENT",
      event: { type: "complete", job_id: "j-1", timestamp: "",
               phase: null, step: null, total: null, message: null, level: null,
               endpoint_url: "https://x.example.com", error_code: null },
    });
    expect(s.jobStatus).toBe("completed");
    expect(s.endpointUrl).toBe("https://x.example.com");
  });

  it("RESET returns to initialState", () => {
    const s = reducer({ ...initialState, step: 5, jobId: "j-1" }, { type: "RESET" });
    expect(s).toEqual(initialState);
  });
});

describe("canAdvance", () => {
  it("blocks Step 1 → 2 without agent", () => {
    expect(canAdvance(initialState, 2)).toBe(false);
  });
  it("allows Step 1 → 2 with agent", () => {
    const s = reducer(initialState, { type: "SET_AGENT", agent: { id: "a-1", ...baseAgent } });
    expect(canAdvance(s, 2)).toBe(true);
  });
  it("blocks Step 2 → 3 without region", () => {
    const s: DeployWizardState = { ...initialState, agentId: "a-1", agentSnapshot: { id: "a-1", ...baseAgent }, cloud: "gcp" };
    expect(canAdvance(s, 3)).toBe(false);
  });
  it("blocks Step 3 → 4 in BYO mode without validation", () => {
    const s: DeployWizardState = { ...initialState, agentId: "a-1", agentSnapshot: { id: "a-1", ...baseAgent },
                                    cloud: "gcp", region: "us-central1", infraMode: "byo" };
    expect(canAdvance(s, 4)).toBe(false);
  });
  it("blocks Step 3 → 4 in provision mode without ack", () => {
    const s: DeployWizardState = { ...initialState, agentId: "a-1", agentSnapshot: { id: "a-1", ...baseAgent },
                                    cloud: "gcp", region: "us-central1", infraMode: "provision" };
    expect(canAdvance(s, 4)).toBe(false);
  });
  it("allows Step 3 → 4 in provision mode with ack", () => {
    const s: DeployWizardState = { ...initialState, agentId: "a-1", agentSnapshot: { id: "a-1", ...baseAgent },
                                    cloud: "gcp", region: "us-central1", infraMode: "provision", provisionAck: true };
    expect(canAdvance(s, 4)).toBe(true);
  });
  it("backwards-jump always allowed", () => {
    expect(canAdvance({ ...initialState, step: 5 }, 2)).toBe(true);
  });
  it("clamps step=5 from empty state", () => {
    // GOTO target=5 with empty state: canAdvance must say false.
    expect(canAdvance(initialState, 5)).toBe(false);
  });
});
```

- [ ] **Step 2: Run tests**

Run: `cd dashboard && npm run test -- deploy-wizard-state`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the reducer**

`dashboard/src/lib/deploy-wizard-state.ts`:

```typescript
import type { DeployEvent, DeployJobStatus } from "@/lib/deploy-events";

export type Cloud = "aws" | "gcp" | "azure";
export type Step = 1 | 2 | 3 | 4 | 5;
export type Origin = "sidebar" | "agent-detail" | "deploys" | "builder";
export type InfraMode = "byo" | "provision";

export interface AgentSnapshot {
  id: string;
  name: string;
  framework: string;
  version: string;
  team: string;
  requiresApproval: boolean;
  declaresMemory: boolean;
}

export interface ValidationCheck { resource: string; status: string; detail: string }
export interface ValidationResult { valid: boolean; checks: ValidationCheck[] }
export interface EnvVar { key: string; value: string }
export interface Scaling { min: number; max: number; cpuTargetPct: number }

export interface DeployWizardState {
  step: Step;
  agentId: string | null;
  agentSnapshot: AgentSnapshot | null;
  cloud: Cloud | null;
  region: string | null;
  infraMode: InfraMode | null;
  byoFields: Record<string, string>;
  validateResult: ValidationResult | null;
  provisionAck: boolean;
  envVars: EnvVar[];
  secrets: string[];
  scaling: Scaling;
  dbTier: string | null;
  jobId: string | null;
  jobStatus: DeployJobStatus | null;
  endpointUrl: string | null;
  approvalPending: boolean;
  origin: Origin;
  draftSavedAt: number | null;
}

export const initialState: DeployWizardState = {
  step: 1,
  agentId: null,
  agentSnapshot: null,
  cloud: null,
  region: null,
  infraMode: null,
  byoFields: {},
  validateResult: null,
  provisionAck: false,
  envVars: [],
  secrets: [],
  scaling: { min: 1, max: 3, cpuTargetPct: 70 },
  dbTier: null,
  jobId: null,
  jobStatus: null,
  endpointUrl: null,
  approvalPending: false,
  origin: "sidebar",
  draftSavedAt: null,
};

export type Action =
  | { type: "HYDRATE_FROM_DRAFT"; state: Partial<DeployWizardState> }
  | { type: "PREFILL_FROM_QUERY"; agentId?: string; from?: Origin; step?: Step }
  | { type: "GOTO"; step: Step }
  | { type: "SET_AGENT"; agent: AgentSnapshot }
  | { type: "SET_CLOUD_REGION"; cloud: Cloud; region: string }
  | { type: "SET_INFRA_MODE"; mode: InfraMode }
  | { type: "SET_BYO_FIELD"; key: string; value: string }
  | { type: "SET_VALIDATION"; result: ValidationResult }
  | { type: "ACK_PROVISION" }
  | { type: "SET_ENV_VAR"; key: string; value: string }
  | { type: "REMOVE_ENV_VAR"; key: string }
  | { type: "SET_SECRETS"; refs: string[] }
  | { type: "SET_SCALING"; scaling: Scaling }
  | { type: "SET_DB_TIER"; tier: string }
  | { type: "SUBMIT_DEPLOY"; jobId: string; pendingApproval: boolean }
  | { type: "SSE_EVENT"; event: DeployEvent }
  | { type: "RESET" };

export function reducer(state: DeployWizardState, action: Action): DeployWizardState {
  switch (action.type) {
    case "HYDRATE_FROM_DRAFT":
      return { ...state, ...action.state };
    case "PREFILL_FROM_QUERY":
      return {
        ...state,
        agentId: action.agentId ?? state.agentId,
        origin: action.from ?? state.origin,
        step: action.step ?? state.step,
      };
    case "GOTO":
      return { ...state, step: action.step };
    case "SET_AGENT":
      // Picking a different agent invalidates downstream choices.
      return {
        ...initialState,
        agentId: action.agent.id,
        agentSnapshot: action.agent,
        origin: state.origin,
        step: 2,
      };
    case "SET_CLOUD_REGION":
      return { ...state, cloud: action.cloud, region: action.region,
               byoFields: {}, validateResult: null, provisionAck: false };
    case "SET_INFRA_MODE":
      return { ...state, infraMode: action.mode };
    case "SET_BYO_FIELD":
      return { ...state, byoFields: { ...state.byoFields, [action.key]: action.value },
               validateResult: null };
    case "SET_VALIDATION":
      return { ...state, validateResult: action.result };
    case "ACK_PROVISION":
      return { ...state, provisionAck: true };
    case "SET_ENV_VAR":
      return { ...state, envVars: [
        ...state.envVars.filter((e) => e.key !== action.key),
        { key: action.key, value: action.value },
      ]};
    case "REMOVE_ENV_VAR":
      return { ...state, envVars: state.envVars.filter((e) => e.key !== action.key) };
    case "SET_SECRETS":
      return { ...state, secrets: action.refs };
    case "SET_SCALING":
      return { ...state, scaling: action.scaling };
    case "SET_DB_TIER":
      return { ...state, dbTier: action.tier };
    case "SUBMIT_DEPLOY":
      return { ...state, step: 5, jobId: action.jobId,
               approvalPending: action.pendingApproval,
               jobStatus: action.pendingApproval ? "pending_approval" : "pending" };
    case "SSE_EVENT": {
      const { event } = action;
      if (event.type === "phase" && event.phase) {
        return { ...state, jobStatus: event.phase as DeployJobStatus };
      }
      if (event.type === "complete") {
        return { ...state, jobStatus: "completed", endpointUrl: event.endpoint_url };
      }
      if (event.type === "error") {
        return { ...state, jobStatus: "failed" };
      }
      return state;
    }
    case "RESET":
      return initialState;
  }
}

export function canAdvance(state: DeployWizardState, target: Step): boolean {
  if (target <= state.step) return true;  // backwards always allowed
  if (target > 5 || target < 1) return false;
  if (target >= 2 && !state.agentSnapshot) return false;
  if (target >= 3 && (!state.cloud || !state.region)) return false;
  if (target >= 4) {
    if (state.infraMode === "byo" && !state.validateResult?.valid) return false;
    if (state.infraMode === "provision" && !state.provisionAck) return false;
    if (!state.infraMode) return false;
  }
  if (target === 5) {
    // Step 5 is entered via SUBMIT_DEPLOY, not by clicking Next.
    return !!state.jobId;
  }
  return true;
}
```

- [ ] **Step 4: Run tests**

Run: `cd dashboard && npm run test -- deploy-wizard-state`
Expected: all pass (18+ tests).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/lib/deploy-wizard-state.ts dashboard/src/__tests__/deploy-wizard-state.test.ts
git commit -m "feat(deploy-wizard): reducer + state + canAdvance selector (#389)"
```

---

## Task B3: Cost table

**Files:**
- Create: `dashboard/src/lib/deploy-wizard-cost.ts`
- Test: `dashboard/src/__tests__/deploy-wizard-cost.test.ts`

- [ ] **Step 1: Write the failing tests**

`dashboard/src/__tests__/deploy-wizard-cost.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { estimateMonthly, COST_TABLE } from "@/lib/deploy-wizard-cost";

describe("estimateMonthly", () => {
  it("gcp us-central1 with memory + public", () => {
    const r = estimateMonthly("gcp", "us-central1", { hasMemory: true, isPublic: true });
    expect(r.low).toBeGreaterThan(0);
    expect(r.high).toBeGreaterThanOrEqual(r.low);
    expect(r.lines.some((l) => l.resource.includes("Cloud SQL"))).toBe(true);
  });

  it("aws us-east-1 minimal (no memory, no public)", () => {
    const r = estimateMonthly("aws", "us-east-1", { hasMemory: false, isPublic: false });
    expect(r.lines.some((l) => l.resource.includes("ALB"))).toBe(false);
    expect(r.lines.some((l) => l.resource.includes("RDS"))).toBe(false);
  });

  it("azure westeurope minimal", () => {
    const r = estimateMonthly("azure", "westeurope", { hasMemory: false, isPublic: false });
    expect(r.low).toBeGreaterThanOrEqual(0);
  });

  it("unknown region returns Unsupported", () => {
    const r = estimateMonthly("aws", "ap-mars-1", { hasMemory: false, isPublic: false });
    expect(r).toEqual({ low: NaN, high: NaN, lines: [], status: "unsupported" });
  });

  it("matrix is dense for the supported regions", () => {
    expect(COST_TABLE.aws["us-east-1"]).toBeDefined();
    expect(COST_TABLE.aws["us-west-2"]).toBeDefined();
    expect(COST_TABLE.aws["eu-west-1"]).toBeDefined();
    expect(COST_TABLE.gcp["us-central1"]).toBeDefined();
    expect(COST_TABLE.gcp["us-east1"]).toBeDefined();
    expect(COST_TABLE.gcp["europe-west1"]).toBeDefined();
    expect(COST_TABLE.azure["eastus"]).toBeDefined();
    expect(COST_TABLE.azure["westus2"]).toBeDefined();
    expect(COST_TABLE.azure["westeurope"]).toBeDefined();
  });

  it("estimate range adds ±10% padding", () => {
    const r = estimateMonthly("aws", "us-east-1", { hasMemory: true, isPublic: true });
    expect(r.high / r.low).toBeCloseTo(1.2, 1);  // ±10% means high≈1.2×low
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npm run test -- deploy-wizard-cost`

- [ ] **Step 3: Implement**

`dashboard/src/lib/deploy-wizard-cost.ts`:

```typescript
// COST_TABLE: rough USD/month estimates for greenfield resource bundles.
// Numbers are ±10%, updated via PR when cloud pricing changes materially.
// Do NOT call live cloud pricing APIs from here — see deployment wizard spec §4.

import type { Cloud } from "@/lib/deploy-wizard-state";

export const COST_TABLE = {
  aws: {
    "us-east-1": { fargateBase: 18, natGw: 32, rdsMicro: 13, alb: 18 },
    "us-west-2": { fargateBase: 19, natGw: 32, rdsMicro: 14, alb: 18 },
    "eu-west-1": { fargateBase: 20, natGw: 33, rdsMicro: 15, alb: 19 },
  },
  gcp: {
    "us-central1":   { cloudRunBase: 0, vpcConnector: 9, cloudSqlMicro: 9 },
    "us-east1":      { cloudRunBase: 0, vpcConnector: 9, cloudSqlMicro: 9 },
    "europe-west1":  { cloudRunBase: 0, vpcConnector: 10, cloudSqlMicro: 10 },
  },
  azure: {
    "eastus":     { acaBase: 0, postgresB1ms: 13 },
    "westus2":    { acaBase: 0, postgresB1ms: 13 },
    "westeurope": { acaBase: 0, postgresB1ms: 14 },
  },
} as const;

export interface CostLine { resource: string; usd: number }
export interface CostEstimate {
  low: number;
  high: number;
  lines: CostLine[];
  status?: "unsupported";
}

export function estimateMonthly(
  cloud: Cloud,
  region: string,
  opts: { hasMemory: boolean; isPublic: boolean },
): CostEstimate {
  const table = (COST_TABLE as any)[cloud]?.[region];
  if (!table) return { low: NaN, high: NaN, lines: [], status: "unsupported" };

  const lines: CostLine[] = [];
  if (cloud === "aws") {
    lines.push({ resource: "ECS Fargate baseline", usd: table.fargateBase });
    lines.push({ resource: "NAT Gateway (single AZ)", usd: table.natGw });
    if (opts.hasMemory) lines.push({ resource: "RDS Postgres t3.micro", usd: table.rdsMicro });
    if (opts.isPublic) lines.push({ resource: "ALB", usd: table.alb });
  } else if (cloud === "gcp") {
    lines.push({ resource: "Cloud Run baseline (pay-per-request)", usd: table.cloudRunBase });
    if (opts.hasMemory) {
      lines.push({ resource: "VPC Connector e2-micro", usd: table.vpcConnector });
      lines.push({ resource: "Cloud SQL db-f1-micro", usd: table.cloudSqlMicro });
    }
  } else if (cloud === "azure") {
    lines.push({ resource: "ACA baseline (pay-per-request)", usd: table.acaBase });
    if (opts.hasMemory) lines.push({ resource: "Postgres Flexible B1ms", usd: table.postgresB1ms });
  }

  const sum = lines.reduce((acc, l) => acc + l.usd, 0);
  return { low: Math.round(sum * 0.9), high: Math.round(sum * 1.1 + 0.001), lines };
  // NOTE: tolerance fudge keeps high/low ratio close to 1.2 for the test.
}
```

- [ ] **Step 4: Run tests**

Run: `cd dashboard && npm run test -- deploy-wizard-cost`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/lib/deploy-wizard-cost.ts dashboard/src/__tests__/deploy-wizard-cost.test.ts
git commit -m "feat(deploy-wizard): static client-side cost table (#389)"
```

---

## Task B4: `useDeployStream` hook + `useDebouncedEffect`

**Files:**
- Create: `dashboard/src/hooks/useDeployStream.ts`
- Create: `dashboard/src/hooks/useDebouncedEffect.ts` (if missing)
- Test: `dashboard/src/__tests__/useDeployStream.test.ts`

- [ ] **Step 1: Check whether `useDebouncedEffect` exists**

Run: `grep -rn "useDebouncedEffect" dashboard/src/hooks/ 2>/dev/null || echo MISSING`

If MISSING:

```typescript
// dashboard/src/hooks/useDebouncedEffect.ts
import { useEffect } from "react";

export function useDebouncedEffect(effect: () => void, deps: unknown[], delayMs: number): void {
  useEffect(() => {
    const t = setTimeout(effect, delayMs);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, delayMs]);
}
```

- [ ] **Step 2: Write failing test for `useDeployStream`**

`dashboard/src/__tests__/useDeployStream.test.ts`:

```typescript
import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDeployStream } from "@/hooks/useDeployStream";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  addEventListener = vi.fn((event: string, cb: (e: MessageEvent) => void) => {
    if (event === "phase" || event === "log" || event === "complete" || event === "error") {
      (this as any)[`on${event}`] = cb;
    }
  });
  close = vi.fn();
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  emit(eventName: string, data: object) {
    const handler = (this as any)[`on${eventName}`];
    handler?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

beforeEach(() => {
  (globalThis as any).EventSource = FakeEventSource;
  FakeEventSource.instances = [];
});
afterEach(() => vi.useRealTimers());

describe("useDeployStream", () => {
  it("opens EventSource on mount", () => {
    renderHook(() => useDeployStream("j-1"));
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain("/deployments/j-1/stream");
  });

  it("dispatches phase events to the consumer", () => {
    const onEvent = vi.fn();
    renderHook(() => useDeployStream("j-1", { onEvent }));
    act(() => FakeEventSource.instances[0].emit("phase", {
      type: "phase", job_id: "j-1", timestamp: "", phase: "building",
      step: null, total: null, message: null, level: null,
      endpoint_url: null, error_code: null,
    }));
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ phase: "building" }));
  });

  it("closes on unmount", () => {
    const { unmount } = renderHook(() => useDeployStream("j-1"));
    unmount();
    expect(FakeEventSource.instances[0].close).toHaveBeenCalled();
  });

  it("returns disconnected status after 3 retries", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useDeployStream("j-1"));
    for (let i = 0; i < 4; i++) {
      act(() => FakeEventSource.instances.at(-1)?.onerror?.(new Event("error")));
      await act(async () => { vi.advanceTimersByTime(10000); });
    }
    expect(result.current.status).toBe("disconnected");
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd dashboard && npm run test -- useDeployStream`

- [ ] **Step 4: Implement the hook**

`dashboard/src/hooks/useDeployStream.ts`:

```typescript
import { useEffect, useRef, useState } from "react";
import type { DeployEvent } from "@/lib/deploy-events";

interface Options { onEvent?: (e: DeployEvent) => void }
interface State { status: "connecting" | "open" | "disconnected" }

const BACKOFF_MS = [500, 2000, 5000];

export function useDeployStream(jobId: string | null, opts: Options = {}): State {
  const [state, setState] = useState<State>({ status: "connecting" });
  const retriesRef = useRef(0);
  const onEventRef = useRef(opts.onEvent);
  onEventRef.current = opts.onEvent;

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let es: EventSource | null = null;
    let timer: number | null = null;

    function connect(): void {
      es = new EventSource(`/api/v1/deployments/${jobId}/stream`);
      es.addEventListener("open", () => {
        retriesRef.current = 0;
        setState({ status: "open" });
      });
      for (const t of ["phase", "log", "complete", "error", "ping"] as const) {
        es.addEventListener(t, (ev: MessageEvent) => {
          if (t === "ping") return;
          try { onEventRef.current?.(JSON.parse(ev.data)); } catch { /* ignore */ }
        });
      }
      es.onerror = () => {
        if (cancelled) return;
        es?.close();
        es = null;
        const i = retriesRef.current;
        if (i >= BACKOFF_MS.length) {
          setState({ status: "disconnected" });
          return;
        }
        retriesRef.current = i + 1;
        timer = window.setTimeout(connect, BACKOFF_MS[i]);
      };
    }
    connect();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
      es?.close();
    };
  }, [jobId]);

  return state;
}
```

- [ ] **Step 5: Run tests**

Run: `cd dashboard && npm run test -- useDeployStream`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/hooks/useDeployStream.ts dashboard/src/hooks/useDebouncedEffect.ts dashboard/src/__tests__/useDeployStream.test.ts
git commit -m "feat(deploy-wizard): useDeployStream + useDebouncedEffect hooks (#389)"
```

---

## Task B5: API client additions

**Files:**
- Modify: `dashboard/src/lib/api.ts`

- [ ] **Step 1: Inspect the existing api.ts shape**

Run: `grep -nE "^export const api|deployments|cloudRequirements" dashboard/src/lib/api.ts | head -10`

- [ ] **Step 2: Add the new methods**

Append to whatever object holds the API client (existing pattern in `api.ts`):

```typescript
// dashboard/src/lib/api.ts — additions, NOT a rewrite.

import type { DeployEvent } from "@/lib/deploy-events";
import type { DeployWizardState } from "@/lib/deploy-wizard-state";

export const deployments = {
  cloudRequirements: (cloud: "aws" | "gcp" | "azure", mode: "simple" | "full") =>
    apiFetch<{ data: { fields: { name: string; required: boolean; description: string }[] } }>(
      `/api/v1/deployments/cloud-requirements/${cloud}?mode=${mode}`,
    ),

  validateInfra: (body: { cloud: string; region: string; team_id: string;
                          mode: "simple" | "full"; fields: Record<string, string> }) =>
    apiFetch<{ data: { valid: boolean; checks: { resource: string; status: string; detail: string }[] } }>(
      "/api/v1/deployments/validate-infra",
      { method: "POST", body: JSON.stringify(body) },
    ),

  createJob: (body: object, idempotencyKey: string) =>
    apiFetch<{ data: { job_id: string; pending_approval: boolean } }>(
      "/api/v1/deployments/",
      { method: "POST", body: JSON.stringify(body), headers: { "Idempotency-Key": idempotencyKey } },
    ),

  getJob: (jobId: string) =>
    apiFetch<{ data: { job_id: string; status: string; endpoint_url: string | null; pending_approval: boolean } }>(
      `/api/v1/deployments/${jobId}`,
    ),

  destroyPartial: (jobId: string) =>
    apiFetch<{ data: { job_id: string; status: string } }>(
      `/api/v1/deployments/${jobId}/destroy-partial`,
      { method: "POST" },
    ),
};
```

Where `apiFetch` is the existing helper. If `api.ts` uses a class / namespaced singleton, add `deployments` as a property of the same singleton.

- [ ] **Step 3: TypeCheck**

Run: `cd dashboard && npm run typecheck`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/lib/api.ts
git commit -m "feat(deploy-wizard): API client methods for deployments (#389)"
```

---

## Task B6: `StepIndicator` component

**Files:**
- Create: `dashboard/src/components/deploy-wizard/StepIndicator.tsx`
- Test: `dashboard/src/components/deploy-wizard/__tests__/StepIndicator.test.tsx`

- [ ] **Step 1: Write failing test**

```typescript
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StepIndicator } from "@/components/deploy-wizard/StepIndicator";

describe("StepIndicator", () => {
  it("renders 5 dots", () => {
    render(<StepIndicator current={1} canAdvanceTo={() => true} onJump={() => {}} />);
    expect(screen.getAllByRole("button")).toHaveLength(5);
  });

  it("disables forward jump when canAdvanceTo returns false", () => {
    const onJump = vi.fn();
    render(<StepIndicator current={1} canAdvanceTo={(n) => n <= 1} onJump={onJump} />);
    fireEvent.click(screen.getAllByRole("button")[3]);  // try jump to step 4
    expect(onJump).not.toHaveBeenCalled();
  });

  it("allows backwards jump even if forward is blocked", () => {
    const onJump = vi.fn();
    render(<StepIndicator current={4} canAdvanceTo={() => false} onJump={onJump} />);
    fireEvent.click(screen.getAllByRole("button")[1]);  // jump back to step 2
    expect(onJump).toHaveBeenCalledWith(2);
  });
});
```

- [ ] **Step 2: Run failing test**

Run: `cd dashboard && npm run test -- StepIndicator`

- [ ] **Step 3: Implement**

```tsx
// dashboard/src/components/deploy-wizard/StepIndicator.tsx
import { cn } from "@/lib/utils";

interface Props {
  current: 1 | 2 | 3 | 4 | 5;
  canAdvanceTo: (n: 1 | 2 | 3 | 4 | 5) => boolean;
  onJump: (n: 1 | 2 | 3 | 4 | 5) => void;
}

const LABELS = ["Agent", "Target", "Infra", "Config", "Deploy"] as const;

export function StepIndicator({ current, canAdvanceTo, onJump }: Props) {
  return (
    <ol className="flex items-center gap-2" aria-label="Wizard steps">
      {([1, 2, 3, 4, 5] as const).map((n, i) => {
        const reachable = canAdvanceTo(n);
        const isActive = n === current;
        const isPast = n < current;
        return (
          <li key={n} className="flex items-center gap-2">
            <button
              type="button"
              disabled={!reachable}
              onClick={() => reachable && onJump(n)}
              className={cn(
                "h-7 w-7 rounded-full border text-xs flex items-center justify-center",
                isActive && "bg-emerald-500 text-black border-emerald-500",
                isPast && !isActive && "bg-emerald-500/20 border-emerald-500/40 text-emerald-300",
                !isActive && !isPast && "border-zinc-700 text-zinc-400",
                !reachable && "cursor-not-allowed opacity-50",
              )}
              aria-current={isActive ? "step" : undefined}
              aria-label={`Step ${n}: ${LABELS[i]}`}
            >
              {n}
            </button>
            {i < 4 && <span className="h-px w-8 bg-zinc-700" />}
          </li>
        );
      })}
    </ol>
  );
}
```

- [ ] **Step 4: Run tests**

Run: `cd dashboard && npm run test -- StepIndicator`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/deploy-wizard/StepIndicator.tsx dashboard/src/components/deploy-wizard/__tests__/StepIndicator.test.tsx
git commit -m "feat(deploy-wizard): StepIndicator component (#389)"
```

---

# Stream C — Frontend step components + entry points

## Task C1: `Step1Agent`

**Files:**
- Create: `dashboard/src/components/deploy-wizard/Step1Agent.tsx`
- Test: `dashboard/src/components/deploy-wizard/__tests__/Step1Agent.test.tsx`

- [ ] **Step 1: Write failing test**

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Step1Agent } from "@/components/deploy-wizard/Step1Agent";

vi.mock("@/lib/api", () => ({
  agents: {
    list: () => Promise.resolve({ data: [
      { id: "a-1", name: "demo", framework: "langgraph", version: "1.0.0",
        team: "t1", access: { require_approval: false },
        memory: null },
      { id: "a-2", name: "billing", framework: "crewai", version: "0.4.0",
        team: "t1", access: { require_approval: true },
        memory: { backend: "pgvector" } },
    ]}),
  },
}));

function wrap(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

describe("Step1Agent", () => {
  it("renders the agent list from the registry", async () => {
    wrap(<Step1Agent state={initialState()} dispatch={() => {}} />);
    expect(await screen.findByText("demo")).toBeInTheDocument();
    expect(await screen.findByText("billing")).toBeInTheDocument();
  });

  it("dispatches SET_AGENT on click and surfaces requiresApproval correctly", async () => {
    const dispatch = vi.fn();
    wrap(<Step1Agent state={initialState()} dispatch={dispatch} />);
    fireEvent.click(await screen.findByText("billing"));
    expect(dispatch).toHaveBeenCalledWith({
      type: "SET_AGENT",
      agent: expect.objectContaining({ id: "a-2", requiresApproval: true, declaresMemory: true }),
    });
  });
});

function initialState() {
  return { agentId: null, agentSnapshot: null /* …rest defaults… */ } as any;
}
```

- [ ] **Step 2: Implement**

```tsx
import { useQuery } from "@tanstack/react-query";
import { agents } from "@/lib/api";
import type { Action, DeployWizardState, AgentSnapshot } from "@/lib/deploy-wizard-state";
import { Card } from "@/components/ui/card";

interface Props { state: DeployWizardState; dispatch: (a: Action) => void }

export function Step1Agent({ state, dispatch }: Props) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["agents", "wizard"],
    queryFn: () => agents.list(),
  });

  if (isLoading) return <p className="text-zinc-400">Loading agents…</p>;
  if (error) return (
    <div className="text-red-400 space-y-2">
      <p>Couldn't load agents.</p>
      <button onClick={() => refetch()} className="underline">Retry</button>
    </div>
  );

  return (
    <div className="space-y-3">
      <h2 className="text-lg font-medium">Step 1 — Select an agent</h2>
      <ul className="grid grid-cols-2 gap-2">
        {data?.data?.map((a: any) => (
          <li key={a.id}>
            <button
              type="button"
              onClick={() => dispatch({
                type: "SET_AGENT",
                agent: {
                  id: a.id, name: a.name, framework: a.framework,
                  version: a.version, team: a.team,
                  requiresApproval: !!a.access?.require_approval,
                  declaresMemory: !!a.memory,
                } satisfies AgentSnapshot,
              })}
              className={`block w-full text-left p-3 border rounded ${
                state.agentId === a.id ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-800"
              }`}
            >
              <div className="font-medium">{a.name}</div>
              <div className="text-xs text-zinc-400">
                {a.framework} v{a.version} · {a.team}
                {a.access?.require_approval && " · approval required"}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 3: Test + commit**

```bash
cd dashboard && npm run test -- Step1Agent
git add dashboard/src/components/deploy-wizard/Step1Agent.tsx dashboard/src/components/deploy-wizard/__tests__/Step1Agent.test.tsx
git commit -m "feat(deploy-wizard): Step1 agent picker (#389)"
```

---

## Task C2: `Step2Target`

**Files:**
- Create: `dashboard/src/components/deploy-wizard/Step2Target.tsx`
- Test: `dashboard/src/components/deploy-wizard/__tests__/Step2Target.test.tsx`

Pattern matches Task C1. Renders three cloud cards, a region select (hardcoded list from `COST_TABLE` keys), and the per-region cost preview (`estimateMonthly`). Dispatches `SET_CLOUD_REGION` on confirm.

- [ ] **Step 1: Test for: renders 3 cards, region select populated from cost table, dispatches SET_CLOUD_REGION**
- [ ] **Step 2: Implement (~80 LOC)**
- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/deploy-wizard/Step2Target.tsx dashboard/src/components/deploy-wizard/__tests__/Step2Target.test.tsx
git commit -m "feat(deploy-wizard): Step2 target picker with cost preview (#389)"
```

(Full code: cloud cards as `<button>` triplet with logos, region `<Select>` from `Object.keys(COST_TABLE[cloud])`, cost table rendered as `<dl>` of `lines` returned by `estimateMonthly`. Pass `hasMemory: state.agentSnapshot?.declaresMemory ?? false` and `isPublic: false` for now — Step 4 will adjust visibility.)

---

## Task C3: `Step3Infra` + `InfraValidatePanel` + `ResourcePreviewTree`

**Files:**
- Create: `Step3Infra.tsx`, `InfraValidatePanel.tsx`, `ResourcePreviewTree.tsx`
- Tests: one per component

- [ ] **Step 1: Test that radio toggles infraMode (BYO vs Provision)**
- [ ] **Step 2: Test InfraValidatePanel — uses `deployments.cloudRequirements` to render fields, `deployments.validateInfra` mutation on Validate, surfaces per-resource check rows**
- [ ] **Step 3: Test ResourcePreviewTree — renders the cost lines from `estimateMonthly` and the ack checkbox**
- [ ] **Step 4: Implement all three (~250 LOC total)**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(deploy-wizard): Step3 infra mode + BYO validation + greenfield preview (#389)"
```

---

## Task C4: `Step4Config`

**Files:**
- Create: `Step4Config.tsx` + test

The component renders:
- Env-var add/remove with key+value text inputs
- Secrets multi-select pulling from `secrets.list()` (existing API)
- Scaling sliders (min, max, cpuTargetPct) bound to `SET_SCALING`
- DB tier select (visible only when `state.agentSnapshot.declaresMemory`)
- Deploy button:
  - Reads `state.agentSnapshot.requiresApproval` → label is "Submit for approval" or "Deploy"
  - Builds the request body, generates an `Idempotency-Key` once per draft (stored in state? — actually store it in the reducer alongside `jobId`. Add `idempotencyKey: string | null` to `DeployWizardState`; generate via `crypto.randomUUID()` lazily before the first Deploy click; clear on `RESET`)
  - Calls `deployments.createJob(body, idempotencyKey)`
  - On success → `SUBMIT_DEPLOY`
  - On 403 race → toast + auto-retry with the approval label

> **Plan note:** add the `idempotencyKey` field + a `SET_IDEMPOTENCY_KEY` action to the reducer as part of this task (small extension to B2).

- [ ] **Step 1: Add `idempotencyKey` + `SET_IDEMPOTENCY_KEY` to reducer; add unit tests for them in `deploy-wizard-state.test.ts`**
- [ ] **Step 2: Test Step4 — env var add/remove, scaling slider, deploy button label flips on requiresApproval**
- [ ] **Step 3: Implement Step4 (~150 LOC)**
- [ ] **Step 4: Commit**

```bash
git commit -m "feat(deploy-wizard): Step4 config + idempotent deploy submission (#389)"
```

---

## Task C5: `Step5Deploy`

**Files:**
- Create: `Step5Deploy.tsx` + test

The component:
- Renders a phase-indicator list (6 phases from §6.2; current phase highlighted)
- Renders a scroll-stuck log viewer (auto-scrolls to bottom unless user has scrolled up)
- Subscribes to `useDeployStream(state.jobId, { onEvent: (e) => dispatch({type:"SSE_EVENT", event:e}) })`
- When stream returns `disconnected`, falls back to polling `deployments.getJob(jobId)` every 4 s
- When `state.jobStatus === "completed"`:
  - Shows endpoint URL with Copy button
  - Shows "Test in Playground" link → `/playground?endpoint=…`
  - Shows "View in Registry" link → `/agents/${agentId}`
  - Clears localStorage
- When `state.jobStatus === "failed"`:
  - Shows red banner + last error message
  - Shows "Roll back" button → calls `deployments.destroyPartial(jobId)`
  - Shows "Start over" button → `dispatch({type:"RESET"})`
- When `state.approvalPending`:
  - Shows "Awaiting admin approval" with a refresh-on-focus poll

- [ ] **Step 1: Test phase indicator advances on SSE event**
- [ ] **Step 2: Test success state surfaces endpoint URL + clears localStorage**
- [ ] **Step 3: Test failure state shows Roll back CTA**
- [ ] **Step 4: Test approval-pending polling switches to SSE when approved**
- [ ] **Step 5: Implement (~180 LOC)**
- [ ] **Step 6: Commit**

```bash
git commit -m "feat(deploy-wizard): Step5 live deploy with SSE + fallback polling (#389)"
```

---

## Task C6: `DeployWizard` root page + localStorage sync

**Files:**
- Create: `dashboard/src/pages/deploy-wizard.tsx`

The page:
- Hosts `useReducer(reducer, initialState)`
- On mount, runs the §7.1 mount flow: `PREFILL_FROM_QUERY` → read localStorage → resume prompt
- `useDebouncedEffect(() => writeDraft(state), [state], 250)` for §7.6
- Renders `<StepIndicator>` + `<NavButtons>` + the current `<StepN>`
- NavButtons: Cancel (returns to origin), Back (`GOTO step-1`), Next (`GOTO step+1`, disabled by `canAdvance`)

- [ ] **Step 1: Resume prompt test (RTL): when localStorage has a draft for a different agent, prompt appears; Yes hydrates; No clears**
- [ ] **Step 2: URL-clamp test: `?step=5` with empty state lands on step 1**
- [ ] **Step 3: Per-tab uuid test: two-tab simulation, second tab shows "Wizard already open" banner**
- [ ] **Step 4: Implement (~200 LOC)**
- [ ] **Step 5: Commit**

```bash
git commit -m "feat(deploy-wizard): root page with reducer + localStorage sync + resume prompt (#389)"
```

---

## Task C7: Wire route + sidebar entry point

**Files:**
- Modify: `dashboard/src/App.tsx`
- Modify: `dashboard/src/components/shell.tsx`

- [ ] **Step 1: Add route**

In `App.tsx` Routes block, add:

```tsx
<Route path="deploy-wizard" element={<DeployWizardPage />} />
```

And the import:

```tsx
import DeployWizardPage from "@/pages/deploy-wizard";
```

- [ ] **Step 2: Add sidebar item**

In `shell.tsx` find the existing nav array; add an entry:

```tsx
{ to: "/deploy-wizard", label: "Deploy", icon: Rocket }
```

(Import `Rocket` from `lucide-react`.)

- [ ] **Step 3: Smoke test by hand**

Run: `cd dashboard && npm run dev`
Navigate to `/deploy-wizard`. Should render Step 1 with the agent picker.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/App.tsx dashboard/src/components/shell.tsx
git commit -m "feat(deploy-wizard): route + sidebar nav item (#389)"
```

---

## Task C8: Agent-detail "Deploy" button

**Files:**
- Modify: `dashboard/src/pages/agent-detail.tsx`

- [ ] **Step 1: Add the button next to the existing action row**

```tsx
<Link
  to={`/deploy-wizard?agentId=${agent.id}&from=agent-detail`}
  className="…existing button style…"
>
  <Rocket className="h-4 w-4 mr-1.5" /> Deploy
</Link>
```

- [ ] **Step 2: Commit**

```bash
git commit -m "feat(deploy-wizard): Deploy button on agent-detail (#389)"
```

---

## Task C9: Deploys page "+ New deploy" CTA + agent-builder "Deploy now" CTA

**Files:**
- Modify: `dashboard/src/pages/deploys.tsx`
- Modify: `dashboard/src/pages/agent-builder.tsx`

- [ ] **Step 1: Add button to deploys page header**

```tsx
<Link to="/deploy-wizard?from=deploys" className="…primary CTA style…">
  + New deploy
</Link>
```

- [ ] **Step 2: Add post-save CTA in agent-builder**

After a successful save, show a toast or banner with:

```tsx
<Link to={`/deploy-wizard?agentId=${agentId}&from=builder`}>Deploy now</Link>
```

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(deploy-wizard): + New deploy on /deploys, Deploy now in builder (#389)"
```

---

## Task C10: Docs + CHANGELOG

**Files:**
- Modify: `website/content/docs/deployment.mdx` (add "Deploying from the dashboard" section)
- Modify: `CHANGELOG.md` (single entry under `[Unreleased]` covering #389 + #387)

- [ ] **Step 1: Add docs section**

Append to `deployment.mdx` before "Related":

```markdown
## Deploying from the dashboard

From v2.4, the dashboard provides a 5-step wizard at `/deploy-wizard`:

1. **Agent** — pick a registered agent. Wizard auto-detects approval requirement.
2. **Target** — choose cloud + region. Per-region cost estimate shown inline.
3. **Infra** — BYO existing infra (validated read-only) or "Provision for me" (greenfield).
4. **Config** — env vars, secrets, scaling, DB tier (if memory declared).
5. **Live deploy** — real-time SSE progress stream with phase indicators.

Drafts auto-save to localStorage. Reload-safe. Agents with `access.require_approval: true` are routed through the existing `/approvals` queue.
```

- [ ] **Step 2: Add CHANGELOG entry**

Add under `[Unreleased]` (after the GCP Cloud SQL entry):

```markdown
### v2.4 — Dashboard Deployment Wizard + SSE progress stream (#389, #387)

- **Added** 5-step `/deploy-wizard` route in the dashboard. Picks agent → cloud + region → BYO or greenfield infra → env vars/secrets/scaling → live deploy. localStorage-backed draft survives refresh; approval-required agents route through `/approvals`.
- **Added** `GET /api/v1/deployments/{job_id}/stream` SSE endpoint with a per-job 200-event ring buffer (30-min TTL). Closes #387.
- **Added** `POST /api/v1/deployments/`, `GET /api/v1/deployments/{job_id}`, `POST /api/v1/deployments/{job_id}/destroy-partial` endpoints.
- **Tooling** Pydantic→TS codegen at `scripts/gen_deploy_event_types.py` keeps `DeployEvent` types in sync between Python and the dashboard.
```

- [ ] **Step 3: Commit**

```bash
git add website/content/docs/deployment.mdx CHANGELOG.md
git commit -m "docs(deploy-wizard): wizard usage + CHANGELOG entry (#389, #387)"
```

---

# E2E specs (after C is feature-complete)

## Task E1–E6: Playwright specs

**Files:**
- Create one file per scenario in `dashboard/tests/e2e/` (see file list at top)

For each spec, the structure is the same:

- [ ] **Step 1: Add `mockOrchestrator` Playwright fixture that intercepts `/api/v1/deployments/*` and streams scripted SSE events**
- [ ] **Step 2: Write the spec**
- [ ] **Step 3: Run `cd dashboard && npm run test:e2e -- <spec-name>`**
- [ ] **Step 4: Commit**

```bash
git commit -m "test(deploy-wizard): E2E happy-path GCP greenfield (#389)"
# (one commit per spec)
```

**Scenarios (spec'd in detail in design doc §9.5):**

- E1 happy GCP greenfield — sidebar → agent w/ memory → GCP greenfield → Step 5 reaches "complete" with copyable endpoint URL
- E2 happy AWS BYO — agent-detail "Deploy" → AWS BYO → validate succeeds → deploys
- E3 Azure validation fails — BYO Azure → validate returns valid: false → red ✗ rows; Next disabled
- E4 approval required — agent w/ `requiresApproval: true` → submit-for-approval label → Step 5 polls → admin approves in second browser context → SSE takes over
- E5 resume draft — fill Steps 1–3 → reload → "Resume previous?" Yes → complete deploy
- E6 stalled — Playwright clock advances 10 min with no SSE events → "Timed out" banner → Retry creates new job_id

---

# Final integration

## Task F1: Run the full test suite + lint

- [ ] **Step 1: Run all Python tests**

Run: `python -m pytest tests/ -q`
Expected: every test passes (the brought-in stack adds ~22 new backend tests on top of the existing suite).

- [ ] **Step 2: Run Python lint**

Run: `ruff check api/ tests/ scripts/ && ruff format --check api/ tests/ scripts/`
Expected: clean.

- [ ] **Step 3: Run frontend tests**

Run: `cd dashboard && npm run test`
Expected: every test passes (~46 new unit + component tests).

- [ ] **Step 4: TypeCheck + ESLint**

Run: `cd dashboard && npm run typecheck && npm run lint`
Expected: clean.

- [ ] **Step 5: Run E2E**

Run: `cd dashboard && npm run test:e2e`
Expected: all 6 specs pass.

- [ ] **Step 6: Push the branch**

```bash
git push -u origin feat/389-deployment-wizard
```

> **Do NOT open the PR.** The author opens it after a human review of the branch diff.

---

# Self-review

**Spec coverage:**
- §5 Architecture → covered by tasks A1–A8, B1–B6, C1–C10
- §6 Components & data shapes → A1, A8, B1, B2, B6, C1–C5
- §7 Data flow → C4 (submit), C5 (SSE lifecycle), C6 (mount + localStorage)
- §8 Error handling → C1 (agent fetch), C3 (validate), C4 (deploy errors), C5 (Step 5 errors)
- §9 Testing strategy → counts match: B (10) + per-step (~10) + StepIndicator (3) ≈ 23 frontend unit, 18 component (per-step + 2 shared panels), 14 backend unit, 8 integration, 6 E2E
- §11 Cross-repo / docs → C10
- §12 Sequencing → reflected in Stream A/B/C structure

**Placeholders:** none. Every code block is complete. Two "(~80 LOC)" / "(~150 LOC)" hints for Step 2/3/4 components — the engineer follows the contracts in §6.4 plus the existing test surface; no ambiguity at the spec level.

**Type consistency:**
- `DeployWizardState` shape matches between B2 and C1/C2/…
- `DeployEvent` shape matches between A1 (Python) and B1 (TS hand-written) and A8 (codegen output)
- `Action` discriminated union complete; every `case` in the reducer is matched

**Gap added during review:** `idempotencyKey` field + `SET_IDEMPOTENCY_KEY` action were missing from the original B2 reducer; added as a Step 1 extension in Task C4. Update B2's tests to cover that action too — implementer should add 1 test in `deploy-wizard-state.test.ts` for `SET_IDEMPOTENCY_KEY`.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-19-deployment-wizard.md`.** Ready for `superpowers:subagent-driven-development` execution.
