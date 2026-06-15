# Wave 4 — Studio Conversational Builder: Design & Architecture

> **Status:** Design (pre-implementation)
> **Date:** 2026-06-14
> **Branch:** `feat/studio-conversational-builder`
> **Closes:** the conversational builder epic (W1–W3 complete and committed)
> **Master spec:** `docs/superpowers/specs/2026-06-14-studio-conversational-builder-design.md`
> **Repos touched:** `agentbreeder` (OSS engine + Studio), `agentbreeder-cloud` (GCP Cloud Run control plane)

This document is the detailed design for W4 and the home for the multi-lens review
(architect / cloud-security / frontend / UX / marketing). It is grounded in the existing
code — every anchor below cites a real file:line.

---

## 1. Scope

W4 has four deliverables:

1. **CloudSandbox** — a managed, tenant-scoped, ephemeral microVM implementing the existing
   `Sandbox` Protocol, flipping `AGENTBREEDER_SANDBOX=cloud` from a `RuntimeError` to working.
2. **Cloud sandbox-minutes metering** — wire real wall-clock minutes into the cloud
   `QuotaCounter` (currently stubbed at 0, `agentbreeder-cloud/api/routes/builder_sessions.py:185`).
3. **Analytics funnel dashboard** — replace the W3 `track()` browser-CustomEvent stub with a
   real collector, the full funnel taxonomy, per-engine eval metrics, and a Studio view.
4. **Codex golden-transcript gated e2e** — mirror the Claude e2e for the `codex` engine.

Non-goals for W4: self-hosted Firecracker backend (designed for, not built), BigQuery funnel
modeling beyond raw event export, tenant-tier sandbox-minute pricing plans.

---

## 2. Existing-code anchors (what we build on)

| Area | File:line | Note |
|---|---|---|
| Sandbox Protocol (6 async methods) | `engine/sandbox/base.py:52-61` | `write/read/list/exec/snapshot/close` |
| `ExecResult` + `.ok` | `engine/sandbox/base.py:24-35` | |
| `select_sandbox_mode()` | `engine/sandbox/base.py:38-49` | reads `AGENTBREEDER_SANDBOX`, fails closed |
| `LocalSandbox` (path containment, caps) | `engine/sandbox/local.py:24-94` | the contract CloudSandbox must match |
| cloud branch raises | `api/services/builder_session_service.py:37-43` | flip point |
| eject flow + SSE contract | `api/services/builder_session_service.py:132-167` | `token/tool_call/file_change/complete/error` |
| coding loop (B2) | `engine/coding_agent/loop.py:79-146` | turns/tokens/wall-clock bounds |
| engines (claude + codex) | `engine/coding_agent/engines.py:50-65` | `codex` already first-class |
| analytics stub | `dashboard/src/lib/analytics.ts` (whole file) | CustomEvent, no collector |
| emit sites | `dashboard/.../ChatBuildPanel.tsx:598,607,610` | engine hardcoded `"claude"` |
| cloud proxy metering | `agentbreeder-cloud/api/routes/builder_sessions.py:94-111,185` | turn unit; minutes TODO |
| QuotaCounter | `agentbreeder-cloud/api/services/quota.py:25-59` | post-increment-by-1, 429 |
| QuotaScopeEnum | `agentbreeder-cloud/api/models/tenancy.py:41` | `{user,tenant}` only today |
| quota limits | `agentbreeder-cloud/api/config.py:19-20` | both default 100/day |
| Claude gated e2e | `tests/e2e/test_builder_eject_e2e.py` | gated on `AGENTBREEDER_E2E_ANTHROPIC_KEY` |

---

## 3. CloudSandbox

### 3.1 Decision

Implement CloudSandbox as a **thin Protocol-conformant client** behind a pluggable
`SandboxBackend` abstraction. **v1 backend = e2b** (managed Firecracker microVMs). Self-hosted
**Firecracker-on-GKE** is the documented enterprise/data-residency v2 (designed for, not built).

