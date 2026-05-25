# AgentBreeder Studio — UX Simplification & Brand Alignment

**Date:** 2026-05-24
**Status:** Design (approved in brainstorming; pending written-spec review)
**Surface:** `dashboard/` (Studio web app), with supporting changes in `api/` and `cli/`.

## Problem

New users open Studio (`localhost:3001`) and are overwhelmed and lost:

- **Model/provider overload.** `/models` (`dashboard/src/pages/models.tsx`, ~815 lines) leads with a flat list of ~12 providers across three tabs (Direct providers / Gateways / Local), greets users with a red "Failed to load catalog: Failed to fetch" error, and shows "0 models in registry". There's no clear "just pick this".
- **No "start here".** Home (`dashboard/src/pages/home.tsx`) shows empty stat cards and "Deploy one with `agentbreeder deploy`" — no guided path. The first-run tour (`use-tour.tsx`, `welcome-tour.tsx`) is a 4-step modal that deep-links but doesn't track progress or funnel into creation.
- **The guided builder isn't surfaced.** The excellent `/agent-build` flow (Claude Code skill at `.claude/commands/agent-build.md`: "know your stack / recommend for me" → problem → workflow → recommended stack → scaffold) exists only in the CLI.
- **Auth is ambiguous** (`dashboard/src/pages/login.tsx`): a login/register toggle with no clear primary action and no local-dev default-login hint.
- **Unfinished features look broken.** Memory & RAG builders are stubs, Fleet shows mock data, etc. — they render empty/fake screens instead of an honest "coming soon".
- **Quickstart CLI is noisy** — prompts for every provider key and runs a confusing Ollama-rebind step that silently fails.

## Goals

- A new user reaches a working, tested agent with the fewest possible decisions.
- One obvious path on every screen; advanced power kept but de-emphasized.
- Honest UI: nothing shown that doesn't work.
- Studio visually consistent with agentbreeder.io.

## Non-goals

- No change to `agent.yaml` schema, the deploy pipeline, or governance.
- No new framework/deployer/runtime.
- Not finishing the stubbed features (Memory/RAG builders, Fleet) — only gating them.

---

## Design

### A. Model & provider simplification

Replace the flat 12-provider list + 3 tabs with one question — **"How do you want to run models?"** — and three paths:

1. **Local (free)** — Ollama, auto-detected, no API key. Default for dev/testing.
2. **Gateway (recommended)** — one key → many models. **OpenRouter** (hosted, pay-per-use) or **LiteLLM** (self-host). Foundation-model keys (OpenAI/Anthropic/Google) flow *through* the gateway, so direct providers are rarely needed.
3. **Direct provider (advanced)** — bring-your-own-key for OpenAI / Anthropic / Google, with the 8 niche OpenAI-compatible providers (Cerebras, Deepinfra, Fireworks, Groq, Hyperbolic, Moonshot, Nvidia, Together) behind a collapsed **"More providers (advanced)"** disclosure.

- Keys continue to live in the workspace secrets backend (never the DB) — unchanged.
- The same simplified picker is reused anywhere a model is chosen (agent wizard, Playground) via a shared `ModelPathPicker` component, instead of re-surfacing the full provider grid.
- Implementation: refactor `models.tsx` so the three paths are the top-level frame; the existing `ProviderCatalog` becomes the body of the "Direct" path's advanced disclosure.

### B. Home — "Get Started" checklist

Home leads with a 4-step checklist that tracks **real** progress (derived from API state, persisted dismissed-state in localStorage); stat cards demote below it.

1. **Connect a model** — satisfied when a local model is detected or any provider/gateway is configured.
2. **Create your first agent** — primary CTA; offers **"✨ Recommend for me"** and **"I know my stack"** (both open the wizard, §C).
3. **Test it in the Playground** — satisfied after first Playground run.
4. **Deploy — or keep local** — satisfied after first deploy (optional; skippable).

Steps show done / active / locked states; the whole panel is dismissible once step 2+ is complete. This replaces (or absorbs) the welcome-tour's job; keep a "Restart guide" affordance.

### C. Guided agent wizard (surface `/agent-build` in-UI)

A **full-page** route `/agents/new` with a left step rail (chosen over a modal — four steps + editable recommendations need room):

1. **Goal** — "What problem does this agent solve?" + business goal.
2. **Workflow** — free-text steps, one per line ("Search KB → look up order → escalate").
3. **Review stack** — Studio recommends framework / model (from the connected path) / memory / RAG / guardrails; **each field editable**.
4. **Name & create** — name, team, owner → scaffolds the agent and routes into the Playground.

