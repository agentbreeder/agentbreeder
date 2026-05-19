# Platform Audit — Wave 3 (Cross-Cutting Threading) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use `- [ ]` for tracking.

**Goal:** Thread the 5 Wave 2 shared utilities (`degraded_mode`, `async_retry`, `poll_until_ready`, `safe_relative_subdir`, `_validators`) through their target callsites across 8 subsystems. **Replace bespoke implementations with the shared helpers** — no new behavior, just consolidation.

**Architecture:** Each task picks one utility and threads it through every appropriate callsite. The contract is **behavioral parity** — existing tests must still pass after each swap. Implementations may differ in tiny details (logging format, exception messages) as long as observable behavior is preserved. Each task is one commit.

**Tech Stack:** Same as Wave 2. Lint via `ruff check && ruff format`; tests via `pytest`.

**Risk envelope:** This is the most invasive wave so far — it touches existing code across 8 subsystems. Constraints:
- No agent.yaml schema changes.
- No DB migrations.
- No public API contract changes (CLI flags, REST endpoint shapes, agent.yaml fields).
- Each task's diff must NOT change any test assertion (only update mocks if a signature changed in Wave 2 — and Wave 2 only added new modules, so no existing-test mocks need updating in Wave 3).
- If an implementer can't preserve behavior, they STOP and surface as DONE_WITH_CONCERNS rather than papering over it.

---

## Threading order (executed sequentially — each task waits for prior task to land)

| # | Task | Utility | Target callsites |
|---|------|---------|------------------|
| 1 | Deployer health-checks | `poll_until_ready` | 6 deployers |
| 2 | Path validation in tools/routes | `safe_relative_subdir` | up to 6 callsites |
| 3 | Retry semantics for LLM + HTTP calls | `async_retry` | up to 5 callsites |
| 4 | Degraded-mode response flag | `warn_once` + `DegradedFlag` | up to 5 callsites |
| 5 | Pydantic field-type aliases | `_validators` field-types + sum-validator | up to 5 model classes |

Tasks are sequential — each new helper's behavior change should land + go green before the next utility's threading begins.

---

## Task 1: Thread `poll_until_ready` through 6 deployers

**Why:** Audit D5 — health-check semantics differ across AWS (deadline+interval), GCP/Azure (fixed sleep), and Kubernetes/Docker. Consolidating cuts ~150-300 LOC of duplicated logic and aligns retry/backoff behavior.

**Approach:** For each deployer's health-check loop, replace the bespoke `while time.time() < deadline: ... sleep(interval)` pattern with a call to `poll_until_ready(check, timeout=..., initial_interval=..., max_interval=..., backoff_factor=...)`. Parameters should match the deployer's prior defaults (preserve timing characteristics).

### Files (recon)

```bash
grep -nE 'asyncio\.sleep|time\.sleep|deadline|health_check|healthy' engine/deployers/*.py | head -60
```

The deployers to thread (per audit § 3.8):

| File | Likely health-check fn | Notes |
|------|------------------------|-------|
| `engine/deployers/aws_ecs.py` | `_wait_for_service_running` or similar | Uses `deadline + asyncio.get_event_loop().time() < deadline` pattern per audit |
| `engine/deployers/aws_app_runner.py` | `_wait_for_service_running` | App Runner has its own status poll |
| `engine/deployers/gcp_cloudrun.py` | `_wait_for_revision_ready` | Fixed-sleep loop per audit |
| `engine/deployers/azure_container_apps.py` | `_wait_for_provisioning_state` | Fixed-sleep loop per audit |
| `engine/deployers/kubernetes.py` | `_wait_for_rollout` | rollout-status poll |
| `engine/deployers/docker_compose.py` | `_wait_for_healthy` | container-healthy poll |

### Steps

- [ ] **Step 1 — Recon**

For each deployer, locate the current health-check loop. Note its current timeout, initial interval, and any backoff logic.

```bash
for f in engine/deployers/aws_ecs.py engine/deployers/aws_app_runner.py engine/deployers/gcp_cloudrun.py engine/deployers/azure_container_apps.py engine/deployers/kubernetes.py engine/deployers/docker_compose.py; do
  echo "=== $f ==="
  grep -n 'asyncio\.sleep\|time\.sleep\|deadline\|while\b' "$f" | head -10
done
```

- [ ] **Step 2 — Replace each deployer's bespoke loop**

The standard refactor pattern (apply per deployer; preserve existing timeout values):

