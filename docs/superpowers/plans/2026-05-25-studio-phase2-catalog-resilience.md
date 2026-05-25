# Studio Phase 2 — Catalog Resilience & Honest Sample-Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the Models page from greeting users with a blocking red "Failed to load catalog" error, make the provider-catalog backend resilient, and honestly label Fleet's seeded cost data — without gating any of the real, working features.

**Architecture:** The model-catalog route (`/api/v1/providers/catalog`) and the `ProviderCatalog` component are correct; the failure mode is poor degradation when the catalog/status fetch transiently fails. This phase (1) hardens the backend `catalog_status` so a secrets-backend hiccup can't 500 it, (2) replaces the frontend's blocking error banner with a non-blocking inline notice + Retry that still renders the provider tabs, and (3) adds a subtle "Sample data" marker to Fleet's seeded cost-anomaly/suggestion sections. No feature gating — verification (2026-05-25) confirmed Memory/RAG/Fleet/Incidents/Compliance/Datasets/Eval Runs are real.

**Tech Stack:** FastAPI (Python, pytest), React + TS + Vite + Tailwind v4, React Query, Vitest + Testing Library, Playwright.

**Branch:** `feat/studio-ux-simplification` (commit per task; no PR until the whole epic passes locally).

---

## File Structure

- `api/routes/providers.py` — wrap the `catalog_status` workspace-backend access in try/except (graceful empty statuses on failure).
- `tests/unit/test_providers_catalog.py` (or extend the existing providers test) — test catalog + status resilience.
- `dashboard/src/components/provider-catalog.tsx` — non-blocking error + Retry; render tabs/list regardless.
- `dashboard/src/components/provider-catalog.test.tsx` (NEW or extend) — component test for the degraded state.
- `dashboard/src/pages/agentops.tsx` — "Sample data" marker on seeded cost-anomaly + suggestion sections.

---

### Task 1: Backend — make catalog status resilient

**Files:**
- Modify: `api/routes/providers.py` (the `catalog_status` handler, ~lines 150-183, and confirm `catalog` ~118-148 cannot 500 on status failure)
- Test: `tests/unit/test_providers_catalog.py` (create if absent; else add to the existing providers test module)

- [ ] **Step 1: Read the two handlers**

Read `api/routes/providers.py` around the `catalog` and `catalog_status` route handlers. Identify the call into the workspace secrets backend (e.g. `get_workspace_backend()` / per-provider key lookups) inside `catalog_status` that can raise when the backend is unconfigured.

- [ ] **Step 2: Write the failing test**

In `tests/unit/test_providers_catalog.py`, add a test that the catalog-status path degrades gracefully when the workspace backend raises. Use the app's existing async test client pattern (copy the import/fixture style from a neighboring `tests/unit/test_*api*.py` or `tests/integration` provider test). Shape:

```python
async def test_catalog_status_degrades_when_backend_unavailable(monkeypatch, client):
    # Force the workspace backend resolution to raise.
    import api.routes.providers as providers_mod
    def boom(*a, **k):
        raise RuntimeError("secrets backend unavailable")
    monkeypatch.setattr(providers_mod, "get_workspace_backend", boom)

    resp = await client.get("/api/v1/providers/catalog/status")
    assert resp.status_code == 200          # not 500
    body = resp.json()
    # statuses come back empty/unconfigured rather than erroring
    assert "data" in body
```

