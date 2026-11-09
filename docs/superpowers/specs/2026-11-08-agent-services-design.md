# Local backing services from a `stack.yaml` (design, r2)

Status: r3. r2 folded in the architecture and security reviews; r3 records the implementation
decisions of slice 2 (typed inputs wired by the stack; see "Review log").
Epic: #734 (sub-issues #735–#740). Stacked on #733 (agent.yaml build).

## Problem

An agent built from `agent.yaml` needs backing services before it can run: Postgres with pgvector,
a LiteLLM gateway, a trace collector, and sometimes project helpers. For Northwind these come from
a 130-line `stack/up.sh` plus a Compose file. The script copies provider keys from `~/.env`,
generates the DB password, the LiteLLM master key and the Phoenix secrets, starts Compose, mints a
Phoenix ingest key through its GraphQL API, and writes `operator.yaml` and `principals.yaml` from
heredocs with absolute paths. Every new project copies and edits it. None of it is declared,
versioned, hashed or checked, and the operator config has to match the Compose file by hand.

## Goal

A project declares its local backing services in `stack.yaml` next to `agent.yaml`.
`agentbreeder services up` starts them, generates and imports their secrets, and writes an
operator config for a **local demo environment**, which `build`, `deploy`, `eval` and `release`
then use through `--stack`. No setup script.

```
agentbreeder services up --environment dev               # shows the grant, asks to confirm
agentbreeder release agent.yaml --stack . --environment dev
```

## Non-goals

- **Not production infrastructure.** Local Docker only, like `deploy --local`. Production operators
  still write their own operator config and run real services. Docs and site say so (claim rule).
- **No change to `agent.yaml`.** It stays environment-neutral and grants nothing.
- **No change to how the real operator config is found.** It is still read from
  `AGENTBREEDER_OPERATOR_CONFIG` or `~/.agentbreeder/operator.yaml`, never from the repo.
- **Not a Compose replacement.** Only the fields a backing service needs.
- **Out of scope:** Studio (#718); replacing Northwind's ops dashboard or its offline/online eval
  scripts (separate follow-up).

## Trust model

The repo is **not** trusted to grant anything. `stack.yaml` and project service entries come from
the repo, so what they can cause is bounded and shown before it happens:

- The generated operator config lives **outside the repo**, in
  `~/.agentbreeder/stacks/<stack-id>/<env>/`, and can only express a local demo environment (see
  "Generated operator config"). A repo can't ship one that AgentBreeder picks up.
- Running `services up` and confirming the grant is operator consent. The grant (models,
  ceilings, demo identities, imported variable names, services with egress) is printed. It needs
  `--yes` or an interactive confirmation on first run and whenever it changes.
- Registry service versions are trusted code, like runtimes and tools: whoever adds a version
  chooses what runs. That is why the grant lists every service and its image.

`<stack-id>` is `<name>-<first 8 hex of sha256(absolute project path)>`, so two repos that both
use `name: northwind` never share secrets, ports, volumes or config.

## Design

### 1. Registry kind `service`

`service/<name>@X.Y.Z` is an immutable, hash-locked registry version. AgentBreeder ships
`postgres-pgvector`, `litellm` and `phoenix`, plus a tiny digest-pinned `echo` test service.
Projects add their own under `./registry/service/`. `service` is added to `registry.KINDS` and
`scaffold`. A test checks that `agent.yaml` rejects `service/` refs in every field.

```
registry/service/litellm/1.0.0/
  manifest.yaml     # kind: service, name, version, description
  service.yaml      # contract below
  files/            # read-only files mounted into the container (optional)
  Dockerfile, src/  # only for services built from source (optional)
```

`service.yaml` (strict YAML, unknown fields rejected):

```yaml
image: ghcr.io/berriai/litellm-database@sha256:35f2…   # or build: Dockerfile
provides: gateway/litellm        # optional contract; requires outputs url + master_key
command: [litellm, --config, /run/params/config, --port, "4000"]   # exec form only
user: image                      # image | host | "<uid>:<gid>" (fixed)
ports:
  http: {port: 4000, expose: agents, guard: litellm-auth}
params:
  config: {type: file, required: true}
  databases: {type: list, item: db_name, default: []}
env: {LITELLM_TELEMETRY: "False", DISABLE_ADMIN_UI: "True", NO_DOCS: "True"}
secrets:                         # mounted at /run/secrets/<name>, never in env
  master-key: {generate: token}
  gemini-api-key: {import: GEMINI_API_KEY}
  openrouter-api-key: {import: OPENROUTER_API_KEY, optional: true}
inputs:                          # provided by another service; wired in stack.yaml
  database-url: {type: secret}   # mounted at /run/secrets/database-url
health: {http: /health/liveliness}
egress: true                     # needs outbound internet (model providers); shown in the grant
outputs:
  url: {type: url, port: http}
  master_key: {type: secret, secret: master-key}
```

**Addresses are computed, not written.** For each port AgentBreeder knows three addresses:
`internal` (`<service>:<container port>` on the stack network), `agents` and `host`. A `url`
output names a port, and the consumer's position picks the address:
- service to service: `internal`;
- an agent container: `agents`;
- AgentBreeder on the host (the gateway URL for key minting, `eval_database`): `host`.

**`expose` decides reachability:**

| `expose` | Reachable from |
|---|---|
| `internal` (default) | Only the stack network |
| `agents` | Also agent containers. Agents join an `agentbreeder-<stack-id>-agents` network that only `agents` ports are attached to, so no `host.docker.internal` is involved and it works on Linux too |
| `host` | Also published on `127.0.0.1` with a recorded, allocated host port |

Binding to anything other than `127.0.0.1` is a hard error. Northwind's ops dashboard (staff
approvals) is `host` only, so agents can't reach it.