**Before:**
```python
async def _wait_for_ready(self, service_arn: str, timeout: int = 600) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        status = await self._get_status(service_arn)
        if status == "RUNNING":
            return
        await asyncio.sleep(5)
    raise TimeoutError(f"Service {service_arn} did not become ready")
```

**After:**
```python
from engine.deployers._health import poll_until_ready, HealthCheckTimeout

async def _wait_for_ready(self, service_arn: str, timeout: int = 600) -> None:
    async def _check() -> bool:
        status = await self._get_status(service_arn)
        return status == "RUNNING"

    try:
        await poll_until_ready(
            _check,
            timeout=timeout,
            initial_interval=5.0,
            max_interval=30.0,
            backoff_factor=1.5,
        )
    except HealthCheckTimeout as e:
        raise TimeoutError(f"Service {service_arn} did not become ready") from e
```

**Key invariants to preserve per deployer:**
- The exception raised on timeout must remain the same type the existing callers + tests expect. If existing code raised `TimeoutError`, wrap `HealthCheckTimeout` into `TimeoutError`. If it raised a custom `DeployError`, wrap accordingly. **Do not change the type that propagates out of the deployer.**
- The total `timeout` value (seconds) must match the prior default.
- The polling cadence may change slightly (the shared helper uses exponential backoff capped at `max_interval`); pick `initial_interval` and `max_interval` to approximate the prior cadence.

**If a deployer's existing loop has unusual behavior** (e.g., custom backoff curve, ad-hoc rate limiter, status-change-based wakeups instead of polling): leave it alone and report it as a deferred item. Stay mechanical.

- [ ] **Step 3 — Run unit tests for the deployer suite**

```bash
pytest tests/unit/test_deployers*.py -q --maxfail=5 2>&1 | tail -20
```

All existing tests must pass. If a test stubs the old `asyncio.sleep` to drive the loop and now fails because the helper uses `time.monotonic()` instead: the implementer must update the test mock. This is the one case where a test mock change is allowed in Wave 3 — it's not a behavior change, it's a clock-source change inside the helper. Document each such mock update in the commit message.

- [ ] **Step 4 — Wider suite**

```bash
pytest tests/unit/ -q --maxfail=10 --ignore=tests/unit/test_approvals_api.py 2>&1 | tail -10
```

- [ ] **Step 5 — Lint + format**

```bash
ruff check engine/deployers/ tests/unit/
ruff format engine/deployers/ tests/unit/
```

- [ ] **Step 6 — Commit**

```bash
git add engine/deployers/ tests/unit/test_deployers*.py
git commit -m "refactor(deployers): thread poll_until_ready through 6 deployers (W3-01)"
```

**Constraint:** Only `engine/deployers/*.py` and `tests/unit/test_deployers*.py` may change in this commit. Wave 2 utility modules (`engine/deployers/_health.py`) are imported, not re-edited.

**Reporting:**
- For each of 6 deployers: which function was swapped, what `initial_interval` / `max_interval` / `backoff_factor` were chosen, exception-type wrapping pattern.
- Test pass/fail counts.
- Deferred items (deployers where the existing logic was too special-case to mechanically swap).

---

## Task 2: Thread `safe_relative_subdir` through path-accepting callsites

**Why:** W1-01 fixed `markdown_writer.py` inline. Wave 3 promotes that one-off fix to a consistently-applied validation across every user-supplied path fragment. Also closes the door on future tools that forget to validate.

### Files (recon)

```bash
grep -rnE 'subdir\s*[:=]|out_dir|output_path|directory\s*[:=]\s*str' engine/ api/ cli/ 2>&1 | grep -v '__pycache__\|\.pyc\|venv' | head -40
```

Likely targets (verify each before touching):

| File | Param | Notes |
|------|-------|-------|
| `engine/tools/standard/markdown_writer.py` | `subdir` | Already has `_validate_subdir` inline (W1-01) — swap to shared helper, delete inline fn |
| `engine/tools/standard/file_writer.py` (if present) | similar | Likely needs validation |
| `api/routes/rag.py` ingest endpoint | upload subdirectory | If accepts a directory param |
| `api/routes/builders.py` agent export endpoint | export path | If accepts an output dir |
| `cli/commands/init_cmd.py` | template-dir param | Local-only but worth defense-in-depth |
| `engine/mcp/packager.py` | MCP out-dir | If accepts user-supplied output dir |

### Steps

