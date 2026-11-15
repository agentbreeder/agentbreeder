# Direct provider endpoints without the gateway (#1023)

Status: spec r3 (2026-11-15), for owner decisions D1–D11 (§10). Nothing here is implemented.
Parent: #1017 (model layer epic), phase 3. Related: #1024 (guardrails; shares the policy-schema
change and the runtime version plan, §4.6, §7), #1025 (more LiteLLM providers, the gateway-side
alternative).

## 1. Problem

Every `runtime/adk` version reaches models only through the stack's LiteLLM gateway. The runtime
builds `LiteLlm(model="openai/<alias>", api_base=AGENTBREEDER_GATEWAY_URL, api_key=<virtual key>)`
(`runtime/adk/1.2.0/runtime/agent.py`, `Runtime.__init__`). Knowledge embeddings use a raw HTTP
client on the gateway's `/v1/embeddings` (`rag.py` `Embedder`). An operator who wants an agent to
call Gemini, OpenAI or Anthropic without running a gateway cannot. The gateway is assumed in many
places:

| Where | Assumption |
|---|---|
| `manifest.py` `ModelAccess.gateway` | required field |
| `build.py` `bind` (~316) and the check at ~378 | always writes `gateway`; `MODEL_ACCESS_UNCONFIGURED` when `env.gateway` is None |
| `gate.py` `_model_access` | checks `environments.<env>.gateway` |
| `deployment.py` `_minting`, `_gateway`, `_mint_key`, `_revoke_key`, orphan sweep (~606–680, ~1547, ~1624) | a key is minted and revoked per container generation |
| `deployment.py` (~825) | sets `AGENTBREEDER_GATEWAY_URL` |
| `evals.py` (~362) and `release.py` | judge and candidate keys are minted on the gateway |
| `llmcost.py`, `agentbreeder cost` | read the gateway spend log |
| `commands/knowledge.py`, `rag.py` | ingest and retrieval embed through the gateway |

The gateway gives five things a direct call does not:

| Gateway property | Where | Direct route |
|---|---|---|
| Key isolation: the provider key lives only in the LiteLLM container; the agent holds a per-deployment virtual key | `gateway_keys.py` `mint`, `service/litellm` `secrets:` | The provider key is in the agent container |
| Budget and rpm per deployment, enforced outside the agent | `mint(budget, rpm)`, `Ceilings` | Nothing outside the agent counts spend |
| Per-deployment revocation, orphan sweep in `reconcile` | `gateway_keys.revoke`, `aliases` | A shared provider key cannot be revoked per deployment |
| Spend log per call, read by `agentbreeder cost` | `spend_logs`, `llmcost.py` | No record outside the agent |
| Alias allow-list on the key | key `models` | Deploy-time check only |

So direct mode is not "the same thing without a hop". It moves a full-power, long-lived credential
into a container whose code AgentBreeder does not fully control: registry tools run in-process, and
`docs/agent-yaml.md` says the runtime is "reliable for honest tools and against the model, not
against hostile tool code". On both targets that container also has open internet egress. Local
Docker networks allow it, and the Kubernetes `NetworkPolicy` allows `0.0.0.0/0` minus
`blocked_cidrs` (`kubernetes.py`).

## 2. Goal

- An operator can let an agent call Gemini's public API directly (OpenAI and Anthropic after each
  is run, D8), with no gateway in the stack, as an explicit, approved, audited deploy choice per
  environment. Only the deploy is gated. Nothing protects the key at run time.
- `agent.yaml`, the build template and the image stay environment-neutral. The same package can run
  through the gateway in one environment and direct in another. The deploy record says which.
- Every loss in §1 is either replaced with something weaker that is named honestly, or stated as a
  gap in `status`, `cost` and the docs. Nothing is claimed that has not been run.

