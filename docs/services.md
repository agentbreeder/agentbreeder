# Local backing services: `stack.yaml` (preview)

> **Local-only preview (#734).** `agentbreeder services` starts the services an agent needs
> (Postgres, a model gateway, a trace collector, project helpers) on the local Docker engine and
> writes a demo operator config for them. It is not production infrastructure. Verified on macOS
> with Docker Desktop: `examples/northwind` started from scratch with `services up`, and
> `agentbreeder release` passed its 61-case eval gate (61/61, 2026-11-08). MCP servers run behind
> a built-in relay (see [MCP servers](#mcp-servers-through-a-relay)). An agent built from
> `agent.yaml` on `runtime/adk@1.1.0` calls tools through it. That is verified in Docker against
> a mock MCP server and a fake LLM gateway. No built-in MCP server is verified yet (#748).

An agent built from `agent.yaml` needs backing services before it can run. A project declares
them in `stack.yaml` next to `agent.yaml`:

```
cd examples/northwind
agentbreeder services up --environment dev --env-file ~/.env   # shows the grant; --yes confirms
agentbreeder release agent.yaml --stack . --environment dev    # build → candidate → evals → promote
agentbreeder services down --purge --yes                       # remove everything up made
```

`agent.yaml` doesn't change and still grants nothing. `services up` writes the operator config
for a **local demo environment** outside the repo, and `--stack DIR` points `build`, `deploy`,
`eval`, `release`, `status`, `remove` and `reconcile` at it.

## `stack.yaml`

```yaml
kind: stack
schema_version: 1
name: northwind
services:
  postgres:
    ref: service/postgres-pgvector@1.1.0
    with:
      databases: [support, litellm, phoenix]   # one database and one login role each
      init_sql: stack/db/commerce.sql          # schema, seed data and grants, as the support role
      init_sql_database: support
      roles: [support_agent, support_ops]      # least-privilege roles in `support`
      roles_database: support
  litellm:
    ref: service/litellm@1.0.0
    with: {config: stack/litellm/config.yaml}
    wire: {database-url: postgres.databases.litellm}
  phoenix:
    ref: service/phoenix@1.0.0
    wire: {database-url: postgres.databases.phoenix}
  proxy:
    ref: service/northwind-proxy@1.0.0             # a project service, built from source
    wire: {phoenix: phoenix.url, phoenix-ingest-key: phoenix.ingest_key}
  ops:
    ref: service/northwind-ops@1.0.0
    wire: {database-url: postgres.roles.support_ops, ...}   # staff decisions
environment:
  gateway: litellm                       # a service that provides gateway/litellm
  models: [support-model, support-embedding, support-judge]
  ceilings: {budget_usd: 50, rpm: 300}
  services:                              # → AGENTBREEDER_SERVICE_<NAME> in the agent
    traces_url: "{proxy.url}/v1/traces"
    tools_proxy_url: "{proxy.url}"
  bindings:                              # secret files mounted into the agent
    DATABASE_URL: {ref: postgres.roles.support_agent}  # can't approve its own requests
  eval_database: {ref: postgres.databases.support}   # host-side fixture resets, as the owner
  agents:
    northwind-support:
      sponsor: support-lead@example.com
      review_by: "2027-06-30"
      candidate: true                    # also a candidate identity, for release
```

- **`with`** sets a service's params; **`wire`** connects each of its inputs to another service's
  output (`SERVICE.OUTPUT`, or `SERVICE.OUTPUT.ITEM` for per-item outputs such as one URL per
  database). Services never name each other, so registry entries stay reusable.
- **Typed outputs.** A `url` output may appear in templates such as `environment.services`. A
  `secret` output is only ever used whole: wired to a secret input, or as a `bindings` /
  `eval_database` value. It never appears in an environment variable or a label.
- **`services_only` outputs** may be wired to a service but never bound into an agent (Phoenix's
  ingest key reads every trace, so only the trace proxy holds it). **`operator_only` outputs**
  (the gateway master key) are used only by AgentBreeder itself, to mint per-agent keys.
- **`stack.lock`** records the hash of every service version and file param and the imported
  variable names. `--locked` refuses any change. After you edit a project file param (an
  `init_sql`, a gateway config), `up --update` accepts its new hash; a published service version
  never changes (publish a new version instead).
- **Least privilege in Postgres.** Give the agent and each service the narrowest role that works.
  `postgres-pgvector@1.1.0`'s `roles` creates login roles that own nothing but a schema named
  after themselves; the database owner's `init_sql` grants them exactly what they use. In
  Northwind the agent can read orders and file cancellation requests but can't approve one or
  change an order: only the ops role can.

## Commands

```
agentbreeder services up     [DIR] --environment ENV [--locked | --update] [--env-file FILE] [--yes] [--json]
agentbreeder services status [DIR] [--json]
agentbreeder services logs   [DIR] NAME [--tail N]       # known secret values are redacted
agentbreeder services env    [DIR] [--environment ENV]   # prints the two export lines
agentbreeder services down   [DIR] [--volumes | --purge] [--yes] [--json]
```

- **`up`** locks the refs, checks params, references and the gateway's model aliases, prints the
  **grant**, and needs `--yes` (or an interactive yes) the first time and whenever the grant
  changes. It then imports and generates secrets, builds project services, starts everything with
  Docker Compose, waits for health, runs `init` and `bootstrap` steps, and writes the operator
  config. Only services whose configuration changed are recreated.
- **The grant** lists every service with its image (built-in or project), egress, which networks
  can reach it, its `127.0.0.1` ports, imported variable names, the wiring, what agents receive,
  the gateway's models and where each provider key goes, and the demo identities.
- **`down`** stops the stack and keeps its data and secrets. `--volumes --yes` also deletes its
  data volumes. `--purge --yes` deletes everything `up` made: volumes, built service images, and
  the state directory (generated secrets, operator config, deploy history), so the next `up`
  starts from scratch. Both refuse while a deployment in the stack is still active
  (`agentbreeder remove ID --local --stack .` first).
- Imported variables come from the environment or from `--env-file` (a strict `KEY=value`
  parser; the file is never sourced). Well-known cloud, VCS and CI credentials can't be imported.

## Built-in services

| Service | What it runs | Params | Outputs |
|---|---|---|---|
| `service/postgres-pgvector@1.1.0` | PostgreSQL 16 with pgvector 0.8, data volume, uid 999 | as 1.0.0, plus `roles` (list) and `roles_database` | as 1.0.0, plus `roles.<name>`: a URL for a role that can connect only to `roles_database`, owns only a schema of its own name and has no other rights until `init_sql` grants them. `postgres` and `template1` are closed to applications. |
| `service/postgres-pgvector@1.0.0` | PostgreSQL 16 with pgvector 0.8, data volume, uid 999 | `databases` (list), `init_sql` (file, ≤ 64 KiB, no `\` meta-commands), `init_sql_database` | `databases.<name>`: a URL with that database's own role (never the superuser) |
| `service/litellm@1.0.0` | LiteLLM 1.106.2 proxy, `provides: gateway/litellm` | `config` (file, required; reviewed: allow-listed keys, each provider key only to its provider) | `url`; `master_key` (operator only) |
| `service/phoenix@1.0.0` | Arize Phoenix 21.0.0 with auth on, data in a wired Postgres database | none | `url` (host only); `ingest_key` (services only, minted by `bootstrap`) |
| `service/echo@1.0.0` | A tiny HTTP test service | `label` | `url`, `token`, `link` |

Phoenix's UI: sign in as `admin@localhost` with the password in the stack state at
`service-secrets/<service>/admin-password`.

## Writing a service

`agentbreeder registry new service NAME` scaffolds a version under `./registry/service/`:

```yaml
# service.yaml (strict: unknown fields are rejected)
build: Dockerfile                 # or image: name@sha256:<digest>
command: [python, /app/proxy.py]  # exec form only, never a shell; the whole argv
user: "65532:65532"               # image | host | uid:gid (required with a volume)
ports:
  http: {port: 8787, expose: agents, guard: host-allowlist}
params:
  label: {type: string, default: proxy}
env:
  PHOENIX_URL: "{inputs.phoenix}"   # {params.X} and {inputs.X} (url inputs) only, in env only
secrets:                            # files under /run/secrets/<name>, never env values
  tavily-api-key: {import: TAVILY_API_KEY}
  token: {generate: token, prefix: tk-}
inputs:
  phoenix: {type: url}
  phoenix-ingest-key: {type: secret}
health: {http: /healthz}
egress: true                        # outbound internet; shown in the grant
outputs:
  url: {type: url, port: http}
  # A secret output built from parts names only {address}, {item} (with `per:`), the service's
  # own {secret.X} and its own {params.X}; checked when the service.yaml is loaded.
```

- **Reachability (`expose`).** `internal` (default): other services only. `agents`: also agent
  containers, which join the stack's agents network. `host`: also published on `127.0.0.1` at an
  allocated port. A list such as `[agents, host]` combines them.
- **Every HTTP port reachable outside the stack declares a `guard`:** `bearer` (a key on every
  request), `litellm-auth`, or `host-allowlist` (Host and Origin checks). It covers browser and
  DNS-rebinding requests against loopback.
- **Hardening for every service:** read-only root filesystem, all capabilities dropped,
  `no-new-privileges`, a size-capped `/tmp`, and (if declared) up to four more `tmpfs` paths.
  `AGENTBREEDER_SERVICE` holds the service's own name on the stack network.
- **`init`** runs after every healthy start and must be idempotent. **`bootstrap`** runs once per
  data identity (again after its volume, or a wired service's volume, is new); its stdout becomes
  the secret named by `writes_secret`.
- **Built from source:** the build context is the hashed version directory. The Dockerfile is a
  strict subset: digest-pinned `FROM`, no `ARG`, `ADD`, build secrets or SSH. The image is tagged
  with the version hash.

## MCP servers through a relay

> **Preview (#745, #746).** Verified on macOS with Docker Desktop against a mock MCP server
> packaged as a stack service. Two tests cover it:
> - `tests/test_mcp_relay_docker.py`, with a test client on the agents network;
> - `tests/test_adk_mcp_docker.py`, with an agent built from `agent.yaml` on `runtime/adk@1.1.0`
>   whose model is a fake gateway that asks for the tool. The agent's container is started with
>   deploy's mounts, not by `deploy` itself.
>
> Remote https upstreams and OAuth refresh are covered by unit tests only. No built-in MCP server
> has been checked live (#748).

```yaml
mcp:
  github: {ref: mcp/github@1.0.0}
environment:
  agents:
    northwind-support:
      mcp: {github: [list_issues]}       # per-agent tool grant
```

Each `mcp:` entry gets its own relay container, `mcp-<name>` (with `_` in the name turned into
`-`, so `mcp.my_tools` runs as `mcp-my-tools`), built from the built-in
`service/mcp-relay@1.0.1` (standard-library Python on the pinned `python:3.12-slim`; 1.0.1 answers
an unknown session with a bare 404, which the ADK client needs in order to reconnect after a
relay restart). A `stack.lock` written before 1.0.1 still names `service/mcp-relay@1.0.0`, so
`services up --locked` refuses it (`LOCK_STALE`). Run `services up` once without `--locked` to
lock 1.0.1. Agents
reach it at `http://mcp-<name>:8080/mcp` on the agents network. The relay holds the upstream
credential as a 0400 file and runs as your uid. Agents hold only a per-deployment token, which
deploy mints and the relay checks against a grant file named by the token's sha256. It re-reads
that file on every request. The relay version can't be named in `stack.yaml`, and `./registry`
or `AGENTBREEDER_REGISTRY_PATH` can't override it. Nor can a `stack.lock` entry that records it
from anywhere else, even with `--locked` (`RELAY_RESERVED`).

What the relay does:

- It accepts only `initialize`, `notifications/initialized`, `notifications/cancelled`, `ping`,
  `tools/list` and `tools/call`. Batches, resources, prompts, sampling and `GET` streams are
  refused.
- It forwards only the tools granted to the deployment: the grant file's tools, intersected with
  the agent's grant in `stack.yaml`. A narrowed grant takes effect at once. An ungranted call
  gets a JSON-RPC error (`TOOL_NOT_GRANTED`) and never reaches upstream.
- It answers `tools/list` from the pinned definitions, in one page, and checks them against
  upstream.
- It mints its own session ids, one set per token. Another token's session id, or an unknown
  one, gets 404, so after a relay restart the client initializes again. `Last-Event-ID` is
  dropped, and `DELETE` closes the upstream session.
- It passes back only the response to the request, plus progress and log notifications.
  Sampling, elicitation, roots and `list_changed` messages are dropped. It rewrites the
  `initialize` capabilities to `{tools: {}}` and drops the upstream's `instructions`.
- Remote upstreams must be https on port 443. Each connection resolves the host once, refuses
  private, loopback and link-local addresses, and pins the address. Redirects are refused, and
  proxy variables are removed. `oauth_refresh` access tokens are cached in memory only, and
  providers that rotate refresh tokens are refused.
- Limits per token: 4 concurrent requests, 60 upstream-bound requests a minute (`initialize`,
  `tools/list`, `tools/call` and `notifications/cancelled`; `RATE_LIMITED`), 120 s per response,
  1 MiB for buffered (rewritten) responses, and 16 MiB for a streamed `tools/call` reply
  (`RESPONSE_TOO_LARGE`). Each relay takes at most 64 connections at once
  (16 from one address), closes a connection that stalls for 30 s, and requires the whole
  request (line, headers and body) to arrive within 30 s.
- Sessions it drops (a 17th session for one token evicts the oldest, and sessions idle for an hour
  expire) are closed upstream too.
- It logs JSON lines with the deployment, method, tool, decision and latency. It never logs
  arguments, results, upstream error bodies or credentials. An ungranted tool name is logged as
  `ungranted` plus a hash.

A service upstream (`upstream.service`) must be the stack service with that ref. Every port of it
must be `expose: internal`, and nothing else may wire to it. It joins a private network shared
with its relay, never the stack network (`MCP_UPSTREAM_EXPOSED`, `MCP_UPSTREAM_WIRED`). If it
declares `egress: true` it also joins the edge network, so it can reach the internet unfiltered.
Browser-type upstreams also need the filtering forward proxy (#747), so `mcp/playwright` isn't
supported yet.

**Consent, in two phases.** The grant you confirm shows each server's origin (built-in,
project or registry-path), its upstream, `credential VAR will be sent to scheme://host:port`, and
each agent's tools with their effect. A project or registry-path entry whose remote upstream
carries a credential also needs `--allow-project-mcp NAME`; `--yes` alone never allows it
(`MCP_PROJECT_CONSENT`). After the relays start, `up` runs a probe inside each relay: it uses a
stack-only admin token passed on stdin, and the host never calls upstream itself. The probe
shows each tool definition it found, whole (every field the pin covers, escaped), and asks you
to pin them. `--yes` accepts first pins and new tools only for built-in servers, or for servers
you named with `--allow-project-mcp`; accepting a changed definition always needs a person. The pins (a hash of each
whole tool object) go in `stack.lock`; the relay reads the definitions from its pins file and
reloads it without restarting. The first pin trusts what the upstream serves at that moment
(trust on first use).

**When a definition changes upstream,** the relay hides that tool and refuses calls to it
(`TOOL_DEFINITION_CHANGED`); it keeps the pinned definition to compare against. A tool missing upstream is reported separately
(`TOOL_MISSING_UPSTREAM`). `services status` lists both. `services up --repin` shows the diff
and asks before accepting it, and `--locked` refuses either change (`LOCK_STALE`). A changed
version, origin, upstream or token endpoint also needs consent again, and its pins are redone.

`up` refuses to drop an `mcp:` entry, or narrow a grant, while an active deployment uses it
(`MCP_GRANT_IN_USE`). Grant files live in `~/.agentbreeder/stacks/<id>/grants/<env>/<server>/`.
A changed imported credential recreates the relay; open sessions then get 404 and initialize
again. `--allow-project-mcp` must name an `mcp:` entry (`MCP_UNKNOWN`). `agentbreeder services
logs mcp-<name>` shows a relay's log lines.

Dropping an `mcp:` entry removes its relay container on the next `up` (it holds the
credential), unless an active deployment still uses it (`MCP_GRANT_IN_USE`). Other services
dropped from `stack.yaml` keep running until `agentbreeder services down`.

What the relay does **not** do:

- **Tool arguments are unconstrained.** A granted `list_issues` reaches any repository the
  credential can see.
- **Upstream results reach the agent unfiltered,** so prompt injection through tool output is not
  mitigated.
- **"The agent never holds the credential" says nothing about the credential's scope.** Use the
  narrowest credential the provider offers.
- **`effect` labels from project or registry-path entries are untrusted.** RBAC/ABAC on tool
  calls (#742) must not rely on them.
- **It is not policy enforcement:** no Cedar/OPA evaluation, approvals or hash-chained audit
  (#742).
- **One MCP session per deployment.** `runtime/adk@1.1.0` keeps one session per server, shared by
  every customer of the deployment. Only use upstreams whose session holds no per-user state;
  stateful (browser-type) upstreams wait for #747, which decides per-conversation sessions.
- **The relay authenticates the agent, not the agent's caller.** An agent's `POST /invoke` is not
  authenticated. Its port is published on loopback only, but other containers on the agents
  network can reach it too: its `Host` check stops browser DNS rebinding, not another
  container. Whoever reaches the endpoint can make the agent call the MCP tools it was granted,
  as any customer. Authenticated invocation is #780 (epic #742).

## State and secrets

Everything lives under `~/.agentbreeder/stacks/<name>-<hash of the project path>/` (0700), so two
checkouts with the same stack name never share anything: generated and imported service secrets
(`service-secrets/`, never mounted into agents), file-param copies, the generated Compose file,
and per environment the operator config, demo identities and deploy state. The generated operator
config can only describe a local demo environment, and `up` refuses to overwrite one you edited
(`OPERATOR_CONFIG_MODIFIED`).

## Limits (preview)

- Local Docker only; not a production deployment path.
- Demo identities come from a generated static provider.
- `customer_id` on an agent's `/invoke` is not authenticated (see the root README).
- MCP relays: an agent's call through the relay is verified with a mock MCP server and a fake
  gateway, not through `deploy` with a real model. No built-in MCP server is verified (#748), and
  browser-type service upstreams need the forward proxy (#747).