**Every HTTP port declares a `guard`:**
- a built-in header check (`litellm-auth` for the master key, `bearer` for a generated token);
- or `host-allowlist` (a Host and Origin check, the way `proxy.py` does it today).

A port without a guard is rejected. This covers browser and DNS-rebinding requests against loopback.

**Outputs are typed.**
- `type: url` outputs may be used anywhere a plain value is allowed.
- `type: secret` outputs may only feed a consumer's `secrets:`, an environment `bindings` entry,
  `eval_database`, or the gateway `master_key` (as a `file:` ref).
- A secret output is always used as a whole value, never interpolated into a string.
- Any other use is a validation error (taint check in slice 2).

Services never name each other. A service declares typed `inputs` (url or secret), and
`stack.yaml` wires each one to another service's output (`wire:`), so registry entries stay
reusable. A wire must match types: a secret input takes a secret output. Postgres-style services
declare per-item outputs (`per: databases`, built from a secret `template` with per-item
generated secrets) instead of callers rewriting URLs.

**Reference grammar:**
- `{params.NAME}` (a string/db_name param) and `{inputs.NAME}` (a url input) may appear in a
  service's `env` values (never in command arguments);
- `{SERVICE.OUTPUT}` (a url output) may appear in `environment.services` values;
- wires and bindings are bare references, `SERVICE.OUTPUT` or `SERVICE.OUTPUT.ITEM` for per-item
  outputs.

Templates are expanded in a single pass with no nesting, and `{{` escapes a brace. `params`,
`ports`, `secrets`, `self`, `item` and `address` are reserved and can't be names.

**Parameter types:**
- `db_name` matches `^[a-z][a-z0-9_]{0,62}$`.
- `type: file` params are copied into the stack state dir, hashed, and mounted read-only at
  `/run/params/<name>`. Symlinks and `..` are rejected, using `lock._no_symlinks`.

Generated tokens and passwords are hex, and passwords are percent-encoded in URLs.

**Container rules:**
- Default hardening is the same as `deploy --local`: read-only root, `cap_drop: ALL`,
  `no-new-privileges`, tmpfs `/tmp`.
- A service with a data volume declares `volumes: {data: /var/lib/postgresql/data}` and
  `user: "999:999"`. The volume is created and owned by that uid before start, so no capabilities
  are added. `privileged`, host networking, `cap_add` and undeclared bind mounts are rejected.
- Secrets reach containers the way `secret_files.materialise` does it: 0444 copies in a 0700
  per-generation directory, bind-mounted read-only.

**One-shot steps** run in a container from the service's image, on the stack network, with the
default hardening. Each one gets only the secrets it declares.
- `init` runs on every `up`, after health, and must be idempotent. For example, the postgres
  service creates the declared databases and roles and applies `init_sql`.
- `bootstrap` runs once per data-volume identity: it runs again after `down --volumes`. Its
  stdout becomes one secret (`writes_secret`).
- It runs with `--log-driver none` and `--rm`, its stdout is read over attach, and stderr is
  discarded.
- Empty or oversized output (more than 8 KiB) fails closed. The value is never echoed.

**Least privilege for databases:**
- The postgres service generates a superuser that is used only by `init`; it is never an output.
- Each declared database gets its own login role with rights on that database only.
- `databases.<name>.url` uses that role.

**Built from source** (`build: Dockerfile`):
- The build context is the hashed version copy.
- `FROM` lines must be digest-pinned.
- Build args, `--secret` and `--ssh` are refused.
- The image is tagged `agentbreeder-svc/<stack-id>-<service>:<content hash>`, so a pulled image
  can't stand in.

### 2. `stack.yaml`

```yaml
kind: stack
schema_version: 1
name: northwind
services:
  postgres:
    ref: service/postgres-pgvector@1.0.0
    with: {databases: [support, adk, litellm, phoenix], init_sql: stack/db/commerce.sql}
  litellm:
    ref: service/litellm@1.0.0
    with: {config: stack/litellm/config.yaml}
    wire: {database-url: postgres.databases.litellm}
  phoenix:
    ref: service/phoenix@1.0.0
    wire: {database-url: postgres.databases.phoenix}
  proxy: {ref: service/northwind-proxy@1.0.0, wire: {phoenix: phoenix.url}}
  ops: {ref: service/northwind-ops@1.0.0}
environment:
  gateway: litellm                       # must provide gateway/litellm
  models: [support-model, support-embedding, support-judge]
  ceilings: {budget_usd: 50, rpm: 300}
  services:
    traces_url: "{proxy.url}/v1/traces"
    tools_proxy_url: "{proxy.url}"
  bindings:
    DATABASE_URL: {ref: postgres.databases.support}
  eval_database: {ref: postgres.databases.support}
  agents:
    northwind-support:
      sponsor: support-lead@example.com
      review_by: "2027-06-30"
      candidate: true                    # also a demo candidate identity for release
```

**`stack.lock`** records:
- each service ref's hash;
- each `type: file` param as a `path:` entry with its hash;
- each service's image digest;
- the imported variable names per service.

**Under `--locked`:**
- a `path:` file param is accepted when its hash matches (unlike agent prompt paths);
- any change is `LOCK_STALE`, including a new imported variable name.

**Gateway alias check:** the gateway module (not services core) parses the LiteLLM config. An
`environment.models` alias that the config doesn't serve is an error. An extra alias in the
config is a warning.

### 3. Generated operator config

`services up` writes `~/.agentbreeder/stacks/<stack-id>/<env>/operator.yaml` and
`principals.yaml` (0600, inside 0700 directories, opened with `O_NOFOLLOW` and atomic replace).
It can only contain:
- `enforcement: enforce`, `targets: [local]`;
- one `static` provider (`demo`), with each agent's principal enabled, `require_sponsor` on and the
  stack's sponsor and review date;