Non-goals: Vertex, Bedrock, Azure, Ollama and custom endpoints (#1025); creating provider-side keys
or projects; the MCP egress boundary (`runtime_enforcement: mcp_http`, §6.5); guardrails (#1024);
knowledge, `eval` and `release` on the direct route in the first slices (D10, D11).

## 3. Options

### 3.1 Where the route is chosen

- **R-A: `agent.yaml` chooses** (`model.provider: gemini`, `model.name: gemini-2.5-flash`). This is
  simple to read. But the author picks the credential's scope, the template differs per route, and
  one release cannot run on the gateway in staging and direct in a sandbox.
- **R-B: the operator environment chooses (recommended).** `agent.yaml` keeps `model.alias`. The
  operator config maps aliases to a route per environment. `agent.yaml` may optionally *pin* the
  provider family (`model.provider: gemini`), so a prompt tuned for one family is never served by
  another. The gate denies a mismatch.

R-B keeps the existing split: the author says what the agent is, and the operator says what it may
use (`docs/manifest.md`: "`model_access` come from the operator environment").

### 3.2 Which client the runtime uses

- **C-A: three native SDKs** (`google-genai`, `openai`, `anthropic`). This means three code paths,
  three tool-call and usage shapes, and three test matrices. `google-genai` and `openai` are
  already in `runtime/adk@1.2.0/runtime/requirements.lock` (via ADK and LiteLLM); `anthropic` is not.
- **C-B: the LiteLLM SDK in-process (recommended).** `litellm==1.108.3` is already locked, and ADK's
  `LiteLlm` wrapper already drives it. Direct mode changes only the `LiteLlm(...)` arguments: the
  model becomes `gemini/<model>` (or `openai/…`, `anthropic/…`), `api_base` is a constant (§6.2) and
  `api_key` is the provider key. This keeps one code path, and tool calls and usage keep the shape
  `AgentPlugin` already reads. P1 verifies that LiteLLM's Gemini and Anthropic paths need no new
  locked dependency.
  The risk: the in-process SDK reads environment variables, `.env` files, proxies and callbacks.
  §6.2 lists the countermeasures.

### 3.3 How the provider key reaches the agent

- **K-A: an author secret in a hand-written manifest.** Refused. The gate cannot tell a model key
  from any other secret.
- **K-B: the existing operator `bindings` (recommended).** `Environment.bindings` already maps an ENV
  name to `secret://name`, and `bind()` writes it into `manifest.secrets`. Binding
  `LLM_PROVIDER_KEY: secret://gemini-direct` reuses the existing copy, mount, `SECRET_MISSING` /
  `SECRET_MODE` preflight (`secret_files.py`) and the Kubernetes Secret path, with no new mount
  mechanism. `bind()` writes bindings into `manifest.secrets` (`build.py` ~310), so a built direct
  manifest declares `LLM_PROVIDER_KEY` itself, and the gate cannot tell a bound secret from a
  hand-written one by name. So the rule is an equality check in `gate._model_access`:
  - `LLM_PROVIDER_KEY` is allowed only when `model_access.direct` is set and
    `manifest.secrets["LLM_PROVIDER_KEY"]` equals `env.bindings["LLM_PROVIDER_KEY"]`
    (`PROVIDER_KEY_UNBOUND` otherwise).
  - On any other manifest, including gateway-route ones, `LLM_PROVIDER_KEY` is denied
    (`SECRET_RESERVED`).
  - A direct manifest may not carry any other secret that `env.bindings` maps to the same
    `secret://` reference (`PROVIDER_KEY_UNBOUND`). It also may not carry a `PROVIDER_KEYS` env name
    (`GEMINI_API_KEY`, …) (`SECRET_RESERVED`). A key filed under an unrelated name is not
    detectable; the docs say so.

## 4. Recommended design (R-B + C-B + K-B)

### 4.1 Operator config

```yaml
model:
  environments:
    sandbox:
      targets: [local]
      models: [support-model]            # unchanged: the aliases this environment grants
      bindings:
        LLM_PROVIDER_KEY: secret://gemini-direct
      direct:                            # new; mutually exclusive with `gateway` (D2)
        acknowledge: provider-key-readable-by-agent-code   # required literal (D5)
        aliases:
          support-model: {provider: gemini, model: gemini-2.5-flash}
        caps: {tokens_per_turn: 200000}  # runtime cap, §5.2
```

- `provider` is `gemini | openai | anthropic`, limited by D8. OpenRouter is excluded because it is
  itself a gateway. `model` uses the existing `_MODEL_NAME` pattern.
- Every alias in `models` must appear in `direct.aliases`, or config load fails.
- `direct` without `bindings.LLM_PROVIDER_KEY` fails config load (`DIRECT_KEY_UNBOUND`).
- `direct` with `gateway` set fails config load (D2). The direct route has no `ceilings`; §5.2 says why.
- `OperatorConfig.digest` is the sha256 of the config file's bytes (`operator_config.py` ~434), so
  an operator config that does not use `direct` keeps its digest. The in-memory path (~424,
  `model_dump_json()`) would change with a new defaulted field, so `direct` defaults to None and is
  excluded there when unset. A test asserts both.

### 4.2 `agent.yaml` and the build template

- `model.provider` (optional): `gemini | openai | anthropic`. It is an `AgentFile` field
  (`agentfile.py` `Model`). There is no agent.yaml JSON Schema today, so the docs change is in
  `docs/agent-yaml.md`.
- The pin goes into `DeployTemplate` (`build.py` `template`) as `model_provider`, left out when
  unset. Packages without it keep their bytes and digests.
- `build` and `release` (which carry the template) stay environment-neutral. Only `bind()` and deploy
  see the route.
- `build` on a direct environment with `knowledge` non-empty is a deny, `DIRECT_KNOWLEDGE_UNSUPPORTED`
  (D11). The embedding client, the ingest path and the chunk-hash prefix (`commands/knowledge.py`,
  which hashes `gateway:<alias>:<dim>`) all assume the gateway.

### 4.3 Manifest (`docs/manifest.schema.json`, `manifest.py`)

- `ModelAccess.gateway` becomes optional. A new optional `ModelAccess.direct` is
  `{provider, model}` per alias (`{alias: {provider, model}}`).
- `bind()` writes exactly one of the two. Both or neither is a validation error.
- Digest stability: `Manifest.digest` (`manifest.py` ~181) excludes only top-level `model_access`/
  `mcp_access` when None. Nested None fields such as `budget_usd` are serialised as `null` today, so
  `exclude_none` would change existing digests. Instead `digest()` adds a nested exclude
  `{"model_access": {"direct"}}` when `direct` is None. `revision_digest` (`gate.py` ~425) dumps the
  whole manifest, so it needs the same exclusion. A test pins one recorded digest of each.
- With `direct` set, `budget_usd`/`rpm` are refused (`BUDGET_UNSUPPORTED`). They would imply a
  limit that does not exist.
- The digest covers `direct`: route, provider and model per alias. It never covers the key.

### 4.4 Runtime

This is a new runtime version (see §4.6 for its number). It reads `AGENTBREEDER_MODEL_ROUTE`, a
non-secret JSON value that deploy writes from the manifest's `model_access.direct`:
`{"provider":"gemini","model":"gemini-2.5-flash"}`.

- The runtime refuses to start on unknown keys, on a provider outside `{gemini, openai, anthropic}`,
  or on a provider that differs from `agent.config.json`'s `model.provider` pin (when set).
- It builds `LiteLlm(model=f"{provider}/{model}", api_base=PROVIDER_BASE[provider],
  api_key=secret("LLM_PROVIDER_KEY"), llm_client=CostClient())`.
- Unset, it behaves exactly like 1.2.0.
- The runtime manifest declares `model_routes: [gateway, direct]`. Deploy sees only the manifest and
  an image digest, not the runtime registry entry, so the check runs where `agent.lock` and the
  runtime manifest are visible: `bind()` refuses a direct environment for a runtime that does not
  declare it (`RUNTIME_ROUTE_UNSUPPORTED`). This covers an `agent.yaml` still pinned to
  `runtime/adk@1.2.0`. The template carries the declared routes (`model_routes`, left out when
  unset), so the gate re-checks a hand-assembled or promoted manifest against them.
- The manifest adds `LLM_PROVIDER_KEY` to `secrets` and `AGENTBREEDER_MODEL_ROUTE` to `environment`.
- On a direct route, deploy sets no `AGENTBREEDER_GATEWAY_URL` and mints no key.
- `status` shows `model: direct gemini/gemini-2.5-flash` where it shows the gateway key today.

### 4.5 Gate, deploy preflight and policy

Gate denies (`gate.py`). None can be overridden in audit mode, the same rule as the existing
`_model_access` denies:

- `DIRECT_UNCONFIGURED`: the manifest has `direct`, the environment has no `direct`.
- `MODEL_NOT_ALLOWED`: an alias is not in `direct.aliases`.
- `MODEL_ROUTE_MISMATCH`: the manifest's provider or model differs from the environment's mapping.
- `MODEL_PROVIDER_MISMATCH`: the template's pin differs from the environment's mapping.
- `PROVIDER_KEY_UNBOUND` and the conditional `SECRET_RESERVED`: §3.3.

Deploy preflight (`deployment.py`):

- The missing or unsafe key file is the existing `SECRET_MISSING`/`SECRET_MODE` check.
- `runtime_enforcement: mcp_http` extends the existing `MODEL_ACCESS_UNSUPPORTED` refusal
  (~562), so no new code is needed.

Policy:

- The Cedar `Agent` entity gains `model_route` (String `gateway|direct`, default `gateway`) and
  `model_providers` (Set of String, default empty).
- The attributes are built by hand in two places in `policy/cedar.py`: the deploy entities and
  `_call_entities`, the `call_tool` snapshot the egress sidecar uses. Both get them with defaults,
  so no `call_tool` evaluation fails on a missing attribute.
- `policy/remote.py` (OPA/AuthZEN) passes them through.
- `gate.py` `policy_request` adds them to `resource.properties`.
- A bundle can then write
  `forbid (principal, action == Action::"deploy", resource) when { resource.model_route == "direct" && resource.risk_tier == "high" };`.
- `SCHEMA_VERSION` is the schema text's sha256 (`policy/__init__.py`). This spec and #1024 change
  the schema in **one** P0 slice, so it changes once (D6).

Approval: every direct-route deploy needs second-person approval (D7). The `approve` action and
the approval flow are the existing ones. The new part is the trigger.

### 4.6 Runtime version plan (shared with #1024)

Registry versions are hash-locked, so two specs cannot both claim `runtime/adk@1.3.0`:

- The first runtime slice to merge takes 1.3.0, and the next takes 1.4.0.
- #1024's gateway guardrails need no runtime change. Only its annotation slice does.
- The `new agent` starter stays on 1.2.0 until the new version has a recorded run. The slice that
  records the run moves the pin.

## 5. What replaces budgets, cost and spend logs

### 5.1 Cost

- On the direct route there is no cost header, so the new runtime's `CostClient` calls
  `litellm.completion_cost(response)` from the in-process price map. It marks the reply and each
  `model_called` event `cost_usd_source: "agent-estimate"`. Gateway calls are marked `"gateway"`.
- `agentbreeder cost` reads only the gateway spend log, and stays that way in this epic (D4). For
  a direct environment it prints `cost: not recorded (direct route; per-turn agent estimates are in
  /chat replies and model_called events)`. In a mixed report, direct deployments get that line and
  are never summed with gateway spend.
- The docs say that the estimate is reported by the agent, which agent code can falsify, and that
  no limit or ceiling decision uses it.

### 5.2 Budgets and limits

- AgentBreeder enforces no spend limit on the direct route. `status` shows
  `budget: none (direct route)`.
- The runtime enforces `max_model_calls` (unchanged) and `direct.caps.tokens_per_turn`, which stops
  the turn with a coded error. These caps catch a runaway model loop. They run in the same process
  as tool code, so they are not a budget and not enforcement. The docs call them "runtime caps".
- The real spend control is provider-side: a key restricted to one project with its own spend limit
  (an OpenAI project budget, an Anthropic workspace limit, a Google Cloud budget). The `acknowledge`
  literal's docs ask the operator to use one. AgentBreeder cannot check it and does not claim to.

### 5.3 Revocation and rotation

- There is no per-deployment key to revoke. Remove and disable stop the container and delete the
  deployment's copy of the secret, as for any secret. The kill switch is unchanged.
- Revoking the credential is a provider-side action. `remove` and `disable` print one fixed line
  saying so, which names the provider and never the secret name or path.
- No rotation-drift check in this epic. A bare hash of the key would be a guessing oracle, and a
  keyed hash needs per-deployment state for a feature nobody has asked for.

## 6. Security

### 6.1 Threats

| # | Threat | Gateway route today | Direct route (this design) |
|---|---|---|---|
| T1 | In-process tool code reads `/run/secrets/<name>` (or the Python string, `/proc/self/mem`, a core dump) and exfiltrates it over open egress | Gets a virtual key capped by budget, rpm and aliases, and revocable | Gets the full provider key. Redaction scrubs only *output* and cannot stop a read or catch a transformed value. **Residual, accepted only via D5** |
| T2 | A runaway loop spends money | The key budget stops it | Runtime caps only (§5.2); the provider-side limit is the operator's |
| T3 | Environment or config redirects the SDK (`*_API_BASE`, `*_BASE_URL`, `HTTPS_PROXY`, `ALL_PROXY`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `LITELLM_*`, a `.env` file), from the image's `ENV` lines or Kubernetes env | `api_base` is explicit and the key is virtual | §6.2 scrub, constant `api_base`, `trust_env=False`; a test per variable asserts the outbound host |
| T4 | The key leaks to logs, traces or errors, including partial forms (`Incorrect API key provided: sk-...abcd`) | Key never in the agent | Callbacks off; the key and its first/last 4 characters are added to the runtime `redaction` set and `mcp_client.remember_secret`; a test feeds a real provider 401 and greps the output |
| T5 | The wrong route is used silently | n/a | The route is in the manifest digest, the audit row and `status`; the runtime refuses an env route that differs from the pin |
| T6 | The cost record is forged | The gateway log is outside the agent | Labelled `agent-estimate`, never summed or used for limits |
| T7 | Kubernetes: another workload or the agent reads the Secret | n/a | The pod has `automountServiceAccountToken: false` (`kubernetes.py`); the deployer token's Secret access is the #770 residual (spec D15), unchanged |
| T8 | CLI and console surfaces echo the key | n/a | Secret names only, as today; `doctor`, `logs`, `up` child output go through the same redaction; one e2e grep |

### 6.2 SDK hardening (new runtime, at import, before `import litellm`)

- Delete from `os.environ` every name matching `*_API_BASE`, `*_BASE_URL`, `*_PROXY`
  (case-insensitive), `LITELLM_*`, `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE`, and the provider key
  variables (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).
- Stop dotenv loading (`LITELLM_MODE`, verified against the pinned version in P1; if it cannot be
  turned off, run from a working directory that has no `.env`).
- `api_base` is a constant per provider (`https://generativelanguage.googleapis.com`,
  `https://api.openai.com/v1`, `https://api.anthropic.com`), never read from config or env.
- Set `litellm.callbacks = litellm.success_callback = litellm.failure_callback = []`,
  `litellm.telemetry = False` and `litellm.set_verbose = False`. Pass an HTTP client with
  `trust_env=False` where LiteLLM accepts one, verified in P1. A test asserts all of these in the
  runtime process.

### 6.3 Key custody

- These are the rules for every `secret://` value (`docs/manifest.md`): the source file is mode
  0600 and owned by the operator, copied after the gate allows, bind-mounted read-only, and never
  put in env, labels or the audit log. Kubernetes uses a per-deployment Secret.
- No automated "same key as the gateway" check (D9). Deploy cannot see the values a stack's
  `service/litellm` imported from the host environment.
- The docs ask for a separate key per agent and environment.

### 6.4 What the docs and `status` must say

- `status` leads with: "provider key is readable by agent tool code; no budget is enforced".
- The docs never call direct mode protected, enforced or budgeted. "Gated" refers only to the
  deploy decision.

### 6.5 Egress boundary

A direct route needs the public internet, which the boundary exists to cut. The existing
`MODEL_ACCESS_UNSUPPORTED` refusal covers it. The docs say "direct mode has no egress boundary".

## 7. Interaction with #1024 (guardrails)

Gateway guardrails cannot see direct calls. An environment with `guardrails.required` and `direct`
fails config load (`DIRECT_NO_GUARDRAILS`). There is no direct-route guardrail claim.

## 8. Change list and cross-repo impact

- **OSS:**
  - `agentfile.py`: `model.provider`.
  - `operator_config.py`: `direct`, digest exclusion when unset.
  - `build.py`: template `model_provider`, `bind`, `DIRECT_KNOWLEDGE_UNSUPPORTED`.
  - `manifest.py` and `docs/manifest.schema.json`: `ModelAccess.direct`, optional `gateway`.
  - `gate.py`: the denies and policy properties.
  - `policy/cedar.py` (both entity builders), `policy/remote.py`, `policy/cedar_schema.json`.
  - `deployment.py`: skip mint/revoke/sweep and the gateway URL on the direct route; route env.
  - `build.py` `bind`: `RUNTIME_ROUTE_UNSUPPORTED`; template `model_routes`.
  - `kubernetes.py`: route env.
  - `evals.py`, `release.py`: `DIRECT_EVAL_UNSUPPORTED`.
  - `llmcost.py` and `commands/cost`: the direct line.
  - The new runtime version.
  - Docs: `agent-yaml.md`, `manifest.md`, `cost.md`, `identity-gate.md`.
- **Cloud:** no code reference to `model_access`, `gateway_keys` or `model.alias` (grep,
  2026-11-15). A `CLAUDE.md` note goes in the slice that changes the manifest schema.
- **Website:** nothing until release R and a verified run.

## 9. Acceptance criteria

1. With no gateway in the stack, a knowledge-less starter agent on the new runtime answers through
   Gemini direct on local Docker and on `kind`, with `cost_usd_source: "agent-estimate"` and a
   non-null estimate.
2. The same package, promoted unchanged from a gateway environment to a direct one, runs in both.
   The deploy records show different `model_access` and digests, and the template bytes are
   identical.
3. A manifest recorded before this change keeps its spec digest (`Manifest.digest`), and
   `revision_digest` computed with a pinned `SCHEMA_VERSION` is unchanged for an operator config
   without `direct` (golden tests). The real `revision_digest` changes once at P0 (D6).
4. Each new deny or refusal has a unit test and a CLI e2e test at the stage that raises it (build
   or bind, gate, deploy preflight), and none can be overridden in audit mode:
   - `DIRECT_UNCONFIGURED`, `MODEL_ROUTE_MISMATCH`, `MODEL_PROVIDER_MISMATCH`,
   - `DIRECT_KNOWLEDGE_UNSUPPORTED`, `DIRECT_EVAL_UNSUPPORTED`, `BUDGET_UNSUPPORTED`,
   - `RUNTIME_ROUTE_UNSUPPORTED` (bind), `DIRECT_KEY_UNBOUND` (config load), `PROVIDER_KEY_UNBOUND`
     and `SECRET_RESERVED` for `LLM_PROVIDER_KEY` on a gateway manifest (gate).
5. A Cedar bundle that forbids `model_route == "direct"` denies the deploy, and the audit row names
   the policy. An existing `call_tool` bundle still evaluates.
6. Key exposure tests:
   - Greps of spans, `model_called` events, container stdout, the audit log and CLI output after a
     forced provider 401 find no full key and no 8-character key fragment.
   - LiteLLM callbacks are empty in the runtime process.
   - Each §6.2 variable, set in the image, does not change the outbound host.
7. `status`, `cost` and `remove` print the §5 and §6.4 lines.
8. Each direct deploy needs a second person's approval.
9. OpenAI and Anthropic direct are each run end to end once before the docs name them.

## 10. Owner decisions

- **D1** The operator environment chooses the route, with an optional `model.provider` pin in
  `agent.yaml` (R-B), rather than `agent.yaml` choosing provider and model (R-A). *Rec: R-B.*
- **D2** `gateway` and `direct` are mutually exclusive per environment. *Rec: yes.*
- **D3** The runtime client is the LiteLLM SDK in-process with the §6.2 hardening (C-B), rather than
  three native SDKs. *Rec: C-B.*
- **D4** Cost on the direct route is a per-turn agent estimate in replies and events only.
  `agentbreeder cost` reports it as not recorded. No AgentBreeder budget. *Rec: yes.*
- **D5** Ship direct mode and accept T1 (a full provider key readable by in-process tool code, with
  open egress), behind the required `acknowledge: provider-key-readable-by-agent-code` literal and
  D7. *Rec: accept.* (If declined, #1023 closes and #1025 is the only path to more providers.)
- **D6** Add the Cedar attributes for this spec and #1024 in one shared P0 slice, so the schema hash
  changes once. `revision_digest` includes `SCHEMA_VERSION` (`gate.py` ~446), so every deployment's
  revision digest changes at P0, and held approvals bound to a revision (`deployment.py` ~1115,
  ~1180, ~1743) must be reissued. P0's changelog fragment says so. *Rec: yes.*
- **D7** Every direct-route deploy needs second-person approval, regardless of risk tier.
  *Rec: yes.* Nothing at run time reduces T1, so the deploy is the only control.
- **D8** Gemini only in the first slices. OpenAI, then Anthropic, each after a recorded run.
  OpenRouter excluded. *Rec: yes.*
- **D9** No automated "same key as the gateway" check (deploy cannot see the stack's imported
  values); docs only. *Rec: yes.*
- **D10** `eval` and `release` are refused on a direct environment (`DIRECT_EVAL_UNSUPPORTED`).
  A package is evaluated on a gateway environment and promoted. `status` says the eval verdict came
  from the gateway route. *Rec: yes; a direct judge is a later spec if asked for.*
- **D11** Knowledge is refused on the direct route (`DIRECT_KNOWLEDGE_UNSUPPORTED`) until a direct
  embedding client, ingest path and hash prefix are specified. *Rec: yes.*

## 11. Slices (after D1–D11)

- **P0 (shared with #1024 G1a):**
  - Schema: `model.provider`, template `model_provider`, operator `direct`, `ModelAccess.direct`.
  - Digest-stability golden tests.
  - Gate denies and Cedar attributes (both entity builders, remote, schema).
  - Unit and CLI e2e tests. No runtime work.
- **P1:**
  - Runtime direct client with §6.2 hardening; route env and secret binding on local and Kubernetes.
  - Skip mint/revoke; `RUNTIME_ROUTE_UNSUPPORTED`; D7 approval trigger.
  - Real Gemini runs on local Docker and `kind`.
- **P2:** cost estimate, `cost`/`status`/`remove` lines, `DIRECT_EVAL_UNSUPPORTED`, docs.
- **P3:** OpenAI, then Anthropic, each with a recorded run before the docs name it.

## 12. Review log

| Round | Reviewer | Lens | Result |
|---|---|---|---|
| r1 | Claude Sonnet (read-only subagent) | /architect | CHANGES REQUESTED: 5 high (runtime version clash with #1024, Cedar entities built in two places, gateway assumed in build/manifest/deploy/evals, digest change from a defaulted field, guardrail sha unbindable), 8 medium, 4 low. Fixed in r2: §1 table, §4.2–§4.6, §5.1, §8, D10, D11; `openai` lock claim corrected; `MODEL_ACCESS_UNSUPPORTED` reused |
| r1 | Claude Sonnet (read-only subagent) | /security | CHANGES REQUESTED: 3 high (SDK env/proxy redirection, key readable by tool code and other paths, D5 needs a firm control), 3 medium, 2 low. Fixed in r2: §6.1 T1/T3/T4/T7/T8, §6.2, §6.4, D7 now required approval, key-shared check dropped (D9), route env strictly parsed, rotation-drift hash dropped |
| r2 | Claude Sonnet (read-only subagent) | /architect + /security | CHANGES REQUESTED: r1 fixes confirmed; 2 medium here (revision digest changes at P0 via `SCHEMA_VERSION`; `SECRET_RESERVED` would reject built direct manifests and deploy cannot see the runtime's routes). Fixed in r3: AC3, D6, §3.3 equality check, §4.4 check moved to `bind` |
| r3 | Claude Sonnet (read-only subagent) | /architect + /security | APPROVED, no high or medium left |