- [ ] **Step 1 — Audit callsites**

For each candidate file, locate the path-accepting parameter and confirm it lacks validation (or has bespoke validation similar to the W1-01 logic). For each callsite that does NOT meet either criterion, leave it alone.

- [ ] **Step 2 — Swap each callsite**

**Pattern A: `markdown_writer.py` (existing inline validator)**

```python
# Before (lines 39-55):
def _validate_subdir(subdir: str) -> str:
    if "\x00" in subdir:
        raise ValueError("subdir must not contain null bytes")
    if subdir.startswith("/") or subdir.startswith("~"):
        raise ValueError(...)
    parts = Path(subdir).parts
    if any(part == ".." for part in parts):
        raise ValueError(...)
    return subdir


def markdown_writer(...):
    ...
    if subdir:
        subdir = _validate_subdir(subdir)
    ...

# After:
from engine.util.path_safety import safe_relative_subdir


def markdown_writer(...):
    ...
    if subdir:
        subdir = safe_relative_subdir(subdir)
    ...
```

The local `_validate_subdir` gets deleted entirely. The test file at `tests/unit/test_standard_tools.py` should continue passing — it tests behavior, not implementation. If a test catches `ValueError`, it should still catch `UnsafePathError` (which subclasses `ValueError`). Verify.

**Pattern B: New-to-validation callsites (e.g., `init_cmd.py`)**

```python
# Before:
def init(template_dir: str):
    out = Path(template_dir)
    out.mkdir(...)

# After:
from engine.util.path_safety import safe_relative_subdir, UnsafePathError

def init(template_dir: str):
    try:
        safe_relative_subdir(template_dir)
    except UnsafePathError as e:
        raise typer.BadParameter(str(e)) from e
    out = Path(template_dir)
    out.mkdir(...)
```

(Choose the framework-appropriate error for each callsite — `typer.BadParameter` in CLI, `HTTPException(422)` in FastAPI routes, `ValueError` if internal.)

- [ ] **Step 3 — Tests**

```bash
pytest tests/unit/test_standard_tools.py tests/unit/test_path_safety.py -v
pytest tests/unit/ -q --maxfail=10 --ignore=tests/unit/test_approvals_api.py 2>&1 | tail -10
```

Existing tests must pass. Add a one-line test per new-to-validation callsite to confirm `..` is rejected.

- [ ] **Step 4 — Lint + format**

```bash
ruff check engine/ api/ cli/ tests/unit/
ruff format engine/ api/ cli/ tests/unit/
```

- [ ] **Step 5 — Commit**

```bash
git add -A   # OR enumerate the swapped files explicitly
git commit -m "refactor(util): adopt safe_relative_subdir across path-accepting callsites (W3-02)"
```

**Reporting:**
- Audited callsites: per-file decision (swapped / left alone / deferred).
- Number of net LOC removed (target: net reduction since `_validate_subdir` deletion).
- Tests added per new-to-validation callsite.

---

## Task 3: Thread `async_retry` through LLM + HTTP-client callsites

**Why:** Audit M-series — provider clients (OpenAI, Anthropic, Google) have ad-hoc retry/no-retry semantics. Connectors and A2A client also lack consistent retry. Centralize on `async_retry` for transient-failure resilience.

### Files (recon)

```bash
grep -rnE 'for attempt in range|max_retries|except.*timeout|httpx\.HTTPStatusError' engine/providers/ engine/a2a/ connectors/ api/services/ api/tasks/ 2>&1 | head -40
```

Likely targets:

| File | What to retry |
|------|---------------|
| `engine/providers/openai_provider.py` | `_call_completions` or `_call_embeddings` |
| `engine/providers/anthropic_provider.py` | `_call_messages` |
| `engine/providers/google_provider.py` | `_call_generate_content` |
| `connectors/litellm/` | HTTP client calls |
| `connectors/openrouter/` | HTTP client calls |
| `engine/a2a/client.py` | JSON-RPC peer calls |
| `api/tasks/provider_health.py` | provider ping |

### Steps

- [ ] **Step 1 — Audit**

Identify the actual retry pattern per file. Common variants:
- Bare `try/except` with no retry — add `async_retry`.
- Hand-rolled `for attempt in range(3)` — replace with `async_retry`.
- Existing retry library (e.g., `tenacity`) — leave alone if it's already idiomatic; flag in the report if you want to consolidate later.

- [ ] **Step 2 — Standard swap pattern**

