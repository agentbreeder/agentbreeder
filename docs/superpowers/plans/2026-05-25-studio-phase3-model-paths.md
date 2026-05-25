# Studio Phase 3 — Model Path Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the flat 3-tab `/models` layout with one question — "How do you want to run models?" — and three clear paths: **Local (free)**, **Gateway (recommended)**, **Direct provider (advanced)**.

**Architecture:** The self-contained `Tabs` block in `models.tsx` (lines ~606-664) is replaced by a `ModelPathChooser` that toggles between three panels. **Local** becomes real by wiring the existing `POST /api/v1/providers/detect-ollama` endpoint (un-gating #216 on this page). **Gateway** and **Direct** reuse the existing `ProviderCatalog` (filtered by `gateway` / `openai_compatible`); Direct adds a "configure OpenAI/Anthropic/Google in Settings" pointer for the foundation providers (which live in the Settings flow, not the catalog) and collapses the 8 niche providers behind a "More providers (advanced)" disclosure. The registered-models table + filter pills below the chooser are unchanged.

**Tech Stack:** React + TS + Vite + Tailwind v4, React Query, Vitest + Testing Library, Playwright. Backend endpoint already exists.

**Branch:** `feat/studio-ux-simplification` (commit per task; no PR until the whole epic passes locally).

---

## File Structure

- `dashboard/src/lib/api.ts` — add `providers.detectOllama()` client method (endpoint exists; client method missing).
- `dashboard/src/components/model-path-chooser.tsx` — NEW: the 3-path frame (segmented chooser + panel switch).
- `dashboard/src/components/model-path-chooser.test.tsx` — NEW: component tests.
- `dashboard/src/components/provider-catalog.tsx` — add optional grouping so `openai_compatible` providers render with the 8 niche ones collapsed under an "Advanced" disclosure (a hardcoded `ADVANCED_PROVIDERS` set), and an optional foundation-providers pointer slot.
- `dashboard/src/pages/models.tsx` — replace the Tabs block (~606-664) with `<ModelPathChooser />`; keep header, table, and filter pills.

---

### Task 1: Add `detectOllama` API client method

**Files:** Modify `dashboard/src/lib/api.ts` (the `providers` object, near `catalog`/`catalogStatus` ~line 1458-1464)

- [ ] **Step 1** — Read `api.ts` `providers` object + how `request()` posts (find an existing POST method for the pattern). Confirm the backend route shape: `POST /api/v1/providers/detect-ollama` (see `api/routes/providers.py:85`) — note its response body (registered provider + discovered models).
- [ ] **Step 2** — Add a typed method:

```ts
detectOllama: () => request<{ provider: string; models: string[]; base_url?: string }>(
  "/providers/detect-ollama",
  { method: "POST" }
),
```

Adjust the response type to match the actual route's return shape (read the handler). No `any`.
- [ ] **Step 3** — `cd dashboard && npx tsc --noEmit` → clean.
- [ ] **Step 4** — Commit: `git commit -m "feat(studio): detectOllama API client method"`

---

### Task 2: `ModelPathChooser` component (the 3-path frame)

**Files:** Create `dashboard/src/components/model-path-chooser.tsx` + `.test.tsx`

- [ ] **Step 1: Write the failing test** (`model-path-chooser.test.tsx`)

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect } from "vitest";
import { ModelPathChooser } from "./model-path-chooser";
// wrap in QueryClientProvider (retries off) like other RQ component tests

