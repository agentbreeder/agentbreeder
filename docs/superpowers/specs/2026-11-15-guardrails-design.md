# Guardrails on model input and output (#1024)

Status: spec r4 (2026-11-15). The owner accepted the recommended D1–D10 (#1024 comment, 2026-11-15 17:32), with agent-level
guardrails additive only (#1053). r4 adds the G0
results (§12) and the changes they force (§12.10). D8's amendment and the agent-level design in §12.8
need the owner. Nothing here is implemented.
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
  digest from `mcr.microsoft.com` (D9). G0 checked both platforms' manifests and ran amd64 only (§12.1).
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
  arm64 and amd64 checked and the two-week rule applied. *Rec: yes.* (G0: both manifests present,
  amd64 run only; an arm64 run is a G1b item, §12.9.)
- **D10** Where `approved_sha256` comes from for a `services up`-generated environment. Either:
  - (a) the stack's `stack.yaml` environment, which `services up` copies after printing the
    guardrails for review, as with MCP pins; or
  - (b) only from a hand-edited operator config. Under (b) `running_sha256` must still come from
    `services up`, so (b) only moves where the approval is written.
  *Rec: (a), with `--expect-guardrails SHA256` for non-interactive runs, mirroring
  `--expect-pins` (#964).*

## 11. Slices (after D1–D10)

- **G0:** verification on the pinned images, with no product code. Write the results into this spec
  as r3 (written as r4, §12):
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
- **G1b as built (2026-11-15).** A `service.yaml` holds one image, so Presidio ships as two
  built-ins, `service/presidio-analyzer@1.0.0` and `service/presidio-anonymizer@1.0.0`, both
  internal-only with no egress. `service/litellm@1.1.0` adds two optional url inputs feeding
  `PRESIDIO_*_API_BASE`. D10 is (a) without the flag: `stack.yaml` `environment.guardrails:
  {required, approved_sha256}`, the grant shows the guardrails sha256 and entities (only when the
  config has guardrails, so other stacks keep their grant digest), and `services up` writes
  `running_sha256` and `effective` into the operator floor. `--expect-guardrails` is a follow-up.
  `GUARDRAILS_INEFFECTIVE` at `up` also covers a project-built gateway, `LITELLM_LOG` below
  `WARNING` and unwired Presidio inputs; a config with `guardrails:` and no Presidio is refused even
  when nothing is required. The gate denies `GUARDRAILS_UNVERIFIED` (no running hash, or no
  effective `pre_call`) and `GUARDRAILS_CHANGED`; the Cedar set is `effective` when the hashes
  match, for every action. `mint` builds its body from field and metadata allow-lists for every key,
  and adds `allowed_routes` (chat completions and embeddings) under a required floor, for deploy
  and eval judge keys. `DRIFT_GUARDRAILS` in `reconcile` moves to G2 with the runtime.
- **G1b real-Docker run (2026-11-15).** `tests/test_guardrails_docker.py` passed (1 test, 7 min 12 s,
  from an image cache holding only alpine) on macOS 13 (Intel), Docker Engine 27.5.1 (Docker
  Desktop), linux/amd64, through `services up`
  with the built-in Presidio and `service/litellm@1.1.0` refs and a scripted provider. All ten steps
  held: input masking, an input block that the provider never sees, an output check, embeddings,
  request overrides still checked, key routes, an agent container unable to reach Presidio by name
  or IP, no seeded PII in the gateway or Presidio logs, fail closed with the analyzer stopped, and
  `GUARDRAILS_CHANGED` after a guardrails edit. With a key limited to chat and embeddings, these
  routes returned 403: `/openai/v1/chat/completions`, `/openai_passthrough/v1/chat/completions`,
  `/v1/moderations`, `/v1/responses`, `/v1/messages`, `/v1/completions`, `/key/generate` and
  `/key/update`; `/v1/chat/completions/extra` returned 404. Not run on arm64.
- **G2:** `RUNTIME_GUARDRAILS_UNSUPPORTED`; runtime events and spans in the next free runtime version, coded block replies, reconcile
  drift, docs, and a recorded run against real Gemini.

## 12. G0 results (r4, 2026-11-15)

Run on macOS 13 (Intel, Docker Desktop 27.5.1, linux/amd64) in a throwaway sandbox stack: one
Docker network holding the pinned LiteLLM image with the image's own `litellm` entrypoint, its own
Postgres, both Presidio images, a scripted OpenAI-compatible fake provider that logged every
request body it received, and a fault proxy in front of each Presidio service. The keys were LiteLLM
virtual keys minted the way `gateway_keys.mint` mints them (`/key/generate` with `key_alias`,
`models`, `metadata`). The PII was synthetic (`jane.doe@example.com`, `212-555-0147`, SSN
`536-22-8174`, test card `4111 1111 1111 1111`, IP `203.0.113.7`). Real Gemini
(`gemini/gemini-2.5-flash`) was called for one mask turn and one block turn. No product code was
changed. After the run Docker matched the pre-run snapshot (images, containers, networks, volumes),
and the key files were deleted. The raw outputs stayed in the session scratchpad and were not kept.

"Provider saw" below means what the fake provider logged.

### 12.1 Images and the hook

| Image | Pin | Platforms | Built |
|---|---|---|---|
| LiteLLM 1.106.2 | `ghcr.io/berriai/litellm-database@sha256:35f2cec3…5d91e79` (unchanged, `service/litellm@1.0.0`) | amd64 here; arm64 not rechecked in G0 | not checked in G0 |
| Presidio analyzer 2.2.362 | `mcr.microsoft.com/presidio-analyzer@sha256:286e3fa7f3a7426e775e8564fe1870f1ba8f999d3ab8bbb8cc46a44355d9d6e9` (multi-arch index) | linux/amd64 `d459416c…`, linux/arm64 `51c773df…` | 2026-03-15 |
| Presidio anonymizer 2.2.362 | `mcr.microsoft.com/presidio-anonymizer@sha256:a10a12a2a613d13cf29d3ad3641e3258444dd8c90403dd644a0a114c472c2483` (multi-arch index) | linux/amd64 `652493f6…`, linux/arm64 `29bf3829…` | 2026-03-15 |

- 2.2.362 is the newest tag. It is eight months old, so it passes the two-week rule (D9). Only the
  amd64 images were run.
- The analyzer is 965 MB and the anonymizer 231 MB. The anonymizer was healthy in under 10 s. The
  analyzer was ready before LiteLLM, which took 35–80 s to start.
- The pinned LiteLLM image's Presidio hook (`proxy/guardrails/guardrail_hooks/presidio.py`) works
  with no extra package. It needs only `PRESIDIO_ANALYZER_API_BASE` and
  `PRESIDIO_ANONYMIZER_API_BASE` in the environment, so `files/start.py` does not change (§4.1):
  `service/litellm@1.1.0` sets the two variables in `env`.

### 12.2 Guardrail fields the pinned image honours

From `PresidioConfigModel` and `CustomGuardrail.__init__` in the image, plus the live runs:

| Field | Seen | Allow-list (r4) |
|---|---|---|
| `guardrail`, `mode`, `default_on`, `pii_entities_config`, `presidio_language`, `presidio_score_thresholds` | yes, as §4.2 | as §4.2 |
| `presidio_entities_deny_list` | in the model | refuse |
| `presidio_analyze_chunk_size_bytes` | default 500 000; larger texts are chunked (§12.5) | refuse (keep the default) |
| `stream_scope` | default `both` (streaming and not) | refuse (a value of `non_streaming` would skip streamed calls) |
| `timeout` | a `CustomGuardrail` argument; with none set, a stalled analyzer failed after 300 s | allow one number, 1–60 (not run with a value) |
| `apply_to_output`, `output_parse_pii`, `logging_only`, `mock_testing`, `mock_redacted_text`, `presidio_ad_hoc_recognizers`, `api_base`, `api_key` | in the model | refuse (as §4.2, plus `apply_to_output` and `mock_testing`) |

- `presidio_language` other than `en` fails every call. With `xx` the analyzer answered HTTP 500 "No
  matching recognizers were found to serve the request", and the gateway refused every request with
  400, even a request with no PII. So `en` only (§4.2) stands.
- **`mode` is not a direction.** A `pre_call` guardrail also checked the model's response. A
  response with a test card number was blocked by `pii-block`, a `pre_call` guardrail that was the
  only one listing `CREDIT_CARD`. A `post_call` SSN BLOCK guardrail was also configured, but the
  error named `pii-block`. The `post_call` MASK guardrail masked responses. So on this image a
  `pre_call` guardrail also checks responses. The config had `apply_to_output` and
  `output_parse_pii` unset. The hook's own `async_post_call_success_hook` checks output only when one
  of them is set, so another path does the output check, probably the unified `apply_guardrail` path
  (`presidio.py`, `input_type="response"`). That was not traced. It is observed behaviour with an
  unknown mechanism, and G1 pins it with a test on this digest. The refusal of `apply_to_output` and
  `output_parse_pii` (§4.2) must not remove output coverage, and that test checks it.
- §4.5's split of MASK between `pre_call` (the provider receives tags) and `post_call` (the agent
  receives tags) therefore does not hold on this image. A `pre_call` MASK guardrail did both.
- The guardrails ran one after another, in config order, on one request (the analyzer saw the
  entity lists in that order). A MASK guardrail rewrites the text before later guardrails see it,
  so an entity that one guardrail masks cannot be blocked by a later one. This is inferred from the
  order and not run.

### 12.3 Request-side overrides (D4): none switched a guardrail off

With `pii-mask` (MASK email, phone) and `pii-block` (BLOCK SSN, card) both `default_on` `pre_call`,
a message with an SSN and an email was sent to `/v1/chat/completions` with each override below.
The override set was not repeated on other routes, and no `post_call`-only guardrail was put through
it (§12.9). **Every one was still blocked
with 400, and the provider received nothing:**

- body `guardrails: []`, `guardrails: ["pii-mask"]`, and
  `guardrails: [{"pii-block": {"extra_body": {...}}}]` (the source drops `extra_body` without an
  Enterprise licence, `CustomGuardrail.get_guardrail_dynamic_request_body_params`);
- `metadata.guardrails: []` and `litellm_metadata.guardrails: []`;
- `disable_global_guardrails` and `disable_global_guardrail`, in the body, `metadata` and
  `litellm_metadata`;
- a forged `metadata.user_api_key_metadata` or `user_api_key_team_metadata` carrying
  `disable_global_guardrails` or `opted_out_global_guardrails`;
- `opted_out_global_guardrails`, in the body and in `metadata`;
- `guardrail_config.language` (`xx`, `es`), in the body and in `metadata`. It never reached
  Presidio: the analyzer log shows only `en`;
- `policies: []`, and a config-defined policy whose `guardrails.remove` lists both guardrails,
  requested in the body or in `metadata`;
- `litellm_disabled_callbacks`, `callbacks: []`, `mock_response`, `metadata.applied_guardrails`,
  `no-log`, and a nested `extra_body`;
- headers `x-litellm-disable-guardrails`, `x-litellm-guardrails` and `x-litellm-tags`;
- `stream: true`;
- the PII in a list-form `content` text part, a `system` message, a `tool` message, or after a
  `name` field.

So the D4 stop rule did not trigger for these requests on `/v1/chat/completions`, and G1 may go
ahead. The route gaps in §12.4 are coverage gaps, not overrides: the caller switches nothing off and
uses another URL or field. `allowed_routes` closes them, so it is mandatory and tested (§12.10). The image strips these fields itself
(`_UNTRUSTED_ROOT_CONTROL_FIELDS`, `_UNTRUSTED_METADATA_CONTROL_FIELDS` in
`proxy/litellm_pre_call_utils.py`).

**What does switch the global guardrails off is key metadata set when the key is minted.** A key
minted with `metadata: {disable_global_guardrails: true}` sent the SSN to the provider, and the
response named no guardrail at all. A key with `metadata: {opted_out_global_guardrails:
["pii-block"]}` sent the SSN with only `pii-mask` applied. Only the minting side (`gateway_keys.mint`,
using the master key) can set these. §4.3's reserved-key list becomes an **allow-list**, because a
deny-list goes stale with each LiteLLM upgrade:
- `mint` sends only `key_alias`, `models`, `max_budget`, `budget_duration`, `rpm_limit`,
  `duration`, `allowed_routes` and `metadata`;
- `metadata` holds only AgentBreeder's own keys.

Everything else is refused, including the top-level `guardrails`, `policies`, `permissions`,
`disable_global_guardrails`, `team_id`, `config`, `object_permission`, `tags` and `agent_id` fields,
and the metadata keys `disable_global_guardrails`, `opted_out_global_guardrails`, `guardrails`,
`policies`, `permissions`, `litellm_disabled_callbacks`, `logging` and `tags`. The source lists these
in `GenerateRequestBase`/`KeyRequestBase`. Team metadata carries the same switches, so keys never get
a `team_id`.

A request can **add** a guardrail that is not `default_on` by naming it (`guardrails:
["key-ip-block"]` blocked an IP for a key that does not otherwise run it). That only adds checks.

### 12.4 Routes and content the hook covers

Each route was sent a BLOCK message (SSN) and a MASK message (email, phone) with an agent key
(models limited to the sandbox aliases, no `allowed_routes`):

| Route | BLOCK | MASK, provider saw | Response masked (REPLY with PII) |
|---|---|---|---|
| `/chat/completions`, `/v1/chat/completions` | 400 | tags | yes |
| `/v1/chat/completions` with `stream: true` | 400 | tags | yes, in the stream chunks |
| `/v1/embeddings` | 400 | tags | — |
| `/v1/completions` (text) | 400 | tags | yes |
| `/v1/responses` | 400 | tags | yes |
| `/v1/messages` (Anthropic shape; sent on as `/v1/responses`) | 400 | tags | yes |
| `/gemini/...` passthrough naming an alias | 400 (routed through the router) | not run | — |
| **`/v1/moderations`** | **not checked: provider saw the SSN** | **provider saw the email** | — |
| **`/openai/*`, `/openai_passthrough/*`** (with `OPENAI_API_KEY` set) | 400 (the guardrail runs) | **not masked: its rewrite is not applied to the forwarded body; provider saw the raw email** (chat and embeddings), sent with the gateway's own OpenAI key | — |
| `/anthropic/*`, `/vertex_ai/*` | refused (model not on the key, or no Vertex credential) | — | — |
| `/utils/token_counter` | not checked; no provider call | — | — |

Inside a chat request, the hook reads only the `text` of `messages[].content`. **Not checked:** an
assistant message's `tool_calls[].function.arguments` (the provider saw an SSN and an email),
`tools[].function.description` (provider saw both), an `image_url` URL (provider saw the SSN), and
the `user` field. `user` was not forwarded to the provider, but LiteLLM stored it as an end-user ID in
`LiteLLM_EndUserTable` and `LiteLLM_DailyEndUserSpend`.

**The key's routes can be limited on the OSS image.** A key minted with `allowed_routes:
["/v1/chat/completions", "/chat/completions", "/v1/embeddings", "/embeddings"]` worked on chat and
embeddings and got 403 ("Virtual key is not allowed to call this route") on `/openai/*`,
`/v1/moderations`, `/v1/responses` and `/key/generate`. ADK's `LiteLlm` client worked with such a key
(§12.6). `/v1/messages` was not sent with this key. It is not on the list, so a 403 is expected, but
that was not run. Only these four 403s were seen, so G1 asserts the 403 across the OpenAI-compatible,
passthrough and management routes, and checks whether the match is exact or by prefix. The
`/openai/*` gap needs `OPENAI_API_KEY` in the litellm environment. `service/litellm`'s launcher puts
an imported OpenAI key there (inferred from `files/start.py`, not run in G0). So the route limit
cannot be left out. G1 therefore mints every key in a guardrails-required environment with `allowed_routes`, and
the claim (§5) names chat completions and embeddings. Embedding calls that go through the gateway
are covered. Whether knowledge ingest and RAG embeddings use this route is checked in G1, not here.
A test asserts the 403 on each excluded route.

### 12.5 Failure behaviour (D5): every case failed closed

The provider received nothing in any case below. "Clean" is a message with no PII.

| Case | Client gets | Message (as returned) |
|---|---|---|
| analyzer HTTP 500, 413, non-JSON body, or `{"error":…}` (clean or MASK message) | 400 | "Presidio analyzer returned invalid response; cannot verify PII when PII protection is configured: …" plus up to 200 characters of Presidio's body |
| analyzer stopped (unreachable) | 500 | "Presidio PII analysis failed: ClientConnectorDNSError" |
| analyzer stalls (no response) | 500 after **300 s** | "Presidio PII analysis failed: TimeoutError" |
| anonymizer HTTP 500, 413, non-JSON, error JSON, or stopped, on a MASK hit | 500 | "Presidio anonymizer returned HTTP 500: …", "Presidio PII anonymization failed: …" |
| same anonymizer faults, clean message | 200 | the anonymizer is not called when nothing matched |
| config `presidio_language: xx` | 400 on every call | analyzer 500 "No matching recognizers…" |
| oversized: 594 kB with an SSN at the end | 400 block after 44 s | chunked analysis found it |
| oversized: 2.16 MB with an email at the end | 200 after 127 s; provider saw the tag | chunked analysis masked it |

- G1 sets `timeout` (§12.2), because 300 s is longer than the runtime's 90 s model timeout.
- Large bodies are analysed, not refused. They are slow (about 1 min per MB here). There was no
  size cap before Presidio. LiteLLM's own body limit was not tested.
- The Presidio error bodies that came back held no input text in these runs, but the gateway copies
  up to 200 characters of them into its error. The runtime's fixed message (§4.5) stays.

### 12.6 Response shapes and the ADK exception

- **Block, on input or output:** HTTP 400, `{"error": {"message": "Blocked entity detected:
  US_SSN by Guardrail: pii-block. This entity is not allowed to be used in this request.", "type":
  "invalid_request_error", "code": "400"}}` (`/v1/messages` uses Anthropic's error shape). The
  message holds the entity type and the guardrail name, not the matched text. An output block is the
  same message as an input block, and it named the `pre_call` guardrail (§12.2).
- **Mask:** HTTP 200. The provider receives `<EMAIL_ADDRESS>`, `<PHONE_NUMBER>`, and the client sees
  the same tags in a masked response.
- In a container installed from `runtime/adk@1.2.0`'s `requirements.lock` (google-adk 2.12.0,
  litellm 1.108.3), an `LlmAgent` with `LiteLlm(model="openai/<alias>", api_base=<gateway>)` raised:

  | Case | Exception | `status_code` |
  |---|---|---|
  | block on input, block on output (fake provider and real Gemini) | `litellm.exceptions.BadRequestError` ("litellm.BadRequestError: OpenAIException - Blocked entity detected: US_SSN by Guardrail: pii-block. …") | 400 |
  | analyzer HTTP 500 | `litellm.exceptions.BadRequestError` ("… Presidio analyzer returned invalid response …") | 400 |
  | analyzer unreachable | `litellm.exceptions.InternalServerError` ("… Presidio PII analysis failed: ClientConnectorDNSError") | 500 |

  No exception message held seeded PII. A mask turn replied normally, and real Gemini repeated the
  message back as "my email is <EMAIL_ADDRESS> and my phone is <PHONE_NUMBER>."
- So a block and a fail-closed error both arrive as the same exception class with status 400. The
  runtime can tell them apart only by the message prefix ("Blocked entity detected" against
  "Presidio …"). G2 pins both prefixes in a test against the pinned image, and maps an unknown 400 to
  a generic model error, never to a block.

### 12.7 Logs and stored records

- At `LITELLM_LOG=WARNING` (the value `service/litellm@1.0.0` sets), with
  `turn_off_message_logging: true`, `set_verbose: false`, `store_prompts_in_spend_logs: false` and
  `disable_error_logs: true`: no seeded PII in the LiteLLM container log, the Presidio analyzer or
  anonymizer logs, `LiteLLM_SpendLogs` (its guardrail fields hold entity types, offsets and
  scores), `LiteLLM_ErrorLogs` (0 rows) or `LiteLLM_AuditLog` (0 rows).
- **At `LITELLM_LOG=DEBUG` the LiteLLM log held the seeded email, phone and SSN** although the four
  settings above were on. `guardrails_effective` therefore also requires the litellm service's
  `LITELLM_LOG` to be `WARNING` or higher. It is a service env value, not a config field, so G1
  checks it in the service plan.
- A full dump of the LiteLLM database found the seeded PII only in `LiteLLM_EndUserTable` and
  `LiteLLM_DailyEndUserSpend`, from the `user` field (§12.4). Acceptance criterion 8 adds those
  tables to its grep, and G1 checks whether the runtime sends `user`.

### 12.8 Hierarchical guardrails (owner request, 2026-11-15; #1053)

The question: an environment-level floor plus agent-level guardrails from `agent.yaml`.

- **Per-key guardrails need LiteLLM Enterprise.** `/key/generate` with a top-level `guardrails`
  field returned 403 ("only available for LiteLLM Enterprise users: guardrails"). A key minted with
  `metadata: {guardrails: [...]}` was created, but **every call with it returned 403** (the same
  Enterprise message), and no guardrail ran.
- **The policy engine works on the OSS image.** In the gateway config,
  `policies: {agent-a-extra: {guardrails: {add: [key-ip-block]}}}` with
  `policy_attachments: [{policy: agent-a-extra, keys: [g0-agent-a]}]` ran `key-ip-block` (a
  non-`default_on` BLOCK on IP addresses) for the key aliased `g0-agent-a` only. Another key's IP
  went to the provider.
- **A request could not remove the attached guardrail.** Still blocked: body `guardrails: []`,
  `metadata.guardrails: []`, body or `metadata` `policies: ["weaken"]` (a policy whose `remove`
  lists it), `opted_out_global_guardrails`, `disable_global_guardrails`, tags in the body and in
  `x-litellm-tags`, and a forged `metadata.user_api_key_alias`. It also ran on `/v1/embeddings` and
  on streamed calls.
- **Both levels run, globals first.** The analyzer saw the `default_on` guardrails in config order,
  then the attached one. The first block stopped the call: a message with both an IP and an SSN was
  refused by the global `pii-block`.
- **Matching.** An attachment matches on `key_alias` and `team_alias` from authentication, and on
  `model` and `tags`, which come from the request. The proposed shape uses neither `models` nor
  `tags`, so a request cannot match or unmatch an attachment. A request can name any registered
  policy in `policies`. With `add` only, that can only add checks. `matches_pattern` treats a
  trailing `*` as a prefix match, so `services up` refuses an alias containing `*`. A key with no
  alias matches only `*` patterns, which the shape does not allow.
- **What this means for the design** (needs the owner, not decided here): an agent's guardrails can
  be gateway config entries that are not `default_on`, attached to that deployment's key alias by a
  policy attachment that `services up` generates. `config_review` would then allow `policies` and
  `policy_attachments` in a narrow shape:
  - `guardrails.add` only (no `remove`, `inherit`, `condition` or `pipeline`);
  - attachments by exact key alias only (no `scope: "*"`, `teams`, `models`, `tags` or wildcard
    aliases).

  The environment floor stays the `default_on` set from §4. An agent can add checks but cannot take
  any away. The agent's set would then join `guardrails_sha256` and the Cedar `guardrails` attribute.
  This needs its own spec delta before G1b.

### 12.9 Not run in G0

- An arm64 run of either Presidio image (only the manifests were checked).
- The override set (§12.3) on routes other than `/v1/chat/completions`, and on a `post_call`-only
  guardrail.
- Whether an agent key without `allowed_routes` can call `/key/update` or `/key/generate` to rewrite
  its own metadata. LiteLLM normally keeps those routes for admin keys, but this was not run. Only
  the 403 with `allowed_routes` was seen. Such a self-edit would be a D4-class switch-off.
  `allowed_routes` is what closes it, and G1 tests it.
- MASK through the `/gemini/...` passthrough, and `/v1/messages` with a route-limited key.
- The `timeout` field with a value.
- The real `service/litellm` launcher (`files/start.py`). The sandbox ran the image's `litellm`
  entrypoint with the same config path, user (65532) and environment names.
- A `post_call` block on a response that asks for tool calls.
- An agent container's reachability of Presidio (acceptance 9; G1b, using the stack's real
  networks).
- Fallbacks beyond one case: `fake-broken` → `fake-model` was blocked (SSN) before any call and
  masked (email) on the fallback.

### 12.10 Changes to the design from G0

- **§4.1:** Presidio 2.2.362 pins as in §12.1. `files/start.py` unchanged; `service/litellm@1.1.0`
  adds the two `PRESIDIO_*` env values. The LiteLLM digest is unchanged.
- **§4.2:** the allow-list rows in §12.2, `timeout` (1–60) added, and
  `presidio_entities_deny_list`, `presidio_analyze_chunk_size_bytes`, `stream_scope`,
  `apply_to_output` and `mock_testing` refused. `guardrails_effective` also requires
  `LITELLM_LOG` at `WARNING` or higher (§12.7). Within one config, an entity that a guardrail masks
  may not be blocked by a later one (§12.2).
- **§4.3:** D4 stays satisfied for the requests tested (§12.3). `mint` becomes an allow-list of
  fields and metadata keys (§12.3). In a guardrails-required environment it always sets
  `allowed_routes` to the chat-completions and embeddings routes (§12.4).
- **§4.5:** MASK direction follows §12.2, not the mode name.
- **§4.5 and D8 (amendment, needs the owner):** an input block and an output block cannot be told
  apart (§12.2, §12.6). Proposed: one code, `MODEL_BLOCKED` (HTTP 422), in place of
  `MODEL_INPUT_BLOCKED` and `MODEL_OUTPUT_BLOCKED`. Fail-closed errors map to `GUARDRAIL_UNAVAILABLE`
  by message prefix (§12.6).
- **§4.6 and acceptance 8:** add the `LITELLM_LOG` requirement and the end-user tables.
- **§5:** the routes are chat completions (streamed and not) and embeddings, with `allowed_routes`
  set. The claim also excludes tool-call arguments, tool definitions and image URLs inside a chat
  request (§12.4).
- **§6:** T2 is closed by §12.3. T8's residual narrows to the content not checked inside chat
  requests. A new T10, "a key minted with guardrail-disabling metadata", is mitigated by the `mint`
  refusal and a unit test per name.
- **§9:** acceptance 2 uses one block code. Acceptance 7 asserts the 403 on each excluded route.
  Acceptance 3 adds the anonymizer and the 300 s default timeout.

## 13. Review log

| Round | Reviewer | Lens | Result |
|---|---|---|---|
| r1 | Claude Sonnet (read-only subagent) | /architect | CHANGES REQUESTED: 1 high (config sha could not be bound at deploy time), plus cross-spec highs (runtime version, Cedar builders), 5 medium, 4 low. Fixed in r2: §4.2 single read and section hash, §4.4 mounted-copy hash written by `services up`, D10, §4.5 error path, §7 runtime split, §8 |
| r1 | Claude Sonnet (read-only subagent) | /security | CHANGES REQUESTED: 3 high (route coverage overstated, `start.py` cannot strip request fields, vacuous configs pass the allow-list), 5 medium, 3 low. One claim refuted: `expose: gateway` is not a scope, but `internal` already keeps agents off Presidio (`services/plan.py` `joins`). Fixed in r2: §3, §4.2, §4.3, §4.5 fail-closed cases, §4.6 logging settings, §5 claim narrowed, §6 T7–T9 |
| r2 | Claude Sonnet (read-only subagent) | /architect + /security | CHANGES REQUESTED: r1 fixes confirmed; 2 medium here (the gate cannot compute effectiveness from a hash; `mint` takes free metadata and 1.2.0 copies block text into replies). Fixed in r3: `services up` records `effective`, `mint` refuses reserved keys, `RUNTIME_GUARDRAILS_UNSUPPORTED`, D10(b) wording |
| r3 | Claude Sonnet (read-only subagent) | /architect + /security | APPROVED. One low gap (no gate re-check of the runtime's guardrails capability) closed in §7 |
| r4 | Claude Sonnet (read-only subagent) | /security + accuracy, on the G0 results | CHANGES REQUESTED: 4 medium (the override route was not named; key self-edit not recorded; the mint deny-list should be an allow-list; the output-check mechanism contradicted the source reading), 7 low (D9/§4.1 arm64 wording, §11 "r3", wildcard attachment aliases, `/openai/*` MASK note, and others). It agreed that the route gaps are coverage, not a D4 stop. All applied in r4 |