```python
# Before:
async def _call_completions(self, payload):
    for attempt in range(3):
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                raise
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)

# After:
from api.retry import async_retry, RetryExhaustedError

async def _call_completions(self, payload):
    async def _do() -> dict:
        resp = await self._client.post(url, json=payload)
        if resp.status_code < 500 and not resp.is_success:
            resp.raise_for_status()  # don't retry 4xx
        resp.raise_for_status()
        return resp.json()

    try:
        return await async_retry(
            _do,
            max_attempts=3,
            initial_delay=1.0,
            backoff_factor=2.0,
            retry_on=(httpx.HTTPStatusError, httpx.TimeoutException),
        )
    except RetryExhaustedError as e:
        raise e.last_exception
```

**Care:**
- Only retry on **transient** errors (5xx, timeout, connection reset). Do NOT retry on 4xx — re-raise immediately. The `retry_on=` tuple controls this.
- After `RetryExhaustedError`, the caller likely expects the *original* exception (e.g., `httpx.HTTPStatusError`) — re-raise `e.last_exception` so existing error-handling code keeps working.
- Token-budget or rate-limit (429) is technically a transient error but often needs special handling (respect `Retry-After` header). For Wave 3, treat 429 the same as 5xx and let `async_retry` back off. If a callsite has special 429 handling, leave it alone and report.

- [ ] **Step 3 — Tests**

```bash
pytest tests/unit/test_engine_providers.py tests/unit/test_retry.py -v
pytest tests/unit/ -q --maxfail=10 --ignore=tests/unit/test_approvals_api.py 2>&1 | tail -10
```

Provider tests must still pass. If a test simulates a 5xx and expects exactly 3 attempts, verify the new behavior matches (3 attempts = `max_attempts=3`). If a test was passing because the bare `try/except` swallowed the error: it was masking a bug — update the test to assert the propagated exception.

- [ ] **Step 4 — Lint + format**

```bash
ruff check engine/providers/ engine/a2a/ connectors/ api/services/ api/tasks/ tests/unit/
ruff format engine/providers/ engine/a2a/ connectors/ api/services/ api/tasks/ tests/unit/
```

- [ ] **Step 5 — Commit**

```bash
git add engine/providers/ engine/a2a/ connectors/ api/services/ api/tasks/ tests/unit/
git commit -m "refactor(retry): thread async_retry through provider + connector clients (W3-03)"
```

**Reporting:**
- Per-callsite decision: swapped (with `retry_on=` value), left-alone-because-no-retry-needed, left-alone-because-existing-library, deferred-because-special-case.
- Any test that needed mock-update (and why).

---

## Task 4: Thread `warn_once` + `DegradedFlag` through fallback paths

**Why:** W1-03 introduced bespoke `_FALLBACK_WARNED` set in `rag_service.py`. Now there's a shared `degraded_mode` module — consolidate. Also extend the pattern to provider fallback chains, secrets fallbacks, and Neo4j unavailability.

### Files (recon)

```bash
grep -rnE 'fallback|degraded|warn.*once|silent.*fail' engine/providers/ engine/secrets/ api/services/ 2>&1 | head -30
```

Likely targets:

| File | What to track |
|------|---------------|
| `api/services/rag_service.py` | Replace local `_FALLBACK_WARNED` + `_warn_fallback_once` with shared `warn_once`. Replace `EmbeddingResult.used_fallback` boolean with the shared `DegradedFlag` (optional — only if it cleans up). |
| `engine/providers/registry.py` | When `FallbackChain.generate()` falls back from primary to fallback model, call `warn_once("provider.fallback", f"{primary}->{fallback}")` and set a `DegradedFlag` on the response |
| `engine/secrets/aws_backend.py` / `gcp_backend.py` / `vault_backend.py` / `env_backend.py` | When falling back from preferred backend to env, call `warn_once("secrets.fallback", reason)` |
| `engine/rag/graph.py` (if Neo4j unavailable triggers vector-only degraded mode) | Track via `DegradedFlag` |

### Steps

- [ ] **Step 1 — Swap `rag_service.py`'s local helpers for the shared one**

```python
# Before (rag_service.py):
_FALLBACK_WARNED: set[tuple[str, str]] = set()

def _warn_fallback_once(model: str, reason: str) -> None:
    key = (model, reason)
    if key in _FALLBACK_WARNED:
        return
    _FALLBACK_WARNED.add(key)
    logger.warning(...)

# After:
from engine.observability.degraded_mode import warn_once

# Replace every call to _warn_fallback_once(model, reason) with:
#   warn_once("rag.embedding", reason, extra={"model": model})

# Delete the _FALLBACK_WARNED set and _warn_fallback_once function.
```