**Architecture decision (recommendation engine):** the heuristics currently live only in the `/agent-build` markdown skill, which the API can't call. Extract them into a single testable module — **`engine/recommend.py`** (pure function: inputs → recommended stack) — exposed via a new endpoint **`POST /api/v1/builders/recommend`**. The UI wizard and (ideally) the skill both reference this one source of truth, satisfying the "fix upstream, don't fork logic" principle. The wizard then writes a normal `agent.yaml` through the existing builder path — it does **not** bypass the config parser (per the three-tier rule in CLAUDE.md).

**Entry points converge on one contract.** The form wizard, a conversational builder, a Claude plugin, and the CLI are all just front-ends that must produce a **schema-validated `agent.yaml` (+ prompts/tools/RAG resources) through the existing builder path**. This lets us add richer front-ends without forking logic:

- **Chat-to-build (BYO Claude key)** — a "Chat to build" tab *side-by-side* with the form on `/agents/new`, shown only when the workspace has a Claude key connected. Claude is a natural-language layer that calls the same `engine/recommend.py` heuristics (as a tool) and emits the config; **its output is validated against `engine/schema/agent.schema.json` before anything is saved** — model output is treated as untrusted (it may hallucinate fields or registry refs). Same destination as the form: a previewed `agent.yaml`.
- **Claude plugin** — package the existing `/agent-build` skill as a distributable Claude plugin (it already scaffolds `agent.yaml` + resources). Cheapest reach for the developer persona already in Claude Code; reinforces "fix upstream, don't fork." It generates files on the user's disk (optionally pushing into the registry via the API), so it complements — not replaces — the in-product flow.

These are sequenced after the form wizard (see Phasing) so the chat builder sits on the already-built recommend endpoint rather than being a throwaway prototype.

### D. Auth clarity

`login.tsx` becomes a single, obvious **Sign in** (email + password, one primary button). Register is demoted to a secondary "Create one" link (separate view, not a competing tab).

**Security:** a local-dev default-login hint (`admin@agentbreeder.local`) is shown **only** when the build/runtime is in local/dev mode (gated on an explicit env/config flag such as `AGENTBREEDER_ENV=development`), never in production builds. The hint never includes a real secret value for any non-local deployment.

### E. "Coming soon" pattern

A reusable `<ComingSoon feature issue />` page state (icon, one-line description, "Track progress #N" link) and a `Soon` badge on the sidebar item (greyed, still clickable → lands on the ComingSoon page, not an empty/mock screen).

- **Gate (start):** Memory builder, RAG builder, Fleet (mock data), Models→Local tab (already), "add custom OpenAI-compatible provider" (already).
- **Keep enabled:** Agents, Tools, A2A Agents, MCP Servers, Models (Direct + Gateway), Prompts, Playground, Templates, Marketplace, Traces, Costs, Datasets, Eval Runs, Deploy Wizard, Teams, Approvals, Audit Log, Incidents, Compliance.
- **Verify-before-gate:** during implementation, each candidate page is checked for genuine end-to-end function (not just "renders") before the final keep/gate call. The classification above is the starting point, not the verdict.
- **Verification verdict (2026-05-25):** Memory builder, RAG builder, Incidents, Compliance, Datasets, and Eval Runs are all **real, wired implementations** → NOT gated. Fleet (`agentops`) is mostly real; its cost anomalies + suggestions are seeded **but were never rendered in the frontend** → the honest action is to leave them unsurfaced (do NOT add seeded-data UI just to label it). No Fleet change needed. The Local tab and custom-provider button are already gated via the existing `ComingSoonBadge`/`ComingSoonBanner` (`dashboard/src/components/coming-soon-badge.tsx`). Net: no new full-page gating; the honesty win is the Fleet "sample data" marker.

### F. Quickstart CLI streamlining

In the `agentbreeder quickstart` model step (`cli/commands/`):

- Default to **"local, no key"** when Ollama models are detected; don't prompt for every provider key.
- Offer **one** optional gateway key (OpenRouter) inline; everything else is "press Enter to skip".
- Make the **Ollama rebind** an explicit, clearly-explained optional step. Fix the silent auto-rebind failure (detect failure, print the exact manual commands — already partially done — and continue rather than appearing to hang).

### G. Catalog bug fix

Root-cause the "Failed to load catalog: Failed to fetch" from `api.providers.catalog()` (the `/models` page). Likely an API route/availability/serialization issue in `api/routes/providers.py`. Fix the endpoint; additionally, the simplified `/models` (§A) must degrade gracefully — a path-level inline notice, never a full-page red error blocking the screen.

### H. Branding & color scheme (align to agentbreeder.io)

**Color scheme is already aligned** — `dashboard/src/index.css` already mirrors the website tokens (`#09090b` bg, `#111113` surface, `#1a1a1e` elevated, green-500 `#22c55e` primary, violet `#a78bfa`, `rgba(255,255,255,0.07)` border, `#e4e4e7` text). No palette change.

