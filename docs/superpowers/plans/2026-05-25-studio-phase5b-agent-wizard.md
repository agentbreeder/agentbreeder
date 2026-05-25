# Studio Phase 5b — Guided Agent Wizard (`/agents/new`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A guided, full-page agent wizard at `/agents/new` — Goal → Workflow → Review recommended stack (editable) → Name & create — that calls `POST /api/v1/builders/recommend`, emits a valid `agent.yaml`, creates the agent via `POST /api/v1/agents/from-yaml`, and routes to the new agent. This is the in-product surfacing of the `/agent-build` flow.

**Architecture:** Mirror the existing **deploy-wizard** pattern (`dashboard/src/pages/deploy-wizard.tsx` + `components/deploy-wizard/`): `useReducer` state, `canAdvance(state, step)` gating, `?step=N` in the URL, a `StepIndicator`. Steps 1-2 collect inputs; Step 3 calls `api.builders.recommend()` and shows framework/model/deploy as **editable** fields plus rag/memory/mcp/evals as read-only "recommended next steps" guidance; Step 4 collects name/team/owner, assembles an `AgentFormData`, emits YAML via the existing `formDataToYaml()`, validates via `api.agents.validate`, creates via `api.agents.fromYaml`, then navigates. The Home checklist CTA is repointed from `/agents/builder` to `/agents/new`.

**Tech Stack:** React + TS + Vite + Tailwind v4, React Query (`useMutation`), React Router v7, Vitest + Testing Library, Playwright. Reuses `agent-yaml-emit.ts` (no new YAML lib).

**Branch:** `feat/studio-ux-simplification` (commit per task; no PR until the whole epic passes locally).

---

## File Structure