- the gateway (whose `master_key` is a `file:` ref into the stack's service secrets);
- `models`, `ceilings`, `services`, `bindings`, `eval_database`, `agents`.

It never contains `pdp`, `deployer`, `maker_checker`, `runtime_enforcement`, `mcp_egress` or
non-static providers. Deploy and release output says "demo identities (generated by services up)".

`up` records the digest of what it wrote in the stack state. On the next run it overwrites the
file only if the current digest matches. Otherwise it stops with `OPERATOR_CONFIG_MODIFIED`.

**Using it:** `--stack DIR` on `build`, `deploy`, `eval`, `release`, `status`, `remove` and
`reconcile` resolves DIR to its stack id and uses that stack's operator config and state dir.
`agentbreeder services env [DIR]` prints the two `export` lines instead.

### 4. State and secrets

```
~/.agentbreeder/stacks/<stack-id>/
  stack.db                  # owned secrets, ports, digests, bootstrap markers, written-config digest
  compose.yaml              # generated, 0600, file paths only, `$` escaped
  params/                   # hashed copies of file params
  service-secrets/          # master key, DB superuser and role passwords, provider keys, minted keys
  <env>/operator.yaml, principals.yaml
  <env>/state/              # the deploy state dir (store, audit, secrets/ for bindings only)
```

**Service secrets:** service-internal secrets live in `service-secrets/` and can't be mounted into
an agent.

**Agent-facing secrets:** only the secret outputs named in `bindings` or `eval_database` are
written, through `set_secret`, into the stack's own `<env>/state/secrets/`.

**Reuse rule:** a secret is reused only if `stack.db` records that `up` generated or imported it.
A pre-seeded file is refused (`SECRET_NOT_OWNED`).

**Data without its password:** if a data volume exists but its generated password is gone, `up`
fails rather than regenerating it.

**Imports:**
- read from the process environment, or from `--env-file` with a strict `KEY=value` parser (the
  file is never sourced);
- only for the names that services declare.

**Denylisted imports.** Well-known cloud, VCS and CI credentials can't be imported:
`AWS_*`, `GOOGLE_APPLICATION_CREDENTIALS`, `AZURE_*`, `GITHUB_TOKEN`, `GH_TOKEN`, `NPM_TOKEN`,
`*_SSH_*` and so on.

**Egress:** project services have no outbound network unless they declare `egress: true`. That
shows in the grant.

### 5. Commands

```
agentbreeder services up     [DIR] --environment ENV [--locked] [--env-file FILE] [--yes]
agentbreeder services status [DIR] [--json]
agentbreeder services logs   [DIR] NAME [--tail N]     # known secret values redacted
agentbreeder services env    [DIR]
agentbreeder services down   [DIR] [--volumes --yes]
agentbreeder secret set NAME (--from-env VAR | --generate)   # usable on its own (slice 1)
```

**`up` runs these steps:**
1. Resolve and lock the refs. Validate params, references, taint rules, the dependency graph (no
   cycles) and the gateway alias check.
2. Show the grant and confirm it.
3. Import and generate secrets.
4. Write the Compose file and run Compose with:
   - `-p agentbreeder-<stack-id>` and an explicit `-f`;
   - `--project-directory` set to the state dir;
   - a sanitised environment: no `COMPOSE_*` variables and no `.env` auto-load.
5. Wait for health, then run `init` and `bootstrap` steps.
6. Write the operator config and print the next command.

**Idempotency:**
- Each service's config hash covers:
  - its registry hash;
  - its resolved params;
  - its file-param hashes;
  - the resolved inbound outputs.
- That hash goes in a container label, and only services whose hash changed are recreated.
- Dependents whose inputs changed are recreated too.

**`down`:**
- keeps volumes and secrets;
- with `--volumes --yes`, removes only volumes labelled with this stack id;
- refuses while an active deployment in the stack's state dir is still running.

The stack has its own state dir, so this is checkable with the existing store.

`doctor` checks that Docker Compose v2 supports `--wait`.

### 6. What changes for Northwind

| Today | After |
|---|---|
| `stack/up.sh`, 130 lines | `stack.yaml`, about 40 lines |
| `compose.yaml`, `litellm/entrypoint.sh`, `Dockerfile.host` | Built-in services plus `northwind-proxy` and `northwind-ops` entries |
| Heredoc `operator.yaml` and `principals.yaml` | Generated under `~/.agentbreeder/stacks/` |
| Agent uses the `support` superuser | Agent uses a role limited to the `support` database |
| Agent can reach the ops dashboard | Ops is `expose: host`, so agents can't reach it |

The Northwind ops dashboard gains a generated bearer token, which also serves as its guard.

### 7. MCP servers through a relay (#743, folded into #734 on 2026-11-08)

**Owner decisions (2026-11-08):** MCP support ships inside #734. Credentials live in a
stack-hosted relay, never in the agent. Live verification targets GitHub, Slack, Supabase,
Google Workspace and Stripe (owner supplies test credentials) plus Playwright (needs none).
RBAC/ABAC on tool calls is #742, which adds a policy check at this same relay.

**Why a relay.** Agents built from `agent.yaml` use `model_access`, and `mcp_http` runtime
enforcement refuses `model_access` (`MODEL_ACCESS_UNSUPPORTED`) and stack networks. So no MCP call
from a built agent can reach the egress sidecar until #742. The relay gives two guarantees now,
both outside the agent process:
- **The agent never holds the upstream credential.** It holds only a per-deployment relay token.
- **The relay forwards only the tools granted to that deployment.**

The relay does not evaluate Cedar/OPA policy, approvals or risk tiers; that is #742. Docs must
not call it policy enforcement.

#### 7.1 Registry kind `mcp`

`mcp/<name>@X.Y.Z` is immutable and hash-locked like every other kind (`mcp` is added to
`registry.KINDS`). `agent.yaml` may reference it only under `mcp:`, and `stack.yaml` only under
`mcp:`. Files: `manifest.yaml` and `mcp.yaml` (strict YAML):

```yaml
upstream:                        # exactly one of remote / service
  remote: {url: "https://api.example.com/mcp"}      # https only; egress through the relay
  # service: {ref: service/playwright-mcp@1.0.0, port: http, path: /mcp}
auth:                            # how the relay authenticates upstream
  type: bearer                   # none | bearer | header | oauth_refresh
  secret: token                  # a relay secret (imported or service-provided), never in agent.yaml
  # header: {name: X-Api-Key, secret: api-key}
  # oauth_refresh: {token_url: https://oauth2.example.com/token, client_id: client-id,
  #                 client_secret: client-secret, refresh_token: refresh-token, scopes: [...]}
secrets:                         # same grammar as service.yaml secrets (import/generate/optional)
  token: {import: GITHUB_MCP_TOKEN}
tools:                           # the only tools that can ever be granted
  list_issues: {effect: read}
  create_issue: {effect: write}
```

- `effect: read|write` is required on every tool. It's recorded for #742 (risk tiers,
  approvals). In #734 it shows in the grant.
