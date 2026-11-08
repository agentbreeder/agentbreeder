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
