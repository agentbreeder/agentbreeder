# agent.yaml → `agentbreeder build` → governed deploy, with registries and LiteLLM (Google ADK first)

- Status: **r3**, design-reviewed (architecture+security; DX) and Codex-reviewed; §18 is normative where it differs. Owner direction approved 2026-11-07:
  rebuild on the new engine, ADK first, LiteLLM first-class, Northwind as the only example.
- Epic #725. This spec: #726. Slices: #727–#731.
- Acceptance reference: `~/personal-github/agentbreeder-support-agent` (hand-written ADK Northwind
  agent, 61/61 eval gate through LiteLLM).

## 1. Problem

The rebuild deploys an image you already built, behind a strong identity gate. Building it is
the hard part: Northwind is about 2,000 lines of hand-written runtime code. 2.x had `agent.yaml`
with registry references, but the truth audit (2026-11-07) found it never delivered:

- ADK still needed the user's `agent.py`;
- resolving a tool to its code returned a 500;
- indexes were in-memory only;
- MCP discovery was fabricated.

## 2. Goal

A user writes `agent.yaml` and **no Python**. AgentBreeder:

- resolves exact-version refs to prompts, tools, knowledge sets and eval sets, and pins them in
  `agent.lock`;
- builds an image from a maintained ADK runtime;
- emits a v2 deploy manifest;
- deploys through the governed engine;
- mints a scoped LiteLLM key per deployment;
- optionally runs the eval gate (`release`).

Custom logic, such as Northwind's order tools, lives in the **tool registry** as packaged code.

## 3. Scope changes, stated honestly

The engine changes in these places, each with its own tests:

| Change | Where |
|---|---|
| Local secret files for `secret://` refs; remove the `SECRETS_UNSUPPORTED` deny (gate.py:295-298) | gate, runtime, deployment, manifest docs (§9) |
| `model_access` block in the v2 manifest; LiteLLM gateway in operator config; per-deployment virtual keys; budget ceilings in the gate | manifest, operator_config, gate, deployment (deploy/remove/disable), reconcile, status, doctor (§10) |
| New commands: `build`, `release`, `eval`, `registry`, `secret set`, `doctor agent.yaml` | cli + new modules |

`agent.yaml` is a new **authoring** file read only by `build`/`release`. The deploy manifest stays
image-in, gaining only the optional `model_access`. `CLAUDE.md`, `ARCHITECTURE.md` and the docs
are updated to say this:

> AgentBreeder builds ADK agents from `agent.yaml` and deploys any image.

Claim rule: the docs name only what the acceptance test proved.

## 4. Non-goals (Phase A)

- Frameworks other than ADK.
- A registry server or UI (Studio, Phase B).
- MCP servers: Phase A has no MCP registry kind. Web search uses a tool through the existing tools
  proxy (review M1: discovery needs secrets at build time, and stdio can't run under the runtime
  hardening). MCP is the next kind, over Streamable HTTP only.
- Provisioning the supporting stack (Postgres+pgvector, LiteLLM, Phoenix, tools proxy, ops). The
  Northwind example ships it as a Compose file, and the docs say so.
- Runtime enforcement (`mcp_http`): acceptance runs with it **off**, because the internal network
  can't reach the stack (egress/boundary.py:59).
- Zero-downtime replace. `release` promotes by remove-then-deploy (§11), with rollback to the
  previous digest.
- Cloud targets; 2.x API, dashboard or registry DB; a "hello" example (owner: Northwind only).

## 5. `agent.yaml` (authoring schema v1)

Environment-neutral: the same file builds one image for every environment. Services, bindings and
identities come from operator config.