- `remote` upstreams resolving to private, loopback or link-local addresses are refused, using the
  sidecar's resolution check.
- A `service` upstream must be a stack service port with `expose: internal`, so agents can't
  bypass the relay. Stdio-only servers are packaged as a service with a bridge, or not at all.
- The import denylist still applies: `GITHUB_TOKEN` and `GH_TOKEN` can't be imported, so the
  GitHub entry imports `GITHUB_MCP_TOKEN`.

#### 7.2 `stack.yaml` and the grant

```yaml
mcp:
  github: {ref: mcp/github@1.0.0}
  browser: {ref: mcp/playwright@1.0.0}   # pulls in its service upstream automatically
environment:
  agents:
    northwind-support:
      mcp: {github: [list_issues]}       # per-agent tool grant, shown in the grant
```

- Each `mcp:` entry becomes one relay container (`service/mcp-relay@1.0.0`, built from source
  under the strict Dockerfile subset, `expose: agents`, guard `bearer`). One credential per
  container. Relays with a remote upstream get `egress: true`. Relays with a service upstream
  don't.
- The grant shows, per server: the upstream (URL or service), the auth type, the imported
  variable names, egress, and per agent the granted tools with their effect.
- The generated operator config gains `mcp_relays: {<name>: {url, grants_dir}}`. It's added to
  the list of fields that config may contain. Nothing else about the config changes.

#### 7.3 Agent side

```yaml
mcp:
  - ref: mcp/github@1.0.0       # deploy finds the stack entry with this ref (§7.7.6)
    tools: [list_issues]
```

- `build` checks that `tools` is a subset of the version's declared tools. It writes them into
  `agent.config.json` and the manifest (`mcp_access: [{server, tools}]`).
- **Deploy**, after the gate and approval, does the following:
  - It refuses (`MCP_SERVER_UNAVAILABLE`) when the environment has no such relay. It refuses
    (`MCP_TOOL_NOT_GRANTED`) when the requested tools aren't in that agent's grant.
  - It mints a per-deployment token, the same way as `gateway_keys`. The value goes only into
    the deployment's private secret file. The relay gets a grant file
    `<grants_dir>/<sha256(token)>.json` holding `{deployment, agent, environment, server, tools}`,
    written atomically (0444, §7.7.4). The relay reads it read-only through a bind mount.
  - On remove or disable, it deletes the grant file before reporting success. `reconcile` deletes
    grant files with no active deployment.
- Deploy refuses `mcp_access` under `runtime_enforcement: mcp_http` (`MCP_RELAY_UNSUPPORTED`), like
  `network`.
- **Runtime:** the ADK runtime becomes `runtime/adk@1.1.0`, which also folds in the #741 fixes.
  It builds one ADK `McpToolset` per server over Streamable HTTP. The URL comes from
  `AGENTBREEDER_MCP_<SERVER>_URL` and the token from its secret file. `tool_filter` is set to
  the granted tools. That filter is a convenience; the relay is the boundary.

#### 7.4 Relay behaviour

1. **Auth.** Hash the bearer token and look up the grant file by that hash (see §7.6, item 10).
   If there's no file, return 401. The agent's `Authorization` header is never forwarded.
2. **Parse** with `egress/protocol.parse_request` (vendored into the relay version). It accepts
   only `initialize`, `notifications/initialized`, `notifications/cancelled`, `ping`,
   `tools/list` and `tools/call`. Batches, resources, prompts and sampling are refused.
3. **`tools/list`.** Filter the upstream response to the deployment's granted tools.
4. **Tool-definition pinning.** `services up` probes each upstream and hashes every declared tool
   (name, description, input schema). The hashes go in `stack.lock`; `--locked` refuses any
   change (`LOCK_STALE`). The relay hides a tool whose live definition hash differs, and refuses
   calls to it (`TOOL_DEFINITION_CHANGED`). `services status` shows it.
5. **`tools/call`.** A call to a tool that isn't granted gets a JSON-RPC error and never reaches
   upstream. A granted call goes upstream with the injected credential (`oauth_refresh` access
   tokens are cached until expiry). Response headers go through an allowlist.
6. **Limits:** a body size limit and calls per minute per token, with the sidecar defaults.
7. **Logs:** one JSON line per request with deployment, method, tool, decision and latency. Never
   arguments, results or credentials. Hash-chained audit integration is #742.

#### 7.5 Built-ins and claims

| Built-in | Upstream (confirmed during verification) | Auth |
|---|---|---|
| `mcp/github` | remote | bearer (fine-grained PAT) |
| `mcp/stripe` | remote | bearer (restricted key) |
| `mcp/supabase` | remote | bearer (access token) |
| `mcp/slack` | remote or service | decided during verification |
| `mcp/google-workspace` | service (digest-pinned community server) or remote | `oauth_refresh` |
| `mcp/playwright` | service (`service/playwright-mcp`) | none |

