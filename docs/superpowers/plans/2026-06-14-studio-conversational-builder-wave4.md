# Wave 4 — Studio Conversational Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the conversational-builder epic: a managed CloudSandbox so `AGENTBREEDER_SANDBOX=cloud` works, real sandbox-minutes metering in the cloud, a Studio Builder analytics funnel, and a Codex golden-transcript gated e2e.

**Architecture:** CloudSandbox is a thin `Sandbox`-Protocol client over a pluggable `SandboxBackend` (v1 = e2b microVM; `FakeBackend` for unit tests). The OSS eject stream emits `sandbox_seconds` in its terminal `complete` frame; the cloud proxy pre-checks the daily sandbox-minute quota, relays the stream, sniffs `sandbox_seconds`, and charges `ceil(minutes)`. Analytics events land in a dedicated OSS Postgres table behind an ingest endpoint; a Studio "Builder" view renders the funnel + per-engine scorecards using the house CSS-bar idiom (no new chart dep).

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Alembic (OSS + cloud), e2b Python SDK (async), React 18 / TS / Tailwind / shadcn / React Query, pytest, Vitest.

**Design source:** `docs/superpowers/specs/2026-06-14-studio-conversational-builder-wave4-design.md` (read §3–§5 and §11 before starting; the security rails in §11.2 are load-bearing).

**Repos:** `agentbreeder` (OSS, this repo) and `agentbreeder-cloud` (`/Users/rajit/personal-github/agentbreeder-cloud`).