Decision matrix (weighted): e2b **8.45** vs Cloud Run/gVisor 6.35 vs Cloud Run Jobs 5.0 vs
self-host Firecracker/GKE 6.55. The `Sandbox` Protocol is *stateful and interactive*
(write→exec→read→snapshot→close over a session), which eliminates batch Cloud Run Jobs;
true microVM isolation for arbitrary BYO-key code rules in e2b/Firecracker over gVisor.

### 3.2 Shape

```
engine/sandbox/
  base.py            # Sandbox Protocol (unchanged)
  local.py           # LocalSandbox (unchanged)
  cloud.py           # NEW: CloudSandbox(Sandbox) — delegates to a SandboxBackend
  backends/
    base.py          # NEW: SandboxBackend interface (provision/exec/read/write/list/snapshot/destroy)
    e2b_backend.py   # NEW: E2BBackend wrapping the e2b SDK
    fake_backend.py  # NEW: in-memory backend for unit tests (no network)
```

- `CloudSandbox` maps the 6 Protocol methods 1:1 onto `SandboxBackend`, applying the SAME
  guardrails LocalSandbox enforces: path containment (no absolute, no `..`), `_MAX_FILE_BYTES`,
  `_MAX_EXEC_TIMEOUT`. The microVM is defense-in-depth, **not** an excuse to drop client checks.
- `_make_sandbox()` (`builder_session_service.py:41-42`) stops raising and returns
  `CloudSandbox(backend=make_backend_from_env())`.

### 3.3 e2b backend contract

| Protocol concern | e2b mapping |
|---|---|
| per-session VM | one e2b sandbox per builder session; tenant id in sandbox metadata/tag |
| secrets | **NONE by default.** The coding-loop LLM calls run in the OSS engine *outside* the VM, so eject needs no key inside it. Untrusted in-VM code could exfiltrate any env var, so we inject nothing long-lived; a future test-invoke step gets only a short-lived, narrowly-scoped token (see cloud-security §11.2 CRITICAL). |
| network | egress **default-deny allowlist**: LLM endpoints + PyPI + npm only. **Drop link-local/metadata ranges (169.254.0.0/16 incl. 169.254.169.254)** — mandatory for the self-hosted GKE backend to prevent SSRF-to-metadata token theft. |
| caps | CPU/mem from e2b template; wall-clock from `AgentBounds.wall_clock_s`; e2b idle-timeout backstop |
| image | non-root `USER`, pinned base, minimal tooling, no build secrets (defense-in-depth inside the µVM) |
| snapshot | `snapshot()` zips the workspace (parity with LocalSandbox); snapshot bytes are tenant data → encrypted at rest, TTL, tenant-scoped |
| teardown | `close()` destroys the VM; e2b idle-timeout is the backstop if the process dies |

The `SandboxBackend` abstraction is a **security requirement** (not just architecture): e2b is a
third-party sub-processor, so data-residency-bound tenants need the self-hosted backend.
`E2B_API_KEY` resolves from GCP Secret Manager in cloud; from user env in self-host (BYO).
e2b ships only as a v1 self-serve option with a signed **DPA** + privacy-policy sub-processor
disclosure + region pinning.

### 3.4 Failure policy (load-bearing)