The tool list for each built-in is limited to what the verification run exercised, plus tools
listed upstream that the owner approves. A built-in counts as **verified** only after a real call
through the relay from a deployed agent. Docs and the site list verified ones only; the others
ship marked `unverified` in their manifest description.

#### 7.6 Security requirements (r5 security review, all normative)

**Origin and consent:**
1. **Origin.** The grant labels every `mcp` entry by origin: built-in, project, or
   `AGENTBREEDER_REGISTRY_PATH`. For each credential it prints "credential `VAR` will be sent to
   `<scheme://host:port>`". A project-origin or env-origin entry whose remote upstream carries
   auth is refused unless the operator passes `--allow-project-mcp NAME`; `--yes` alone never
   allows it. `stack.lock` records each entry's origin, upstream origin and `token_url` host, and
   a change requires consent again (`LOCK_STALE` under `--locked`).
2. **Relay image.** `service/mcp-relay` resolves only from the built-in root. It can't be named,
   shadowed or overridden by `stack.yaml`, `./registry` or `AGENTBREEDER_REGISTRY_PATH`.

**Relay protocol handling:**

3. **Sessions.** The relay mints its own `Mcp-Session-Id`s and maps each one to an upstream
   session, keyed by the token hash. A session id presented by another token is refused.
   `Last-Event-ID` is dropped; streams are not resumable through the relay. Unknown session
   ids get 404 (§7.7.7).
4. **Server-to-client traffic.** The relay parses every response event, JSON or SSE. It passes
   only the JSON-RPC response matching the request id, plus `notifications/progress` and
   `notifications/message`. It drops `sampling/*`, `elicitation/*`, `roots/*` and
   `notifications/tools/list_changed`. It rewrites the `initialize` result's capabilities to
   `{tools: {}}`. `GET` (the standalone server stream) is refused.
5. **Pinning.**
   - The hash covers the whole canonical tool object, including `title`, `annotations`,
     `outputSchema` and `_meta`.
   - The relay answers `tools/list` from the locked definitions and fetches upstream only to
     check them, following `nextCursor` up to 20 pages.
   - The grant shows each granted tool's definition hash.
   - Docs state that the first lock trusts the upstream (trust on first use).
6. **Headers.** The body is authoritative. `Mcp-Method` and `Mcp-Name` are optional for the
   relay, and when present they must equal the body. The relay sets them correctly upstream.

**Network:**

7. **SSRF and rebinding.** The relay calls `resolve_destination` and pins the address on every
   upstream connection, the way the sidecar does with `_PinnedHTTPS`. That applies to the MCP
   upstream and to `token_url`. It refuses redirects, strips `*_PROXY` from its environment, and
   accepts https on port 443 only for remote upstreams, unless the operator config allow-lists a
   port.
8. **Service upstreams.** A service upstream joins only a private network shared with its relay,
   never the `stack` network. A browser service (`mcp/playwright`) has no direct egress. Its
   outbound traffic goes through a built-in filtering forward proxy that refuses private,
   loopback, link-local and metadata addresses (checked after resolution), and the host's
   published ports. This is required hardening, not an optional exception.

**Credentials and tokens:**

9. **OAuth refresh.**
   - Cached access tokens live in memory only.
   - Providers that rotate refresh tokens are refused in #734.
   - Token-endpoint bodies and `error_description` are never echoed or logged.
10. **Grant files.**
    - Each relay has its own `grants_dir`, and each file carries `server`, which must match.
    - The relay re-reads the file on every request; there is no cache.
    - It accepts only names matching `^[0-9a-f]{64}\.json$`.
    - Tokens are 256-bit random hex. A presented token that is longer than 128 characters or
      contains characters outside `[0-9a-f]` is rejected before it is hashed.
    - The lookup is by sha256 filename. That is not a comparison, so the spec makes no
      constant-time claim.
    - The relay runs as a fixed uid. For file permissions, see §7.7.4.
    - `disable` (principal-wide) deletes the grant files for every deployment of that principal
      before it reports success.
11. **Relay credential and limits.**
    - The upstream credential is a mounted 0400 file, never an environment variable.
    - The relay enforces per-token concurrency (default 4).
    - Each response has a wall-clock cap (default 120 s).
    - Rewritten responses (`tools/list`, `initialize`) are buffered with a 1 MiB cap.
    - Criterion 10's canary check covers the relay's `docker inspect` and the generated Compose
      file too.

**Logging and claims:**

12. **Logs.** Every field is JSON-encoded. An ungranted tool name is logged as `ungranted` plus
    its hash. Upstream error bodies and `WWW-Authenticate` are never logged.
13. **Claims.** Docs must say all of the following:
    - Tool arguments are unconstrained: a granted `list_issues` reaches any repository the
      credential can see.
    - Upstream results reach the agent unfiltered, so prompt injection through tool output is not
      mitigated.
    - "The agent never holds the credential" says nothing about the credential's scope.
    - `effect` labels from project-origin entries are untrusted, and #742 must not rely on them.

#### 7.7 Architecture requirements (r5 architecture review, normative; override earlier text)

1. **Pinning without credentials before consent.**
   - `up` confirms in two phases. Phase 1 is the grant: upstreams, credential destinations,
     per-agent tools. Imports and the relay start happen only after phase 1.
   - Phase 2 runs inside the relay: a probe mode, started with a stack-only admin token, never
     from the host. It shows the definitions it found (hash, description, schema) and asks
     "pin these?".
   - Pins are written to a file on the relay's mount that the relay reloads, so re-pinning
     doesn't recreate it. `--yes` accepts both phases only for built-in origins.
   - `services up --repin` shows a diff with consent, reporting "missing upstream" separately
     from "changed".