(Adjust `client` fixture name + response envelope to match this repo's conventions — check a sibling test.)

- [ ] **Step 3: Run it to confirm it fails**

Run: `pytest tests/unit/test_providers_catalog.py -v`
Expected: FAIL (currently 500, or the route raises).

- [ ] **Step 4: Implement the try/except**

In `catalog_status`, wrap the workspace-backend access so any exception yields an "unconfigured/empty statuses" response (status 200) and logs a warning via the module logger. Do not change the success path. The `catalog` route itself returns static catalog entries and must remain independent of status — confirm it doesn't depend on the backend.

- [ ] **Step 5: Run the test to confirm it passes**

Run: `pytest tests/unit/test_providers_catalog.py -v`
Expected: PASS. Also run the existing providers tests to confirm no regression: `pytest tests/unit -k provider -v`.

- [ ] **Step 6: Commit**

```bash
git add api/routes/providers.py tests/unit/test_providers_catalog.py
git commit -m "fix(api): providers catalog status degrades gracefully (never 500 on backend hiccup)"
```

---

### Task 2: Frontend — non-blocking catalog error + Retry

**Files:**
- Modify: `dashboard/src/components/provider-catalog.tsx` (the `catalogQuery.error` branch, ~lines 97-101)
- Test: `dashboard/src/components/provider-catalog.test.tsx`

- [ ] **Step 1: Read the component**

Read `dashboard/src/components/provider-catalog.tsx`. Find where `catalogQuery.error` renders the red `Failed to load catalog: {message}` banner and where the provider list/tabs render. Note the React Query object exposes `refetch` and `isFetching`.

- [ ] **Step 2: Write the failing test**

Create/extend `dashboard/src/components/provider-catalog.test.tsx`. Mock `api.providers.catalog` to reject once, render the component (wrapped in a `QueryClientProvider` with retries disabled), and assert: (a) an inline notice with a **Retry** button is shown, (b) it is NOT a full-width blocking banner that replaces all content (the surrounding tab structure / "Add provider" affordance still renders). Example assertions:

```tsx
expect(await screen.findByRole("button", { name: /retry/i })).toBeInTheDocument();
expect(screen.queryByText(/Failed to load catalog/i)).not.toBeInTheDocument(); // old copy gone
```

(Use the project's existing component-test setup — mirror `page-title.test.tsx` for imports and a sibling test that uses React Query for the `QueryClientProvider` wrapper.)

- [ ] **Step 3: Run it to confirm it fails**

Run: `cd dashboard && npx vitest run src/components/provider-catalog.test.tsx`
Expected: FAIL.

- [ ] **Step 4: Implement graceful degradation**

Replace the blocking error branch with a compact, non-blocking inline notice rendered ABOVE the still-present tab/list content: a muted message like "Couldn't reach the model catalog." + a `Retry` button calling `catalogQuery.refetch()` (disabled while `isFetching`). Keep the provider tabs/filters/list rendering using whatever data is available (empty list is fine — the existing "No models match your filters" empty state covers it). Use Tailwind classes consistent with the codebase; no inline styles.

- [ ] **Step 5: Run the test to confirm it passes**

Run: `cd dashboard && npx vitest run src/components/provider-catalog.test.tsx`
Expected: PASS.

- [ ] **Step 6: Typecheck + build**

Run: `cd dashboard && npx tsc --noEmit && npm run build`
Expected: both succeed.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/components/provider-catalog.tsx dashboard/src/components/provider-catalog.test.tsx
git commit -m "fix(studio): models catalog degrades gracefully with retry, no blocking error"
```

---

### Task 3: Honest "Sample data" marker on Fleet seeded sections

**Files:**
- Modify: `dashboard/src/pages/agentops.tsx` (the cost-anomaly + cost-suggestion sections backed by `_SEED_*` data)

- [ ] **Step 1: Locate the seeded sections**

Read `dashboard/src/pages/agentops.tsx`. Find the UI sections that render cost anomalies and cost suggestions (backed server-side by `_SEED_COST_ANOMALIES` / `_SEED_COST_SUGGESTIONS` in `api/services/agentops_service.py`). These are the only non-real parts of the Fleet page.

- [ ] **Step 2: Add the marker**

Add a small, subtle inline badge/label — text "Sample data" — to each of those two section headers (a muted pill, Tailwind classes consistent with the existing `ComingSoonBadge` styling but worded "Sample data", or reuse a small `<span class="...muted pill...">Sample data</span>`). Do not gate or hide the sections; just label them honestly. Leave the real Fleet/heatmap/events sections untouched.

- [ ] **Step 3: Typecheck + build**

Run: `cd dashboard && npx tsc --noEmit && npm run build`
Expected: both succeed.

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/pages/agentops.tsx
git commit -m "feat(studio): label Fleet seeded cost sections as 'Sample data'"
```

---

### Task 4: Verify the phase

- [ ] **Step 1: Backend + frontend test suites**

Run: `pytest tests/unit -k "provider or catalog" -v` (green) and `cd dashboard && npx vitest run` (green) and `npx tsc --noEmit` (clean).

- [ ] **Step 2: Controller browser verification (done by the controller, not a subagent)**

The controller will run the dev server and confirm: switching to the Gateways tab with the catalog reachable shows providers; when unreachable, the page shows the inline retry notice (not a blocking red banner) and the tabs still render; Fleet shows the "Sample data" labels on the cost sections.

---

## Self-Review

**Spec coverage:** §G catalog graceful-degrade (Task 2) + backend resilience (Task 1) ✓; §E revised verdict — no gating of real features, Fleet "sample data" marker (Task 3) ✓. The existing Local/custom-provider `ComingSoonBadge` is untouched (already correct) ✓.

**Placeholder scan:** Tasks 1-2 instruct the implementer to mirror existing test fixtures (the repo's async client / React Query wrapper) rather than inventing them — that's "read the sibling and copy the pattern", not a placeholder. No "handle errors appropriately"-style vagueness; each task states the exact behavior.

**Type/name consistency:** `catalogQuery.refetch()` / `isFetching` are the standard React Query members used in Task 2. `catalog_status` is the handler named consistently in Task 1. "Sample data" is the exact marker text in Task 3.