- `dashboard/src/lib/agent-wizard-state.ts` — NEW: state type, initial state, reducer, `canAdvance`, and `recommendationToFormData()` mapping.
- `dashboard/src/components/agent-wizard/StepIndicator.tsx` — NEW: 4-step variant (copy deploy-wizard's, labels `["Goal","Workflow","Stack","Create"]`).
- `dashboard/src/components/agent-wizard/Step1Goal.tsx`, `Step2Workflow.tsx`, `Step3Stack.tsx`, `Step4Create.tsx` — NEW step components.
- `dashboard/src/pages/agent-wizard.tsx` — NEW: page shell (reducer + step switch + indicator + nav).
- `dashboard/src/App.tsx` — add `<Route path="agents/new" element={<AgentWizardPage />} />` before `agents/:id`.
- `dashboard/src/components/get-started-checklist.tsx` — repoint CTA `/agents/builder` → `/agents/new`.
- Tests: `agent-wizard-state.test.ts`, `agent-wizard.test.tsx`, `dashboard/tests/e2e/agent-wizard.spec.ts`.

---

### Task 1: Wizard state + recommendation→formData mapping

**Files:** Create `dashboard/src/lib/agent-wizard-state.ts`; Test `dashboard/src/lib/agent-wizard-state.test.ts`

State shape:
```ts
export interface AgentWizardState {
  step: 1 | 2 | 3 | 4;
  // Step 1
  businessGoal: string;
  cloudPreference: "aws" | "gcp" | "azure" | "local";
  scaleProfile: "realtime" | "batch" | "event_driven" | "low_volume";
  languagePreference: "python" | "typescript" | "none";
  // Step 2
  workflow: string;                 // textarea, one step per line
  stateFlags: string[];             // subset of a..e
  dataFlags: string[];              // subset of a..e
  // Step 3 (recommendation, editable)
  recommendation: Recommendation | null;
  framework: string;                // editable, seeded from recommendation
  modelPrimary: string;             // editable
  deployCloud: string;              // editable, mapped from deploy_target
  // Step 4
  name: string; version: string; team: string; owner: string;
}
```

- `canAdvance(state, target)`: to step 2 requires `businessGoal.trim()`; to step 3 requires `workflow.trim()`; to step 4 requires `recommendation !== null`; create requires a valid slug `name` + email `owner`.
- `recommendationToFormData(state)`: build an `AgentFormData` (import the type + `emptyFormData()` from `agent-yaml-emit.ts`): set `name, version, team, owner`, `framework = state.framework`, `model.primary = state.modelPrimary`, `deploy.cloud = state.deployCloud`, `description` = first line of businessGoal, `tags` = `[]`. The `deploy_target → cloud` map (`ecs_fargate→aws, cloud_run→gcp, azure_container_apps→azure, docker_compose→local`) lives in a helper `deployTargetToCloud(t)`.

- [ ] **Step 1: failing test** (`agent-wizard-state.test.ts`): assert initial step=1; `canAdvance` gates per the rules above; `deployTargetToCloud("cloud_run")==="gcp"`; `recommendationToFormData` produces an object with the 7 required leaves populated and `framework`/`model.primary`/`deploy.cloud` from the edited state.
- [ ] **Step 2** — run → FAIL.
- [ ] **Step 3** — implement the state module (reducer actions: `SET_FIELD`, `SET_RECOMMENDATION`, `GOTO_STEP`). Pure, typed, no `any`.
- [ ] **Step 4** — run → PASS. `cd dashboard && npx tsc --noEmit` clean.
- [ ] **Step 5** — commit: `git commit -m "feat(studio): agent wizard state + recommendation→formData mapping"`

---

### Task 2: 4-step StepIndicator + page shell + route

**Files:** Create `components/agent-wizard/StepIndicator.tsx`, `pages/agent-wizard.tsx`; modify `App.tsx`

- [ ] **Step 1** — Copy `components/deploy-wizard/StepIndicator.tsx` to `components/agent-wizard/StepIndicator.tsx`; change the step literal type to `1|2|3|4` and labels to `["Goal","Workflow","Stack","Create"]`.
- [ ] **Step 2** — Create `pages/agent-wizard.tsx`: `useReducer` over the Task-1 state, `useSearchParams` to sync `?step=N`, render `<StepIndicator current canAdvanceTo onJump />` + a `switch(step)` rendering the four step components (Task 3-5) with `{state, dispatch}`, plus Back/Next buttons gated by `canAdvance`. Wrap content in the standard page container + a `<PageTitle>Create an agent</PageTitle>`.
- [ ] **Step 3** — In `App.tsx`, import `AgentWizardPage` and add `<Route path="agents/new" element={<AgentWizardPage />} />` immediately BEFORE the `agents/:id` route (param routes last), inside `RequireAuth`.
- [ ] **Step 4** — `npx tsc --noEmit` clean; `npm run build` ok (step components can be minimal stubs returning a heading until Tasks 3-5 — but to keep build green, create all four files now as typed stubs taking `{state,dispatch}`).
- [ ] **Step 5** — commit: `git commit -m "feat(studio): /agents/new wizard shell + route + step indicator"`

---

### Task 3: Step 1 (Goal) + Step 2 (Workflow)

**Files:** `components/agent-wizard/Step1Goal.tsx`, `Step2Workflow.tsx`

- [ ] **Step 1** — Step1Goal: a textarea "What problem does this agent solve?" (→ `businessGoal`) + three labelled selects (cloud preference, scale profile, language) wired via `dispatch(SET_FIELD)`. Use shadcn form controls already in the repo (check `components/ui/`).
- [ ] **Step 2** — Step2Workflow: a textarea "Describe the workflow — one step per line" (→ `workflow`) + two checkbox groups: "Does it need…" loops/checkpoints/human-approval/parallel (→ `stateFlags` a/b/c/d) and "What data…" unstructured-docs/database/knowledge-graph/live-APIs (→ `dataFlags` a/b/c/d). Friendly labels mapping to the a-d codes.
- [ ] **Step 3** — Add component tests in `agent-wizard.test.tsx` (or per-file) asserting typing the goal enables Next, and selections update state. tsc clean.
- [ ] **Step 4** — commit: `git commit -m "feat(studio): wizard Goal + Workflow steps"`

---

### Task 4: Step 3 (Review stack)

**Files:** `components/agent-wizard/Step3Stack.tsx`

- [ ] **Step 1** — On entering step 3 (or via a "Recommend" button), call `useMutation(() => api.builders.recommend({ business_goal, technical_use_case: workflow, state_flags, cloud_preference, language_preference, data_flags, scale_profile }))`. On success `dispatch(SET_RECOMMENDATION, rec)` and seed `framework`/`modelPrimary`/`deployCloud` (via `deployTargetToCloud(rec.deploy_target)`).
- [ ] **Step 2** — Render: **editable** controls for framework (select of the 6 enum values), model primary (text/select), deploy cloud (select aws/gcp/azure/local). Below, **read-only** "Recommended next steps" cards for `rag`, `memory`, `mcp_a2a`, and `eval_dimensions` (display the values + the matching `reasoning[...]` text) — clearly labelled as guidance, not blocking. Pending/error states for the mutation (non-blocking error + retry).
- [ ] **Step 3** — Test: mock `api.builders.recommend` resolving to a known stack; assert the editable fields seed from it, the guidance cards render rag/memory, and editing framework updates state. tsc clean.
- [ ] **Step 4** — commit: `git commit -m "feat(studio): wizard Review-stack step (recommend + editable + guidance)"`

---

### Task 5: Step 4 (Name & create) — emit YAML, validate, create, navigate

**Files:** `components/agent-wizard/Step4Create.tsx`

- [ ] **Step 1** — Fields: name (slug; show the pattern hint), version (default `1.0.0`), team (default `engineering`), owner (email; prefill from the current user's email if available via the auth context). 
- [ ] **Step 2** — A "Create agent" action: build `AgentFormData` via `recommendationToFormData(state)`, emit YAML via `formDataToYaml(data)`, call `api.agents.validate(yaml)`; if invalid, show the errors inline and stop; if valid, `api.agents.fromYaml(yaml)` then `navigate(\`/agents/${created.id}\`)`. Use `useMutation`; disable the button while pending; surface server errors inline (non-blocking).
- [ ] **Step 3** — Test: fill state, mock `validate` (valid) + `fromYaml` (returns `{id}`); assert clicking Create calls both and navigates. Also a test where `validate` returns errors → they render and `fromYaml` is NOT called.
- [ ] **Step 4** — `npx tsc --noEmit` clean; `npm run build` ok.
- [ ] **Step 5** — commit: `git commit -m "feat(studio): wizard Create step — emit/validate/create agent"`

---

### Task 6: Repoint the Home checklist CTA

**Files:** `dashboard/src/components/get-started-checklist.tsx`

- [ ] **Step 1** — Change the "Create your first agent" CTA `to="/agents/builder"` → `to="/agents/new"`. Update the existing checklist test that asserts the `/agents/builder` href to `/agents/new`.
- [ ] **Step 2** — `npx vitest run get-started-checklist` green; tsc clean.
- [ ] **Step 3** — commit: `git commit -m "feat(studio): point Get Started CTA to the new agent wizard"`

---

### Task 7: Verify

- [ ] **Step 1** — `cd dashboard && npx vitest run` (green), `npx tsc --noEmit` (clean), `npm run build` (ok).
- [ ] **Step 2** — Playwright (`dashboard/tests/e2e/agent-wizard.spec.ts`, reuse `authedPage`, mock `api/v1/builders/recommend`, `agents/validate`, `agents/from-yaml`): navigate `/agents/new`; fill Goal → Next; fill Workflow → Next; assert Stack step shows seeded framework + guidance cards; on Create, assert it posts to `from-yaml` and navigates. Run `npm run test:e2e -- agent-wizard`.
- [ ] **Step 3 (controller)** — Browser verification by the controller.

---

## Self-Review

**Spec coverage (§C):** full-page `/agents/new` 4-step wizard ✓; surfaces the `/agent-build` flow via the shared recommend endpoint ✓; framework/model/deploy editable, rag/memory/mcp/evals shown as guidance ✓; writes a schema-valid `agent.yaml` through the existing `from-yaml` path (does NOT bypass the config parser) ✓; Home CTA repointed ✓. The chat-to-build tab + plugin remain Phases 7-8.

**Placeholder scan:** the recommend→formData mapping + the deploy_target→cloud table are concrete; step components specify exact fields + the minimal-valid YAML (7 required leaves). "Mirror deploy-wizard" / "use shadcn ui controls" are concrete reuse instructions (the reference files are named). Tests give explicit assertions.

**Type/name consistency:** `recommendationToFormData`/`deployTargetToCloud` defined in Task 1, used in Tasks 4-5; `AgentWizardState` field names consistent across the reducer (1), steps (3-5); `AgentFormData`/`formDataToYaml`/`emptyFormData` come from the existing `agent-yaml-emit.ts`; the recommend request fields match the Phase-5a `RecommendInput`.