2. **Per-environment grants.**
   - Grant files live in `~/.agentbreeder/stacks/<id>/grants/<env>/<server>/`.
   - Each file carries `server` and `environment`, and the relay checks both.
   - `reconcile` and `disable` touch only their own environment.
3. **Grant lifecycle** (state-backed, unlike the stateless `gateway_keys` alias):
   - The deployment record stores the current token hash per server.
   - Before minting, and on any failure in `_run`, delete every grant file whose `deployment`
     matches. A retry or crash never leaves an earlier token valid.
   - `reconcile` also deletes grants whose hash isn't the recorded one.
   - The MCP token file is part of `secret_files.present` for the restart path.
4. **File permissions.** Grant files and the pin file follow the existing service-mount
   convention: 0444 files in 0755 staging directories (`services/state.py`). They hold only a
   hash, deployment, agent, environment, server and tools, never a token.
5. **Where the grant lives.**
   - The environment's agent entry in the generated operator config gains
     `mcp: {<server>: [tools]}`.
   - `MCP_SERVER_UNAVAILABLE` and `MCP_TOOL_NOT_GRANTED` are checked in `_deploy_requirements`,
     so `preflight` and `release --rollback` see them.
   - The relay intersects each grant file's tools with its configured grant, so a narrowed grant
     takes effect at once.
   - `up` refuses to drop an `mcp:` entry, or narrow a grant, while an active deployment uses it
     (as `down` does).
6. **Contracts.**
   - `Manifest.mcp_access` is excluded from `digest()` when unset, like `model_access`, so
     existing digests don't change.
   - `mcp_access` is the relay contract; `capabilities.tools` stays the `mcp_http` contract. #742
     unifies them, with `capabilities` deriving from `mcp_access`.
   - `agent.yaml` names only `ref`; there is no `server:` field. Deploy resolves the stack entry
     whose ref matches. Zero matches gives `MCP_SERVER_UNAVAILABLE`, and two or more give
     `MCP_SERVER_AMBIGUOUS`. This keeps the non-goal of no stack names in `agent.yaml`.
   - The cross-repo grep for `mcp_access` / `mcp` in agentbreeder-cloud happens before the PR.
7. **Protocol scope** (verified against google-adk 2.12.0 and mcp 2.3.0):
   - **Handshake-era protocol versions only, on both sides.** Upstreams that support only the
     2026-07-28 stateless protocol (`server/discover`, no sessions) are refused.
   - **One page for `tools/list`.** ADK calls it once with no cursor loop, so the relay returns
     every granted tool in one page with no `nextCursor`.
   - **`DELETE` closes the upstream session.** ADK's default `terminate_on_close` sends `DELETE`,
     and the relay closes the mapped upstream session, so browser contexts don't leak.
   - **Unknown or expired session ids get 404,** which makes the client initialize again. A relay
     restart, or recreation after a credential rotation, therefore recovers. An acceptance case
     covers this.
   - **ADK client settings.** The runtime sets `tool_name_prefix=<server>_` and uses
     `header_provider` for the token. Per the installed library, mcp 2.3 sends
     `Mcp-Method`/`Mcp-Name`.
8. **Credential rotation.** `up` re-reads imported values. A changed value makes a new mount
   generation and recreates the relay, and live sessions get 404 and re-initialize.
9. **Google Workspace.** It ships only with an upstream that accepts a bearer or OAuth access
   token from the relay: remote, or a server with no token store of its own. Otherwise it is
   unverified.
10. **Server names.** `stack.yaml` `mcp:` keys match `^[a-z][a-z0-9_]{0,31}$`, so
    `AGENTBREEDER_MCP_<NAME>_URL` is always a valid variable name.

## Acceptance criteria

1. **No script, no exports:** from a clean support-agent checkout, run `services up --environment
   dev --yes` and then `release agent.yaml --stack . --environment dev`. It passes the Northwind
   eval gate (61/61) with no `up.sh`, no Compose file in the repo and no exports.
2. **Idempotent:** a second `up` recreates nothing.
3. **Data survives:** `down` then `up` keeps data and secrets.
4. **Volumes are scoped:** `down --volumes --yes` removes only this stack's labelled volumes.
5. **Stacks are isolated:** two checkouts with the same stack name run side by side with no
   collisions.
6. **No secret leaks.** Canary secret values never appear in:
   - AgentBreeder's own output;
   - the generated Compose file, the operator config and `status --json`;
   - container labels, or `docker inspect` of services and agents;
   - the bootstrap container's logs.

   Third-party `services logs` output is redacted only on a best-effort basis.
7. **Refusals.** Tests show that each of these is refused:
   - a repo-supplied operator config;
   - a denylisted import;
   - a secret output used in `env`;
   - an unguarded HTTP port;
   - a non-loopback bind;
   - a pre-seeded secret.
8. **Tests:** unit tests for parsing, references, taint, the graph, ports and config writing; one
   opt-in real-Docker test with the `echo` service; at least 95% coverage on new modules.
9. **Docs:** `docs/services.md` and the site describe it as a local-only preview.
10. **MCP relay.** Tested against a mock upstream that counts calls, then live:
    - a deployed agent calls a granted tool through the relay on every verified built-in;
    - an ungranted tool is absent from `tools/list`, and calling it never reaches upstream;
    - a token from another deployment, or from a removed one, gets 401;
    - a session id or stream belonging to another deployment is refused;
    - a server-initiated `sampling/createMessage` injected upstream never reaches the agent;
    - the upstream credential (a canary) never appears in the agent container's environment,
      files, `docker inspect` output, or the relay's logs.
11. **Tool definition change:** a changed upstream tool definition is hidden and refused, and
    `status` reports it; a tool missing upstream is reported separately; `--locked` refuses both;
    `up --repin` shows the diff and asks before accepting it. A relay restart mid-run recovers
    (404, then initialize again).
