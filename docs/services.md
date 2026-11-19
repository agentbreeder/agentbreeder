# Local backing services: `stack.yaml` (preview)

> **Local-only preview (#734).** `agentbreeder services` starts the services an agent needs
> (Postgres, a model gateway, a trace collector, project helpers) on the local Docker engine and
> writes a demo operator config for them. It is not production infrastructure. Verified on macOS
> with Docker Desktop: `src/agentbreeder/examples/northwind` started from scratch with `services up`, and
> `agentbreeder release` passed its 61-case eval gate (61/61, 2026-11-08). MCP servers run behind
> a built-in relay (see [MCP servers](#mcp-servers-through-a-relay)). An agent built from
> `agent.yaml` on `runtime/adk@1.1.0` calls tools through it. That is verified in Docker against
> a mock MCP server and a fake LLM gateway. The built-in `mcp/github` and `mcp/playwright` are
> verified live through a real `deploy`, with a scripted model (#748, 2026-11-09).

An agent built from `agent.yaml` needs backing services before it can run. A project declares
them in `stack.yaml` next to `agent.yaml`:

```
agentbreeder example northwind   # copies it to ./northwind
cd northwind
agentbreeder services up --environment dev --env-file ~/.env   # shows the grant; --yes confirms
agentbreeder release agent.yaml --stack . --environment dev    # build → candidate → evals → promote
agentbreeder services down --purge --yes                       # remove everything up made
```

`agent.yaml` doesn't change and still grants nothing. `services up` writes the operator config
for a **local demo environment** outside the repo, and `--stack DIR` points `build`, `deploy`,
`eval`, `release`, `status`, `remove` and `reconcile` at it. When `--stack DIR` cannot be resolved, every command that takes it (except `cluster` and `watch`, whose `--stack` names the project) prints a coded line on stderr (for example `STACK_MISSING`, `STACK_INVALID`, `STACK_ENVIRONMENT`, `STACK_NOT_UP`, or `STACK_UNREADABLE` when the directory cannot be read) and, with `--json`, `{"schema_version": 1, "error": {"code", "message"}}` on stdout; exit 2 (on `main`, not in a published preview).

## `stack.yaml`

```yaml
kind: stack
schema_version: 1
name: northwind
services:
  postgres:
    ref: service/postgres-pgvector@1.2.0
    with:
      databases: [support, litellm, phoenix]   # one database and one login role each
      init_sql: stack/db/commerce.sql          # schema, seed data and grants, as the support role
      init_sql_database: support
      roles: [support_agent, support_ops]      # least-privilege roles in `support`
      roles_database: support
      console_database: support                # AgentBreeder's console role, for CLI reads
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
  models: [support-model, support-model-openrouter, support-embedding, support-judge]
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
- **The console role** (`postgres-pgvector@1.2.0`, `console_database`). The login role
  `agentbreeder_console` can connect only to that database among the stack's databases (and
  `postgres`/`template1` are closed) and has no rights of its own: no schema, no membership, no
  `CREATE` or `TEMP`. The database owner's `init_sql` grants it rights
  on tables **the owner made** (Northwind: `turns`, `audit_events`, `notes`, `tickets`), never on
  tables the agent's role owns, because a role that reads another role's table runs that table's
  triggers with its own rights. A `GRANT` to a missing role fails the whole `init_sql`, so guard
  it:

  ```sql
  DO $$ BEGIN
    IF to_regrole('agentbreeder_console') IS NOT NULL THEN
      GRANT SELECT ON turns, audit_events TO agentbreeder_console;
    END IF;
  END $$;
  ```

  Its URL is the `console` output, `operator_only`: never bound into an agent, wired to a service
  or used as `eval_database`. `agentbreeder_console` is reserved as a `databases` or `roles` name.
  Removing `console_database` makes the role `NOLOGIN`, revokes its `CONNECT` and ends its
  sessions on the next `services up`. Where the agent's role is the database owner (the starter
  from `new agent`), there is no console role: there is no boundary inside that database to keep.

## Commands

```
agentbreeder services up     [DIR] --environment ENV [--locked | --update] [--env-file FILE] [--yes] [--json]
agentbreeder services status [DIR] [--json]
agentbreeder services logs   [DIR] NAME [--tail N]       # known secret values are redacted
agentbreeder services usage  [DIR] [--follow] [--json]   # CPU, memory, IO and disk (on main)
agentbreeder services env    [DIR] [--environment ENV]   # prints the two export lines
agentbreeder services down   [DIR] [--volumes | --purge] [--yes] [--json]
agentbreeder services start|stop|restart [DIR] NAME [--yes] [--json]   # one container (on main)
```

- **`up`** locks the refs, checks params, references and the gateway's model aliases, prints the
  **grant**, and needs `--yes` (or an interactive yes) the first time and whenever the grant
  changes. It then imports and generates secrets, builds project services, starts everything with
  Docker Compose, waits for health, runs `init` and `bootstrap` steps, and writes the operator
  config. Only services whose configuration changed are recreated. A service with an HTTP
  health check gets 180 s; if its container is still running then (a cold start on a busy
  engine), `up` says so and waits up to 240 s more. A container that exited fails at once
  with `SERVICE_UNHEALTHY`. Started services are kept, and every `up` checks each service's
  health again, so running it again with the same flags (`--yes` included) continues (on `main`,
  not in a published preview).
- **The grant** lists every service with its image (built-in or project), egress, which networks
  can reach it, its `127.0.0.1` ports, imported variable names, the wiring, what agents receive,
  the gateway's models and where each provider key goes, and the demo identities.
- **`down`** stops the stack and keeps its data and secrets. `--volumes --yes` also deletes its
  data volumes. `--purge --yes` deletes everything `up` made: volumes, built service images, and
  the state directory (generated secrets, operator config, deploy history), so the next `up`
  starts from scratch. Both refuse while a deployment in the stack is still active
  (`agentbreeder remove ID --local --stack .` first). On `main`, not in a published preview
  (#1048): `down` also removes the stack's networks that Compose leaves behind. Compose removes
  only the networks the current compose file names, so a dropped `mcp:` entry's private network
  (`agentbreeder-<stack-id>-mcp-<name>`) used to stay, as did any network from an earlier
  render. `down` lists the networks labelled `io.agentbreeder.stack=<stack-id>` and named after
  the stack, and removes them, also after a failed `up`. A network something outside the stack
  is still attached to (a leftover agent container or the kind node on `<stack>-agents`) is kept
  and reported (`Kept network …`, `networks_left` in `--json`), never forced; `docker network
  inspect NAME` shows what holds it. This is checked with unit tests against a fake Docker; the
  real-Docker assertions (`tests/test_mcp_relay_docker.py`, `tests/test_services_docker.py`) are
  opt-in and have not been run yet. If the network cannot be listed, `down` carries on with
  volumes, images and state.

  Networks left by earlier versions are not pruned automatically. To clean them up by hand, list
  them, check that no container uses them, then remove them one at a time:

  ```bash
  docker network ls --filter label=io.agentbreeder.stack --format '{{.Name}}\t{{.Label "io.agentbreeder.stack"}}'
  docker network inspect --format '{{.Name}}: {{len .Containers}} containers' NAME
  docker network rm NAME     # only one with 0 containers whose stack you no longer run
  ```

  Every stack network has carried the `io.agentbreeder.stack` label since stacks shipped (#734),
  so the first command lists them all.

  Running `agentbreeder services down` in the project removes its stack's leftovers itself.
- **`start`, `stop` and `restart`** (on `main`) act on one existing container through
  `docker compose`, without re-rendering or recreating anything. `stop` and `restart` refuse
  (`SERVICE_IN_USE`) while deployments use the stack, unless `--yes`. Every write (`up`, `down`,
  `plan`, these three, `cluster up|down`) holds a per-stack lock; a second one fails at once with
  `SERVICES_BUSY`. See [`commands/services.md`](commands/services.md).
- Imported variables come from the environment or from `--env-file` (a strict `KEY=value`
  parser; the file is never sourced). Well-known cloud, VCS and CI credentials can't be imported.

## Built-in services

| Service | What it runs | Params | Outputs |
|---|---|---|---|
| `service/postgres-pgvector@1.2.0` | PostgreSQL 16 with pgvector 0.8, data volume, uid 999 | as 1.1.0, plus `console_database` | as 1.1.0, plus `console` (operator only): a URL for `agentbreeder_console`, which can connect only to `console_database` and has no rights until `init_sql` grants them. |
| `service/postgres-pgvector@1.1.0` | PostgreSQL 16 with pgvector 0.8, data volume, uid 999 | as 1.0.0, plus `roles` (list) and `roles_database` | as 1.0.0, plus `roles.<name>`: a URL for a role that can connect only to `roles_database`, owns only a schema of its own name and has no other rights until `init_sql` grants them. `postgres` and `template1` are closed to applications. |
| `service/postgres-pgvector@1.0.0` | PostgreSQL 16 with pgvector 0.8, data volume, uid 999 | `databases` (list), `init_sql` (file, ≤ 64 KiB, no `\` meta-commands), `init_sql_database` | `databases.<name>`: a URL with that database's own role (never the superuser) |
| `service/litellm@1.3.0` | as 1.2.0, plus a request filter: the gateway refuses provider-routing, endpoint and credential parameters in requests, and call types other than chat completions and embeddings (#1105; see "Request parameters" below) | as 1.2.0 | as 1.2.0 |
| `service/litellm@1.2.0` | as 1.1.0, plus Ollama on the operator's host (`reaches_host: [11434]`, #1025 O1) and a stricter launcher: LiteLLM gets only an allow-list of variables, with cloud metadata lookups and credential files turned off | as 1.1.0 | as 1.1.0 |
| `service/litellm@1.1.0` | as 1.0.0, plus two optional url inputs, `presidio-analyzer` and `presidio-anonymizer`, for Presidio guardrails (#1024) | as 1.0.0 | as 1.0.0 |
| `service/litellm@1.0.0` | LiteLLM 1.106.2 proxy, `provides: gateway/litellm` | `config` (file, required; reviewed: allow-listed keys, each provider key only to its provider) | `url`; `master_key` (operator only) |
| `service/presidio-analyzer@1.0.0` | Microsoft Presidio analyzer 2.2.362 (multi-arch index pinned by digest), uid 1001, no egress, no auth, so its port is internal only | none | `url` (other services only) |
| `service/presidio-anonymizer@1.0.0` | Microsoft Presidio anonymizer 2.2.362, as the analyzer | none | `url` (other services only) |
| `service/phoenix@1.1.0` | as 1.0.0 | none | as 1.0.0, plus `read_key` (operator only): a personal API key of the VIEWER user `agentbreeder-reader`, minted by `bootstrap` with the ingest key. `traces` reads with it; Phoenix refuses writes made with it. Moving a stack from 1.0.0 runs the bootstrap again on the same data, which mints a new ingest key too (the old one is not revoked, as when 1.0.0 mints again). Back on 1.0.x, `traces` ignores a stored read key and uses the ingest key. |
| `service/phoenix@1.0.0` | Arize Phoenix 21.0.0 with auth on, data in a wired Postgres database | none | `url` (host only); `ingest_key` (services only, minted by `bootstrap`) |
| `service/echo@1.0.0` | A tiny HTTP test service | `label` | `url`, `token`, `link` |

Phoenix's UI: sign in as `admin@localhost` with the password in the stack state at
`service-secrets/<service>/admin-password`.

### Gateway config review

`up` reviews a `service/litellm` config before any key reaches it, and refuses anything else with
`GATEWAY_CONFIG_UNSAFE`, naming the location only (config values are never echoed):

- Only allow-listed keys per section; no custom endpoints (`api_base`, `base_url`, headers) or
  callbacks.
- Every `model_list[].litellm_params.model` is `PROVIDER/MODEL` with `PROVIDER` one of `anthropic`,
  `gemini`, `openai`, `openrouter` (#1031), matched exactly, and the rest 1–256 printable ASCII
  characters with no space. Unknown
  prefixes and bare names (`gpt-4o`) are refused, because LiteLLM would fall back to the
  provider SDK's default credential chain, which on a cloud host can reach the machine's own
  identity.
- `api_key` is absent or `os.environ/<the provider's own key>`; `os.environ/` is otherwise allowed
  only for `general_settings.master_key` (`LITELLM_MASTER_KEY`) and `database_url` (`DATABASE_URL`).
- `model_info` may set only `mode` (`chat` or `embedding`) (#1025 D13). Price keys
  (`input_cost_per_token`, …), `id`, `access_groups` and every other key are refused: a repo must
  not set the price that budgets are enforced against. Before #1025 any plain `model_info` key
  passed; no shipped config used one.
- The config is reviewed for the gateway that runs it. Ollama (`ollama/`, `ollama_chat/`), Vertex
  AI (`vertex_ai/`), Bedrock (`bedrock/`) and Azure OpenAI (`azure/`) have review rules (per
  provider model patterns, required explicit credentials, and endpoint rules; see the #1025 spec).
  Each provider is added in its own gateway version after a real run. Ollama is served by
  `service/litellm@1.2.0` (see "Ollama on this machine" below); an Ollama entry on 1.0.0 or 1.1.0,
  and any Vertex, Bedrock or Azure entry, is refused with `GATEWAY_PROVIDER_UNSUPPORTED`.
- Their credentials (`VERTEX_SERVICE_ACCOUNT_JSON`, `BEDROCK_ACCESS_KEY_ID`,
  `BEDROCK_SECRET_ACCESS_KEY`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`) are gateway-only
  imports: `secret set --from-env`, MCP versions and every service other than the built-in
  `service/litellm` gateway are refused them (`IMPORT_DENIED`, `IMPORT_GATEWAY_ONLY`). A project
  registry version named `service/litellm` is project code and is refused too. They never go into
  an agent container: an operator `bindings` entry or a manifest `secrets` entry with one of these
  names is refused (`SECRET_RESERVED`) in every environment, however its value was stored. When a
  gateway version supports one of these providers, `up` refuses to start it while any alias in
  the config (granted or not) lacks its credential (`MODEL_CREDENTIAL_MISSING`), so LiteLLM never
  falls back to a default credential chain; `next` lists those keys as required the same way.
- `next` checks model keys only for a gateway version this release ships; for a project or
  unknown `service/litellm` version its keys step says not checked (`up` still reviews the config
  against the version it resolves). A project registry copy that shadows a shipped version is
  reviewed by `next` as the shipped one; `up` reviews the copy it resolves, and refuses
  gateway-only imports from it.
- Prompt and reply logging stays off (#1028). If present, `litellm_settings.turn_off_message_logging`
  must be `true`, `litellm_settings.set_verbose` must be `false` (its debug log prints request
  bodies), and `general_settings.store_prompts_in_spend_logs` must be `false`, each as a YAML
  boolean. There is no operator opt-in; spend logs keep tokens and cost, not messages.
- Every fallback target is granted (#1033). LiteLLM serves a fallback under the caller's virtual
  key without checking the key's `models`, so a target missing from `environment.models` would
  reach a model and provider the environment never granted. `up` refuses it with
  `GATEWAY_FALLBACK_UNGRANTED`, naming the aliases: add them to `environment.models` or remove
  the fallback. This applies to `litellm_settings.fallbacks` and `context_window_fallbacks`, from
  any source alias. `up` writes the granted fallbacks into the operator config
  (`environments.<env>.fallbacks`), and deploy and `eval` mint each key with its aliases plus
  every fallback target reachable from them, so a granted fallback keeps working however LiteLLM
  checks it. Northwind grants `support-model-openrouter`, its `support-model` fallback. Its
  `OPENROUTER_API_KEY` stays optional: without it the fallback fails and the primary's error
  stands. Whether a fallback served a turn is not shown yet (#1039). The check covers a gateway
  that `up` runs, with the config `up` reviewed: edit the config and run `up` again. A
  hand-written operator config with a gateway `up` does not run gets no check, and its keys
  hold only the manifest's aliases plus any `fallbacks` it lists. The gate and policy see only
  `model_access.aliases`; the audit log's `gateway_key` `minted` event lists every model the key
  holds (`GATEWAY_KEY_MODELS`).

### Ollama on this machine (preview)

On `main`, not in a published preview (#1025 O1). `service/litellm@1.2.0` can serve aliases from
Ollama running on the operator's own machine, with no key:

```yaml
# stack.yaml
    ref: service/litellm@1.2.0
# stack/litellm/config.yaml
  - model_name: chat
    litellm_params: {model: "ollama_chat/llama3.2:3b", api_base: http://host.docker.internal:11434}
  - model_name: embed
    litellm_params: {model: "ollama/nomic-embed-text", api_base: http://host.docker.internal:11434, dimensions: 768}
```

`new agent NAME --model ollama` writes this (chat and judge on `llama3.2:3b`, `reasoning: none`).

- `api_base` must be exactly `http://host.docker.internal:11434` (#1025 D4). Use `ollama_chat/`
  for chat (Ollama's chat API, with tool calls) and `ollama/` for embeddings. Model names are
  letters, digits and `._:-`; an optional `keep_alive` (`30m`, or `-1`) is allowed. No built-in
  `service/ollama` exists (D5): install Ollama yourself and `ollama pull` each model first.
- The grant shows `ollama (no key → host port 11434)` per alias and "litellm can reach the host".
  On Linux 1.2.0 adds `host.docker.internal:host-gateway` to the gateway only when its config
  has an Ollama entry. That mapping reaches **every** host port bound to the docker bridge, not
  only 11434; Docker cannot narrow it, so the exact `api_base` is the control. Docker Desktop
  (macOS) resolves the name in every container anyway.
- **Linux has not been run.** There Ollama must listen on the docker bridge, which in practice
  means `OLLAMA_HOST=0.0.0.0` and exposes Ollama, which has no authentication, to your network
  unless a firewall blocks port 11434.
- Run on macOS with Docker Desktop (`docs/superpowers/verification/ollama.md`): `services up`,
  `release` 2/2 with `llama3.2:3b` as both agent and judge, `invoke`, one `nomic-embed-text`
  embedding at 768 dimensions. That run used `runtime/adk@1.3.0`; starters now write `1.4.0`
  (#1056), which has not been run with Ollama. On a CPU without a GPU a 3B model answers in 20–40 s once loaded;
  Ollama unloads an idle model after 5 minutes, and a cold model plus the eval's parallel calls
  can exceed the agent runtime's 90 s model timeout. **Load the model before `release`** (any
  short call, e.g. `ollama run llama3.2:3b hi`).
- Models without "thinking" (llama3.2) refuse `reasoning_effort`: use `model.reasoning: none` in
  `agent.yaml`.
- `cost` and the console's cost screens show `no price (local model)` for Ollama calls, never
  $0.00. LiteLLM records their spend as 0.0, so a budget would not count them; that was not run.
- Callers' request parameters: `api_base` and `base_url` in a request body are refused by
  LiteLLM, and a test listener on another host port received nothing. On 1.0.0 to 1.2.0 LiteLLM
  honours a per-request `custom_llm_provider`, which changes the provider an alias is served by;
  `service/litellm@1.3.0` refuses it (see "Request parameters" below, #1105).

The 1.2.0 launcher (spec §4.3) also passes LiteLLM only the variables its `service.yaml` sets,
the image's own (`PATH`, `SSL_CERT_FILE`, `LITELLM_RELEASE_TAG`, four `PRISMA_*`) and its
secrets, never the container's whole environment, so ambient `AWS_*`, `GOOGLE_*`, Azure identity
and proxy variables never reach it. It forces `AWS_EC2_METADATA_DISABLED=true`,
`AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE=/dev/null`, `NO_GCE_CHECK=true` and the three
`GCE_METADATA_*` names to `metadata.invalid`, and refuses to start when the config cannot be
parsed or names a gateway-only credential it did not import. In a test with fake metadata
services on 169.254.169.254 and 169.254.170.2, the launcher's environment sent them nothing
while the raw environment reached both. These matter on a cloud VM, not on a laptop. Nothing
blocks the gateway's network egress to metadata addresses (residual R3).

### Request parameters (`service/litellm@1.3.0`)

On `main`, not in a published preview (#1105). An alias's provider, endpoint and credentials come
from the reviewed gateway config only. Two controls keep a key on its own aliases' providers:

- **Key routes, every gateway version.** Deploy and `eval` mint every key with `allowed_routes`
  limited to chat completions and embeddings (`/v1/chat/completions`, `/chat/completions`,
  `/v1/embeddings`, `/embeddings`). LiteLLM's file, batch and provider pass-through routes choose
  a provider from a path, query parameter or header rather than a model alias, and some of them
  never reach a pre-call hook, so the key's routes are what closes them. Keys minted before this
  change keep their old routes until the deployment is redeployed.
- **Request filter, `service/litellm@1.3.0`.** 1.3.0 is 1.2.0 plus a request filter that its
  launcher loads as LiteLLM's only callback (the reviewed config itself still may not set
  `litellm_settings.callbacks`). Before any provider call it refuses a request carrying a
  provider-routing, endpoint or credential parameter, such as `custom_llm_provider`,
  `litellm_credential_name`, `api_key`, `api_version` (also as the `api-version` query
  parameter), `organization`, `extra_headers`, the `aws_*`, `vertex_*` and `azure_*` parameters,
  or `litellm_params`, at the top level, in `extra_body` or in a request-level fallback, with a
  400 whose message starts with `GATEWAY_PARAM_REFUSED`. The list is explicit, with
  no prefix rule, so provider options such as `anthropic_beta` pass. A fallback nested deeper
  than the filter reads is refused too. It refuses every other call type that reaches it
  (pass-through, file upload, responses, messages and the rest) with `GATEWAY_ROUTE_REFUSED`. It
  does not read request `metadata` or headers: on the pinned LiteLLM, `custom_llm_provider` in
  metadata and the `x-litellm-api-base`, `x-litellm-base-url`, `api_base`, `x-api-key` and
  `OpenAI-Organization` headers were ignored in the recorded run, and request tags and tracing
  metadata (top level or in `extra_body.metadata`) passed.

Each refusal writes one line to the gateway log (`services logs`): `agentbreeder gateway: refused
<code> field=<name> in=<where> call=<type> key_alias=<alias>`, with no value and no key material.
1.0.0 to 1.2.0 are unchanged: on them LiteLLM still honours a request's `custom_llm_provider`
for a model alias. Move the stack's `ref` to `service/litellm@1.3.0` and run `services up` (a new
lock). Starter stacks and Northwind still name their earlier versions.

Recorded run (2026-11-16, macOS 13 Intel, Docker Desktop, `tests/test_gateway_request_filter_docker.py`):
a stack with two scripted providers, alias `a` on the gateway's OpenAI key and `b` on its
Anthropic key, and keys granted `a` only.
- On 1.3.0, every `custom_llm_provider` value (anthropic, openai, gemini, openrouter,
  ollama_chat, azure, bedrock), streamed or not, and each other listed parameter got a 400 from
  the filter or LiteLLM's own 401. Neither provider received anything.
- The deploy-minted key got 403 on the file, batch, pass-through, responses, messages,
  completions and moderations routes.
- Chat, streamed chat, embeddings, the eval judge's `json_schema` call and one real
  `runtime/adk@1.4.0` turn were served by provider A on its own path.
- On 1.2.0, the same `custom_llm_provider` requests reached A's endpoint in another provider's
  wire format (Anthropic messages, Gemini, Ollama, Azure, Bedrock).
- Not run: Linux, `kind`, real provider endpoints.

### Gateway guardrails (preview)

A `service/litellm@1.1.0` config may hold a `guardrails:` list of Presidio PII checks (#1024 G1b).
Wire the two Presidio services into the gateway:

```yaml
services:
  analyzer: {ref: service/presidio-analyzer@1.0.0}
  anonymizer: {ref: service/presidio-anonymizer@1.0.0}
  litellm:
    ref: service/litellm@1.1.0
    with: {config: stack/litellm/config.yaml}
    wire:
      database-url: postgres.databases.litellm
      presidio-analyzer: analyzer.url
      presidio-anonymizer: anonymizer.url
environment:
  guardrails: {required: true, approved_sha256: <the guardrails sha256 up prints>}
```

```yaml
# in the gateway config
guardrails:
  - guardrail_name: pii-block
    litellm_params:
      guardrail: presidio
      mode: pre_call
      default_on: true
      pii_entities_config: {US_SSN: BLOCK, CREDIT_CARD: BLOCK}
      timeout: 30
  - guardrail_name: pii-mask
    litellm_params:
      guardrail: presidio
      mode: pre_call
      default_on: true
      pii_entities_config: {EMAIL_ADDRESS: MASK, PHONE_NUMBER: MASK}
      timeout: 30
```

The review refuses anything outside this allow-list with `GATEWAY_CONFIG_UNSAFE`:

- `guardrail: presidio` only; `mode` is `pre_call`, `post_call` or `logging_only` (`during_call`
  runs beside the provider call and is refused); `default_on: true`, or `false` for an agent
  guardrail (below).
- `pii_entities_config` maps Presidio entity names to `MASK` or `BLOCK`, and is not empty.
  An entity that one guardrail masks may not be blocked by a later one: guardrails run in config
  order, and a mask rewrites the text first, so the block would never fire.
- `presidio_language: en`, `presidio_score_thresholds` per entity from 0 to 0.8, and `timeout`
  from 1 to 60 seconds. Without a `timeout` a stalled analyzer fails the call after 300 s.
- Everything else is refused, including `presidio_ad_hoc_recognizers`, `output_parse_pii`,
  `apply_to_output`, `stream_scope`, `presidio_entities_deny_list`, `api_base`, `api_key`,
  `callbacks` and any `os.environ/` value.

`up` hashes the guardrails list plus four logging settings (`turn_off_message_logging`,
`set_verbose`, `store_prompts_in_spend_logs`, `disable_error_logs`) of the config copy the gateway
container mounts. An edit to `model_list` doesn't change it. The grant shows that sha256 and each
guardrail's entities, and `up` writes `environments.<env>.guardrails` into the operator config
with `running_sha256` and `effective` (`presidio:pre_call`, `presidio:post_call`; `logging_only`
never counts). With `required: true`, `up` refuses (`GUARDRAILS_INEFFECTIVE`) unless:

- the gateway is the built-in `service/litellm`, with its two Presidio inputs wired to the
  built-in `service/presidio-analyzer` and `service/presidio-anonymizer` (a project service could
  answer every analysis with nothing);
- it logs at `LITELLM_LOG` `WARNING` or above (at `DEBUG` LiteLLM prints request bodies);
- the config has a `default_on` `pre_call` guardrail with at least one entity;
- `turn_off_message_logging: true` and `disable_error_logs: true` are set (the error-log table
  stores request payloads).

A config with `guardrails:` but without the built-in Presidio services wired is refused even when
nothing is required. With `required: false`, `up` records `effective: []` for a floor that would
not pass these checks, so setting `required` later by hand never verifies it. The review checks the
shape of `pii_entities_config`, not which entities it names: whoever approves the sha256 owns
the coverage (a typo such as `US_SNN` is not caught). The
gate then compares `running_sha256` with `approved_sha256` (see
[identity-gate.md](identity-gate.md#guardrails-floor)). Copy the sha256 `up` prints into
`stack.yaml` after reviewing the guardrails, and run `up` again. A non-interactive
`--expect-guardrails` flag is a follow-up.

#### Agent guardrails (#1053, preview)

A guardrail with `default_on: false` runs for no one by default. An agent adds it by naming it in
`agent.yaml` (`guardrails: [agent-ip-block]`, see [agent-yaml.md](agent-yaml.md)). Agent
guardrails only add to the floor. Nothing in `agent.yaml` can remove or weaken a `default_on`
guardrail, and there is no opt-out.

```yaml
  - guardrail_name: agent-ip-block
    litellm_params:
      guardrail: presidio
      mode: pre_call
      default_on: false
      pii_entities_config: {IP_ADDRESS: BLOCK}
      timeout: 30
```

- The same allow-list applies. LiteLLM runs agent guardrails after every `default_on` one, so an
  agent guardrail may not block an entity that a `default_on` guardrail, or another agent
  guardrail, masks (`GATEWAY_CONFIG_UNSAFE`). A `logging_only` guardrail is never offered.
- When the floor has an `approved_sha256`, agent guardrails need the running config to be the
  approved one, even when the floor isn't required (`GUARDRAILS_CHANGED`).
- The stack must declare `environment.guardrails` (`required: false` is enough). `up` then writes
  `optional: [names]` into the operator config's `environments.<env>.guardrails`, but only when the
  built-in gateway and Presidio services, `LITELLM_LOG` and the logging settings checked above are
  in place. The grant marks each one "agents that name it". Its hash is part of
  `guardrails_sha256`, so editing it changes the approved hash like any other guardrail.
- The gateway config's own `policies:` and `policy_attachments:` sections stay refused. Deploy uses
  LiteLLM's policy API (open source, unlike per-key guardrails, which need LiteLLM Enterprise). After
  minting the deployment's key, deploy creates a policy named like the key alias
  (`agentbreeder-<id>`) with `guardrails_add` only, and attaches it to that one key alias. It does
  this before the key is written anywhere. It then checks two things: that the running gateway
  defines each guardrail (`/guardrails/list`; LiteLLM's policy API accepts any name, and an unknown
  one never runs), and that LiteLLM's own resolution for the key alias includes them
  (`/policies/resolve`). If the attach or either check fails, the key is revoked and the deploy
  fails (`GATEWAY_POLICY`). The checks see the serving worker's view, and `service/litellm` runs a
  single worker. Remove, disable and `reconcile` revoke the key first and then delete the policy.
- A deployment with agent guardrails always gets `allowed_routes` (below), even without a
  required floor. LiteLLM treats the policy routes as admin routes, and the key's
  `allowed_routes` leaves them out as well, so an agent key cannot change its policy. As under a required floor, its manifest may not mount a provider key name
  (`SECRET_RESERVED`). This checks exact names only, so a key renamed to get around the gateway
  is not detected.
- `status --json` shows `guardrails: {floor, agent, effective, drift}`. `floor` is the
  environment's verified floor now, not at deploy time. What the key was minted with is in the
  mint's audit row, as `GUARDRAILS_EFFECTIVE` (the floor's verified checks plus `agent:<name>`). `reconcile` reports
  `noncompliant:DRIFT_GUARDRAILS` when the deployment's policy is missing, attached more widely or
  adds other guardrails, when the attachment is a `default` one, or when the gateway no longer
  defines or resolves them. It does the same when a policy that can remove guardrails
  (`guardrails_remove`, `inherit` or `pipeline`) reaches the key through `*`, a blank attachment
  (LiteLLM reads no `keys` as every key), a matching alias pattern, teams, models or tags. This is
  checked for deployments with agent guardrails, with each gateway's policy lists read once per
  run. A policy can't remove a `default_on` guardrail on the pinned LiteLLM, so deployments with
  only the floor aren't checked. `reconcile` reports drift but doesn't repair it: redeploy to
  re-attach.

Deploy and `eval` mint every key with LiteLLM `allowed_routes` set to
`/v1/chat/completions`, `/chat/completions`, `/v1/embeddings` and
`/embeddings`. The Presidio hook covers those. `/openai/*` passthrough runs the hook but forwards
the unmasked body, and `/v1/moderations` is not checked at all. Key metadata is an allow-list of
AgentBreeder's own fields, because LiteLLM reads `disable_global_guardrails` and
`opted_out_global_guardrails` from it.

Before #1105 only keys under a required floor or with agent guardrails carried these routes;
since then every key does, on every gateway version (see "Request parameters").

What is not covered: calls that bypass the gateway, tool-call arguments, tool definitions and
image URLs inside a chat request, the request's `user` field (LiteLLM stores it as an end-user ID), what the agent itself stores (spans, sessions, turns), and
detection quality (Presidio is pattern and NER based). On this LiteLLM digest a `pre_call`
guardrail also checks the model's reply. Blocked turns reach the agent as the gateway's HTTP 400.
`runtime/adk@1.4.0` answers such a turn `MODEL_BLOCKED` (422) with a `guardrail_blocked` event
([agent-yaml.md](agent-yaml.md#runtimeadk140)). One live run (2026-11-16, Northwind, macOS) showed
this for a message with a US SSN, including the gateway blocking the turn's embedding call; the
agent still stored that message in its own `turns` table and trace spans. `runtime/adk@1.5.0` (#1106, runtime tests only) keeps a blocked turn's text out of the `turns` row and its spans, not out of the session. The rest of G2 (reconcile drift, the
runtime capability check) is still to come. On `main`, not in a published preview.

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
# reaches_host: [11434]             # built-in gateway only (REACHES_HOST_REFUSED otherwise):
                                    # host.docker.internal for a reviewed Ollama entry
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
  data identity (again after its volume, or a wired service's volume, is new, or when a secret it
  writes is missing); its stdout becomes the secret named by `writes_secret`. A bootstrap that
  writes 2–4 secrets names them in `writes_secrets` instead and prints one JSON object with
  exactly those names as keys, each a non-empty one-line string (else `ONESHOT_OUTPUT`, which
  never quotes the output).
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
> The built-ins `mcp/github` and `mcp/playwright` are checked live (#748, 2026-11-09):
> `tests/test_mcp_builtins_live.py` runs `services up`, `build` and a real `deploy` (identity
> gate, per-deployment relay token and grant file), invokes the agent, which calls each granted
> tool through the relay to the real upstream, then `remove` (the grant is deleted). The model is
> a fake gateway that asks for a fixed list of tools; a real model through `deploy` isn't checked
> yet. OAuth refresh is covered by unit tests only.

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
`service/mcp-relay@1.0.2` (standard-library Python on the pinned `python:3.12-slim`; 1.0.1 answers
an unknown session with a bare 404, which the ADK client needs in order to reconnect after a
relay restart, and 1.0.2 writes an audit record for every tool call, below). A `stack.lock`
written before 1.0.2 still names an older relay version, so `services up --locked` refuses it
(`LOCK_STALE`). Run `services up` once without `--locked` to lock 1.0.2. Agents
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

### What the audit log records about MCP tool calls

Since `service/mcp-relay@1.0.2` (on `main`, not in a published preview), each `tools/call` from
an authenticated deployment, forwarded or refused, is recorded in the stack environment's
hash-chained audit log as an `mcp_tool_call` row (the exceptions are under **Limits**):

- **Recorded:** a correlation id (the same id is in the relay's log line), the deployment, the
  agent (as the row's actor, marked unverified: the relay checked the deployment's token, no
  identity provider was asked), the server, the tool, the decision (`allow`, or the refusal code:
  `TOOL_NOT_GRANTED`, `TOOL_DEFINITION_CHANGED`, `TOOL_MISSING_UPSTREAM`, `TOOL_NOT_PINNED`,
  `RATE_LIMITED`, `UPSTREAM_ERROR`, `UPSTREAM_INVALID`), the relay's time and the latency. An
  ungranted tool's name is recorded only as the relay's 16-character sha256 prefix (`tool` is
  null).
- **Not recorded:** arguments, results, upstream error bodies and credentials; `initialize`,
  `tools/list`, `ping` and session requests; and refusals before the relay reads the request (a
  bad or missing token, too many concurrent requests, a malformed body), which have no
  deployment and are only in the relay's log. `allow` means the upstream returned a response,
  not that the tool succeeded. An agent that disconnects while the reply streams is recorded as
  `UPSTREAM_ERROR`, as in the relay's log.
- **How it gets there:** the relay appends one JSON line per call to
  `~/.agentbreeder/stacks/<id>/relay-audit/<env>/<server>.jsonl` (0700, bind-mounted read-write at
  `/run/audit`; the relay's only writable mount). `agentbreeder audit list` and `audit verify`
  import new lines first, through the same writer as every other audit row, so a call appears
  the next time the log is read, including in the console's Audit view. Each line is checked
  against the relay's record shape; lines that don't match are not imported, and one
  `mcp_relay_record` row (`RELAY_RECORD_INVALID`) counts them. The row's id is a hash of those
  lines, so the same lines read again, or repeated later, add no second row.
- **Fail closed, for opening only:** the relay forwards a call only after it has opened the
  record's file. If it can't, the call is refused with `AUDIT_UNAVAILABLE`; that refusal is in the
  relay's log (`audit_unavailable`), not in the audit log, since the file couldn't be opened.
- **Limits:** the record is written after the reply, so a call has no row if the relay is killed
  mid-call or the write fails after the open (the relay logs `audit_write_failed`). The relay's
  file is plain JSON lines until it is imported: deleting or editing it before `audit list` runs
  leaves no trace in the chain. The chain detects corrupted or edited rows; whoever owns the state
  can recompute it. This is a record of calls, not policy enforcement on arguments.

A service upstream (`upstream.service`) must be the stack service with that ref. Every port of it
must be `expose: internal`, and nothing else may wire to it. It joins a private network shared
with its relay, never the stack network (`MCP_UPSTREAM_EXPOSED`, `MCP_UPSTREAM_WIRED`). If it
declares `egress: true` it also joins the edge network, so it can reach the internet unfiltered.
A browser-type upstream with `egress: true` could reach private addresses, so the built-in
`mcp/playwright` ships with no egress until the filtering forward proxy (#747) exists.

### Built-in MCP servers

Two built-ins are verified live (2026-11-09, macOS, Docker Desktop):

| Ref | Upstream | Credential | Tools (all granted only per agent) |
|---|---|---|---|
| `mcp/github@1.0.0` | GitHub's hosted server, `https://api.githubcopilot.com/mcp/` | `GITHUB_MCP_TOKEN` (bearer) | `get_me`, `list_issues`, `issue_read`, `search_repositories`, `get_file_contents` (all `read`) |
| `mcp/playwright@1.0.0` | `service/playwright-mcp@1.0.0` in the stack | none | `browser_navigate`, `browser_click`, `browser_type`, `browser_close` (`write`); `browser_snapshot` (`read`) |

```yaml
services:
  browser: {ref: service/playwright-mcp@1.0.0}   # the upstream mcp/playwright needs
mcp:
  github: {ref: mcp/github@1.0.0}
  playwright: {ref: mcp/playwright@1.0.0}
environment:
  agents:
    my-agent:
      mcp: {github: [list_issues, issue_read], playwright: [browser_navigate, browser_snapshot]}
```

- **GitHub.** Use a fine-grained personal access token, read-only and limited to the
  repositories the agent needs: a granted `list_issues` reaches every repository the token can
  see. `GITHUB_TOKEN` and `GH_TOKEN` can't be imported, so the token is read from
  `GITHUB_MCP_TOKEN`. Measured through the relay: about 0.8 s to initialize, 0.7 s for
  `tools/list` and 0.6 to 1.4 s per call.
- **Playwright.** Microsoft's Playwright MCP v0.0.83, digest-pinned: headless Chromium with an
  in-memory profile, a read-only root, no capabilities, image responses off and no tools a page
  registers itself. Chromium's own process sandbox is off (`--no-sandbox`, the image default;
  it can't start without capabilities), so the container hardening and the missing egress are
  the boundary. Its port is internal and only its relay reaches it. **It has no egress until
  #747:** it opens pages the agent supplies (`data:` URLs) and has no route outside its private
  network (on Docker 27.5 outside names don't even resolve), so it can't browse the web yet. Every customer of a deployment shares one browser (one MCP
  session per deployment), so use it only where that's acceptable, such as a single-user demo.
  The tools that run code, upload files or read network traffic aren't declared. The relay is
  the only tool boundary: the server itself would serve every tool, and its Host check is off
  (`--allowed-hosts *`, because the relay addresses it by its stack name). A page in the browser
  can't call the server directly past the relay: Chromium's Private Network Access refuses a
  non-secure page's requests to loopback and private addresses. The live test checks this for
  `localhost`, `127.0.0.1` and the service's own name.

Slack, Supabase, Google Workspace and Stripe aren't shipped; they wait for demand and test
credentials.

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

**Repin without a terminal** (#964; on `main`, not in a published preview). `agentbreeder
services pins DIR [--json]` probes every running relay and shows what a repin would pin: each
new or changed definition in full (escaped, long lines wrapped with `↩` for display only), a
diff against the pinned definition, the pins it would drop, and one review digest. `services up
DIR --environment ENV --repin --expect-pins DIGEST` then repins with no prompt, but only if the
live definitions, the pins in `stack.lock` and each server's identity still give that digest; on
any difference it pins nothing (`PINS_CHANGED`). The digest proves the definitions were fetched
and are unchanged, not that a person read them: a script can chain the two commands, as
`plan --json` chains into `--expect-grant`. It has no expiry. A pin binds the advertised
definition, not what a tool does, and a description is upstream text that can address the
reader. First pins still go through a plain `services up` (`MCP_PINS_NEED_UP`). See
[`services`](commands/services.md).

`agentbreeder mcp grant|revoke AGENT SERVER TOOL…` edits an agent's grant in `stack.yaml` for
you; see [`mcp`](commands/mcp.md) (on `main`, not in a published preview).

`up` refuses to drop an `mcp:` entry while an active deployment uses it, or to start with an
unreadable grant file (`MCP_GRANT_IN_USE`). A narrowed grant (`mcp revoke`) is applied even while a
deployment holds the tool: from that `up` on, the relay refuses the tool to it
(`TOOL_NOT_GRANTED`), and `up` prints a warning naming the agent and deployment (#1072, on `main`, not in a
published preview). Release the agent without the tool, or its next deploy is refused
(`MCP_TOOL_NOT_GRANTED`). Grant files live in `~/.agentbreeder/stacks/<id>/grants/<env>/<server>/`.
A changed imported credential recreates the relay; open sessions then get 404 and initialize
again. `--allow-project-mcp` must name an `mcp:` entry (`MCP_UNKNOWN`). `agentbreeder services
logs mcp-<name>` shows a relay's log lines.

Dropping an `mcp:` entry removes its relay container on the next `up` (it holds the
credential), unless an active deployment still uses it (`MCP_GRANT_IN_USE`). Other services
dropped from `stack.yaml` keep running until `agentbreeder services down`, which also removes the
relay's private network (#1048, on `main`).

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
  every customer of the deployment. Only use upstreams whose session holds no per-user state,
  or accept the sharing (as with `mcp/playwright`); per-conversation sessions are #747.
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

Beside the bindings, `up` writes host-form database URLs into the environment's deploy state
(`envs/<env>/state/secrets/`, 0600) for AgentBreeder's own commands on the host, never for an
agent (on `main`, not in a published preview):

- `eval-database`: the `eval_database` reference, used by `release` to reset eval fixtures.
- `agent-database`: the `DATABASE_URL` binding as seen from the host (`127.0.0.1:<port>` instead
  of `postgres:5432`). Written only when that output's port is exposed to `host`.
- `console-database`: the `console` output of the `DATABASE_URL` service, written only when it is
  `operator_only` and the service's `console_database` param is set (the read-only console role,
  planned for `service/postgres-pgvector@1.2.0`, M12).

`up` deletes `agent-database` or `console-database` when the binding or the console role is
removed, and `down --purge` deletes all of them with the rest of the stack's state. They are
redacted from command output like every stored secret. The three names are reserved: a deploy
manifest that references `secret://eval-database`, `secret://agent-database` or
`secret://console-database` is denied with `SECRET_RESERVED`, so an agent cannot mount the
owner's or the console's credential. Deploy is the boundary: `build` copies the operator config's
bindings as they are, and deploy denies them. `up` owns these three files and overwrites or
deletes them; don't store your own secrets under these names.

## Limits (preview)

- Local Docker only; not a production deployment path.
- Demo identities come from a generated static provider.
- `customer_id` on an agent's `/invoke` is not authenticated (see the root README).
- MCP relays: a deployed agent's calls through the relay are verified against the built-in
  `mcp/github` and `mcp/playwright` with a scripted model, not with a real model. The browser
  has no egress until the forward proxy (#747).