describe("ModelPathChooser", () => {
  it("renders the three paths with Gateway recommended", () => {
    renderWithClient(<ModelPathChooser />);
    expect(screen.getByText(/local/i)).toBeInTheDocument();
    expect(screen.getByText(/gateway/i)).toBeInTheDocument();
    expect(screen.getByText(/direct/i)).toBeInTheDocument();
    expect(screen.getByText(/recommended/i)).toBeInTheDocument();
  });

  it("defaults to the Gateway panel and switches on click", async () => {
    renderWithClient(<ModelPathChooser />);
    // Gateway panel content present by default
    expect(screen.getByText(/one key/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /local/i }));
    expect(screen.getByRole("button", { name: /detect ollama/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2** — Run it; expect FAIL (module missing).
- [ ] **Step 3: Implement** `model-path-chooser.tsx`:
  - A segmented control / card row with three options: **Local** (`🖥️ Free`), **Gateway** (`🌐 Recommended`), **Direct provider** (`🔑 Advanced`). Default selection = `gateway`.
  - Renders the matching panel below: `<LocalPath />`, `<GatewayPath />`, `<DirectPath />` (define these as local sub-components in the same file unless any grows large, then split — see plan file-structure).
  - **GatewayPath**: short blurb ("One key → 100+ models. Foundation-model keys flow through here.") + `<ProviderCatalog filter="gateway" heading="Model Gateways" />`.
  - **LocalPath**: blurb ("Runs on your machine. No API key.") + a **"Detect Ollama"** button → `useMutation(api.providers.detectOllama)`; on success list the returned models; on error show an inline notice (Ollama not reachable) — do NOT block. (Task 3 fills detail.)
  - **DirectPath**: a foundation-providers pointer ("Using OpenAI, Anthropic, or Google? Configure them in Settings →" linking to `/settings`) + `<ProviderCatalog filter="openai_compatible" collapseAdvanced />` (Task 4 adds the prop).
  - Tailwind only, fully typed, no `any`.
- [ ] **Step 4** — Run the test → PASS. `npx tsc --noEmit` clean.
- [ ] **Step 5** — Commit: `git commit -m "feat(studio): ModelPathChooser — Local/Gateway/Direct frame"`

---

### Task 3: Wire the Local (Ollama) path

**Files:** `dashboard/src/components/model-path-chooser.tsx` (LocalPath)

- [ ] **Step 1** — Implement LocalPath fully: `useMutation` on `api.providers.detectOllama`. States: idle (show "Detect Ollama" + helper text), pending (spinner/disabled), success (list `models`, show base_url, success note), error (inline "Couldn't reach Ollama at localhost:11434 — is it running?" with a Retry; non-blocking). Use the existing toast/notification pattern if the codebase has one; otherwise inline.
- [ ] **Step 2** — Add/extend a test in `model-path-chooser.test.tsx`: mock `api.providers.detectOllama` resolving to `{ provider: "ollama", models: ["llama3.2:3b"] }`, click Detect, assert the model is listed. Mock a rejection, assert the inline error + Retry.
- [ ] **Step 3** — Run tests → PASS. tsc clean.
- [ ] **Step 4** — Commit: `git commit -m "feat(studio): wire Local path to Ollama detection"`

---

### Task 4: Direct-path advanced disclosure in `ProviderCatalog`

**Files:** Modify `dashboard/src/components/provider-catalog.tsx`

- [ ] **Step 1** — Read the row-render block (~lines 172-180) and props (~44-58). Add an optional prop `collapseAdvanced?: boolean` (default false → current behavior unchanged for other call sites).
- [ ] **Step 2** — When `collapseAdvanced` is true and `filter === "openai_compatible"`, split `presets` using a hardcoded set:

```ts
const ADVANCED_PROVIDERS = new Set([
  "cerebras", "deepinfra", "fireworks", "groq",
  "hyperbolic", "moonshot", "nvidia", "together",
]);
```

Render any non-advanced presets first (currently there are none in the catalog, so this list may be empty — that's fine), then a collapsed `<details>`/disclosure "More providers (advanced) — N" containing the advanced ones. Keep all existing row markup (`CatalogRow`) inside.
- [ ] **Step 3** — Add a test (extend `provider-catalog.test.tsx`): with `collapseAdvanced` and a catalog of niche providers, assert they are inside a collapsed disclosure (not shown until expanded) and that the disclosure label shows the count. Confirm the default (no `collapseAdvanced`) still renders the flat list (existing tests stay green).
- [ ] **Step 4** — Run tests → PASS; tsc clean; `npm run build` ok.
- [ ] **Step 5** — Commit: `git commit -m "feat(studio): ProviderCatalog collapseAdvanced disclosure"`

---

### Task 5: Swap `ModelPathChooser` into `/models`

**Files:** Modify `dashboard/src/pages/models.tsx`

- [ ] **Step 1** — Read lines ~601-664. Replace the entire `Tabs` block (the `<div className="mb-4">` wrapping TabsList/TabsContent for direct/gateways/local + the Sync button placement) with `<ModelPathChooser />` (import it). Preserve the Sync button — move it into `ModelPathChooser`'s header OR keep it in the page header next to the existing actions (choose whichever reads cleaner; the Sync mutation logic stays). Keep the `PageTitle`, the registered-models filter pills (~666-686), and the model table (~717-810) exactly as-is.
- [ ] **Step 2** — Remove now-dead imports (Tabs/TabsList/TabsTrigger/TabsContent, ComingSoonBadge if only used by the old Local tab — check) only if nothing else uses them. Run `npx tsc --noEmit` → clean (catches dangling refs).
- [ ] **Step 3** — `npm run build` → ok.
- [ ] **Step 4** — Commit: `git commit -m "feat(studio): reframe /models around three model paths"`

---

### Task 6: Verify

- [ ] **Step 1** — `cd dashboard && npx vitest run` (all green), `npx tsc --noEmit` (clean), `npm run build` (ok).
- [ ] **Step 2** — Add a Playwright check in `dashboard/tests/e2e/` (new `model-paths.spec.ts` or extend brand-foundation): on `/models`, assert the three path options render, Gateway is default/marked recommended, and clicking Local reveals a "Detect Ollama" control. Reuse the `authedPage` fixture. Run `npm run test:e2e -- model-paths`.
- [ ] **Step 3 (controller)** — Browser verification by the controller: `/models` shows the 3-path chooser; Gateway shows gateways; Direct shows the advanced disclosure + Settings pointer; Local shows Detect Ollama; the model table below still works.

---

## Self-Review

**Spec coverage (§A):** three paths ✓ (Task 2); Gateway recommended + reuses catalog ✓; Direct advanced-collapse + foundation pointer ✓ (Task 4); Local real via detect-ollama ✓ (Tasks 1,3, per the approved "wire it up" decision); keys still via secrets backend (unchanged — `Configure` flow untouched) ✓. The `ModelPathPicker`-reuse-in-builder/playground item from the spec is explicitly deferred (RegistryPicker uses mock data; out of scope this phase — noted in research).

**Placeholder scan:** test bodies show real assertions; the `renderWithClient` helper is "mirror the existing RQ component-test wrapper" (a concrete instruction, the repo has the pattern). No vague "handle errors" — each panel's states are enumerated.

**Type/name consistency:** `detectOllama` (Task 1) used in Tasks 2-3; `collapseAdvanced` prop (Task 4) used by DirectPath (Task 2); `ModelPathChooser` named consistently across Tasks 2,5.
