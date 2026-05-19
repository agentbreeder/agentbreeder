# Platform Audit — Wave 2 (Shared Utilities) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use `- [ ]` for tracking.

**Goal:** Land 5 small shared-utility modules that Wave 3 will thread through 8 subsystems. Three are essential prerequisites for Wave 3 (`degraded_mode`, `retry`, `_health`); two are bottom-up extractions identified by the Wave 1 final reviewer (`path_safety`, `_validators`).

**Architecture:** Pure additions — 5 new modules, 5 new test files. No callsite refactoring in Wave 2 (that's Wave 3's job — keeps each wave's blast radius small and reviewable). Each module has one clear responsibility and a minimal API surface.

**Tech stack:** Python 3.11+, pytest + pytest-asyncio. Lint via `ruff check && ruff format`. Tests under `tests/unit/`.

**Risk envelope:** Additive only. No schema changes, no migrations, no breaking changes. Net new code only.

---

## File Structure

| Task | Module (new) | Test file (new) | Responsibility |
|------|--------------|-----------------|----------------|
| 1 | `engine/observability/__init__.py` + `engine/observability/degraded_mode.py` | `tests/unit/test_degraded_mode.py` | Warn-once-per-(component,reason) + `DegradedFlag` for response tagging |
| 2 | `api/retry.py` | `tests/unit/test_retry.py` | `async_retry` decorator/wrapper with exponential backoff + jitter |
| 3 | `engine/deployers/_health.py` | `tests/unit/test_deployer_health.py` | `poll_until_ready` — shared deployer health-check loop |
| 4 | `engine/util/__init__.py` + `engine/util/path_safety.py` | `tests/unit/test_path_safety.py` | `safe_relative_subdir(value)` validator (extracted from W1-01) |
| 5 | `api/models/_validators.py` | `tests/unit/test_model_validators.py` | Reusable Pydantic validators: `weights_sum_to_one`, numeric bounds presets |

Each task is one commit. Conventional-commit messages.

**No production code outside the new modules changes** in Wave 2. Existing callsites (e.g., `markdown_writer.py`'s `_validate_subdir`, `embed_texts`'s `_warn_fallback_once`) stay as-is until Wave 3 consolidates them.

---

## Task 1: `engine/observability/degraded_mode.py`

**Why:** Wave 1 introduced a process-wide warn-once dedup + `degraded: true` response flag for RAG embedding fallback (`api/services/rag_service.py`). Wave 3 needs the same pattern for provider fallback chains, MCP scanner timeouts, sandbox-execution downgrades, and identity-provisioning warnings. Generalize the pattern now.

**Files:**
- Create: `engine/observability/__init__.py` (empty package marker)
- Create: `engine/observability/degraded_mode.py`
- Create: `tests/unit/test_degraded_mode.py`

### Steps

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_degraded_mode.py
"""Tests for engine.observability.degraded_mode."""
from __future__ import annotations

import logging

import pytest

from engine.observability.degraded_mode import (
    DegradedFlag,
    clear_degraded_state,
    warn_once,
)


@pytest.fixture(autouse=True)
def reset_state():
    clear_degraded_state()
    yield
    clear_degraded_state()


def test_warn_once_logs_first_occurrence(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key")
    matches = [r for r in caplog.records if "rag.embedding" in r.message]
    assert len(matches) == 1


def test_warn_once_dedupes_same_pair(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key")
    warn_once("rag.embedding", "openai-no-api-key")
    warn_once("rag.embedding", "openai-no-api-key")
    matches = [r for r in caplog.records if "rag.embedding" in r.message]
    assert len(matches) == 1


def test_warn_once_logs_distinct_reasons(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key")
    warn_once("rag.embedding", "ollama-unreachable")
    warn_once("provider.fallback", "anthropic-429")
    matches = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(matches) == 3


def test_warn_once_includes_extras(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key", extra={"model": "text-embed-3"})
    record = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert record.component == "rag.embedding"
    assert record.reason == "openai-no-api-key"
    assert record.model == "text-embed-3"


def test_degraded_flag_starts_false() -> None:
    flag = DegradedFlag()
    assert flag.is_degraded is False
    assert flag.first_reason is None


def test_degraded_flag_records_first_reason() -> None:
    flag = DegradedFlag()
    flag.mark("openai-no-api-key")
    assert flag.is_degraded is True
    assert flag.first_reason == "openai-no-api-key"


def test_degraded_flag_keeps_first_reason() -> None:
    flag = DegradedFlag()
    flag.mark("openai-no-api-key")
    flag.mark("ollama-unreachable")
    assert flag.is_degraded is True
    assert flag.first_reason == "openai-no-api-key"


def test_clear_state_resets_dedup(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    warn_once("rag.embedding", "openai-no-api-key")
    clear_degraded_state()
    warn_once("rag.embedding", "openai-no-api-key")
    matches = [r for r in caplog.records if "rag.embedding" in r.message]
    assert len(matches) == 2
```

- [ ] **Step 2: Run tests, confirm they fail (module doesn't exist yet)**

```bash
pytest tests/unit/test_degraded_mode.py -v
```
Expected: import error.

- [ ] **Step 3: Implement the module**

Create `engine/observability/__init__.py` as a one-line `"""Observability utilities for shared logging + degraded-mode tagging."""` docstring file (or empty).

Create `engine/observability/degraded_mode.py`:

```python
"""Process-wide warn-once dedup + per-call DegradedFlag for response tagging.

Use ``warn_once(component, reason, extra=...)`` to emit a structured WARNING
that fires at most once per (component, reason) per process. Use
``DegradedFlag`` inside request handlers to track whether any sub-step degraded
and surface it to callers via response fields.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_WARNED: set[tuple[str, str]] = set()
_LOCK = Lock()


def warn_once(component: str, reason: str, extra: dict[str, Any] | None = None) -> None:
    """Emit a WARNING log the first time a (component, reason) pair is seen.

    Args:
        component: stable identifier for the failure surface (e.g. ``rag.embedding``).
        reason: stable identifier for the failure mode (e.g. ``openai-no-api-key``).
        extra: optional additional fields to include in the log record's ``extra``.
    """
    key = (component, reason)
    with _LOCK:
        if key in _WARNED:
            return
        _WARNED.add(key)
    log_extra: dict[str, Any] = {"component": component, "reason": reason}
    if extra:
        log_extra.update(extra)
    logger.warning(
        "%s degraded: %s",
        component,
        reason,
        extra=log_extra,
    )


def clear_degraded_state() -> None:
    """Reset the warn-once dedup set. Tests should call this between cases."""
    with _LOCK:
        _WARNED.clear()


@dataclass
class DegradedFlag:
    """Per-call degraded-mode tracker. Default state is not degraded."""

    is_degraded: bool = False
    first_reason: str | None = None

    def mark(self, reason: str) -> None:
        """Record a degraded event. First reason wins (preserves earliest cause)."""
        if not self.is_degraded:
            self.is_degraded = True
            self.first_reason = reason
```

- [ ] **Step 4: Run tests; expect all 8 pass**

```bash
pytest tests/unit/test_degraded_mode.py -v
```

- [ ] **Step 5: Lint + format**

```bash
ruff check engine/observability/ tests/unit/test_degraded_mode.py
ruff format engine/observability/ tests/unit/test_degraded_mode.py
```

- [ ] **Step 6: Commit**

```bash
git add engine/observability/ tests/unit/test_degraded_mode.py
git commit -m "feat(observability): warn-once dedup + DegradedFlag for response tagging (W2-01)"
```

---

## Task 2: `api/retry.py`

**Why:** Multiple audit findings flagged missing retry semantics on transient failures (A4 — agent invoke; D1/D3 — deployer retries; M-series — provider fallback). Centralize the backoff logic so Wave 3 can thread it consistently.

**Files:**
- Create: `api/retry.py`
- Create: `tests/unit/test_retry.py`

### Steps

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_retry.py
"""Tests for api.retry."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from api.retry import async_retry, RetryExhaustedError


@pytest.mark.asyncio
async def test_success_on_first_try() -> None:
    fn = AsyncMock(return_value="ok")
    result = await async_retry(fn, max_attempts=3)
    assert result == "ok"
    assert fn.await_count == 1


@pytest.mark.asyncio
async def test_retries_on_exception() -> None:
    fn = AsyncMock(side_effect=[RuntimeError("boom"), RuntimeError("boom"), "ok"])
    result = await async_retry(fn, max_attempts=3, initial_delay=0.001)
    assert result == "ok"
    assert fn.await_count == 3


@pytest.mark.asyncio
async def test_raises_retry_exhausted_after_max_attempts() -> None:
    fn = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(RetryExhaustedError) as exc:
        await async_retry(fn, max_attempts=3, initial_delay=0.001)
    assert exc.value.attempts == 3
    assert isinstance(exc.value.last_exception, RuntimeError)


@pytest.mark.asyncio
async def test_only_retries_listed_exceptions() -> None:
    fn = AsyncMock(side_effect=ValueError("bad input"))
    with pytest.raises(ValueError):
        await async_retry(
            fn, max_attempts=3, initial_delay=0.001, retry_on=(RuntimeError,)
        )
    assert fn.await_count == 1  # not retried


@pytest.mark.asyncio
async def test_exponential_backoff_delays() -> None:
    """Sleep between attempts approximates exponential growth (with jitter)."""
    sleeps: list[float] = []

    async def fake_sleep(t: float) -> None:
        sleeps.append(t)

    fn = AsyncMock(side_effect=[RuntimeError(), RuntimeError(), RuntimeError(), "ok"])
    # Patch asyncio.sleep with our recorder.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fake_sleep)
        result = await async_retry(
            fn,
            max_attempts=4,
            initial_delay=0.1,
            max_delay=10.0,
            backoff_factor=2.0,
            jitter=False,
        )
    assert result == "ok"
    # 3 sleeps between 4 attempts; with backoff_factor=2 and jitter off:
    # 0.1, 0.2, 0.4
    assert sleeps == pytest.approx([0.1, 0.2, 0.4])


@pytest.mark.asyncio
async def test_max_delay_caps_backoff() -> None:
    sleeps: list[float] = []

    async def fake_sleep(t: float) -> None:
        sleeps.append(t)

    fn = AsyncMock(side_effect=[RuntimeError()] * 5 + ["ok"])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fake_sleep)
        await async_retry(
            fn,
            max_attempts=6,
            initial_delay=1.0,
            max_delay=2.0,
            backoff_factor=2.0,
            jitter=False,
        )
    # 1.0, 2.0 (capped), 2.0, 2.0, 2.0
    assert sleeps == pytest.approx([1.0, 2.0, 2.0, 2.0, 2.0])
```

- [ ] **Step 2: Run tests; confirm import failure**

```bash
pytest tests/unit/test_retry.py -v
```

- [ ] **Step 3: Implement**

Create `api/retry.py`:

```python
"""Async retry helper with exponential backoff + jitter.

Use ``async_retry(fn, max_attempts=..., retry_on=...)`` to wrap an async call.
On exhaustion, ``RetryExhaustedError`` is raised with the last exception
attached.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RetryExhaustedError(Exception):
    """Raised when async_retry exhausts max_attempts without success."""

    def __init__(self, attempts: int, last_exception: BaseException) -> None:
        super().__init__(f"Retry exhausted after {attempts} attempts: {last_exception}")
        self.attempts = attempts
        self.last_exception = last_exception


async def async_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Retry an async callable with exponential backoff.

    Args:
        fn: zero-arg async callable to retry.
        max_attempts: total attempts including the first call. Must be ≥ 1.
        initial_delay: delay (seconds) before the 2nd attempt.
        max_delay: cap on the delay between attempts.
        backoff_factor: multiplier applied each retry.
        jitter: if True, multiply each delay by a random factor in [0.5, 1.5].
        retry_on: tuple of exception types to retry on. Other exceptions
            propagate immediately.

    Returns the result of ``fn()`` on the first successful attempt.

    Raises:
        RetryExhaustedError: after ``max_attempts`` failed retries.
        Exception: any exception not matching ``retry_on`` is re-raised as-is.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    last_exc: BaseException | None = None
    delay = initial_delay

    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except retry_on as exc:  # noqa: PERF203 — retries by design
            last_exc = exc
            if attempt >= max_attempts:
                break
            sleep_for = min(delay, max_delay)
            if jitter:
                sleep_for *= 0.5 + random.random()
            logger.debug(
                "async_retry attempt %d/%d failed: %s — sleeping %.3fs",
                attempt,
                max_attempts,
                exc,
                sleep_for,
            )
            await asyncio.sleep(sleep_for)
            delay *= backoff_factor

    assert last_exc is not None
    raise RetryExhaustedError(max_attempts, last_exc)
```

- [ ] **Step 4: Run tests; expect 6 pass**

```bash
pytest tests/unit/test_retry.py -v
```

- [ ] **Step 5: Lint + format**

```bash
ruff check api/retry.py tests/unit/test_retry.py
ruff format api/retry.py tests/unit/test_retry.py
```

- [ ] **Step 6: Commit**

```bash
git add api/retry.py tests/unit/test_retry.py
git commit -m "feat(api): async_retry helper with exponential backoff + jitter (W2-02)"
```

---

## Task 3: `engine/deployers/_health.py`

**Why:** Audit D5 — AWS uses deadline + variable interval; GCP/Azure use fixed sleep. Inconsistent. Centralize into a shared `poll_until_ready` so Wave 3 can replace per-deployer implementations.

**Files:**
- Create: `engine/deployers/_health.py`
- Create: `tests/unit/test_deployer_health.py`

### Steps

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_deployer_health.py
"""Tests for engine.deployers._health.poll_until_ready."""
from __future__ import annotations

import asyncio

import pytest

from engine.deployers._health import HealthCheckTimeout, poll_until_ready


@pytest.mark.asyncio
async def test_returns_when_healthy_on_first_check() -> None:
    async def check() -> bool:
        return True

    await poll_until_ready(check, timeout=1.0, initial_interval=0.01)


@pytest.mark.asyncio
async def test_returns_when_healthy_after_retries() -> None:
    calls = {"n": 0}

    async def check() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    await poll_until_ready(check, timeout=5.0, initial_interval=0.01, max_interval=0.05)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_raises_on_timeout() -> None:
    async def check() -> bool:
        return False

    with pytest.raises(HealthCheckTimeout):
        await poll_until_ready(check, timeout=0.05, initial_interval=0.01)


@pytest.mark.asyncio
async def test_passes_through_check_exceptions() -> None:
    async def check() -> bool:
        raise RuntimeError("infra error")

    with pytest.raises(RuntimeError, match="infra error"):
        await poll_until_ready(check, timeout=1.0, initial_interval=0.01)


@pytest.mark.asyncio
async def test_interval_grows_with_backoff_factor() -> None:
    sleeps: list[float] = []

    async def fake_sleep(t: float) -> None:
        sleeps.append(t)

    async def check() -> bool:
        return len(sleeps) >= 3

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(asyncio, "sleep", fake_sleep)
        await poll_until_ready(
            check,
            timeout=5.0,
            initial_interval=0.1,
            max_interval=10.0,
            backoff_factor=2.0,
        )

    # Should have grown: 0.1, 0.2, 0.4 ... up to when check returned True.
    assert sleeps[0] == pytest.approx(0.1)
    assert sleeps[1] == pytest.approx(0.2)
    assert sleeps[2] == pytest.approx(0.4)
```

- [ ] **Step 2: Run tests; confirm import failure**

```bash
pytest tests/unit/test_deployer_health.py -v
```

- [ ] **Step 3: Implement**

Create `engine/deployers/_health.py`:

```python
"""Shared health-check polling loop for deployers.

All cloud deployers must wait for a deployed service to become healthy before
declaring success. AWS, GCP, Azure, and Kubernetes each implemented this loop
differently. ``poll_until_ready`` is the single shared implementation: a
deadline-bounded loop with exponential backoff (capped) between checks.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class HealthCheckTimeout(Exception):
    """Raised when poll_until_ready exceeds its timeout."""


async def poll_until_ready(
    check: Callable[[], Awaitable[bool]],
    *,
    timeout: float,
    initial_interval: float = 1.0,
    max_interval: float = 10.0,
    backoff_factor: float = 2.0,
) -> None:
    """Poll ``check`` until it returns True or ``timeout`` seconds elapse.

    Args:
        check: zero-arg async callable returning True when healthy. Exceptions
            from ``check`` propagate (treat them as transport failures the
            caller wants to know about, not as "not yet healthy").
        timeout: total seconds before raising HealthCheckTimeout.
        initial_interval: seconds to wait after the first failed check.
        max_interval: cap on inter-check delay.
        backoff_factor: multiplier each iteration.

    Raises:
        HealthCheckTimeout: if ``timeout`` elapses without ``check`` returning True.
    """
    if timeout <= 0:
        raise ValueError("timeout must be > 0")

    deadline = time.monotonic() + timeout
    interval = initial_interval

    while True:
        ok = await check()
        if ok:
            return

        now = time.monotonic()
        if now >= deadline:
            raise HealthCheckTimeout(
                f"Health check did not become ready within {timeout}s"
            )

        # Don't sleep past the deadline.
        sleep_for = min(interval, max_interval, deadline - now)
        logger.debug("health-check not ready; sleeping %.3fs", sleep_for)
        await asyncio.sleep(sleep_for)
        interval = min(interval * backoff_factor, max_interval)
```

- [ ] **Step 4: Run tests; expect 5 pass**

```bash
pytest tests/unit/test_deployer_health.py -v
```

- [ ] **Step 5: Lint + format**

```bash
ruff check engine/deployers/_health.py tests/unit/test_deployer_health.py
ruff format engine/deployers/_health.py tests/unit/test_deployer_health.py
```

- [ ] **Step 6: Commit**

```bash
git add engine/deployers/_health.py tests/unit/test_deployer_health.py
git commit -m "feat(deployers): poll_until_ready shared health-check loop (W2-03)"
```

---

## Task 4: `engine/util/path_safety.py`

**Why:** Promote `_validate_subdir` from `markdown_writer.py` (W1-01) to a reusable validator. Same rules: reject null bytes, absolute paths, `~` expansion, `..` segments. Future Wave-4 tasks (e.g., a `file_reader` tool, RAG ingestion endpoints accepting user-supplied paths) will use it.

**Files:**
- Create: `engine/util/__init__.py` (empty package marker)
- Create: `engine/util/path_safety.py`
- Create: `tests/unit/test_path_safety.py`

### Steps

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_path_safety.py
"""Tests for engine.util.path_safety."""
from __future__ import annotations

import pytest

from engine.util.path_safety import UnsafePathError, safe_relative_subdir


def test_allows_empty_string() -> None:
    assert safe_relative_subdir("") == ""


def test_allows_simple_dir() -> None:
    assert safe_relative_subdir("reports") == "reports"


def test_allows_nested_dir() -> None:
    assert safe_relative_subdir("reports/q1/v2") == "reports/q1/v2"


def test_rejects_absolute_unix_path() -> None:
    with pytest.raises(UnsafePathError, match="absolute"):
        safe_relative_subdir("/etc")


def test_rejects_home_expansion() -> None:
    with pytest.raises(UnsafePathError, match="home"):
        safe_relative_subdir("~/notes")


def test_rejects_parent_traversal() -> None:
    with pytest.raises(UnsafePathError, match="traversal"):
        safe_relative_subdir("../etc")


def test_rejects_nested_parent_traversal() -> None:
    with pytest.raises(UnsafePathError, match="traversal"):
        safe_relative_subdir("ok/../../etc")


def test_rejects_null_byte() -> None:
    with pytest.raises(UnsafePathError, match="null"):
        safe_relative_subdir("ok\x00etc")


def test_error_message_includes_offending_input() -> None:
    with pytest.raises(UnsafePathError, match="'/etc'"):
        safe_relative_subdir("/etc")
```

- [ ] **Step 2: Run tests; confirm import failure**

```bash
pytest tests/unit/test_path_safety.py -v
```

- [ ] **Step 3: Implement**

Create `engine/util/__init__.py` (single docstring line or empty).

Create `engine/util/path_safety.py`:

```python
"""Validators for user-supplied path fragments.

Use ``safe_relative_subdir(value)`` to validate that a user-supplied string
is a safe relative path: no null bytes, no leading ``/``, no leading ``~``,
no ``..`` path segments. Returns the original value unchanged when valid;
raises ``UnsafePathError`` otherwise.
"""
from __future__ import annotations

from pathlib import PurePosixPath


class UnsafePathError(ValueError):
    """Raised when a user-supplied path fragment is unsafe."""


def safe_relative_subdir(value: str) -> str:
    """Validate ``value`` is a safe relative-path fragment. Return as-is when valid.

    Empty string is allowed (caller means "use the base directory").

    Raises:
        UnsafePathError on null bytes, absolute paths, home expansion, or
        parent-directory traversal.
    """
    if value == "":
        return value
    if "\x00" in value:
        raise UnsafePathError(f"path must not contain null bytes: {value!r}")
    if value.startswith("/"):
        raise UnsafePathError(
            f"path must be relative, not absolute: {value!r}"
        )
    if value.startswith("~"):
        raise UnsafePathError(
            f"path must not begin with home-directory expansion: {value!r}"
        )
    parts = PurePosixPath(value).parts
    if any(part == ".." for part in parts):
        raise UnsafePathError(
            f"path must not contain parent-directory traversal: {value!r}"
        )
    return value
```

- [ ] **Step 4: Run tests; expect 9 pass**

```bash
pytest tests/unit/test_path_safety.py -v
```

- [ ] **Step 5: Lint + format**

```bash
ruff check engine/util/ tests/unit/test_path_safety.py
ruff format engine/util/ tests/unit/test_path_safety.py
```

- [ ] **Step 6: Commit**

```bash
git add engine/util/ tests/unit/test_path_safety.py
git commit -m "feat(util): safe_relative_subdir shared path validator (W2-04)"
```

---

## Task 5: `api/models/_validators.py`

**Why:** Promote `weights_must_sum_to_one` from `RagSearchRequest` (W1-02) to a reusable Pydantic validator. Also expose presets for the numeric-bound `Field(ge=…, le=…)` patterns so other endpoints (evals, memory, a2a) can adopt them consistently.

**Files:**
- Create: `api/models/_validators.py`
- Create: `tests/unit/test_model_validators.py`

### Steps

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_model_validators.py
"""Tests for api.models._validators."""
from __future__ import annotations

import pytest
from pydantic import BaseModel, Field, ValidationError

from api.models._validators import (
    HopsField,
    SeedEntityLimitField,
    TopKField,
    WeightField,
    make_weights_sum_validator,
)


class _M(BaseModel):
    """Anonymous model used by tests below."""

    top_k: TopKField = 10
    hops: HopsField = None
    seed_entity_limit: SeedEntityLimitField = 5
    vector_weight: WeightField = 0.7
    text_weight: WeightField = 0.3

    _check_weights = make_weights_sum_validator("vector_weight", "text_weight")


def test_defaults_valid() -> None:
    m = _M()
    assert m.top_k == 10
    assert m.hops is None
    assert m.seed_entity_limit == 5
    assert m.vector_weight == 0.7
    assert m.text_weight == 0.3


def test_top_k_low_bound() -> None:
    with pytest.raises(ValidationError):
        _M(top_k=0)


def test_top_k_high_bound() -> None:
    with pytest.raises(ValidationError):
        _M(top_k=1_000_000)


def test_hops_low_bound() -> None:
    with pytest.raises(ValidationError):
        _M(hops=-1)


def test_hops_high_bound() -> None:
    with pytest.raises(ValidationError):
        _M(hops=999)


def test_seed_entity_limit_low_bound() -> None:
    with pytest.raises(ValidationError):
        _M(seed_entity_limit=0)


def test_seed_entity_limit_high_bound() -> None:
    with pytest.raises(ValidationError):
        _M(seed_entity_limit=999)


def test_weight_low_bound() -> None:
    with pytest.raises(ValidationError):
        _M(vector_weight=-0.1, text_weight=1.1)


def test_weight_high_bound() -> None:
    with pytest.raises(ValidationError):
        _M(vector_weight=1.5, text_weight=-0.5)


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        _M(vector_weight=1.0, text_weight=1.0)


def test_weights_within_tolerance() -> None:
    m = _M(vector_weight=0.1, text_weight=0.9)
    assert m.vector_weight + m.text_weight == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests; confirm import failure**

```bash
pytest tests/unit/test_model_validators.py -v
```

- [ ] **Step 3: Implement**

Create `api/models/_validators.py`:

```python
"""Reusable Pydantic field types + validators.

Field aliases:
    TopKField, HopsField, SeedEntityLimitField, WeightField — pre-configured
    constrained types matching the audit's W1-04 bounds.

Validator factory:
    make_weights_sum_validator(name_a, name_b, tolerance=1e-6) — returns a
    model_validator that enforces ``getattr(self, name_a) + getattr(self, name_b)
    sums to 1.0 within tolerance``.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, model_validator


# --- Field-type aliases ------------------------------------------------------
TopKField = Annotated[int, Field(ge=1, le=1000)]
HopsField = Annotated[int | None, Field(default=None, ge=0, le=10)]
SeedEntityLimitField = Annotated[int, Field(ge=1, le=50)]
WeightField = Annotated[float, Field(ge=0.0, le=1.0)]


# --- Cross-field validator factory ------------------------------------------
def make_weights_sum_validator(
    name_a: str,
    name_b: str,
    *,
    tolerance: float = 1e-6,
):
    """Return a Pydantic model_validator(mode='after') that enforces sum-to-1.0.

    Use as::

        class Foo(BaseModel):
            vector_weight: WeightField = 0.7
            text_weight: WeightField = 0.3
            _check_weights = make_weights_sum_validator("vector_weight", "text_weight")
    """

    @model_validator(mode="after")
    def _validate(self: Any) -> Any:
        a = getattr(self, name_a)
        b = getattr(self, name_b)
        total = a + b
        if abs(total - 1.0) > tolerance:
            raise ValueError(
                f"{name_a} + {name_b} must sum to 1.0 (got {total:.6f})"
            )
        return self

    return _validate
```

- [ ] **Step 4: Run tests; expect 11 pass**

```bash
pytest tests/unit/test_model_validators.py -v
```

- [ ] **Step 5: Lint + format**

```bash
ruff check api/models/_validators.py tests/unit/test_model_validators.py
ruff format api/models/_validators.py tests/unit/test_model_validators.py
```

- [ ] **Step 6: Commit**

```bash
git add api/models/_validators.py tests/unit/test_model_validators.py
git commit -m "feat(models): reusable Pydantic field types + weights-sum validator factory (W2-05)"
```

---

## Closing

After all 5 tasks land, no separate consolidation/CHANGELOG task is needed — Wave 2 modules are net-new code with no behavioral impact (no callsites use them yet). The CHANGELOG entry will be added at the END of Wave 3 when callsites are threaded.

The final reviewer will run a full unit test pass + lint to confirm no regressions.

---

## Self-review notes (applied inline)

- **Spec coverage:** 5 utilities map to spec W2-01 (degraded_mode), W2-02 (retry), W2-03 (_health), + 2 reviewer additions W2-04 (path_safety), W2-05 (_validators). ✅
- **Placeholder scan:** No TBDs. Every module is fully implemented in the plan.
- **Type consistency:** `DegradedFlag.is_degraded` / `first_reason` match across module + tests. `RetryExhaustedError(attempts, last_exception)` interface matches across module + tests. `safe_relative_subdir(value)` interface matches. `make_weights_sum_validator(name_a, name_b)` consistent.
- **Risk envelope:** All 5 modules are net new code. No callsites changed. No external API surface (these are utilities). Cross-repo (cloud, website) not affected.
- **Test isolation:** `degraded_mode` tests use `clear_degraded_state()` fixture; `retry` tests stub `asyncio.sleep` via `pytest.MonkeyPatch.context()` — both isolation patterns are correct.

---

## Execution

Subagent-driven. Fresh implementer per task; combined spec + code-quality review after each. Final reviewer after all 5.

After Wave 2 closes, generate Wave 3 plan (16 cross-cutting threadings of these 5 utilities through the 8 subsystems).