In cloud mode, if the backend is unavailable, CloudSandbox raises → the eject route surfaces
a **503 with a clear message**. It **never** falls back to `LocalSandbox` — that would breach
the W3 invariant ("In cloud mode, LocalSandbox construction raises — only CloudSandbox is
allowed", `docs/superpowers/plans/...wave3.md:13`). Circuit-break repeated e2b failures.

### 3.5 Duration reporting

`CloudSandbox` tracks VM wall-clock (provision → close) and exposes `sandbox_seconds`. The
eject service includes it in the terminal `complete` SSE frame so the cloud proxy can meter
actuals (see §4). This also folds in the C5 polish item by adding a `code` field to
engine-origin `complete`/`error` frames.

---

## 4. Cloud sandbox-minutes metering

### 4.1 Pattern: pre-check, then charge actuals

Turns use post-increment-by-1 (`quota.py:44-59`). Minutes are variable-per-call and must not
be charged only *after* expensive work. So:

1. **Pre-check** (before forwarding eject): if the user or tenant is already at the daily
   sandbox-minute cap → **429** immediately (reuse the `QuotaExceeded` → 429 shape,
   `builder_sessions.py:94-111`).
2. **Charge actuals** (after the stream): sniff `sandbox_seconds` from the terminal `complete`
   frame, post-increment by `ceil(seconds / 60)` with an **idempotency key** on `session_id`
   to avoid double-charging on retries.
3. **Backstop**: if the client disconnects mid-stream, a best-effort charge keyed on
   backend session-close prevents free unmetered minutes.

### 4.2 Schema / code changes (agentbreeder-cloud)

- `QuotaScopeEnum` (`api/models/tenancy.py:41`): add `user_sandbox_minutes`,
  `tenant_sandbox_minutes`. **Alembic migration** to ALTER the Postgres enum (gated review —
  enum ALTER is awkward to roll back).
- `quota.py`: add `amount: int = 1` to `_increment_one` / `increment_and_check`; add a
  pre-check helper `check_only(scope, scope_id, limit)`.
- `api/config.py`: add `user_daily_sandbox_minutes_limit` / `tenant_daily_sandbox_minutes_limit`
  (proposed default **30 min/day** free tier — confirm).
- `builder_sessions.py:185`: replace the TODO stub with pre-check + post-stream charge.

### 4.3 Why not meter in OSS

Free-tier quotas are a **cloud** concern; OSS self-host is unmetered. OSS only contributes the
`sandbox_seconds` value in the SSE contract. No quota logic enters the OSS engine.

---

## 5. Analytics funnel

### 5.1 Decision

Replace the CustomEvent stub with a real collector: **OSS Postgres `analytics_events` table +
ingest endpoint + funnel aggregation endpoint + a Studio "Builder" view**. Cloud additionally
streams the same events to **BigQuery** (cold tier) for funnel SQL at scale. Events are **not**
written to `audit_log` (keep the immutable compliance log clean).

### 5.2 Event taxonomy (master spec §12)

`builder_session_started → user_message_sent → stack_recommended →
setup_card_shown → setup_card_completed → spec_validated → eject_to_code_started →
coding_agent_turn → deploy_started → deploy_succeeded | deploy_failed → first_invoke`

- **North-star metric:** time-to-first-deployed-agent.
- Expand the typed `AnalyticsEvent` union in `dashboard/src/lib/analytics.ts` to the full set.
- Fix the hardcoded `engine: "claude"` (ChatBuildPanel.tsx:598) → derive from the active engine.
- **PII rule (cloud-security §11.2):** events carry **structural data only** — event name,
  engine, counts, ids. **Never** message bodies or prompt content in `props` jsonb (user
  messages may contain PII). Pin the typed union as the contract; add a **retention/TTL** on
  `analytics_events`; the BigQuery export inherits both.

### 5.3 Per-engine eval metrics (master spec §11)

Computed from `analytics_events` + eval-run records, grouped by engine (`claude`, `codex`):
spec-validity rate, deploy-success rate, turns-to-spec, hallucinated-field rate.

### 5.4 Shape

```
OSS:    track() (batched) ──POST /api/v1/analytics/events──► analytics_events (Postgres)
                                                            └► GET /api/v1/analytics/funnel (aggregates)
Cloud:  same events ──► BigQuery (cold tier) for funnel SQL  [follow-on / via existing pipeline]
Studio: new "Builder" view renders the funnel + per-engine scorecards
```

### 5.5 Naming (CLAUDE.md "Studio rule")

The view is **"Studio › Builder"** (functional noun). **Never** "Analytics Studio" / "Builder
Studio". The funnel chart inside it is "the funnel", lowercase, unbranded.

---

## 6. Codex golden-transcript gated e2e

Mirror `tests/e2e/test_builder_eject_e2e.py` for the `codex` engine:

- Gate on `AGENTBREEDER_E2E_OPENAI_KEY` (skip cleanly when absent — same pattern as Claude).
- Build `OpenAIProvider`, run `engine_for("codex", provider=...)`.
- Elevate to a **golden transcript** (master spec §11): scripted conversation asserting
  build → valid `agent.yaml` → local deploy → `/health` 200 → invoke.
- Apply the same golden transcript to the Claude path for parity.

---

## 7. Cross-repo & polish items folded in

- **C5 error frames:** add a `code` field to engine-origin `complete`/`error` SSE frames (needed
  by §4 metering anyway).
- **Eject engine prop:** derive engine in analytics instead of hardcoding `"claude"` (§5.2).
- **B2 loop:** name the bare `20_000` truncation constant; note `tokens_seen` counts chars not
  tokens (out of strict W4 scope; fix if cheap).
- **Cloud doc drift:** `agentbreeder-cloud/ARCHITECTURE.md` says "ECS Fargate" but
  `infra/stacks/compute.py` provisions Cloud Run — fix the doc (cross-repo sync rule).
- **hono dev-only CVE:** separate `npm audit` / security PR (shadcn CLI, not bundled).

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| Untrusted code escapes sandbox | microVM isolation + client-side path/size/timeout caps + egress allowlist |
| Secrets leak via e2b / on disk | env-only injection, never disk; Secret Manager; trust-boundary review (§ cloud-security) |
| e2b outage | circuit-break → 503; never fall back to LocalSandbox in cloud |
| Sandbox-minute cost abuse | pre-check quota + charge actuals + idempotency + backstop charge on close |
| Postgres enum ALTER hard to revert | gated migration review; additive-only |
| Funnel jsonb sprawl | typed `AnalyticsEvent` union as the contract |

---

## 9. Implementation phasing

1. **Immediate (days):** `SandboxBackend` + `CloudSandbox(e2b)` + flip `_make_sandbox`;
   `FakeBackend` unit tests; `sandbox_seconds` in `complete` frame + `code` field.
2. **Short-term (weeks):** cloud metering (enum + migration + amount + pre-check + sniff);
   analytics table + ingest + funnel endpoint + Studio Builder view; Codex golden e2e.
3. **Long-term (months):** self-hosted Firecracker-on-GKE backend; BigQuery funnel; per-engine
   eval scorecards; tenant-tier sandbox-minute plans.

---

## 10. Open questions (for review lenses)

1. Is e2b acceptable as a v1 data/secret processor, or must v1 be self-hosted? → **cloud-security**
2. Free-tier sandbox-minute default (30/day?) and overage behavior (hard 429 vs soft)? → architect/marketing
3. Standalone Studio "Builder" view vs a tab in an existing view? → **ui-ux-pro-max / frontend**
4. Cloud `ARCHITECTURE.md` ECS-vs-Cloud-Run fix — same PR or follow-on? → cross-repo

---

## 11. Multi-lens review

### 11.1 Architect — DONE
See §3–§5 verdicts and the §3.1 decision matrix. Summary: e2b-backed CloudSandbox behind a
`SandboxBackend` abstraction; pre-check-then-charge-actuals metering; dedicated analytics
events table + Studio Builder view. North-star = time-to-first-deployed-agent.

### 11.2 Cloud-security — DONE

Design-time threat model (the scan skill targets deployed infra; applied here as a pre-impl
review in its CRITICAL/HIGH/MEDIUM/LOW + Gate-5 vocabulary). **The microVM is the easy part;
the trust boundary inside it is where W4 can go wrong.**

**[CRITICAL] Secrets injected into the sandbox are readable by the untrusted code.**
§3.3 originally said "secrets injected as env vars." But the coding agent executes
*untrusted, model-generated commands* inside that same VM — any env var is exfiltratable to
the allowlisted egress. **Revised stance: inject ZERO long-lived secrets into the sandbox.**
The coding-loop LLM calls happen in the OSS engine process *outside* the VM, so the eject
sandbox needs no model key at all. If a future "test-invoke the generated agent" step needs a
credential, inject only a **short-lived, narrowly-scoped** token and treat the VM as hostile.
→ Design updated (§3.3).

**[CRITICAL] Block cloud metadata endpoint (169.254.169.254) from sandbox egress.** For the
self-hosted Firecracker-on-GKE v2, an un-blocked metadata server lets sandbox code steal the
node/SA token → full tenant-data compromise (SSRF-to-metadata). The egress allowlist must be
**default-deny** and explicitly drop link-local/metadata ranges. e2b (off-GCP) is lower risk
but the allowlist requirement stands. → Added to §3.3 / §8.

**[HIGH] e2b is a third-party sub-processor.** User prompts + generated code (and any token we
pass) transit e2b infrastructure. Required before GA: signed **DPA with e2b**, e2b added to the
**privacy-policy sub-processor list**, documented **data residency** (e2b region pinning), and
the self-hosted Firecracker path positioned as the answer for regulated/enterprise tenants.
This is the answer to Open Question #1: **e2b is acceptable for v1 self-serve with disclosure +
DPA; it is NOT acceptable as the only option for data-residency-bound tenants** — hence the
SandboxBackend abstraction is a security requirement, not just architecture.

**[HIGH] Egress allowlist must be explicit and minimal.** Default-deny; allow only configured
LLM endpoints + PyPI + npm registry. Prevents data exfiltration and crypto-mining-to-pool.

**[HIGH] Sandbox runs non-root, pinned base, minimal tooling.** Defense-in-depth even inside a
microVM — the e2b template image gets a non-root `USER`, pinned base, no build secrets.

**[MEDIUM] Analytics events must be PII-free.** User messages may contain PII; funnel events
must carry **structural data only** (event name, engine, counts, ids) — never message bodies or
prompt content in `props` jsonb. Pin the typed union, add a **retention/TTL** on
`analytics_events`, and inherit both in the BigQuery export. → Added to §5.

**[MEDIUM] Metering integrity.** `sandbox_seconds` must be read by the cloud proxy from the
*OSS stream* (trusted, bearer-authed), never settable by the client. Idempotency key on the
metering write prevents double-charge on retry; the close-time backstop prevents under-charge
on mid-stream disconnect (DoS-by-disconnect). Pre-check quota caps resource-exhaustion abuse.

**[LOW] Snapshot bytes** may contain whatever the agent wrote — treat snapshot storage as
tenant data (encrypted at rest, TTL, tenant-scoped access).

**Gate 5 (design-time): CONDITIONAL PASS** — the two CRITICALs (no-secrets-in-sandbox,
metadata-egress-block) and the e2b DPA/sub-processor HIGH **must be reflected in the design and
landed in implementation** before the cloud path ships. With §3.3/§5/§8 updated below, the
design clears the gate; implementation re-runs the live scan (Gate 5) before the cloud deploy.

### 11.3 Frontend design — DONE

**Aesthetic direction: "instrument panel, not dashboard slop."** Editorial, data-dense,
restrained — intentionality within the existing Studio design system, *not* a bolt-on with a
foreign look. The one memorable thing: a **left-to-right funnel ribbon** where each step's bar
width encodes survivors and the **gap between bars is labeled with the drop-off %** — the eye
reads conversion loss as literal negative space.

**House-style constraint (load-bearing):** Studio ships **no charting library**. Every chart is
Tailwind: `costs.tsx:140-224` renders bars via `style={{ width: \`${pct}%\` }}` with
emerald/amber/red thresholds; `eval-comparison.tsx:41-117` already has `getScoreColor` /
`getBarColor` / delta-badge / metric-card. **W4 adds zero new deps** (recharts/tremor/d3) —
this keeps the bundle lean *and* clears a security concern (no new npm attack surface, ref §11.2).

**Route/file:** `dashboard/src/pages/builder-insights.tsx`, nav label **"Builder"** (Studio rule —
never "Analytics Studio"/"Builder Studio"). Sits in the existing sidebar next to Evals/Costs.

**Layout (top → bottom):**
1. **North-star band** — one wide `Card`, oversized tabular-nums figure for
   *time-to-first-deployed-agent* (p50 big, p90 secondary), a sparkline-as-CSS-bars trend strip,
   and a period selector (`Tabs`: 7d / 30d / all). This is the hero; give it air.
2. **Funnel ribbon** — full-width `Card`. Vertical stack of 11 steps
   (`builder_session_started … first_invoke`); each row = step label + count + a bar
   (`width: survivors/total`) reusing the costs.tsx bar. Between rows, a muted
   `text-xs text-muted-foreground` drop-off delta (`−37%`) using the eval-comparison delta-badge
   colors. Biggest drop-off row gets a subtle left accent border to draw the eye to the leak.
3. **Per-engine scorecards** — 2-col grid (`grid-cols-1 lg:grid-cols-2`), one `Card` per engine
   (claude / codex), each with 4 metric rows (spec-validity, deploy-success, turns-to-spec,
   hallucinated-field) rendered with `eval-comparison`'s metric-card + threshold bars. A compact
   header `Badge` shows the engine + sample size. Reuse `getScoreColor`/`getBarColor` verbatim.

**Components:** shadcn `Card`, `Tabs`, `Badge` (all already in `src/components/ui`). Data via
React Query (`useQuery(['builder-funnel', period])` → `GET /api/v1/analytics/funnel`). Honor the
CLAUDE.md rule: handle `isLoading` (`<Skeleton />` rows) and `error` (`<ErrorBanner />`); empty
state ("no builder sessions yet") for fresh installs. Tabular figures use `tabular-nums`; respect
dark mode via the existing `dark:` thresholds. Typography/colors inherit the Studio theme — no
new font (consistency beats novelty for an internal instrument).

### 11.4 UI/UX (ui-ux-pro-max) — DONE

Validated against the skill's chart DB (Funnel/Flow = AA; Compare-Categories bar = AAA) and the
§1/§10 a11y rules. Two refinements to the frontend design + an a11y contract:

**[REFINEMENT 1 — funnel length] 11 raw steps exceeds the "3–8 stages optimal" rule** ("beyond
8, group minor steps"). Fix: the **headline funnel shows ~5 macro-stages** —
*Converse → Spec validated → Eject → Deploy → Live (first invoke)* — with the 11 raw events
available on **expand/drill** per stage. Keeps the hero readable; preserves granularity for
debugging. Single-hue gradient start→end; **highlight the biggest drop-off** (already in §11.3).

**[REFINEMENT 2 — scorecard chart type] Do NOT use radar/spider for engine comparison** (DB
grade B: "values need precise comparison → use grouped bar"). The `eval-comparison.tsx`
metric-card + threshold-bar idiom (Compare-Categories, **AAA**) is the correct choice — confirms
§11.3. Two side-by-side engine cards with aligned metric rows read more precisely than a radar.

**[A11y contract for div-based bars] (CRITICAL §1, §10):**
- **`color-not-only`:** drop-off and pass/fail must carry **text + icon**, never color alone
  (the emerald/amber/red bars need a numeric label and an ▲/▼ glyph).
- **`screen-reader-summary`:** each chart container gets an `aria-label` stating the key insight
  (e.g. "Builder funnel: 1,204 sessions started, 38% reach first deploy; biggest drop at Eject").
- **`data-table` fallback:** render the funnel as a semantic list/table (`role="list"` rows with
  stage name + count + drop-off %) — value labels **always visible**, not hover-only (hover
  tooltips fail keyboard + touch; `tooltip-keyboard`).
- **`contrast-data`:** bars vs background ≥3:1, text labels ≥4.5:1; verify the amber threshold in
  **both** light and dark (`color-dark-mode`).
- **`number-tabular` + locale formatting** on all figures; **`reduced-motion`** disables bar-fill
  animation (data must be readable immediately, `animation-optional`).
- **`empty-data-state`** ("No builder sessions yet" + a link to start one) and **skeleton loading**
  (`loading-chart`) — never a bare axis frame. (Echoes §11.3.)
- Custom SVG/div is an **accepted funnel implementation** per the DB ("Custom SVG") — the no-new-
  dependency decision (§11.3) stands and is a11y-viable.

**[CSV export]** Data-dense analytics views should offer **CSV export** of the funnel + scorecard
data (`export-option`) — cheap to add, expected by PM/ops users.

### 11.5 Marketing — DONE

W4 is the piece that makes the epic *demoable to a stranger with zero setup* — that's the
marketing unlock. CloudSandbox turns "clone the repo and run the CLI" into "open a tab and talk."

**Positioning.** Lead with the outcome, not the microVM. The line:
> **"Describe your agent. Watch it get built. Ship it governed — without leaving the browser."**
Tie to the master tagline: CloudSandbox is how *"Define Once. Deploy Anywhere"* becomes true for
people who don't have a terminal. **Avoid** "Lovable for agents" in public copy as the *headline*
(borrowed equity, and it undersells governance) — use it only as a one-line analogy for warm
audiences. The real differentiator vs. Lovable/Replit/v0: **the eject is real code AND the deploy
is governed** (RBAC, cost, audit as a side effect). Nobody else pairs no-code speed with
enterprise governance. That's the wedge.

**Audience framing (one product, three hooks):**
- **PMs / citizen builders:** "Build a working agent in a conversation. No setup, no YAML."
- **ML engineers:** "Prototype by chatting, then `eject` to real LangGraph/CrewAI/Claude SDK code
  you own — no lock-in."
- **Developers/platform teams:** "Every agent your org ships is governed automatically. The
  builder is the on-ramp; the registry is the moat."

**Most relevant plays from the library (stage = console.agentbreeder.io launch):**
1. **Product Hunt launch (#78) + waitlist referrals (#79).** The browser demo IS the launch
   asset — a 30-sec "prompt → live agent" screen recording is the hero. Ties to the existing
   Vercel-KV waitlist (`project_website_waitlist_kv`). *Start:* cut the demo video; gate cloud
   builder access behind the waitlist; referral bumps queue position. *Outcome:* launch-day
   signups + a ranked lead list.
2. **Product-led viral loop / Powered-by (#93, #87).** Agents built in the cloud builder carry an
   optional "Built with AgentBreeder" attribution on their public agent card / A2A endpoint.
   *Outcome:* every shared agent is a distribution surface.
3. **Engineering-as-marketing / free tool (#15, #14).** The conversational builder itself is the
   free tool — a public, no-login "spec your agent" mode that produces a downloadable `agent.yaml`
   (the eject-to-deploy step requires signup). *Outcome:* top-of-funnel that maps directly to the
   §5 funnel's `builder_session_started`.
4. **DevRel + the eject story (#133–136).** A "from chat to production code in 4 minutes" tutorial
   + livestream; the *no-lock-in eject* is the credibility hook for skeptical engineers.
5. **Comparison pages (#11).** "AgentBreeder vs. [no-code agent builders]" — axis = *does the
   output deploy with governance / can you own the code*. Most competitors lose both columns.

**The funnel view is also a GTM asset, not just an internal tool.** North-star
(time-to-first-deployed-agent) is the exact number to publish as a proof point ("median user ships
a governed agent in N minutes") and to A/B-test launch copy against. The §5 funnel events are the
measurement backbone for every play above — instrument before launch, not after.

**Sequencing:** CloudSandbox must be load-tested and the sandbox-minute free tier tuned (Open
Q #2) *before* a Product Hunt spike — a launch that 503s on the demo is the one risk that turns
the marketing unlock into a liability. Gate the launch on cloud-path reliability.
