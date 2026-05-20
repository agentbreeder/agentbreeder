# Deployment Wizard — Design Spec

**Issues:** closes #389 (Dashboard wizard), absorbs #387 (SSE progress stream + CLI `--provision` flag)
**Branch:** `feat/389-deployment-wizard`
**Date:** 2026-05-19

## 1. Context

Agentbreeder shipped greenfield provisioners for GCP (#437, #440 VPC, #443 Cloud SQL), AWS (#441), and Azure (#442). The orchestrator can already drive a deploy end-to-end. What's missing is a UI to drive it from the dashboard — operators today must use the CLI or call the API by hand. Issue #389 specifies a 5-step wizard; #387 specifies the SSE backend it needs. This spec rolls both into one PR because the wizard's Step 5 is unusable without the stream.

## 2. Goals

- Operators can deploy any registered agent to AWS / GCP / Azure from the dashboard in ≤ 5 clicks once they reach Step 1.
- Two infra modes: **BYO** (validate existing) and **Provision for me** (greenfield). Both reach the same Step 5 progress stream.
- Resumable: partial wizards survive refresh / accidental nav via localStorage.
- Approval-aware: agents with `access.require_approval: true` are routed through the existing /approvals queue without bypass.
- Real progress, not polling: backend streams phase + log events via SSE; frontend renders them live.

## 3. Non-goals

- **No live cloud-pricing calls.** Cost estimates are a static client-side table (see §6 cost source decision).
- **No multi-region multi-deploy.** One agent → one cloud → one region per wizard run.
- **No CLI streaming UI in this PR.** The SSE endpoint also unblocks `agentbreeder deploy --provision` to stream in the CLI, but the CLI consumer is tracked separately and not part of this scope.
- **No marketplace/template flow.** Users start from a registered agent.
- **No A/B rollout / canary controls.** Plain rollout, full traffic.

## 4. Locked decisions (from brainstorming, 2026-05-19)

| # | Decision | Rationale |
|---|---|---|
| 1 | Scope: #389 + #387 land together | Step 5 cannot ship as a real feature without the stream; mocking Step 5 would create a half-built wizard. |
| 2 | Form factor: full-page route at `/deploy-wizard` | Step 5 needs vertical space; deep-linking lets ops share `?step=5&jobId=…`; modal/drawer get cramped. |
| 3 | Cost source: static client-side TS table | API-free, zero new endpoint surface, accepts ±10% drift. Documented in module header. |
| 4 | Persistence: localStorage draft + resume prompt | Per-browser, no server change, cleared on success or RESET. |
| 5 | Entry points (4): sidebar, agent-detail, /deploys, agent-builder | All pass `?agentId=&from=<origin>` and skip to Step 2 when an agent is preselected. |
| 6 | Approval flow: detect → reroute to /approvals queue | Honours existing approvals UI; Step 5 polls for approval, then switches to SSE. |
| 7 | Implementation approach: single-component wizard with query-param step routing + `useReducer` | One file owns state, easy to test reducer purely, browser back/forward via URL Just Works. |

## 5. Architecture

```
FRONTEND
├── dashboard/src/pages/deploy-wizard.tsx          ~280 LOC (route component)
│   ├── useReducer<DeployWizardState, Action>
│   ├── useSearchParams() for step + prefill
│   ├── useEffect → localStorage("deploy-wizard-draft", state)
│   └── Renders <StepIndicator/>, <StepN/>, <NavButtons/>
├── dashboard/src/components/deploy-wizard/
│   ├── StepIndicator.tsx
│   ├── Step1Agent.tsx          uses existing <RegistryPicker/>
│   ├── Step2Target.tsx         cloud cards + region select + cost preview
│   ├── Step3Infra.tsx          mode picker + BYO form OR provision tree
│   ├── Step4Config.tsx         env vars, secrets, scaling, db tier
│   ├── Step5Deploy.tsx         SSE log viewer + phase indicators
│   ├── InfraValidatePanel.tsx  shared by Step 3 BYO
│   └── ResourcePreviewTree.tsx shared by Step 3 greenfield
├── dashboard/src/lib/
│   ├── deploy-wizard-state.ts  reducer + Action types + canAdvance + initial
│   ├── deploy-wizard-cost.ts   static cost table (cloud × region × tier)
│   ├── deploy-events.gen.ts    generated TS types for SSE events
│   └── api.ts (additions)      deployments.{cloudRequirements,
│                                validateInfra, createJob, getJob, stream}
└── dashboard/src/hooks/
    └── useDeployStream.ts      EventSource wrapper

ENTRY POINTS
├── App.tsx                      <Route path="/deploy-wizard">
├── components/shell.tsx         sidebar "Deploy" link
├── pages/agent-detail.tsx       "Deploy" button → ?agentId=…&from=detail
├── pages/deploys.tsx            "+ New deploy" button
└── pages/agent-builder.tsx      "Deploy now" CTA after save

BACKEND (absorbs #387)
├── api/routes/deployments.py
│   ├── GET  /api/v1/deployments/cloud-requirements/{cloud}  (exists)
│   ├── POST /api/v1/deployments/validate-infra              (exists)
│   ├── POST /api/v1/deployments/                            NEW: create job
│   ├── GET  /api/v1/deployments/{job_id}                    NEW: status poll
│   ├── GET  /api/v1/deployments/{job_id}/stream             NEW: SSE
│   └── POST /api/v1/deployments/{job_id}/destroy-partial    NEW: rollback CTA
├── api/services/deploy_orchestrator.py                       per-job event queue
├── api/models/deploy_events.py                               Pydantic DeployEvent
└── scripts/gen_deploy_event_types.py                         Pydantic → TS codegen

Sizing: frontend ~1100 LOC (10 files), backend ~300 LOC.
```

**Invariants**

- The reducer is pure. The only side effects are: (1) localStorage sync, (2) SSE hook, (3) API mutations.
- `canAdvance(state, n): boolean` is a pure selector. The Next button is disabled unless it returns true. Forwards-jump in `StepIndicator` is gated the same way; backwards-jump is always allowed.
- Programmatic step jumps (URL manipulation) are clamped to the highest valid step. `?step=5` with empty state lands on Step 1.

## 6. Components & data shapes

### 6.1 State + actions (`dashboard/src/lib/deploy-wizard-state.ts`)

```ts
type DeployWizardState = {
  step: 1 | 2 | 3 | 4 | 5;
  // Step 1
  agentId: string | null;
  agentSnapshot: {
    name: string; framework: string; version: string; team: string;
    requiresApproval: boolean; declaresMemory: boolean;
  } | null;
  // Step 2
  cloud: "aws" | "gcp" | "azure" | null;
  region: string | null;
  // Step 3
  infraMode: "byo" | "provision" | null;
  byoFields: Record<string, string>;
  validateResult: ValidationResult | null;
  provisionAck: boolean;
  // Step 4
  envVars: { key: string; value: string }[];
  secrets: string[];
  scaling: { min: number; max: number; cpuTargetPct: number };
  dbTier: string | null;
  // Step 5
  jobId: string | null;
  jobStatus: DeployJobStatus | null;
  endpointUrl: string | null;
  approvalPending: boolean;
  // Meta
  origin: "sidebar" | "agent-detail" | "deploys" | "builder";
  draftSavedAt: number | null;
};

type Action =
  | { type: "HYDRATE_FROM_DRAFT"; state: Partial<DeployWizardState> }
  | { type: "PREFILL_FROM_QUERY"; agentId?: string; from?: Origin }
  | { type: "GOTO"; step: 1 | 2 | 3 | 4 | 5 }
  | { type: "SET_AGENT"; agent: AgentSnapshot }
  | { type: "SET_CLOUD_REGION"; cloud: Cloud; region: string }
  | { type: "SET_INFRA_MODE"; mode: "byo" | "provision" }
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
```

### 6.2 SSE event union — source of truth in Python (`api/models/deploy_events.py`)

```python
class DeployEvent(BaseModel):
    type: Literal["phase", "log", "complete", "error"]
    job_id: str
    timestamp: datetime
    phase: Literal["provisioning", "building", "pushing",
                   "deploying", "health_check", "registering"] | None = None
    step: int | None = None        # 1-based step within phase
    total: int | None = None       # phase total steps
    message: str | None = None
    level: Literal["info", "warn", "error"] | None = None  # for type=log
    endpoint_url: str | None = None  # only on type=complete
    error_code: str | None = None    # only on type=error
```

`scripts/gen_deploy_event_types.py` produces `dashboard/src/lib/deploy-events.gen.ts`. Run via `make gen-types` (also wired into pre-commit).

### 6.3 Static cost table (`dashboard/src/lib/deploy-wizard-cost.ts`)

```ts
export const COST_TABLE = {
  aws: { "us-east-1": { fargateBase: 18, natGw: 32, rdsMicro: 13, alb: 18 }, /* … */ },
  gcp: { "us-central1": { cloudRunBase: 0, vpcConnector: 9, cloudSqlMicro: 9 }, /* … */ },
  azure: { "eastus": { acaBase: 0, postgresB1ms: 13 }, /* … */ },
} as const;

export function estimateMonthly(
  cloud: Cloud, region: string,
  opts: { hasMemory: boolean; isPublic: boolean; tier?: string },
): { low: number; high: number; lines: { resource: string; usd: number }[] } { ... }
```

Header comment notes: numbers are ±10%, updated via PR when cloud pricing changes materially.

### 6.4 Component contracts

| Component | Inputs | Dispatches |
|---|---|---|
| `StepIndicator` | `state.step`, `canAdvance` | `GOTO(n)` |
| `Step1Agent` | `agentsQuery` | `SET_AGENT` |
| `Step2Target` | `cloud`, `region`, `agentSnapshot` | `SET_CLOUD_REGION` |
| `Step3Infra` | full state | `SET_INFRA_MODE`, `SET_BYO_FIELD`, `SET_VALIDATION`, `ACK_PROVISION` |
| `Step4Config` | full state | env / secrets / scaling / dbTier actions + owns Deploy mutation |
| `Step5Deploy` | `jobId`, `useDeployStream(jobId)` | `SSE_EVENT` |
| `InfraValidatePanel` | `cloud`, `byoFields`, validate mutation | (renders only) |
| `ResourcePreviewTree` | `cloud`, `agentSnapshot`, `region` | (renders only) |

### 6.5 Critical decisions baked in

- **Agent snapshot, not live join.** Step 1 stores a snapshot of agent fields (`requiresApproval`, `declaresMemory`, `team`). Edits to the agent mid-flow do not silently change wizard behaviour. Re-fetch on `RESET`.
- **`canAdvance(state, n)` is pure.** No side effects, no API.
- **Reducer is the only writer to localStorage.** Single `useEffect(state)`. Prevents drift.

## 7. Data flow

### 7.1 Mount

```
/deploy-wizard?step=2&agentId=xxx&from=builder
        │
        ▼
PREFILL_FROM_QUERY → read localStorage
        │
        ├─ exists, different agentId  → "Resume previous?" → HYDRATE_FROM_DRAFT (or clear)
        └─ matches                    → silent HYDRATE_FROM_DRAFT
        │
        ▼
If state.agentId set, fetch agent → SET_AGENT (snapshot)
```

### 7.2 Step 2 → 3

Cloud card click stages cloud. Region select fires `GET /cloud-requirements/{cloud}?mode=simple` (TanStack Query, cached per cloud). Cost preview is client-side from `COST_TABLE`. `SET_CLOUD_REGION` enables Next.

### 7.3 Step 3 branches

- **BYO:** user fills form from `cloud-requirements`. "Validate" → `POST /validate-infra`. On success → `SET_VALIDATION`. Next gated on `validateResult.valid === true`.
- **Greenfield:** `<ResourcePreviewTree>` shows what will be created + cost lines. User checks ack → `ACK_PROVISION`. Next enabled.

### 7.4 Step 4 → 5 (submit)

```
POST /api/v1/deployments/ body={agentId, cloud, region, infraMode,
                                byoFields?, envVars, secrets, scaling, dbTier}
        │
        ├─ 202 → {job_id, pending_approval: false}
        │           → SUBMIT_DEPLOY → GOTO 5 → open EventSource(stream)
        │
        ├─ 202 → {job_id, pending_approval: true}
        │           → SUBMIT_DEPLOY → GOTO 5 → poll GET /deployments/{job_id} every 4s
        │              → when status leaves pending_approval, switch to SSE
        │
        ├─ 403 → approval race: banner, stay on Step 4
        └─ 4xx → inline error
```

**Idempotency key:** the `POST /deployments/` request includes header `Idempotency-Key: <uuid generated once per draft>` (stored alongside state in localStorage). The server stores `(team_id, idempotency_key) → job_id` for 24 h. A user who hits Deploy twice in 2 s — or has flaky network — gets the same `job_id` back, not a duplicate deploy. `RESET` rotates the key. Conforms to the pattern already used by `POST /agents/` (which key-aliases via `x-idempotency-key`).

**API field naming:** the wire format is snake_case (`pending_approval`); the TS state mirror is camelCase (`approvalPending`). The generated types from §6.2 codegen keep these in sync — no hand-maintained mapping table.

### 7.5 Step 5 SSE lifecycle

```
EventSource opens
  ├─ "phase"    → reducer marks phase active
  ├─ "log"      → appended to log viewer (auto-scroll-stuck)
  ├─ "complete" → endpointUrl set; localStorage cleared; SSE closed
  └─ "error"    → red banner; SSE closed; Retry creates a NEW job_id (never replays the old one)

On unmount: EventSource closed in cleanup; server keeps last 200 events per job
            in a ring buffer (30-min TTL) so reopen replays them.
```

### 7.6 localStorage write trigger

```ts
useEffect(() => {
  if (state.step === 5 && state.jobStatus === "completed") return;  // don't save terminal
  localStorage.setItem("deploy-wizard-draft", JSON.stringify({
    ...state,
    secrets: [],          // refs only; belt-and-suspenders
    validateResult: null, // always re-validate on resume
    jobStatus: null,
  }));
}, [state]);  // debounced 250ms
```

### 7.7 Races handled

| Race | Handling |
|---|---|
| Agent edited mid-flow | Snapshot in state — no silent change |
| Two tabs open | localStorage key has a per-tab uuid; only the owning tab resumes |
| SSE drops | 3-retry backoff, then polling fallback (transparent) |
| Approval granted while user is away | Re-poll on focus, then re-open SSE |
| `cloud-requirements` shape changed on backend redeploy | TanStack stale-while-revalidate; validation fails loudly if a removed field is required |

## 8. Error handling

See §7.5 for Step 5 errors. The full per-step matrix and "Retry creates a new job" rule live in §4 of the brainstorming notes; key invariants reiterated here:

- **Every error has a user-facing recovery action.** No dead-end states.
- **Retry on Step 5 never reuses the old job_id.** Failed jobs stay in `/deploys` history for audit; Retry dispatches `RESET` then re-triggers Step 4 with the preserved draft.
- **Programmatic step jumps are clamped** to the highest valid step in `canAdvance` terms.
- **Schema drift between session and resume:** wizard re-fetches `cloud-requirements`, shows a single banner "Form was updated since you started — please re-check fields." No migration code.
- **Cost-table drift is silent and acceptable.** Documented in module header.
- **Mid-deploy permission revocation:** existing SSE keeps streaming (job has its own scoped permissions); subsequent retries fail at Step 4.

Per-step failures and their UX:

| Step | Failure | UX | Recovery |
|---|---|---|---|
| 1 | Agent list 5xx | Inline error in RegistryPicker | Retry |
| 1 | Agent ID in URL doesn't exist | Toast, strip param | Fall back to picker |
| 1 | User lacks deployer role on agent's team | Disabled Next + reason | Pick another agent |
| 2 | Cloud not in enabled targets | Card disabled with reason + Settings link | Configure creds |
| 2 | Region list 4xx | Banner + retry | Retry |
| 3 BYO | validate-infra valid: false | Per-resource red ✗ rows | Fix field, re-validate |
| 3 BYO | validate-infra 429 | Toast + countdown | Auto-re-enable |
| 3 GF | provisionAck unchecked | Next disabled + helper | Check the box |
| 4 | Required env var missing | Field-level red border | Fix and re-Deploy |
| 4 | Secret ref deleted | Inline "Pick another" | Re-select |
| 4 | POST /deployments 5xx | Banner above Deploy; body preserved (idempotency key) | Retry |
| 4 | POST /deployments 403 (approval race) | Auto-relabel button → "Submit for approval" + re-submit | One click |
| 5 | SSE drops, retries exhausted | Connection banner; auto-polling fallback | Transparent |
| 5 | error event during provision | Red banner + engine message + "Roll back" CTA → `POST /deployments/{job_id}/destroy-partial`, or "Keep partial state" | One CTA |
| 5 | error during build/push | Banner + "View build log" link | New job |
| 5 | No events 10 min | "Stalled" at 5m, "Timed out" at 10m | Same as error |
| Any | Network offline | Toast; draft saved; Next disabled | Auto-recover |
| Any | API 401 | Redirect /login?next=…; draft loads on return | Resume prompt |

## 9. Testing strategy

Test pyramid:

| Layer | Count | Location |
|---|---|---|
| Frontend unit | 28 | `dashboard/src/__tests__/` |
| Component (RTL) | 18 | colocated per step |
| Backend unit | 14 | `tests/unit/` |
| Backend integration | 8 | `tests/integration/` |
| E2E (Playwright) | 6 | `tests/e2e/` |

### 9.1 Frontend unit (28)

- `deploy-wizard-state.test.ts` (~18): every Action, `canAdvance` exhaustive, URL prefill, localStorage hydration, RESET, snapshot decoupling
- `deploy-wizard-cost.test.ts` (~6): deterministic ranges, public-visibility line items, unknown region returns `Unsupported`
- `useDeployStream.test.ts` (~4): EventSource mock, phase events dispatched, 3-retry backoff, cleanup

### 9.2 Component (18) — RTL + msw

One file per step + the two shared panels. Step 3 gets the most coverage (BYO vs greenfield, validate failure, ack). Step 5 tested with stubbed SSE stream.

### 9.3 Backend unit (14)

- `test_deploy_event_model.py` (5): discriminator works per type, rejects malformed, JSON round-trip
- `test_deploy_orchestrator_events.py` (5): one phase event per boundary, log events forward, complete/error are terminal
- `test_event_queue_ring.py` (4): per-job 200-event ring, 30-min TTL, replay on second subscribe, drop oldest when full

### 9.4 Backend integration (8) — httpx ASGI transport

- `test_deployments_sse_stream.py` (6): subscribe, drive orchestrator with fake provisioner/deployer, assert wire events terminate with `\n\n`; second subscriber replays then live; 401/403; rate-limit exemption for SSE
- `test_deployments_create_job.py` (2): 202 + {job_id, pending_approval}; approval-required path

### 9.5 E2E (6) — Playwright

All E2E specs use a mocked orchestrator (no real cloud creds in CI).

1. `deploy-wizard-happy-gcp-greenfield.spec.ts` — sidebar nav, GCP, greenfield, agent with memory; Step 5 reaches complete
2. `deploy-wizard-happy-aws-byo.spec.ts` — from agent-detail, AWS BYO, validate-infra succeeds, deploys
3. `deploy-wizard-azure-validation-fails.spec.ts` — BYO Azure, validate-infra returns valid: false; per-resource red rows, Next disabled
4. `deploy-wizard-approval-required.spec.ts` — agent with `requiresApproval: true`; Step 5 polls; admin approves in second context; SSE takes over
5. `deploy-wizard-resume-draft.spec.ts` — Steps 1–3, reload, accept resume, complete
6. `deploy-wizard-stalled-deploy.spec.ts` — clock advance to 10m; Timed out banner; Retry creates new job_id

### 9.6 Out of scope

- Real cloud calls (covered by provisioner unit tests in #440–#443)
- Visual regression (shadcn primitives have their own baselines)
- Cross-browser (Chromium gate only)

### 9.7 CI placement

- Frontend unit + component → `dashboard-test` job, **blocks merge**
- Backend unit + integration → `pytest` job, **blocks merge**
- E2E → nightly `dashboard-e2e` job, **alert-only** (auto-issues on failure)

## 10. Open questions

None at spec time. Anything that needs decisions during implementation should be raised as a comment on the PR, not silently chosen by the implementer.

## 11. Cross-repo + docs sync

Per CLAUDE.md rules:

- `website/content/docs/deployment.mdx` — add "Deploying from the dashboard" section with screenshots (taken from Playwright artifacts)
- `website/content/docs/cli-reference.mdx` — note that `agentbreeder deploy` also streams progress to `/deployments/{job_id}/stream` (groundwork for the future CLI consumer)
- `agentbreeder-cloud` repo — no change needed (Cloud uses the same agentbreeder packages; new endpoints are additive)
- `CHANGELOG.md` — one v2.3 entry covering both #389 and #387

## 12. Sequencing summary

The plan that follows this spec breaks the work into 3 streams that can run in parallel:

- **Stream A — Backend (#387):** SSE endpoint, event model, orchestrator queue, ring buffer, tests. Self-contained.
- **Stream B — Frontend state + shared infra:** reducer, cost table, types codegen, `useDeployStream` hook, `<StepIndicator>`, tests for the reducer. No dependency on Stream A's runtime (uses mocked stream in tests).
- **Stream C — Frontend step components + entry points:** five Step components, two shared panels, four entry-point links, E2E specs. Depends on Stream B's reducer types (TS imports).

All three converge on a single `feat/389-deployment-wizard` branch (Stream A may use a stacked branch for review hygiene). The author merges all three then opens the PR.

---

**Status:** ready for `writing-plans`.
