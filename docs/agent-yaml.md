# `agent.yaml` (authoring schema v1, preview)

> Part of the `agent.yaml` build work (#725), preview. `validate` and `build` work today with the
> ADK runtime (`runtime/adk@1.0.6`). Verified end to end on macOS + Docker Desktop with the
> Northwind example (`src/agentbreeder/examples/northwind/`): its services started from `stack.yaml` with
> `agentbreeder services up` (see `services.md`), then built from `agent.yaml`, deployed through
> the gate and released through the 61-case eval gate with `agentbreeder release` (61/61,
> 2026-11-08).

An agent file says what an agent is made of: a runtime, a model alias, a prompt, knowledge, tools,
memory and evals. Everything is a registry reference (`kind/name@X.Y.Z`, see `registry.md`). You
write no Python: the runtime and the tool packages provide the code.

An agent file is **not** the deploy manifest (`manifest.md`), which stays image-in. It is also
environment-neutral: the gateway, services, secret bindings and identities come from the operator
config for the environment you build for. Nothing in it can grant a permission or raise a limit.

```yaml
kind: agent                    # required; tells agent files apart from manifests and 2.x files
schema_version: 1
name: northwind-support        # DNS label, at most 53 characters
runtime: runtime/adk@1.0.6
model:
  alias: support-model         # a LiteLLM gateway alias
  reasoning: low               # none | low | medium | high
  max_model_calls: 4           # the last call has tools switched off
  budget_usd: 25               # optional; can only lower the operator's ceiling
  rpm: 120                     # optional; same rule
prompt: {ref: prompt/northwind-support@1.0.1}   # or {path: ./prompts/dev.md} while iterating
knowledge:                     # runtime/adk 1.x: at most one knowledge set
  - {ref: knowledge/northwind-help@1.0.0, top_k: 5, extra_searches: 1}
tools:
  - {ref: tool/customer-notes@1.0.0}
  - {ref: tool/orders@1.0.0}
memory: {sessions: postgres, notes: true}       # notes needs tool/customer-notes; postgres is the only session store
agent_events: postgres         # the agent's own event log, not the deploy audit chain
traces: otlp
evals: {ref: eval/northwind-golden@1.0.1, threshold: 1.0, retries: 1}
```

`guardrails:` (optional, at most 10, #1053) names agent guardrails that the environment's gateway
config defines with `default_on: false`, for example `guardrails: [agent-ip-block]`. They run on
this agent's model calls through the gateway (chat completions and embeddings) in addition to the
environment floor, and never in place of it. A manifest with them may not mount a provider key
name (`SECRET_RESERVED`). `build`
copies them into the package's deploy template and the manifest's `guardrails`. At deploy, the
gate refuses a name the environment doesn't offer (`GUARDRAIL_UNDEFINED`), and the deploy attaches
them to the deployment's gateway key (see [services.md](services.md#agent-guardrails-1053-preview)).

`mcp:` (optional, at most 10) lists MCP servers by registry ref only, with the tools the agent
uses: `- {ref: mcp/github@1.0.0, tools: [list_issues]}`. `build` refuses tools the version's
`mcp.yaml` doesn't declare (`MCP_TOOL_UNKNOWN`) and writes them into `agent.config.json` and the
manifest's `mcp_access`. `agentbreeder services up` runs a relay for each stack `mcp:` entry
(see [services.md](services.md#mcp-servers-through-a-relay)). `runtime/adk@1.1.0` and later call the
tools through it: one ADK `McpToolset` per server, with tools named `<server>_<tool>` (for
example `github_list_issues`). `build` refuses `mcp:` on a runtime without an MCP client, such
as `runtime/adk@1.0.6` (`MCP_RUNTIME_UNSUPPORTED`). It also refuses MCP tool names that would
clash: the same name from two servers, a function tool or knowledge search (`MCP_TOOL_CLASH`),
or a name ADK reserves (`MCP_TOOL_RESERVED`).

What happens at run time:
- **Relay unreachable when the tools are listed:** the turn runs without that server's tools,
  and the turn result and its audit events say so (`mcp_unavailable`). The runtime caches a
  successful listing for 60 seconds. If the relay goes away after that, the tools stay declared,
  and each call fails to the model and is audited as failed.
- **A call the relay refuses** (for example, a tool that is no longer granted) reaches the model
  as an error, and the audit records it as failed.
- **After a relay restart,** the next tool call fails if the listing is still cached, and the one
  after it reconnects. Once the cache has expired, the listing itself reconnects and no call
  fails.
- **Sharing:** every customer of a deployment shares one MCP session, and the relay's per-token
  limits (4 concurrent calls, 60 requests a minute). Use only MCP servers whose session holds no
  per-user state, or accept the sharing: with `mcp/playwright`, every customer drives the same
  browser. Per-conversation sessions are #747.
- **Unauthenticated callers:** `POST /invoke` is not authenticated, so whoever can reach the
  agent can make it call its granted MCP tools. That means loopback and, despite the `Host`
  check, other containers on the agents network. Authenticated invocation is #780.
- **Tool data:** MCP tool arguments go to the agent's audit events and traces, and tool results
  to traces, as for registry tools.
- **Timeouts:** connecting, initializing and listing tools each have 10 seconds, with one retry.
  The relay's `tools/list` checks definitions against the upstream live. Measured through the
  relay to GitHub's hosted server: about 0.8 s to initialize and 0.7 s to list tools (#748).

**What has been verified:** an agent built from `agent.yaml` called an MCP tool through the relay
in Docker, against a mock MCP server and a fake LLM gateway (`tests/test_adk_mcp_docker.py`).
Through a real `deploy` (identity gate, relay token and grant file), a deployed agent called
every tool of the built-in `mcp/github` and `mcp/playwright` through the relay to the real
upstream, with a fake gateway asking for the tools (`tests/test_mcp_builtins_live.py`, #748,
2026-11-09). The full `deploy` path with a real model is not verified yet.

`skills:` (optional, at most 10, on `main`, not in a published preview) lists skills by registry
ref, one version per name: `- {ref: skill/refund-policy@1.0.0}` (see
[registry.md](registry.md#skills-on-main-not-in-a-published-preview)). The refs are locked like
any other. **No runtime loads skills yet**, so `build` refuses an agent with `skills:`
(`RUNTIME_NO_SKILLS`) before the image is built or the lock is written; a later runtime version
adds them (#1059). An agent file without `skills:` builds exactly as before; `validate --json`
now prints `"skills": []` for it (and `"guardrails": []` when it has no `guardrails:`).

## Validation rules

- **Strict YAML:** at most 64 KiB, unique keys, no anchors or aliases, and no unknown fields.
- **Refs:** each ref must have the kind its field expects, an exact version, and may appear only
  once.
- **Paths:** `prompt.path` must be a relative path inside the project. Its file is copied into
  the image as `prompt.md`.
- **Tools:** two tool packages may not define the same Python module (`TOOL_MODULE_CONFLICT`).
- **Error messages** name the field and never echo the value you supplied.

## Wrong file kinds

`validate` and `deploy` tell the three file kinds apart:

- **An agent file passed to `deploy`** is refused: build it first.
- **A deploy manifest passed where an agent file is expected** gets a pointer to `deploy`.
- **A 2.x `agent.yaml`** (`spec_version` or `framework`, without `kind`) is reported as
  unsupported.

## Build

```
agentbreeder build agent.yaml --environment dev [--locked | --update [REF ...]] [--output deploy/] [--json]
```

`build` resolves every ref (`registry.md`), copies and hashes each one into a temporary build
context, writes `agent.config.json` (data only), and runs `docker build` with the runtime's fixed
Dockerfile. That Dockerfile has a base image pinned by digest, hash-locked requirements, and no
build arguments or secrets. `build` then writes:

- `deploy/<name>.deploy.yaml`: the v2 deploy manifest for that image. Its identity, secret
  bindings and `model_access` come from the operator environment.
- `deploy/<name>-candidate.deploy.yaml`, when the environment defines a candidate identity.
- `deploy/<name>.build.json`: the image, the agent.yaml and lock hashes, and every ref's hash.
- `agent.lock`, unless you ran with `--locked`.

`build` never deploys and never reads secrets. Deploy the manifest with `agentbreeder deploy`.

The operator environment has to grant what the build needs. For a local demo,
`agentbreeder services up` writes it from `stack.yaml` and `--stack .` uses it (see
`services.md`); a hand-written operator config looks like this:

```yaml
gateways:
  litellm-local:
    type: litellm
    url: http://127.0.0.1:4000
    agent_url: http://host.docker.internal:4000
environments:
  dev:
    targets: [local]
    gateway: litellm-local
    models: [support-model, support-embedding]
    # fallbacks: {support-model: [support-model-openrouter]}   # granted models only; `services up` writes it (#1033)
    ceilings: {budget_usd: 50, rpm: 300}                       # required with a gateway
    services: {traces_url: …, tools_proxy_url: …}            # → AGENTBREEDER_SERVICE_<NAME>
    bindings: {DATABASE_URL: secret://northwind-db}
    # guardrails: {required: true, approved_sha256: …}       # floor; `services up` adds running_sha256, effective, optional (identity-gate.md)
    agents:
      northwind-support:
        identity: {provider: demo, ref: support-agent, environment: dev}
        candidate_identity: {provider: demo, ref: support-agent-candidate, environment: dev}
```

## The ADK runtime (`runtime/adk@1.0.6`)

The runtime ships with AgentBreeder and is built on Google ADK 2.12. It reaches models through
the LiteLLM gateway, using the `openai/<alias>` route with `reasoning_effort` set from
`model.reasoning`.

- **Sessions:** ADK sessions in Postgres, in an `adk_sessions` schema of the agent's own
  database (`DATABASE_URL`), so the agent needs one database credential.
- **Memory:** per-customer notes, injected into the prompt.
- **Knowledge:** hybrid pgvector and BM25 retrieval, with embeddings through the gateway.
- **Tools:** loaded from tool packages (`tool.yaml` plus `src/`). The customer comes from the
  server-side session, never from the model.
- **Step cap:** the last model call has tools switched off.
- **Observability:** OpenInference traces and an append-only event log.
- **Secrets:** read only from `*_FILE` mounts.
- **Least privilege (1.0.6):** if the database owner has already created the runtime's tables
  (`notes`, `tickets`, `turns`, `audit_events`) and the sessions schema, the runtime runs no DDL
  on them, so its role needs only the grants it uses (see `src/agentbreeder/examples/northwind/stack/db/commerce.sql`).

### `runtime/adk@1.2.0`

On `main`, not in a published preview (#828). Northwind used it until the `1.3.0` relock; it
released 61/61 on `1.2.0` from `main` (2b4e4cf, macOS, 2026-11-15), and in that run every `invoke`
turn returned `cost_usd`, which added up, within rounding, to LiteLLM's spend log for the key's
chat calls (see [`cost.md`](cost.md#per-turn-cost-runtimeadk120)). `1.2.0` is `1.1.0` (so it
includes the MCP client) plus:

- **Cost per turn:** each `/invoke` reply has `cost_usd`, the sum of the LiteLLM gateway's
  `x-litellm-response-cost` response header over the turn's model calls, in US dollars. It is
  `null` when any model call returned no cost header (for example a model the gateway has no
  price for). Knowledge embeddings are not included. Each `model_called` event has the call's
  own `cost_usd`. This is the gateway's figure, not a bill from the provider.
- **Tool events:** every `tool:<name>` event in `audit_events` has `detail.agent` (the agent's
  name), `detail.latency_ms`, and `detail.tool` (`tool/<name>@<version>`) for registry tools,
  including failed calls. A failed call writes one event with `ok: false`. Runtimes `1.0.6` and
  `1.1.0` also wrote a second event with `ok: true` for each failed call (#922).
- **No shadowing:** a tool is refused at startup if a top-level name in its `src/` (a module
  file or a directory) is a standard-library, built-in or runtime module, matches `__*__`, is
  importable from the image's own `sys.path` (any installed package, locked or not), or is
  also in another tool of the agent. A function's `module` must be in its tool's own `src/`.
  The check covers what is importable when the agent starts; a library's optional import of a
  package that isn't installed is not covered.
- **Trace attribution:** when `AGENTBREEDER_DEPLOYMENT_ID` is set, the root `agent_turn` span
  has the attribute `agentbreeder.deployment_id` (#907). `deploy` does not set it yet.
- **Manifest:** `memory_schema: 2`.

Tool code runs in the agent's process, so a malicious tool can still change `cost_usd`, its own
audit rows or the turn's record (it holds the agent's database connection). These fields are
reliable for honest tools and against the model, not against hostile tool code.

### `runtime/adk@1.3.0`

On `main`, not in a published preview (#1030, #1034, #1039). Northwind uses it: from a wheel
of the relock branch in a clean sandbox (macOS, Docker Desktop, 2026-11-15) it released 61/61, a
refund turn was served by `support-model`, a turn with the primary model broken in the sandbox's
gateway config was served by the OpenRouter fallback (`fallbacks`, `served_by`, the `invoke` note
and `traces` all showed it), and a sandbox copy redeployed with `budget_usd: 0.002` got
`MODEL_BUDGET_EXCEEDED` on its third turn. `1.3.0` is `1.2.0` plus:

- **Coded model errors.** A failed model call is answered with a fixed message, never the
  gateway's or provider's text:
  `{"error", "code", "correlation_id", "trace_id", "retry_after_s"}`. `correlation_id` is the
  turn id. The detail (redacted, at most 500 characters) is only in the agent's log, on one
  `model call failed: turn=<correlation_id> code=<code> …` line (`agentbreeder logs ID`), and in
  the turn's record and `turn_failed` event.

  | `code` | HTTP | When | Next step in the message |
  |---|---|---|---|
  | `MODEL_BUDGET_EXCEEDED` | 402 | the gateway refused the key's budget (`budget_exceeded`) | raise `model.budget_usd` (within the environment's ceiling) and deploy again, or wait for the budget period to reset |
  | `MODEL_RATE_LIMITED` | 429, `Retry-After` | the gateway's rate limit, still hit after the runtime's bounded wait | wait (`retry_after_s`), or raise `model.rpm` |
  | `MODEL_ACCESS_DENIED` | 502 | the gateway refused the key for the model (401/403) | check `environment.models` and deploy again |
  | `MODEL_REQUEST_REFUSED` | 502 | another 4xx: sending it again won't help | the log line under `correlation_id` |
  | `MODEL_UNAVAILABLE` | 502 | a 5xx, timeout or connection error, after retries | try again |

- **Bounded retries.** The OpenAI client under LiteLLM no longer retries on its own: it waited
  out a rate limit's `retry-after` (up to 120 s, twice) unseen. `1.3.0` retries a model call
  itself, up to 3 attempts. It retries a 409, 500, 502 or 503 (connection errors included)
  after a short backoff. It retries a rate limit only while the turn's waits add up to at most
  10 s, so LiteLLM's 60 s `rpm` window gives `MODEL_RATE_LIMITED` at once (seen live: 0.8 s,
  where `1.2.0` waited 67.6 s). It never retries a spent budget or a timeout (408, 504): the
  provider may already have answered and billed the call. No retry starts in the last 40 s of
  the turn's 170 s. Each reply has `rate_limit_wait_s`, the seconds the turn spent waiting on
  rate limits.
- **Fallbacks shown.** Each reply has `fallbacks` (how many fallbacks LiteLLM's
  `x-litellm-attempted-fallbacks` header reported over the turn's model calls) and
  `served_by` (the aliases that answered, from `x-litellm-model-group`). Only values shaped like
  an alias are kept (letters, digits, `.`, `_` and `-`), so a provider model id with a `/` or an
  API base is dropped. A gateway whose model groups are named like bare model ids would show
  those names. Both are `null` and `[]` when
  the turn made no gateway call. `model` is still the agent's own alias. The root `agent_turn`
  span has `agentbreeder.fallbacks` and `agentbreeder.served_by`, and each `model_called` event
  has the call's `served_by` and `fallbacks`. `invoke` prints a note, and `traces` shows
  `fallback: ALIAS`.
- **Log hygiene.** LiteLLM's masked key (`sk-...WXYZ`, also written with `…` or `***`) is
  redacted wherever the runtime redacts text. SHA-256 token hashes are redacted in the
  model-error detail and in the exception text and status of exported spans, which `traces`
  shows. Any other failure of a turn is answered with a fixed 500 and logged as one redacted
  `turn failed:` line, not as a traceback. ADK, MCP and LiteLLM
  library log lines are one redacted line each, with no traceback, for every agent (in `1.2.0`
  only for agents with `mcp:` servers). LiteLLM's "Give Feedback" banner is off.

**Earlier versions (`1.2.0` and before).** Every failed model call returns
`{"error": "The model service is unavailable; please try again"}` (502), or "busy" (429). There
is no code, so a spent budget reads as "try again". The reply never carried the upstream text, but
the stored turn error and ADK's logged traceback did, with LiteLLM's masked key fragment
unredacted. A rate-limited turn could wait up to about two minutes without saying so.

Northwind uses `1.3.0`.

### `runtime/adk@1.4.0`

On `main`, not in a published preview (#1056, #1074, #1024 G2). The `new agent` starter uses it
(its stack stays on `postgres-pgvector@1.1.0`). The runtime tests drive the real ADK, LiteLLM and
OpenAI clients against a scripted gateway. One live run on macOS with Docker Desktop (2026-11-16,
from a wheel built from the #1056 branch, a local copy of Northwind set to `1.4.0` behind the
Presidio gateway; the shipped example stays on `1.3.0`) checked cached
tokens on Gemini, OpenRouter and OpenAI, a guardrail block, `traces` and `cost`, and a `--model
gemini` starter released 2/2 on `1.4.0`; what it showed is under each item below. Northwind's 61
evals were not run on `1.4.0`. `1.4.0` is `1.3.0` plus:

- **Cached prompt tokens (#1056).** Each reply has `cached_prompt_tokens`: the prompt tokens the
  provider said it read from its cache, summed over the turn's model calls. ADK takes them from
  LiteLLM's usage (`prompt_tokens_details.cached_tokens` and the provider variants). They are a
  part of `prompt_tokens`, never added to it, and never estimated: `0` when the provider reported
  none. Each `model_called` event has the call's own figure, and the root `agent_turn` span has
  `agentbreeder.cached_prompt_tokens`. The `turns` table gets no new column (a database owner may
  have created it, so a column would need a new memory schema). Whether a provider caches at all is
  the provider's choice; the runtime asks for nothing. In the live run, Gemini
  (`gemini-3.8-flash`) reported cached tokens, in the one long session run, only once its prompt passed about 5,000
  tokens, and not on every turn after that (3,032 of 5,122 on one turn, 0 of 7,649 on a later one;
  never on Northwind's own 1,800-token prompt), OpenAI
  (`gpt-5.6`) from that session's second turn (2,438 of 3,628), and OpenRouter
  (`google/gemini-3.8-flash`) never: its own usage said 0. The sums matched LiteLLM's spend logs.
- **Tool names the model invented (#1074).** When the model asks for a tool the agent does not
  have, the runtime answers the model with a fixed error ("There is no tool with that name …",
  without the list of the agent's tools) and lists the name under `unknown_tools` in the reply,
  never under `tools`. It writes one `tool_unknown` event (`name`, `agent`, `unknown: true`,
  `ok: false`), not a `tool:<name>` row, so tool logs don't count it as a call. Its span is not
  a TOOL span, and the root span has `agentbreeder.unknown_tools`. A name is kept as at most 64
  characters of letters, digits, `_`, `.` and `-` (anything else becomes `?`), redacted. If an
  `mcp:` server is down, its tools are missing for that turn, so a call to one may be reported as
  unknown (not tested). Not seen live: in four Gemini turns and three each on OpenRouter and OpenAI
  that asked for a tool the agent does not have, `unknown_tools` stayed empty (the model used
  `lookup_orders` or declined), so this path is checked only by the runtime tests.
- **Guardrail blocks (#1024 G2).** A turn that a gateway Presidio guardrail blocks (the gateway's
  HTTP 400 "Blocked entity detected: ENTITY by Guardrail: NAME") is answered `MODEL_BLOCKED`, HTTP
  422, with a fixed message, the `correlation_id` and no content: neither the matched text nor
  the entity type nor the guardrail name is in the reply. The gateway can't tell an input block
  from an output block, so there is one code. The turn writes a `guardrail_blocked` event
  (`guardrail`, `entity`, `action: blocked`, `code`, `model`), and the root span has
  `agentbreeder.guardrail.action`, `.name` and `.entity`. The block is matched only at the start
  of the gateway's error text, so any other 400 (including Presidio being unavailable, which
  fails closed) stays `MODEL_REQUEST_REFUSED`. It is never retried. In the live run, a message
  with a US SSN was answered `MODEL_BLOCKED` with the event naming `pii-block` and `US_SSN`, and
  the gateway also blocked the turn's knowledge-search embedding, so no provider got the number.
  The agent still stores the customer's message, as for every turn: the blocked message, SSN
  included, was in the `turns` table and in the input of the trace's `agent_turn` and
  `retrieve_knowledge` spans. Presidio did not flag `123-45-6789`: that turn was answered normally.

  | `code` | HTTP | When | Next step in the message |
  |---|---|---|---|
  | `MODEL_BLOCKED` | 422 | a gateway guardrail blocked the message or the model's answer | rephrase without the blocked content; sending it again won't help |

Agents built from `agent.yaml` can't yet run behind the opt-in MCP egress sidecar
(`runtime_enforcement: mcp_http`): the deploy is refused with `MODEL_ACCESS_UNSUPPORTED`, because
the sidecar's network has no route to the gateway.

Tools run in the agent's own process with its privileges. Customer scoping is enforced by the
shipped tools' own code, not by an engine boundary.

**`customer_id` is not authenticated.** `POST /invoke` accepts `customer_id` from the request
body, and AgentBreeder does not ship authenticated invocation. Anyone who can reach the endpoint
can act as any customer. The endpoint is published on loopback only. Put an authenticating proxy
in front of it before exposing it to real users, and have that proxy set the customer. Requests
without a `customer_id` run as the shared `guest` identity. Guests get no saved notes, so notes
cannot leak between anonymous visitors, and note text is neutralised before it enters the
prompt.

## Eval and release

```
agentbreeder eval agent.yaml --endpoint URL [--environment ENV] [--only id,...] [--retries N] [--json]
agentbreeder release agent.yaml --environment ENV [--stack DIR] [--threshold 1.0] [--retries N] [--version X.Y.Z] [--json]
agentbreeder release agent.yaml --rollback [--environment ENV]   # gate-checked before anything is removed
```

`eval` runs the agent's eval set (`eval/<name>@X.Y.Z`, verified against `agent.lock`) against a
running endpoint. A case passes when every rule check passes (sources, tools, regex) and the LLM
judge scores correctness at least 0.7. The judge calls the gateway alias from the eval manifest,
on its own key, which is revoked after the run and expires after 6 hours in case the run is
killed. The eval set's `fixtures.sql` runs against the environment's `eval_database`, with that
database's credentials, before the run and before each retry round. That needs
`pip install "agentbreeder[eval]"`. An eval set is therefore trusted code, like a tool package:
only use eval sets you would let write to that database.

`release` is orchestration around two ordinary governed deploys. The exit codes are 0 released or
already released into production, 1 runtime failure (including `PACKAGE_INPUTS_CHANGED` and
`PACKAGE_PUBLISH_MISMATCH`), 2 denied or a release precondition failed (for example `LOCK_STALE`,
`PRODUCTION_CHANGED`, `REMOVE_FAILED`, `RELEASE_BUSY`, `VERSION_EXISTS`, `VERSION_INVALID`), 3
needs approval, 5 eval gate failed. The steps:

1. Build with `--locked`.
2. Check the production manifest through the gate without deploying. A deny exits 2. A required
   approval exits 3, because release does not run approvals.
3. Deploy `<name>-candidate` under its own identity and gateway key, run the eval set against it,
   then remove the candidate.
4. Below the threshold, exit 5 with production unchanged. The report is in
   `deploy/<name>.eval-<run>.json`. Each failed case is also printed (and listed as
   `failed_cases` with `--json`): its id (up to 80 characters), the checks that failed and the
   judge's reason (up to 200 characters), with control characters removed. These excerpts come
   from the model and the eval set, and they also appear in console job logs (on `main`, not in a
   published preview).
5. Otherwise write the agent package `agent/<name>@X.Y.Z` into `./registry/agent/` (see
   below), write a release journal (the previous production and its manifest) to
   `<state-dir>/releases/<name>.json`,
   remove the old production, and deploy the **exact evaluated image**.
6. If the new production fails to start, restore the previous one.

**The agent package** (on `main`, not in a published preview; format in
[`registry.md`](registry.md#agent-packages-in-progress-on-main-not-in-a-published-preview)).
Each release writes one immutable registry version, `./registry/agent/<name>/<X.Y.Z>/`, holding
the released `agent.yaml`, a redacted copy of `agent.lock`, and `package.json` (image ID and
platform, input hashes, eval summary, deploy template).
- **Version.** `--version X.Y.Z`, or by default the next patch above every taken version (`0.1.0`
  first). A version is taken when its directory exists or when an earlier release in this state
  directory reserved it, even if that release failed or its directory was deleted. A taken version
  exits 2 with `VERSION_EXISTS` before any eval runs, and the `Next:` line names the next free
  version. The version is chosen after the build and before the preflight.
- **When it is written.** Only after the eval gate passes and production is checked unchanged,
  and before the journal. If `agent.yaml` or `agent.lock` changed while the evals ran, nothing is
  published and release exits 1 (`PACKAGE_INPUTS_CHANGED`); production is unchanged.
- **The release row.** `release/promoted` names the package (`RELEASE_PACKAGE`), its tree hash
  (`RELEASE_PACKAGE_SHA256`) and a hash of the project path (`RELEASE_PROJECT`).
- **Cleanup.** If no `promoted` row is recorded after the package is published (a failed or
  denied production deploy, an interrupt, any error), the package is deleted again, but only if
  it is unchanged. The version stays reserved. If the audit log could not be confirmed
  (`AUDIT_WRITE_FAILED`), the package is kept and release says so (`PACKAGE_KEPT_UNVERIFIED`);
  run `agentbreeder audit verify`. A directory left by a release that was killed outright is never
  reused; `--version` naming it says it was never released and can be deleted.
- **Releasing the same image again** while it is the ready production gives its existing package
  (`[PACKAGE_EXISTS]`, exit 0); a different `--version` is not used. That needs the package
  directory to still hold exactly what was released, from this project; otherwise the image is
  evaluated again and released as a new version without replacing production.
- `--json` adds `package: {ref, sha256, path}` (the path relative to the project), and text output
  prints a `Package:` line.
- `package.json` records the image's `os/architecture` from `docker image inspect`.

Before it removes the candidate, every release reads the candidate's last 500 log lines (stdout
and stderr, timestamped, newest kept) and the values needed to redact them; after the removal it
redacts the lines and, when the candidate failed to start or failed the gate, keeps them:
`<state-dir>/releases/<name>.candidate.log`, mode 0600, one per agent, deleted when a later
release passes. A failed start deletes the candidate's own secret copies (its gateway key, MCP
tokens); the deploy keeps what it read just before that cleanup, in memory for that run only, so
the tail is still redacted with them. If any value could not be read, nothing is saved and
release says why (and names `agentbreeder migrate` when a state is from an older version). `agentbreeder logs --failed NAME` (with the same
`--state-dir`, or `--stack DIR [--environment ENV]`) prints it, with control characters shown as
visible escapes (`--raw` prints them as they are). Redaction is best effort: it covers the
stack's stored secrets, every live deployment's secret copies and the `agentbreeder secret`
values, in their literal, base64 (also inside a larger base64 string), URL-quoted and
JSON-escaped forms, and passwords in URLs; a value hashed, compressed or split across lines is
not found, and the short-lived eval judge key is not covered (on `main`, not in a published
preview).

With `--json`, release adds these keys when they apply:

- `failed_cases`: each failed case as `{id, failed_checks, reason}` (redacted, then capped).
- `candidate_log`: the saved tail's path; or `candidate_log_skipped`: why no tail was saved.
- `unreleased_production`: `{id, rollback}` when production ran this image without an audited
  release and the gate failed (`rollback` is true only when a release journal makes
  `--rollback` possible).
- `advice`: commands to run next, as text to show (never to execute), already carrying the
  `--stack`/`--environment` or `--state-dir` of this run: `show_log`, `remove`, `rollback`,
  `build`.
- `warnings`: for example that production was not released through the gate.

Production that already runs the built image counts as released only when the audit log has a
`release/promoted` row for that image and manifest, at no lower a threshold and with the same
eval set as now, and (on `main`) that row names a package still present in the project. Rows
written before packages existed do not count for `release`, which evaluates the image again and
records a packaged release; `promote` still accepts them. Otherwise (for example after a plain `deploy` of the production manifest),
release warns, evaluates the same image as a candidate, and on a pass records the release
without replacing production; on a fail it exits 5 and says to `remove ID --local` (and
`--rollback` only when a release journal with a previous production exists). It never removes
production on its own (on `main`, not in a published preview).

Messages that name their fix (on `main`, not in a published preview): `LOCK_STALE` says to run
`agentbreeder build` (release builds with `--locked`, which never updates `agent.lock`);
`REF_UNPINNED` on release says how to publish a path ref as a registry version; and
`CONTEXT_ENVIRONMENT_UNKNOWN` or `MODEL_ACCESS_UNCONFIGURED` suggest `--stack DIR` when a
`stack.yaml` sits beside the agent file (or one level above a manifest).

Limits:
- **The eval gate can be bypassed.** It is release orchestration, not an engine control: anyone
  with deploy rights can run plain `deploy`.
- **Promotion has brief downtime.** The engine refuses two specs under one name, so the old
  production is removed before the new one starts.
- **Gateway changes are not gated.** Changing the gateway's model mapping does not change the
  image, so `release` reports "already in production". Re-run `eval` against production after
  gateway changes.