12. **Service upstream:** `mcp/playwright` runs end to end with its upstream on `expose: internal`
    and the default hardening (or a documented, tested exception).

## Decisions (former open questions)

1. **Compose behind one module.** It never builds from repo paths, its generated file holds paths
   only, and it is labelled for `down`.
2. **`services up` only writes demo identities.** Real identities stay hand-written in the real
   operator config.
3. **Online evals stay a Northwind project service.** Replacing the ops scripts with AgentBreeder
   features is a separate follow-up.
4. **Gateway alias check:** a missing alias is an error, an extra one a warning, implemented in a
   gateway module.

## Slices

1. **#736:** `secret set --from-env / --generate`, the strict env-file parser and the denylist.
2. **#737:**
   - the `service` kind and `service.yaml` and `stack.yaml` parsing;
   - typed outputs, the reference grammar and taint rules;
   - `stack.lock` and the gateway alias check;
   - the agent.yaml rejection test.
3. **#738:**
   - the stack state dir and Compose module, and port allocation and networks (including the agents
     network and the deploy-side change to join it);
   - `init` and `bootstrap`, and generated operator config with the digest rule;
   - `--stack` and `services env/status/logs/down`;
   - the `echo` test service and the opt-in Docker test.
4. **#739:** built-in `postgres-pgvector` (roles, init), `litellm` and `phoenix` (bootstrap). The
   Northwind project services and the ops token. Remove `up.sh` and Compose from the example and
   the support-agent repo, then the acceptance run.
4a. **MCP contracts:** the `mcp` kind and `mcp.yaml`; `stack.yaml` `mcp:` and per-agent
   grants in the operator config; `agent.yaml` `mcp:` and `mcp_access`; deploy, remove, disable
   and reconcile grant lifecycle against a fake relay.
4b. **MCP relay:** `service/mcp-relay` (sessions, filtering, pinning with the two-phase
   confirmation, OAuth, SSRF pinning, caps); mock-upstream tests and an opt-in real-Docker test.
4c. **Runtime:** `runtime/adk@1.0.5` (#741 only), then `runtime/adk@1.1.0` (the MCP toolset).
4d. **Filtering forward proxy** for browser-type service upstreams (§7.6.8).
4e. **MCP built-ins:** the six entries; live verification with owner credentials.
5. **#740:** docs and site.

Maintenance note: built-in service digests are a CVE and update duty. They change only through a
reviewed release, and `stack.lock` records them.

## Review log

r2 folds in the architecture review (B1–B5, S1–S9, nits) and the security review (C1–C2, H1–H5,
M1–M6, L1–L3) of r1:
- **Config location:** the generated config moves out of the repo, and the default path stays
  unchanged.
- **Grants:** bounded and confirmed.
- **Outputs:** computed address scopes, typed outputs and the taint rule.
- **Network:** `expose` levels and an agents network; port guards.
- **Locking:** file params locked and copied.
- **Secrets:** service secrets namespaced, with ownership-checked reuse; an import denylist; egress
  off by default.
- **Databases:** least-privilege DB roles.
- **One-shot steps:** bootstrap logging off, and `init` added.
- **Containers:** exact hardening deltas, and source builds pinned.
- **Isolation:** stack id from the project path; label-scoped `down`; Compose hygiene.
- **Scope:** the ops script removal moved out of scope.

r3 (slice 2 implementation):
- **Inputs and wiring:** services declare typed inputs and the stack wires them, instead of
  service entries naming other services.
- **References:** per-item references drop the `.url` suffix.
- **Image digests:** these are recorded through the service version hash. A service's image digest
  lives in its `service.yaml`.
- **Gateway contract:** `provides: gateway/litellm` also requires a `config` file param, which the
  alias check reads.
- **Operator-only outputs** (security review of slice 2): the gateway's `master_key` output must be
  `operator_only`. Such an output can never be wired to a service, bound into an agent or used as
  `eval_database`: only AgentBreeder reads it, to mint per-agent keys.
- **Operator-only follows the secret:** every output that exposes an operator-only secret, whole
  or inside a template, must itself be operator-only. The master key output must name its secret.
- **Services-only outputs** (security review of slice 4): a secret output marked `services_only`
  can be wired to another service's secret input, but never bound into an agent or used as
  `eval_database` (`OUTPUT_SERVICES_ONLY`). Phoenix's `ingest_key` is one: it is a system key that
  reads every trace, so only the trace proxy holds it and agents send traces through the proxy.
  As with operator-only, every output exposing such a secret must be at least as restricted.
- **No templated arguments:** commands, health checks and one-shot steps can't contain
  placeholders. Values reach a service through `env`, which is never parsed as a command line;
  this rules out re-parsing by interpreters and wrappers, which no denylist could. As defence in
  depth, argv[0] can't be a shell or wrapper (`sh`, `bash`, `ksh`, `busybox`, `env` and so on).
  The reference grammar above is narrowed accordingly: `{params.NAME}` and `{inputs.NAME}` are
  valid in `env` only.

r4 (slice 3 implementation, verified against Docker 27.5 / Compose 2.33):
- **`expose` takes a list:** `[agents, host]` serves both agent containers and AgentBreeder on
  the host (a gateway it mints keys on, a database used for eval resets). A single value still
  works.
- **Reachability is per container:** Docker networks don't filter ports, so a service with any
  `agents` port is reachable from agents on all its ports. Keep agent-facing and internal-only
  listeners in separate services when that matters.
- **Host ports imply egress:** Docker can't publish ports from an internal network, so a service
  with a `host` port also joins the stack's non-internal `edge` network. The grant shows
  `egress: host port` for it.
- **Guards are declarations:** the service enforces its guard (LiteLLM's key check, a bearer
  check); AgentBreeder only requires one and shows it. The `echo` test service enforces nothing.
- **HTTP health:** probed from inside the stack network by a one-shot container from the pinned
  `python:3.12-slim` image the egress sidecar already uses. `health.command` uses Compose
  healthchecks and `up --wait`.
- **Agents network:** Docker refuses the default bridge plus a user network at create, so the
  agent container is created as before (bridge, loopback publish) and then connected to
  `Environment.network` before it starts. Unset, the create call is unchanged.
- **Start order:** services start one at a time in dependency order, each followed by its health
  check, `init` and (once per volume identity) `bootstrap`; a service that mounts what its own
  bootstrap minted is recreated once.
- **Mount generations:** a service's `/run/secrets` directory is named by an HMAC (per-stack
  random key) of its values, so unchanged values keep the same path and Compose recreates
  nothing, and the path reveals nothing about the values.