The existing `EmbeddingResult` dataclass can stay — it's a result shape, not a tracker. Don't replace its `used_fallback: bool` with `DegradedFlag` unless the latter clearly simplifies. The shared helper is just for the *warning side*, not the return-value side.

**Test impact:** Tests that asserted `_FALLBACK_WARNED.clear()` need to switch to `clear_degraded_state()` from the shared module. Update both `tests/unit/test_rag_service.py` (autouse fixture) and any other test that pokes `_FALLBACK_WARNED` directly.

- [ ] **Step 2 — Add fallback warnings to provider registry**

In `engine/providers/registry.py`, locate the `FallbackChain.generate` method. After a fallback occurs (primary failed, fallback succeeded):

```python
from engine.observability.degraded_mode import warn_once

# Inside FallbackChain.generate after fallback succeeds:
warn_once(
    "provider.fallback",
    f"{primary_name}-to-{fallback_name}",
    extra={"primary": primary_name, "fallback": fallback_name},
)
```

- [ ] **Step 3 — Add fallback warnings to secrets backends**

Each `engine/secrets/*_backend.py` that has a "fallback to env if cloud backend unavailable" path should emit `warn_once("secrets.fallback", "aws-secrets-manager-unreachable")` (or similar reason).

- [ ] **Step 4 — Tests**

```bash
pytest tests/unit/test_rag_service.py tests/unit/test_degraded_mode.py tests/unit/test_engine_providers.py tests/unit/test_engine_secrets.py -v
pytest tests/unit/ -q --maxfail=10 --ignore=tests/unit/test_approvals_api.py 2>&1 | tail -10
```

- [ ] **Step 5 — Lint + format**

```bash
ruff check engine/ api/ tests/unit/
ruff format engine/ api/ tests/unit/
```

- [ ] **Step 6 — Commit**

```bash
git add engine/ api/ tests/unit/
git commit -m "refactor(observability): thread warn_once through fallback paths (W3-04)"
```

**Reporting:**
- Callsites swapped (per file).
- New `warn_once` calls added (per file + reason).
- Tests updated (per file + why).

---

## Task 5: Thread `_validators` field-types through Pydantic models

**Why:** W1-02 introduced inline `Field(ge=1, le=1000)` constraints in `RagSearchRequest`. Wave 2 promoted these to `TopKField`, `HopsField`, etc. Now adopt them in all models that have the same patterns.

### Files (recon)

```bash
grep -rnE 'top_k\s*:|hops\s*:|seed_entity_limit\s*:|vector_weight\s*:|text_weight\s*:' api/models/ 2>&1 | head
```

Likely targets:

| File | Pattern to adopt |
|------|------------------|
| `api/models/schemas.py` `RagSearchRequest` | Already inline (W1-02) — swap to `TopKField`/`HopsField`/`SeedEntityLimitField`/`WeightField` + `make_weights_sum_validator` |
| Any other model with `top_k` / pagination `limit` / weight-summing fields | Adopt where the constraint set matches |

### Steps

- [ ] **Step 1 — Swap `RagSearchRequest`**

```python
# Before:
from pydantic import Field, model_validator

class RagSearchRequest(BaseModel):
    index_id: str = Field(..., min_length=1, description="...")
    query: str = Field(..., min_length=1, max_length=10_000)
    top_k: int = Field(10, ge=1, le=1000)
    vector_weight: float = Field(0.7, ge=0.0, le=1.0)
    text_weight: float = Field(0.3, ge=0.0, le=1.0)
    hops: int | None = Field(None, ge=0, le=10)
    seed_entity_limit: int = Field(5, ge=1, le=50)

    @model_validator(mode="after")
    def weights_must_sum_to_one(self) -> "RagSearchRequest":
        total = self.vector_weight + self.text_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(...)
        return self

# After:
from pydantic import BaseModel, Field

from api.models._validators import (
    HopsField,
    SeedEntityLimitField,
    TopKField,
    WeightField,
    make_weights_sum_validator,
)


class RagSearchRequest(BaseModel):
    index_id: str = Field(..., min_length=1, description="UUID of the target index")
    query: str = Field(..., min_length=1, max_length=10_000)
    top_k: TopKField = 10
    vector_weight: WeightField = 0.7
    text_weight: WeightField = 0.3
    hops: HopsField = None
    seed_entity_limit: SeedEntityLimitField = 5

    _check_weights = make_weights_sum_validator("vector_weight", "text_weight")
```

