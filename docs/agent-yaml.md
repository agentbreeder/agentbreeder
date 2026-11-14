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
    ceilings: {budget_usd: 50, rpm: 300}                       # required with a gateway
    services: {traces_url: …, tools_proxy_url: …}            # → AGENTBREEDER_SERVICE_<NAME>
    bindings: {DATABASE_URL: secret://northwind-db}
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

On `main`, not in a published preview (#828). Northwind uses it; its 61/61 eval runs were on
`1.0.6`, and a run on `1.2.0` is pending. `cost_usd` has not yet been seen from a live gateway. `1.2.0` is `1.1.0` (so it includes the MCP client) plus:

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
reliable for honest tools and against the model, not against hostile tool code. The
`new agent` starter also uses `1.2.0` (its stack stays on `postgres-pgvector@1.1.0`).

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