Two changes:

1. **Display font:** add **Bricolage Grotesque** (the website's `--font-display`, hero/headlines) to Studio for page titles and the wizard/Get-Started hero. Body/UI stays **Geist**, code stays **Geist Mono**. Add the marketing type scale (display/h1/h2 sizes + tight letter-spacing from `website/tailwind.config.ts`) to Studio's theme so headings match.
2. **Dark-only:** drop Studio's light theme to match the website (which removed its theme toggle). Remove the `:root` light token block and any theme-toggle UI; keep `.dark` as the single theme. Reduces QA surface and guarantees brand consistency.

Source of truth for tokens remains `website/app/globals.css`; Studio mirrors it. (Future: consider a shared token package, out of scope here.)

---

## Components / interfaces (touch list)

| Area | Files |
|---|---|
| Model paths | `dashboard/src/pages/models.tsx`, new `ModelPathPicker`, reused in builder + `playground` |
| Home checklist | `dashboard/src/pages/home.tsx`, new `GetStartedChecklist`, retire/repurpose `welcome-tour.tsx` |
| Wizard | new route `dashboard/src/pages/agents/new.tsx`, `engine/recommend.py`, `POST /api/v1/builders/recommend` (`api/routes/builders.py`) |
| Auth | `dashboard/src/pages/login.tsx` |
| Coming soon | new `dashboard/src/components/coming-soon.tsx`, `dashboard/src/components/shell.tsx` (badges) |
| Quickstart CLI | `cli/commands/` (quickstart + scan), Ollama rebind helper |
| Catalog bug | `api/routes/providers.py`, `dashboard` ProviderCatalog error handling |
| Branding | `dashboard/src/index.css` (Bricolage import, type scale, remove light tokens), `dashboard/index.html` |

## Data flow

Wizard: UI form → `POST /api/v1/builders/recommend` (pure heuristics in `engine/recommend.py`) → editable stack → existing builder/save path writes `agent.yaml` → registry. No new write paths to the registry; no deploy-pipeline change.

## Error handling

- `/models` never blocks on a catalog fetch failure — show a contained, dismissible notice per path.
- Recommend endpoint returns a deterministic default stack even on sparse input; the UI always lets the user override.
- Coming-soon pages must not call stub endpoints.

## Testing

- Unit: `engine/recommend.py` (input → stack matrix), `ModelPathPicker` logic, checklist progress derivation.
- Component/E2E (Playwright): first-run journey (Home → wizard → create → Playground), auth sign-in + default-hint gating (dev vs prod), `/models` renders without error when catalog fails, coming-soon pages render for gated features.
- CLI: quickstart "local, no key" default path; rebind-failure path prints manual commands and continues.

## Security considerations

- Default-login hint strictly dev-gated (§D).
- Provider/gateway keys remain in the workspace secrets backend, never the DB (§A) — unchanged invariant.
- **BYO Claude key (chat builder)** stored in the workspace secrets backend, never the DB, never logged; scoped to the chat-build feature. Generated YAML is schema-validated before save; model output is untrusted.
- Gated features expose no stub/mock endpoints (§E).
- Run the `security` review skill on the implementation diff before merge.

## Cross-repo sync (per CLAUDE.md)

- No `agent.yaml` schema change. CLI quickstart changes → update `website/content/docs/quickstart.mdx` in the same PR.
- New `/api/v1/builders/recommend` endpoint → check whether `agentbreeder-cloud` surfaces the builder and needs a companion update.
- Confirm whether the cloud SaaS reuses this dashboard or ships its own before wide UI changes.

## Phasing (decomposition for planning)

This is an epic; suggested independent, shippable phases:

1. **Brand foundation** — Bricolage + type scale + dark-only (low-risk, unblocks visual consistency).
2. **Coming-soon pattern + catalog bug fix** — honesty + the red-error papercut (small, high-trust).
3. **Model path simplification** — `ModelPathPicker` + `/models` reframe.
4. **Onboarding** — Home Get-Started checklist.
5. **Guided wizard (form)** — `engine/recommend.py` + `POST /api/v1/builders/recommend` + `/agents/new` (largest; depends on 3). The deterministic, no-key baseline.
6. **Quickstart CLI streamlining** — independent.
7. **Claude plugin** — package the existing `/agent-build` skill as a distributable plugin (cheap; can run in parallel; reuses the skill).
8. **Chat-to-build tab (BYO Claude key)** — conversational front-end on `/agents/new`, reusing the recommend endpoint from phase 5 and validating output against the agent schema before save. Premium layer; depends on 5.

The expert-review skills the user named map onto implementation: `ui-ux-pro-max` / `frontend-design` for the dashboard work, `architect` for the recommend-engine extraction, `security` for the auth/secrets review.