**Scope check:** This plan has 4 independent parts; each produces working, testable software on its own. Part C (analytics) is the heaviest and full-stack — it may be executed as its own session. Recommended order: A → B → C → D (B depends on A's `sandbox_seconds` contract).

---

## File Structure

**Part A — CloudSandbox (OSS)**
- Create `engine/sandbox/backends/__init__.py` — `make_backend_from_env()` factory
- Create `engine/sandbox/backends/base.py` — `SandboxBackend` Protocol
- Create `engine/sandbox/backends/fake.py` — `FakeBackend` (in-memory, unit tests)
- Create `engine/sandbox/backends/e2b.py` — `E2BBackend` (e2b async SDK; gated integration)
- Create `engine/sandbox/cloud.py` — `CloudSandbox` (implements `Sandbox`, guards + caps, delegates)
- Modify `api/services/builder_session_service.py` — flip `_make_sandbox`; add `sandbox_seconds`+`code` to frames
- Test `tests/unit/test_cloud_sandbox.py`

**Part B — Cloud sandbox-minutes metering (`agentbreeder-cloud`)**
- Modify `api/models/tenancy.py:41-43` — add enum values
- Modify `api/config.py` — add minute limits
- Modify `api/services/quota.py` — `amount` param, `peek_count`, `charge_sandbox_minutes`
- Modify `api/routes/builder_sessions.py:175-188` — pre-check + sniff + charge
- Create `alembic/versions/<next>_add_sandbox_minute_scopes.py` — ALTER enum
- Test `tests/test_builder_sessions_metering.py`

**Part C — Analytics funnel (OSS + dashboard)**
- Modify `api/models/database.py` — add `AnalyticsEvent`
- Create `alembic/versions/025_add_analytics_events.py`
- Modify `api/models/schemas.py` — ingest + funnel schemas
- Create `api/routes/analytics.py` — POST ingest, GET funnel
- Modify `api/main.py` — register router
- Modify `dashboard/src/lib/analytics.ts` — expand union + network ingest
- Modify `dashboard/src/lib/api.ts` — `api.analytics`
- Create `dashboard/src/pages/builder-insights.tsx` — the Builder view
- Modify `dashboard/src/App.tsx` + `dashboard/src/components/shell.tsx` — route + nav
- Modify `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx:598` — derive engine
- Tests `tests/integration/test_analytics.py`, `dashboard/src/pages/builder-insights.test.tsx`

**Part D — Codex golden e2e (OSS)**
- Create `tests/e2e/test_builder_eject_codex_e2e.py`

---

# Part A — CloudSandbox

### Task A1: `SandboxBackend` Protocol

**Files:**
- Create: `engine/sandbox/backends/__init__.py`
- Create: `engine/sandbox/backends/base.py`
- Test: `tests/unit/test_cloud_sandbox.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cloud_sandbox.py
"""Unit tests for CloudSandbox + the SandboxBackend abstraction (no network)."""

from __future__ import annotations

import pytest

from engine.sandbox.backends.base import SandboxBackend
from engine.sandbox.backends.fake import FakeBackend


def test_fake_backend_satisfies_protocol():
    assert isinstance(FakeBackend(), SandboxBackend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cloud_sandbox.py::test_fake_backend_satisfies_protocol -v`
Expected: FAIL — `ModuleNotFoundError: engine.sandbox.backends`.

- [ ] **Step 3: Create the package + Protocol**

```python
# engine/sandbox/backends/__init__.py
"""Pluggable execution backends for CloudSandbox.

CloudSandbox is a thin Sandbox-Protocol client; the backend is the substrate
that actually runs code (e2b microVM in prod, FakeBackend in tests). Selected by
AGENTBREEDER_SANDBOX_BACKEND (default 'e2b').
"""

from __future__ import annotations

import os

from engine.sandbox.backends.base import SandboxBackend


def make_backend_from_env() -> SandboxBackend:
    name = os.environ.get("AGENTBREEDER_SANDBOX_BACKEND", "e2b").strip().lower()
    if name == "e2b":
        from engine.sandbox.backends.e2b import E2BBackend

        return E2BBackend()
    if name == "fake":
        from engine.sandbox.backends.fake import FakeBackend

        return FakeBackend()
    raise ValueError(f"unknown AGENTBREEDER_SANDBOX_BACKEND={name!r}")
```

```python
# engine/sandbox/backends/base.py
"""SandboxBackend — the execution substrate behind CloudSandbox.

The backend deals in already-validated, workspace-relative paths; CloudSandbox
applies path containment + size/timeout caps before delegating here. This split
keeps the security boundary in one place (cloud.py) and lets us swap e2b for a
self-hosted Firecracker backend without touching callers.
"""

from __future__ import annotations

import builtins
from typing import Protocol, runtime_checkable

from engine.sandbox.base import ExecResult


@runtime_checkable
class SandboxBackend(Protocol):
    async def start(self) -> None: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def read_file(self, path: str) -> str: ...
    async def list_files(self, directory: str = ".") -> builtins.list[str]: ...
    async def run(self, cmd: builtins.list[str], timeout: float) -> ExecResult: ...
    async def snapshot(self) -> bytes: ...
    async def destroy(self) -> None: ...
```

- [ ] **Step 4: Write `FakeBackend` (test double)**

```python
# engine/sandbox/backends/fake.py
"""In-memory SandboxBackend for unit tests — no network, no subprocess."""

from __future__ import annotations

import builtins
import io
import zipfile

from engine.sandbox.base import ExecResult


class FakeBackend:
    """Records lifecycle; stores files in a dict; run() returns a canned success."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.started = False
        self.destroyed = False
        self.commands: list[list[str]] = []

    async def start(self) -> None:
        self.started = True

    async def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    async def read_file(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def list_files(self, directory: str = ".") -> builtins.list[str]:
        prefix = "" if directory in (".", "") else f"{directory.rstrip('/')}/"
        return sorted(p for p in self.files if p.startswith(prefix))

    async def run(self, cmd: builtins.list[str], timeout: float) -> ExecResult:
        self.commands.append(cmd)
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def snapshot(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, content in sorted(self.files.items()):
                zf.writestr(path, content)
        return buf.getvalue()

    async def destroy(self) -> None:
        self.destroyed = True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_cloud_sandbox.py::test_fake_backend_satisfies_protocol -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add engine/sandbox/backends/ tests/unit/test_cloud_sandbox.py
git commit -m "feat(builder): SandboxBackend abstraction + FakeBackend (W4 A1)"
```

---

### Task A2: `CloudSandbox` with path containment + caps

**Files:**
- Create: `engine/sandbox/cloud.py`
- Test: `tests/unit/test_cloud_sandbox.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/test_cloud_sandbox.py
from engine.sandbox.base import Sandbox
from engine.sandbox.cloud import CloudSandbox, _MAX_FILE_BYTES, _safe_relpath


def test_cloud_sandbox_satisfies_protocol():
    assert isinstance(CloudSandbox(FakeBackend()), Sandbox)


@pytest.mark.parametrize("bad", ["/etc/passwd", "../escape", "a/../../b", "\\abs"])
def test_safe_relpath_rejects_escapes(bad):
    with pytest.raises(ValueError):
        _safe_relpath(bad)


def test_safe_relpath_normalizes():
    assert _safe_relpath("./a/b.py") == "a/b.py"


@pytest.mark.asyncio
async def test_cloud_sandbox_write_read_roundtrip_starts_backend():
    backend = FakeBackend()
    sb = CloudSandbox(backend)
    await sb.write("agent.py", "print('hi')")
    assert backend.started is True
    assert await sb.read("agent.py") == "print('hi')"


@pytest.mark.asyncio
async def test_cloud_sandbox_write_rejects_oversize():
    sb = CloudSandbox(FakeBackend())
    with pytest.raises(ValueError):
        await sb.write("big.txt", "x" * (_MAX_FILE_BYTES + 1))


@pytest.mark.asyncio
async def test_cloud_sandbox_close_records_elapsed_and_destroys():
    backend = FakeBackend()
    sb = CloudSandbox(backend)
    await sb.write("a.py", "1")  # starts backend
    await sb.close()
    assert backend.destroyed is True
    assert sb.elapsed_seconds >= 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_cloud_sandbox.py -v`
Expected: FAIL — `ModuleNotFoundError: engine.sandbox.cloud`.

- [ ] **Step 3: Implement `CloudSandbox`**

```python
# engine/sandbox/cloud.py
"""CloudSandbox — Sandbox Protocol over a managed microVM backend (Wave 4).

SECURITY (design §11.2): this is the single place client-side guards live —
path containment + per-file/byte and exec-timeout caps — applied BEFORE the
backend runs anything. The microVM is defense-in-depth, not a reason to drop
these checks. No long-lived secrets are placed in the workspace.
"""

from __future__ import annotations

import builtins
import time
from pathlib import PurePosixPath

from engine.sandbox.backends.base import SandboxBackend
from engine.sandbox.base import ExecResult

_MAX_EXEC_TIMEOUT = 120.0
_MAX_FILE_BYTES = 1_000_000  # 1 MB per file cap (parity with LocalSandbox)


def _safe_relpath(path: str) -> str:
    """Return a normalized workspace-relative path or raise on escape."""
    if path.startswith("/") or path.startswith("\\"):
        raise ValueError(f"absolute paths are not allowed: {path!r}")
    parts: list[str] = []
    for part in PurePosixPath(path).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"path escapes the sandbox workspace: {path!r}")
        parts.append(part)
    return "/".join(parts)


class CloudSandbox:
    """Sandbox backed by a managed microVM (via a pluggable SandboxBackend)."""

    def __init__(self, backend: SandboxBackend) -> None:
        self._backend = backend
        self._started = False
        self._t0: float | None = None
        self.elapsed_seconds: float = 0.0

    async def _ensure_started(self) -> None:
        if not self._started:
            await self._backend.start()
            self._started = True
            self._t0 = time.monotonic()

    async def write(self, path: str, content: str) -> None:
        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
            raise ValueError(f"file exceeds {_MAX_FILE_BYTES} byte cap: {path!r}")
        await self._ensure_started()
        await self._backend.write_file(_safe_relpath(path), content)

    async def read(self, path: str) -> str:
        await self._ensure_started()
        return await self._backend.read_file(_safe_relpath(path))

    async def list(self, directory: str = ".") -> builtins.list[str]:
        await self._ensure_started()
        norm = _safe_relpath(directory) if directory not in (".", "") else "."
        return await self._backend.list_files(norm)

    async def exec(self, cmd: builtins.list[str], timeout: float = 30.0) -> ExecResult:
        await self._ensure_started()
        return await self._backend.run(cmd, min(timeout, _MAX_EXEC_TIMEOUT))

    async def snapshot(self) -> bytes:
        await self._ensure_started()
        return await self._backend.snapshot()

    async def close(self) -> None:
        if self._t0 is not None:
            self.elapsed_seconds = time.monotonic() - self._t0
        await self._backend.destroy()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_cloud_sandbox.py -v`
Expected: PASS (all CloudSandbox tests green).

- [ ] **Step 5: Commit**

```bash
git add engine/sandbox/cloud.py tests/unit/test_cloud_sandbox.py
git commit -m "feat(builder): CloudSandbox with path containment + caps (W4 A2)"
```

---

### Task A3: `E2BBackend` (e2b async SDK)

> **Verify first:** e2b's async SDK surface changes across versions. Before writing, run
> `python -c "import e2b; help(e2b)"` (after install) and confirm the async class name and the
> `files.write/read/list`, `commands.run`, and `kill` method names. Adjust the calls below to match
> the installed version. This backend is **not** unit-tested (it hits e2b) — it is covered by the
> gated integration test in Step 4 and by Part D's live path.

**Files:**
- Create: `engine/sandbox/backends/e2b.py`
- Modify: `pyproject.toml` (add optional dep)
- Test: `tests/integration/test_e2b_backend.py` (gated)

- [ ] **Step 1: Add the optional dependency**

Add to `pyproject.toml` under `[project.optional-dependencies]` (create the `cloud` extra if absent):

```toml
cloud = [
  "e2b>=1.0,<2.0",
]
```

Run: `pip install -e ".[cloud]"`
Expected: e2b installs.

- [ ] **Step 2: Write the gated integration test**

```python
# tests/integration/test_e2b_backend.py
"""Gated integration test for E2BBackend. Skipped unless E2B_API_KEY is set."""

from __future__ import annotations

import os

import pytest

_KEY = os.environ.get("E2B_API_KEY")
pytestmark = pytest.mark.skipif(not _KEY, reason="E2B_API_KEY not set")


@pytest.mark.asyncio
async def test_e2b_write_read_exec_roundtrip():
    from engine.sandbox.backends.e2b import E2BBackend

    backend = E2BBackend()
    try:
        await backend.start()
        await backend.write_file("hello.txt", "world")
        assert await backend.read_file("hello.txt") == "world"
        res = await backend.run(["echo", "ok"], timeout=30.0)
        assert res.exit_code == 0
    finally:
        await backend.destroy()
```

- [ ] **Step 3: Implement `E2BBackend`**

```python
# engine/sandbox/backends/e2b.py
"""E2BBackend — managed Firecracker microVM via the e2b async SDK (Wave 4).

SECURITY (design §11.2): no long-lived secrets are injected; egress is a
default-deny allowlist (LLM endpoints + PyPI + npm) configured on the e2b
template; CPU/mem/wall-clock caps come from the template + per-call timeouts.
e2b is a third-party sub-processor — requires a signed DPA + privacy-policy
disclosure before GA (see design §11.2 / Open Q #1).
"""

from __future__ import annotations

import builtins
import io
import os
import zipfile

from engine.sandbox.base import ExecResult

# Workspace root inside the microVM.
_WORKDIR = "/home/user/project"
_TEMPLATE = os.environ.get("AGENTBREEDER_E2B_TEMPLATE", "base")


class E2BBackend:
    """Thin adapter over e2b's AsyncSandbox. One microVM per builder session."""

    def __init__(self) -> None:
        self._sbx = None  # type: ignore[var-annotated]

    async def start(self) -> None:
        from e2b import AsyncSandbox  # imported lazily so the dep stays optional

        # NB: verify kwargs against the installed e2b version (see Task A3 note).
        self._sbx = await AsyncSandbox.create(template=_TEMPLATE)
        await self._sbx.files.make_dir(_WORKDIR)

    def _abs(self, path: str) -> str:
        return f"{_WORKDIR}/{path}"

    async def write_file(self, path: str, content: str) -> None:
        await self._sbx.files.write(self._abs(path), content)

    async def read_file(self, path: str) -> str:
        return await self._sbx.files.read(self._abs(path))

    async def list_files(self, directory: str = ".") -> builtins.list[str]:
        base = _WORKDIR if directory in (".", "") else self._abs(directory)
        entries = await self._sbx.files.list(base)
        # Return workspace-relative file paths only.
        out: list[str] = []
        for e in entries:
            full = getattr(e, "path", str(e))
            if getattr(e, "is_dir", False):
                continue
            out.append(full.removeprefix(f"{_WORKDIR}/"))
        return sorted(out)

    async def run(self, cmd: builtins.list[str], timeout: float) -> ExecResult:
        import shlex

        joined = " ".join(shlex.quote(c) for c in cmd)
        try:
            res = await self._sbx.commands.run(
                joined, cwd=_WORKDIR, timeout=int(timeout)
            )
        except Exception as exc:  # e2b raises on non-zero/timeout in some versions
            return ExecResult(stdout="", stderr=str(exc), exit_code=1)
        return ExecResult(
            stdout=getattr(res, "stdout", "") or "",
            stderr=getattr(res, "stderr", "") or "",
            exit_code=int(getattr(res, "exit_code", 0) or 0),
        )

    async def snapshot(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in await self.list_files("."):
                zf.writestr(rel, await self.read_file(rel))
        return buf.getvalue()

    async def destroy(self) -> None:
        if self._sbx is not None:
            await self._sbx.kill()
            self._sbx = None
```

- [ ] **Step 4: Run the gated test (skips without a key)**

Run: `pytest tests/integration/test_e2b_backend.py -v`
Expected: SKIPPED (no `E2B_API_KEY`). With a key set: PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/sandbox/backends/e2b.py tests/integration/test_e2b_backend.py pyproject.toml
git commit -m "feat(builder): E2BBackend microVM adapter + gated test (W4 A3)"
```

---

### Task A4: Flip `_make_sandbox` + emit `sandbox_seconds`/`code`

**Files:**
- Modify: `api/services/builder_session_service.py:25-43` and `:132-167`
- Test: `tests/unit/test_builder_session_service_sandbox.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_builder_session_service_sandbox.py
"""_make_sandbox selection + eject frame contract (W4)."""

from __future__ import annotations

import json

import pytest

from api.services import builder_session_service as svc
from engine.sandbox.cloud import CloudSandbox
from engine.sandbox.local import LocalSandbox


def test_make_sandbox_local(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "local")
    sb = svc._make_sandbox()
    assert isinstance(sb, LocalSandbox)


def test_make_sandbox_cloud_returns_cloud_sandbox(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "cloud")
    monkeypatch.setenv("AGENTBREEDER_SANDBOX_BACKEND", "fake")
    sb = svc._make_sandbox()
    assert isinstance(sb, CloudSandbox)


def test_make_sandbox_disabled_raises(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "disabled")
    with pytest.raises(svc.CloudSandboxUnavailable):
        svc._make_sandbox()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_builder_session_service_sandbox.py -v`
Expected: FAIL — cloud branch still raises `CloudSandboxUnavailable`.

- [ ] **Step 3: Update imports + `_make_sandbox`**

In `api/services/builder_session_service.py`, change the imports block (lines 24-26) to add `time`, `CloudSandbox`, the backend factory, and the `Sandbox` type:

```python
import time
...
from engine.sandbox.backends import make_backend_from_env
from engine.sandbox.base import Sandbox, select_sandbox_mode
from engine.sandbox.cloud import CloudSandbox
from engine.sandbox.local import LocalSandbox
```

Replace `_make_sandbox` (lines 37-43) with:

```python
def _make_sandbox() -> Sandbox:
    mode = select_sandbox_mode()
    if mode == "local":
        return LocalSandbox()
    if mode == "cloud":
        return CloudSandbox(make_backend_from_env())
    raise CloudSandboxUnavailable("Sandbox is disabled (AGENTBREEDER_SANDBOX=disabled)")
```

- [ ] **Step 4: Add `sandbox_seconds` + `code` to the eject frames**

In `run_eject` (lines 132-167), capture a start time and enrich the `done`/`error` frames. Replace the loop body's `done`/`error` arms:

```python
    async def run_eject(self, sess, provider, instruction: str, engine_name: str):
        """Run the coding agent in a sandbox; stream events; persist files."""
        sandbox = _make_sandbox()
        state = dict(sess.state or {})
        for path, content in (state.get("files") or {}).items():
            await sandbox.write(path, content)
        if state.get("agent_yaml"):
            await sandbox.write("agent.yaml", state["agent_yaml"])

        engine = engine_for(engine_name, provider=provider)
        t0 = time.monotonic()
        try:
            async for evt in engine.run(instruction, state.get("history", []), sandbox):
                if evt.type == "token":
                    yield {"event": "token", "data": _json({"text": evt.text})}
                elif evt.type == "tool_call":
                    yield {"event": "tool_call", "data": _json({"tool": evt.tool_name})}
                elif evt.type == "file_change":
                    try:
                        content = await sandbox.read(evt.path)
                    except FileNotFoundError:
                        content = ""
                    yield {
                        "event": "file_change",
                        "data": _json(
                            {"path": evt.path, "diff": evt.diff, "content": content}
                        ),
                    }
                elif evt.type == "done":
                    yield {
                        "event": "complete",
                        "data": _json(
                            {
                                "summary": evt.text,
                                "code": "ok",
                                "sandbox_seconds": round(time.monotonic() - t0, 3),
                            }
                        ),
                    }
                elif evt.type == "error":
                    yield {
                        "event": "error",
                        "data": _json({"detail": evt.error, "code": "engine_error"}),
                    }
            state["files"] = {p: await sandbox.read(p) for p in await sandbox.list(".")}
            await self.save_state(sess, state)
            await self._db.commit()
        finally:
            await sandbox.close()
```

- [ ] **Step 5: Add a frame-contract test**

```python
# append to tests/unit/test_builder_session_service_sandbox.py
from unittest.mock import AsyncMock, MagicMock

from engine.coding_agent.base import AgentEvent


class _FakeEngine:
    name = "claude"

    async def run(self, instruction, history, sandbox, bounds=None):
        yield AgentEvent(type="done", text="built it")


@pytest.mark.asyncio
async def test_eject_complete_frame_has_code_and_sandbox_seconds(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "cloud")
    monkeypatch.setenv("AGENTBREEDER_SANDBOX_BACKEND", "fake")
    monkeypatch.setattr(svc, "engine_for", lambda name, provider: _FakeEngine())

    db = AsyncMock()
    service = svc.BuilderSessionService(db, svc.SessionEventBus())
    sess = MagicMock()
    sess.state = {"history": [], "files": {}, "agent_yaml": None}

    frames = [f async for f in service.run_eject(sess, provider=None,
                                                 instruction="x", engine_name="claude")]
    complete = [f for f in frames if f["event"] == "complete"][0]
    payload = json.loads(complete["data"])
    assert payload["code"] == "ok"
    assert "sandbox_seconds" in payload and payload["sandbox_seconds"] >= 0.0
```

- [ ] **Step 6: Run all Part A tests**

Run: `pytest tests/unit/test_cloud_sandbox.py tests/unit/test_builder_session_service_sandbox.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/services/builder_session_service.py tests/unit/test_builder_session_service_sandbox.py
git commit -m "feat(builder): enable cloud sandbox + emit sandbox_seconds/code frames (W4 A4)"
```

---

# Part B — Cloud sandbox-minutes metering (`agentbreeder-cloud`)

> All paths in Part B are under `/Users/rajit/personal-github/agentbreeder-cloud`. Commit in that repo.

### Task B1: Quota scope enum + minute limits + migration

**Files:**
- Modify: `api/models/tenancy.py:41-43`
- Modify: `api/config.py`
- Create: `alembic/versions/<next>_add_sandbox_minute_scopes.py`
- Test: `tests/test_quota_sandbox_minutes.py`

- [ ] **Step 1: Add enum values**

In `api/models/tenancy.py`, extend `QuotaScopeEnum` (lines 41-43):

```python
class QuotaScopeEnum(enum.StrEnum):
    user = "user"
    tenant = "tenant"
    user_sandbox_minutes = "user_sandbox_minutes"
    tenant_sandbox_minutes = "tenant_sandbox_minutes"
```

- [ ] **Step 2: Add config limits**

In `api/config.py`, next to `user_daily_free_limit` / `tenant_daily_free_limit` (lines 19-20), add:

```python
    user_daily_sandbox_minutes_limit: int = 30
    tenant_daily_sandbox_minutes_limit: int = 30
```

- [ ] **Step 3: Create the migration (ALTER enum — additive)**

> Find the current head: `alembic heads`. Use that as `down_revision`. Postgres `ADD VALUE` cannot
> run inside a transaction, so set `op.execute` with autocommit per the snippet.

```python
# alembic/versions/<next>_add_sandbox_minute_scopes.py
"""Add sandbox-minute scopes to quota_scope_enum (W4).

Revision ID: <next>
Revises: <current_head>
Create Date: 2026-06-14
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "<next>"
down_revision: str | None = "<current_head>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW = ("user_sandbox_minutes", "tenant_sandbox_minutes")


def upgrade() -> None:
    # ADD VALUE must run outside a transaction block.
    with op.get_context().autocommit_block():
        for value in _NEW:
            op.execute(f"ALTER TYPE quota_scope_enum ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres cannot drop enum values; downgrade is a no-op (additive-only).
    pass
```

- [ ] **Step 4: Commit (schema)**

```bash
git add api/models/tenancy.py api/config.py alembic/versions/
git commit -m "feat(cloud): sandbox-minute quota scopes + limits + migration (W4 B1)"
```

---

### Task B2: Quota service — amount, peek, charge

**Files:**
- Modify: `api/services/quota.py`
- Test: `tests/test_quota_sandbox_minutes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quota_sandbox_minutes.py
"""Sandbox-minute metering: increment-by-N, peek, charge + 429 path."""

from __future__ import annotations

import uuid

import pytest

from api.services import quota


@pytest.mark.asyncio
async def test_charge_sandbox_minutes_over_limit_raises(db_session):
    uid, tid = uuid.uuid4(), uuid.uuid4()
    # 31 minutes against a 30/day limit → QuotaExceeded on user scope.
    with pytest.raises(quota.QuotaExceeded) as ei:
        await quota.charge_sandbox_minutes(
            db_session, user_id=uid, tenant_id=tid, minutes=31,
            user_limit=30, tenant_limit=30,
        )
    assert ei.value.scope == "user_sandbox_minutes"


@pytest.mark.asyncio
async def test_peek_count_zero_before_any_charge(db_session):
    uid = uuid.uuid4()
    assert await quota.peek_count(db_session, scope="user_sandbox_minutes", scope_id=uid) == 0
```

> Uses the cloud repo's existing `db_session` fixture (ephemeral Postgres — see other tests in
> `tests/`). If absent, mirror the session fixture from `tests/test_builder_sessions.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/pytest tests/test_quota_sandbox_minutes.py -v`
Expected: FAIL — `charge_sandbox_minutes`/`peek_count` undefined.

- [ ] **Step 3: Extend `quota.py`**

Add an `amount` param to `_increment_one`, and add `peek_count` + `charge_sandbox_minutes`:

```python
async def _increment_one(
    session: AsyncSession,
    *,
    scope: str,
    scope_id: uuid.UUID,
    amount: int = 1,
) -> int:
    sql = text(
        """
        INSERT INTO quota_counters (id, scope, scope_id, day, count)
        VALUES (gen_random_uuid(), :scope, :scope_id, CURRENT_DATE, :amount)
        ON CONFLICT (scope, scope_id, day)
        DO UPDATE SET count = quota_counters.count + :amount
        RETURNING count
        """
    )
    res = await session.execute(
        sql, {"scope": scope, "scope_id": scope_id, "amount": amount}
    )
    return int(res.scalar_one())


async def peek_count(
    session: AsyncSession, *, scope: str, scope_id: uuid.UUID
) -> int:
    """Current day's count for a scope without incrementing (for pre-checks)."""
    sql = text(
        """
        SELECT count FROM quota_counters
        WHERE scope = :scope AND scope_id = :scope_id AND day = CURRENT_DATE
        """
    )
    res = await session.execute(sql, {"scope": scope, "scope_id": scope_id})
    row = res.scalar_one_or_none()
    return int(row) if row is not None else 0


async def charge_sandbox_minutes(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    minutes: int,
    user_limit: int,
    tenant_limit: int,
) -> QuotaResult:
    """Add `minutes` to the per-day user+tenant sandbox-minute counters; raise if over."""
    user_count = await _increment_one(
        session, scope="user_sandbox_minutes", scope_id=user_id, amount=minutes
    )
    tenant_count = await _increment_one(
        session, scope="tenant_sandbox_minutes", scope_id=tenant_id, amount=minutes
    )
    if user_count > user_limit:
        raise QuotaExceeded("user_sandbox_minutes", user_limit)
    if tenant_count > tenant_limit:
        raise QuotaExceeded("tenant_sandbox_minutes", tenant_limit)
    return QuotaResult(user_count=user_count, tenant_count=tenant_count)
```

Keep the existing `increment_and_check` unchanged (turn metering still uses post-increment-by-1).

- [ ] **Step 4: Run to verify it passes**

Run: `venv/bin/pytest tests/test_quota_sandbox_minutes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/quota.py tests/test_quota_sandbox_minutes.py
git commit -m "feat(cloud): quota peek + charge_sandbox_minutes (increment-by-N) (W4 B2)"
```

---

### Task B3: Eject route — pre-check + sniff + charge

**Files:**
- Modify: `api/routes/builder_sessions.py` (imports, `eject_session`, add helpers)
- Test: `tests/test_builder_sessions_metering.py`

- [ ] **Step 1: Write the failing test (respx + ASGITransport, per E1 pattern)**

```python
# tests/test_builder_sessions_metering.py
"""Eject sandbox-minute metering: pre-check 429 + post-stream charge."""

from __future__ import annotations

import math

import pytest

from api.routes import builder_sessions as bs


def test_extract_sandbox_seconds_from_complete_frame():
    frame = 'event: complete\ndata: {"summary": "done", "code": "ok", "sandbox_seconds": 95.4}'
    assert bs._extract_sandbox_seconds(frame) == 95.4


def test_extract_sandbox_seconds_ignores_non_complete():
    assert bs._extract_sandbox_seconds('event: token\ndata: {"text": "hi"}') is None


def test_minutes_rounds_up():
    assert math.ceil(95.4 / 60) == 2
```

> A full SSE-relay metering test (respx mock of the OSS upstream + asserting a 2-minute charge on
> the cloud DB) should mirror `tests/test_builder_sessions.py`'s respx + ASGITransport setup. Add it
> after the unit helpers pass.

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/pytest tests/test_builder_sessions_metering.py -v`
Expected: FAIL — `_extract_sandbox_seconds` undefined.

- [ ] **Step 3: Add the frame parser + minute-charge helpers**

In `api/routes/builder_sessions.py`, add imports and helpers:

```python
import json
import math
import uuid as _uuid

from api.db import get_sessionmaker  # verify export name; see Step 3 note
from api.services.quota import (
    QuotaExceeded,
    charge_sandbox_minutes,
    increment_and_check,
    peek_count,
)
```

> **Verify:** confirm how a fresh session is obtained outside a request (the post-stream charge runs
> after the request session may have closed). Grep `api/db.py` for the sessionmaker/`async_session`
> export and use it in `_charge_after_stream`. If only `get_session` (a generator dep) exists, add a
> thin `get_sessionmaker()` returning the `async_sessionmaker`.

```python
def _extract_sandbox_seconds(frame: str) -> float | None:
    """Return sandbox_seconds from an SSE 'complete' frame, else None."""
    if "event: complete" not in frame:
        return None
    for line in frame.splitlines():
        if line.startswith("data:"):
            try:
                payload = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                return None
            val = payload.get("sandbox_seconds")
            return float(val) if val is not None else None
    return None


async def _sandbox_minutes_precheck(db: AsyncSession, user: CloudUser, tenant: Tenant) -> None:
    """429 before opening the stream if the daily sandbox-minute cap is already met."""
    s = get_settings()
    user_used = await peek_count(db, scope="user_sandbox_minutes", scope_id=user.id)
    tenant_used = await peek_count(db, scope="tenant_sandbox_minutes", scope_id=tenant.id)
    if user_used >= s.user_daily_sandbox_minutes_limit:
        raise HTTPException(
            status_code=429,
            detail=f"user_sandbox_minutes daily quota exceeded "
            f"(limit={s.user_daily_sandbox_minutes_limit})",
        )
    if tenant_used >= s.tenant_daily_sandbox_minutes_limit:
        raise HTTPException(
            status_code=429,
            detail=f"tenant_sandbox_minutes daily quota exceeded "
            f"(limit={s.tenant_daily_sandbox_minutes_limit})",
        )


async def _charge_after_stream(user_id: _uuid.UUID, tenant_id: _uuid.UUID, seconds: float) -> None:
    """Charge ceil(minutes) on a fresh session once the stream has fully relayed.

    NB(W4): idempotency across network retries is deferred — each completed eject genuinely
    consumes minutes. A retry-keyed idempotency store is a documented follow-up (design §11.2).
    """
    if seconds <= 0:
        return
    minutes = math.ceil(seconds / 60)
    s = get_settings()
    maker = get_sessionmaker()
    async with maker() as db:
        try:
            await charge_sandbox_minutes(
                db, user_id=user_id, tenant_id=tenant_id, minutes=minutes,
                user_limit=s.user_daily_sandbox_minutes_limit,
                tenant_limit=s.tenant_daily_sandbox_minutes_limit,
            )
            await db.commit()
        except QuotaExceeded:
            # Over-cap on the trailing charge: the work already ran; record the overage and move on.
            await db.commit()
```

- [ ] **Step 4: Rewrite `eject_session` to pre-check, relay-with-sniff, then charge**

Replace `eject_session` (lines 175-188) with a custom streaming relay (the generic `_forward_stream`
can't sniff frames):

```python
@router.post("/{session_id}/eject")
async def eject_session(
    session_id: str,
    body: dict[str, Any],
    user: CloudUser = Depends(require_user),
    tenant: Tenant = Depends(require_tenant),
    db: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Eject to code (SSE). Metered: one turn unit + actual sandbox-minutes."""
    await _meter_turn(db, user, tenant)
    await _sandbox_minutes_precheck(db, user, tenant)

    s = get_settings()
    url = f"{s.agentbreeder_api_url.rstrip('/')}/api/v1/builder/sessions/{session_id}/eject"
    user_id, tenant_id = user.id, tenant.id

    async def gen() -> AsyncIterator[bytes]:
        buffer = ""
        seconds = 0.0
        async with (
            httpx.AsyncClient(timeout=None) as client,
            client.stream("POST", url, json=body, headers=_auth_headers()) as upstream,
        ):
            async for chunk in upstream.aiter_raw():
                yield chunk
                buffer += chunk.decode("utf-8", "replace")
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    found = _extract_sandbox_seconds(frame)
                    if found is not None:
                        seconds = found
        await _charge_after_stream(user_id, tenant_id, seconds)

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 5: Run Part B tests + lint**

Run: `venv/bin/pytest tests/test_builder_sessions_metering.py tests/test_quota_sandbox_minutes.py -v && ruff check api/`
Expected: PASS, no lint errors (combine nested `async with` per SIM117 — already done above).

- [ ] **Step 6: Commit**

```bash
git add api/routes/builder_sessions.py tests/test_builder_sessions_metering.py
git commit -m "feat(cloud): meter eject sandbox-minutes (pre-check + sniff + charge) (W4 B3)"
```

---

# Part C — Analytics funnel (OSS + dashboard)

> Backend paths under `agentbreeder` (this repo). The view reuses the house CSS-bar idiom — **no new
> chart dependency** (design §11.3). Events are PII-free + structural-only (design §11.2/§11.4).

### Task C1: `AnalyticsEvent` model + migration

**Files:**
- Modify: `api/models/database.py`
- Create: `alembic/versions/025_add_analytics_events.py`
- Test: `tests/unit/test_analytics_model.py`

- [ ] **Step 1: Add the model**

Append to `api/models/database.py`:

```python
class AnalyticsEvent(Base):
    """Structural product-analytics event (W4). PII-free: no message/prompt bodies."""

    __tablename__ = "analytics_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    engine: Mapped[str | None] = mapped_column(String(20), nullable=True)
    team: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    props: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
```

- [ ] **Step 2: Create the migration**

```python
# alembic/versions/025_add_analytics_events.py
"""Add analytics_events table (W4 funnel).

Revision ID: 025
Revises: 024
Create Date: 2026-06-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "analytics_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("engine", sa.String(20), nullable=True),
        sa.Column("team", sa.String(100), nullable=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=True),
        sa.Column("props", sa.JSON(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_analytics_events_event", "analytics_events", ["event"])
    op.create_index("ix_analytics_events_team", "analytics_events", ["team"])
    op.create_index("ix_analytics_events_created_at", "analytics_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("analytics_events")
```

- [ ] **Step 3: Test the model imports + apply migration**

```python
# tests/unit/test_analytics_model.py
from api.models.database import AnalyticsEvent


def test_analytics_event_table_name():
    assert AnalyticsEvent.__tablename__ == "analytics_events"
```

Run: `pytest tests/unit/test_analytics_model.py -v && alembic upgrade head`
Expected: PASS; migration applies cleanly.

- [ ] **Step 4: Commit**

```bash
git add api/models/database.py alembic/versions/025_add_analytics_events.py tests/unit/test_analytics_model.py
git commit -m "feat(builder): analytics_events table + migration (W4 C1)"
```

---

### Task C2: Schemas + ingest/funnel routes

**Files:**
- Modify: `api/models/schemas.py`
- Create: `api/routes/analytics.py`
- Modify: `api/main.py` (register)
- Test: `tests/integration/test_analytics.py`

- [ ] **Step 1: Add schemas**

Append to `api/models/schemas.py`:

```python
class AnalyticsEventIngest(BaseModel):
    event: str = Field(..., min_length=1, max_length=64)
    engine: str | None = None
    session_id: str | None = None
    props: dict[str, Any] = Field(default_factory=dict)


class FunnelStage(BaseModel):
    key: str
    label: str
    count: int
    dropoff_pct: float  # vs previous stage; 0.0 for the first


class EngineScorecard(BaseModel):
    engine: str
    samples: int
    spec_validity_rate: float
    deploy_success_rate: float
    turns_to_spec: float
    hallucinated_field_rate: float


class FunnelMetrics(BaseModel):
    period: str
    time_to_first_deploy_p50_s: float | None = None
    time_to_first_deploy_p90_s: float | None = None
    stages: list[FunnelStage] = Field(default_factory=list)
    engines: list[EngineScorecard] = Field(default_factory=list)
```

- [ ] **Step 2: Write the failing integration test**

```python
# tests/integration/test_analytics.py
"""Analytics ingest + funnel aggregation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def _override_db():
    from api.database import get_db
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    yield
    app.dependency_overrides.pop(get_db, None)


def test_ingest_rejects_unknown_event(client, _override_db):
    r = client.post("/api/v1/analytics/events",
                    json={"event": "x" * 100})  # too long
    assert r.status_code == 422


def test_funnel_requires_auth(client):
    r = client.get("/api/v1/analytics/funnel?period=7d")
    assert r.status_code in (401, 403)
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/integration/test_analytics.py -v`
Expected: FAIL — route not registered (404).

- [ ] **Step 4: Implement the route**

```python
# api/routes/analytics.py
"""/api/v1/analytics/* — product funnel ingest + aggregation (W4).

PII rule (design §11.2): events are structural only. The ingest endpoint stores
event/engine/team/session_id/props; callers must never send message or prompt
bodies. A retention job (TTL) prunes old rows (see ops runbook).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import AnalyticsEvent, User
from api.models.schemas import (
    AnalyticsEventIngest,
    ApiResponse,
    FunnelMetrics,
    FunnelStage,
)

router = APIRouter(prefix="/api/v1", tags=["analytics"])

# Macro-stages shown in the headline funnel (design §11.4 — collapse the 11 raw events).
_FUNNEL: list[tuple[str, str]] = [
    ("builder_session_started", "Converse"),
    ("spec_validated", "Spec validated"),
    ("eject_to_code_started", "Eject"),
    ("deploy_started", "Deploy"),
    ("deploy_succeeded", "Live"),
]

_PERIODS = {"7d": 7, "30d": 30, "all": 3650}


@router.post("/analytics/events", status_code=201)
async def ingest_event(
    body: AnalyticsEventIngest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    row = AnalyticsEvent(
        event=body.event,
        engine=body.engine,
        team=getattr(user, "team", None),
        session_id=uuid.UUID(body.session_id) if body.session_id else None,
        props=body.props or {},
    )
    db.add(row)
    await db.commit()
    return ApiResponse(data={"id": str(row.id)})


@router.get("/analytics/funnel")
async def get_funnel(
    period: str = Query("7d"),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[FunnelMetrics]:
    days = _PERIODS.get(period, 7)
    since = datetime.now(timezone.utc) - timedelta(days=days)

    counts: dict[str, int] = {}
    for key, _label in _FUNNEL:
        res = await db.execute(
            select(func.count())
            .select_from(AnalyticsEvent)
            .where(AnalyticsEvent.event == key, AnalyticsEvent.created_at >= since)
        )
        counts[key] = int(res.scalar_one())

    stages: list[FunnelStage] = []
    prev: int | None = None
    for key, label in _FUNNEL:
        c = counts[key]
        dropoff = 0.0 if prev in (None, 0) else round((1 - c / prev) * 100, 1)
        stages.append(FunnelStage(key=key, label=label, count=c, dropoff_pct=dropoff))
        prev = c

    metrics = FunnelMetrics(period=period, stages=stages, engines=[])
    return ApiResponse(data=metrics)
```

> Per-engine scorecards + p50/p90 time-to-first-deploy are computed in Task C3 (they need a join
> across `eval_runs` / timing props); C2 ships the funnel stages so the view has data immediately.

- [ ] **Step 5: Register the router**

In `api/main.py`, add `analytics` to the routes import and add near the other `include_router` calls (around line 233):

```python
app.include_router(analytics.router)
```

- [ ] **Step 6: Run to verify it passes**

Run: `pytest tests/integration/test_analytics.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/models/schemas.py api/routes/analytics.py api/main.py tests/integration/test_analytics.py
git commit -m "feat(builder): analytics ingest + funnel endpoint (W4 C2)"
```

---

### Task C3: Per-engine scorecards + p50/p90

**Files:**
- Modify: `api/routes/analytics.py` (`get_funnel`)
- Test: `tests/unit/test_funnel_metrics.py`

- [ ] **Step 1: Write the failing test (pure helper)**

```python
# tests/unit/test_funnel_metrics.py
from api.routes.analytics import _percentile, _scorecard_from_rows


def test_percentile_p50_p90():
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(data, 50) == 30.0
    assert _percentile(data, 90) == 50.0


def test_percentile_empty_is_none():
    assert _percentile([], 50) is None


def test_scorecard_rates():
    # 4 sessions: 3 valid specs, 2 deploy successes, avg 5 turns, 1 hallucination
    sc = _scorecard_from_rows(
        engine="claude", samples=4, valid=3, deployed=2, total_turns=20, hallucinated=1
    )
    assert sc.spec_validity_rate == 0.75
    assert sc.deploy_success_rate == 0.5
    assert sc.turns_to_spec == 5.0
    assert sc.hallucinated_field_rate == 0.25
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_funnel_metrics.py -v`
Expected: FAIL — helpers undefined.

- [ ] **Step 3: Add the helpers + wire into `get_funnel`**

Add to `api/routes/analytics.py`:

```python
from api.models.schemas import EngineScorecard


def _percentile(values: list[float], pct: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _scorecard_from_rows(
    *, engine: str, samples: int, valid: int, deployed: int,
    total_turns: int, hallucinated: int,
) -> EngineScorecard:
    rate = lambda n: round(n / samples, 4) if samples else 0.0  # noqa: E731
    return EngineScorecard(
        engine=engine,
        samples=samples,
        spec_validity_rate=rate(valid),
        deploy_success_rate=rate(deployed),
        turns_to_spec=round(total_turns / samples, 2) if samples else 0.0,
        hallucinated_field_rate=rate(hallucinated),
    )
```

Then in `get_funnel`, replace `engines=[]` by aggregating `coding_agent_turn` counts + `deploy_succeeded`
per `engine`, and compute p50/p90 from any `deploy_succeeded` events carrying a
`props["time_to_deploy_s"]`. Minimal wiring:

```python
    # p50/p90 time-to-first-deploy from deploy_succeeded props
    res = await db.execute(
        select(AnalyticsEvent.props).where(
            AnalyticsEvent.event == "deploy_succeeded",
            AnalyticsEvent.created_at >= since,
        )
    )
    times = [
        float(p["time_to_deploy_s"])
        for (p,) in res.all()
        if isinstance(p, dict) and p.get("time_to_deploy_s") is not None
    ]
    metrics = FunnelMetrics(
        period=period,
        stages=stages,
        engines=[],  # full per-engine joins are a follow-on; helpers above are unit-covered
        time_to_first_deploy_p50_s=_percentile(times, 50),
        time_to_first_deploy_p90_s=_percentile(times, 90),
    )
    return ApiResponse(data=metrics)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/unit/test_funnel_metrics.py tests/integration/test_analytics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/analytics.py tests/unit/test_funnel_metrics.py
git commit -m "feat(builder): funnel p50/p90 + engine scorecard helpers (W4 C3)"
```

---

### Task C4: Frontend — analytics seam + api client

**Files:**
- Modify: `dashboard/src/lib/analytics.ts`
- Modify: `dashboard/src/lib/api.ts`
- Modify: `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx:598`
- Test: `dashboard/src/lib/analytics.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// dashboard/src/lib/analytics.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { track, ANALYTICS_EVENTS } from "./analytics";

describe("analytics seam", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
  });

  it("includes the full funnel taxonomy", () => {
    expect(ANALYTICS_EVENTS).toContain("builder_session_started");
    expect(ANALYTICS_EVENTS).toContain("first_invoke");
  });

  it("POSTs the event to the ingest endpoint", () => {
    track("eject_to_code_started", { engine: "codex" });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/analytics/events"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd dashboard && npx vitest run src/lib/analytics.test.ts`
Expected: FAIL — `ANALYTICS_EVENTS` not exported; no fetch.

- [ ] **Step 3: Expand `analytics.ts` (network ingest, PII-free)**

```typescript
// dashboard/src/lib/analytics.ts
/** Product funnel analytics (W4). track() POSTs a structural, PII-free event to the
 *  ingest endpoint and also dispatches a CustomEvent for any in-page listener.
 *  NEVER pass message/prompt bodies in props (design §11.2). */
import { ingestAnalytics } from "@/lib/api";

export const ANALYTICS_EVENTS = [
  "builder_session_started",
  "user_message_sent",
  "stack_recommended",
  "setup_card_shown",
  "setup_card_completed",
  "spec_validated",
  "eject_to_code_started",
  "coding_agent_turn",
  "deploy_started",
  "deploy_succeeded",
  "deploy_failed",
  "first_invoke",
] as const;

export type AnalyticsEvent = (typeof ANALYTICS_EVENTS)[number];

export function track(event: AnalyticsEvent, props: Record<string, unknown> = {}): void {
  // Fire-and-forget network ingest (never block UI; swallow failures).
  void ingestAnalytics(event, props).catch(() => {});
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new CustomEvent("agentbreeder:analytics", { detail: { event, props } }));
  }
}
```

- [ ] **Step 4: Add `api.analytics` + `ingestAnalytics` to `api.ts`**

Add the funnel types near the other types, the `ingestAnalytics` export, and the `analytics`
namespace inside the `api` object (mirror `builderSessions`):

```typescript
// types
export interface FunnelStage { key: string; label: string; count: number; dropoff_pct: number; }
export interface EngineScorecard {
  engine: string; samples: number; spec_validity_rate: number;
  deploy_success_rate: number; turns_to_spec: number; hallucinated_field_rate: number;
}
export interface FunnelMetrics {
  period: string;
  time_to_first_deploy_p50_s: number | null;
  time_to_first_deploy_p90_s: number | null;
  stages: FunnelStage[];
  engines: EngineScorecard[];
}

// standalone helper (used by analytics.ts to avoid a circular import on the `api` object)
export function ingestAnalytics(event: string, props: Record<string, unknown>): Promise<unknown> {
  return request("/analytics/events", {
    method: "POST",
    body: JSON.stringify({ event, engine: props.engine ?? null, props }),
  });
}

// inside the `api` object:
analytics: {
  funnel: (period: string) => request<FunnelMetrics>(`/analytics/funnel?period=${period}`),
},
```

- [ ] **Step 5: Fix the hardcoded engine prop**

In `dashboard/src/components/agent-wizard/ChatBuildPanel.tsx:598`, change
`track("eject_to_code_started", { engine: "claude" })` to use the active engine
(the panel already has the session's engine in scope — use that variable, e.g. `session.engine`):

```typescript
track("eject_to_code_started", { engine: session.engine });
```

- [ ] **Step 6: Run to verify it passes**

Run: `cd dashboard && npx vitest run src/lib/analytics.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/lib/analytics.ts dashboard/src/lib/api.ts dashboard/src/components/agent-wizard/ChatBuildPanel.tsx dashboard/src/lib/analytics.test.ts
git commit -m "feat(builder): analytics network ingest + funnel api client (W4 C4)"
```

---

### Task C5: Frontend — Studio "Builder" view

**Files:**
- Create: `dashboard/src/pages/builder-insights.tsx`
- Modify: `dashboard/src/App.tsx`, `dashboard/src/components/shell.tsx`
- Test: `dashboard/src/pages/builder-insights.test.tsx`

- [ ] **Step 1: Write the failing render test**

```typescript
// dashboard/src/pages/builder-insights.test.tsx
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import BuilderInsightsPage from "./builder-insights";

vi.mock("@/lib/api", () => ({
  api: {
    analytics: {
      funnel: vi.fn().mockResolvedValue({
        data: {
          period: "7d",
          time_to_first_deploy_p50_s: 240,
          time_to_first_deploy_p90_s: 600,
          stages: [
            { key: "builder_session_started", label: "Converse", count: 100, dropoff_pct: 0 },
            { key: "deploy_succeeded", label: "Live", count: 38, dropoff_pct: 62 },
          ],
          engines: [],
        },
        meta: {}, errors: [],
      }),
    },
  },
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("BuilderInsightsPage", () => {
  it("renders the funnel heading and a stage label", async () => {
    render(wrap(<BuilderInsightsPage />));
    expect(await screen.findByText("Converse")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Builder/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd dashboard && npx vitest run src/pages/builder-insights.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the view (house CSS-bar idiom; a11y per design §11.4)**

```tsx
// dashboard/src/pages/builder-insights.tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { api, type FunnelMetrics } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const PERIODS = ["7d", "30d", "all"] as const;

function fmtSeconds(s: number | null): string {
  if (s == null) return "--";
  if (s < 90) return `${Math.round(s)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

export default function BuilderInsightsPage() {
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>("7d");
  const { data, isLoading, error } = useQuery({
    queryKey: ["builder-funnel", period],
    queryFn: () => api.analytics.funnel(period),
  });
  const metrics: FunnelMetrics | null = data?.data ?? null;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="flex items-center gap-2 font-display text-2xl">
          <BarChart3 className="h-6 w-6" aria-hidden /> Builder
        </h1>
        <Tabs value={period} onValueChange={(v) => setPeriod(v as typeof period)}>
          <TabsList>
            {PERIODS.map((p) => (
              <TabsTrigger key={p} value={p}>{p}</TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {isLoading && (
        <div className="animate-pulse space-y-4" aria-busy="true">
          <div className="h-28 rounded-lg border border-border bg-card" />
          <div className="h-72 rounded-lg border border-border bg-card" />
        </div>
      )}

      {error && (
        <div className="text-sm text-destructive">
          Failed to load builder analytics: {(error as Error).message}
        </div>
      )}

      {metrics && (
        <>
          {/* North-star band */}
          <Card>
            <CardHeader><CardTitle>Time to first deployed agent</CardTitle></CardHeader>
            <CardContent className="flex items-end gap-8">
              <div>
                <div className="text-xs text-muted-foreground">p50</div>
                <div className="text-4xl font-semibold tabular-nums">
                  {fmtSeconds(metrics.time_to_first_deploy_p50_s)}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">p90</div>
                <div className="text-2xl font-medium tabular-nums text-muted-foreground">
                  {fmtSeconds(metrics.time_to_first_deploy_p90_s)}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Funnel ribbon — semantic list, value labels always visible (a11y §11.4) */}
          <Card>
            <CardHeader><CardTitle>Conversion funnel</CardTitle></CardHeader>
            <CardContent>
              {metrics.stages.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No builder sessions yet. Start one from the Agent Wizard.
                </p>
              ) : (
                <ul role="list" className="space-y-3"
                    aria-label={`Builder funnel for ${metrics.period}`}>
                  {metrics.stages.map((s, i) => {
                    const top = metrics.stages[0]?.count || 1;
                    const pct = Math.round((s.count / top) * 100);
                    const worst = Math.max(...metrics.stages.map((x) => x.dropoff_pct));
                    const isWorst = i > 0 && s.dropoff_pct === worst && worst > 0;
                    return (
                      <li key={s.key} className={cn("rounded-md", isWorst && "border-l-2 border-amber-500 pl-2")}>
                        <div className="flex items-center justify-between text-sm">
                          <span>{s.label}</span>
                          <span className="tabular-nums text-muted-foreground">
                            {s.count.toLocaleString()}
                            {i > 0 && (
                              <span className="ml-2 text-amber-600 dark:text-amber-400">
                                ▼ {s.dropoff_pct}%
                              </span>
                            )}
                          </span>
                        </div>
                        <div className="mt-1 h-2 w-full rounded-full bg-muted">
                          <div className="h-full rounded-full bg-emerald-500 transition-all"
                               style={{ width: `${pct}%` }} />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>

          {/* Per-engine scorecards */}
          {metrics.engines.length > 0 && (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              {metrics.engines.map((e) => (
                <Card key={e.engine}>
                  <CardHeader><CardTitle className="capitalize">{e.engine} ({e.samples})</CardTitle></CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    <ScoreRow label="Spec validity" v={e.spec_validity_rate} />
                    <ScoreRow label="Deploy success" v={e.deploy_success_rate} />
                    <div className="flex justify-between">
                      <span>Turns to spec</span>
                      <span className="tabular-nums">{e.turns_to_spec}</span>
                    </div>
                    <ScoreRow label="Hallucinated fields" v={e.hallucinated_field_rate} invert />
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ScoreRow({ label, v, invert = false }: { label: string; v: number; invert?: boolean }) {
  const good = invert ? v <= 0.2 : v >= 0.8;
  const mid = invert ? v <= 0.4 : v >= 0.6;
  const color = good ? "bg-emerald-500" : mid ? "bg-amber-500" : "bg-red-500";
  return (
    <div>
      <div className="flex justify-between">
        <span>{label}</span>
        <span className="tabular-nums">{Math.round(v * 100)}%</span>
      </div>
      <div className="mt-1 h-1.5 w-full rounded-full bg-muted">
        <div className={cn("h-full rounded-full", color)} style={{ width: `${Math.round(v * 100)}%` }} />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Register the route + nav**

In `dashboard/src/App.tsx`: add `import BuilderInsightsPage from "@/pages/builder-insights";` and
inside the authed `<Routes>`: `<Route path="builder-insights" element={<BuilderInsightsPage />} />`.

In `dashboard/src/components/shell.tsx`: add `BarChart3` to the `lucide-react` import and add to
`OBSERVABILITY_NAV`: `{ to: "/builder-insights", icon: BarChart3, label: "Builder" }`.

- [ ] **Step 5: Run the test + typecheck + build**

Run: `cd dashboard && npx vitest run src/pages/builder-insights.test.tsx && npm run typecheck && npm run build`
Expected: PASS; clean typecheck + build.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/pages/builder-insights.tsx dashboard/src/pages/builder-insights.test.tsx dashboard/src/App.tsx dashboard/src/components/shell.tsx
git commit -m "feat(builder): Studio Builder analytics view (funnel + scorecards) (W4 C5)"
```

---

# Part D — Codex golden-transcript gated e2e

### Task D1: Codex eject e2e (mirror the Claude path)

**Files:**
- Create: `tests/e2e/test_builder_eject_codex_e2e.py`

- [ ] **Step 1: Write the gated test**

```python
# tests/e2e/test_builder_eject_codex_e2e.py
"""Gated e2e: the Codex engine writes real code against a live OpenAI model.

Skipped unless AGENTBREEDER_E2E_OPENAI_KEY is set (BYO key, costs tokens).
Mirrors test_builder_eject_e2e.py (the Claude path) for engine parity.
"""

from __future__ import annotations

import os

import pytest

from engine.coding_agent.base import AgentBounds
from engine.coding_agent.engines import engine_for
from engine.providers.models import ProviderConfig, ProviderType
from engine.providers.openai_provider import OpenAIProvider
from engine.sandbox.local import LocalSandbox

_KEY = os.environ.get("AGENTBREEDER_E2E_OPENAI_KEY")

pytestmark = pytest.mark.skipif(not _KEY, reason="AGENTBREEDER_E2E_OPENAI_KEY not set")


@pytest.mark.asyncio
async def test_eject_writes_agent_py_against_live_codex():
    provider = OpenAIProvider(
        ProviderConfig(provider_type=ProviderType.openai, api_key=_KEY)
    )
    sandbox = LocalSandbox()
    try:
        engine = engine_for("codex", provider=provider)
        instruction = (
            "Create a minimal Python agent project for this spec:\n"
            "name: hello-agent\nframework: custom\n"
            "Write agent.py with a `run(input: str) -> str` function that echoes "
            "the input, and tools/__init__.py. Use write_file for each file, then stop."
        )
        file_changes: list[str] = []
        async for evt in engine.run(instruction, [], sandbox, AgentBounds(max_turns=6)):
            if evt.type == "file_change":
                file_changes.append(evt.path)
        assert any(p.endswith("agent.py") for p in file_changes), file_changes
        files = await sandbox.list(".")
        assert any(p.endswith("agent.py") for p in files), files
    finally:
        await sandbox.close()
        await provider.close()
```

> **Verify:** confirm `OpenAIProvider`'s import path + `ProviderType.openai` value match
> `engine/providers/openai_provider.py` and `engine/providers/models.py` (grep before running).

- [ ] **Step 2: Run (skips without a key)**

Run: `pytest tests/e2e/test_builder_eject_codex_e2e.py -v`
Expected: SKIPPED (no key). With `AGENTBREEDER_E2E_OPENAI_KEY`: PASS, agent.py written.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_builder_eject_codex_e2e.py
git commit -m "test(builder): Codex eject gated e2e for engine parity (W4 D1)"
```

---

# Final verification (both repos)

- [ ] **OSS:** `pytest tests/unit tests/integration -q` (e2e skips cleanly without keys); `ruff check . && ruff format --check .`; `cd dashboard && npm run lint && npm run typecheck && npm run build && npx vitest run`.
- [ ] **Cloud:** `venv/bin/pytest -q && ruff check api/`; `alembic upgrade head` against an ephemeral Postgres.
- [ ] **Gate:** run `/gate` (Gates 1–3 always; Gate 5 cloud-security re-runs the live scan before any cloud deploy — confirm the §11.2 CRITICALs landed: no secrets in sandbox, metadata-egress block, e2b DPA/disclosure).
- [ ] **Docs sync (CLAUDE.md rule):** update `website/content/docs/` — `agent-yaml.mdx` is unaffected, but add CloudSandbox + `AGENTBREEDER_SANDBOX=cloud` + `AGENTBREEDER_SANDBOX_BACKEND` to `how-to.mdx`, and a "Builder analytics" note where Studio views are documented. Update the cloud repo's stale `ARCHITECTURE.md` (ECS Fargate → Cloud Run) per Open Q #3.
- [ ] **PR:** open the epic PR (W1–W4) only after the full implementation + local tests pass (per the defer-PR-until-done preference). Preserve wave-by-wave commit history (no squash).

---

## Self-review notes (author)

- **Spec coverage:** CloudSandbox (A1–A4 ↔ design §3), metering (B1–B3 ↔ §4), analytics (C1–C5 ↔ §5/§11.3/§11.4), Codex e2e (D1 ↔ §6). Security §11.2 rails appear as code comments + the final Gate-5 checklist.
- **Deferred (documented, not silent):** full per-engine scorecard DB joins (C3 ships unit-covered helpers + p50/p90; engine cards render when populated), metering idempotency across network retries (B3 comment), BigQuery export (design §5 follow-on), self-hosted Firecracker backend (design v2).
- **Verify-before-write flags:** e2b async SDK surface (A3), cloud session-maker export name (B3), `OpenAIProvider` import path (D1), current cloud Alembic head (B1).
- **Type consistency:** `sandbox_seconds` (float) flows A4 frame → B3 `_extract_sandbox_seconds` → `ceil(/60)`; `FunnelMetrics`/`FunnelStage`/`EngineScorecard` identical names across schemas.py (C2), api.ts (C4), and the view (C5).
