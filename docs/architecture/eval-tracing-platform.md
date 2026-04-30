# Eval + Tracing Platform — Online + Offline Evals, Trace Replay, Drift, Canary

> **Status:** Design (not yet implemented). Tracked under epic `#TBD-G` (linked once filed).
> **Author:** AgentBreeder team, 2026-04-30.
> **Related design:** Closes the AgentOps loop opened by `docs/architecture/agentops-lifecycle.md` (#243). Reuses every primitive from epics #244, #250-#252, #270, #283, #284.

---

## 1. The problem

The lifecycle epic (#244) lets users ship agents through `dev → staging → prod` with eval gates on each step. But the *eval gate* is a placeholder — there's no concrete pipeline that actually:

1. Runs a candidate agent against a fixed dataset and produces a pass/fail score
2. Samples production traffic, judges it asynchronously, and aggregates per-agent quality scores
3. Detects when a deployed agent's quality regresses and alerts on it
4. Replays a real production trace against a candidate version to A/B compare
5. Pools human raters with consensus rules
6. Splits traffic at the deployer (canary / blue-green) so prod evals decide ramp / rollback

Today AgentBreeder ships:
- A basic `eval.yaml` schema (`engine/schema/eval.schema.json`)
- Migration 008 (`evaluation_framework_tables` — datasets, runs, comparisons)
- `api/routes/evals.py`
- `api/routes/tracing.py` + the sidecar (Track J) emitting OTLP spans
- Dashboard `/evals/{datasets,runs,compare}` pages
- `cost_events` table for per-call cost attribution

But none of it is wired into the lifecycle (#244), and the **online** dimension — continuous quality judgement on live traffic — is missing entirely. Without this, the platform ships agents but doesn't *know* when they regressed.

## 2. Goals and non-goals

### Goals
- Two distinct eval modes — **online** (sample N% prod, judge async, drift alerts) and **offline** (run a dataset, gate promotion).
- Both modes feed one canonical **judge pool** with three judge kinds: LLM-as-judge, programmatic, human raters.
- **Trace replay** as the bridge: a real prod trace can be reconstructed and run against a candidate version.
- **Drift detection** with auto-incident creation (#207) when prod regression crosses a threshold.
- **A/B / canary deploys** at the deployer layer — eval scores per variant determine ramp / rollback.
- **Human rater pool** with consensus rules and inter-rater agreement scoring.
- **Universal `obs-mcp` tools** (peer of #270, #283, #284) so any agent in any framework can search traces, run evals, query metrics, replay calls.
- Per-env parity, ResourcePermission ACL, full reuse of the AgentOps lifecycle (#244).
- Cost attribution for judge calls (online evals are not free).

### Non-goals
- Custom monitoring / observability backends beyond a Postgres + ClickHouse abstraction (use the platform's existing OTLP path).
- Offline distillation / dataset synthesis from prod (separate research effort; we only support deterministic replay).
- Active-learning loops that auto-curate new training data.
- Multi-region eval aggregation.
- Real-time (sub-second) drift detection. Default is 5-min rolling windows.

---

## 3. The architecture

```
                                        AGENT INVOCATION (any env)
                                                  │
                                                  ▼
                                      Sidecar (Track J) emits OTLP
                                                  │
                                                  ▼
                              ┌──────────────────────────────────┐
                              │   Trace store                    │
                              │   (Postgres for small,           │
                              │    ClickHouse for large)         │
                              │                                  │
                              │   queryable by agent / env /     │
                              │   git_ref / status / latency     │
                              └──────────────────┬───────────────┘
                                                 │
                                       ┌─────────┴─────────┐
                                       │                   │
                                       ▼                   ▼
                                  ONLINE               OFFLINE
                                       │                   │
                                  Sample N%          PR opens / promote click
                                       │                   │
                                       ▼                   ▼
                              ┌─────────────────────────────────────────┐
                              │           JUDGE POOL                     │
                              │                                          │
                              │   LLM-as-judge   programmatic   human   │
                              │   (Claude /       (regex, AST,   (queue,│
                              │    GPT-4o)        schema,        consen-│
                              │                   citation)      sus)   │
                              └────────────────────┬─────────────────────┘
                                                   │
                                                   ▼
                                       eval_scores (per agent / env / window)
                                                   │
                            ┌──────────────────────┼──────────────────────┐
                            │                      │                      │
                            ▼                      ▼                      ▼
                      Drift detector      /agentops dashboard    Promote gate (#246)
                            │              (per-agent quality           │
                            ▼               trend lines)                │
                      Auto-incident                                     │
                      via #207                                          ▼
                                                              Block PR if score
                                                              < threshold;
                                                              comment with details
```

The trace store is the single source of truth — both online and offline pipelines read from it. The judge pool is the single judging implementation — both ingestion paths pump items into it.

---

## 4. Three new YAML kinds

### `eval.yaml` (extends existing schema)
What to measure for a given agent.

```yaml
name: customer-support-quality
agent: agents/customer-support
dataset: datasets/support-golden-2026q1
judges:
  - ref: judges/answer-correctness         # LLM-as-judge
  - ref: judges/contains-citation          # programmatic
  - ref: judges/human-helpfulness-rater    # human pool

metrics:
  - name: pass_rate
    type: average                           # average | percentile | weighted
    threshold: 0.92
    aggregation_window: 24h

modes:
  online:
    enabled: true
    sample_rate: 0.05                       # 5% of prod traffic
    judge_concurrency: 8
    cost_budget:
      max_monthly_usd: 500.0                # cuts off when exceeded
  offline:
    enabled: true
    promote_gate: true                      # block #246 promote on threshold fail
    on_pr_open: true                        # also run the dataset on every PR

drift:
  enabled: true
  baseline: "7d"                            # compare to last 7 days
  alert_regression_pct: 5                   # >=5% regression triggers alert
  auto_incident: true                       # via IncidentService (#207)
```

### `dataset.yaml`
What to run against. Three kinds:

```yaml
# 1. Curated — golden inputs/outputs
name: support-golden-2026q1
kind: curated
items:
  - input: "How do I refund order #1234?"
    expected_contains: ["refund policy", "30 days"]
  - input: "..."

# 2. Replay — sampled from prod traces
# kind: replay
# source:
#   trace_query: "agent.name='customer-support' AND env='prod' AND duration_ms > 1000"
#   sample_size: 500
#   sample_strategy: stratified  # uniform | stratified | most_recent

# 3. Synthetic — generated by an agent (deferred to v2)
```

### `judge.yaml`
How to score. Three kinds:

```yaml
# LLM-as-judge
name: answer-correctness
kind: llm
llm:
  model: claude-sonnet-4
  prompt: prompts/judge-correctness-v3
  output_schema:
    score: { type: number, range: [0, 1] }
    rationale: { type: string }
  temperature: 0.0

# Programmatic
# name: contains-citation
# kind: programmatic
# module: engine.judges.standard.contains_citation
# args:
#   patterns: ["\\[\\d+\\]", "see (section|page) \\d+"]

# Human
# name: human-helpfulness-rater
# kind: human
# human:
#   rater_pool: support-leads
#   prompt: prompts/human-rater-rubric-v1
#   consensus: 2_of_3
#   max_items_per_rater_per_day: 50
```

---

## 5. Online eval pipeline

### Sampling
The sidecar (Track J) emits an OTLP span for every agent invocation. A **trace writer** ingests them into the trace store with a per-`(agent, env)` sampling decision based on `eval.yaml.modes.online.sample_rate`. Sampling is deterministic on `trace_id` so the same traces always sample (reproducibility).

### Judge queue
Sampled traces enqueue a job into `online_judge_jobs`. A worker (using the existing daily-cron + asyncio infra from #199) drains the queue, runs each judge from `eval.yaml.judges`, and writes per-(trace, judge) rows into `eval_scores`.

### Score aggregation
A second cron job rolls per-trace scores into per-`(agent, env, window)` aggregates with the metric type from `eval.yaml.metrics` (average / percentile / weighted). The result lives in `eval_aggregates` and powers the `/agentops` dashboard's "agent quality trend" column.

### Drift detection
A third cron job computes `current_window_score - baseline_window_score`. If `regression_pct >= alert_regression_pct`, the platform:
1. Auto-creates an `Incident` via `IncidentService` (#207) with severity `high` and the eval name + scores
2. Posts a comment on the most recent merged PR for that agent
3. Surfaces a red dot on `/agentops` for that agent row

### Cost attribution
Every judge LLM call writes a `cost_events` row attributed to the calling team's budget. Online evals respect `eval.yaml.modes.online.cost_budget.max_monthly_usd` — the worker pauses sampling when the budget is exhausted (with an audit event + dashboard banner).

---

## 6. Offline eval pipeline

### Trigger paths
Three ways to fire an offline eval:
1. **PR opened** with `eval.yaml.modes.offline.on_pr_open: true` → automatic
2. **Promote button clicked** in the dashboard or `agentbreeder promote --env <env>` CLI → required if `promote_gate: true`
3. **Manual** — `agentbreeder eval run <eval-name>` or POST `/api/v1/evals/{name}/run`

### Run orchestrator
For each item in the dataset:
1. Send the input to the candidate agent version (deployed in the env, or a temporary sandbox)
2. Capture the output
3. Pump (input, expected, output) into the judge pool
4. Write per-item scores to `eval_runs.items`

After all items:
1. Aggregate per `eval.yaml.metrics`
2. Compare to `threshold`
3. Write `eval_runs.result` = `pass | fail | error`

### Promote gate
The promotion service (#246) checks `eval_runs.result` for the active eval before allowing a promote. If `fail`, the promote is blocked and a comment is posted on the PR with the failing items + scores.

---

## 7. Trace replay — the bridge

`POST /api/v1/traces/{trace_id}/replay?candidate=<agent>:<version>` does:

1. Fetch the trace from the trace store
2. Reconstruct the **input message + context** by walking the OTLP span tree (look for `agent.input.message` attribute on the root span)
3. Resolve the candidate version's endpoint (in the current env, or a temporary sandbox spun up from the candidate's `agent.yaml`)
4. Invoke the candidate with the same inputs
5. Run the eval's judges against (original_output, candidate_output)
6. Return a structured diff: `{ original_score, candidate_score, regressed: bool, rationale }`

This primitive powers:

- **A/B testing at promote time** — replay the last 500 prod traces against the candidate, gate on win-rate
- **Regression debugging** — given a customer complaint, replay against the previous version to confirm the regression
- **Dataset bootstrapping** — promote a curated set of replayed traces into a permanent `dataset.yaml`
- **Continuous canary** — see §8

---

## 8. A/B / canary at the deploy layer (extends #246)

`agent.yaml` grows a `deploy.strategy` block:

```yaml
deploy:
  cloud: aws
  runtime: ecs-fargate
  strategy: canary                          # canary | blue_green | all_at_once
  canary:
    initial_percent: 5
    eval_ref: evals/customer-support-quality
    auto_promote_threshold: 0.95
    auto_rollback_threshold: 0.85
    ramp_steps: [5, 25, 50, 100]
    soak_per_step: 30m
```

The deployer (per-cloud) configures traffic-splitting at the cloud's load-balancer / mesh:
- **AWS ECS** — weighted target groups via ALB
- **GCP Cloud Run** — `gcloud run services update-traffic --to-revisions=NEW=5,OLD=95`
- **Azure Container Apps** — revision weights
- **Kubernetes** — Argo Rollouts or native traffic-splitting

The online eval pipeline (§5) computes scores per variant (`agent.version` is part of the OTLP span attributes). A separate canary controller cron:
1. Reads scores per variant for the current ramp step
2. If `score >= auto_promote_threshold` after `soak_per_step` → advance to next step
3. If `score < auto_rollback_threshold` → rollback to previous version, auto-create Incident
4. Emit audit events on every ramp / rollback

---

## 9. Human rater pool

A judge of `kind: human` enqueues prompts into a `human_eval_queue` table:

```
queue_row = (
    eval_name,
    item_id,
    input,
    output,
    rater_pool,
    consensus_rule,
    state,           # pending | in_review | scored | discarded
    rater_assignments,
    scores,
    created_at,
    completed_at,
)
```

Raters score via a new dashboard `/evals/rater` queue page:
- Pulls next item from the rater's pool
- Shows the input + output side-by-side with the rubric prompt
- Rater submits a score (1-5) + free-text rationale
- Server checks consensus (e.g. `2_of_3`); when reached, finalizes the score

**Inter-rater agreement** — an analytics job computes Krippendorff's α per pool per week. Low-α pools get flagged on `/agentops` (rubric ambiguous, raters disagreeing). Raters with consistently outlier scores get auto-excluded from consensus.

---

## 10. Universal observability tools — `obs-mcp`

Same pattern as #270 (RAG) and Epic E (memory). Single MCP server; thin SDK wrappers in Python / TypeScript / Go.

| Tool | Purpose | ACL |
|---|---|---|
| `trace.search(query, time_range)` | Find traces by agent / env / status / latency / metadata | `read` |
| `trace.get(trace_id)` | Full trace detail with spans + attributes | `read` |
| `trace.replay(trace_id, candidate)` | Replay against a different agent version, return diff | `run` |
| `eval.run(eval_name, dataset, candidate)` | Kick off an offline eval; returns run id | `run` |
| `eval.score(agent, env, window)` | Aggregated score over a window | `read` |
| `metric.record(name, value, tags)` | Custom metrics from agent code | `write` |
| `metric.query(name, agg, window)` | Query custom metrics | `read` |

ACL via `ResourcePermission`. Server-level `read` lets agents discover their own traces; `run` is required to start an offline eval (deployer role default); `write` on `metrics:<namespace>` is per-agent.

The `metric.record` tool is the platform's substrate for **custom evals** — an agent can emit `metric.record("answer_length", 142, {tier: "free"})` and a custom dashboard query will surface it.

---

## 11. Lifecycle reuse

`eval.yaml`, `dataset.yaml`, `judge.yaml` are all git-backed registry artifacts. Same `Save & Submit` → PR → approve → deploy → registry flow as agents (#244). Editing a threshold, adding a judge, or curating a new dataset is a reviewable change.

Per-env scope: an eval can apply to `dev` only (smoke check), to `staging + prod` (the gate), or to `prod` only (drift monitoring on live traffic). Per-env scoping reuses the same `environments:` block as agents and RAG.

---

## 12. Per-env parity

Same pattern as everywhere else. The eval/judge/dataset configs declare per-env overrides where they matter (e.g. judge model in dev = `gpt-4o-mini` to save cost; in prod = `claude-sonnet-4`). Trace store is per-env (Postgres tables namespaced or separate ClickHouse clusters). Connection pools assume per-env service principal (#248).

---

## 13. Cost attribution

Every judge call → `cost_events` row attributed to the calling team. The `eval.yaml.modes.online.cost_budget.max_monthly_usd` is enforced at the queue worker — when the rolling-30d sum crosses the cap, sampling pauses + an audit event fires + the dashboard surfaces a budget banner.

---

## 14. Migration plan

### Phase 1 — Trace + replay foundation
- Trace store abstraction (Postgres impl first, ClickHouse impl second)
- Sampling policy + writer
- `POST /api/v1/traces/{id}/replay` primitive

### Phase 2 — Schemas + lifecycle
- `eval.yaml` extension
- `dataset.yaml` schema
- `judge.yaml` schema
- Wire all three into the AgentOps lifecycle (#244)

### Phase 3 — Online pipeline
- Online judge queue + scorer
- Score aggregator
- Drift detector + auto-incident

### Phase 4 — Offline pipeline + gate
- Offline run orchestrator
- Promote gate integration with #246
- 3 standard judges + 4 starter datasets seeded via #180

### Phase 5 — Human + canary
- Human eval queue + rater UI
- Consensus rules + inter-rater agreement
- A/B / canary at deploy layer (extends #246)

### Phase 6 — Universal tools
- `obs-mcp` server + 7 tools
- Python / TS / Go SDK wrappers
- Custom metrics primitive

Each phase is independently shippable. v2.4 → v2.6 spans all six.

---

## 15. Open questions

1. **Trace store backend.** Postgres is enough for small workspaces (<10 RPS); ClickHouse is needed for large (>100 RPS). Default to Postgres; ClickHouse opt-in via `workspace.yaml`. Migration TBD.
2. **OTLP attribute schema.** Standardize on a fixed set of attributes (`agent.name`, `agent.version`, `agent.input.message`, `agent.output.message`, `env`, `git_ref`, `actor_email`, etc.) so replay can reliably reconstruct inputs. Spec in a follow-up issue.
3. **Replay isolation.** Where does the candidate run? Three options: (a) hit the deployed candidate in the same env, (b) spin up a one-off sandbox per replay, (c) use a dedicated "replay env" with mock external services. Default to (a) for simplicity; (c) for high-fidelity eval; (b) for security-sensitive replays. Per-eval `replay.isolation` field.
4. **Cost ceiling for replay.** Replaying 1000 traces against a candidate is 1000 LLM calls + 1000 judge calls = potentially $$. Need a per-eval `replay.max_items` and a workspace-level monthly cap.
5. **Human rater authentication.** Are raters AgentBreeder users or external (e.g. Mechanical Turk-style)? v1 = AgentBreeder users only; external rater pools are v2.
6. **Canary controller scheduling.** Does the canary controller live in the API server or as a separate process? v1 = API server cron (uses #199 infra); v2 = dedicated controller for high-frequency ramps.
7. **PII in traces.** Production inputs may contain PII. The trace store needs a redaction pipeline before judges see content. Reuse existing guardrails. Spec in a follow-up.

---

## 16. What exists today that we keep

| Primitive | Status | Reuse |
|---|---|---|
| `engine/schema/eval.schema.json` | Shipped | Extend with `modes`, `drift`, `metrics` blocks |
| Migration 008 (eval tables) | Shipped | Extend with `eval_scores`, `eval_aggregates`, `online_judge_jobs`, `human_eval_queue` |
| `api/routes/evals.py` | Shipped | Extend with replay + run + score endpoints |
| `api/routes/tracing.py` | Shipped | Extend with sampling + replay |
| Sidecar OTLP emission (Track J) | Shipped | Spans become trace store rows |
| `cost_events` table | Shipped | Per-judge-call attribution |
| `audit_log` | Shipped | New event types: `eval.run.started`, `eval.run.completed`, `eval.gate.blocked`, `drift.alert.created`, `canary.advanced`, `canary.rolled_back` |
| `IncidentService` (#207) | Shipped | Drift detector auto-creates incidents |
| AgentOps lifecycle (#244) | Designed | `eval.yaml` / `dataset.yaml` / `judge.yaml` follow the same flow |
| Promote gate (#246) | Designed | Eval result blocks/allows promote |
| Per-env registry (#247) | Designed | Eval configs get `git_ref` + `deployed_by` stamps |
| Per-env service principals (#248) | Designed | Judge LLM connections + replay invocations assume env role |
| Daily cron infra (#199) | Shipped | Sample worker, score aggregator, drift detector, canary controller |
| First-boot seeder (#180) | Shipped | Seeds 3 standard judges + 4 starter datasets |
| ResourcePermission ACL (migration 015) | Shipped | Eval / trace / metric authorization |
| Workspace secrets (Track K) | Shipped | Judge LLM API keys, ClickHouse DSN, etc. |

This epic adds **one obs-mcp server + the online/offline pipelines + a canary controller**. The lifecycle, security, infra, and reporting all come from already-designed work.

---

## 17. Out of scope

- Custom monitoring backends beyond Postgres + ClickHouse
- Active-learning / dataset synthesis from prod
- External rater marketplace integrations (MTurk, Scale, Surge — separate effort)
- Real-time (sub-second) drift alerts (default 5-min windows; hot-loop drift is a v3 concern)
- Multi-region eval aggregation
- Adversarial / red-team automated evals (separate research effort, eventually a peer epic)
