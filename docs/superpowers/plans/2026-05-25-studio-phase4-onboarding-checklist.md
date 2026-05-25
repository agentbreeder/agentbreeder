# Studio Phase 4 — Home "Get Started" Checklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give Studio's Home page a guided 4-step "Get Started" checklist that tracks real progress and funnels new users into creating their first agent — replacing the auto-opening welcome tour as the first-run guide.

**Architecture:** A new `GetStartedChecklist` component renders above the Home stat cards, showing four steps with done/active/locked states derived from real signals (active providers, agent count, a Playground-used localStorage flag, deploy count). It self-dismisses (persisted) once all steps are done or the user closes it. The welcome tour stops auto-opening (it stays available via the shell's "Restart tour"). The "Create your first agent" CTA routes to the existing `/agents/builder` until the Phase-5 wizard (`/agents/new`) lands.

**Tech Stack:** React + TS + Vite + Tailwind v4, React Query, shadcn `Card`/`Badge`/`Button`, Vitest + Testing Library, Playwright.

**Branch:** `feat/studio-ux-simplification` (commit per task; no PR until the whole epic passes locally).

---

## File Structure

- `dashboard/src/components/get-started-checklist.tsx` — NEW: the checklist (queries + step states + dismiss).
- `dashboard/src/components/get-started-checklist.test.tsx` — NEW: tests for progress derivation + states + dismiss.
- `dashboard/src/pages/home.tsx` — render the checklist above the stats.
- `dashboard/src/pages/playground.tsx` — set `localStorage["ag-playground-used-v1"]="1"` on first successful chat.
- `dashboard/src/hooks/use-tour.tsx` — stop auto-open; bump key to `ag-tour-completed-v2`.

---

### Task 1: Mark Playground as "used" on first successful chat

**Files:** Modify `dashboard/src/pages/playground.tsx`

- [ ] **Step 1** — Read `playground.tsx` to find where a chat response resolves (the `api.playground.chat` success path / mutation `onSuccess`).
- [ ] **Step 2** — On a successful chat response, set the flag (idempotent):

```ts
localStorage.setItem("ag-playground-used-v1", "1");
```

Place it in the success handler so it only fires after a real response. No other behavior change.
- [ ] **Step 3** — `cd dashboard && npx tsc --noEmit` → clean.
- [ ] **Step 4** — Commit: `git commit -m "feat(studio): record first Playground use for onboarding"`

---

### Task 2: `GetStartedChecklist` component

**Files:** Create `dashboard/src/components/get-started-checklist.tsx` + `.test.tsx`

- [ ] **Step 1: Write the failing test** (`get-started-checklist.test.tsx`) — wrap in the repo's `QueryClientProvider` (retries off; mirror `provider-catalog.test.tsx`). Mock `api.providers.list`, `api.agents.list`, `api.deploys.list`. Cover:
  - all four steps render with labels;
  - a step shows "done" when its signal is satisfied (e.g. `agents.list` meta.total = 2 → step 2 done) and "active"/"locked" otherwise (first not-done step is active, rest locked);
  - when ALL signals satisfied + playground flag set, the component renders nothing (auto-dismissed);
  - clicking the dismiss control hides it and persists `ag-getstarted-dismissed-v1`.

Example assertion shape:
```tsx
it("marks 'Create your first agent' done when agents exist", async () => {
  mockApi({ agents: { meta: { total: 2 } } });
  renderWithClient(<GetStartedChecklist />);
  const step = await screen.findByTestId("step-create-agent");
  expect(step).toHaveAttribute("data-state", "done");
});
```

- [ ] **Step 2** — Run; expect FAIL (module missing).
- [ ] **Step 3: Implement** `get-started-checklist.tsx`:
  - Queries: `providers = useQuery(api.providers.list({ status: "active" }))`, `agents = useQuery(api.agents.list({ per_page: 1 }))`, `deploys = useQuery(api.deploys.list({ per_page: 1 }))`. (Confirm exact method signatures in `api.ts`; `per_page` may be `perPage`/`page_size` — match the codebase.) Playground signal: `localStorage.getItem("ag-playground-used-v1") === "1"`.
  - Steps (in order), each `{ id, label, description, done, cta? }`:
    1. **Connect a model** — done when `providers.meta.total > 0`. CTA → `/models`.
    2. **Create your first agent** — done when `agents.meta.total > 0`. CTA → `/agents/builder` (primary, prominent).
    3. **Test it in the Playground** — done when the localStorage flag is set. CTA → `/playground`.
    4. **Deploy — or keep local** — done when `deploys.meta.total > 0`. CTA → `/deploy` (or the deploy-wizard route — confirm path). Mark this step optional/skippable.
  - State logic: a step is `done` if its signal is true; the first non-done step is `active`; subsequent non-done steps are `locked` (CTA shown only for done+active, muted for locked). Use `data-state` + `data-testid` attributes for testability.
  - Dismiss: a small "Dismiss" / "×" control sets `ag-getstarted-dismissed-v1="1"` and hides. If `dismissed` OR all four done → render `null`.
  - Use shadcn `Card`/`CardHeader`/`CardContent`, `Badge` for state labels, `Button`/`Link` for CTAs. Colors: `emerald-500` done, `primary` active, `muted`/`zinc-700` locked. Bricolage `font-display` on the panel heading ("Welcome — let's ship your first agent") for brand consistency.
- [ ] **Step 4** — Run tests → PASS. `npx tsc --noEmit` clean.
- [ ] **Step 5** — Commit: `git commit -m "feat(studio): GetStartedChecklist onboarding component"`

---

### Task 3: Render the checklist on Home

**Files:** Modify `dashboard/src/pages/home.tsx`

- [ ] **Step 1** — Import `GetStartedChecklist`. Insert `<GetStartedChecklist />` between the `PageTitle` block (~line 79) and the stats grid (~line 81). The component self-hides when done/dismissed, so no conditional needed in `home.tsx` (it returns null internally).
- [ ] **Step 2** — `npx tsc --noEmit` clean; `npm run build` ok.
- [ ] **Step 3** — Commit: `git commit -m "feat(studio): show Get Started checklist on Home"`

---

### Task 4: Retire the welcome tour's auto-open

**Files:** Modify `dashboard/src/hooks/use-tour.tsx`

- [ ] **Step 1** — Read `use-tour.tsx`. Change the `isOpen` initialization so the tour does NOT auto-open on first load (initialize `false`); keep `open()`/`close()` so the shell's "Restart tour" still works. Bump `STORAGE_KEY` from `ag-tour-completed-v1` to `ag-tour-completed-v2` so existing users don't get a now-redundant auto-open (the value check stays `=== "1"`).
- [ ] **Step 2** — Confirm the shell "Restart tour" affordance still calls `open()` (grep `shell.tsx` for the tour usage; no change expected, just verify). `npx tsc --noEmit` clean.
- [ ] **Step 3** — Commit: `git commit -m "feat(studio): checklist replaces tour auto-open (tour stays on-demand)"`

---

### Task 5: Verify

- [ ] **Step 1** — `cd dashboard && npx vitest run` (green), `npx tsc --noEmit` (clean), `npm run build` (ok).
- [ ] **Step 2** — Playwright (`dashboard/tests/e2e/`, reuse `authedPage`): on `/` with no agents, assert the checklist renders with "Create your first agent" CTA linking to `/agents/builder`, and that the tour modal does NOT auto-open (assert the welcome-tour dialog is absent without seeding the tour-completed key). Run `npm run test:e2e -- get-started` (or the chosen spec name).
- [ ] **Step 3 (controller)** — Browser verification by the controller.

---

## Self-Review

**Spec coverage (§B):** 4-step checklist with real progress ✓; funnels to agent creation CTA ✓; stats demoted below ✓ (Task 3 inserts above stats); tour auto-open retired so the checklist is the first-run guide ✓ (Task 4). The wizard CTA routes to `/agents/builder` now and will repoint to `/agents/new` in Phase 5 (noted).

**Placeholder scan:** test bodies are concrete; the `per_page` pagination param + the deploy route path are flagged as "confirm against `api.ts`/router" (a real verification instruction, not a placeholder). No vague "handle states".

**Type/name consistency:** localStorage keys are exact and reused across tasks (`ag-playground-used-v1` written in Task 1, read in Task 2; `ag-getstarted-dismissed-v1` in Task 2; `ag-tour-completed-v2` in Task 4). `GetStartedChecklist` named consistently (Tasks 2,3).