```yaml
kind: agent                               # required discriminator
schema_version: 1
name: northwind-support                   # DNS label, ≤ 53 chars (room for "-candidate")
runtime: runtime/adk@1.0.0
description: Customer support for Northwind Notes and the Northwind Pen.

model:
  alias: support-model                    # LiteLLM alias; the operator's gateway maps it to providers
  reasoning: low                          # → ADK LiteLlm reasoning_effort
  max_model_calls: 4                      # last call has tools off
  budget_usd: 25                          # optional; may only LOWER the environment ceiling
  rpm: 120                                # optional; same rule

prompt: {ref: prompt/northwind-support@1.0.0}     # or {path: ./prompts/support.md} while iterating
knowledge:
  - {ref: knowledge/northwind-help@1.0.0, top_k: 5, extra_searches: 1}
tools:
  - {ref: tool/customer-notes@1.0.0}
  - {ref: tool/escalate-to-human@1.0.0}
  - {ref: tool/orders@1.0.0}
  - {ref: tool/cancel-order@1.0.0}         # writes a request row only; staff decide elsewhere
  - {ref: tool/web-search@1.0.0}
memory: {sessions: postgres, notes: true}  # notes requires tool/customer-notes (validated)
agent_events: postgres                     # the agent's own event log, NOT the deploy audit chain
traces: otlp
evals: {ref: eval/northwind-golden@1.0.0, threshold: 1.0, retries: 1}
```

**Validation** follows `manifest.py`'s strict style:

- bounded YAML, at most 64 KiB;
- no anchors, aliases or duplicate keys; unknown fields rejected;
- pydantic models.

**Wrong-file detection.** `validate` and `build` detect the three file shapes and redirect:

- a v2 manifest gets "this is a deploy manifest; run `deploy`";
- a 2.x file (`spec_version`/`framework` without `kind`) gets "unsupported, see legacy-2x".

Nothing in `agent.yaml` can grant permissions, select policy or raise a ceiling.

## 6. Refs, registries and `agent.lock`

**Ref grammar.**

- `kind/name@X.Y.Z`, where kind is one of `runtime`, `prompt`, `tool`, `knowledge`, `eval`.
  Name and version are regex-checked before any path join.
- **Exact versions only** in Phase A, with no ranges.
- `{path: ./…}` refs are allowed for iteration. They are hashed into the lock and refused by
  `build --locked`.

**Registry layout.**

- Filesystem roots: `./registry` plus `AGENTBREEDER_REGISTRY_PATH`. Each version lives at
  `<root>/<kind>/<name>/<version>/manifest.yaml` plus its payload.
- If a ref exists in several roots **with different hashes**, that is an error (no silent
  shadowing). `--locked` loads only the locked `source`.

**Integrity.**

- **Allowed entries:** regular files and directories only. Symlinks, hardlinks, devices,
  absolute paths, `..` and non-UTF-8 names are rejected.
- **Copy, then hash:** the version is copied into the temp build context, then hashed. The hash
  covers length-prefixed `(relpath, mode, size, bytes)` records in sorted order.
- **Build from the hashed copy**, so there's no time-of-check/time-of-use gap.
- **Lock checks:** a mismatch with `agent.lock` gives `[HASH_MISMATCH]`.
- **Publish:** `registry publish` refuses to overwrite a version.
- **Limit (documented):** hashes detect tampering only after the first lock.

**Lock semantics (cargo-like).**

| Command | Behaviour |
|---|---|
| plain `build` | uses the lock where it matches, adds missing entries, refuses a stale lock (`[LOCK_STALE]`) |
| `build --update [ref]` | re-resolves one ref, or all |
| `build --locked` | refuses any change |

`agent.lock` sits next to `agent.yaml`.

**Kinds.**

| Kind | Payload |
|---|---|
| `runtime` | Shipped in the wheel (`src/agentbreeder/runtimes/adk/1.0.0/`): runtime code, Dockerfile template, hash-locked requirements for amd64 and arm64 |
| `prompt` | `prompt.md`; variables are runtime-provided only (customer notes, date) |
| `tool` | `tool.yaml` (functions, JSON schemas, `writes_requests` flag, informational `needs`) plus `src/`. Copied onto `PYTHONPATH`, **never pip-installed**. Phase A tools may not add dependencies beyond the runtime lock |
| `knowledge` | `*.md`/`*.txt` sources, `chunking`, `embedding: {alias, dimensions}`, `store: pgvector`. The runtime syncs on start (hash-diffed); the table is named by name and version |
| `eval` | `golden.jsonl` plus `judge: {alias}` and fixture-reset SQL |

**Commands.** `registry new <kind> <name>`, `registry publish <dir> [--to ROOT]` (prints
`ref sha256 path`), `registry list [ref]`, `registry verify`.

## 7. The ADK runtime (`runtime/adk@1.0.0`)

This is Northwind's proven code, generalised and driven by a data-only `agent.config.json`. It
replaces `agent.py`, `server.py`, `memory.py`, `rag.py` and `telemetry.py`. Reviewed pieces of
`v2.7.3:engine/runtimes/templates/google_adk_server.py` are used where they help.