- **Review fixes (independent review + security review of 11238fb):** environments live under
  `envs/<env>/` (a name like `mounts` could otherwise collide with the stack's own dirs); the
  grant also lists wires, agent service URLs, bindings and `eval_database`, so rewiring a secret
  needs reconfirmation; `stack.lock` and host ports are written only after confirmation;
  `SECRET_LOST` applies only to a secret up recorded before; the probe image is pulled before the
  first probe; status and logs use the authorised Docker context; one-shot steps have a real
  deadline and are removed by name when they time out or exceed 8 KiB; the Dockerfile check joins
  continuations, refuses parser directives, `ONBUILD` and `ARG`, and pins `--from` and mount
  `from=`; `Environment.network` is refused together with runtime enforcement.
- **Dockerfile subset (security review of 0c62ab0):** instead of re-implementing BuildKit's
  parser, a built service's Dockerfile must stay inside a small unambiguous subset
  (`services/dockerfile.py`): ASCII only, one instruction per line (no continuations or heredocs),
  FROM/RUN/COPY/WORKDIR/ENV/USER/EXPOSE/LABEL/CMD/ENTRYPOINT/HEALTHCHECK/STOPSIGNAL only, an
  option allowlist per instruction, and `FROM name@sha256:<64 hex>`, a named stage or `scratch`.

r5 (2026-11-08) folds #743 in as §7 (MCP relay, owner decision). It also folds in the
security review (4 high, 6 medium, 2 low, now §7.6) and the architecture review (2 blockers,
8 should-fix, 2 nits, now §7.7). §7.7 overrides earlier §7 text where they differ. Postgres
hardening was verified: it needs a `tmpfs:` field and a CHOWN-only one-off volume chown (see the
plan's Risks section).

r6 (#750, security review of #749): the agent and the ops service shared the `support` owner
role, so a compromised agent process could approve its own cancellation request.
- **`service/postgres-pgvector@1.1.0`:** `roles` + `roles_database` add login roles that can
  connect only to that database, own only a schema named after themselves (so their unqualified
  CREATEs land there; PostgreSQL 16 closes `public` to CREATE) and get nothing else until the
  owner's `init_sql` grants it. The owner is a member of each, so it can grant to them and create
  objects for them. `postgres` and `template1` lose CONNECT for PUBLIC. Output `roles.<name>`.
- **Output templates** may use the service's own `{params.X}` (the role's database); every
  template placeholder is now checked when `service.yaml` loads.
- **`runtime/adk@1.0.6`:** no DDL when the owner pre-created all runtime tables (a partial set is
  refused, since an unqualified CREATE would shadow them in the role's schema), and no
  `CREATE SCHEMA` when the sessions schema exists.
- **Why the owner creates the tables the agent writes:** the ops role must never touch objects
  the agent owns. An agent-owned table could carry a trigger, or a column type with an implicit
  cast, whose function would run with the ops role's rights.
- **Northwind:** agent = `support_agent` (SELECT on orders/catalog, column-level INSERT of
  pending requests, inserts into its runtime tables); ops = `support_ops` (decides requests,
  eval tables in its own schema); the owner only resets eval fixtures. `tool/cancel-order@1.0.1`
  drops `FOR UPDATE` (a row lock needs UPDATE rights). Verified from scratch: 61/61, and the
  agent role is refused on approve, order change, forged insert, audit delete, trigger drop,
  public CREATE, CREATE SCHEMA and reading customers; a staff approval through the ops API works.
- **`services up --update`** accepts new hashes for edited project files (never a published
  version); lock errors name the right lock file and command.

r7 (slice 4a, #744, implementation decisions):
- **`Manifest.mcp_access` carries `{ref, tools}`,** not a server name (§7.3 sketch said
  `server`): deploy resolves the relay from the ref (§7.7.6), so a manifest stays
  environment-neutral. It needs `model_access` (built agents only) and is left out of the
  digest when unset.
- **The grant belongs to the agent's identities:** deploy finds the operator environment's agent
  entry whose `identity` or `candidate_identity` equals the manifest's, so the release candidate
  gets the same grant.
- **The token file is the record (§7.7.3):** instead of a new store column, `reconcile` keeps a
  grant only if its deployment is active and the sha256 of the deployment's private token file
  equals the grant's name. Deploy deletes the deployment's grants before minting; failure,
  remove and disable delete them first (a deletion failure fails the remove).
- **Runtime contract:** `agent.config.json` gains `mcp: [{name, version, tools}]`; the container
  gets `AGENTBREEDER_MCP_<NAME>_URL` and `AGENTBREEDER_MCP_<NAME>_TOKEN_FILE`, where `<NAME>` is
  the mcp version's name (upper case, `-` as `_`), not the stack key.
- **`services up` refuses `mcp:`** (`MCP_RELAY_UNAVAILABLE`) until slice 4b adds the relay and
  writes `mcp_relays` into the generated operator config; grants nobody serves would otherwise
  look like working deploys. `mcp` versions aren't locked into `stack.lock` yet (4b).
- Cross-repo grep (agentbreeder-cloud, 2026-11-08): no use of `mcp_access`, `model_access` or
  `mcp_relays`; no companion change.
