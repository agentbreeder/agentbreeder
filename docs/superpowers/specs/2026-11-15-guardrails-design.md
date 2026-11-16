# Guardrails on model input and output (#1024)

Status: spec r3 (2026-11-15), for owner decisions D1–D10 (§10). Nothing here is implemented.
Parent: #1017 (model layer epic), phase 3. Related: #1023 (direct providers; shares the
policy-schema change and the runtime version plan, §7).

## 1. Problem

Nothing inspects what goes into or comes out of a model call. Policy today acts at deploy time (the
gate, Cedar `deploy`/`approve`/`enable`). At run time it acts only on MCP Streamable HTTP tool calls
through the opt-in sidecar (`egress/host.py` evaluates `call_tool`). Model calls go from the
runtime to the stack's LiteLLM gateway (`runtime/adk@1.2.0`, `LiteLlm(model="openai/<alias>")`).
The gateway's config is reviewed by `gateway_keys.config_review`, which allows no `guardrails:`
section and no `callbacks` (`_SECTIONS`). So an operator cannot turn on LiteLLM's own guardrails
either.

CLAUDE.md's claim rule applies directly: no runtime-enforcement claim beyond what is verified.
Today the only verified runtime enforcement is MCP Streamable HTTP via the sidecar.

## 2. Goal

- An operator can require PII masking or blocking on chat-model input and output for every agent in
  an environment. The operator configures it; the author cannot.
- Each check's outcome is recorded without the content it found.
- The docs and `status` say exactly where the check runs, which gateway routes it covers, and what
  bypasses it.

Non-goals:

- Guardrails on tool calls (the sidecar's scope).
- Prompt-injection or jailbreak detection claims.
- A hosted moderation vendor (#1025 territory).
- Keeping PII out of the agent's own stores (§5).
- The direct route of #1023 (§7).

## 3. Where a check can run

| Option | Runs in | Sees | Bypassed by |
|---|---|---|---|
| **G-A, LiteLLM `guardrails:`** (Presidio PII) | the gateway container (`service/litellm`) | calls on the gateway routes the hook covers (G0 lists them; at least `/chat/completions`) | any route the hook does not cover that the virtual key can reach (the key's `models` limits aliases, not routes, `gateway_keys.py` `mint`); an agent that reaches a provider without the gateway |
| **G-B, runtime callbacks** (`before_model_callback` / `after_model_callback` in `AgentPlugin`) | the agent container | the ADK request and response, after RAG passages are added | in-process tool code (same process; `docs/agent-yaml.md`: "not against hostile tool code"); any runtime that is not `runtime/adk` |
| **G-C, a Cedar `call_model` action** | the host decision service, per call | the attributes the caller sends | needs a caller (G-A hook or G-B); it is a decision point, not a detector, and cannot find PII in text |

G-A is the only option whose check runs outside the agent's code.

## 4. Recommended design

Gateway guardrails (G-A) for detection, and a deploy-time requirement bound to the gateway config
that the stack actually runs. G-B is used for annotation only. A per-call Cedar `call_model`
action (G-C) is deferred (D1).

### 4.1 Presidio service

- A new built-in `service/presidio@1.0.0` runs the analyzer and the anonymizer: two images pinned by
  digest from `mcr.microsoft.com` (arm64 and amd64 checked in G0, D9).
- It sets `egress: false` and `ports: {http: {expose: internal}}`. An `internal` port joins only the
  stack network. Agent containers join only `<project>-agents` (`services/plan.py` `joins`), so
  agents cannot reach Presidio. The analyzer and anonymizer have no auth, so an acceptance test
  asserts an agent container cannot connect to them.
- `service/litellm@1.1.0` adds the `presidio` input and sets `PRESIDIO_ANALYZER_API_BASE` /
  `PRESIDIO_ANONYMIZER_API_BASE` from it. Whether `files/start.py` changes is settled in G0.
  `service/litellm@1.0.0` stays byte-identical.
- G0 verifies that the pinned `litellm-database@sha256:35f2…` image's Presidio hook needs no extra
  package. If it needs one, G1 pins a new LiteLLM image digest in 1.1.0.

### 4.2 `config_review` allow-list

- `config_review` gains a top-level `guardrails:` list. Fields outside the allow-list are refused
  (`GATEWAY_CONFIG_UNSAFE`).
- The draft allow-list is frozen only after G0 checks it against the pinned image:

  | Field | Allowed |
  |---|---|
  | `guardrail_name` | the `_MODEL_NAME` pattern |
  | `litellm_params.guardrail` | `presidio` only (D2) |
  | `litellm_params.mode` | `pre_call` or `post_call`; `logging_only` is allowed but never counts toward a requirement; `during_call` is refused (D3) |
  | `litellm_params.default_on` | must be `true` (D4) |
  | `litellm_params.pii_entities_config` | a non-empty map from Presidio entity names to `MASK` or `BLOCK` |
  | `litellm_params.presidio_language` | `en` (others after G0) |
  | `litellm_params.presidio_score_thresholds` | per-entity floats `<= 0.8` (a threshold of 1.0 turns detection off) |

- Refused outright: `presidio_ad_hoc_recognizers` (a file path the gateway reads),
  `output_parse_pii`, `mock_redacted_text`, `presidio_filter_scope`, `api_base`, `api_key`,
  `callbacks`, and any `os.environ/` value.
- `_scalar` and `_env_strings` are extended to the new shape.
- Hashing: `config_review` reads the file once, parses those bytes and hashes the same bytes. Today
  it calls `path.read_bytes()` a second time after `load_file`; that is fixed in G1. `ConfigReview`
  gains `guardrails_sha256`, the sha256 of the canonical JSON of the `guardrails` list plus the
  logging settings in §4.6. An unrelated `model_list` edit does not change it.
- New semantic check, used when a requirement is set:
  `ConfigReview.guardrails_effective(required: bool)`. When the requirement is set it refuses
  (`GUARDRAILS_INEFFECTIVE`) a config that lacks either:
  - at least one `default_on` `pre_call` guardrail with a non-empty entity map;
  - the §4.6 logging settings.
- The existing callers (`newagent.py`, `guide_checks.py`, `services/plan.py`) call `config_review`
  with no requirement. They accept or refuse `guardrails:` by the same allow-list and are otherwise
  unchanged.

### 4.3 Request-side overrides

LiteLLM has ways for a caller to choose or switch off guardrails per request: body or `metadata`
`guardrails`, key and team metadata, and in some versions a disable flag. The agent controls its
own request body. `start.py` only `exec`s the proxy, so it cannot filter requests.

- G0 sends each known override (body, metadata, headers) to the pinned gateway with a default-on
  guardrail and records whether the guardrail still runs.
- `gateway_keys.mint` takes a free `metadata` dict from its callers. G1 makes `mint` itself refuse
  reserved metadata keys (`guardrails`, `permissions`, and any G0 finds) instead of relying on
  callers. A unit test pins it.
- If G0 finds any override that disables a default-on guardrail, G1 stops. A request filter in front
  of the gateway then needs its own spec (D4). Nothing is claimed until the filter is built and
  tested.

### 4.4 Requirement and gate

- Operator config: `environments.<env>.guardrails: {required: true, approved_sha256: <hex>}`.
  `approved_sha256` is the `guardrails_sha256` someone reviewed. D10 decides where a
  `services up`-generated config gets it from.
- `services up` reviews the content-addressed config copy that the litellm container mounts
  (`services/plan.py` already runs `config_review` on it). That copy is what the gateway runs, not
  the source file the operator edits.
  - It runs `guardrails_effective` on that copy. On failure it refuses to write the environment
    (`GUARDRAILS_INEFFECTIVE`).
  - On success it writes `guardrails.running_sha256` and `guardrails.effective: [presidio:pre_call,
    …]` into the environment it writes (`services/operator.py`).
  - The gate sees only operator-config fields, so it takes `GUARDRAILS_UNVERIFIED`,
    `GUARDRAILS_CHANGED` and the Cedar set from these recorded values. They are trusted as far as
    the operator config is: the residual is T3's.
  - A hand-edited operator config without `services up` has no `running_sha256`, so it always gets
    `GUARDRAILS_UNVERIFIED` (D10).
- Gate denies, none of them overridable in audit mode:
  - `GUARDRAILS_UNVERIFIED`: guardrails are required and no `running_sha256` is recorded.
  - `GUARDRAILS_CHANGED`: `running_sha256` differs from `approved_sha256`.
  - `GUARDRAILS_INEFFECTIVE`: raised by `services up` (above), not the gate.
- `reconcile` compares the stack's recorded param hash with the approved one (`DRIFT_GUARDRAILS`).
  It does not read the live gateway, which has no safe config read.
- Residual: a change made between deploys is reported, not prevented, until the next deploy or
  reconcile. This is change detection, not tamper-evidence.
- Policy: the Cedar `Agent` entity gains `guardrails` (Set of String, default empty), copied from
  the recorded `guardrails.effective` list. Each entry is `presidio:pre_call` or `presidio:post_call`; `logging_only`
  entries are never added. It is added in the same places as #1023's attributes:
  - both entity builders in `policy/cedar.py`, with a default in `_call_entities`;
  - `policy/remote.py`;
  - `cedar_schema.json`.
  It ships in the shared P0/G1a slice, so `SCHEMA_VERSION` changes once (#1023 D6). Every
  deployment's `revision_digest` changes with it at that slice, and held approvals are reissued.
- `DIRECT_NO_GUARDRAILS`: an environment with `guardrails.required` and a #1023 `direct` route
  fails config load.
- In a guardrails-required environment, operator `bindings` and manifest `secrets` may not use the
  names in `PROVIDER_KEYS` (`GEMINI_API_KEY`, …) or `LLM_PROVIDER_KEY` (`SECRET_RESERVED` extension;
  today it covers only `LLM_GATEWAY_KEY`, `deployment.py` ~569). This is a naming check. It cannot
  stop a provider key mounted under another name, and the docs say so.

### 4.5 Failure behaviour

- **BLOCK on input or output:** the gateway returns an error. G0 records its exact shape and the
  exception ADK's `LiteLlm`/`LiteLLMClient` raises from it.
  - The runtime maps that exception in `Runtime.chat_async`'s existing `ModelError` path to
    `MODEL_INPUT_BLOCKED` or `MODEL_OUTPUT_BLOCKED`.
  - `/chat` (`server.py`) answers HTTP 422 with the code and no content (D8). Today `ModelError`
    carries `str(failure)[:300]` (`agent.py` ~349), and a gateway block error may quote the
    matched text. The blocked path must carry a fixed message, and a test asserts the seeded PII
    is not in the reply.
  - The turn is recorded as failed in `turns`, with a `guardrail_blocked` event. There is no retry.
  - A `post_call` block on a response that asked for tool calls stops before those calls run.
    Earlier steps' tool side effects stand, and the docs say so.
- **MASK:** the provider receives (`pre_call`) or the agent receives (`post_call`) the entity tag in
  place of the text. The turn continues. An event records the entity types and counts.
- **Fail closed** in a guardrails-required environment (D5). Each of these must end the call with
  `GUARDRAIL_UNAVAILABLE` and send nothing to the provider:
  - Presidio stopped, timing out or returning 5xx;
  - an oversized body;
  - an unsupported language;
  - an anonymizer failure on `MASK`.
  G0 verifies whether the pinned hook swallows these errors. If it does, G1 stops, as in §4.3.
- **Fallbacks:** guardrails are `default_on`, not per model, so a `fallbacks` target alias is
  checked the same way. A test drives a fallback.
- **Streaming:** the gateway accepts `stream: true` from any caller with the key. `post_call` is not
  claimed for streamed responses, and a test records what happens. No runtime streams today (D6).

### 4.6 What is logged

- **Events and spans:** guardrail name, mode, action (`masked` or `blocked`), entity types and
  counts, model alias, step. **Never** the matched text, the original message or the masked
  message.
- **Gateway settings required by `guardrails_effective`:**
  - `litellm_settings.turn_off_message_logging: true`;
  - `litellm_settings.set_verbose: false`;
  - `general_settings.store_prompts_in_spend_logs: false`;
  - `general_settings.disable_error_logs: true` (the error-log table stores request payloads).
  Northwind sets the first and third today (`examples/northwind/stack/litellm/config.yaml`).
- **LiteLLM and Presidio exception logs:** G0 checks whether either prints the analysed text on a
  BLOCK or an error. If one does, G1 sets a log level that does not, or the claim in §5 drops "logs".
- **Audit chain:** one `guardrails` field on the deploy row (`approved_sha256`, the effective set).
  Nothing per call.

## 5. What can be claimed

The claim applies only after G1, a recorded real-Docker run, and G0's route list. It is worded to
the covered routes:

> "Gateway guardrails (Presidio) mask or block configured PII types in chat-completion requests
> and responses that an agent sends through the stack's LiteLLM gateway with its deployment key,
> before the provider sees them. They do not cover [routes G0 found uncovered, e.g. embeddings],
> calls that bypass the gateway, or what the agent itself stores."

Explicitly not claimed:

- That PII is not stored. The agent records the unmasked prompt before the gateway sees it, in its
  spans (`telemetry`, Phoenix), ADK sessions, `turns` and events.
- Embeddings and knowledge ingest, unless G0 shows the hook covers `/embeddings`. RAG passages and
  ingested documents go there.
- Detection quality. Presidio is pattern- and NER-based, and misses and false positives are
  expected.
- Prompt-injection or jailbreak protection.
- Enforcement inside the agent process.
- Anything about tool-call content.
- Tamper-evidence for the config hash.

The website's `what-ships` changes only at release R.

## 6. Security

| # | Threat | Mitigation | Residual |
|---|---|---|---|
| T1 | The agent bypasses the gateway with another key | `DIRECT_NO_GUARDRAILS`; reserved provider-key names (§4.4) | A provider key under another name, or obtained elsewhere, with open egress. A naming check is not detection |
| T2 | The caller disables a default-on guardrail by request | §4.3: G0 negative tests; G1 blocked until none works | — |
| T3 | The operator changes the gateway config after approval | `GUARDRAILS_CHANGED` at deploy, `DRIFT_GUARDRAILS` in reconcile, both against the mounted copy's hash | Reported, not prevented, between deploys |
| T4 | PII ends up in gateway logs | §4.6 required settings; event fields are types and counts only; seeded-PII grep | Agent-side stores hold PII (§5, not claimed); provider-side logs are the provider's |
| T5 | Presidio used as an unauthenticated oracle or exfiltration hop | `internal` port on the stack network only, `egress: false`, agent-cannot-connect test | — |
| T6 | A guardrail failure passes silently | Fail closed on each §4.5 case, verified in G0 | Availability cost |
| T7 | A vacuous config looks "required" (`logging_only` only, empty entities, threshold 1.0) | `guardrails_effective`; Cedar set built from effective checks | — |
| T8 | An uncovered route (`/embeddings`, `/v1/responses`, passthrough) carries PII | G0 route list, a seeded-PII test per route, the claim names covered routes | Uncovered routes stay uncovered unless a later slice restricts the key's routes |
| T9 | TOCTOU between review and the running config | One read, hash of the mounted copy (§4.2, §4.4) | — |

## 7. Interaction with #1023

Gateway guardrails cannot see direct calls. An environment is either "gateway, with optional
required guardrails" or "direct, with no guardrail claim". The runtime annotation slice (G2) takes
the next free `runtime/adk` version (#1023 §4.6). The gateway guardrails (G1) need no runtime
change. But on `runtime/adk@1.2.0` a block's error text is copied into `ModelError`
(`agent.py` ~349), and from there into the `/chat` reply, `turns` and events, and it may quote the
matched PII. So a guardrails-required environment refuses a runtime that does not declare
`guardrails` in its manifest (`RUNTIME_GUARDRAILS_UNSUPPORTED`, checked in `bind()` like #1023's
`RUNTIME_ROUTE_UNSUPPORTED`). The template also records the runtime's declared capabilities
(`runtime_capabilities`, left out when unset), so the gate re-checks a hand-assembled or promoted
manifest. G2 ships that runtime. G1b alone can be run and tested, but not
claimed, until G2 lands.

## 8. Change list and cross-repo impact

- **OSS:**
  - `gateway_keys.config_review`: allow-list, single read, `guardrails_sha256`,
    `guardrails_effective`.
  - `operator_config.py`: `guardrails` (`required`, `approved_sha256`, `running_sha256`,
    `effective`).
  - `services/operator.py` and `services/plan.py`: `guardrails_effective`, `running_sha256`,
    `effective`.
  - `gate.py`: the denies.
  - `deployment.py`: `SECRET_RESERVED` extension.
  - `policy/cedar.py` (both builders), `policy/remote.py`, `cedar_schema.json`.
  - `service/presidio@1.0.0`, `service/litellm@1.1.0`.
  - `reconcile`.
  - The runtime annotation slice.
  - Docs: `services.md`, `identity-gate.md`, `traces.md`, `agent-yaml.md`.
- **Cloud:** no reference today. A `CLAUDE.md` note goes in when the operator config gains
  `guardrails`.
- **Website:** nothing until release R and a verified run.

## 9. Acceptance criteria

1. **Masking.** A stack with `service/presidio` and a `pre_call` MASK guardrail is run against a
   scripted fake provider behind LiteLLM, then once against real Gemini. A message with a seeded
   email and phone number reaches the provider masked, and the turn succeeds.
2. **Blocking.** A BLOCK entity on input ends the turn with `MODEL_INPUT_BLOCKED` (HTTP 422). On
   output it ends with `MODEL_OUTPUT_BLOCKED`. Both are recorded as events with types and counts
   only.
3. **Fail closed.** Each case below produces `GUARDRAIL_UNAVAILABLE`, and the fake provider
   receives nothing:
   - Presidio stopped;
   - Presidio timing out;
   - Presidio returning 5xx;
   - an oversized body;
   - an unsupported language.
4. **Config review.** `config_review` refuses each non-allow-listed or path-valued field, with one
   unit test each. `guardrails_effective` refuses a `logging_only`-only config, an empty entity
   map, a threshold above the cap, and missing §4.6 settings.
5. **Overrides.** Each request-side override from G0 still gets checked (real gateway test).
6. **Config changes.** Editing the guardrails section produces `GUARDRAILS_CHANGED` at deploy and
   `DRIFT_GUARDRAILS` in reconcile. An unrelated `model_list` edit produces neither.
7. **Route coverage.** Seeded PII is posted to each gateway route the key can reach, and the
   provider-side payload is recorded. The docs' route list matches.
8. **No PII in gateway records.** A grep over gateway logs, spend rows, the error-log table, events
   and the audit log finds none of the seeded PII. Agent spans and sessions are listed as holding
   it, not claimed clean.
9. **Network isolation.** An agent container cannot connect to Presidio.
10. **Docs.** The docs carry the §5 claim and its limits, and nothing else.

## 10. Owner decisions

- **D1** Use gateway guardrails (G-A) as the check, and a deploy-time requirement bound to the
  mounted config's hash. No per-call Cedar `call_model` action in this epic. *Rec: yes.*
- **D2** Presidio only in the first slice. *Rec: yes.*
- **D3** Allow `pre_call` and `post_call`. Allow `logging_only` but never count it toward a
  requirement. Refuse `during_call`, because it runs in parallel and does not stop input reaching
  the provider. *Rec: yes.*
- **D4** Require `default_on: true`. If G0 finds a request-side switch-off, stop G1 and spec a
  request filter rather than ship. *Rec: yes.*
- **D5** Fail closed on every §4.5 case in a guardrails-required environment, with no fail-open
  switch. *Rec: yes.*
- **D6** Make no streaming claim for `post_call`. A runtime that streams is refused in a
  guardrails-required environment until streaming is verified. *Rec: yes.*
- **D7** The runtime annotates only (G-B detection not built now). *Rec: yes.*
- **D8** A block answers `/chat` with HTTP 422 and a coded body, and the turn is recorded as
  failed. *Rec: yes.*
- **D9** Use Microsoft's published Presidio images from `mcr.microsoft.com`, pinned by digest, with
  arm64 and amd64 checked and the two-week rule applied. *Rec: yes.*
- **D10** Where `approved_sha256` comes from for a `services up`-generated environment. Either:
  - (a) the stack's `stack.yaml` environment, which `services up` copies after printing the
    guardrails for review, as with MCP pins; or
  - (b) only from a hand-edited operator config. Under (b) `running_sha256` must still come from
    `services up`, so (b) only moves where the approval is written.
  *Rec: (a), with `--expect-guardrails SHA256` for non-interactive runs, mirroring
  `--expect-pins` (#964).*

## 11. Slices (after D1–D10)

- **G0:** verification on the pinned images, with no product code. Write the results into this spec
  as r3:
  - the guardrail fields;
  - the request-side overrides;
  - the routes the hook covers;
  - the block and mask response shapes and the ADK exception they raise;
  - fail-closed behaviour;
  - Presidio and LiteLLM log levels;
  - Presidio image architectures.
- **G1a (shared with #1023 P0):** the operator `guardrails` field, Cedar attributes and
  `SECRET_RESERVED` names.
- **G1a as built (2026-11-15).** The owner dropped #1023 (no direct route to vendors; LiteLLM or
  OpenRouter only), so `DIRECT_NO_GUARDRAILS` and the `LLM_PROVIDER_KEY` reserved name are gone.
  The environment field is the **floor**, `guardrails: {required, approved_sha256}` (bare hex).
  `running_sha256` and `effective` arrive in G1b together with `services up`, which writes them, so
  nobody can record them by hand first. Until G1b a required floor denies every deploy
  (`GUARDRAILS_UNVERIFIED`, never overridable). The Cedar `guardrails` set exists but is always
  empty. `SECRET_RESERVED` covers the `PROVIDER_KEYS` names in `bindings` (config load) and
  manifest `secrets` (gate). Planned later (owner, 2026-11-15): agent-level guardrails declared in
  `agent.yaml` that may only add to the floor, never remove or weaken it, and that map to the
  deployment key's mint. The manifest gets an agent `guardrails:` list in that slice, not before.
- **G1b:**
  - `service/presidio@1.0.0` and `service/litellm@1.1.0`;
  - the `config_review` allow-list, single read and `guardrails_effective`;
  - `running_sha256` and the gate denies;
  - a real-Docker test with a scripted provider.
- **G2:** `RUNTIME_GUARDRAILS_UNSUPPORTED`; runtime events and spans in the next free runtime version, coded block replies, reconcile
  drift, docs, and a recorded run against real Gemini.

## 12. Review log

| Round | Reviewer | Lens | Result |
|---|---|---|---|
| r1 | Claude Sonnet (read-only subagent) | /architect | CHANGES REQUESTED: 1 high (config sha could not be bound at deploy time), plus cross-spec highs (runtime version, Cedar builders), 5 medium, 4 low. Fixed in r2: §4.2 single read and section hash, §4.4 mounted-copy hash written by `services up`, D10, §4.5 error path, §7 runtime split, §8 |
| r1 | Claude Sonnet (read-only subagent) | /security | CHANGES REQUESTED: 3 high (route coverage overstated, `start.py` cannot strip request fields, vacuous configs pass the allow-list), 5 medium, 3 low. One claim refuted: `expose: gateway` is not a scope, but `internal` already keeps agents off Presidio (`services/plan.py` `joins`). Fixed in r2: §3, §4.2, §4.3, §4.5 fail-closed cases, §4.6 logging settings, §5 claim narrowed, §6 T7–T9 |
| r2 | Claude Sonnet (read-only subagent) | /architect + /security | CHANGES REQUESTED: r1 fixes confirmed; 2 medium here (the gate cannot compute effectiveness from a hash; `mint` takes free metadata and 1.2.0 copies block text into replies). Fixed in r3: `services up` records `effective`, `mint` refuses reserved keys, `RUNTIME_GUARDRAILS_UNSUPPORTED`, D10(b) wording |
| r3 | Claude Sonnet (read-only subagent) | /architect + /security | APPROVED. One low gap (no gate re-check of the runtime's guardrails capability) closed in §7 |