- **Model:** ADK `LiteLlm("openai/<alias>", api_base=<gateway agent_url>,
  api_key=<LLM_GATEWAY_KEY_FILE>, reasoning_effort=…)`. Thought signatures were verified on
  2026-11-07.
- **Plugins:**
  - step cap;
  - agent events (append-only, the agent's own);
  - token accounting;
  - tool errors returned to the model;
  - OpenInference spans.
- **Tools:**
  - loaded from `PYTHONPATH`, with ADK function declarations from the tool's schema;
  - every call gets a `ToolRuntime(customer, session, db, events)`, where `customer` is the
    server-side session user and is never model-supplied;
  - `writes_requests` tools only insert request rows: the runtime has no execute path for
    irreversible actions.
- **Memory:** `DatabaseSessionService` and notes through the instruction provider.
- **RAG:** hybrid pgvector, BM25 and RRF, up front plus a capped search tool.
- **Server:** `/health`, `/invoke` and the chat page, with the Host check, JSON only, a
  per-session lock and error mapping.
- **Secrets:** read only from `*_FILE` (§9).
- **Honest limit (documented):** tools run in-process with the agent's privileges, so `needs` is
  informational. Real DB scoping needs a least-privilege role with row-level security; that is a
  later slice, and Phase A states it.

## 8. `agentbreeder build`

```
agentbreeder build agent.yaml [--locked | --update [REF]] [--environment ENV] [--output deploy/] [--json]
```

1. Validate, resolve refs, then verify or write `agent.lock`.
2. Assemble the build context in a temp dir: copy, then hash. The **fixed** Dockerfile
   template interpolates nothing; it only COPYs:
   - the runtime;
   - the resolved artifacts;
   - `agent.config.json`.

   The base image is pinned by digest, installs use `pip install --require-hashes`, and the
   image declares no `VOLUME`.
3. `docker build` with no secrets and no build args. Produces an image ID.
4. Write `deploy/<name>.deploy.yaml` (v2 manifest) from the operator environment:
   - `image`;
   - `identity` from `environments.<env>.agents.<name>.identity`;
   - `secrets` from `environments.<env>.bindings`;
   - `model_access` (gateway, aliases, budget, rpm).
5. Write `deploy/<name>.build.json` (not secret): the yaml hash, lock hash, runtime version and
   image.

Human output ends with the next command to run. `--json` output is
`{name, image, manifest_path, build_record_path, lock:{path,changed}, refs[], aliases}`.

## 9. Local secret files (#727)

- **Source.** `agentbreeder secret set <name> [--state-dir]` reads stdin and writes
  `<state>/secrets/<name>` (0600, caller-owned, `O_NOFOLLOW`). `AGENTBREEDER_STATE_DIR` sets the
  default state dir.
- **At deploy,** after gate and policy:
  1. A stat-only preflight checks each source file's owner and mode. Failures return
     `[SECRET_MISSING]` or `[SECRET_MODE]`.
  2. Each value is copied to `<state>/deployments/<id>/secrets/<name>` as 0444, inside a 0700
     directory, so the container's uid 65532 can read the file while nobody else on the host can
     traverse to it.
  3. The file is mounted with
     `--mount type=bind,src=…,dst=/run/secrets/<name>,readonly` and the container gets
     `<ENV>_FILE=/run/secrets/<name>`. This works with `--read-only`.
- **Cleanup.** The copies are deleted on remove and on every failure path.
- **Never exposed.** Values never appear in env, labels, build args, logs or audit. The audit
  records names only.
- **Tests run on Linux as well as Docker Desktop**, whose file sharing hides permission problems.

## 10. LiteLLM support (first-class gateway)

**Operator config:**

```yaml
gateways:
  litellm-local:
    type: litellm
    url: http://127.0.0.1:4000
    agent_url: http://host.docker.internal:4000
    master_key: file:/Users/me/.agentbreeder/litellm-master   # existing `env:`/`file:` string refs; absolute path
environments:
  dev:
    targets: [local]
    gateway: litellm-local
    ceilings: {budget_usd: 50, rpm: 300}
    services: {traces_url: …, tools_proxy_url: …}       # passed to the runtime as config
    bindings: {DATABASE_URL: secret://northwind-db}         # env name → local secret file
    agents:
      northwind-support:
        identity: {provider: demo, ref: support-agent, environment: dev}
        candidate_identity: {provider: demo, ref: support-agent-candidate, environment: dev}
```

**Manifest `model_access`** (optional, v2):
`{gateway, aliases: [...], budget_usd, rpm}`. It is visible to policy. If the requested budget or
RPM is above the environment ceiling, the gate returns `[BUDGET_CEILING]`.

**Key lifecycle.**

- **Mint (deploy).** Inside `store.lock()`, after the gate, approval and the `starting` commit.
  The key is aliased to the deployment ID (idempotent on retry) and restricted to the aliases,
  budget, RPM and metadata `{agent, environment, deployment_id, principal}`. Its value goes to
  the deployment's secret dir as `LLM_GATEWAY_KEY_FILE`, and its ID is stored on the record. The
  audit records the key ID only.
- **Failure.** Any failure before ready revokes the key and deletes the file.
- **Remove.** Revokes the key.
- **Disable.** Sets the local blocker first, then revokes best-effort. The kill switch never
  depends on the gateway being up.
- **Enable.** Does not mint, because it starts nothing; the next deploy mints.
- **Reconcile.** Revokes orphaned aliases and reports missing or over-budget keys.

**Other commands.** `status` shows spend versus budget. `doctor` checks gateway reach, the master
key and the aliases. The alias check runs at **deploy** (with operator config) and in `doctor`,
not in `build`, so `build` stays unprivileged.

**What stays the operator's.** AgentBreeder never writes LiteLLM's `config.yaml`; model mapping
and fallbacks (Gemini, then OpenRouter) stay there. Users may not declare `LLM_GATEWAY_KEY`. The
eval judge uses its own key, minted for the run and revoked after it.

## 11. `release` and `eval`

```
agentbreeder eval agent.yaml --endpoint URL            # golden set only, no promotion
agentbreeder release agent.yaml [--environment ENV] [--rollback]
```

`release` holds a release lockfile and runs these steps:

1. `build --locked`.
2. Deploy `<name>-candidate` with the candidate identity and its own key. A candidate that comes
   back `pending_approval` makes release exit 3 with the re-run command.
3. Reset the eval fixtures (the eval set's SQL), then run the golden set (§5 `evals` threshold;
   `--threshold` overrides it).
4. Remove the candidate.
5. If the gate passes, deploy the **exact evaluated image** as `<name>`. Because the engine
   refuses a different spec under an active name (deployment.py:189-200), release **removes the
   old production first**. There is brief downtime, and the previous image is kept in
   `deploy/<name>.release.json`, so `release --rollback` redeploys it.
6. An already-promoted image is a no-op.

Release writes its own `release_started/eval_result/promoted/failed` audit events carrying the
image and the eval run ID. The gate is **orchestration, not an engine control**: plain `deploy`
can bypass it, and the docs say so.

**Exit codes:**

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Runtime failure |
| 2 | Invalid input or a denial, with a `[CODE]` |
| 3 | Pending approval |
| 5 | Eval gate failed; production unchanged |

## 12. `doctor agent.yaml --environment ENV`

Preflight checks:

- operator config;
- that identities and the candidate identity resolve (read-only);
- secret files exist and have the right modes;
- gateway reach, master key and aliases;
- that refs resolve and hashes match;
- Docker;
- `host.docker.internal` (Linux needs `--add-host`).

Every failure prints the fix.

## 13. Northwind conversion: `examples/northwind/`

| Hand-written piece | Becomes |
|---|---|
| SYSTEM prompt | `registry/prompt/northwind-support/1.0.0` |
| kb/*.md + rag.py | `registry/knowledge/northwind-help/1.0.0` + runtime RAG |
| commerce.py (lookup, catalog) | `registry/tool/orders/1.0.0` |
| request_cancellation | `registry/tool/cancel-order/1.0.0` (`writes_requests: true`) |
| notes / escalate / web_search | `tool/customer-notes`, `tool/escalate-to-human`, `tool/web-search` (via tools proxy) |
| golden.jsonl + fixture reset | `registry/eval/northwind-golden/1.0.0` |
| agent.py, server.py, memory.py, telemetry.py, rag.py | `runtime/adk@1.0.0` |
| Compose stack, proxy, ops dashboard, cancellation approvals, seed SQL | `examples/northwind/stack/` (not AgentBreeder product; stated) |

**Walkthrough (documented, run verbatim):**

```
.venv/bin/pip install -e .
cd examples/northwind
export GEMINI_API_KEY=… TAVILY_API_KEY=… [OPENROUTER_API_KEY=…]
./stack/up.sh        # Compose stack + seed; writes .agentbreeder/operator.yaml, principals, master key, DB secret
export AGENTBREEDER_OPERATOR_CONFIG=$PWD/.agentbreeder/operator.yaml AGENTBREEDER_STATE_DIR=$PWD/.agentbreeder/state
agentbreeder doctor agent.yaml --environment dev
agentbreeder build agent.yaml --environment dev
agentbreeder release agent.yaml --environment dev   # candidate → 61 evals → promote
agentbreeder status <deployment id printed by release>
```

## 14. Acceptance criteria

1. `examples/northwind` contains **no user-written agent Python**. The Python in its tool
   packages and the stack is registry and ops code, and is labelled as such.
2. The §13 walkthrough runs verbatim from a clean checkout on macOS with Docker Desktop.
   `release` passes **61/61** (one retry per case), promotes the exact evaluated image, and the
   chat endpoint answers.
3. No secret value (DB URL, gateway key, master key, provider keys) appears in image layers,
   `docker history`, `docker inspect`, labels, logs, the audit, `agent.lock` or the build or
   release records. A test greps for each value.
4. A one-byte change to a locked registry version makes `build --locked` fail with
   `[HASH_MISMATCH]`. A symlink in a registry version is rejected.
5. Customer scoping holds: asking about another customer's order gets "not found" (golden cases).
6. Cancellation is never executed by the agent; it only writes a request row.
7. **LiteLLM:**
   - each deployment has its own key;
   - `remove` revokes it (401 after);
   - an alias outside the allow-list is refused;
   - an above-ceiling budget gives `[BUDGET_CEILING]`;
   - `disable` works with the gateway down.
8. **Engine:** the existing suite passes; `deploy` of a hand-built image without `secrets` or
   `model_access` behaves exactly as before; secret-file tests pass on Linux.
9. **Quality bar:** coverage ≥ 80% on changed files; `/launch` gate green; the docs and website
   claim only items 1–8.

## 15. Cross-repo

| Repo | Change |
|---|---|
| `agentbreeder` | all of the above, plus docs (manifest.md `model_access`/secrets, local-deployment, a new agent-yaml.md, CLAUDE.md, ARCHITECTURE.md) |
| `website/` | agent.yaml/build docs page and "what ships", after AC 1–8 |
| `agentbreeder-cloud` | none in Phase A; it will consume the same refs and `model_access` |

## 16. Risks

- **The runtime grows into a framework.** It is limited to Northwind's features; anything new
  needs an acceptance case.
- **Tool packages are arbitrary code** running with the agent's privileges. They are pinned and
  hashed, and the docs state the limit.
- **About 150 hash-locked packages** for ADK and LiteLLM. They are compiled with
  `uv pip compile --generate-hashes` for both architectures and shipped with the runtime.
- **LiteLLM SDK quirks** (it imports `mcp`; the `openai/` route). Pinned, and covered by the
  acceptance eval.
- **Eval cost:** about $0.19 per release on Gemini Flash.
- **Candidate evals write to the shared stack DB.** Eval customers are isolated (`eval-cust-*`),
  and fixtures are reset per run.

## 17. Review log

**r1 → r2.**

- *Architecture and security:*
  - C1: engine scope listed and `model_access` added (§3, §10).
  - C2: readable secret copies and bind mount (§9).
  - H1: promotion by remove-then-deploy, plus rollback (§11).
  - H2: transactional home for key minting (§10).
  - H3: engine-managed key (§10).
  - H4: enforcement off (§4).
  - H5: integrity rules (§6).
  - H6: tool boundary and no pip install (§6, §7).
  - M1: MCP deferred (§4).
  - M2: `writes_requests` (§6).
  - M3: release audit, lock and approval (§11).
  - M4: alias check at deploy (§10).
  - M5: fixed Dockerfile and hash locks (§8).
  - L1/L2 (§5, AC3).
- *DX:*
  - H1: `kind`, wrong-file detection and the `.deploy.yaml` name (§5, §8).
  - H2: environment-neutral `agent.yaml` (§5, §10).
  - H3: ref grammar and lock semantics (§6).
  - H4: naming (§5, §6).
  - H5: `doctor` and `secret set` (§9, §12).
  - M1: `eval` and the release rules (§11).
  - M2: path refs and registry commands (§6).
  - M3: name ≤ 53 characters (§5).
  - Exit codes and JSON (§8, §11).
  - The "hello" demo was declined by the owner.

**Verified against code:** gate.py:294-298, deployment.py:189-200, runtime.py:113/140-141,
egress/boundary.py:59, operator_config.py:91/220, store.py:156-165.

## 18. r3: Codex review (normative)

All of these were checked against the code.

**Slice 1, implemented:**

- **Preflight.** Preflight checks metadata only (lstat: type, owner, mode, size). No value is
  read until after the gate and approval; values are read with
  `O_NOFOLLOW | O_NONBLOCK` plus fstat, so a FIFO can't block while the lock is held.
- **Container generations.** Secret copies belong to one **container generation**. If a
  container exists but its copies are missing (a failed attempt cleaned them up), it is removed
  and recreated. Rotation means redeploying a new container. A healthy existing container keeps
  the copies it started with.
- **Cleanup.** Copies are deleted on health or start failure, on filesystem errors during
  materialise (`SECRET_UNREADABLE`), on remove and on **disable**. After an `AuditError` they are
  kept on purpose: the container may be running, and `reconcile`/`remove` handles that.
- **One mount per secret name.** Several env names can share one mount, each getting its own
  `_FILE` variable.
- **State dir.** `secret set` creates the state dir as 0700 owned by you, which is the
  `Store` rule.
- **Not with enforcement.** With `runtime_enforcement: mcp_http`, secrets are denied
  (`SECRETS_UNSUPPORTED`) until the boundary path supports mounts.

**Later slices, spec changes:**

1. **Release ordering** (§11).
   - Release first obtains or verifies approval for the **exact production manifest**. A pending
     or denied approval exits 3 or 2 with production untouched.
   - It writes a **release journal** before any destructive step: previous deployment ID, full
     manifest, environment, Docker context and candidate image. Every step is resumable, and
     `--rollback` uses the journal.
   - Idempotency compares the **manifest digest and state**, not just the image.
2. **Key lifecycle crash safety** (§10).
   - Record a durable mint intent on the deployment before `/key/generate`.
   - On retry, delete by alias, then regenerate. LiteLLM never returns an existing key's value.
   - The client includes `/key/list`. `reconcile` lists keys by alias and metadata, including
     those for removed records, and revokes orphans.
3. **Environment config delivery.** The engine passes `environments.<env>.services` and the
   gateway `agent_url` to the container as plain (non-secret) environment variables at deploy:
   `AGENTBREEDER_SERVICE_<NAME>` and `AGENTBREEDER_GATEWAY_URL`. One image digest therefore runs
   in two environments, and that is tested.
4. **Gateway authorisation in the gate.**
   - `model_access.gateway` must equal `environments.<env>.gateway`.
   - Aliases must be in the operator allow-list `environments.<env>.models`.
   - An omitted budget or RPM defaults to the ceiling.
   - `BUDGET_CEILING` and `MODEL_NOT_ALLOWED` are not overridable in audit mode.
   - `model_access` is added to the policy request and the approval snapshot, with a policy test
     that depends on it.
5. **Linux reachability.** The engine adds `--add-host host.docker.internal:host-gateway` on
   Linux, and the example stack binds LiteLLM where containers can reach it. Both are in
   slice 6.
6. **Slice order.** The `model_access` and `gateways` schema moves into slice 4, so slice 4 and
   slice 5 artifacts validate. Key minting stays in slice 6. Before slice 6, the stack mints one
   key and passes it as a plain secret file.
7. **Validators.** A user `LLM_GATEWAY_KEY` binding or secret is rejected, with a test.
8. **Honest ACs.** AC5 and AC6 become "the shipped Northwind tools …". They are cooperative tool
   code, not an engine boundary. A restricted DB role is future work.
9. **Eval fixtures.** Eval fixture SQL runs under an eval-only DB role limited to the `eval-cust-*`
   rows and tables it resets.