The behavior must be identical — `TopKField` is `Annotated[int, Field(ge=1, le=1000)]` so the bounds match exactly. The `make_weights_sum_validator` factory produces the same `model_validator(mode="after")`. The previously-inline `weights_must_sum_to_one` method is deleted.

- [ ] **Step 2 — Look for other models with the same patterns**

`api/models/graphrag.py` (if it exists), `api/models/evals.py`, etc. — adopt where the pattern matches. Don't force-fit aliases onto fields with subtly different bounds.

- [ ] **Step 3 — Tests**

```bash
pytest tests/unit/test_rag_routes.py tests/unit/test_model_validators.py -v
pytest tests/unit/ -q --maxfail=10 --ignore=tests/unit/test_approvals_api.py 2>&1 | tail -10
```

Existing tests for `RagSearchRequest` must all pass — same bounds, same validator behavior.

- [ ] **Step 4 — Lint + format**

```bash
ruff check api/models/ tests/unit/
ruff format api/models/ tests/unit/
```

- [ ] **Step 5 — Commit**

```bash
git add api/models/ tests/unit/
git commit -m "refactor(models): adopt _validators field-types in RagSearchRequest (W3-05)"
```

**Reporting:**
- Models swapped (per file).
- Net LOC delta (target: net reduction).
- Any model with a *slightly different* bound that was intentionally NOT swapped — list with reason.

---

## Wave 3 closing

After all 5 tasks land, dispatch a final reviewer to:
1. Confirm full unit suite still green (≥4322 tests).
2. Confirm lint + format clean.
3. Confirm no agent.yaml / DB / CLI / API contract changes leaked in.
4. Append a `### Refactored` section to `CHANGELOG.md` summarizing the Wave 3 consolidations.

The CHANGELOG entry should look like:

```markdown
### Refactored
- **Deployer health-checks** (W3-01): All 6 deployers (AWS ECS, App Runner, GCP Cloud Run, Azure Container Apps, Kubernetes, Docker Compose) now share a single `poll_until_ready` implementation. Behavioral parity with prior bespoke loops; ~150-300 LOC removed.
- **Path validation** (W3-02): All user-supplied-path callsites in tools, routes, and CLI now flow through `safe_relative_subdir`. Closes defense-in-depth gaps in N additional callsites beyond the W1-01 markdown_writer fix.
- **Retry semantics** (W3-03): Provider clients, connectors, A2A client, and provider-health task now use `async_retry` for transient-failure resilience. Consistent backoff, jitter, and retry-on tuples; 4xx errors propagate immediately.
- **Degraded-mode warnings** (W3-04): RAG embedding, provider fallback, and secrets-backend fallback paths now emit dedup'd `warn_once("component", "reason", extra=...)` warnings via the shared `degraded_mode` module.
- **Pydantic field-types** (W3-05): `RagSearchRequest` (and other applicable models) now use the shared `TopKField` / `HopsField` / `SeedEntityLimitField` / `WeightField` aliases + `make_weights_sum_validator` factory.
```

---

## Self-review notes

- **Spec coverage:** 5 Wave 3 tasks map to 5 audit-spec wave-3 themes. ✅
- **Placeholder scan:** Every task spells out before/after patterns. No "TBD."
- **Type consistency:** All utility imports match the symbols exported in Wave 2 (`poll_until_ready`, `HealthCheckTimeout`, `safe_relative_subdir`, `UnsafePathError`, `async_retry`, `RetryExhaustedError`, `warn_once`, `clear_degraded_state`, `DegradedFlag`, `TopKField`, etc.).
- **Risk envelope:** Each task preserves observable behavior (existing tests pass), only consolidates internals. Cross-repo (cloud, website) not affected.
- **Test mock updates:** Task 1 (deployer poll-loops switching from `asyncio.sleep` to `time.monotonic`-based deadline) may legitimately require test mock updates. All other tasks should leave tests untouched.

---

## Execution

Subagent-driven, sequential (not parallel — these tasks may share files across utility groups, e.g., `rag_service.py` touched by W3-04 and indirectly by W3-05 via response field naming). One implementer + one combined reviewer per task. Final reviewer after all 5.
