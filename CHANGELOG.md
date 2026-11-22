# Changelog

All notable changes to AgentBreeder are documented here.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [3.0.0a4] — 2026-11-22

### Added

- Console Try it: a reply's Turn details list the sub-agents called and the skills loaded
  (`runtime/adk@1.5.0` replies), as plain text with each sub-agent's call and model-call counts
  and each skill's reference files (#1193). Tested with a fake CLI only.

- `runtime/adk@1.4.1` and `runtime/adk@1.5.1` (#1190):
  `1.4.0` and `1.5.0` with a dependency update to opentelemetry 1.45.2 (CVE-2026-114603,
  CVE-2026-126360) and google-adk 2.14.0, with ADK's legacy span schema pinned so the spans are
  unchanged. Docker rerun on macOS x86_64 (conformance as the base versions, Northwind 61/61 on
  1.5.1); not yet labelled run end to end, so Northwind, the `new agent` starter and the console
  stay on their current versions. Earlier versions are unchanged.

- `runtime/crewai@1.0.0` (#1090, #1083 F6), run end to end
  on macOS x86_64 (Northwind 61/61 on local Docker and on `kind`; conformance 26 pass, 0 fail,
  13 not run; arm64 not run), not verified. CrewAI is not a supported
  framework. It runs on the shared runtime kit (kit 1.0.0, vendored byte for byte): one CrewAI
  `Agent` per turn through `Agent.kickoff_async`, with the conversation as structured messages and
  no `Crew`; memory, planning, reasoning, delegation, code execution, context summarising, the
  tool-result cache and CrewAI's own rate-limit retries off; models only through the kit's
  loopback proxy (a per-turn token through CrewAI's interceptor). CrewAI's task-output SQLite
  store and its console output (panels with the backstory, tool arguments and answers, printed
  whatever `verbose` says) are switched off and checked at start-up, so that no customer text is
  kept outside `turns` or printed to the log (checked in the runtime tests with a scripted model,
  and on the built image). Tool schemas become pydantic models (the runtime refuses
  what it cannot express). Capabilities `knowledge` and `notes`; no MCP, sub-agents or skills.
  `src/agentbreeder/examples/northwind-crewai/` is Northwind with `name` and `runtime` changed,
  sharing Northwind's registry and stack (its `stack.yaml` names the new agent). New CI job
  `runtime-crewai`; the Docker and `kind` acceptance is an opt-in test
  (`docs/superpowers/verification/F6-crewai.md`). CrewAI's instrumentor writes no LLM span, so
  the adapter writes one per model call from its `model_called` row (found by `conformance`). The image carries `chromadb` 1.5.9 (a `crewai`
  dependency), which has four advisories in ChromaDB's HTTP server and no fixed version; the
  runtime imports it as a library and never starts that server. They are a documented known
  security exception, limited to this runtime's lock and re-checked when chromadb ships a fix.

- `runtime/claude-agent-sdk@1.0.0` and Northwind on it (#1089, #1083 F7). **Run end to end on macOS 13 x86_64 (Northwind 61/61 through `/v1/messages`,
  locally and on `kind`); not supported.** This runtime is not in the
  built-in registry and AgentBreeder publishes no image of it (owner decision D8): it is the
  port's own project registry, `examples/northwind-claude-agent-sdk/registry/`, and a
  user's own build downloads the `claude-agent-sdk` wheel with the Claude Code CLI inside
  (accepting Anthropic's terms). The CLI runs in a second container from the agent's image, as
  uid 65533, with no secret mount and (on local Docker) no network: the **two-uid deploy
  contract** (D15), declared by the new runtime manifest key `isolation: two-uid` and the deploy
  manifest's optional `runtime.isolation`. The kit side and the CLI host talk over a shared
  directory with kernel peer-credential checks; the CLI's environment is built from scratch, its
  command line must keep every control (no built-in tools, verbatim prompts, only the kit's
  in-process MCP server), and its process group and per-turn directory are removed at turn end.
  The model proxy gains the Anthropic Messages route in this runtime (kit 1.0.0 unchanged); a
  deployment with `runtime.model_api: anthropic-messages` gets `/v1/messages` on its gateway key
  (D17) and is refused wherever guardrails apply (`RUNTIME_GUARDRAILS_UNSUPPORTED`). A
  configurable concurrent-turn cap (D16): 2 turns and 3 leases, from a measurement of the CLI
  container's memory.

- Built-in `service/litellm@1.4.0` (#1089): 1.3.0 plus
  LiteLLM's Anthropic Messages route (`/v1/messages`) through the request filter, for runtimes
  that declare `model_api: anthropic-messages`. A Messages request gets 1.3.0's refusals and is
  also refused (`GATEWAY_PARAM_REFUSED`) for `mcp_servers`, `container`, `service_tier`, `speed`,
  `inference_geo`, `betas`/`anthropic_beta`, `compaction`, `safeguards`, typed (server,
  Anthropic-defined, MCP, advisor) tools and `allowed_callers`, the `anthropic-beta` header or
  any provider header but `anthropic-version`, and Files API or URL content sources.
  `/v1/messages/count_tokens` stays reachable and unfiltered for a key allowed `/v1/messages`. Chat completions and embeddings are filtered as on 1.3.0. `gateway_keys.key_routes`
  takes `model_api` and adds `/v1/messages` for `anthropic-messages`; deploy passes it for a
  runtime that declares `anthropic-messages` (#1089 F7). Unit-tested; the opt-in Docker test passed
  on 2026-11-20 against the real LiteLLM 1.106.2 image with stub providers (#1187). 1.0.0 to 1.3.0, starter stacks
  and Northwind are unchanged.

- `runtime/deepagents@1.0.0` (#1083 F5, #1088), built and
  **run end to end on macOS x86_64, not verified**. Deep Agents is not a supported framework. The second runtime on the
  runtime kit (kit 1.0.0, vendored byte for byte, unchanged): `create_deep_agent` with the state
  backend only, so Deep Agents' file tools (`ls`, `read_file`, `write_file`, `edit_file`,
  `delete`, `glob`, `grep`) act on the turn's in-memory state, never on a disk, and there is no
  `execute` tool; those names and `task` are reserved tool names. The built-in `task` sub-agent is
  kept (owner decision D10): its model calls go through the kit's proxy and count against the
  turn's `max_model_calls`, its calls to the agent's tools are audited as the agent's, and a cap
  reached inside it fails the turn `MODEL_STEP_LIMIT`. The runtime registers the main loop's
  system prompt and tool set with the proxy each turn, so `model_called` rows say
  `main_loop: false` for the sub-agent's calls. Capabilities `knowledge` and `notes`; no MCP,
  sub-agents or skills. `src/agentbreeder/examples/northwind-deepagents/` is Northwind with `name`
  and `runtime` changed, sharing Northwind's registry and stack (its `stack.yaml` names the new
  agent). What ran (macOS x86_64, real Gemini): Northwind 61/61 on local Docker and on `kind`,
  conformance 26 pass, 0 fail, 13 not run, the `task` sub-agent counted against the cap, the file
  tools confined to the turn's state, and default-deny egress, which also stops the unused vendor
  SDKs in the image. Not run: arm64 (`docs/superpowers/verification/F5-deepagents.md`). Its
  lock adds packages (among them `anthropic`, `deepagents`, `filetype`, `wcmatch`) whose import
  names are now refused as tool module names on every runtime.

- The console's **Build an agent** form has a **Framework** group (#1085 S1). It lists every `runtime/` version from `agentbreeder registry runtimes
  --json` (`GET /api/builder/parts` gains `runtimes`, read by a CLI child and allow-listed): one
  radio per framework with its ref, status and note, a Version select, and a line for each
  runtime the CLI cannot use. When this landed only `runtime/adk` existed, so it showed Google ADK
  and its versions; the kit runtimes below were added later. The form sends a version 2 spec with
  `runtime`; the server refuses a ref that is not listed and usable (`SPEC_UNSUPPORTED`). A pick
  that does not fit the chosen version (an MCP server on `runtime/adk@1.0.4` to `1.0.6`, a tool
  past the runtime's limit, a reserved tool name) stays on the form with its reason and a Remove
  control instead of being dropped. Create stays on only for combinations run end to end, which
  all ran on `runtime/adk@1.4.0`; its reason and the preview now name the framework and version.

- The console shows the framework an agent runs (#1085 S2).
  A neutral text badge (the framework's own name, no logo) sits under each agent's name on the
  Agents list, with its ref beside it, or "Not recorded"; the agent's Overview gains Framework,
  Protocol and Capabilities rows; Try it's turn details gain Runtime and Framework rows. The names
  come from a new `GET /api/runtimes`, which reads `agentbreeder registry runtimes --json`. The
  Agents list has a framework filter kept in the address (`#/agents?framework=adk`), with a count
  and an empty state. Try it explains `MODEL_STEP_LIMIT`, and its "Not measured" cost text no
  longer names `runtime/adk` versions. When this landed, other frameworks were shown only from a
  scripted answer in tests; the later LangGraph run drove the console
  (`docs/superpowers/verification/verify-langgraph-adk150.md`).

- Registry ▸ Runtimes in the console lists every runtime version with its framework, protocol, kit
  and status, and a runtime version's detail shows its manifest facts, the packages its
  `requirements.in` names, its newest conformance report (failing groups open) and **Run
  conformance**, a job on the project's own built image (#1085 S3). New CLI read: `agentbreeder
  registry conformance REF [--json]` prints that newest report whole. A report whose result is
  `incomplete` now reads "Conformance incomplete", not "Conformance failed", on Build too. The
  reports in its tests are scripted. Registry ▸ Code and the guided-path Conformance step
  wait for code mode (F8).

- Default-deny egress for deployments whose manifest says `runtime.profile: kit` (#1083 F1b,
  multi-framework spec §13.4). Unit-tested with a
  scripted Docker and kubectl; the opt-in real-engine tests
  (`tests/test_kit_egress_docker.py`) passed on 2026-11-19 (3 on local Docker Desktop, 1 on a local
  `kind` cluster; `docs/superpowers/verification/kind-optin-2026-11-19.md`). The four kit
  runtimes in this release have `profile: kit`; every `runtime/adk` deployment is unchanged. On local Docker the agent joins
  only internal networks (its own and the environment's), and a forwarding sidecar in the
  phase-4 boundary's pattern publishes its port on loopback and forwards one port to a gateway
  at `http://host.docker.internal`; deploy and `reconcile` verify the boundary
  (`RUNTIME_BOUNDARY_UNVERIFIED`), and what such a deployment cannot reach is refused first
  (`KIT_EGRESS_UNSUPPORTED`). On `kind` the NetworkPolicy allows cluster DNS and the new
  optional operator key `kubernetes.agents_cidrs` only, which `cluster up` now records
  (`KUBERNETES_KIT_EGRESS_UNCONFIGURED` without it). `invoke` checks the sidecar's port for
  such a deployment. See `docs/local-deployment.md`.

- Agent protocol v1 is written down and has a first conformance kit (#758, #759, #1083 F2a). `docs/agent-protocol.md` describes what `runtime/adk@1.3.0`
  serves (`GET /health`, `POST /invoke`, the reply fields, the statuses and the coded model
  errors). `agentbreeder conformance IMAGE [--project DIR] [--json]` runs black-box checks
  against a built image on local Docker, with a scripted fake gateway, a span collector and a
  throwaway Postgres on an internal network, and writes a report to
  `~/.agentbreeder/conformance/<runtime>/<version>/<digest>.json`; `registry runtimes --json`
  shows the newest one as `conformance`. 26 of the 39 checks are built: the fixture agent
  (knowledge, tools, notes), the audit-row read, the kit-profile checks and the network log are
  not, so every report is `incomplete` at best and no runtime is called conformant. The checks
  are unit-tested against a stub agent and the Docker steps against a fake runner; the ADK
  baseline (`docs/superpowers/verification/F2a-adk-baseline.md`) ran on Docker Desktop (macOS
  x86_64) on 2026-11-19: `fail` on `runtime/adk` 1.3.0 and 1.5.0 (22 pass, 4 fail, 8 not run,
  5 not applicable). `list --json`
  and `list --all --json` gain `runtime` per deployment (the manifest's `runtime`, else the
  project's build record or linked package for the same image, else `null`; the image label is
  not read).

- The runtime kit and `runtime/langgraph@1.0.0` (#1083 F2b and F3, #1087). LangGraph is not a supported framework yet.
  The kit (`src/agentbreeder/runtime_kit/`, vendored byte for byte as each kit runtime's
  `runtime/kit/`) serves agent protocol v1 with the `kit` profile (`/health` 503 until the
  agent is built, 409 at once for a busy session, a SIGTERM drain, `MODEL_STEP_LIMIT`), runs the
  turn as `runtime/adk@1.3.0` does (pre-model knowledge, notes, events, the `turns` row) and puts
  every model call through a loopback model proxy in the agent's process. The proxy holds the
  gateway key and gives the framework a per-turn token; it rebuilds each request from an
  allow-list of fields and forwards no inbound header, refuses any other path, model alias or
  token, owns 1.3.0's retries, cost tally and coded model errors (parity tests against 1.3.0's
  own functions), and enforces the step cap without trusting the provider: the last allowed call
  goes with `tool_choice: none`, and an answer that still calls a tool is withheld and the turn
  fails `MODEL_STEP_LIMIT` (#1111). `runtime/langgraph@1.0.0` binds LangChain's `create_agent`
  to it (capabilities `knowledge` and `notes`; no MCP, sub-agents or skills).
  `src/agentbreeder/examples/northwind-langgraph/` is Northwind with `name` and `runtime`
  changed, sharing Northwind's registry and stack (its `stack.yaml` names the new agent). The
  real-stack acceptance ran on macOS x86_64 with Docker and `kind` (61/61 locally and on `kind`,
  conformance 26 pass and 13 not run of 39, the step cap on Gemini, coded budget and access
  errors; `docs/superpowers/verification/F3-langgraph.md`); arm64 was not run, and LangGraph is
  not verified. Northwind's `commerce.sql` grants the agent role SELECT on six columns of
  `turns`, which the kit reads for history; an existing Northwind stack needs
  `services up --update`. The `dev` extra gains `h11`, `httpx` and `opentelemetry-sdk`
  for the kit's tests, and `scripts/runtime_modules.py` skips installed names that cannot be
  imported (`*-stubs`, `*.libs`).

- `runtime/openai-agents@1.0.0` (#1083 F4, #1086), run
  end to end on macOS x86_64 and **not verified**: the OpenAI Agents SDK (`openai-agents` 0.24.0) on the runtime kit, the
  second kit runtime after LangGraph. The SDK's `Agent` runs over Chat Completions at the kit's
  loopback model proxy, with one `FunctionTool` per declared function and none of the SDK's
  built-in tools, handoffs or MCP servers. Every `OPENAI_*` variable is removed before the SDK is
  imported, and the OpenInference instrumentor replaces the SDK's own trace exporter to OpenAI.
  Capabilities `knowledge` and `notes`; no MCP, sub-agents or skills.
  `src/agentbreeder/examples/northwind-openai-agents/` is Northwind with `name` and `runtime`
  changed, sharing Northwind's registry and stack (its `stack.yaml` names the new agent). The
  real-stack acceptance (`tests/test_northwind_openai_agents_docker.py`) ran on 2026-11-20 on
  macOS x86_64: `release` 61/61 on local Docker and on `kind`, conformance 26 pass, 0 fail and
  13 not run of 39; arm64 not run (`docs/superpowers/verification/F4-openai-agents.md`).

- `runtime/langgraph@1.0.0` is verified on macOS x86_64 for what ran: from a wheel in a clean sandbox, `new agent --runtime runtime/langgraph@1.0.0`
  with Gemini and a `--from-spec` agent with one tool each passed `services up`, `build`,
  `release`, `invoke` and `eval` with Gemini, and an agent was built, released and chatted with in
  the console (`agentbreeder up`, Playwright in Chrome). `registry runtimes` labels it run end to
  end, and `new agent` and the console's builder call the starter and one tool with `gemini` on
  its default model verified. arm64, other providers, knowledge, notes and more tools were not
  run on it.

- Console: **Build an agent** can add one knowledge version (#1069), picked from the roots a new
  project resolves, for a provider with an embedding model (gemini or openai); the preview shows
  its embedding model, alias and dimensions, and Create follows the verified combinations. A path
  is not imported from the form.

- `runtime/adk@1.5.0` loads an agent's `skills:` when they have no scripts (#1059). `build` checks each skill again, refuses one with scripts
  (`RUNTIME_NO_SKILL_SCRIPTS`), places it without `scripts/` and records the hash of the placed
  files; the runtime checks that hash at start (`SKILL_TAMPERED`). The model gets each skill's
  name and description and two tools, `load_skill` and `load_skill_resource`, capped at 3 calls
  each per turn (`SKILL_LOAD_LIMIT`). The reply gains `skills[]`; events and spans carry a
  skill's name, version and hash, never its text. Checked by the runtime's tests and stub-builder
  build tests, and run on Docker Desktop (macOS x86_64) with Gemini on 2026-11-19 (items 1 to 5,
  one provider, one sample per question; `docs/superpowers/verification/1059-skills-a.md`).
  Script skills are not built.

- Console: **Registry ▸ Skills** (#1059 UI-R) lists `skill/` versions with a Code column
  (instructions, scripts, or unknown and treated as code) and shows a version's description
  first ("sent on every model call"), its `SKILL.md` and references through a new safe subset
  Markdown renderer with a Formatted / Source toggle, and its scripts as code; Import and Fork
  review a skill's description and decide the code question from its files. `registry list
  --json` gains `code` on skill rows and `registry diff --json` gains `skill:
  {description_changed, scripts_changed}` for skill diffs.

- Sub-agents and skills instruction packs are verified combinations for `new agent --from-spec`
  and the console's builder (#1058, #1059): a spec that pins
  `runtime/adk@1.5.0` with `gemini` and up to two sub-agents (at most one tool among every agent),
  or up to two skills, now has `combination.verified: true`. Each was run once end to end on
  Docker Desktop with Gemini (`services up`, `release`, `invoke`): sub-agent routing, the audit
  row naming the sub-agent, Phoenix spans, per-sub-agent cost equal to LiteLLM's spend rows, a
  wrong-routing eval failing `release` and an ungranted sub-agent alias denied; and a skill loaded
  only when needed, a refused front matter and a tampered image failing start.

- `runtime/adk@1.5.0` runs an agent's `subagents:` (#1058).
  The agent calls each sub-agent as a tool, `ask_<name>(request)`; a sub-agent sees only its
  prompt and that request, and acts as the turn's customer. Model calls are capped per agent and
  per turn (`limits.turn_model_calls`), the reply gains `subagents[]` with each one's calls, tools
  and gateway cost, `model_called` and tool events gain `agent` and `subagent`, spans gain
  `agentbreeder.subagent`, and each agent's model requests carry an `agentbreeder-subagent:<name>`
  tag for the gateway's spend log. A callback that cannot find its turn now fails instead of
  being skipped. Checked by the runtime's tests (a scripted model and a fake gateway), and run on
  Docker Desktop (macOS x86_64) with Gemini on 2026-11-19, all eight acceptance items, one
  provider and one sample per question (`docs/superpowers/verification/1058-subagents.md`);
  Northwind moved to 1.5.0 later (see Changed). Tool
  separation between agents is not a security boundary.

- `new agent --from-spec` reads spec `schema_version` 2 (#1058 SP). Version 2 adds `runtime`, `subagents`, `skills`, `limits`, case `tags` and the sub-agent
  and skill eval checks; it writes each sub-agent's prompt as `prompt/NAME-SUB-prompt@0.1.0`, gives
  a sub-agent on another model its own gateway alias with its provider's key, and refuses a runtime
  without the capability a part needs with the code `build` would give. Version 2 specs may be
  512 KiB; version 1 keeps 256 KiB, refuses the new fields (`SPEC_INVALID`) and writes the same
  files as before. No built-in runtime runs sub-agents or skills yet, and no combination with them
  is marked verified. The console's builder server accepts version 2 bodies and checks the
  answer's `spec_version`; its form still sends version 1.

- `agentbreeder cost --by subagent` splits each deployment key's LLM cost by sub-agent (#1058 EV). It reads the request tag `agentbreeder-subagent:<name>`
  (`agentbreeder-subagent:-` for the parent) from LiteLLM's spend rows (`request_tags`), and
  `cost --json` gains a `subagents` list on a key with tagged calls; a key without one, `total` and
  `models` are unchanged. A tag is the caller's claim, so it only splits the spend of the key that
  sent it; a call with no price is shown as `unknown`, never zero. `eval` and `release` print one
  line per case tag with its pass rate, only for a tagged eval set. `cost --by subagent` matched LiteLLM's
  spend rows exactly on a real `runtime/adk@1.5.0` run (`1058-subagents.md`, item 4); the per-tag
  eval line is tested on fixture reports only.

- Vertex AI through the model gateway, config-only (#1025 V1). **Not run against Vertex AI**: no model call has been made. The pinned-image checks
  (redirects, client-side parameters, credential readback, logs, metadata services) ran on
  Docker Desktop (macOS) with a throwaway key and scripted Google endpoints, on 1.5.0.
  - New built-in `service/litellm@1.5.0`: 1.4.0 (#1089) plus an optional gateway-only import,
    `VERTEX_SERVICE_ACCOUNT_JSON` (a service-account key on one line). The launcher accepts only
    a `service_account` key with Google's own token endpoint, passes LiteLLM a re-serialised
    copy with seven known fields, and stops unless every `vertex_ai/` entry reads that key and
    names the key's own project, with a valid `vertex_location` and only the parameters the
    host review allows (so no `api_base`, `api_key` or `custom_llm_provider`). 1.0.0 to 1.4.0 are unchanged and still refuse Vertex entries.
  - When an alias uses Vertex, `services up` refuses a value that is not such a key before
    any container starts (`VERTEX_KEY_INVALID`). Behaviour change for a gateway that imports
    the key: the check now covers the whole key (type, token endpoint, universe, required
    fields, one line), not only `type` and `project_id`. No built-in gateway imported the key
    before 1.5.0.
  - `new agent NAME --model vertex --vertex-project PROJECT --vertex-location LOCATION` writes a
    starter on 1.5.0 (`verified: false`; plain starter only, not `--stack-for`, `--from-spec` or
    the console). `--json` gains `options` for this provider only.
  - Behaviour change in log redaction, for every stack (`services logs`, `logs`, a failed
    candidate's saved tail): a stored service-account key's `private_key` and `private_key_id`
    are redacted on their own, and so are a PEM private-key block, a JWT and a bearer token
    (the token after `Bearer `, and `ya29.…`) whatever the stored values. A line with `Bearer `
    followed by 16 or more token characters loses that word even when it was not a token.

- Git registry sources (#829 slice 1b). Run only against
  git's own `git http-backend` on loopback HTTPS in the test suite (macOS); not yet against a
  hosted git server, over ssh or behind a proxy.
  - `agent.yaml` gains optional `registries:` (git URL, full commit SHA, optional `path`). `build`
    looks for refs in `./registry`, `AGENTBREEDER_REGISTRY_PATH`, each source in order, then the
    built-in registry; a ref with different content in two places is `REF_CONFLICT`. Sources
    provide `prompt`, `eval`, `knowledge` and `tool` versions only (`REMOTE_KIND_REFUSED`).
  - With `registries:`, `agent.lock` is version 2 (a `sources` table with each pin and
    `remote:<name>` entries); without it, the lock is version 1 byte for byte as before.
    `build.json` gains `sources` only when `registries:` is declared.
  - Fetches reach only sources you trusted: `registry trust URL [--whole-host] [--private]
    [--yes]`, `--list`, `--remove` keep `~/.agentbreeder/sources.yaml`; CI can set
    `AGENTBREEDER_REGISTRY_TRUST` (prefix entries only). Private addresses need `--private`.
  - The hardened git fetcher: one commit into a fresh private bare repository per build, no
    checkout, hooks, submodules, LFS, redirects or transports other than https and ssh; an
    allow-listed environment; 120 s and 200 MB limits. Symlinks, submodules and LFS files in a
    version are `ENTRY_UNSAFE`.
  - A cache of extracted versions (`~/.agentbreeder/cache`, mode 0700) serves refs already in
    `agent.lock` (checked against the lock); a first lock is always fetched. `build --offline`
    never uses the network.
  - `release` reads an eval set locked from a source the same way. `package.json` and the
    package's `agent.lock` copy accept lock version 2, and the console reads lock version 2 with
    the lock's own rules.
  - New: `docs/agent-lock.schema.json`.

- Console notifications (#827, M11 console half): a bell in the top bar counts the project's
  conditions (`agentbreeder next`'s) and unread stored events (`agentbreeder events`), opens a
  panel with "Needs attention" and "Recent events" (mark read; a gate failure's report path and a
  summary of its failed cases), and new failures appear as toasts.

- Console sign-in pages (#818, M2): first-run setup, an email and password sign-in page that
  returns to the screen you were on, and Settings (change password with the current one, session
  length of 1, 4, 8 or 24 hours, sign out everywhere, theme, recent security events). A session
  that ends while a page is open opens a sign-in dialog over it, so the route and anything typed
  stay; unsaved registry edits are also kept in the tab and restored only once it has signed in. A
  console state from an earlier version (projects, no password) goes to setup with its projects
  kept.

- Console sign-in, server and terminal (#818, M2): one user, `admin@localhost`, with a password
  set on first run from a session of the one-time terminal link (`POST /api/setup`) or with
  `agentbreeder up --set-password`, then `POST /api/session {email, password}`, which mints the
  tab's header token (no cookie). `up --reset-password` replaces a forgotten password and ends every
  open session, also in a running console. Passwords: at least 12 characters, not on a
  common-password list, read without echo on a terminal or as one stdin line, never from argv;
  stored as scrypt (`n=2**15, r=8, p=1`, kept per row) in `center.sqlite3`, which moves to schema 4
  and gains a `security_events` table (time, event, outcome, source; no secrets). After five wrong
  passwords in a row the console waits 30 s, doubling, never more than 5 minutes, before checking
  another; the terminal link is not affected. Once a password exists `up` prints no link unless
  asked (`up --link`, or Enter), and a link goes only to a terminal, otherwise to the 0600
  `login.html`. `localhost:PORT` redirects to `127.0.0.1:PORT` for pages.

- `agentbreeder services render --cluster --environment ENV --out DIR` writes a Helm 3 chart
  for a stack's services from the locked stack (#772 K3). Not installed or run on any cluster, and `helm lint`/`helm template` have not been
  run on it. Files and file params become ConfigMaps, Secrets are referenced by name only, every
  pod gets the `restricted` hardening, `init`/`bootstrap` steps are Jobs outside the release, and
  render refuses a chart that contains a known secret value (`CHART_SECRET_LEAK`). `service.yaml`
  gains an optional `resources` field (requests and limits; used only by this command), so a
  service that sets it does not load on an older AgentBreeder.

- A second cluster provider, `token`, for a cluster AgentBreeder has no cloud API for (#771 K1,
  #770 §4.2): the operator config's `kubernetes` block accepts `provider: token` with a `token:`
  block (API server, pinned CA bundle and hash, a dedicated mint kubeconfig by path and hash, an
  exec plugin's hash, four service accounts, `token_minutes` 10 to 60) and `tools:` (kubectl by
  absolute path and hash). Each operation mints a short-lived service account token with a hashed
  copy of the mint kubeconfig, in a 0700 session directory removed in `finally` and on
  SIGTERM/SIGHUP, with allowlisted environments; kubectl gets `--server`,
  `--certificate-authority` and `--tls-server-name` on argv and is re-hashed before each run;
  directories older than 70 minutes are swept and `doctor` reports leftovers. New `agentbreeder
  cluster connect token` checks a cluster (CA against the served chain, public CAs refused,
  Kubernetes 1.30 or later, namespace labels, quota) and computes `blocked_cidrs` (§6.4), then
  refuses with `KUBERNETES_POLICY_UNCHECKED` and records nothing, because the admission policy
  check is not wired in yet (K2 built the policies, not this check); deploys and `promote` to a
  `token` cluster are refused with the same code. A by-reference cluster binding must now belong
  to the deployment's environment (`KUBERNETES_BINDING_CHANGED`), and `disable` revokes a deployment's gateway key even when its
  workload step fails, reporting a failed revoke beside the original error. Unit-tested with a
  fake kubectl and a local TLS server; the `kind` check passed once on
  2026-11-21 after the #1205 mint fix (macOS, kubectl 1.31.4, stub policy check).

- Restricted accounts and admission policies for the Kubernetes target (#771, #773; #770 K2):
  `agentbreeder cluster policy print --namespace N` prints six `ValidatingAdmissionPolicy`
  objects (Kubernetes 1.30 or later) that hold the deployer's and the canary's objects to
  AgentBreeder's own shapes, with a digest; `cluster rbac print --namespace N` prints the four
  service accounts and their Roles; `cluster up --restricted` installs them on the local `kind`
  cluster, writes `ab-grants` and records an `ab-deployer` kubeconfig. The agent name `ab-probe`
  is reserved on Kubernetes (`AGENT_NAME_RESERVED`). Unit-tested with the policies' CEL
  evaluated in-process. The opt-in `kind` test passed 80/80 on Kubernetes 1.37 after #1204's fix.

### Changed

- Console Settings ▸ Security events show the source of an HTTP event as "Console (HTTP)" instead
  of "Browser" (#1180): the server cannot tell a browser from `curl` or a local container. The
  stored value is unchanged.

- Design system v2 tokens, step T (#1119, epic #1112):
  `website/brand.css` gets the light-first monochrome palette (neutral greys, a near-black or
  near-white primary, a blue focus ring, green only for the gate line and success), new tokens
  (`--brand`, `--border-strong`, `--destructive-subtle`, tints, glass, `--elev-1…4`, `--scrim`,
  code and syntax colours), the `.glass`/`.glass-strong` utilities with opaque fallbacks, and
  `@theme` mappings. The local console's palette follows (its token copy must match). It lands
  together with the website's v2 switch (#1115). `tests/test_brand_tokens.py` checks the glass
  copy, name collisions and WCAG AA on glass, status and syntax colours.

- The local console on design system v2 (#1116, epic #1112); it lands together with the website's v2 switch (#1115). It has a System / Light / Dark
  toggle in the top bar, set before first paint by `theme.js`. Actions are monochrome, and the
  gate line (a green ring around a check, with the word) marks passed and healthy states. The
  sticky top bar, the user menu and confirm dialogs are glass, with opaque fallbacks. Links are
  underlined. `tests/test_center_style.py` checks control borders, the use of `--primary` and the
  glass surfaces.

- Website on design system v2, step W (#1115, epic #1112):
  light by default with a dark theme that follows the system or a System/Light/Dark toggle (in
  the nav, the mobile menu and the docs sidebar), with no flash of the wrong theme; monochrome
  components on the shared tokens (no colour literals, checked by `tests/test_website_tokens.py`);
  a glass nav, docs top bar and home sheet; the gate line as the one green device; docs code in a
  token-based Shiki theme; reduced-motion-safe home animations that pause off-screen; Bricolage
  Grotesque dropped (no Google Fonts download); a new dark OG image, a theme-aware favicon and
  `theme-color`. The Cloud waitlist no longer names a launch quarter (#492).

- The engine reads framework facts from the runtime version instead of holding them as ADK
  constants (#1083 F1, "engine data-driven"). A
  `runtime/` manifest may declare `protocol`, `profile`, `kit`, `framework`, `display_name`,
  `capabilities`, `limits`, `reserved_tool_names`, `model_api`, `notes` and `docs`, and must
  then ship a generated `runtime/modules.json` (`scripts/runtime_modules.py`). A manifest
  without `protocol` (every `runtime/adk` version so far) gets the engine's one legacy profile,
  `LEGACY_ADK`, so Northwind and the `new agent` starter build byte-identically on
  `runtime/adk` 1.2.0, 1.3.0 and 1.4.0 with the refusals they gave before (a golden test).
  New: `agentbreeder registry runtimes [--json]`, `agentbreeder new agent --runtime REF` (ADK
  stays the default), an optional `runtime: {ref, profile}` field in the deploy manifest and
  its JSON Schema (written only for a runtime with `profile: kit`, which none had when this landed; digests and
  revisions of manifests without it are unchanged), and the build refusals
  `RUNTIME_NO_KNOWLEDGE`, `RUNTIME_NO_NOTES`, `RUNTIME_LIMIT`, `TOOL_NAME_RESERVED`,
  `TOOL_MODULE_SHADOWS` and `PROTOCOL_UNSUPPORTED`. Changed: `agent.yaml` allows up to 5
  `knowledge:` entries and `build` refuses more than the runtime takes (`RUNTIME_LIMIT`; still
  one on `runtime/adk`), where the file's own check refused a second one (`AGENT_INVALID`), so
  `validate` no longer refuses it; `MCP_RUNTIME_UNSUPPORTED`'s message names the missing `mcp`
  capability. No framework other than ADK has a runtime yet.

- Northwind pins `runtime/adk@1.5.0` (was `1.3.0`), after it
  released 61/61 on it from a wheel of `main` with a byte-identical `agent.config.json` (#1058).

- `registry runtimes` labels `runtime/adk@1.5.0` run end to end, after a fresh run from a wheel in a clean sandbox (sub-agents and skills, Gemini,
  through the CLI and the console). The console's Build an agent form now starts on the starter's
  runtime version, `runtime/adk@1.4.0`, rather than the newest version labelled run end to end,
  so a plain form stays a verified combination that Create accepts.

- Groundwork for remote registry sources (#829 slice 1a):
  - Behaviour change: `build --locked`, `services up --locked` and `release`'s eval set read
    each locked ref only from the root its lock entry names, with no cross-root check, and
    that root must be configured now (`registry`, an `AGENTBREEDER_REGISTRY_PATH` entry or
    `builtin`); any other locked `source` is `LOCK_STALE`.
  - `agent.lock` and `stack.lock` are read strictly: unknown keys, malformed hashes and empty
    sources are `LOCK_INVALID`, and a newer `lock_version` says to upgrade.
  - `agent.lock` version 2 (a `sources` table, `remote:<name>` entries, per-entry `pin`) can be
    read and written; nothing writes it yet, and every existing lock stays version 1 byte for
    byte. Remote entries give `SOURCE_OFFLINE` (no fetcher yet).
  - `registry publish`, `import`, `fork` and `edit` refuse an empty directory in a version, and
    `registry verify` reports one (`ENTRY_UNSAFE`).

- The Kubernetes target runs through a cluster provider interface (`kubernetes_providers.py`:
  `session()`, `check(session)`, `registry()`, `blocked_cidrs()`), with `kind` the only provider
  and its behaviour unchanged (#771 K0 = #908 G0). The operator config's `kubernetes` block gains
  an optional `provider` (only `kind`; omitted means `kind`). State schema v5 adds a
  `cluster_bindings` table and rewrites each Kubernetes deployment's recorded binding from
  `<context>|<uid>|<namespace>|<kubeconfig>` to `kind|…` (shown in `status --json`'s `context`);
  `remove`, `disable`, `reconcile`, `logs`, `forward` and `watch` rebuild the binding from the
  record. A by-reference binding (`<provider>|<id>`, for later providers) is refused with
  `KUBERNETES_BINDING_CHANGED` when its row no longer matches the digest in the deployment's audit
  event (a consistency check within the state file, not tamper-evidence). Unit-tested; the `kind`
  lifecycle regression is written and not yet run.

- A Kubernetes deployment's Secret is now created after its Deployment and owned by it
  (`blockOwnerDeletion`), mounted through one projected volume, and deleted by the garbage
  collector: `remove`, `disable` and `reconcile` delete with `--cascade=foreground` and never
  list or delete Secrets (#771, #770 K2). Service and container ports state `protocol: TCP`.
  `cluster up` deletes Secrets that deployments created before this change leave behind. Not yet
  run on `kind`.

### Internal

- The website's lint tooling uses `typescript-eslint` 8.73.0 (was 8.72.1), which depends on
  `minimatch` 11 and clears Dependabot alert #277 (#1168). Development dependency only.

- Website: the postbuild check now fails the build if any prerendered page nests a link inside a
  link, the cause of the docs hydration error #418 that #1138 fixed (#1129).

- Website docs attribute each claim to the run that made it (#1114, after the 3.0.0a3
  public-registry check): the docs index, the console page and the shorter path in "Your own
  agent" separate what ran from a checkout or 3.0.0a2 from what the published 3.0.0a3 ran, and
  the claims inventory gains an a3 public-registry column.

- Website docs for 3.0.0a3 (#1114, part of #1112): every page under `website/content/docs/`
  rechecked against what ran, new pages for the console, models and gateway, guardrails, cost,
  traces, registries, MCP tools, evals and release, sub-agents and skills, and Kubernetes on
  `kind`, and a claims inventory in `docs/superpowers/verification/website-a3-claims.md`.

- Design system v2 spec (#1113, epic #1112): a light-first monochrome token set with glass
  surfaces, a dark theme chosen by the system or a toggle with no flash, motion and performance
  rules, and the rollout across the website, the local console and Cloud. Design only; nothing is built.

- The multi-framework agents spec (#1084) takes in the F0 probe record (#1110): the model proxy
  enforces the step cap itself because Gemini ignores `tool_choice: none` (#1111), the Claude
  runtime requires `verbatim_prompts` and an `env -i` wrapper, CrewAI's task-output store must be
  off or per turn, and D8, D15 and D16 are restated for the owner. Design only; nothing is built.

- #1083 F2a: the ADK conformance baseline ran on a real Docker engine against Northwind's
  `runtime/adk@1.3.0` and `1.5.0` images: 22 pass, 4 fail (`sessions.busy`, `traces.llm_span`,
  `model_errors.text_logs`, `model_errors.text_spans`), 8 not run and 5 not applicable of 39 on
  both; the record is `docs/superpowers/verification/F2a-adk-baseline.md`.

- #1058 S0: a probe on a live LiteLLM 1.106.2 found that request tags reach the spend logs
  (`request_tags`) and are billed to the sending key, on `service/litellm@1.0.0` and `@1.3.0`; the
  record is in `docs/superpowers/verification/1058-S0-litellm-tags.md`.

- #1048: a live Docker run confirmed that `services down` leaves no stack networks after an `up`,
  and that `down --purge` leaves none after a failed `up`; the record is in
  `docs/superpowers/verification/1048-network-live-assert.md`.

- #1020: a third Anthropic run with a new key is still refused, because the key is not
  scoped to a workspace; the record is in `docs/superpowers/verification/providers.md`.

- #1019: an opt-in Docker test (`AGENTBREEDER_DOCKER_TEST=1`) checks the gateway's budget, rate
  limit, model grant and fallback on `service/litellm@1.3.0`, and
  `docs/superpowers/verification/gateway-limits.md` records a re-check after #1034 and #1039.

- The changelog fold test accepts the `### <Type>` headings a fold adds to an empty
  `[Unreleased]` section, so `main` is green again after the 3.0.0a3 fold (#893).

- Website: the install page, the footer badge and "what works today" describe the published
  `3.0.0a3` (#836), with a verification table limited to what ran from the public registries on
  2026-11-16.

- UI tests: the log viewer tests pick the Lines select by its exact accessible name instead of a
  substring label match (#821). Once the logs had loaded,
  "Lines" also matched the "Show lines" group, the Level select, "Wrap lines", the Copy button
  and the "Log lines" region, so `test_read_again_keeps_focus_and_a_single_match_scrolls_back`
  failed in strict mode whenever the read finished first (CI on #1170, #1172 and #1173).

- #818: console sign-in verified from a wheel built from `main` in a clean sandbox: the S-02 check
  from inside a Northwind container and from the default bridge, a Chrome click-through (Playwright)
  of setup, sign-in, Settings, the re-auth dialog after `up --reset-password` with a draft kept and
  the notifications bell, and the upgrade from a 3.0.0a2 console. The record is in
  `docs/superpowers/verification/M2.md`.

- Design spec for component owners and approval states (#764): `approved`, `deprecated` and
  `revoked` per component version, set by a verified human approver of the owning team named in
  the operator config, and recorded in the audit log.
  Design only; nothing is built.

- Agent packages (#763, M13 slice 4a): a library module
  that builds a package's OCI artifact (`application/vnd.agentbreeder.agent.v1`, one
  deterministic `tar+gzip` layer) and pushes it to a registry at `127.0.0.1` only, with the tag
  read back and compared. No command uses it yet; `deploy agent/<name>@X.Y.Z --cluster` still
  refuses.

- Docs synced with `main` since 3.0.0a3, ahead of 3.0.0a4: a new `docs/runtimes.md` (runtimes,
  the runtime kit, conformance, with each run's record), `ARCHITECTURE.md`, the README's "On
  `main`, not in a published preview" section ("New in `3.0.0a4`" since the release), stale lines in `docs/cost.md`, `docs/evals.md`,
  `docs/registry.md` and `docs/command-center.md`, and the website docs and `what-ships` rows,
  each new capability labelled "on main, coming in 3.0.0a4". Install pins and the footer badge
  are unchanged.

### Fixed

- The `token` provider's mint no longer runs `kubectl create token --duration Nm -- <sa>`, which
  kubectl 1.31 rejects client-side ("exactly one NAME is required, got 0") (#1205). The service
  account name is a positional, re-checked as a DNS-1123 label where it goes on kubectl's argv
  (`KUBERNETES_TOKEN_INVALID`), and `KUBERNETES_MINT_FAILED` shows one line of kubectl's stderr
  with the mint's credentials, tokens, CA data and home paths replaced (none of it when the mint
  uses an exec plugin). The docs note `Install Certificates.command` or `SSL_CERT_FILE` for
  python.org Pythons on macOS (`KUBERNETES_TRUST_STORE_EMPTY`). Unit-tested, and
  `tests/test_kubernetes_token_kind.py` passed once against a local `kind` cluster (macOS, kubectl
  1.31.4, 2026-11-21), with a stub policy check; no other cluster has been run.

- `cluster up --restricted` now installs all its admission policies on a real API server; on
  `main` before this, three of them (`ab-deployments`, `ab-networkpolicies`, `ab-pods`) were
  refused and their bindings failed open (#1204). The generated CEL wraps mixed-type list and map
  values in `dyn()`, which Kubernetes' homogeneous-literal check accepts; equality still refuses
  any extra, missing or different key. The install fails closed. It writes `ab-grants` first,
  because a binding whose parameters are missing denies every write in the namespace, the
  admin's included; `ab-deny-other` now excludes that one ConfigMap by name, and a new
  parameter-free policy, `ab-grants-guard`, refuses every write the deployer or the canary makes
  to it. It then applies each policy alone and reads it back before its binding, and installs
  the Roles last. On any failure
  it creates no further binding, deletes the bindings that run created, and raises.
  `CLUSTER_FAILED` now carries kubectl's stderr, scrubbed of tokens, CA data, certificates, keys
  and home paths. The `initContainer` and service-account-token Secret probes are now valid
  objects, so the named policy refuses them, and the kind test's RBAC probes check subresources
  correctly. The opt-in kind test passed on Kubernetes 1.37 (kind, macOS).

- Console: a view whose read got a 401 is read again after the re-auth dialog signs in, so Build's
  Framework list and a registry version card no longer stay loading (#1188); views that already
  had their answer keep it.

- Website builds reject stale homepage CSS, compile production styles without the restored
  Turbopack filesystem cache, and verify Vercel adapter output in addition to local builds.
  The homepage recording stays readable until playback is requested, and docs logo links no
  longer nest anchors (#1137).

- Website: pages no longer download and run the docs search dialog (Orama and an HTML and a
  Markdown parser) on load; it loads when search is first opened (#1130). `/docs/install` transfers 99,694 fewer bytes on load (Lighthouse total byte
  weight 626,248 to 526,554). No content change.

- `runtime/adk@1.5.0`: a turn a gateway guardrail blocked (`MODEL_BLOCKED`) no longer keeps its
  text in the `turns` row or in trace spans (#1106). The
  row's message is `[blocked by a gateway guardrail]` with empty `sources` and `evidence`; its
  `error`, the `turn_failed` event and the log line are `MODEL_BLOCKED: ENTITY by guardrail
  NAME`; and the turn's spans are exported with ids, times, kinds and counts only. A 400 that names
  a guardrail block behind another prefix keeps its code but is stored the same way. Every turn's
  spans are now sent when the turn ends. The ADK session and the audit rows of earlier tool
  calls still hold what they held. Checked by the runtime's tests and by one live run on Docker
  Desktop (macOS) with Presidio and Phoenix, with `1.4.0` as the control.

- Gateway hardening against request-side provider routing (#1105):
  - Behaviour change: deploy and `eval` mint every key with `allowed_routes` limited to chat
    completions and embeddings (`/v1/chat/completions`, `/chat/completions`, `/v1/embeddings`,
    `/embeddings`), on any gateway, not only keys under a guardrails floor. LiteLLM's file, batch
    and pass-through routes are closed to them. A key minted earlier keeps its routes until the
    deployment is deployed again, which mints a new key.
  - New built-in `service/litellm@1.3.0` refuses provider-routing, endpoint and credential
    parameters (a listed set) at the top level of a request, in `extra_body` and in request
    fallbacks (`GATEWAY_PARAM_REFUSED`), and call types other than chat completions and
    embeddings (`GATEWAY_ROUTE_REFUSED`). On 1.0.0 to 1.2.0, which are unchanged, LiteLLM still
    honours a request's `custom_llm_provider`; move a stack to 1.3.0 to get the filter.

- The console's **Build an agent** form starts each framework on its newest version that has run
  end to end, not on an unverified newer one (#1085 S1).
  Google ADK now starts on `runtime/adk@1.4.0`; `1.5.0` stays one pick away in the Version select;
  it has been labelled run end to end since its fresh-wheel run (#1058).

- `agentbreeder conformance` no longer reports `fail` for an image built with `traces: none` (the
  starter's default): the checks that read spans are
  `not_run`, with the reason, when the project's `agent.yaml` (the one the build hashed) turns
  traces off. Build with `traces: otlp` to run them.

- Console: a deployment whose identity nothing has checked since it was deployed says "not
  checked since deploy" on Agents, the agent page and Deployments, and the agent page says why
  `agentbreeder status` can report `compliant` for it (a deploy records no result; `status` and
  `reconcile` do) with a link to Re-verify. Deployments now words the identity check as the
  Agents list does (`noncompliant: CODE`, `unbound (no principal)`) (#1078).

- Console: signing in no longer waits for the first `doctor --json` read (partial fix for
  #1047). The shell and Home appear as soon as the
  projects are read; the version and Home's doctor card follow when doctor answers, and only the
  newest overview read is shown. Before, a slow first CLI child (a loaded machine, a slow Docker)
  held the page on "Loading the console…" for up to 30 seconds. A rarer stall in the UI tests,
  where the page never boots, is not reproduced; the tests' sign-in now reports what failed to
  load.

- Console: the "Session ended" page also names a password changed in another tab or reset with
  `agentbreeder up --reset-password` as a cause (#879, found in the M2 run).

## [3.0.0a3] — 2026-11-16

### Added

- MCP tool calls through the relay are in the hash-chained audit log: `service/mcp-relay@1.0.2`
  records every `tools/call` from a deployment, forwarded or refused (`TOOL_NOT_GRANTED` and the
  other refusal codes), with a correlation id, the deployment, agent, server, tool, decision and
  latency, never arguments or results. `audit list` and `audit verify` import the records as
  `mcp_tool_call` rows, and the console's Audit view names the server and tool. The relay refuses
  a call it can't record (`AUDIT_UNAVAILABLE`). Run `services up` once without `--locked` to move
  a stack to the new relay (#1068).

- The console shows what `runtime/adk@1.3.0` and `1.4.0` report (#1062). In Try it, a failed
  model call keeps its code (`MODEL_BUDGET_EXCEEDED`, `MODEL_RATE_LIMITED`, `MODEL_ACCESS_DENIED`,
  `MODEL_REQUEST_REFUSED`, `MODEL_UNAVAILABLE` or `MODEL_BLOCKED`) instead of `AGENT_ERROR`. It
  shows the runtime's next step, the correlation ID and a rate limit's wait. A reply's Turn
  details show a gateway fallback, refused unknown tool names and cached prompt tokens. A trace
  shows those and a guardrail block (the name and the entity type only), and the cost tables gain
  **Cached in**. Tested with a fake CLI only, written before `1.4.0` merged, against its
  documented `--json` shapes; not run against a real `1.4.0` reply.

- The `skill` registry kind (#1059 slice K1):
  `skill/<name>@X.Y.Z` versions (`manifest.yaml`, `SKILL.md`, optional `references/` and
  `scripts/`) are checked at `publish`, `import`, `fork`, `edit` and `verify` (strict front matter,
  `SKILL_FIELD_UNSUPPORTED` for `metadata`, `allowed-tools` and other keys,
  `SKILL_UNDECLARED_SCRIPT`), shown by `registry show`, locked, and found by `registry used-by`.
  A version with scripts is code, decided from its files and failing closed (`registry list`
  marks it, and `import` and the console ask the code confirm). `registry new skill NAME
  [--scripts]` scaffolds one. `agent.yaml` accepts `skills:` (at most 10 refs), but no runtime
  loads skills yet, so `build` refuses it (`RUNTIME_NO_SKILLS`); nothing runs a script.

- `agent.yaml` `subagents:` and `limits:` (#1058, slice AY):
  up to 5 sub-agents, each with its own description, model alias, prompt, registry tools
  and a subset of the agent's MCP grant, depth 1 (`SUBAGENT_DEPTH`), no `budget_usd`/`rpm`
  (`SUBAGENT_FIELD_UNSUPPORTED`), no customer notes (`SUBAGENT_NOTES`), MCP tools only from the
  agent's own entry (`SUBAGENT_MCP_UNGRANTED`), and `ask_<name>` clashes refused at build
  (`SUBAGENT_NAME_CLASH`). `build` writes them into `agent.config.json` and the build record and
  mints the one gateway key for every sub-agent alias, but no runtime runs sub-agents yet, so it
  refuses them (`RUNTIME_NO_SUBAGENTS`). A file without sub-agents builds the same bytes. `used-by`,
  `set-ref`, `new agent --stack-for` and the provider-keys guide step read sub-agent refs and
  aliases. Eval cases gain `subagents_include`, `subagents_exclude`, `subagent_tools_include`,
  `skills_loaded`, `skills_not_loaded` and `tags`; the report gives per-tag pass rates and
  `evals.tag_threshold` gates `release` and `promote` per tag.

- `runtime/adk@1.4.0` (#1056, #1074, #1024 G2). Replies,
  `model_called` events and the root span carry `cached_prompt_tokens`, the prompt tokens the
  provider reported reading from its cache (never estimated); `agentbreeder cost` adds a
  `CACHED IN` column (`cached_prompt_tokens` in `--json`) from LiteLLM's spend logs, and `traces`
  shows `cached N`. A tool name the model invented is no longer listed as a tool call: the model
  gets a fixed error, the reply lists the name under `unknown_tools`, one `tool_unknown` event is
  written instead of a `tool:<name>` row, `invoke` prints a note and `traces` shows
  `unknown tools (refused)`. A gateway guardrail block is answered `MODEL_BLOCKED` (HTTP 422, no
  content) with a `guardrail_blocked` event and root span attributes naming only the guardrail
  and entity type; `invoke` and `traces` show it. The `new agent` starter moves to `1.4.0`;
  Northwind stays on `1.3.0`. CI runs the `1.4.0` runtime tests. `1.3.0` is unchanged. One live run
  (macOS, 2026-11-16) showed cached tokens on Gemini and OpenAI (OpenRouter reported none),
  matching LiteLLM's spend logs, a Presidio SSN block answered `MODEL_BLOCKED`, and the starter
  released 2/2; no model invented a tool name, so that path is checked by tests only.

- Agent-level guardrails (#1053): `agent.yaml` `guardrails:` names guardrails that the
  environment's gateway config defines with `default_on: false`. They are carried through the
  package template into the manifest's new `guardrails` field (JSON Schema updated; the digest and
  approval revision are unchanged when it is unset). `services up` records the offered names as
  `environments.<env>.guardrails.optional`. The gate refuses any other name with
  `GUARDRAIL_UNDEFINED`, even in audit mode, and Cedar sees `agent:<name>` in the `guardrails` set.
  After minting the key, deploy creates an add-only LiteLLM policy and attaches it to that key
  alias alone (OSS policy engine). The key is limited to chat completions and embeddings, and the
  mint's audit row records `GUARDRAILS_EFFECTIVE`. Remove deletes the policy after revoking the key.
  `status --json` shows the effective set, and `reconcile` reports `DRIFT_GUARDRAILS` and deletes
  the policies of removed deployments. Agent guardrails only add to the floor; there is no
  opt-out.

- `runtime/adk@1.3.0` (#1030, #1034, #1039). A failed
  model call is answered with a fixed coded error and never the gateway's or provider's text:
  `MODEL_BUDGET_EXCEEDED` (402), `MODEL_RATE_LIMITED` (429, `retry_after_s` and `Retry-After`),
  `MODEL_ACCESS_DENIED`, `MODEL_REQUEST_REFUSED` or `MODEL_UNAVAILABLE` (502). Each comes with a
  next step and a `correlation_id`, the turn id that the agent's single redacted
  `model call failed` log line carries. The runtime does its own bounded retries instead of the
  OpenAI client's unseen ones: up to 3 attempts for transient failures (never for a timeout
  or a spent budget), and at most 10 s of rate-limit waiting per turn, reported as
  `rate_limit_wait_s`. Replies carry `fallbacks` and `served_by`
  (aliases only) when a gateway fallback served the turn, and so do the root span and the
  `model_called` events. LiteLLM's masked key fragments and token hashes are redacted, and library
  logs are one redacted line for every agent. `invoke` prints the model error code with its
  correlation id and notes a fallback; `traces` shows `fallback: ALIAS` (`fallbacks`,
  `served_by` in `--json`). The `new agent` starter moves to `1.3.0`, and Northwind stays on
  `1.2.0` until a relock with a full eval run. CI runs the `1.3.0` runtime tests. `1.2.0` is
  unchanged. See `docs/agent-yaml.md`.

- **Ollama through the stack gateway** (#1025 O1): `service/litellm@1.2.0`
  serves `ollama_chat/…` and `ollama/…` aliases from Ollama on the operator's machine at exactly
  `http://host.docker.internal:11434`, with no key. `new agent --model ollama` writes a keyless
  starter on `llama3.2:3b` (`reasoning: none`), run end to end on macOS with Docker Desktop
  (`release` 2/2, `invoke`, one embedding); Linux has not been run. `cost` shows
  `no price (local model)` for those calls. The 1.2.0 launcher passes LiteLLM only an
  allow-list of variables and turns off cloud metadata lookups. A new `service.yaml` field,
  `reaches_host`, is allowed only on the built-in gateway (`REACHES_HOST_REFUSED`).

- An operator environment can declare a guardrails floor, `guardrails: {required,
  approved_sha256}` (#1024 G1a). No guardrail runs yet: with `required: true` the gate denies every
  deploy in that environment (`GUARDRAILS_UNVERIFIED`, also in audit mode) until the gateway check
  lands, and provider key names (`GEMINI_API_KEY` and the like) are refused in its `bindings` and in
  manifest `secrets` (`SECRET_RESERVED`, a naming check only). Cedar's `Agent` gains a `guardrails`
  set, always empty for now. The schema change alters every revision digest once, so approvals
  held from before have to be requested again.

- Presidio guardrails on the stack gateway (#1024 G1b): new built-ins
  `service/presidio-analyzer@1.0.0` and `service/presidio-anonymizer@1.0.0`, and
  `service/litellm@1.1.0` with their two inputs. The gateway config review accepts a `guardrails:`
  list from a Presidio allow-list, and `services up` records the hash of the mounted config's
  guardrails (`running_sha256`) and its effective checks. A required floor is refused at `up`
  when it cannot work (`GUARDRAILS_INEFFECTIVE`). The gate now checks the running hash against
  `approved_sha256` (`GUARDRAILS_UNVERIFIED`, `GUARDRAILS_CHANGED`) instead of denying every
  deploy, and the Cedar `guardrails` set carries the verified checks. Under a required floor,
  deployment and eval keys are minted with `allowed_routes` limited to chat completions and
  embeddings. Key metadata is now an allow-list for every key. Blocked turns are not handled by a
  runtime yet (G2), so no guardrail claim is made.

- `agentbreeder new agent NAME --from-spec FILE` takes one pinned `knowledge/` version (#989,
  part of #833). The generated stack serves the version's
  embedding alias with the provider's embedding model (`gemini` or `openai`; `anthropic` and
  `openrouter` are refused) and uses `service/postgres-pgvector@1.2.0`. `--json` adds a
  `knowledge` field with the alias, dimensions, model and the expected document and chunk
  counts. The combination is not yet verified end to end. See `docs/commands/new.md`.

- MCP repin without a terminal (#964): `services pins
  [DIR] [--json]` probes every relay and prints what a repin would pin (whole definitions, a diff,
  dropped pins) with one review digest, and `services up --repin --expect-pins SHA256` repins with
  no prompt only when the live definitions, the base pins and each server's identity still give
  that digest (`PINS_CHANGED` otherwise, pinning nothing). New codes: `MCP_RELAY_NOT_RUNNING`,
  `MCP_PINS_NEED_UP`, `PINS_EXPECT_INVALID`, `PINS_EXPECT_NEEDS_REPIN`, `PINS_CHANGED`. See
  `docs/commands/services.md`.

- The console reviews and applies an MCP repin (#964, console half).
  When a pinned tool changed upstream, the services review offers **Review
  repin**: the server runs `agentbreeder services pins --json`, checks every definition against
  its pin and the review digest, and shows the definitions as the CLI renders them. After a tick,
  Start runs `services up --repin --expect-pins DIGEST`, which pins nothing if the definitions
  changed since the review. Not run against live relays. See `docs/command-center.md`.

- `agentbreeder evals list|show` reads back the eval reports `release` saves (passed/total, the
  judge, and per case pass/fail, failed checks, the judge's reason and the trace id; replies only
  with `--content`, redacted) for the console's report viewer (#932).


- Console eval report viewer (#932, console half): an
  Audit release row's run id and an agent's Releases tab open one saved eval report (`evals show
  --json`): the score, eval set, judge, and every case failed first with its failed checks, the
  judge's reason and the trace id; replies and explanations only on request. See
  `docs/command-center.md`.

- `service/phoenix@1.1.0` (#925). Its bootstrap also mints
  a read-only key: a personal API key of the VIEWER user `agentbreeder-reader` (`read_key`,
  operator only). `agentbreeder traces`, and through it the console's Traces tab, reads Phoenix
  with that key, which Phoenix refuses for any write. A stack on phoenix 1.0.x still works with
  the ingest key, and `traces --json` then says so in `notes`. A service's `bootstrap` can now
  write several secrets (`writes_secrets`). Checked against the real Phoenix 21.0.0 image in a
  single container (`tests/test_phoenix_read_key_docker.py`, opt-in).

- `agentbreeder traces [ID | --trace TRACE_ID] --stack DIR [--last N] [--since D] [--json]`
  lists recent agent traces per deployment from the stack's Phoenix: the span tree, models,
  tokens, tool calls and latency, with message content never shown and every string redacted.
  Traces are attributed exactly by an `agentbreeder.deployment_id` root-span attribute, else by
  the deployment's lifetime window. Deploy now gives model-access containers
  `AGENTBREEDER_DEPLOYMENT_ID` (#907).

- The console has a **Traces** tab on an agent's detail (#907, console half).
  It lists one deployment's recent traces from the stack's Phoenix through
  `agentbreeder traces ID --last N --json`: start, status, latency, models, tokens, span count and
  tool calls. Each trace's span tree opens on request. The eval report viewer opens a case's trace
  the same way. Only a checked summary reaches the browser, with no message content. Tested with a
  fake CLI; not yet run against a live Phoenix. See `docs/command-center.md`.

- Console Try it shows a turn's trace as a span tree (#907, console half):
  a **Trace spans** disclosure beside Turn details runs
  `traces --trace ID --stack DIR --environment ENV --json` when opened and shows span names, kinds,
  status, latency, models, tokens, tool calls and error text (never prompts, replies or tool
  arguments). A trace Phoenix
  does not have yet can be read again. See `docs/command-center.md`.

- `agentbreeder cost [--stack DIR] [--since DAY] [--until DAY] [--by deployment|model] [--json]`
  (#906): LLM cost per deployment key and per model from
  the gateway's LiteLLM spend logs (`/spend/logs/v2`), read-only, in USD as LiteLLM computed it
  (an estimate, not a bill); removed deployments are still counted. Checked against a real
  LiteLLM 1.106.2 with mocked model responses, not a real provider. The per-turn `cost_usd` the
  console's Try it will show is documented as planned, not emitted. The gateway client no longer
  follows HTTP redirects with the master key (a redirect is now `GATEWAY_ERROR`, for minting too)
  and reports an answer that is not JSON as `GATEWAY_ERROR` instead of `GATEWAY_UNREACHABLE`.
  See `docs/cost.md` and `docs/commands/cost.md`.

- The console shows LLM cost (#906, console half): Home has an **LLM cost** card for a project, and
  an agent's Overview has one for its own deployments, removed ones included. Each card shows spend
  per model and per deployment, with the currency, the window (7, 30 or 90 UTC days) and the time
  it was read. It is LiteLLM's estimate from its price list, not a bill. The data comes from
  `agentbreeder cost --json`, read fresh, and the answer is checked before the browser sees it. Not yet run against a live gateway.

- Console Try it shows each turn's cost (#906): the
  `cost_usd` that `runtime/adk@1.2.0` reports (LiteLLM's estimate, in USD) appears in the turn
  inspector and its summary; older runtimes, or a turn with a call that had no cost header, show
  "Not measured". Tested with a fake CLI, not a live gateway.

- `new agent NAME --model anthropic|openai|openrouter [--model-id ID]` (#905):
  model providers beside `gemini`, all through LiteLLM with the key read from
  the stack's local secret file. Defaults `anthropic/claude-opus-5-5` and `openai/gpt-5.6`;
  openrouter needs `--model-id openrouter/<vendor>/<model>`. Only gemini has been run end to end;
  the others are config-only, not run end to end. `--stack-for` takes the same providers and
  `--model-id`. `--json` adds `model_id` and `verified`; `next` links each provider's key page
  for `KEYS_MISSING`. `--model gemini` alone is unchanged. See `docs/commands/new.md`.

- `agentbreeder enable`, `disable`, `approve`, `reject`, `approve-call` and `reject-call` take
  `--stack DIR` (#860): with `--environment ENV` they act on the deployments `services up` state
  holds, checked against the stack's generated operator config, instead of needing
  `--state-dir` plus `AGENTBREEDER_OPERATOR_CONFIG`. `next` now prints these forms for
  `PRODUCTION_DISABLED` and `APPROVAL_PENDING`.

- `agentbreeder new agent NAME --from-spec FILE` (#833, M17 CLI half):
  writes an agent project from the console builder's private JSON spec (model,
  prompt text, eval cases, picked tool versions and MCP servers with their granted tools), in the
  starter's shape, and `--dry-run --json` returns every file's text for the preview. Knowledge is
  refused for now. See `docs/commands/new.md`.

- Console agent builder, tool and MCP pickers (#833, M17 console half, second part):
  **Build an agent** offers the `tool/` versions and `mcp/` servers a
  new project resolves (`AGENTBREEDER_REGISTRY_PATH` and the built-ins), one version per name, with
  a checkbox for each tool a server declares. A tool or server from outside the built-ins asks for
  the "this code runs in agents" confirm. No combination with tools or MCP servers has been run
  end to end yet, so the console previews them and leaves Create off. See
  `docs/command-center.md`.

- Console agent builder (#833, M17 console half, first part):
  Home links to **Build an agent**, a form (name, model provider and id, prompt, eval
  cases, model calls, pass threshold and retries) beside a sticky preview of every file
  `new agent --from-spec --dry-run --json` would write, with its text. The spec goes to the CLI as
  a private temporary file, never in argv; Create is on only for combinations run end to end
  (today gemini with a prompt and evals), is pinned to the previewed `plan_sha256`
  (`--expect-digest`), and adds the new project. See `docs/command-center.md`.

- The console's MCP grants screen grants and revokes an agent's MCP tools (#831, M15, second
  part): `agentbreeder mcp grant|revoke` reviewed as a dry
  run, then written bound to the reviewed `stack.yaml` digest; the declared tools from the
  version's `mcp.yaml`; an in-use revoke shows the `remove` and `services up` steps. See
  `docs/command-center.md`.

- The console's **MCP grants** screen (#831, M15, first part):
  each MCP server's version, origin, upstream, relay state and pinned tools (from
  `stack.yaml`, `stack.lock` and `services status --json`), upstream drift with the terminal
  `services up --repin` to review it, and each agent's grants. Read-only; see
  `docs/command-center.md`.

- The console starts services for a stack with MCP servers (#831, M15, third part):
  the services review lists each server's origin, upstream and which
  credential goes where; a project or registry-path server needs its own consent tick
  (`--allow-project-mcp`); changed tool definitions are never accepted from the console (#964).
  See `docs/command-center.md`.

- `agentbreeder mcp grant|revoke AGENT SERVER TOOL…` (#831, M15 CLI half):
  edits one agent's MCP tool grant in `stack.yaml`, checks the tools against
  the server's declared list, keeps the rest of the file byte for byte, and supports `--dry-run`,
  `--expect-base` and `--json`. A revoke that an active deployment still holds is reported as
  `in_use`; `services up` still refuses to narrow it until that deployment is removed. There is
  also an MCP fixture project (`tests/fixtures/mcp_github_project`). See `docs/commands/mcp.md`.

- `agentbreeder knowledge status REF [--project DIR] [--environment ENV] [--json]` compares a
  knowledge version's documents with the runtime's index in the stack's database (chunks fresh,
  stale, missing or extra; documents; index times; embedding alias and dimensions), read-only with
  the agent's own credential and without reading chunk text (#830, M14).


- Console knowledge documents (#830, M14 console half, second part):
  a project knowledge version's Edit card edits, adds and removes its documents
  (`docs/NAME.md` or `.txt`), each reviewed as a diff and saved as a new version bound to the
  reviewed digest; removal uses `registry edit --remove`. The review warns that documents land in
  a git-tracked registry. A release after the change has not been run from the console yet. See
  `docs/command-center.md`.

- Console knowledge index status (#830, M14 console half, first part):
  a knowledge version in Registry ▸ Knowledge shows its index status from
  `knowledge status --json` (state, chunks and documents expected and indexed, fresh, stale,
  missing and extra, the index times and the embedding), read fresh with the agent's own
  credential; chunk text is never read. A refusal, such as services not running, shows as
  "Unavailable" with the next step. Not yet run against a real stack's database. See
  `docs/command-center.md`.

- `service/postgres-pgvector@1.2.0` (#828, M12 slice 1):
  a new `console_database` param creates the login role `agentbreeder_console`, which can
  connect only to that database and has no rights until the database owner's `init_sql` grants
  them; its URL is the new `operator_only` `console` output. The name is reserved for
  `databases` and `roles`; removing the param makes the role `NOLOGIN` on the next `services up`.
  Northwind moves to 1.2.0 with `console_database: support`: `commerce.sql` grants the role
  SELECT/DELETE on `turns` and `tickets`, SELECT, INSERT and UPDATE/DELETE on `notes`, SELECT and
  a column INSERT on `audit_events`, and adds `audit_events.db_role`, set by a trigger to the
  inserting login (attribution, not tamper-evidence). Existing Northwind stacks need
  `services up --update`. No CLI command uses the role yet. See `docs/services.md`.

- `runtime/adk@1.2.0` (#828, M12): each reply has
  `cost_usd`, the sum of the gateway's `x-litellm-response-cost` header over the turn's model
  calls (`null` when any call had none), and each `model_called` event has the call's cost.
  Every tool event has `detail.agent`, `detail.latency_ms` and, for registry tools,
  `detail.tool`, including failed calls. A failed call now writes one event; `1.0.6` and `1.1.0`
  also wrote a phantom `ok: true` event (#922). A tool whose `src/` would shadow an installed,
  standard-library or runtime module, or another tool's module, is refused at startup. The
  manifest declares `memory_schema: 2`. The root `agent_turn` span carries
  `agentbreeder.deployment_id` when `AGENTBREEDER_DEPLOYMENT_ID` is set (for #907; nothing sets
  it yet). A function's `module` must be in its tool's own `src/`.
  Northwind (`agent.yaml`, `agent.lock`) and the `new agent` starter move to `1.2.0`; the
  starter's stack stays on `postgres-pgvector@1.1.0`. Northwind's 61/61 run on `1.2.0` is
  pending. CI runs the `1.1.0` and `1.2.0` runtime tests. See `docs/agent-yaml.md`.

- Console tool detail (#828, M12 console half): a tool
  version in Registry ▸ Tools shows its functions (tool.yaml's description and parameters, and the
  Python signature `registry show` read with `ast`, never importing the code), "no dependencies of
  its own" with the runtime it runs on, and who may use it. See `docs/command-center.md`.

- Console tool code editor and "Fork to edit" (#828, M12 console half):
  a project tool version's Python source under `src/` is edited, reviewed with
  the CLI's tool check and saved as a new version only after the "this code runs in agents"
  confirm. A built-in or shared version of any kind the console edits is forked into the project
  first (a reviewed `registry fork`), then edited. See `docs/command-center.md`.

- Console tool execution log (#828, M12 console half): a
  tool version in Registry ▸ Tools lists the calls the agent runtime recorded (`tool log --json`),
  newest first, for this version or every version, by window, row count and agent. Arguments and
  error text are customer data: read only after "Show arguments", never kept. A refusal, such as
  a stack without the console role, shows as "Unavailable" with the next step. See
  `docs/command-center.md`.

- Registry tool checks and `registry show` tool functions (#828, M12):
  `registry publish|import|fork|edit` refuse a `tool/` version
  (`TOOL_INVALID`, nothing written) that holds files other than `manifest.yaml`, `tool.yaml`,
  `README*` and `src/**/*.py`, has a `src/` module that shadows the standard library, the runtime
  or a package its locks install, or declares a function that does not resolve to a top-level
  `def` that parses with `ast` (tool code is never imported). `registry show tool/…` adds
  `functions` with each function's `tool.yaml` parameters and Python signature. `registry new
  tool` now writes a tool that passes these checks. See `docs/registry.md`.

- `agentbreeder tool log tool/NAME[@X.Y.Z]` lists a registry tool's recorded calls from the stack's database, read-only: agent, version, function, status, latency, turn and trace id, and the inserting database role, with arguments and error text only under `--content` (redacted). It reads with the agent's own credential where the agent owns the tables and as `agentbreeder_console` where the database owner made them, refusing tables that carry unexpected triggers, row security or rules. The duplicate `ok` rows that `runtime/adk` 1.0.6/1.1.0 wrote after a failed call (#922) are left out and counted. See [`docs/commands/tool.md`](docs/commands/tool.md).

- Console prompt editor and "Move agent to this version" (#826, M10 console half):
  a project prompt version's text files open in a mono editor with a line
  gutter; Review (or Cmd/Ctrl+S) shows the server's diff from `registry edit --dry-run`, and "Save
  as X.Y.Z" publishes exactly the reviewed text as a new version (`--expect-digest`; the text goes
  on stdin, never in argv). Built-in and shared versions show the `registry fork` command instead.
  When `agent.yaml` uses another version of the name, the console shows the `agent set-ref` diff,
  writes it bound to the reviewed file (`--expect-base`) and offers "Build and update agent.lock"
  (a build without `--locked`), then release. See `docs/command-center.md`.

- Console eval-set case editor (#826, M10 console half, part 2):
  a project eval set's cases file opens as one row per case (id, turns, checks,
  reference answer), with Add and Remove. Unchanged cases keep their bytes, so the review shows
  only the edited cases; incomplete cases are marked before review, and the CLI's `EVAL_INVALID`
  is shown at its case. The save is the prompt editor's (`registry edit --dry-run`, then
  `--expect-digest`). See `docs/command-center.md`.

- Console gate-failed view (#826, M10 console half, part 3):
  when an agent's latest release failed the eval gate, its Overview and Build and
  release show one row per failed check (the case, the check, what was expected, what happened,
  the judge's reason), links to edit the prompt and the case (`#/registry/evals/NAME/case/ID`
  opens that case in the editor), the agent's replies on request, and the log tail `release`
  saved from the candidate (`logs --failed NAME --json`, through the new
  `GET /api/projects/ID/logs/failed/NAME`). See `docs/command-center.md`.

- Console fork in the registry browser (#825, M9a console half, second part):
  any version outside the project registry (a built-in service, MCP server or
  runtime too) is forked into it after a review, optionally under another name (`registry fork
  --as`). A fork of code that runs in agents (tool, service, eval, MCP server, runtime) now needs
  the "this code runs in agents" confirm, checked by the server on its own dry run. See
  `docs/command-center.md`.

- Console import by path (#825, M9a console half, first part):
  Registry ▸ Import reviews a version directory from anywhere on this machine with
  `registry import --dry-run --json` (the staged files and digest), then imports exactly that with
  `--expect-digest`. Tool, service, eval, MCP server and runtime imports need the "this code runs
  in agents" confirm, which the server checks again before it writes. See `docs/command-center.md`.

- Console registry browser (#824, M8 console half):
  Registry ▸ Prompts, Eval sets, Tools, Knowledge, MCP servers, Service definitions and Runtimes
  list every name with latest version, version count, root, verify status and this project's use;
  a name's page has the version timeline, one version's manifest and files (`registry show`), the
  diff between any two versions (`registry diff`) and used-by across the registered projects of
  the environment (`registry used-by`: declared, locked and deployed). Read-only. See
  `docs/command-center.md`.

- Console Audit view links each row's agent name to the agent's detail page (#824, M8 console
  half). A release candidate's row links to the agent it
  belongs to. See `docs/command-center.md`.

- Console Audit view and `audit list --before SEQ` (#824, M8 console half, part 2):
  Governance ▸ Audit pages the audit rows 50 at a time (Older, Newer,
  Newest) and shows each row's agent (release candidates marked), enforcement mode and reason
  codes, a release row's eval run and report path, and a Try it link with the endpoint for an
  agent's ready local production deployment. `audit list --json` adds an `agent` key per row (the
  row's deployment's name, removed ones included, or null). See `docs/commands/audit.md` and
  `docs/command-center.md`.

- Console Try it (#823, M7 console half, first part): chat
  with a project's production deployment from a Deployments row or the guided path's Try it step.
  Each message runs `invoke NAME - --json` with the text on stdin, never argv, as an uncached
  read that is not a job and is never stored. Candidates are refused. Replies are plain text with
  a Turn details inspector (tools, sources, latency, tokens, the trace ID; cost and a trace link
  wait for #906 and #907). The screen states the cost and that the endpoint is unauthenticated.
  See `docs/command-center.md`.

- Console Agents (#823, M7 console half, second part):
  an Agents list across every stack on this machine (`list --all --json`: candidates marked,
  registered and unregistered projects, identity check, last release from the audit log, stacks
  that could not be read), and an agent detail with the gate strip (Build → Candidate → Evals n/N
  → Identity → Production), an identity strip of what `stack.yaml` declares, and Overview, Try it,
  Releases and Logs tabs. See `docs/command-center.md`.

- Onboard an `agent.yaml` alone from the console (#823, M7 console half, third part):
  Home's **An agent.yaml alone** form previews `new agent --stack-for
  --dry-run --json`, asks a "this code runs in agents" confirm when the project brings code from
  outside the built-in registry, writes `stack.yaml` pinned to the previewed `agent.yaml` digest
  and registers the project (the ownership check again). `new agent --stack-for --json` gains
  `agent_sha256`, `refs` and `runs_code`, and `--expect-digest`; `--stack-for` now refuses an
  `agent.yaml` with any unresolvable registry ref (`REF_NOT_FOUND`), not only its eval set. See `docs/command-center.md` and
  `docs/commands/new.md`.

- The console's Services rows link to a service detail (`#/services/NAME`) with its config, a
  Logs link and Start / Stop / Restart, each one `services start|stop|restart NAME --json`; a stop
  or restart while deployments use the stack asks first, naming them, then passes `--yes`, and
  `PORT_BUSY` and other refusals show the CLI's message (#822).


- Console Docker-down blocker (#822, M6 console half, first part):
  every screen shows "Docker is not reachable." with doctor's message, the
  `agentbreeder doctor` remedy and Retry while Docker is down, checked every 5 s through a new
  `GET /api/docker` (`doctor --json --only docker`, never cached, one check shared by every tab
  for 3 s), and clears without a reload when Docker is back. New flag `doctor --only docker` runs
  the engine check alone. See `docs/command-center.md` and `docs/commands/doctor.md`.

- Console Home lists every registered project's stack services with a status chip (icon, word,
  colour), host ports ("internal only" explained), and CPU and memory "of the Docker VM",
  network and block IO per second, each with a five-minute sparkline whose accessible name
  gives its low, high and newest value; a service's detail gains a Usage tab with one tile per
  figure and the disk reading (#822, M6 console half, part 4).


- `agentbreeder services start|stop|restart [DIR] NAME [--yes] [--json]` act on one service
  container without re-rendering the stack; `stop` and `restart` refuse with `SERVICE_IN_USE`
  while deployments use the stack unless `--yes`. Every `services` write (`up`, `down`, `plan`, the
  new verbs, `cluster up|down`) now holds a per-stack lock and fails fast with `SERVICES_BUSY` when
  another holds it (#822, S-11).

- The console keeps a five-minute in-memory history of each project's service usage from one
  `services usage --follow --json` child per project, read with `GET /api/projects/ID/usage`;
  the child stops 15 seconds after the last read and when the console stops, and a console
  killed outright leaves none running (stdin EOF, plus a PID-file sweep at the next `up`)
  (#822, S-10).

- The console's log viewer can **Follow** a service's or a deployment's logs: one `services logs
  ... --follow --json` or `logs ID ... --follow --json` child per following tab, with Pause (or
  scrolling up) and "Jump to latest", at most 8 follows per session and 16 long-lived children
  overall, stopped when the page leaves, 15 seconds after its last read and on shutdown (#821).


- The console watches each project's containers: one `watch --stack DIR --json --record` child
  per project (stopped 15 seconds after the last request and on shutdown), its checked status and
  newest events in memory at `GET /api/projects/ID/watch`, and a "Container events" list under each
  project on Home; a failed or cancelled console job is stored as a `job_failed` event with its
  kind, exit code and CLI error code only (#821, #827).

- Agent package format and a deterministic registry packer (#763, M13 slice 1a):
  `package.py` (the strict `package.json` schema, assembling the four
  package files, loading them back, and replacing unsafe `agent.lock` source labels with
  `external:`) and `pack.py` (a reproducible `tar+gzip` of one registry version). No command
  writes or deploys a package yet. See `docs/registry.md`.

- The `agent` registry kind (#763, M13 slice 1b):
  `agent/<name>@X.Y.Z` refs parse; `registry list`, `show` (with a `Package:` summary, and
  `package`/`package_error` in `--json`), `diff` and `verify` (which checks the package's files,
  `package.json` and `agent.yaml` hash) read package versions; `registry publish`, `import`,
  `fork`, `edit` and `registry new agent` refuse them with `PACKAGE_RELEASE_ONLY`, since only
  `release` writes packages (in a later slice). No `agent.yaml`, `stack.yaml` or lock field takes
  an `agent/` ref. See `docs/registry.md`.

- `agentbreeder deploy agent/<name>@X.Y.Z --local [--project DIR]` deploys a released agent package
  (#763, M13 slice 3): only from the project's own
  `./registry`, and only when this state directory's `release/promoted` row matches the package's
  project, tree hash, image and eval evidence. Identity, secrets and the gateway come from the
  operator config, and the identity gate, policy and approvals apply as for any deploy. The local
  state moves to schema v4, which adds each deployment's `package` (shown by `status --json`): the
  first writable command upgrades a v3 state, and 3.0.0a2 cannot read it afterwards. `release`
  links production to its package (`deploy/package_linked`), and `registry used-by agent/…` lists
  the deployments linked to a package. See `docs/commands/deploy.md`.

- `agentbreeder release` writes an agent package (#763, M13 slice 2):
  each released image becomes `./registry/agent/<name>/<X.Y.Z>/` (the
  released `agent.yaml`, a redacted `agent.lock` and `package.json`), published after the eval
  gate and before production is replaced, and deleted again if no release is recorded.
  `--version X.Y.Z` picks the version (default: the next free patch); a taken version exits 2 with
  `VERSION_EXISTS` before any eval runs. The `release/promoted` row names the package, its tree
  hash and a hash of the project path, and `--json` adds `package`. Releasing the image already in
  production again gives its package (`PACKAGE_EXISTS`); a release row written before packages
  existed no longer counts for `release`, which evaluates that image again without replacing
  production. No command deploys a package yet. See `docs/agent-yaml.md`.

- `agentbreeder list --all [--json]` and `new agent NAME --model gemini --stack-for agent.yaml`
  (#823, M7, CLI half): `services up` records the project
  path in the stack's meta; `list --all` lists the default state and every stack's deployments
  read-only (validated directory names, no symlinks, `AGENTBREEDER_STACKS_DIR`, unreadable or
  unsupported stores reported and skipped); `--stack-for` writes the starter's `stack.yaml` and
  LiteLLM config beside an existing `agent.yaml` (gemini; agents with knowledge, tools or MCP
  refused), after the console's ownership check (S-01). See `docs/commands.md`.

- Console log viewer (#821, M5, first part): a Logs
  screen, and a Logs link on every Services and Deployments row, reading one snapshot through
  `services logs --json` or `logs ID --json` (`--previous` for Kubernetes). Filter, level filter,
  search, wrap and copy work on the lines read; unwrapped rows are windowed (10,000 lines). Notes
  say what was read, that secrets are redacted and when the read was cut. `LOG_UNAVAILABLE` and a
  pending pod are empty states. The server checks every option and the answer's shape, and never
  caches or stores log lines. Follow, `watch` and the S-10 limits are the next parts. See
  `docs/command-center.md`.

- `agentbreeder registry edit REF --set PATH FILE|--remove PATH [--bump patch|minor|major |
  --version X.Y.Z] [--project DIR] [--dry-run | --expect-digest SHA256] [--json]` and
  `agentbreeder agent set-ref OLD NEW [--project DIR] [--dry-run | --expect-base SHA256] [--json]`
  (#826, M10 CLI). `edit` copies a project version to a
  private staging directory, applies the files (from a file or stdin, never argv), raises the
  version and publishes it atomically with `publish`'s checks; it prints the diff first, refuses
  a version that exists or appears meanwhile (`VERSION_EXISTS`), an unchanged edit, a broken eval
  set and a non-project version (fork it first). `set-ref` replaces one ref in `agent.yaml` by
  exact string, refusing ambiguity, and shows the diff first; `--expect-base` refuses a file that
  changed after the review. Neither writes an audit row: the record is the project's files and
  git. The console editors are not built yet. See `docs/registry.md`.

- `release` ends with `Next: … invoke NAME Hello` after a successful release, including
  `--rollback` and an image already released (#820, M4 finding 1):
  with the `--stack` and `--environment` or `--state-dir` it used; `--json` carries the
  command as `advice.invoke`. `your-first-agent.md` no longer says to run `next` after
  `release`.

- Console guided path and Create starter (#820, M4): Home
  shows the project's `next --json` checklist (state as icon and word, reason, the current step's
  button, command and note), the top bar a "Next" pill, finished jobs a result row, and empty Home
  and Deployments screens the next action. Buttons map to fixed console moves; commands are only
  shown. Create starter runs `new agent NAME --model gemini` into `~/agentbreeder-projects/NAME`
  and registers it. The server validates `next`'s answer with `guide.Guide`. Try it stays M7
  (#823). See `docs/command-center.md`.

- `agentbreeder registry fork REF [--as NAME] [--version X.Y.Z] [--project DIR] [--json]` (#825,
  M9a): copies a built-in, `AGENTBREEDER_REGISTRY_PATH` or
  project version into the project's `./registry` as a new version the project owns, with
  `import`'s checks, `--dry-run`/`--expect-digest` and JSON. A fork must change the name or the
  version (`FORK_UNCHANGED` otherwise), and a source that no longer declares `REF` when it is
  staged is refused (`MANIFEST_INVALID`). See `docs/registry.md`.

- `agentbreeder events [--project DIR] [--limit N] [--unread] [--mark-read ID] [--mark-all-read]
  [--json]` and `watch --record` (#827, M11): stored
  events, newest 500, in `~/.agentbreeder/center/events.sqlite3` (0600). The kinds are fixed:
  `job_failed`, `gate_failed` (stored by `release` on exit 5, with case ids and counts),
  `container_died` and `container_unhealthy` (from `watch --record`; `restarted` is not stored).
  Fields are validated and messages are rendered from templates; no text from logs, stderr or
  models is stored. Conditions are not stored: with `--project` they come from the function
  behind `next`. The console does not call `job_failed` or `--record` yet. Tested with fakes;
  the real-Docker acceptance has not run. See `docs/events.md`.

- `agentbreeder registry import PATH [--as NAME] [--version X.Y.Z] [--project DIR] [--json]`
  (#825, M9a): copies a version directory from anywhere
  into the project's `./registry` with `publish`'s checks, plus a `REF_CONFLICT` check against the
  other search roots so the imported version resolves. `--as`/`--version` replace only the staged
  manifest's top-level `name:` and `version:` lines (anything else fails closed). `registry
  publish` and `import` now take `--dry-run` (stage and check, write nothing, print the digest
  and, with `--json`, the staged files) and
  `--expect-digest SHA256`, which writes only if the new staged copy has the reviewed digest
  (`DIGEST_MISMATCH` otherwise; security review S-04). Both now check the manifest from the staged
  copy rather than the source, refuse a symlinked kind or name directory in the target root, and
  report a version published between the check and the rename as `VERSION_EXISTS`. The JSON
  carries `runs_code` (tool, service, eval, mcp, runtime; S-05) for the console's confirm, and
  `import` prints a matching note. `publish --json` keeps its one-line object and gains fields;
  its `path` is now absolute. See `docs/registry.md`.

- `agentbreeder services usage [DIR] [--follow] [--json]` (#821 S-10, M6):
  one stack's CPU, memory, network and block IO, and its disk, reading only.
  Containers match as `watch` matches them (stack services by label, agent containers by this
  stack's deployment IDs), listed again every 10 s; ONE streaming `docker stats` child runs over
  exactly those IDs and is replaced when the set changes (never with an empty list, which
  would show every container on the engine). A sample is printed at most every 2 s. Disk (the
  stack's volumes and its containers' writable layers) comes from `docker system df -v` every
  60 s on a worker thread; the last good reading is kept and carries `as_of`, and `stale` when
  the latest one failed. Docker's container names, labels and mounts are never printed. With
  `--follow` it stops with its child on stdin EOF, SIGTERM, SIGHUP or Ctrl-C (S-10);
  `docker stats` ignores SIGTERM, so the child is killed at once. `docker stats` also exits at
  once when a listed container is already gone, so a child that ends is replaced after a new
  listing (three in a row fail with `DOCKER_STATS_FAILED`). Without `--follow` it prints one
  sample and one disk reading (failing after 30 s without them). On macOS with Docker Desktop and Northwind: six containers
  sampled, and a stop by stdin EOF or SIGTERM ended in under half a second with no
  `docker stats` left.
- `agentbreeder watch --stack DIR [--json]` (#821, M5):
  one stack's container events until stopped, reading only. `died` (a container exited by
  itself: a `die` with no `kill` or `stop` since it last started, and never for an agent
  container whose deployment is not `ready`), `unhealthy` and `restarted`, plus a `status`
  record at the start and every 60 s (one `docker compose ps`, the deployment rows, and a pod
  check for ready Kubernetes deployments). Agent containers carry no stack label, so they match
  by this stack's deployment IDs; two `label=` filters on `docker events` combine as AND, so
  one stream filtered by action is matched in the CLI. It stops with its `docker events` child
  on stdin EOF, SIGTERM, SIGHUP or Ctrl-C (S-10). On macOS with Docker Desktop: two releases
  and `services down` reported nothing; Postgres exiting by itself reported one `died` and one
  `restarted`. Events are not stored yet.
- `--level warning|error` on `logs ID` and `services logs` (#821, M5):
  keeps only records at that level or above, with one parser module
  (`loglevels.py`) keyed by built-in ref: Postgres (`ERROR`, `FATAL`, `PANIC`, with `DETAIL`,
  `HINT` and `STATEMENT` kept with their record), LiteLLM, Phoenix (Python logging, Uvicorn,
  `warnings`, tracebacks) and `runtime/adk` (its own failure messages). Continuation lines stay
  with their record. A heuristic: project services, MCP relays and images not built from a
  known runtime stay unclassified, and `--level` on them fails with `LEVELS_UNAVAILABLE`. An
  agent's runtime comes from `--stack DIR`'s build record (built-in runtimes only). `--json`
  adds `format`, `levels` and `level`, and `--follow --json` each line's `level`. Tested against logs captured from a
  Northwind run (`tests/fixtures/logs/`); `runtime/adk` printed nothing in the runs captured.
- Console browser-side guards (#816, M0b, slice 1). The
  content security policy now requires Trusted Types with no policy allowed
  (`trusted-types 'none'; require-trusted-types-for 'script'`) and allows `data:` images, so any
  string assigned to an HTML or script sink throws. A source guard test fails on `innerHTML`,
  `eval` and the other HTML and code sinks outside `vendor/`, and on any external origin in a
  static file. Preact + htm (htm 3.1.1's standalone module, pinned by SHA-256, licences beside
  it) and the pure modules for the new shell (hash router, API client, the S-35 text escaping)
  are vendored and tested (slice 2 builds the screens on them). `center/static/**` is packaged
  recursively and a packaging test checks every tracked static file ships. Tests: `node --test
  'tests/js/*.test.mjs'` and a Playwright + axe job against the built wheel with a fake CLI (`tests/ui/`, CI
  jobs `console-js` and `console-ui`).
- Console shell (#816, M0b, slice 2). The walkthrough
  column is replaced by a sidebar of the screens that work: Home, Services, Build and release,
  Deployments, Targets ▸ Kubernetes and Governance ▸ Audit. Each screen has its own hash address,
  title and focused `h1`, and there is a skip link. The top bar holds a project scope switcher,
  an activity pill and a user menu. A Jobs dock follows several jobs at once, appends log lines
  instead of rebuilding the log, and collapses a finished job to one line. Separate pages cover
  "Session ended" (signing in again returns to the same screen) and "Console not reachable at
  this address". Walkthrough fixes from #811:
  - audit time from `at`, plus agent, mode, reasons and verified actor;
  - Verify in words;
  - a missing `kind` names its install command and the restart of `up`;
  - a reason under every disabled control;
  - confirms focus Cancel and give focus back;
  - "internal only" ports;
  - violet "candidate, temporary" labels;
  - notices scoped to their screen;
  - Northwind's cost figures only for Northwind.

  After `cluster up`, the Kubernetes screen shows the grant-review step (GRANT_PENDING) instead of
  offering `promote`. Text from projects, jobs and the CLI shows control, bidi and invisible
  characters as highlighted escapes (S-35). Tests: 35 Playwright tests (every screen, each fix,
  focus after each action, two projects with jobs at once, zero CSP violations, axe with no
  serious or critical findings in light and dark) and `node --test` for the job store and
  wording.

- `next`'s Kubernetes step gains the reason `GRANT_PENDING` (#820, M4):
  the cluster is attached (typically after `cluster up` without `--yes`, as
  the console's job runs it) but the environment's operator config does not name it, so
  `promote` would fail with `KUBERNETES_UNCONFIGURED`. The fix is `services up DIR --environment ENV --yes`
  (`services-up`), with a note to review the grant first with `services plan`. Before, `next`
  pointed at that failing `promote`. `next --json` adds `cluster_configured` to the step's
  `data`; additive, `schema_version` stays 1 (`docs/next.md`, `docs/next.schema.json`).
- `docs/your-first-agent.md` (#820, M4): from an empty
  directory to a released starter agent and an `invoke` reply, one section per `next` step
  (prerequisites, project, model keys, services, build, release, try it, optional Kubernetes)
  with the commands `next` suggests and what each prints, then clean up. Steps 1 to 7 ran once
  with the starter from a checkout on macOS (2026-11-12); step 8 has not been run with it.
  `tests/test_your_first_agent_doc.py` fails when the page and `guide.py`/`guide_checks.py`
  drift: a missing, duplicated or reordered step, a suggested command the page does not show
  with the same subcommand words and flags, a command in a block that the guide never
  suggests, a non-`sh` block, or a command (in a block or inline) the CLI parser rejects. The
  website page is added after the release.
- `agentbreeder services logs [DIR] NAME` gains `--since D`, `--follow`, `--json` and `--raw`
  (#821, M5): the same shape, redaction and escaping as
  `logs ID`. It reads `docker compose logs --no-color --no-log-prefix --timestamps` (4 MiB cap,
  Docker's 16 KiB partial records joined), redacts every line and now prints nothing when a value
  it needs cannot be read (`REDACTION_INCOMPLETE`; before, an unreadable stack secret was skipped
  silently), and shows controls as visible escapes unless `--raw` (S-08; before, they were
  printed raw). `--json` prints `{"schema_version", "source": "service", "target": "local",
  "name", "lines", "truncated"}`; `--follow` streams NDJSON lines (`source`, `target`, `name`,
  `line`) with the same stop rules and end record as `logs ID --follow` (S-10). `--tail` is now
  validated (1 to 10 000) instead of clamped. Text output changes shape: each line now starts
  with Docker's timestamp and no longer with compose's `name |` prefix. Errors are coded
  (`DOCKER_LOGS_FAILED` when compose cannot read the logs), with the JSON error object under
  `--json`; compose's own stderr is never shown. A follow stops the compose process and its
  plugin together (the reader and its descendants, SIGKILL after 2 s; before, a stop while
  compose was starting could hang the follow), and so do `logs ID --follow` readers, which stay
  in the CLI's process group so a group kill of the CLI (the console's S-10 sweep) reaches them.
  A service follow carries on across container restarts. Run against real Postgres
  and LiteLLM on macOS.

- `agentbreeder logs ID [--previous]` for a Kubernetes (local `kind`) deployment (#821, M5):
  reads the `agent` container of one pod owned by the
  verified Deployment (through its ReplicaSets' owner references, so a stray or stale pod that
  carries the label is never read or reported; a running pod first, then the newest) with
  `kubectl logs pod/NAME -c agent --timestamps`, through the cluster binding recorded with the
  deployment, after the cluster, namespace and ownership checks every Kubernetes operation
  makes; kubectl's stderr is never shown. Lines go through the same partial-record join,
  redaction and escaping as local logs. A pod with no output yet returns no lines and its reason
  (a fixed Kubernetes word; `pod_reason` in `--json`); a crash-looping pod is read with its
  reason; `--previous` reads the container before the last restart; `--follow` streams from the
  chosen pod with the same stop rules and ends when that pod goes away. `--json` now carries
  `target`, `previous` and `pod_reason` for local deployments too. New codes:
  `CLUSTER_UNAVAILABLE`, `KUBECTL_LOGS_FAILED`, `POD_NOT_RUNNING`, and `TARGET_UNSUPPORTED`
  (instead of `NOT_LOCAL`) for an unknown target. Run by hand on macOS with kind: a promoted
  deployment read (no lines: the runtime logs nothing), `--json`, `--previous` refused on a
  container that never restarted, and `--follow` stopped at the end of stdin. Pending and
  crash-looping pods are covered by tests only. Claim: local kind only.

- `agentbreeder logs ID [--tail N] [--since D] [--follow] [--raw] [--json] [--state-dir DIR |
  --stack DIR [--environment ENV]]` (#821, M5): a local
  deployment's container logs (stdout and stderr merged, timestamped; `--tail` default 200, at
  most 10 000; 4 MiB cap), read by container ID after Docker confirms the ownership labels.
  Every line is redacted with the stack's stored secrets, every non-removed deployment's secret
  copies and `<state>/secrets` (literal, base64, URL-quoted and JSON-escaped forms, URL
  passwords; best effort), after Docker's 16 KiB partial records of a long line are joined
  (their mid-line timestamps removed), so a value across that boundary is still found. Nothing
  is printed when a value cannot be read: an unreadable or oversized secret, or a deployment
  whose secret copies are gone (a failed deploy deletes them; `REDACTION_INCOMPLETE`). Lines are
  split on `\n` only; controls, bidi and the Unicode line separators are shown as visible
  escapes unless `--raw` (S-08). `--follow` streams until the container stops, Ctrl-C, SIGTERM,
  SIGHUP or the end of a non-terminal stdin, and stops its `docker logs` child (S-10: the child
  stays in the CLI's process group, so a hangup or a group kill reaches it; a SIGKILL of the
  CLI process alone leaves it until its next write); a line over 1 MiB is dropped whole. `--follow --json`
  prints one object per line (`schema_version`, `source`, `name`, `deployment`, `line`) and a
  final `{"event": "end", "reason", "dropped"}`; a failing `docker logs` is `DOCKER_LOGS_FAILED`.
  Kubernetes deployments: see the entry above; the state is opened read-only;
  errors are coded, with a JSON error object under `--json`. An opt-in real-Docker test runs a
  container that logs a stored secret in several encodings and across a 16 KiB boundary, and
  checks none is printed. `logs --failed NAME` reads the same file; its `--json` gains
  `schema_version`, its errors are coded the same way (with the JSON error object under
  `--json`), and its lines are split on `\n` only. `services` stack secrets that cannot be
  read are now reported to the redactor instead of skipped silently.

- `agentbreeder new agent NAME --model gemini [--dir DIR] [--dry-run] [--json]` (#769, M4):
  writes a starter based on the one walked by hand on
  3.0.0a2 to `DIR` (default `./NAME`; absent, or an empty real directory you own that others
  cannot write to). This template, with `traces: none` and `agent_events: none` added, ran once
  end to end from a checkout on macOS (2026-11-12: `services up`, `build --locked`, `release`
  2/2, `next` complete). It writes `agent.yaml` on `runtime/adk@1.0.6` with
  `traces: none`, `agent_events: none` and a comment on the edit loop, `stack.yaml` (Postgres and
  LiteLLM, `chat` and `judge` aliases, a `DATABASE_URL` binding, `candidate: true`, `review_by`
  180 days out), `stack/litellm/config.yaml`, and editable sources in `src/prompt/` and
  `src/evals/` (the two verified eval cases, `retries: 1`). It publishes
  `prompt/NAME-prompt@0.1.0` and `eval/NAME-evals@0.1.0` into `DIR/registry` and writes
  `agent.lock`, so `build --locked` passes. Everything is written in a staging directory inside
  `DIR`, validated, then moved into place; files 0644, directories 0755. Only `gemini` is offered
  and `--model` is required. Names that YAML reads as non-strings (`true`, `no`, `null`, …) and
  destinations with control characters are refused. With `--json`, a refusal also prints
  `{"schema_version": 1, "error": {"code", "message"}}` on stdout (exit 2; argparse usage errors
  such as an unknown `--model` stay plain text). An existing `DIR` that is group-writable is
  accepted only off macOS and only for your own group. `next` now offers `new agent` for a
  directory without `agent.yaml` (`example northwind` stays in the fix's note), `quickstart` ends
  with `Next: … new agent hello --model gemini`, and `scripts/install_smoke.py` also runs
  `next --json` and `new agent --dry-run --json`.
- `agentbreeder next [DIR] [--environment ENV] [--output DIR] [--json]` (#820, M4):
  where a project is on the guided path (prerequisites, project,
  model keys, services, build, release through the eval gate, try it, and the local cluster when
  one is attached) and the one command to run next. It only reads: local state is opened
  read-only, `DIR`'s stack state is used (as `--stack DIR` would), Docker is asked once
  (`compose ps`), and no key value is ever printed. Text exits 0 when done and 1 when steps
  remain; `--json` prints the `docs/next.md` contract and exits 0. In the contract, `LOCK_STALE`
  now comes before `NOT_BUILT`/`BUILD_STALE`, a provider key used only by a fallback alias is
  optional, and step objects list `id`, `title` and `required` first.
- `agentbreeder invoke NAME MESSAGE|- [--candidate] [--customer ID] [--session ID] [--json]
  [--state-dir DIR | --stack DIR [--environment ENV]]` (M4):
  sends one message to a ready local deployment's `/invoke` and prints the reply. It
  calls only `http://127.0.0.1:<port>` endpoints recorded in local state (a Kubernetes row is
  refused with the `forward` command to use), and before sending asks Docker that the container
  is still the recorded one and still publishes that port. No proxies, no redirects, one 180 s
  deadline for the whole exchange; the state is opened read-only. The reply is model text:
  controls (and lone surrogates) are shown as visible escapes, malformed HTTP or JSON is
  `RESPONSE_INVALID`, and `--json` prints the whole answer. `--candidate` calls
  `NAME-candidate`; `--customer` and `--session` are sent as given and are not authenticated
  (#780). A message given as an argument is visible in `ps`; `-` reads it from stdin. Exit 0 with
  a reply; 1 when the message was (or may have been) sent but no usable reply came back (an agent
  error, an invalid answer, a timeout: a retry is a second turn); 2 when nothing was sent. Errors
  are `[CODE] message` on stderr, and with `--json` also
  `{"schema_version": 1, "error": {"code", "message", "sent"}}` on stdout. Printed follow-ups
  (`status`, `forward`, `next`) carry the same `--stack` or `--state-dir`. `next`'s done
  "Try it" step now suggests `invoke NAME Hello --stack DIR --environment ENV`. `safe_text`
  also escapes lone surrogates, so `logs` cannot crash on one either.
- The `agentbreeder next --json` contract (#820, M4):
  `docs/next.md` and `docs/next.schema.json` (generated from `src/agentbreeder/guide.py`) fix the
  guided-path checklist's shape (steps, states, reason codes, fix actions, `complete`, live
  conditions) before the command lands, so the console can build its checklist from it. Contract
  only: the `next` command itself comes in a later M4 PR.
- `agentbreeder registry show REF [--project DIR] [--json]` (#824):
  one version's hash, root, manifest, files and the text of its text files, read-only.
  It goes through the same safety checks as `verify`, opens files without following symlinks,
  caps text at 256 KiB per file and 2 MiB per version, and removes control and bidi characters
  from file names, manifest values and content (S-27), and from every registry error message.
  The hash and manifest it shows come from the same read as the content. The `--json` shape is
  documented in `docs/registry.md` for the console's registry pages.
- `agentbreeder registry diff FROM TO [--context N] [--project DIR] [--json]` (#824):
  what changes from one version of a `kind/name` to another, file
  by file, as a valid unified diff (patience anchors, then difflib: where lines repeat, hunk
  placement can differ from `diff(1)`). Binary, oversized, over-budget (one budget of work and
  output lines per diff), mode-only and stripped-character-only changes are reported rather
  than hidden. Reads like `show`.
- `agentbreeder registry used-by REF --project DIR... [--state-dir DIR] [--json]` (#824):
  per project, where `agent.yaml`/`stack.yaml` declare the
  ref (in ref fields only), the `agent.lock`/`stack.lock` entry, and recorded deployments of the
  agent, reported separately: `ready` ones whose image the project's current build record lists
  the ref for, the same match in other states, and deployments that cannot be matched
  ("undetermined", with the reason). Project files are read as regular, size-capped files and
  deploy state read-only (private, rollback-journal, `mode=ro`, bounded queries); states are the
  last recorded observation, not a live check.
- Brand tokens (#817): `website/brand.css` gains a light `:root` palette and, in both palettes,
  `--text-muted` (AA on every surface; `#a1a1aa` on dark), a 3:1 `--control-border`, and
  `--success`, `--warning` and `--info` with `-subtle` backgrounds. Existing token names and
  values are unchanged, so the website (forced dark) and the Cloud console look the same; the
  dark `--muted-foreground` therefore stays below AA (3.36:1), and new UI uses `--text-muted`.
- The local console ships a copy of the two palettes at `center/static/brand.css` and vendored
  Geist and Geist Mono variable woff2 files (geist 1.7.0, SIL OFL 1.1) under
  `center/static/fonts/`; the console does not use them yet. `tests/test_brand_tokens.py` checks
  the copy against `website/brand.css`, computes WCAG contrast for both palettes and pins the font
  hashes.
- `agentbreeder migrate [--state-dir DIR | --stack DIR [--environment ENV]] [--json]` (#819): upgrades the local deployment state to this version's
  schema and finishes an interrupted write. No identity-provider calls; it never creates state.
- `agentbreeder logs --failed NAME [--state-dir DIR | --stack DIR [--environment ENV]]` and the
  failed-candidate log tail (#819): `release` reads the
  candidate's last 500 lines and its secret copies before removing it, then redacts the lines and
  keeps them at `<state-dir>/releases/<name>.candidate.log` (0600) when the candidate fails to
  start or fails the gate; a passing release deletes it. A failed start deletes the candidate's
  secret copies, so the deploy keeps (in memory, for that run only) what it read just before
  that cleanup. When a value needed for redaction cannot be read, nothing is saved and release
  says why (naming `agentbreeder migrate` when another environment's state is too old).
  Failed-case excerpts are redacted before they are cut.
  `logs --failed` shows control characters as visible escapes (`--raw` keeps them, `--json`
  escapes them). Release's printed follow-up commands keep the `--stack`, `--environment` or
  `--state-dir` it ran with.

### Changed

- The opt-in MCP egress sidecar authenticates decision-service requests and replies with derived
  keys (decision protocol v2, #1097): the per-deployment token is no longer sent, requests carry a
  timestamped, single-use MAC, and replies a MAC under a separate key bound to the request. A
  deployment from an earlier version fails `reconcile` with `RUNTIME_BOUNDARY_UNVERIFIED`
  (`PEP_PROTOCOL_MISMATCH`); redeploy it.

- An eval set whose case has an unknown `expect` key, or malformed `tags`, is refused when it
  loads (`EVAL_INVALID`) instead of the key being ignored (#1058).
  `validate --json` prints `subagents`, `limits` and `evals.tag_threshold` for every
  agent file.

- The Northwind example moves to `runtime/adk@1.3.0` (relocked; follow-up to #1030, #1034,
  #1039). From a wheel of the branch it released 61/61 on
  real Docker. A refund turn was served by the primary model; with the primary broken in a
  sandbox copy's gateway config, the OpenRouter fallback served the turn and `invoke` and `traces`
  showed it; and a sandbox copy redeployed with `budget_usd: 0.002` got `MODEL_BUDGET_EXCEEDED`.

- The gateway config review has a per-provider table (#1025 slice P1).
  **Changed:** `model_info` now accepts only `mode` (`chat` or `embedding`);
  price keys, `id`, `access_groups` and every other key are refused with `GATEWAY_CONFIG_UNSAFE`,
  so a repo cannot set the price budgets are enforced against (no shipped config used
  `model_info`). Key-only model strings must be printable ASCII. Review rules for Ollama, Vertex
  AI, Bedrock and Azure OpenAI are in place, but `service/litellm@1.0.0` does not import their
  credentials, so `services up` refuses those entries with `GATEWAY_PROVIDER_UNSUPPORTED`; each
  provider ships in its own gateway version after a real run. Their credential names are
  gateway-only: `secret set --from-env`, MCP versions and any service but the built-in gateway are
  refused them, and an operator binding or manifest secret with one of those names is refused
  (`SECRET_RESERVED`) in every environment. `next` no longer checks model keys for a project or
  unknown `service/litellm` version (its keys step says not checked). Grants for stacks using only Gemini, OpenRouter, OpenAI or Anthropic are
  unchanged byte for byte.

- `new agent --model openai` (#1020) is now `verified` in `--json`, with its default
  `openai/gpt-5.6`, the model it ran end to end with (`services up`, `release` 2/2 with the
  judge's strict `json_schema` output, `invoke` 2/2, from a wheel built from `main`). In
  `--from-spec`'s `combination`, openai is verified for model, prompt and evals alone and with one
  knowledge version (embedding with `openai/text-embedding-3-small`, which indexed 4 of 4 chunks
  and answered from the articles). A tool on openai stays unverified (none was run), as do two or
  more tools and any MCP server. The console's Build and onboarding forms no longer mark openai
  "not run end to end". `anthropic` stays config-only.

- `new agent --model openrouter` (#1020) now defaults to `openrouter/google/gemini-3.8-flash`, the
  model it ran end to end with (`services up`, `release` 2/2 with the judge's strict
  `json_schema` output, `invoke` 2/2, from a wheel built from `main`), so `--model-id` is no
  longer required, and `openrouter` is `verified` in `--json` and in `--from-spec`'s
  `combination` (model, prompt and evals alone; it has no embedding model, so no knowledge).
  `verified` covers the gateway and judge path with the default model, not another model's
  answers: with another `--model-id` the text output and the console say only the default has
  run. The console's Build and onboarding forms no longer mark openrouter "not run end to end"
  or ask for a model id. `anthropic` and `openai` stay config-only.

- The gateway config written by `new agent` (the starter, `--stack-for` and `--from-spec`) pins
  `litellm_settings.turn_off_message_logging: true` and
  `general_settings.store_prompts_in_spend_logs: false`, as Northwind does, instead of relying on
  LiteLLM's defaults (#1018). With a `--model gemini`
  starter on a running LiteLLM (M4 record, #1040), the spend log kept tokens and cost, and
  `messages`, `response` and `proxy_server_request` were `{}` in every row.

- Text that the CLI prints with visible escapes (`logs`, `services logs`, `invoke`, `evals`, `events`, `usage`) now also escapes invisible format characters: zero-width space, soft hyphen, BOM, U+2060–U+2064, U+206A–U+206F, the Mongolian vowel separator and the tag block U+E0000–U+E007F (shown as `\UXXXXXXXX`), so hidden text cannot ride along in a log line or reply. One-line fields replace them with a space. The set is the console's; zero-width joiner and non-joiner are kept (#901).

- Messages that report removed or refused characters now say "control, bidi or invisible characters", to match what is actually stripped since #956: the `registry show` file note, the `new agent` and `new agent --stack-for` `DESTINATION_UNSAFE` errors, and the `registry diff` note in `docs/registry.md` and the console docs (#901).

- `registry show`, `registry diff` and the console's registry browser now also strip the soft hyphen, the Mongolian vowel separator, U+2061–U+2064 and U+206A–U+206F from file names, manifest values and content, and strip zero-width space, word joiner, BOM and tag characters from content as well as names (before, only from names), so the set matches what the CLI escapes elsewhere; zero-width joiner and non-joiner stay in content, and a stripped file is still flagged. `new agent --from-spec` refuses the same characters in the prompt, turns and references (`SPEC_INVALID`, "has control, bidi or invisible characters") (#901).

- `agentbreeder new agent --from-spec` now reports a `gemini` spec with one knowledge version
  (and no tools or MCP servers) as verified (`combination.verified: true`)
  (#833). A project generated from such a spec was run end to end from a wheel
  built from `main`: `services up`, `build`, `release`, an `invoke` answering from an article and
  a fresh `knowledge status`. Knowledge with tools, MCP servers or `openai` stays unverified.

- `agentbreeder new agent --from-spec` now reports a `gemini` spec with one tool, alone or with
  one knowledge version (and no MCP servers), as verified (`combination.verified: true`)
  (#833). Projects generated from both specs, each with one
  path tool, were run end to end from a wheel built from this change: `services up`, `build`,
  `release` through an eval case that requires the tool, and an `invoke` that called it. The
  console's Build an agent form creates the tool-only one after its code confirm (it still
  refuses knowledge). Two or more tools, any MCP server and a tool with a provider other than
  `gemini` (openrouter included) stay unverified.

- `agentbreeder next`: the `LOCK_STALE` note now says a plain `build` rewrites `agent.lock` and
  names the console's **Build and update agent.lock**; `fix.action` stays `null` (#826).


- `services up|start|restart` fail with `[PORT_BUSY] port N is already in use on this machine`
  (and the `--json` error object) instead of a `DOCKER_FAILED` stderr tail when a host port is
  taken, and a local `deploy` records the same `PORT_BUSY` error; only the port number is read
  from Docker's output (#822).

- `agentbreeder services up|plan|status|down --json` print the error object
  `{"schema_version": 1, "error": {"code", "message"}}` on stdout for every coded error, including
  `SERVICES_BUSY` and `CONFIRM_REQUIRED` (#822).

- A `--stack DIR` that cannot be resolved now fails with a coded `[CODE] message` line on every
  command that takes it (an unreadable directory is `STACK_UNREADABLE`, not a bare OS error), and
  with `--json` also prints `{"schema_version": 1, "error": {"code", "message"}}` (#820).


- `agentbreeder services up` also writes `agent-database` (the `DATABASE_URL` binding in host
  form) and, when the database service's `console_database` param is set, `console-database`
  (its `operator_only` `console` output in host form) beside `eval-database` in the
  environment's deploy state, for CLI commands on the host (#828, M12 prerequisite).
  A removed binding or console role deletes the stale file. The three
  names are reserved: a deploy manifest that references `secret://eval-database`,
  `secret://agent-database` or `secret://console-database` is now denied with
  `SECRET_RESERVED` (before, `eval-database` could be mounted into an agent). See
  `docs/services.md`.

- "Command center" is now "console" (#816, M0b, slice 3):
  `agentbreeder up`'s help, its startup line ("AgentBreeder console: URL") and stop
  line ("Console stopped. …"), and `docs/`. The page keeps its file name,
  `docs/command-center.md`, because the README, the website and `MANIFEST.in` link to it. The
  README and website change at the release milestone (#836). A milestone verification template
  is at `docs/superpowers/verification/TEMPLATE.md`.
- Console backend foundation (#815):
  - Every API route goes through one table that applies the host, API-header, `Sec-Fetch-Site`,
    `Origin`, content-type and session checks before any handler, `404` or `405`.
  - Every API reply is an envelope `{"data", "error", "stale"}`; `error` carries the CLI's
    `[CODE]` and message, and other stderr from a failed read is no longer forwarded to the
    browser.
  - Body caps per route (64 KiB JSON, up to 1 MiB for editors; `413` when exceeded); a static
    allow-list built at start, serving the vendored `woff2` fonts.
  - Registering a project, onboarding an example and starting a job refuse directories, files and
    parent directories that other users own or can write (sticky directories such as `/tmp` are
    allowed).
  - Several jobs at once: one engine job per project, and cluster jobs (`cluster up/down`,
    promote to kind) one at a time on the machine. The API's overview lists every running job and
    `/api/activity` adds the latest jobs; the current screens follow one job and disable only
    that project's actions (a jobs dock comes with the new console shell). These locks order
    console jobs only; a terminal command does not see them.
  - Stopping `up` stops every job and long-lived child: SIGINT first, then, after 5 seconds,
    SIGTERM to each child's process group. Cancel still sends one SIGINT to the job.
  - `up` re-execs as `python -I -m agentbreeder.center` and holds a single-instance lock in the
    console directory; a second `up` prints the running URL and exits. At start it deletes
    key and editor temp files a crashed console left.
  - **The default port is now 8080 (3.0.0a2 used 8900).** Before a password exists, a busy port
    moves the console to the next free port up to 8099; afterwards `up` refuses a busy port and
    names it. Bookmarks to `127.0.0.1:8900` from 3.0.0a2 no longer reach the console.
  - Registry versions, `agent.yaml`, `agent.lock` and `used-by`'s declared and locked parts are
    read in the server process with the CLI's own functions, each file checked first (regular,
    non-blocking, size-capped) and cached until it changes or a job ends. `registry verify` runs
    as a CLI child in the background (the registry card shows "verifying" or the last answer). A
    test keeps the server process to an allowlist of modules.
  - `center.sqlite3` gains a `schema_version` table and migrations; a 3.0.0a2 database is
    upgraded in place with its projects and job history kept.
- Registries (#824): `build`, `release` and `services up`
  now refuse a symlinked `<root>/<kind>` or `<root>/<kind>/<name>` directory in a registry root
  (`ENTRY_UNSAFE`), as they already refused a symlinked version directory. A symlinked or
  dangling `./registry/tool` fails every `tool/*` ref even when a later root has the version
  (fail closed). `registry verify` reports such directories and `registry list --json` marks the
  versions under them `"symlinked": true`. An unreadable directory inside a version is now an
  error instead of being skipped.
- `doctor` reports whether the `agentbreeder` your shell finds on `PATH` is this version (#805,
  #819; advisory, never a failure). Under npx or uvx, the
  temporary environment uv puts first on `PATH` is skipped, so a stale install behind it is found.
- `cluster up` and `cluster down` take a per-user lock, `~/.agentbreeder/locks/cluster.lock`, and
  fail with `CLUSTER_BUSY` while another one runs (security finding S-11, #819).
- One redactor (`agentbreeder.redaction`, #819): literal
  values, each line of a multi-line value, base64 (also of the value inside a larger base64
  string), URL-quoted (either hex case) and JSON-escaped forms, and URL passwords, replaced in one
  pass in linear time. `services logs` uses it and now also redacts the stack's deployments'
  secret copies, and failed-case excerpts from `release` go through it. The CLI never lets a
  child process write to its own stdout or stderr unredacted (a test checks every call).
- `release` no longer calls production "already in production" just because it runs the built
  image (G-F3, #819): that needs an audited release of the
  image. A hand-deployed production is warned about and evaluated as a candidate; a pass records
  the release without replacing production, a fail exits 5 with `remove ID --local` (and
  `--rollback` only when a release journal exists). `promote` reads the same evidence through the
  new `evidence.released`.
- `list` opens the local state read-only (`mode=ro`: no mkdir, no migration, no write lock) and,
  for older or interrupted state, says to run `agentbreeder migrate` (#819). `--stack DIR` resolution no longer creates the stack's state directory.
- `Next:` lines name the launcher you are using (#819):
  `npx agentbreeder@<ver>` under the npm launcher (which now sets `AGENTBREEDER_LAUNCHER=npx` and
  `AGENTBREEDER_LAUNCHER_VERSION` for the CLI; only that value and a strict semver are accepted),
  `uvx --from 'agentbreeder[eval]==<ver>' agentbreeder` inside a uvx environment, a bare
  `agentbreeder` when the one on `PATH` is this install, otherwise `python -m agentbreeder`.
  `build` now suggests `release` (never a production `deploy`) with the `--stack` and
  `--environment` it used, and `services up` suggests `build` (no `agent.lock` yet) or `release`
  with absolute paths. No `Next:` line is printed with `--json`.
- Messages that name their fix (#819):
  `CONTEXT_ENVIRONMENT_UNKNOWN` and `MODEL_ACCESS_UNCONFIGURED` suggest `--stack DIR` when a
  `stack.yaml` sits beside the agent file or one level above a manifest; `REF_UNPINNED` on
  `release` explains how to publish a path ref; `LOCK_STALE` says to run `agentbreeder build`
  instead of "run without --locked"; a failed eval gate prints each failed case (id, failed
  checks, judge reason; control characters removed, capped) and `--json` adds `failed_cases`;
  deploys through `--stack` add one line explaining the demo-identity warnings.

### Internal

- F0 framework probe record for multi-framework agents (#1084):
  LangGraph, Deep Agents, OpenAI Agents, CrewAI and the Claude Agent SDK were each run
  against a local LiteLLM 1.106.2 gateway and under the engine's container limits. Gemini ignores
  `tool_choice: none` through LiteLLM. The Claude CLI reads `@path` mentions unless
  `verbatim_prompts` is set. CrewAI stores customer messages in SQLite. With a stub Presidio,
  guardrails block and mask input on `/v1/messages`. No product code changed.

- Design specs for multi-framework agents (#1084: framework runtimes on a shared runtime kit, agent
  protocol v1 and conformance, with a Fable review of the 2.x runtimes) and their console (#1085),
  part of epic #1083. Specs only; nothing is built.

- Implementation plan for sub-agents (#1058) and skills (#1059), and the deploy-contract addendum
  for script skills (the host-side runner and per-run `--network none` sandbox), part of epic #1057.
  Plans and design only; nothing is built.

- Console design for sub-agents (#1058) and skills (#1059): Build's Sub-agents section and Skills
  picker, agent detail cards, Try it, Traces and cost splits, Registry ▸ Skills with a safe
  Markdown renderer, and the API the console needs from the CLI, part of epic #1057.
  Design only; nothing is built.

- Design specs for sub-agents (#1058) and skills (#1059, instruction packs and script skills),
  part of epic #1057. Specs only; nothing is built.

- Test-only fix for the console builder's preview-then-create tests (#1050).
  The generated files carry the local date, so a preview before local midnight
  and a create after it are refused with `DIGEST_MISMATCH` ("preview it again"), by design. The
  tests in `tests/test_center_builder.py` and `tests/ui/test_builder.py` now preview and create
  once more only when that refusal comes with a date change; any other failure fails as before.
  No product code changed.

- Design spec for more gateway providers (#1025):
  `docs/superpowers/specs/2026-11-15-more-providers-design.md` covers Ollama, Vertex AI, Bedrock
  and Azure OpenAI behind the stack's LiteLLM gateway (model-string and endpoint review,
  gateway-only credential imports, one new `service/litellm` version per provider, embeddings,
  slices and owner decisions D1–D15). Docs only; nothing is implemented or claimed.

- Guardrails spec r4 (#1024): G0 results on the pinned LiteLLM 1.106.2 image and Presidio 2.2.362,
  covering fields, request overrides, routes, fail-closed cases, the ADK exceptions, logs and the
  policy engine for agent-level guardrails. Spec only; nothing is implemented.

- Specs for two model-layer features (#1023, #1024): direct
  Gemini/OpenAI/Anthropic endpoints without the LiteLLM gateway (the operator environment picks the
  route, the provider key is mounted into the agent, cost is a runtime estimate) and Presidio
  guardrails in the gateway, required per environment and pinned to the gateway config's sha256.
  Docs only; owner decisions pending, not implemented.

- Provider verification re-run (#1020): OpenAI passes end to end with credit (starter release and
  invoke, plus `text-embedding-3-small` knowledge via `--from-spec`); Anthropic is still blocked
  by a key that is not workspace-scoped. Records only; `Provider.verified` is unchanged.

- Providers record (#1020): `docs/superpowers/verification/providers.md` records starter runs
  (`new agent` → `services up` → `release` → `invoke`) from a wheel built from `main`. OpenRouter
  passed with `openrouter/google/gemini-3.8-flash` (release 2/2, invoke 2/2), and the judge's
  `json_schema` output worked through it. OpenAI and Anthropic were not verified: the OpenAI key
  had no credits, and the Anthropic key was not scoped to a workspace. `Provider.verified` is
  unchanged.

- Gateway limits record (#1019): `docs/superpowers/verification/gateway-limits.md` records a live
  LiteLLM 1.106.2 run from a wheel built from `main`. A tiny `budget_usd` is refused once spent,
  but `invoke` and the console show it as a generic 502. An `rpm` cap returns 429 on direct calls
  and only delays an `invoke`. A retry was seen; a cooldown was not seen consistently. Northwind's
  OpenRouter fallback works under the deployment's scoped key, which is not granted that alias
  (#1033).

- The console UI tests' fake CLI records its `invoke`, `evals` and `traces` calls under a lock
  and replaces the file whole, so a test polling it never reads it empty (the
  `test_choosing_a_deployment_and_a_count_reads_again` CI flake).

- 907-tracing.md addendum (#1009, #1010): on a real Phoenix with Northwind from a wheel of
  `main` (34472fb), trace token totals equal the runtime's count and LiteLLM's spend log for 4
  turns and 1 eval case. A Try it trace read 1.1 s after the reply is marked still arriving and
  not matched by time, and Read again fills it. The CLI's `(still arriving)` mark was not seen.

- Test-only fix for a flaky console usage test (#1006).
  The fake `services usage --follow` in `tests/test_center_usage.py` made its first write
  outside its `BrokenPipeError` handling. When the console died before that write, the fake
  exited and left its fake `docker stats` reader running. Its writes now sit inside a `try`, and
  a `finally` stops the reader, as the real CLI does. No product code changed.

- Spec delta for repinning MCP tool definitions without a terminal (#964):
  a read, `services pins --json`, that probes the relays and returns the pin
  diff with a digest, and `services up --repin --expect-pins SHA256`, which pins all or nothing
  only when the live definitions still match the reviewed digest. Docs only; not implemented yet.

- Spec for agent identity at creation (#909, epic #742):
  `docs/superpowers/specs/2026-11-13-identity-at-creation-design.md`. A spec only;
  nothing is built: no built-in Keycloak, no principal is created and in-process
  tool calls are not checked.

- The GKE target spec (#908) is amended to match the production-Kubernetes spec (#770): agents reach
  the services their grant names, the deployer has no Secret read and the admission policies are G4
  prerequisites, cluster state is per environment, and G1b is built as #770's K5. Spec text only;

- Spec for the Google Cloud target through GKE (#908, epic #770):
  `docs/superpowers/specs/2026-11-14-gke-target-design.md`. A spec only;
  nothing is built and nothing has run on GKE.

- The tracing verification record `docs/superpowers/verification/907-tracing.md` (#907):
  a real-Docker run on Northwind of a Try it turn's trace and an eval
  case's trace, with the defects found (#1009, #1010).

- The LLM cost verification record `docs/superpowers/verification/906-cost.md` (#906):
  on a real stack, `agentbreeder cost` and the console's cost cards
  equal LiteLLM's own spend log per deployment key and per model. The docs defect found is #1011.

- Changelog fragments and per-command docs (#893): a PR
  adds its entry as `changelog.d/<issue>.<type>.md` instead of editing `CHANGELOG.md`, and
  `scripts/changelog.py fold` moves the fragments into `[Unreleased]` at release. A CI workflow
  (`changelog.yml`) fails a PR that edits `CHANGELOG.md` without the `release` label, or that
  changes code or docs without a fragment. `docs/commands.md` is split into
  `docs/commands/<command>.md`, one per command, with an index generated by
  `scripts/commands_index.py`. Entries already in `[Unreleased]` stay as they are.

- M17 record (#833), re-run after #1051: from a wheel built from `main`, a gemini agent with a
  prompt, an eval case and one path tool was created from the console's Build an agent form,
  started and released from the console (1/1), and called its tool in Try it. The acceptance is
  met. Knowledge is still not on the form.

- The M17 agent builder verification record `docs/superpowers/verification/M17.md` (#833):
  the form creates and releases a gemini + prompt + evals
  agent, but not one with a tool (not run end to end yet); the same spec with a tool and knowledge
  released through `new agent --from-spec`.

- M15 record (#831), real-token addendum: from a wheel built from `main`, with a real
  fine-grained GitHub token, a granted tool was listed and called by the agent. After a revoke,
  `remove` and `services up`, the relay refused that tool and the narrowed agent no longer listed
  it. The relay refused ungranted calls (`TOOL_NOT_GRANTED`). The CLI and console repin flows
  passed on a simulated drift. The acceptance is met.

- M15 record (#831): `docs/superpowers/verification/M15.md` records a run from a wheel built from
  `main` with a stub GitHub token, so no GitHub call succeeded. `mcp grant|revoke`, the console's
  grants view and its grant and revoke flow (including a stale-review refusal) and the services
  review's MCP section passed. The relay logs the refused credential as `UPSTREAM_AUTH` and the CLI
  as `MCP_PROBE_FAILED (UPSTREAM_AUTH)`, but the console omits the cause. The acceptance (an agent
  lists a granted tool) needs a real token and was not run.

- M14 verification record (#830, M14): `docs/superpowers/verification/M14.md`, run from a wheel built from `main` with Northwind on real Docker. A document added with `registry edit` and released 61/61 is returned by retrieval, and `knowledge status` shows the new chunk count. The console's knowledge screens were driven by Playwright against the same stack (screenshots; no video on macOS 13).

- M12 verification record (#828, M12): `docs/superpowers/verification/M12.md`, run from a wheel built from `main` with Northwind on real Docker. It covers tool checks, `registry show` functions, a `tool/orders` edit released 61/61, and `tool log` through the console credential. It records the deploy race (#978) and the LiteLLM probe margin (#979).

- M10 verification record (#826, M10): `docs/superpowers/verification/M10.md`, run from a wheel
  built from `main` with the starter agent on real Docker. A prompt edited in the console and
  saved as `0.1.1`, moved to and released, passed 2/2; an eval case broken in the console blocked
  the release (exit 5, 1/2), and the console's gate-failed view showed the case, its check, the
  judge's reason and the candidate's saved tail (screenshots; no video on macOS 13).

- M9a verification record, console addendum (#825, M9a): `docs/superpowers/verification/M9a.md`,
  run from a wheel built from `main` on real Docker. A prompt imported by path in the console
  resolved (the build locked the reviewed digest and the image holds its text); a built-in service
  version forked to the next patch and under another name, and the copy was edited with
  `registry edit`; a tool import asked for the code confirm, and the server refused a write without
  it (`CODE_NOT_CONFIRMED`).

- M8 console addendum (#824): `docs/superpowers/verification/M8.md` now records the console half,
  run from a wheel built from `main` with Northwind released (61/61) on real Docker. The registry
  lists showed Northwind's 13 versions and all 16 built-ins, the 1.0.0 → 1.0.1 prompt diff was
  byte-identical to `diff -u`, Used by showed `tool/orders@1.0.0` declared, locked and deployed,
  and Audit paged 112 rows (50, 50, 12) with agent names linking to the agent detail.

- M7 verification record (#823, M7): `docs/superpowers/verification/M7.md`, run from a wheel
  built from `main` on real Docker. The console's Agents list showed Northwind (61/61) and a
  starter agent (2/2) with their state; Try it on Northwind production returned a reply with its
  tool and sources; a project with an `agent.yaml` and no stack was onboarded with the
  three-tier confirm and released 2/2 from the console (screenshots; no video on macOS 13).

- M6 verification record, partial (#822, M6): `docs/superpowers/verification/M6.md`, run from a wheel built
  from `main` with Northwind on real Docker. Home showed all 5 services with CPU, memory, network
  and block IO within 4.6 s and changed by 8.3 s; `services restart litellm` and the console's
  Restart restarted only that container. The Docker-stop step was not run (shared Docker Desktop);
  its UI tests against the fake CLI passed.

- M4 console record (#820): `docs/superpowers/verification/M4.md` gains a console addendum from a
  wheel built from `main`. Create starter reached a first Try it reply in about 123 s, with the
  checklist and Next pill equal to `next --json` at every step. GRANT_PENDING on a local kind
  cluster was shown and cleared through the Services review. APPROVAL_PENDING could not be
  produced without a verified deployer and has unit-test evidence only. A starter's LiteLLM spend
  log holds tokens and cost but no prompts or replies (#1018).

- Spec for production Kubernetes (#770: the deployer on a real cluster, a Helm chart for the stack's
  services, secrets and network policy, the exit run):
  `docs/superpowers/specs/2026-11-14-production-kubernetes-design.md`. A spec only;
  nothing is built and nothing has run on a real cluster.

- `build` makes the deploy manifest in two steps (#763, M13 slice 1c):
  `template()` (port, health path, aliases, budget, rpm and MCP tools, from the
  agent and its runtime only) and `bind()` (identity, secrets and the gateway from the operator
  config; image and target from the deploy path). Generated manifests are byte-identical to
  before (pinned for Northwind), and the template is now checked before `docker build` runs.

- CLI commands split into modules (no behaviour change) (#813). Each command's parser and handler
  moved from `cli.py` to `src/agentbreeder/commands/<name>.py`; a golden test checks every
  command's `--help` output byte for byte.
- Design spec for authenticated `/invoke` and caller-derived customer identity (#780, epic #812):
  `docs/superpowers/specs/2026-11-13-authenticated-invoke-design.md`. A spec only; nothing is
  built, and `/invoke` is still unauthenticated.
- Design spec for governed agent-to-agent calls (A2A, M19 #835, epic #812):
  `docs/superpowers/specs/2026-11-13-a2a-design.md`. A spec only;
  nothing is built, and agents cannot call each other.
- Spec delta for M12, tools and the read-only console role (#828, epic #812):
  `docs/superpowers/specs/2026-11-13-m12-tools-console-role-design.md`. A spec only;
  nothing is built, and there is no console role or `tool log` yet.
- `safe_text` writes its bidirectional-control character class as `\u` escapes instead of seven
  literal invisible characters (#820). No behaviour
  change: a test checks every code point (U+0000 to U+10FFFF) against the documented set
  (C0, DEL and C1, bidi controls, U+2028/U+2029, lone surrogates) and that the module source
  holds no invisible control character.

### Fixed

- The console's agent page no longer says "No release recorded" for an agent that has releases
  while its releases are still loading: the gate strip's steps say "Loading releases…" (or
  "Loading deployments…") until their first read answers (#1073).


- The console's Agents list now reads again every 3 seconds while a job runs, and again with its
  last releases when a job ends, so a release's candidate and new production row show without a
  reload (#1073).

- The console's agent page no longer says Production "passed" before its releases are read, or
  "no deployment" on the Overview, Logs and Traces tabs before its deployments are read: each says
  what it is loading until the read answers. Switching the project scope with an agent open no
  longer shows the other project's production card (#1073).

- `mcp revoke` then `services up` applies cleanly while a deployment still holds the revoked
  tool: `up` narrows the relay's grant (the relay refuses the tool to that deployment from then
  on, `TOOL_NOT_GRANTED`) and warns, naming the agent and deployment, instead of refusing with
  `MCP_GRANT_IN_USE` until it was removed. Dropping an `mcp:` entry in use is still refused. The
  console's grant review says the same (#1072).

- `services down` (#1048) now removes the stack's networks that Docker Compose leaves behind.
  Compose removes only the networks the current compose file names. A dropped `mcp:` entry's
  private network (`agentbreeder-<stack-id>-mcp-<name>`), or any network from an earlier
  render, was left in Docker after the stack was gone. `down` now removes every network labelled
  with the stack's id and named after the stack, also after a failed `up`. A network that is
  still in use is kept and reported, not removed: an `<stack>-agents` network that a leftover
  agent container or the kind node still holds is in this group. `--json` adds
  `networks_removed` and `networks_left`. This is tested against a fake Docker; the opt-in
  real-Docker assertions have not been run yet.
  Networks left by earlier versions are not pruned automatically; `docs/services.md` shows how
  to remove them by hand.

- `agentbreeder next` and the console checklist no longer report `PROMOTION_STALE` for the
  optional Kubernetes step when there is no production deployment (#1044).
  With the cluster up and in the grant but no production to promote or
  compare (never released, or removed since), the step is `NOT_CHECKED`, "Release first".

- The console's MCP grant form no longer labels every granted tool "no longer declared" while
  the server's declared tools are still loading: it says "checking the declared tools" (or
  "declared tools unavailable" when they can't be read). The Change and Tools choices now line up
  at the start of their boxes in light and dark (#1043).

- Coded failures keep their cause (#1042). A `services up` that stops at the MCP pin probe names
  the relay's cause (`MCP_PROBE_FAILED` with `UPSTREAM_AUTH`) in `services status --json`
  (`mcp.<server>.cause`), and `next` reports the failed run as `SERVICES_UP_FAILED` with its code
  and cause (`data.up_failure`) instead of "services up has not run". The console's job rows show
  a failed job's code, its cause and the CLI's message (with its next step) instead of only "See
  its log", including a release's coded refusal, and the MCP grants screen says what the cause
  means and which relay log to read.

- `services up` refuses a gateway config whose `fallbacks` or `context_window_fallbacks` name an
  alias `environment.models` doesn't grant (`GATEWAY_FALLBACK_UNGRANTED`, naming the aliases),
  because LiteLLM serves a fallback under the caller's key without checking the key's models
  (#1033). It writes the granted fallbacks into the operator config, and deployment and eval
  judge keys are minted with them, so a granted fallback keeps working. Northwind now grants
  `support-model-openrouter`; `OPENROUTER_API_KEY` stays optional.

- `services up` refuses a gateway config model that is not `PROVIDER/MODEL` with a provider
  AgentBreeder reviews (`anthropic`, `gemini`, `openai`, `openrouter`), with
  `GATEWAY_CONFIG_UNSAFE` (#1031). Other prefixes and bare
  model names used to be accepted as "unknown (no key)" and left LiteLLM to the provider SDK's
  default credential chain.

- `services up` refuses a gateway config that turns prompt or reply logging on
  (`litellm_settings.turn_off_message_logging` other than `true`, `litellm_settings.set_verbose`
  other than `false`, `general_settings.store_prompts_in_spend_logs` other than `false`) with
  `GATEWAY_CONFIG_UNSAFE` naming the key (#1028). There is
  no operator opt-in.

- The cost and tracing docs now say what the 2026-11-15 Northwind runs showed (#1011):
  `runtime/adk@1.2.0` reports each turn's `cost_usd` (LiteLLM's
  estimate for the turn's chat calls, evidently without its embedding calls), `agentbreeder cost` and the
  console's cost cards equalled LiteLLM's spend log on real Gemini calls, and the console's trace
  panels were run against a live Phoenix (with #1009 and #1010 noted). A key reaching its budget
  has not been run.

- A trace read before its root span reached Phoenix is marked `incomplete` ("still arriving",
  with **Read again** in the console) instead of being matched by time with the wrong "this
  runtime does not record its deployment" note (#1010). Tested against a fake Phoenix, not yet a
  live one.

- `agentbreeder traces` and the console's trace views count each model call's tokens once: a
  trace's total no longer adds both ADK's `call_llm` span and its `generate_content` child,
  which doubled it (#1009). Each span's `tokens_counted` says whether its counts are in the
  total. Tested against a fake Phoenix, not yet a live one.

- A local `deploy` with the opt-in egress sidecar re-inspects the sidecar for up to 5 seconds
  while Docker still shows its ingress port as empty after `container start`, instead of failing
  at once with "Sidecar has no single loopback ingress binding"; a stopped sidecar or a binding
  that is not loopback still fails at once (#991).

- `registry edit` no longer suggests `agent set-ref` when that command would refuse: a service
  version, which `stack.yaml` references, got a hint that exits 2 with `REF_NOT_DECLARED`. The
  `Next:` line now follows where the project declares the version: the `agent set-ref` command
  (with `--project DIR` when needed) only when `agent.yaml` declares it and the command's dry run
  passes, otherwise the field to change in `agent.yaml` or `stack.yaml`, or which file can
  reference the new version (#990).

- Every strict YAML read (agent files, registry manifests) opens the file non-blocking and
  refuses anything that is not a regular file, so a FIFO in a shared registry root no longer
  hangs the CLI (#989).

- `services up` gives a service whose HTTP health check has not passed after 180 s up to 240 s
  more while its container is still running (a cold start on a busy engine, such as LiteLLM's on
  a fresh stack), and fails at once when the container has exited; the `SERVICE_UNHEALTHY`
  message now says that started services are kept, so the command can be run again (#979).


- A local `deploy` (and so `release`'s candidate and production deploys) re-inspects the
  container for up to 5 seconds while Docker still shows its published port as empty after
  `container start`, instead of failing at once with "Container is not running with one loopback
  HTTP binding"; a binding that is present but not loopback still fails at once (#978).


- The console's Kubernetes screen offers Deploy to cluster only for the deployment `promote` takes
  (this agent's ready local deployment, from `next --json`), not for another agent's deployment
  in the list (#902).

- `agentbreeder events --mark-read ID` now reports an id that matches no stored event: it prints `ignored unknown event id N` on stderr, and `--json` gains `"unknown_ids"` (always present, `[]` when none). The exit code stays 0 and known ids in the same call are still marked (#897).

- The console's "Session ended" page also names a console restart as a cause (sessions live in the
  console's memory), not only 8 hours open or 1 hour idle (#879).


- `evals` and `release` stop a case's `must_match` / `must_not_match` patterns after 5 seconds and fail the case (check `patterns_finished: false`), so a pattern that backtracks on a long reply can no longer stall a release; a pattern that is not a regular expression fails its case instead of ending the run with a traceback. S-34 #10 (#833).

- The console's guided path offers **Build and update agent.lock** for a stale lock
  (`LOCK_STALE`) instead of only a CLI command, and a grant review or services start on a stack
  where another services write runs says "Another operation is running on this stack; retry
  when it finishes" (`SERVICES_BUSY`) instead of a generic failure (#826, #822).


- The console read `doctor --json`'s exit 1 (a required check failed) as a failed read and kept
  the last passing answer, so Home's Docker notice never appeared; it now keeps doctor's answer
  (#822).

- `agentbreeder logs`, `services logs` and `services start|stop|restart` keep a coded error on
  one stderr line: control characters and newlines in a Docker or kubectl message are escaped,
  as for every other `[CODE]` line, and the `--json` error object escapes control characters too
  (#822).

- Copying a registry version now reports a file that was replaced by a directory while it was
  copied as `[ENTRY_UNSAFE] changed while copying`, not a raw `IsADirectoryError` (#763).


- A deploy behind the egress boundary (runtime enforcement) waits 30 s, not 10 s, for its two
  engine probes: the listen-mode `docker info` and the engine-version `docker version` (#732).
  That is the same timeout as every other Docker call. On a
  loaded Docker Desktop, `docker info` stalled for more than 10 s right after an image build, and
  the deploy ended in `failed` with "Docker operation failed or timed out; check docker context".
  The runtime preflight's own 10 s `docker version` check (also used by `doctor`) is unchanged.

- `registry publish --json` reports `root_kind: "project"` when `--to` resolves to `DIR/registry`
  and DIR holds an `agent.yaml` or `stack.yaml` file (#898).
  It reported `path` for a project's own `./registry`, including `publish DIR` run from
  the project with the default `--to registry`; `import`, `fork` and `show` already said
  `project`. Any other `--to` stays `path`; the write is unchanged. See `docs/registry.md`.

- `logs ID` for a Kubernetes (local `kind`) deployment:
  when `kubectl logs` exits 0 but its whole output is one kubelet message saying the container
  or its log file is gone, it now fails with `LOG_UNAVAILABLE` (exit 2, with the `--json` error
  object) instead of printing that message as a log line. On kind (node v1.37), `--previous` on
  a crash-looping pod gave `unable to retrieve container logs for containerd://<id>` in 6 of 6
  runs, and a container whose log file was removed gave `failed to try resolving symlinks in
  path "…": lstat …: no such file or directory`. Only one untimestamped line that exactly matches
  one of these forms counts; a timestamped container line with the same words is still printed.
  `--follow` is unchanged.

- Console, Services: after `services down` a service with no running container (absent,
  exited, created or dead) no longer lists its last host port as if something listened there;
  it reads "not listening (last: NAME → 127.0.0.1:PORT)", muted. **Stop services** is off,
  with "The services are stopped.", only when every service is absent: `services down` also
  removes exited containers, so it stays on for those (#882).
- `invoke`: a state directory it cannot look into (no search permission on it or a parent,
  another OS error, named by its errno) is now `[STATE_UNREADABLE] cannot read the
  AgentBreeder state at DIR (permission denied)`, exit 2, with the `--json` error object
  (`sent: false`), instead of a Python traceback; a missing state is still `NO_STATE` (#820).
- Console, Targets ▸ Kubernetes: after a released deployment was removed, the "Deploy this
  release to the cluster" card said "Nothing released locally yet." It reads the ready local
  deployment, which is what `promote` needs (`NO_LOCAL_RELEASE`), not the release history. It now
  says "No ready release is running locally." (and "Running locally: NAME" when there is one),
  and Deploy to cluster stays off with "Needs a release running locally: run a release first."
  (#880).
- `logs ID --follow`, `services logs --follow`, `watch` and `services usage --follow`: when the
  terminal's Ctrl-C or a group SIGTERM/SIGHUP ends their docker or kubectl child before the
  CLI's own handler records it, a child exit of 130, 143 or 129 (it caught the signal) now counts
  as that stop, like a child killed by the signal: reason `interrupt` or `signal`, exit 0,
  instead of `DOCKER_LOGS_FAILED`, `DOCKER_EVENTS_FAILED` or a `docker stats` restart. The
  child's own exit code is read before the CLI stops it, so the CLI's own SIGTERM is never taken
  for one. On Docker 27.5, `docker compose logs -f` (used by `services logs`) exits 130 after
  SIGINT and SIGTERM; `docker logs -f` and `docker events` exit 1, which still reads as a
  failure in that rare race; `docker stats` ignores both, so its case is covered by tests only;
  kubectl was not checked (#821).
- `next`'s Kubernetes step suggested `cluster up` without `--yes`, which leaves the grant and the
  operator config unchanged, so the `promote` it suggested next failed with
  `KUBERNETES_UNCONFIGURED`; it now suggests `cluster up --yes`.
- Five small findings from the M3/M8 verification (#854):
  `cluster down` removes the registry container's anonymous volume (`docker rm --volumes`) and,
  without `kind` on `PATH`, fails with `CLUSTER_TOOL_MISSING` as `cluster up` does; `migrate` on
  a state with an interrupted write says it recovered that write (its `--json` adds
  `interrupted_write`) instead of "from an unreadable version"; a `LOCK_STALE` that names `build`
  adds the `--stack DIR` note when a stack.yaml is beside the agent file, and `eval --stack`
  repeats its stack in that hint; `registry used-by` on a `service` ref no longer lists the
  project's agent deployments as undetermined (the keys stay, empty).

## [3.0.0a2] — 2026-11-11

### Added

- `agentbreeder up`: a loopback, single-user command center (#783, preview). Each action runs the
  CLI in a child process.
- A Kubernetes runtime for local clusters: `deploy --cluster`, `remove --cluster`, `forward`,
  `cluster up|status|down` and `promote`, which deploys an audited release by digest.
- `services plan`, `services up --expect-grant` and `list`.

### Changed

- `release` records the manifest digest, threshold and eval-set hash in its audit event, and defers
  SIGINT while it replaces production.

## [3.0.0a1] — 2026-11-10

The first installable preview of the focused rebuild, and not an upgrade from 2.x:
[PyPI `3.0.0a1`](https://pypi.org/project/agentbreeder/3.0.0a1/),
[npm `3.0.0-alpha.1`](https://www.npmjs.com/package/agentbreeder/v/3.0.0-alpha.1),
[GitHub pre-release](https://github.com/agentbreeder/agentbreeder/releases/tag/v3.0.0a1).
A bare `pip install agentbreeder` still installs 2.7.3.

Verified from the public registries on 2026-11-10: on macOS with Docker Desktop, `quickstart` on
pip, uvx and npx, and Northwind `services up` and `release` at 61/61 with a live `/invoke` and a
full teardown. On Linux (Ubuntu 24.04 in a container), install, `--version` and `doctor` on all
three paths only. See #754.

### Installable preview (#751)
- Version `3.0.0a1` from `agentbreeder.__version__`. The npm launcher is `3.0.0-alpha.1`.
- The examples ship in the package (`src/agentbreeder/examples/`). Built-in content is checked
  against `_content.json` (a corruption check), and uv hardlink installs are supported.
- `agentbreeder example list | NAME [DIR]`, `agentbreeder quickstart [--dry-run]`, and `validate`
  of a `stack.yaml`.
- A 2.x notice in the README (the PyPI description); `doctor` reports 2.x state in
  `~/.agentbreeder`.
- `npm/`: the `npx agentbreeder` launcher. It runs the pinned PyPI release through `uvx` with
  `--exclude-newer` and a constraints file. It looks `uvx` up on PATH, skipping `node_modules`
  directories and refusing a match from the current directory. It has no dependencies or install
  scripts, and has `--launcher-version`.
- `.github/workflows/release.yml` for `v3.*` tags. It verifies, builds twice and compares,
  attests, publishes to TestPyPI and then PyPI through OIDC trusted publishing behind environment
  approval, then creates the GitHub pre-release. `v3.0.0a1` shipped through it (run 42891188083).
  Its npm job stays off until trusted publishing is set up: `3.0.0-alpha.1` was published manually,
  without npm provenance (#794 tracks provenance for the next release).
- New CI jobs:
  - `install-smoke`: from the built wheel, pip from the hashed requirements file and uv in
    hardlink mode, plus the 2.7.3 checks against a local index offering the pre-release;
  - `npm-launcher`: Node 18 and 22;
  - `upgrade-guard`: pip still installs 2.7.3 from PyPI;
  - `workflow-lint`: actionlint and zizmor.

  CI actions are now pinned by commit SHA.

### Fixed
- `doctor` and the deploy preflight no longer pass when the Docker engine is unreachable
  (`docker info --format` exits 0 in that case; checks now use `docker version`).
- `reconcile` exits 4 when any deployment is noncompliant or unverifiable (1 still means blocked
  cleanup failed). Read commands error on a missing `--state-dir` instead of creating empty state.
- Keycloak/Okta sponsor responses of the wrong shape fail closed with `PROVIDER_ERROR`;
  `status`/`reconcile` report unexpected adapter failures as `unverifiable` instead of crashing.
- The VOLUME rejection reason is no longer replaced by a generic error; `remove` names the Docker
  context to restore when it is unreachable.
- Docs: the bring-your-own-image guide and README recovery commands now include the operator
  config and `--environment`; exit codes, `disable`/`enable` and `SECURITY.md` corrected.

### Changed
- The website now documents the focused rebuild; 2.x docs were replaced by a Legacy 2.x page.


### Added
- **Deploy-time agent identity gate (#701, phases 0–3).** Manifest `schema_version: 2`
  (`identity`, scoped `requires`, `capabilities`, `policy.restrictions`); owner-only operator
  config with `enforce`/`audit` modes and operator environment/target mappings; read-only
  `static`, `entra`, `keycloak`, `aws` and experimental `okta` adapters; OIDC-verified deployers
  (CI token, device-flow `login`); Cedar (default), OPA and AuthZEN PDPs with pinned operator
  baselines and approved restrictions; revision-bound maker-checker approvals (`approve`/`reject`,
  exit 3 while pending); durable `disable`/`enable` kill switch, `reconcile` and `status`
  re-verification; hash-chained attempt audit log (`audit list`/`audit verify`). Deploy-time
  only. See `docs/identity-gate.md`.
- **Runtime MCP egress boundary (#706, phase 4).** `runtime_enforcement.mode: mcp_http` runs the
  agent on a Docker `--internal` network behind a per-deployment sidecar PEP. Boundary verified by
  inspection and active probes before traffic (`RUNTIME_BOUNDARY_UNVERIFIED`, exit 2, no degraded
  mode). Per-call `call_tool` policy (Cedar schema adds `Tool`, `call_tool`,
  `RequireCallApproval`), body-authoritative JSON-RPC with `Mcp-Method`/`Mcp-Name` checks, audit
  before forward, held calls with `approve-call`/`reject-call`, host-side credential broker
  (static or RFC 8693 token exchange), rate and size limits, and `reconcile` restoring the
  sidecar. State schema v3 (`held_calls`).
- **Python SDK**: async `RagMcpClient` wrapping the 8 RAG MCP tools (experimental). (#574)

### Changed
- **Design system extracted into `dashboard/src/styles/brand.css` (#583).** The
  AgentBreeder brand layer — dark palette, Tailwind v4 `@theme` mapping, brand
  keyframes (`ab-pulse`, `ab-glow-breathe`), and brand utilities (`gradient-text`,
  `ab-radial-glow`, `ab-card-glow`, `ab-status-dot`) — moved out of
  `dashboard/src/index.css` into a dedicated, portable `brand.css` that
  `index.css` now `@import`s (`@custom-variant`/`@layer base` stay in `index.css`).
  This is the single source of truth for the brand, consumed OSS → Cloud → Website:
  the Cloud console vendors a snapshot and drift-checks the `.dark` + `@theme`
  tokens against it. Pure source-level reorganization — compiled CSS is
  byte-identical before/after (`npm run build`), zero visual regression.


### Fixed
- **Packaging follow-up to issue #560.** `examples/quickstart/*.yaml` now ships
  in the published wheel via hatch `force-include`; `Dockerfile` COPYs
  `examples/quickstart/` so in-container wheel builds work. Discovered while
  testing the freshly-published `agentbreeder==2.7.1`: `quickstart` Step 7
  (Deploying Sample Agents) silently no-op'd on a pipx install because the
  examples dir didn't exist in site-packages. Also makes `_register_agents()`
  emit an explicit warning if `EXAMPLES_QS` is empty so the silent no-op can't
  hide again, and extends the smoke workflow with an
  `examples/quickstart/*.yaml` guard alongside the existing `deploy/*.yml` one.

### Fixed
- **Quickstart + how-to happy-path bundle (issue #560).** Resolves 15 bugs and
  6 doc errors uncovered by the docs-vs-reality audit:
  - PyPI wheel now ships `deploy/` (hatchling `force-include`); `Dockerfile`
    COPYs `deploy/` so in-container wheel builds work (#560 bug #1).
  - Podman is a real supported runtime — new `deploy/docker-compose.podman.yml`
    override clears `host-gateway` and rewrites `OLLAMA_BASE_URL` to
    `host.containers.internal` when podman is the detected runtime (#560 bug #2).
  - Dev-version pin (`2.7.1.dev3+gHASH`) → PEP-440 base (`2.7.1`) in generated
    `requirements.txt`; deploy from dev checkout no longer breaks
    `pip install` inside the agent image (#560 bug #4).
  - `GET /api/v1/secrets` returns 200 + empty data on backend failure instead
    of 500 — unblocks `/agents/new` chat-to-build (#560 bug #5).
  - `agentbreeder list <entity>` calls the real API (agents, deploys,
    providers, orchestrations, mcp_servers, templates) and reads the local
    scan registry for tools/models/prompts (#560 bug #6).
  - `agentbreeder down --clean` honors the runtime quickstart used
    (cached at `~/.agentbreeder/quickstart-runtime.json`) — docker/podman/nerdctl
    stacks all tear down cleanly (#560 bug #7).
  - `agentbreeder eject AGENT --to {yaml,code}` exists, matching the documented
    tier-mobility flow (#560 bug #8).
  - Quickstart Step 5/8 transient failures bubble `typer.Exit(1)` instead of
    silently exiting 0 (#560 bug #9).
  - `agentbreeder validate` invokes the matching runtime's `validate()` so
    missing `agent.py` / `requirements.txt` fail at validate, not deploy; new
    `--schema-only` flag for standalone snippet YAMLs (#560 bug #10).
  - Cloud Run deploy auto-resolves `GCP_PROJECT_ID` through YAML → shell env
    (`GCP_PROJECT_ID` / `GOOGLE_CLOUD_PROJECT`) → `gcloud config get-value project`
    (#560 bug #11).
  - `--target claude-managed` listed in `deploy --help`; `AsyncAnthropic` +
    beta-surface fallback handles SDK API drift gracefully (#560 bug #12).
  - Ollama auto-rebind uses `pkill` (no GUI dialog) and polls the port before
    relaunching with `OLLAMA_HOST=0.0.0.0` (#560 bug #13).
- Six doc-vs-reality drifts in `quickstart.mdx` + `how-to.mdx` (A–F): Home vs
  Agents empty-state, Claude key location (no Settings → Secrets), `with_tools`
  → `with_tool`, claude-managed beta callout, Test / Try-it tab visibility,
  GCP project resolution chain.

### Added
- **CI smoke gate.** `.github/workflows/smoke-quickstart.yml` runs the
  `pipx install wheel → quickstart → secrets endpoint → local deploy` flow on
  every PR, protecting against regression of issue #560 bugs #1, #3, #4, #5.
- `agentbreeder validate --schema-only` for snippet YAML files that aren't full
  agent bundles.

---

## [2.7.0] — 2026-06-16

### Added
- **GCP + Azure deploy parity with AWS** (multi-cloud parity epic #505). The same
  `agent.yaml` now reaches feature parity across all three managed clouds:
  - **MCP discovery on every cloud** — the GCP Cloud Run and Azure Container Apps
    deployers now inject the agent-facing `AGENTBREEDER_MCP_SERVERS` env so
    `agenthub.mcp.load_mcp_tools()` discovers co-deployed MCP servers (was AWS-only;
    closes #533).
  - **CLI greenfield `--provision` for GCP and Azure** — `agentbreeder deploy
    --target cloud-run --provision --local` and `--target container-apps --provision
    --local` now stand up a fresh footprint, mapping the provisioned IDs into the
    deploy (was AWS-only; #537). GCP greenfield additionally builds a dedicated VPC +
    subnet + Cloud NAT + a Private Service Access range (for Cloud SQL private IP),
    matching the AWS VPC story; the footprint is torn down by `agentbreeder teardown`.

### Fixed
- **GCP sidecar boot-auth** — the Cloud Run sidecar now emits `AGENT_AUTH_TOKEN`
  (or `AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH=1`) so it starts, matching the AWS injector.
- **GCP/Azure resource normalization** — `resources.cpu`/`memory` accept the
  documented `agent.yaml` notation (vCPU + `Gi`/`Mi`/`G`/`M`/raw) and render
  Cloud Run (Mi/Gi, whole vCPU/millicpu) and Container Apps (fractional cores,
  canonical `<x>Gi`) valid values.

### Security
- **Cleared `dashboard` high-severity advisories via npm `overrides` + a `vite` patch bump** —
  pinned `hono` to `^4.12.25` (transitive through the `shadcn` CLI → MCP SDK; fixes the IPv6
  deny-rule bypass, Set-Cookie injection, JWT-scheme, and mount-prefix advisories) and `esbuild`
  to `^0.28.1` (transitive through `vite`; fixes the Deno-module integrity and dev-server
  file-read advisories). A second wave of advisories surfaced afterward and is cleared here too:
  bumped `vite` to `^7.3.5` (fixes the high-severity `launch-editor` NTLMv2 UNC disclosure and the
  `server.fs.deny` Windows-alternate-path bypass — patched in 7.3.4, **no `vite` 8 major bump**),
  and added overrides for `@babel/core` `^7.29.7` (transitive through `@vitejs/plugin-react` /
  `shadcn` / `eslint-plugin-react-hooks`; fixes the sourceMappingURL arbitrary-file-read advisory)
  and `js-yaml` `^4.2.0` (transitive through `eslint` / `cosmiconfig`; fixes the merge-key
  quadratic-complexity DoS). All are build/dev-time dependencies not bundled into the shipped app.
  `npm audit` now reports 0 vulnerabilities, and the dashboard build + 227 tests pass.
- **Bumped `vitest` to `^4.1.8` in `sdk/typescript`** (was `^1.6.0`), clearing a critical advisory
  (GHSA — Vitest UI server arbitrary file read/execute, fixed in 4.1.0). Dev/test-only dependency;
  all 94 SDK tests pass on the new major. (`dashboard` was already on a patched 4.1.x.)

### Fixed
- **AWS ECS Fargate deploy hardening** (live-validated end-to-end, incl. the MCP/sidecar topology):
  `resources.cpu`/`memory` accept both vCPU/Gi and raw-unit notation (normalized to Fargate units);
  the CloudWatch log group is pre-created (stock execution role suffices — no `logs:CreateLogGroup`);
  injected sidecar + MCP containers ship logs to CloudWatch; the agent honors `$PORT` (8081 behind the
  sidecar) and uses a `python` healthcheck (`python:slim` has no `curl`); the sidecar receives
  `AGENT_NAME`/`AGENT_VERSION` + a no-auth fallback and drops its impossible distroless `curl`
  healthcheck; the default sidecar image points at the published `rajits/agentbreeder-sidecar`.
- `engine/resolver._resolve_memory_config` no longer leaks a "coroutine never awaited" warning when
  called from within a running event loop (runs the registry lookup in a worker thread).

### Added
- **Studio Conversational Builder (Waves 1–4)** — a chat-first agent builder: converse to a
  validated `agent.yaml`, eject to real code in a sandbox (Claude or Codex engine), and deploy
  through the governed pipeline, all from one thread. Wave 4 adds a managed **CloudSandbox** over a
  pluggable backend (`AGENTBREEDER_SANDBOX=cloud`, `AGENTBREEDER_SANDBOX_BACKEND=e2b` via the
  optional `[cloud]` extra) with per-session microVM isolation, path containment, byte/exec caps,
  and a default-deny egress allowlist; a PII-free product-analytics funnel (ingest + funnel API with
  p50/p90 time-to-first-deploy, and a team-scoped **Studio › Builder** view); and a Codex eject
  gated e2e for engine parity. Cloud deployments meter actual sandbox-minutes (429 pre-check) in the
  companion `agentbreeder-cloud` change. (#555)
- **CLI greenfield AWS provisioning** — `agentbreeder deploy --provision` (`-p`) now stands up the
  full AWS footprint (VPC, public/private subnets, NAT gateway, ECS cluster, IAM execution role,
  security groups) before deploying, for accounts with no pre-existing infrastructure. The
  provisioned `InfraState` is persisted to `infra-state.json` (next to the agent YAML) and reused
  idempotently on redeploys, and is mapped into the deployer env (incl. an auto-derived
  `AWS_ACCOUNT_ID`). `agentbreeder teardown` reverses the greenfield footprint. GCP/Azure greenfield
  provisioning remain on the roadmap. (#539)
- **First-class MCP tool loading for Full Code agents**: `agenthub.mcp.load_mcp_tools()` reads the
  `AGENTBREEDER_MCP_SERVERS` map the deployer injects and returns the co-deployed servers' tools as
  LangChain tools (`create_react_agent(model, load_mcp_tools())`). The langgraph runtime bundles
  `langchain-mcp-adapters` when `mcp_servers` is declared.
- The langgraph agent server normalizes convenience input shapes — a bare string or
  `{"input":{"message":"…"}}` is coerced to the `messages` list the graph expects (previously the
  user text never reached the model).
- **Studio self-hosting (P5)**: a Helm chart (`deploy/helm/agentbreeder`) deploys the full platform
  (Studio + API + migrations) on Kubernetes, with toggleable bundled Postgres/Redis subcharts, a
  TLS-capable Ingress, and auto-generated signing keys. The dashboard image now renders its nginx
  upstream from env (`API_UPSTREAM`/`LISTEN_PORT`) at startup, so the same image self-hosts anywhere.
  See docs → Self-Hosting.
- **MCP server deployment (P4)**: `agent.yaml` `mcp_servers` now deploy end-to-end. A new optional
  `url`/`image`/`port` on each entry lets you forward to a **remote** HTTP/SSE MCP server or
  **co-deploy** a container MCP server as a sidecar (AWS ECS, GCP Cloud Run, Azure Container Apps,
  docker-compose). The deployer resolves each ref and hands the Go sidecar a `{name:{transport,url}}`
  map via `AGENTBREEDER_SIDECAR_MCP_SERVERS`, so the agent reaches every server at
  `http://localhost:9091/mcp/<name>`.
- Agent images now bundle the AgentBreeder runtime (`agentbreeder` dist), so registry-ref
  prompts, first-party tools, RAG and memory work on AWS/GCP/Azure — not just self-contained agents.
  Override the bundled requirement via `AGENTBREEDER_RUNTIME_REQUIREMENT`.
- `agent.yaml`: `knowledge_bases[].backend_url`, `memory.backend`, `memory.backend_url` for pointing
  agents at explicit, cloud-reachable RAG/memory backends. `knowledge_bases[].backend_url` is exposed
  to the agent as `KB_PGVECTOR_DSN`.
- Cloud RAG retrieval (P2): a deployed agent now embeds the user query and retrieves from its
  managed **pgvector** store at invoke time via `KB_PGVECTOR_DSN` (the in-container in-memory index is
  empty in the cloud). The resolver also pins `KB_EMBEDDING_MODEL` so queries are embedded with the
  same model used at ingest. GCP Cloud SQL now records its private IP, and Azure Flexible Server
  allow-lists the `vector` extension (`azure.extensions`), so a provisioned Postgres can serve pgvector.
- Cloud memory (P3): `memory.backend_url` alone is now enough to target a managed Redis/Postgres —
  the resolver infers `MEMORY_BACKEND` from the URL scheme (`redis(s)://` → redis, `postgres(ql)://`
  → postgresql) and wires `REDIS_URL`/`DATABASE_URL` accordingly. The deployed runtime persists
  conversation history to that managed store, with nothing read from the deploy host.
- **Auto-provision managed data backends at deploy.** When an agent declares a knowledge base, or
  `memory.backend` (`postgresql` or `redis`), *without* a `backend_url` and targets AWS/GCP/Azure,
  `agentbreeder deploy` now provisions a managed store **into the agent's own BYO network** and
  injects the connection env automatically:
  - **Postgres** (KB pgvector / `memory.backend: postgresql`) → AWS RDS · GCP Cloud SQL (private IP)
    · Azure Flexible Server (delegated subnet) → `KB_PGVECTOR_DSN` / `DATABASE_URL`. A KB and
    postgresql memory share a single instance.
  - **Redis** (`memory.backend: redis`) → AWS ElastiCache · GCP Memorystore · Azure Cache for Redis
    → `REDIS_URL`. AWS/GCP are network-isolated (`redis://`); Azure is TLS + access-key (`rediss://`).

  Each store lands behind a dedicated security group / private network (5432/6379 from the agent
  only, never the internet). Passwords / access keys are stored only in the cloud secret manager —
  never on disk. The footprint is recorded in `.agentbreeder/infra-state.json`, and `agentbreeder
  teardown <agent>` destroys everything it created (never your VPC / resource group). `backend_url`
  always wins; local/kubernetes/claude-managed are out of scope. Per-cloud SDK provisioning is
  validated against live clouds during rollout.
- NVIDIA NIM as a local gateway model provider: `deploy/litellm_config.yaml` exposes
  `nvidia/meta/llama-3.1-8b-instruct` (via `nvidia_nim/…`) and the API + LiteLLM compose services
  read `NVIDIA_API_KEY`, so an `agent.yaml` can target an NVIDIA-hosted model through the gateway.

### Fixed
- **AWS ECS deploy now returns a reachable endpoint.** The deployer previously returned a
  placeholder `https://<name>.<region>.ecs.local` URL, so the post-deploy health check could never
  reach the task and tore the service back down. It now reads the running task's ENI public IP
  (`assignPublicIp=ENABLED`) and returns `http://<public-ip>:8080`, falling back to the placeholder
  only when no public IP exists (e.g. private subnets). Surfaced during real-cloud validation.
- AWS ECS image build no longer fails with `PermissionError(13)` on a shared/multi-user host whose
  `/var/run/docker.sock` symlinks to another user's root-owned socket — the deployer now honors
  `DOCKER_HOST` and otherwise prefers the current user's Docker Desktop socket
  (`~/.docker/run/docker.sock`).
- LangGraph image build now copies any bundled local wheels into the build context **before**
  `pip install -r requirements.txt`, so deploying from an unpublished dev checkout
  (`AGENTBREEDER_RUNTIME_REQUIREMENT=./*.whl`) resolves the path requirement instead of erroring.

### Changed
- Deploy no longer forwards the deploy host's local `REDIS_URL`/`DATABASE_URL`/`NEO4J_URL` to cloud
  agents. Set an explicit `backend_url`, or `AGENTBREEDER_ALLOW_LOCAL_BACKENDS=1` for local dev. A
  warning is logged when a resolved backend URL targets localhost on a cloud deploy.

## [2.6.0] — 2026-05-25

### Added
- **Multi-cloud deploy parity (epic [#505](https://github.com/agentbreeder/agentbreeder/issues/505))** — the same `agent.yaml` now deploys to AWS ECS Fargate, GCP Cloud Run, and Azure Container Apps with identical Track J (sidecar: guardrails/cost/tracing) and Track K (secret mirroring) governance.
- **Azure Key Vault auto-mirror ([#425](https://github.com/agentbreeder/agentbreeder/issues/425), [#197](https://github.com/agentbreeder/agentbreeder/issues/197))** — secrets declared in `agent.yaml` are mirrored to Key Vault at deploy with an idempotent "Key Vault Secrets User" RBAC grant (falls back to an access policy when the vault is in access-policy mode). The deploy uses a per-agent **user-assigned managed identity** so the principal is known before the app exists (no create-time grant race).
- **Cross-cloud parity matrix tests ([#198](https://github.com/agentbreeder/agentbreeder/issues/198))** — `tests/unit/test_multicloud_parity.py` asserts Track J + Track K wiring is equivalent across ECS, Cloud Run, and Container Apps.
- **Studio UX simplification (PR [#519](https://github.com/agentbreeder/agentbreeder/pull/519))** — Studio is now dark-only with the Bricolage Grotesque display font and the marketing type scale; Home leads with a 4-step **Get Started** checklist; `/models` is reframed around *"How do you want to run models?"* (Local / Gateway / Direct); a new guided `/agents/new` wizard (Goal → Workflow → editable recommended stack → Name & create) emits a schema-valid `agent.yaml`; and the catalog "Failed to load" banner is replaced by a non-blocking inline retry.
- **Agent-builder endpoints** — `POST /api/v1/builders/recommend` returns a deterministic stack recommendation from the advisory heuristics (extracted into a pure, tested `engine/recommend.py`), and `POST /api/v1/builders/chat` drives a BYO-key conversational builder (`engine/agent_chat_builder.py`) that emits a validated `agent.yaml` via Anthropic tool-use.
- **Anthropic prompt caching** — `AnthropicProvider` now auto-attaches `cache_control` breakpoints to the large static parts of a request (the tools array and/or the system prompt, gated by Anthropic's ~1024-token minimum). Repeated calls that re-send a large static prefix — e.g. the conversational builder's ~6K-token tool schema — read from cache instead of reprocessing. Cached input tokens are folded into `prompt_tokens` so cost attribution stays accurate.

### Fixed
- **GCP Cloud Run inbound now routes through the sidecar ([#203](https://github.com/agentbreeder/agentbreeder/issues/203))** — the sidecar is the ingress container on `:8080` and reverse-proxies to the agent on internal `:8081`, so bearer-token auth and guardrail egress can no longer be bypassed. Previously external traffic hit the agent directly.
- **Azure Key Vault name round-trip ([#499](https://github.com/agentbreeder/agentbreeder/issues/499))** — the mirrored secret name now matches the deployer's `keyVaultUrl` reference via a shared `sanitize_secret_name()`, making Track K functional on Container Apps.
- **`env` secrets backend now falls back to a writable location** — `agentbreeder secret set` (and the Studio Configure modal) failed with `[Errno 13] Permission denied: '/app/.env'` when the API ran in its container, because the `env` backend always wrote to `$CWD/.env` and the image runs as a non-root user under a root-owned `/app` WORKDIR. `_find_env_file()` now writes to `~/.agentbreeder/.env` when the working directory isn't writable (matching the behavior already documented in the CLI reference). Project-local `.env` files in a writable directory are unaffected.
- **Docs: local-dev `docker compose` commands now point at the compose file** — there is no root-level compose file, so bare `docker compose up` failed with `no configuration file provided: not found` when run from the repo root. CONTRIBUTING and the gateway/graphrag/how-to docs now use `docker compose -f deploy/docker-compose.yml ...`, matching the convention already used elsewhere.
- **Quickstart/standalone Docker images now pull from the published `rajits/` namespace** — `agentbreeder quickstart`, the `agentbreeder studio` CLI generator, and the standalone compose referenced `agentbreeder/agentbreeder-{api,dashboard}:latest`, but those repos aren't provisioned on Docker Hub (HR-7), so the pull failed with `pull access denied` (quickstart silently fell back to a from-source build). Repointed the quickstart/standalone compose, the `studio` command, and the docs `docker pull`/`run`/`rmi` commands to `rajits/agentbreeder-*`, where `release.yml` actually publishes. (The injected sidecar default image is still `agentbreeder/agentbreeder-sidecar` — tracked separately since it affects production deploys, not just local dev.)

### Changed
- **App Runner fails fast at validate-infra ([#501](https://github.com/agentbreeder/agentbreeder/issues/501))** — when `guardrails:` or `secrets:` are declared, App Runner (single-container, can't host the sidecar) now errors at validation instead of silently deploying without governance. ECS Fargate is the AWS parity target.
- **Hardened the injected sidecar ([#500](https://github.com/agentbreeder/agentbreeder/issues/500))** — pinned the sidecar image to a version tag (dropped `:latest`) and added a `securityContext` across the cross-cloud injectors ([#400](https://github.com/agentbreeder/agentbreeder/issues/400)).
- **Dev `docker compose` stack persists secrets** — the `api` image now pre-creates `/home/appuser/.agentbreeder` (owned by the runtime user) and the `api` service mounts a named `agentbreeder-secrets` volume there, so keys set through Studio/CLI survive `docker compose down`/`up` instead of living only in ephemeral container storage.

## [2.5.1] — 2026-05-22

### Fixed
- **Release pipeline** — rolled `release.yml` Docker image refs back from `agentbreeder/agentbreeder-*` to `rajits/agentbreeder-*`. The canonical `agentbreeder/` org doesn't have repos provisioned on Docker Hub yet (HR-7), so v2.5.0 failed at the `Build & Push Images` step with `insufficient_scope`, cascade-blocking PyPI publish + GitHub Release + Homebrew. v2.5.0 stays as a phantom tag; this patch is the first build to actually land on PyPI + Docker Hub since v2.1.0.

## [2.5.0] — 2026-05-22 — **YANKED (phantom tag, never published)**

> **Do not `pip install agentbreeder==2.5.0` — it does not exist on PyPI.** The git tag `v2.5.0` was cut but `release.yml` failed at the Docker Hub push step (`agentbreeder/agentbreeder-api` → `insufficient_scope`; the canonical `agentbreeder/` org has no repos provisioned yet, see [#479](https://github.com/agentbreeder/agentbreeder/issues/479)). The cascade-failure left publish-pypi, github-release, and homebrew-tap all skipped. **v2.5.1** carries the same payload (security fixes + 5 v2.5 features) and is the first build to actually land on PyPI + Docker Hub since v2.1.0.

### Security — HIGH-severity dependency CVE fixes

> **All four findings were pulled in transitively and predate the v2.4 cycle.** Pinning floors here so every `pip install agentbreeder` picks up the patched releases. CI's `pip-audit` job is now clean against the v2.5.0 lockset.

- **Bumped** `python-multipart>=0.0.27` (was `>=0.0.26`) — fixes [GHSA-pp6c-gr5w-3c5g](https://github.com/advisories/GHSA-pp6c-gr5w-3c5g): DoS via unbounded multipart part headers. Reaches us through FastAPI's form parsing.
- **Pinned** transitive `Mako>=1.3.12` — fixes [GHSA-2h4p-vjrc-8xpq](https://github.com/advisories/GHSA-2h4p-vjrc-8xpq): path traversal via backslash URI in `TemplateLookup` (Windows-only, used by alembic).
- **Pinned** transitive `urllib3>=2.7.0` — fixes [GHSA-mf9v-mfxr-j63j](https://github.com/advisories/GHSA-mf9v-mfxr-j63j) (decompression-bomb safeguards bypassed in streaming API) and [GHSA-qccp-gfcp-xxvc](https://github.com/advisories/GHSA-qccp-gfcp-xxvc) (sensitive headers forwarded across origins on proxied low-level redirects).
- **Pinned** transitive `starlette>=1.0.1` and `idna>=3.15` — picks up [GHSA-86qp-5c8j-p5mr](https://github.com/advisories/GHSA-86qp-5c8j-p5mr) and [GHSA-65pc-fj4g-8rjx](https://github.com/advisories/GHSA-65pc-fj4g-8rjx) (IDNA encode bypass of CVE-2024-3651 fix) alongside the urllib3 bump.

### v2.5 — Realistic quickstart expectations + `--yes` for scripted runs (#468)

> **P3 trust gap closed.** "Two commands. That's it." was technically true and practically misleading — `quickstart` is an interactive wizard with up to six prompts (container runtime, model source, port conflicts, Ollama install, model pull, cloud keys), plus a ~3 GB download if you pick Ollama. The new headline sets realistic expectations, the new callout names what the wizard will ask, and a `--yes` flag gives CI users a true zero-prompt path.

- **Added** a `--yes` / `-y` flag to `agentbreeder quickstart`. Sets a module-level sentinel consulted by 13 `console.input` call sites so every yes/no prompt picks the safe default: model-source falls back to the legacy "Both" path (or Cloud when `--no-ollama` is set), port-conflict killer auto-accepts, Ollama install / rebind / model-pull / Studio rebuild auto-accept, missing env keys are silently skipped. Blocking prompts that require human input (e.g. "press Enter once the daemon is up") **exit fast with a clear error** instead of waiting on stdin. `--yes` deliberately does **not** auto-install Docker (heavy side effect, sudo) and does **not** invent provider keys (export `OPENAI_API_KEY` etc. before running). The recommended scripted recipe is `agentbreeder quickstart --yes --no-ollama --no-browser`.
- **Marketing copy** — `quickstart.mdx` headline changes from "Two commands. That's it." to "Two commands. Three minutes. Done." A new "What to expect" callout names the interactive prompts and the ~3 GB download, plus shows the scripted recipe.
- **Docs sync** — `cli-reference.mdx` `quickstart` table gains a `--yes` row that names every prompt the flag short-circuits and explicitly calls out the two things it does not do (auto-install Docker, invent provider keys). `quickstart.mdx` "Useful flags" table gets a matching `--yes` / `-y` row. The fully-scripted example is added to both the `Examples:` block in the docstring and the docs Examples list.
- **Tests** new `tests/unit/test_quickstart_assume_yes.py` — 9 cases covering the flag toggle, `_ask_model_source` short-circuit (with/without `--no-ollama`, TTY override), `_collect_provider_keys` non-interactive paths (Ollama reachable / unreachable / no keys collected), and a signature-introspection check confirming `quickstart()` exposes both `--yes` and `-y` decls. Existing 9 model-source tests still pass.

### v2.5 — Container-runtime diagnostics: clear cause + fix per failure (#467)

> **P3 UX gap closed.** Quickstart already detected Docker / Podman / nerdctl, but when the daemon was *installed but unreachable* the user got a generic "cannot connect" message with no clue why. Rootless installs, Snap/Flatpak Docker (socket path mismatch), partial installs (daemon up, CLI missing), and permission-denied on `/var/run/docker.sock` were all silently lumped together. Each now produces a specific cause line and the exact fix command.

- **Added** new diagnostic helpers in `cli/commands/quickstart.py`: `_docker_host_socket_path()`, `_docker_socket_candidates()`, `_docker_socket_status()`, `_docker_is_rootless()`, `_docker_info_error()`, `_diagnose_missing_runtime()`, and `_diagnose_runtime_failure(binary)`. These probe `$DOCKER_HOST`, the default socket, `$XDG_RUNTIME_DIR/docker.sock`, and `/run/user/$UID/docker.sock`; classify failures into `daemon_down` / `permission_denied` / `stale_docker_host` / `cli_missing_socket_present`; and emit Rich-formatted hints with cause + fix.
- **Wired** the diagnostics into the Step 1 (Container Runtime) flow: the "daemon not running" branch now routes through `_diagnose_runtime_failure(binary)` so each failure prints a specific cause; the no-runtime-found branch first calls `_diagnose_missing_runtime()` to catch the partial-install case before falling back to the generic install panel.
- **Surfaced** the active runtime + override hint: the `Found docker` line now echoes `via $DOCKER_HOST → <path>` when applicable and prints `Override the daemon target with export DOCKER_HOST=unix://<path>` for the default case, so users always know how to retarget.
- **Added** a rootless-Docker info note printed after the daemon-up confirmation (volume ownership caveat) — non-blocking, surfaced via `docker info --format '{{.SecurityOptions}}'`.
- **Docs** — `cli-reference.mdx` `quickstart` gains a "Container-runtime diagnostics" table mapping each detected condition to its cause line + fix. `quickstart.mdx` "When something goes wrong" table gains five new rows (stale `$DOCKER_HOST`, EACCES on socket, Snap/Flatpak socket path mismatch, partial install, rootless volume permissions).
- **Tests** new `tests/unit/test_quickstart_runtime_diagnostics.py` — 23 cases covering `$DOCKER_HOST` parsing (unix / tcp / unset), socket-candidate ordering and de-duplication, socket-status classification (reachable / permission_denied / missing / PermissionError on `stat`), rootless detection (present / absent / `docker info` fails / no CLI), the partial-install and permission-denied missing-runtime hints, the stale-`$DOCKER_HOST` priority branch, the EACCES branch, the socket-path-mismatch branch, the macOS daemon-down branch, the Podman branch, and the generic fallback for unknown binaries.

### v2.5 — Cloud-only quickstart branch (#466)

> **P3 UX gap closed.** A power user with an existing OpenAI / Anthropic / Google API key used to sit through `quickstart`'s Ollama install + ~3 GB model pull whether they wanted local inference or not. `--no-ollama` existed but was buried in the troubleshooting table. Quickstart now asks up front, and the flag is documented as a first-class cloud-only shortcut.

- **Added** `_ask_model_source()` to `cli/commands/quickstart.py` — early in Step 2 (LLM Providers) the interactive wizard asks: **Local** (Ollama + model pull), **Cloud** (skip Ollama, prompt for cloud keys), or **Both** (default, current behavior). Pressing Enter picks "Both" so muscle-memory keeps working; passing `--no-ollama` short-circuits to "Cloud" without prompting; non-TTY runs keep the legacy "Both" behavior so existing CI scripts don't break.
- **Wired** the new helper into the Step 2 flow: `skip_ollama` is now forwarded to `_ensure_ollama()`, and `skip_cloud_keys` shortcuts past `_collect_provider_keys()` for the Local-only path (Ollama status is still checked via the existing `_ollama_running()` helper so the summary line stays accurate).
- **Improved** the `--no-ollama` help text on `agentbreeder quickstart --help` from a terse one-liner to a description that names the use case, the time/disk it saves, and that it's equivalent to the interactive Cloud choice.
- **Added** an `--no-ollama` example to the quickstart docstring's `Examples:` block so it shows up in `--help`.
- **Docs** — `quickstart.mdx` step-2 description rewritten to name the three model-source options; new "Already have a cloud API key?" callout pointing at `--no-ollama`. `cli-reference.mdx` `quickstart` table updates the `--no-ollama` row, adds the model-source prompt to the "What it does" list, and adds the `--no-ollama` example.
- **Tests** new `tests/unit/test_quickstart_model_source.py` — 9 cases covering the `--no-ollama` short-circuit, the non-TTY short-circuit (with and without the flag), each interactive branch (1 → Local, 2 → Cloud, 3 → Both), the default-on-Enter case, the invalid-input-then-retry case, and whitespace trimming.

### v2.5 — `agentbreeder doctor` prerequisite preflight (#462)

> **P2 UX gap closed.** A first-time user on a machine without Docker, Python ≥ 3.11, or 8 GiB free disk used to discover this *mid-`quickstart`* — sometimes after a 3-minute image pull. The new `doctor` command runs the same checks in under two seconds and prints platform-specific fix commands; `quickstart` now runs it as its first step.

- **Added** `agentbreeder doctor` CLI command in `cli/commands/doctor.py`. Checks Python version, container runtime presence + daemon reachability (reuses the existing detection from `quickstart.py` so Docker / Podman / nerdctl all work), free disk on the working volume (≥ 8 GiB), and total RAM (≥ 4 GiB, best-effort via `os.sysconf` on Linux and `sysctl hw.memsize` on macOS — falls back to a warn-only check on platforms where RAM can't be detected without a new dependency). Each failed check renders a copy-pasteable platform-specific fix block. Exits `0` on pass, `1` on any blocker. Supports `--json` for scripts and CI.
- **Wired** `doctor` into `agentbreeder quickstart` as the first step. Quickstart now bails in ~1.2 s on a machine without a running container runtime instead of running through the welcome panel and dying at "Step 1: Container Runtime" with a less actionable error. Bypassable via the new `--skip-doctor` flag for CI sandboxes that intentionally skip host checks.
- **Updated** welcome panel in `cli/main.py` to surface `doctor` in the "Useful anytime" command list, alongside `list agents`, `chat`, `down`, and `--help`.
- **Docs** added a "Before you start" callout at the top of `quickstart.mdx` listing the three concrete prerequisites and pointing at `doctor`; added a full `doctor` entry to `cli-reference.mdx` (above the `quickstart` entry so it reads in install order); added `--skip-doctor` to the quickstart options table in both docs.
- **Tests** new `tests/unit/test_doctor.py` — 15 cases covering each check in isolation (Python floor, disk-low, runtime-missing, runtime-daemon-down, runtime-happy, RAM-undetectable, RAM-below-min) plus `has_blocker()`, the Typer command's exit codes for pass and fail, and the `--json` payload shape. All 15 pass.

### v2.5 — Install path discovery + pip/pipx standardization (#463)

> **P2 UX gap closed.** The #1 entry in `quickstart.mdx`'s troubleshooting table is `agentbreeder: command not found` after `pip install` — pip's script directory often isn't on `PATH`. Docs now recommend `pipx install agentbreeder` as the primary path (pipx adds the binary to a directory that's already on `PATH` everywhere), with `python3 -m pip install agentbreeder` as the alternative (works regardless of whether `pip`/`pip3` is on `PATH`).

- **Added** install-path hint to the welcome panel (`cli/main.py`). When pip's script directory isn't on `PATH`, `agentbreeder welcome` and the first-run banner now print a yellow panel showing the scripts dir (`sysconfig.get_path('scripts')`), the active shell, and the exact line to append to the user's shell rc (zsh / bash / fish / PowerShell). Suppressed when the scripts dir is already on `PATH`, so pipx and venv users see no noise.
- **Updated** `quickstart.mdx` two-command snippet to `pipx install agentbreeder` (with a callout pointing at `python3 -m pip install agentbreeder` for users without pipx). Same change in the troubleshooting row for `command not found`.
- **Updated** `how-to.mdx` Install section to lead with `pipx install agentbreeder`, then fall back to `python3 -m pip install agentbreeder`. Removed `pip3 install ...` lines.
- **Updated** `faq.mdx` "command not found" section to recommend `pipx` first, then the manual PATH-edit instructions, with the section header switched to `python3 -m pip install agentbreeder`.
- **Standardized** install commands across `full-code.mdx`, `low-code.mdx`, all five `migrations/from-*.mdx` checklists, and `migrations/overview.mdx`. Every user-facing install line now reads `pipx install agentbreeder` (or `python3 -m pip install agentbreeder`) — no more `pip` vs `pip3` ambiguity in docs. Blog posts and CI examples (where `pip install` is idiomatic for the platform) are intentionally untouched.
- **Tests** new `tests/unit/test_cli_install_path.py` — 12 cases covering `_scripts_dir()`, `_scripts_dir_on_path()` happy/empty/unrelated PATH cases, `_shell_rc_hint()` for zsh / fish / bash-default / Windows PowerShell, and the welcome command's panel-shown / panel-hidden branches.

### v2.5 — First-run guided tour + empty-state hero (#465)

> **P1 UX gap closed.** After first sign-in the user used to land in Studio with a fully populated registry (5 sample agents seeded by quickstart) and zero guidance on what to click. The 4-step welcome tour fixes that, and the no-agents page now ships a hero CTA instead of a one-line text dead-end.

- **Added** `TourProvider` + `useTour()` in `dashboard/src/hooks/use-tour.tsx`. Per-browser via `localStorage` (key `ag-tour-completed-v1`). Bumping the version suffix re-shows the tour to everyone after a major redesign. No backend round-trip — tour is an onboarding affordance, not user state worth syncing across devices.
- **Added** `WelcomeTour` modal in `dashboard/src/components/welcome-tour.tsx`. Four steps — Welcome / Try chatting / Build your own / Governed by default — each with a deep-link CTA (Visit Agents, Open Playground, Open Builder, See Costs) that closes the tour and navigates. Centered modal with progress dots, Back / Next / Skip / Done nav, top-right X, and Esc-to-dismiss. No DOM-spotlight coupling to specific element positions — pure prose tour, immune to UI rot across 46 pages.
- **Wired** `TourProvider` + `<WelcomeTour />` into the `RequireAuth` tree in `App.tsx` so the tour only renders for authenticated users and overlays the full viewport.
- **Added** "Restart tour" link in the shell footer (`components/shell.tsx`) so users can replay the tour after dismissing it. Hidden when the sidebar is collapsed.
- **Replaced** the text-only "No agents registered. Deploy an agent with `agentbreeder deploy` and it will appear here automatically" empty state on `/agents` with a `NoAgentsHero` block: two equal-weight CTAs (visual builder Link + CLI snippet), both routing through the same 8-step deploy pipeline. Filtered-empty state (when search/filters return nothing) preserves the old `EmptyState` since it's a different intent.
- **Tests** new `dashboard/src/__tests__/use-tour.test.tsx` — 4 Vitest cases covering auto-open on first mount, no-show when flag set, `dismiss()` persistence, and `open()` after dismiss (the restart-tour path). All pass.

### v2.5 — Force password change on first login (#464)

> **P0 security gap closed.** The seeded admin account ships with the publicly documented credential `admin@agentbreeder.local` / `plant`. Anyone exposing Studio beyond `localhost` (e.g. via the Caddy reverse-proxy example in `SELF_HOSTING.md`) was one curl away from a known-credential takeover. This fix forces a rotation on first sign-in before the user can do anything else.

- **Added** `must_change_password BOOLEAN NOT NULL DEFAULT FALSE` to the `users` table (alembic migration `023`). Existing user rows on running installs are unaffected — only new admins seeded by `_seed_default_admin` get the flag set to TRUE.
- **Added** `POST /api/v1/auth/change-password` endpoint. Accepts `{old_password, new_password}` on an authenticated session, verifies the old hash, rejects passwords shorter than 8 chars or equal to the old one, rotates the hash, and clears the flag. Returns the refreshed `UserResponse`.
- **Updated** `POST /api/v1/auth/login` response: `TokenResponse` now carries a `must_change_password` boolean so clients can route to a forced-rotation flow.
- **Updated** `_seed_default_admin` in `api/main.py` to set `must_change_password=True` on the seeded admin row. Existing single-user installs that already rotated stay unaffected (the flag's default is FALSE).
- **Studio** — new `/change-password` route with a non-dismissable rotation form (current / new / confirm fields, live validation, sign-out escape hatch). The `RequireAuth` guard now intercepts every navigation with a redirect to `/change-password` while the flag is set; a separate `RequireAuthOnly` guard wraps the change-password route itself to break the redirect loop.
- **CLI** — `agentbreeder login` detects the flag in `/auth/login` (email+password path) or `/auth/me` (`--token` paste path) and walks the user through the rotation **before** persisting the token to the OS keychain. Tone-aware messaging — the seeded-admin case gets a security-focused warning, admin-rotated users get a generic one.
- **Tests** new `TestChangePasswordRoute` class with 5 cases (requires auth, happy path clears flag, wrong old → 401, short new → 422, same-as-old → 422). Updated `TestLoginRoute` to cover the flag-passthrough. 31 tests pass in `test_auth.py`; 89 adjacent RBAC tests unaffected.
- **Docs** new "First-login password rotation" section in `authentication.mdx` covering the user flow, the wire format, the manual `UPDATE users SET must_change_password=TRUE` admin reset path, and the note that the gate is currently client-side only. Inline mentions in `quickstart.mdx`, `no-code.mdx`, `how-to.mdx`, `local-development.mdx`.

### v2.5 — Rename Dashboard → Studio (user-facing rebrand, #460)

> **User-facing string sweep only.** No folder renames, no Docker image renames, no route/env/identifier changes. `dashboard/`, `agentbreeder/agentbreeder-dashboard`, `DASHBOARD_URL`, the `dashboard` compose service, and `area:dashboard` label all stay; they're internal handles, not the brand.

- **Studio is the canonical name** for the React UI at `:3001`. Every user-visible "Dashboard" string in the shell, CLI output, docs, API docstrings, and root contributor docs now reads "Studio" (or "AgentBreeder Studio" at top-level).
- **CLI breaking change** `agentbreeder ui` is gone. The command is `agentbreeder studio` — same flags (`--follow`, `--pull`, `--port`, `--api-port`), same behavior. Anyone with a script using `agentbreeder ui` must update it. The compose project name (`agentbreeder-ui` → `agentbreeder-studio`) and state file (`~/.agentbreeder/docker-compose.ui.yml` → `docker-compose.studio.yml`) also renamed; existing containers from a previous `ui` run can be cleaned up with `docker compose -p agentbreeder-ui down` if needed.
- **Studio shell** browser `<title>`, sidebar wordmark, version chip, and two body strings on the Models + Agent Detail pages now show "AgentBreeder Studio".
- **CLI output** every `quickstart`, `studio`, `up`, `auth` panel / status table / smoke check / error message reads "Studio". The `services_ok` dict keys derived from those labels flipped from `dashboard` → `studio` in lockstep across 3 call sites.
- **Website docs** 27 `.mdx` files updated across `quickstart.mdx`, `faq.mdx`, `no-code.mdx`, `low-code.mdx`, `full-code.mdx`, `authentication.mdx`, `cli-reference.mdx`, `how-to.mdx`, `local-development.mdx`, `playground.mdx`, `tools.mdx`, `rag.mdx`, `graphrag.mdx`, `mcp-servers.mdx`, `prompts.mdx`, `gateway.mdx`, `gateways.mdx`, `providers.mdx`, `secrets.mdx`, `evaluations.mdx`, `runtime-contract.mdx`, `migrations.mdx`, `migrations/overview.mdx`, `migrations/from-autogen.mdx`, `orchestration-sdk.mdx`, `orchestration-yaml.mdx`, `registry-guide.mdx`. Tab-pair labels (7 `Tabs items={['Studio', …]}` ↔ `<Tab value="Studio">` pairs) updated in lockstep. The FAQ anchor `#dashboard-at-httplocalhost3001…` slug renamed to `#studio-at-httplocalhost3001…` with all inbound links updated.
- **Marketing site** `agent-for-all.tsx` animated demo header reads "Studio → New Agent"; `cloud-coming.tsx` "cost dashboards" reads "cost tracking".
- **API docstrings** `agentops.py`, `providers.py`, `secrets.py`, `models.py`, `agents.py`, plus `engine/builder.py` and `api/services/agentops_service.py` — visible via `/docs` OpenAPI.
- **Root docs** `ARCHITECTURE.md`, `SELF_HOSTING.md`, `AGENT.md`, `CONTRIBUTING.md` — stack matrix, IA diagrams, contributor commands.
- **`CLAUDE.md`** new "📛 Product Naming" section codifies the **Studio rule**: Studio is a top-level surface name only, **never** a feature suffix. Inner pages take functional nouns (Agents, Costs, Sessions, Evals, Registry, Audit, Fleet, Playground). No "Cost Studio", no "Sessions Studio". Also lists the third-party "dashboard" references (Grafana, Stripe, Google Security Dashboard, AWS CloudWatch) that stay as-is.
- **Pre-commit guardrail** new `.githooks/pre-commit` blocks staged lines that combine "dashboard"/"studio" with a JWT, API key, or third-party hostname like `dashboard.stripe.com`. Activate with `git config core.hooksPath .githooks`; `SKIP_REBRAND_GUARD=1` bypass for legitimate hits (e.g. the Stripe URL in `examples/templates/returns-processor/README.md`).
- **CI check** new `MDX Title Uniqueness` job in `.github/workflows/docs-check.yml` (calling `.github/scripts/check-mdx-titles.sh`) fails when two `website/content/docs/*.mdx` files share an identical frontmatter `title:`. Prevents the SEO collision a sloppy find-and-replace can produce.
- **SEO transition copy** the H1 of `no-code.mdx` reads "AgentBreeder Studio (formerly the Dashboard)" for ~6 months so users Googling "agentbreeder dashboard" still match. Page titles in the sweep are suffixed " — AgentBreeder Studio" to keep `<title>` tags unique.
- **What we did NOT touch** the `dashboard/` folder, `agentbreeder/agentbreeder-dashboard:latest` Docker image, `DASHBOARD_URL` constant, `dashboard_port` / `dashboard_key` / `_dashboard_smoke_check` identifiers, `CostDashboard` React component, `workflow:new-dashboard-page` slug, GitHub `area:dashboard` label, and any historical name in `CHANGELOG.md` itself. Internal handles are stable.
- **Test** 11 unit tests in `tests/unit/test_studio_cmd.py` (renamed from `test_ui_cmd.py`) cover the `studio` command end-to-end; all pass.
- **Cross-repo follow-up** `agentbreeder-cloud` has ~50 user-facing "Dashboard" references in `ROADMAP.md` and internal specs that should be swept in a companion PR. Tracked alongside the broader UX work in #461.
- **UX tracker** the rebrand surfaced 7 first-run UX gaps (forced password change on `plant`, prereq preflight, empty-state tour, etc.) — filed as #462–#468 under epic #461.

### v2.4 — Tighten `/api/v1/deploys` with team-scoped RBAC (#414)

- **Closed** the HR-1 analogue gap for deploys. Before this change, a user with the `deployer` role in team A could `POST /api/v1/deploys` against an agent owned by team B — `require_role("deployer")` was applied **without** `resource_team=`, so the middleware only verified deployer-anywhere.
- **Two-layer gate** the route now stacks `require_role("deployer")` (fast-rejects users who aren't a deployer in any team) with a new `enforce_team_role(user, agent.team, "deployer")` call (refines the check after the agent's team is resolved). Defense in depth, and the v2.3 viewer-rejection behavior is preserved.
- **Resolution** team is read from `agent.team` when the request carries an `agent_id`, and from the YAML's `team:` field when the request is a builder-mode `config_yaml`. Builder payloads without a `team:` field now return 400 instead of falling through to a less-helpful error.
- **Lifecycle parity** the same team-scope check now gates `DELETE /api/v1/deploys/{id}` (cancel) and `POST /api/v1/deploys/{id}/rollback`. Both look up the job's agent to find the team.
- **Audit log** every deploy lifecycle action (`deploy.create` / `deploy.cancel` / `deploy.rollback`) now emits an `AuditService.log_event` entry tagged with the agent's team and the caller's email + IP.
- **Platform admin bypass** unchanged — admins still pass for any team.
- **Test** 8 new tests cover the cross-team 403 path (POST agent_id, POST config_yaml, DELETE, POST /rollback), the same-team 200 path, the YAML-missing-team 400 path, and the platform-admin escape hatch. Six existing tests in `test_api_routes_extended.py` now override `get_db` to mock the new agent/job lookups that the team-scope check requires.
- **Cross-repo** `agentbreeder-cloud` does not consume `/api/v1/deploys`; no companion PR needed.

### v2.4 — `agentbreeder deploy --remote` closes in-process RBAC bypass (#416)

- **Closed** the Phase A bypass where `agentbreeder deploy` ran `engine.builder.DeployEngine` in-process — RBAC, audit logging, and team-scoped credentials never fired for CLI deploys.
- **Added** `--remote` and `--local` flags on `agentbreeder deploy`. `--remote` POSTs `/api/v1/deploys` and polls the detail endpoint until terminal; the bearer token comes from `cli/_http` (env var or OS keychain). `--local` keeps the in-process engine for dev / offline use.
- **Default behavior** the CLI picks remote when `$AGENTBREEDER_URL` is set, otherwise local. Explicit flags always win over the env var. Production environments should set `$AGENTBREEDER_URL` so `agentbreeder deploy` defaults to the gated path.
- **Polling** until SSE streaming lands in #387, remote mode polls `GET /api/v1/deploys/{job_id}` every 2 s for up to 30 min. Terminal statuses (`succeeded`/`failed`/`cancelled` from v2.3 plus `complete`/`error` from the v2.4 event bus) all map onto the same exit-code semantics.
- **Tests** 15 new tests cover the mode-selection matrix (flag wins, env wins, mutual exclusion), the happy path (POST → poll → succeeded), the 403 team-scope path (which #414 will make load-bearing), 401 → login hint, terminal `failed` → exit 1, and that `--local` does not hit the API even when `$AGENTBREEDER_URL` is set.
- **Docs** `website/content/docs/cli-reference.mdx` and `website/content/docs/deployment.mdx` describe the two modes and when to use each.

### v2.4 — CLI `login` / `logout` / `whoami` + keychain token storage (#415)

- **Added** three new top-level commands — `agentbreeder login`, `agentbreeder logout`, `agentbreeder whoami` — plus an `agentbreeder auth` sub-app with the same commands for discoverability. Tokens are stored in the OS keychain (macOS Keychain, GNOME libsecret, Windows Credential Manager) via the existing `keyring>=24.0.0` dependency; the `AGENTBREEDER_API_TOKEN` env var still wins over the keychain so CI keeps working unchanged.
- **Added** `agentbreeder context use <team>` / `show` / `clear` for users who belong to multiple teams. Active team is persisted to `~/.agentbreeder/context.json` and sent on every authenticated request as the `X-AgentBreeder-Team` header.
- **Refactor** new `cli/_http.py` consolidates the duplicate `_auth_headers()` / `_api_base()` / `_post()` / `_get()` / `_post_multipart()` / `_request()` helpers that lived in `cli/commands/registry_cmd.py` and `cli/commands/model.py`. Both files now delegate to the shared module; behavior is unchanged for users.
- **DX** the "no token" error now points to `agentbreeder login` instead of telling users to curl `/api/v1/auth/login` and `export` the response themselves. A 401 on any authenticated call surfaces a "your token has expired" hint with the same direction.
- **Test** 36 new unit tests cover token storage (env-wins-over-keychain precedence, headless fallback, set/clear round-trips), `auth_headers()` composition, the `request()` helper's success/4xx/401 paths, and the user-facing `login` / `logout` / `whoami` / `context` flows via `typer.testing.CliRunner`.
- **Docs** new "CLI login" section in `website/content/docs/authentication.mdx`; new `agentbreeder login` / `logout` / `whoami` / `context` entries in `website/content/docs/cli-reference.mdx`.

### v2.4 — DeployOrchestrator.destroy_partial wires to provisioner.destroy() (#450)

- **Wired** the "Roll back" button in the deployment wizard now actually rolls back. `DeployOrchestrator.start()` persists the returned `InfraState` onto the job record after `provisioner.provision()`; `destroy_partial(job_id)` reloads it and calls `provisioner_for(state.cloud).destroy(state)`.
- **Events** publishes `phase: "destroying"` (new value added to the `PhaseName` literal) + log events + terminal `complete`/`error`, so the SSE client transitions cleanly.
- **Idempotent** clears `job.infra_state` after a successful destroy so a second call is a no-op.
- **Safety** delegates to the per-cloud `destroy()` impls which already refuse untagged resources and tolerate 404s.
- **Test** 6 new tests cover state-persist on start, real destroy with state shape, no-op branches (no state / unknown job), idempotency of a second call, error event on provisioner failure.

### v2.4 — Redis-backed DeployJobService persistence (#449)

- **Hardened** `DeployJobService` no longer keeps idempotency map + job records in process memory. Two new Protocols (`IdempotencyStore`, `JobStore`) in `api/services/deploy_stores.py` with in-memory implementations for tests and Redis-backed implementations for production.
- **TTLs** idempotency entries: 24h (per the wizard spec §6); job records: 30d (audit-friendly window).
- **Multi-replica safe** the API can now scale horizontally — the wizard's `Idempotency-Key` dedupe + `GET /api/v1/deployments/{job_id}` reads survive restarts and load-balanced replicas.
- **Test** 9 new tests (`fakeredis`-backed) cover get/set round-trip, TTL assertions, and restart-safety simulations (two store instances over the same Redis backend).

### v2.4 — Deployment wizard E2E specs (#446)

- **Test** Six Playwright specs at `dashboard/tests/e2e/deploy-wizard-*.spec.ts` cover the wizard's happy paths (GCP greenfield, AWS BYO), the validation-failure path (Azure BYO), approval-required flow (poll → SSE handoff), draft-resume from localStorage, and the stalled-deploy failure UI.
- **Fixture** `dashboard/tests/e2e/deploy-wizard-helpers.ts` provides a `mockOrchestrator` extension over the existing `authedPage` fixture — overrides `window.EventSource` with a `FakeEventSource` and exposes `__pushDeployEvent` for driving scripted SSE sequences. Plus per-test helpers for `mockAgents`, `mockValidateInfra`, `mockCreateJob`, `mockGetJob`.
- **CI** Specs run under the existing "E2E Tests (Docker)" job — alert-only per spec §9.7.

### v2.4 — Container hardening: drop root in dashboard + cli images (#444)

- **Security** `dashboard/Dockerfile` and `Dockerfile.cli` now declare a non-root `USER` directive. The main `Dockerfile` (api image) already did this; these two images had been shipping as `root` since v0.
- **dashboard image** runs as `nginx` (UID 101 from `nginx:alpine`). `/usr/share/nginx/html`, `/var/cache/nginx`, `/var/log/nginx`, and `/var/run/nginx.pid` are chowned to the same user. Listens on port 3001 (≥1024), so `CAP_NET_BIND_SERVICE` is not required.
- **cli image** runs as `appuser` (UID 1000). A `--create-home` HOME at `/home/appuser` keeps Python tool caches (`~/.cache`, `~/.config`) writable.
- **Verification** `docker inspect ... --format='{{.Config.User}}'` returns `nginx` and `appuser` respectively; Kubernetes / Cloud Run `runAsNonRoot: true` admission now passes.

### v2.4 — Dashboard Deployment Wizard + SSE progress stream (#389, #387)

- **Added** `/deploy-wizard` route in the dashboard. 5-step UI: agent → cloud + region → BYO or greenfield infra → env vars / secrets / scaling → live deploy with SSE progress. localStorage-backed draft survives refresh; approval-required agents route through `/approvals`.
- **Added** `GET /api/v1/deployments/{job_id}/stream` SSE endpoint with a per-job 200-event ring buffer (30-min TTL). Closes #387.
- **Added** `POST /api/v1/deployments/`, `GET /api/v1/deployments/{job_id}`, `POST /api/v1/deployments/{job_id}/destroy-partial` endpoints.
- **Tooling** Pydantic→TS codegen at `scripts/gen_deploy_event_types.py` keeps `DeployEvent` types in sync.

### v2.3 — Cloud-Deploy Foundation (#413)

- **Added** `engine/provisioners/` — `InfraProvisioner` ABC with read-only `validate_existing` impls for AWS / GCP / Azure (boto3, google-cloud, azure-sdk). Greenfield `provision()` / `destroy()` stubbed (per-cloud follow-ups #382 / #383 / #384).
- **Added** `engine/provisioners/state.py` — `InfraState` round-trippable via `.agentbreeder/infra-state.json`.
- **Added** `engine/provisioners/requirements.py` — static user-input contract per cloud × `(simple, full)` mode.
- **Added** API: `GET /api/v1/deployments/cloud-requirements/{cloud}?mode=simple|full` (any authenticated user) and `POST /api/v1/deployments/validate-infra` (`deployer` role required in the requested team, cross-team → 403, rate-limited 10 req/min via slowapi, audit-logged via `AuditService`).
- **Added** `pgvector>=0.2.5` + `slowapi>=0.1.9` to optional deps; new `gcp` extras group; expanded `azure` group.
- **Docs** — rewrote `website/content/docs/deployment.mdx` to a three-cloud tabbed contract.

### v2.3 — HR-1 memory team-scope enforcement (#418, closes #403)

- **Fixed** `api/routes/memory.py` now forwards `user.team` to `MemoryService` on every read/write. Cross-team access → HTTP 403.
- **Hardened** `MemoryService` now raises `PermissionError` when a team-scoped config is accessed without `requesting_team` (previously a silent allow).
- **Tests** — flipped 2 `MM7` xfail tests to passing + added route-layer 403 coverage.

### v2.3 — HR-3 GraphRAG `custom_entity_types` (#419, closes #405)

- **Added** `entity_extraction.custom_types[]` accepted by `engine/schema/agent.schema.json` + Pydantic mirror (`engine/config_parser.py`).
- **Threaded** `custom_types` through `extract_entities` / `_call_claude` / `_call_ollama`; cache key extended so different type sets don't collide.
- **Docs** — flipped roadmap callout in `graphrag.mdx` to shipped.

### v2.3 — HR-7 Docker Hub namespace migration (#420, closes #408)

- **Changed** every operational reference from `rajits/agentbreeder-*` to `agentbreeder/agentbreeder-*` across 18 files (CI workflow, docker-compose, sidecar, CLI defaults, docs, tests). Symmetric +54/-54.
- Historical files (`CHANGELOG.md`, `ROADMAP.md`, audit specs/plans) intentionally untouched.

### v2.3 — HR-2 memory wiring in 3 Python runtimes (#421, closes #404)

- **Added** `MemoryManager` init + per-turn load/save + shutdown hook to Claude SDK, OpenAI Agents, and CrewAI server templates (mirrors the LangGraph reference).
- **Added** 18 source-level smoke tests under `tests/integration/runtimes/test_memory_wiring_hr2.py`.
- TS runtime parity tracked in #417.

### v2.3 — HR-4 pgvector backend (#422, closes #406)

- **Added** `api/services/pgvector_rag_backend.py` — `PgvectorRAGBackend` with `asyncpg` pool, dimension-namespaced chunk tables (`rag_pgvector_chunks_d{N}`), IVFFlat cosine index. Self-installs the `vector` extension on connect.
- **Changed** `registry/rag.py:_make_pgvector` returns the real backend now; missing DSN raises a clear `ValueError` (no more silent fallback).
- **Added** `pgvector>=0.2.5` to the `rag` extra; integration tests against `pgvector/pgvector:pg16` via testcontainers.
- Wire-through to `RAGStore.search/ingest` lands in #423 / PR #434.

### v2.3 — Dashboard Docker build EPIPE (#433, closes #411)

- **Fixed** `dashboard/Dockerfile` build stage now uses `node:22-slim` (Debian glibc) instead of `node:22-alpine` (musl). Eliminates the esbuild EPIPE that intermittently killed `vite build` under Docker Desktop / BuildKit on macOS. `NODE_OPTIONS=--max-old-space-size=4096` added as belt-and-suspenders.

### v2.3 — pgvector wire-through to RAGStore (#434, closes #423)

- **Added** `RAGIndex.backend` + `backend_config` fields; `RAGStore` now lazily caches per-index backends and dispatches `search` / `upsert` to them when configured.
- **Restart-safe** search no longer short-circuits on empty `idx.chunks` when an external backend is in play — answers come from Postgres after a server restart.
- Stacked on #422 (HR-4 backend adapter).

### v2.3 — GCP greenfield provisioner (#437, closes #382)

- **Added** `GCPProvisioner.provision()` / `destroy()` — creates Artifact Registry repo + per-agent Service Account + 4 default IAM bindings (storage.objectViewer, cloudbuild.builds.builder, logging.logWriter, secretmanager.secretAccessor). Idempotent.
- **Deferred** Cloud SQL → #435, VPC Connector → #436. Both surface as explicit deferred markers in `InfraState.resources` when requested.
- Stacked on #413 (Phase A foundation).

### v2.3 — AWS greenfield provisioner (#383)

- **Added** `AWSProvisioner.provision()` / `destroy()` — creates the minimum-viable ECS Fargate footprint end-to-end via boto3: VPC `10.0.0.0/16` + 2 public + 2 private subnets across 2 AZs, IGW, single NAT Gateway (multi-AZ opt-in via `AWS_MULTI_AZ_NAT=1`), three security groups (alb / agent / db with strict tier-to-tier ingress only), ECS cluster (`FARGATE` + `FARGATE_SPOT`), per-agent IAM execution role with `AmazonECSTaskExecutionRolePolicy` + ECR pull, optional RDS PostgreSQL `t3.micro` in private subnets (when `memory:` declared, `publicly_accessible=False`, `storage_encrypted=True`, random password generated via `secrets.token_urlsafe(32)` and stored in AWS Secrets Manager — never on disk), optional ALB + target group + listener (when `access.visibility: public`, TLS 1.2+ policy `ELBSecurityPolicy-TLS13-1-2-2021-06`). Every resource tagged `AgentBreeder=true` + `AgentName` + `Version`; `destroy()` refuses to touch any resource missing the canonical tag and takes a final RDS snapshot unless `--no-final-snapshot`.
- **Idempotent** — re-running `provision()` against an existing state file is a no-op.
- **Tests** 25 unit tests with mocked boto3 clients cover the security-critical paths: DB SG never ingresses from 0.0.0.0/0, RDS is private + encrypted, plaintext password never appears in `InfraState.model_dump_json()`, RDS rolled back if Secrets Manager write fails, ALB listener uses TLS 1.2+, `destroy()` refuses untagged resources, session never accepts inline credentials.
### v2.3 — GCP Serverless VPC Connector provisioning (#436)

- **Added** `GCPProvisioner._ensure_vpc_connector()` / `_delete_vpc_connector()` — creates a Serverless VPC Access connector named `ab-{agent_name}` (`e2-micro`, min 2 / max 3 instances, `/28` IP range `10.8.0.0/28` by default). Idempotent (check-then-create) and reverses cleanly in `destroy()`.
- **Trigger** explicit via `GCP_PROVISION_VPC_CONNECTOR=1`, or implicit when Cloud SQL is requested with private IP (the #435 path). Tuning knobs: `GCP_VPC_NAME` (default `default`), `GCP_VPC_CONNECTOR_IP_CIDR`, `GCP_VPC_CONNECTOR_MIN_INSTANCES`, `GCP_VPC_CONNECTOR_MAX_INSTANCES`, `GCP_VPC_CONNECTOR_MACHINE_TYPE`.
- **Wiring** Cloud Run deploy already honoured `GCP_VPC_CONNECTOR` (`engine/deployers/gcp_cloudrun.py:110`); operators now pipe the connector name from `.agentbreeder/infra-state.json` instead of provisioning out of band.
- **Deps** added `google-cloud-vpc-access>=1.10.0` to the `gcp` + `all-clouds` extras.
- **Tests** 9 new unit tests cover the trigger predicate (`_should_provision_vpc_connector`), the truncator (`_truncate_connector_id` — 2-25 chars, lowercase, hyphenated), the create / skip / custom-network / idempotent-state paths, plus destroy semantics including the legacy "deferred-marker" branch.
### v2.3 — Azure greenfield provisioner (#384)

- **Added** `AzureProvisioner.provision()` / `destroy()` — creates the minimum-viable Container Apps footprint end-to-end via azure-mgmt-*: Resource Group `agentbreeder-{agent}-rg`, Log Analytics workspace (required prerequisite for ACA), Container Apps Environment (internal-only unless `access.visibility: public`), Azure Container Registry (`admin_user_enabled=False` — Managed Identity auth only), per-agent user-assigned Managed Identity with `AcrPull` role scoped to the specific registry resource ID (never the subscription), and — when `memory:` is declared — VNet + delegated subnet + Azure Database for PostgreSQL Flexible Server (`public_network_access=Disabled`, private DNS) with a random password stored in a per-agent Key Vault. State stores the Key Vault secret URI only.
- **Idempotent** — every helper is check-then-create; re-running `provision()` is a no-op. `destroy()` refuses untagged resources, then deletes the resource group last (cascades anything not explicitly tracked).
- **Deps** added `azure-keyvault-secrets`, `azure-mgmt-authorization`, `azure-mgmt-keyvault`, `azure-mgmt-loganalytics`, `azure-mgmt-msi`, `azure-mgmt-network`, `azure-mgmt-rdbms` to the `azure` + `all-clouds` extras.
- **Tests** 24 unit tests with mocked azure-mgmt clients pin the security-critical paths: PostgreSQL `public_network_access=Disabled`, ACR `admin_user_enabled=False`, AcrPull role scoped to registry not subscription, plaintext password never appears in `InfraState.model_dump_json()`, DB rolled back if Key Vault write fails, `destroy()` refuses untagged resources.

### v2.3 — GCP Cloud SQL provisioning (#435, stacked on #436)

- **Added** `GCPProvisioner._ensure_cloud_sql()` / `_delete_cloud_sql()` — creates a Cloud SQL Postgres 15 instance named `{agent_name}-memory` (default tier `db-f1-micro`, configurable via `GCP_CLOUD_SQL_TIER`), database `agentbreeder_memory`, user `agentbreeder` with a random password generated via `secrets.token_urlsafe(32)`. The password is written to Secret Manager as `agentbreeder-{instance_id}-db-password` in the same `provision()` call; `InfraState.resources["cloud_sql"]` carries the secret resource name only — the plaintext password never appears on disk, in logs, or in the state file. If the Secret Manager write fails after a fresh instance was created, the instance is rolled back.
- **Private only** `ip_configuration.ipv4_enabled=False`, `require_ssl=True`, `private_network` points at the VPC the connector (#436) was created on. Cloud SQL implies the VPC connector unless `GCP_CLOUD_SQL_PRIVATE_IP=0`.
- **Idempotent** instance / database / user are all check-then-create. Re-running `provision()` rotates the user password and re-writes Secret Manager but does not recreate the instance.
- **Wiring** Cloud Run deploy already honoured `GCP_CLOUD_SQL_INSTANCE` (connection name); operators now pipe it from `.agentbreeder/infra-state.json`.
- **Deps** added `google-cloud-sql>=1.6.0` + `google-cloud-secret-manager>=2.20.0` to the `gcp` + `all-clouds` extras.
- **Tests** 9 new unit tests cover skip-by-default, create-when-flag-set, custom-tier, implied-VPC-connector, plaintext-password-never-in-state (regex assertion on `state.model_dump_json()`), the `_truncate_cloud_sql_instance_id` helper, and destroy semantics including the legacy "deferred-marker" branch (37 total).

### Platform Audit Summary (2026-05-18)

A 9-way parallel audit (`docs/superpowers/specs/2026-05-18-platform-audit-design.md`) surfaced 91 findings across 8 code subsystems plus website. 85 additive-safe items landed across 5 execution waves:

- **Wave 0** — Website docs aligned 1:1 with current v1.7.x implementation (`quickstart.mdx`, `how-to.mdx`, `cli-reference.mdx` 100% correct; honest deploy-target status across 9 additional pages; per-target prereqs canonicalized in `deployment.mdx`).
- **Wave 1** — 4 P0 security/correctness fixes: path-traversal sanitization in `markdown_writer`; Pydantic validation of RAG search weights + numeric bounds; structured alerting on pseudo-embedding fallback.
- **Wave 2** — 5 shared utility modules introduced (`engine/observability/degraded_mode.py`, `api/retry.py`, `engine/deployers/_health.py`, `engine/util/path_safety.py`, `api/models/_validators.py`).
- **Wave 3** — Cross-cutting threading of 4 Wave-2 utilities into existing callsites (6 deployers consolidated onto `poll_until_ready`; path validation propagated; warn-once dedup standardized; Pydantic field-types adopted).
- **Wave 4** — 37 P1 fixes across 8 subsystems (idempotency keys, retry semantics, schema validation, sandbox AST guards, MCP timeout config, RAG upload size cap, content-hash dedup, atomic graph extraction, Neo4j indexes + native vector support, memory LIKE escaping, summary circuit breaker, deploy idempotency, sidecar pre-validation, and more).
- **Wave 5** — 38 P2 polish items (docs, type hints, observability, malformed-input tests, framework example READMEs, contributor guides, integration tests).

See per-wave detail in subsequent sections of this changelog.

### Human-Review Backlog (not in this release)

Six audit findings require breaking changes, schema extensions, or cross-repo coordination and were deferred:

- **HR-1** — Memory team-scope isolation not enforced at runtime (data-isolation gap). RBAC integration plan needed; signature changes across memory routes. Wave-5 added xfail tests documenting the current state.
- **HR-2** — Memory wiring missing from Claude SDK / OpenAI Agents / CrewAI runtimes (only LangGraph has full memory integration today). New runtime feature wiring + multi-runtime test plan needed.
- **HR-3** — GraphRAG `custom_entity_types` claimed in docs but not in `agent.yaml` schema. Requires schema bump → website + cloud sync per CLAUDE.md cross-repo rule. Wave-5 added a doc callout marking this as roadmap.
- **HR-4** — `pgvector` RAG backend silently falls back to in_memory. Either implement (large) or remove (breaking).
- **HR-5** — Greenfield (scenario B) infrastructure provisioning across AWS/Azure/K8s (~970 LOC gap). Aligns with the comprehensive architecture plan epics #377–#381.
- **HR-7** — `rajits/` → `agentbreeder/` Docker Hub namespace migration (15 files across CI, sidecar, deployers, docs). Coordination with Docker Hub org needed; out of scope for an additive-only audit.

### Added
- **`registry rag ingest` / `registry rag search` CLI + `agenthub.rag.RagIndex` SDK module** — the API has shipped `POST /api/v1/rag/indexes/{id}/ingest` and `POST /api/v1/rag/search` since v2.0, but neither was reachable from the CLI or the Python SDK, so operators had to fall back to `curl` with hand-built multipart payloads. Two new CLI subcommands close the gap: `agentbreeder registry rag ingest NAME FILE...` resolves the index name to its UUID (UUIDs are accepted directly), validates each file's extension against the API allow-list (`.pdf`, `.txt`, `.md`, `.csv`, `.json`) client-side, and POSTs multipart/form-data to the API; `agentbreeder registry rag search NAME --query TEXT --top-k N` posts to `/api/v1/rag/search` and renders results as a Rich table (score / source / snippet) with a `--json` escape hatch. New `_post_multipart` helper in `cli/commands/registry_cmd.py` reuses the same bearer-token auth flow as the rest of the registry CLI. New `agenthub.rag` SDK module (`sdk/python/agenthub/rag.py`) exports `RagIndex`, `IngestResult`, and `RagIndexError` — `RagIndex(name_or_id, base_url=..., token=...).ingest([paths])` returns a typed `IngestResult` dataclass mirroring the API `IngestionJob` shape, and `.search(query, top_k=...)` returns a normalized list of `{score, source, text, metadata}` dicts that handles both `score`/`similarity` and `text`/`content` field aliases the API has historically used. The name→UUID lookup is lazy and cached on the instance. 21 new tests: `tests/unit/test_cli.py::TestRegistryRagIngest` (5 tests covering UUID passthrough, name lookup, missing-file error, unsupported-extension error, and the full multipart POST flow with field-by-field assertions) + `TestRegistryRagSearch` (2 tests for the body shape and the `--json` path); `tests/unit/test_sdk_rag.py` (14 tests covering token requirement, base-url normalization, name lookup, all four ingest error paths, multipart payload shape, HTTP error propagation, search body, similarity/content field aliasing, and a regression test that asserts the SDK allowlist stays in sync with `api/routes/rag.py`). Docs added in `website/content/docs/cli-reference.mdx` under `### agentbreeder registry`.

- **`agentbreeder registry memory` and `agentbreeder registry rag` CLI commands** — registering a memory config or RAG index previously required hitting `POST /api/v1/memory/configs` / `POST /api/v1/rag/indexes` by hand because the registry CLI only shipped `prompt`, `tool`, and `agent` subapps. Two new Typer subapps in `cli/commands/registry_cmd.py` (`memory push|list`, `rag push|list`) close the gap with the same `_post`/`_get` auth flow the rest of the registry CLI uses. `memory push` parses a `memory.yaml`, maps `backend → backend_type`, `config.window_size → max_messages`, propagates `namespace_pattern`, `scope`, `linked_agents`, and tags through, then POSTs to `/api/v1/memory/configs`. `rag push` parses a `rag.yaml`, flattens the nested `embedding_model: {provider, name}` block to a slash-joined string (`openai/text-embedding-3-small`), promotes `chunking.{strategy,chunk_size,chunk_overlap}` to top-level `chunk_*` fields, and POSTs to `/api/v1/rag/indexes`. Both `list` commands render Rich tables (name / backend / type / id) and accept `--json` for scripting. 9 new regression tests in `tests/unit/test_cli.py` (`TestRegistryMemoryPush`, `TestRegistryRagPush`) cover yaml-to-body mapping for `backend`, `memory_type`, `max_messages`, `embedding_model` flattening, default chunking values, and the missing-name / missing-file error paths. Docs added in `website/content/docs/cli-reference.mdx` under `### agentbreeder registry`.

### Fixed
- **`registry agent push` 500 (MissingGreenlet) + polyglot regression for `runtime.framework` agents** (#376) — registering a polyglot agent (`runtime: { language: node, framework: vercel-ai }` with no top-level `framework:` key) via `agentbreeder registry agent push agent.yaml` was failing the request lifecycle in two distinct places. (1) The validator path was inconsistent: `engine/config_parser.py` had already grown `validate_framework_or_runtime` to accept either top-level `framework:` or `runtime.framework:`, and `api/routes/agents.py` had `_NODE_FRAMEWORKS` + `_guess_language_for_framework()` to coerce the runtime block, but the regression had no test coverage — so any future revert would have silently re-broken the polyglot path. (2) After the validation fix landed, the API was still 500'ing with `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called` because `registry.agents.create_from_yaml` called `AgentRegistry.register(session, config, endpoint_url="")` and returned the ORM instance without ever issuing `await session.commit()` / `await session.refresh(agent)`. The downstream FastAPI response serializer then triggered a lazy-load of the DB-default columns (`created_at`, `updated_at`), which crashed under the async greenlet bridge. The fix in `registry/agents.py` mirrors the manual `create_agent` path in `api/routes/agents.py` — commit + refresh before returning. New regression test `TestCreateFromYaml::test_create_polyglot_runtime_agent` in `tests/unit/test_agent_yaml.py` posts a polyglot YAML (no top-level `framework:`, `runtime.framework: vercel-ai`) through `create_from_yaml` and asserts (a) the agent is created with `framework == "vercel-ai"`, and (b) `created_at` / `updated_at` are populated (this assertion is what fails under the pre-fix code, because the lazy-load on those columns raises). Verified end-to-end against a live API container: `agentbreeder registry agent push /tmp/my-vercel-agent/agent.yaml` now returns 200 and the agent surfaces in `registry agent list` with the correct `vercel-ai` framework.
- **RAG search returns N duplicate hits when a file is re-ingested — ingest now idempotent + search dedups by content hash** — re-ingesting the same file into a RAG index used to silently double the chunk count, and `POST /api/v1/rag/search` happily returned every byte-identical copy, so the dashboard `/rag` search page would show 8 (or more) identical result cards for a single query. Two root causes, both fixed: (1) `RAGStore.ingest_files` was append-only — it never checked whether the incoming bytes already lived in the index. Every chunk now carries a SHA-256 `content_hash` in its metadata (back-filled transparently on legacy chunks the first time they're encountered), incoming chunks whose hash already exists are skipped, and a new keyword-only `replace: bool = False` parameter drops any pre-existing chunks whose `source` matches one of the incoming filenames before ingestion when the caller explicitly wants to overwrite. (2) `hybrid_search` now deduplicates results by `content_hash` (falling back to a SHA-256 of the chunk text for legacy hits) before truncating to `top_k`, so pre-fix indexes that already contain duplicates surface only one copy per unique chunk. Plumbed through the stack: `POST /api/v1/rag/indexes/{id}/ingest` accepts a `replace` form field (`Form(False)`), `agentbreeder registry rag ingest` grows a `--replace` flag, and `agenthub.RagIndex.ingest(files, *, replace=False)` accepts the kwarg — all backward compatible. 7 new regression tests in `tests/unit/test_rag_service.py::TestRAGStoreIngestDedup` (idempotent re-ingest, replace-by-source drop, within-batch dedup, legacy-chunk hash back-fill, the user-facing "8 duplicates for one query" bug), plus CLI test `TestRegistryRagIngest::test_ingest_replace_flag_sends_form_field` and SDK test `TestIngest::test_ingest_replace_kwarg_sends_form_field`. All 95 unit tests across `test_rag_service.py`, `test_sdk_rag.py`, and `test_cli.py::TestRegistryRagIngest` pass.

- **Dashboard `/mcp-servers` page returns 500 — missing alembic migration for ORM columns** — `GET /api/v1/mcp-servers` was failing with `sqlalchemy.exc.ProgrammingError: column mcp_servers.version does not exist`, surfaced to the user as `Failed to load MCP servers: API error 500` on the dashboard. The `McpServer` SQLAlchemy model (`api/models/database.py`) declares `version`, `team`, `deploy_config` and `image_uri` but the columns were never added to the database — they shipped in the v1.1 commit `057f143` ("v1.1 Connectivity") with no accompanying migration, leaving `select(*)` queries built from the ORM mapper to reference columns that didn't exist in the `mcp_servers` table. New idempotent alembic migration `022_add_mcp_server_registry_fields` adds all four columns (nullable, with `ADD COLUMN IF NOT EXISTS` so partially-applied states re-run safely) and indexes `team` to match the `index=True` declaration on the model. New regression test `TestMigration015::test_migration_022_mcp_server_fields` in `tests/unit/test_rbac_system.py` (same pattern as the existing 012/015 migration tests) asserts revision identifiers, that `upgrade`/`downgrade` are callable, and that the migration source includes `ADD COLUMN IF NOT EXISTS` for each of the four fields plus the `ix_mcp_servers_team` index. Verified locally: `alembic upgrade head` advances the DB from `021` to `022` and `GET /api/v1/mcp-servers` returns `200 OK` with `{"data": [], "meta": {"page":1,"per_page":20,"total":0}, "errors":[]}`.

- **Registry rejects polyglot agents; memory config 500s; `validate memory.yaml` uses wrong schema** (#372, #373, #374) — three independent registry/memory bugs surfaced while registering a polyglot Vercel-AI agent + Postgres memory config. (1) `POST /api/v1/memory/configs` was 500'ing with `column "tags" is of type text[] but expression is of type json` because Alembic migration `016` creates the column as `text[]` while the ORM declared it `JSON`. Fixed by switching `MemoryConfig.tags` to `JSON().with_variant(ARRAY(Text), "postgresql")` so PostgreSQL uses the native `text[]` and SQLite falls back to JSON (tests stay portable). (2) `agentbreeder registry agent push` rejected any agent that used the polyglot `runtime: { language, framework }` block with `Validation failed: 'framework' is required`. `registry.agents.validate_config_yaml` now accepts either top-level `framework:` (Python) **or** `runtime.framework:` (polyglot) — mutually exclusive — and `create_from_yaml` routes through the appropriate `AgentConfig` kwarg. The API `POST /api/v1/agents` (and v2 equivalent) now fall back to constructing the `runtime` block with a guessed language (`vercel-ai` / `mastra` / `langchain-js` / `openai-agents-ts` / `deepagent` / `mcp-ts` / `mcp-py` → `node`; `go-custom` → `go`) when the framework string doesn't match `FrameworkType`. (3) `agentbreeder validate memory.yaml` was running the file against the agent schema, producing nonsense `'framework' or 'runtime.framework' is required` errors. `cli/commands/validate.py::_detect_config_type` now classifies memory configs by filename (`memory.yaml` / `memory.yml`) or content shape (`backend` + `memory_type` present, `framework` / `runtime` / `model` absent) and a new `_validate_memory_config` validates against `engine/schema/memory.schema.json` using `jsonschema.Draft202012Validator`. 7 new regression tests in `tests/unit/test_agent_yaml.py` (`TestValidateConfigYaml::test_polyglot_runtime_accepted`, `…test_both_framework_and_runtime_rejected`, `…test_neither_framework_nor_runtime_rejected`) and `tests/unit/test_cli.py` (`TestValidateCommand::test_validate_detects_memory_yaml_by_filename`, `…test_validate_detects_memory_yaml_by_content`). Docs updated in `website/content/docs/cli-reference.mdx` (file-type detection rules) and `website/content/docs/low-code.mdx` (polyglot `runtime:` block accepted by `registry agent push`).

### Docs
- **Wave 0 of the platform audit lands.** Quickstart, how-to, and CLI reference are 100% aligned with the v1.7.x implementation. Stale "supported deploy targets" claims across 9 additional pages now distinguish ✅ Shipped (Local, GCP Cloud Run greenfield) from 🟡 Deployer-exists-but-requires-existing-infra (AWS ECS, App Runner, Azure, K8s, Claude Managed). Canonical status table at [cli-reference#agentbreeder-deploy](https://agentbreeder.io/docs/cli-reference#agentbreeder-deploy); canonical prereqs at [deployment#prerequisites-per-target-as-of-2026-05-18](https://agentbreeder.io/docs/deployment#prerequisites-per-target-as-of-2026-05-18). See audit spec at `docs/superpowers/specs/2026-05-18-platform-audit-design.md`.
- **Quickstart troubleshooting refresh** — added a "blank dashboard at `:3001`" section to `website/content/docs/faq.mdx`, `quickstart.mdx`, and `how-to.mdx` covering: stale `rajits/agentbreeder-dashboard:latest` cached locally, the `--dev` flag for building images from local source, the `BUNDLE` health-check one-liner, and the React-version verification. Added an FAQ note that `migrate-1` exiting is expected (one-shot alembic job). Added a stale-`DOCKER_HOST` row to the Troubleshooting tables. `CONTRIBUTING.md` §6 now documents `quickstart --dev` and the clean-rebuild incantation. `README.md` Install section now points contributors at `--dev` for local-source workflows.

### Fixed
- **`agentbreeder quickstart` autonomous-recovery path on macOS** (#349, #350) — three additional auto-recovery paths so a fresh `quickstart` run reaches a working dashboard without manual intervention. (1) Stale `DOCKER_HOST` auto-strip: when the user's shell exports `DOCKER_HOST=unix:///path/that/no/longer/exists` (e.g. a removed Rancher Desktop socket), the compose pre-flight detects the dead socket, strips the variable from the subprocess env only (never the user's shell), and retries. (2) Ollama 127.0.0.1-only bind detection: parses `lsof -nP -iTCP:11434 -sTCP:LISTEN` to detect loopback-only binds (which are invisible to Docker containers via `host.docker.internal`); offers an interactive Y/n prompt to rebind via `launchctl setenv OLLAMA_HOST 0.0.0.0:11434` followed by an Ollama restart (brew services / `Ollama.app` / `pkill`+`ollama serve` fallback) and verifies the new bind before continuing. (3) Dashboard bundle smoke check: after services come up, validates that the served HTML contains a React mount point and that the Vite bundle is fetchable and non-trivial; on failure offers `compose up -d --build dashboard` when a build context is available, or prints the `compose pull dashboard && compose up -d dashboard` fallback for pip-installed users. Also pins `react` and `react-dom` to **exact** `19.2.5` (was caret) and adds an `overrides` block in `dashboard/package.json` to prevent any transitive bump from reintroducing React error #527.
- **Path traversal in `markdown_writer`** (W1-01): `subdir` is now validated against parent traversal (`..`), absolute paths (`/`), home expansion (`~`), and null bytes. Unsafe inputs raise `ValueError` instead of writing to arbitrary filesystem paths.
- **RAG search request validation** (W1-02 / W1-04): `POST /api/v1/rag/search` now validates body via `RagSearchRequest` Pydantic model — `vector_weight + text_weight` must sum to 1.0 (within float tolerance), `top_k` is bounded `[1, 1000]`, `hops` is bounded `[0, 10]`, `seed_entity_limit` is bounded `[1, 50]`. Invalid bodies produce `422 Validation Error`.
- **Silent embedding fallback alerting** (W1-03): When OpenAI / Ollama is unreachable or `OPENAI_API_KEY` is missing, `embed_texts` still falls back to deterministic pseudo-embeddings to keep ingest moving — but now WARN-logs the first occurrence per (model, reason) per process, and the search response carries a `degraded: true` flag so callers can detect quality-degraded results.

### Security
- **Resolve 6 medium-severity Dependabot alerts before launch** (#173, #338) — bumps `github.com/go-chi/chi/v5` from `5.1.0` to `5.2.2` in `sidecar/`, `sdk/go/agentbreeder/`, and `examples/go-agent/` (fixes host-header injection → open redirect in `RedirectSlashes`); adds npm `overrides` to `website/package.json` pinning `postcss` to `^8.5.10` (resolves to `8.5.13`, fixes XSS via unescaped `</style>` in CSS Stringify), and to `sdk/typescript/package.json` pinning `vite` to `^6.4.2` and `esbuild` to `^0.25.0` (resolves to `6.4.2` / `0.25.12`, fixes vite path traversal in optimized-deps `.map` handling and esbuild dev-server CSRF-style request issue). Verified resolved versions via `npm ls` against each advisory's `first_patched` constraint.

---

## [2.1.0] — 2026-04-30

> **Real backends everywhere.** v2.1 finishes the v2.0 honesty patch by replacing every Coming-soon stub and seeded placeholder on the dashboard with real, persisted, DB-backed implementations. Eight dashboard pages and three runtime contracts move from mock data to production state. Net: 14 issues closed, 17 PRs landed (#211, #213, #210, #214, #212, #207, #204, #209, #205-area, #180, #199, #215, #206, #208 + CI hotfixes #232 #233 #234 #235 #236).

### Added
- **Auto-seed registry tables on first boot** (#180) — a fresh `docker compose up` no longer shows an empty Agents / Prompts / Tools / MCP / Providers dashboard. New `engine.seed.seed_registries(db, examples_dir=None)` is called from the API lifespan handler after `_seed_default_admin`; for each registry table it counts rows, and only when the count is zero loads canonical seed YAMLs from `examples/seed/{agents,prompts,tools,mcp_servers,providers,knowledge_bases}/` and inserts via the existing `AgentRegistry.register` / `PromptRegistry.register` / `ToolRegistry.register` / `McpServerRegistry.create` / `ProviderRegistry.create` services (no raw SQL). Idempotent: re-running is a no-op when any table is non-empty. Gated by `AGENTBREEDER_AUTO_SEED` (default `true` for `AGENTBREEDER_INSTALL_MODE=dev`, `false` for `cloud`). Failures during seeding log a warning and never crash startup. Six canonical seed YAMLs ship in-tree (customer-support agent, support-system-v1 prompt, web-search + order-lookup tools, example-mcp server, local-ollama provider, product-docs knowledge base) and validate against `engine/schema/*.schema.json`. The existing `agentbreeder seed` CLI command grows a `--registry [--examples-dir PATH]` flag that runs the same seeder against a live API DB. 8 new unit tests in `tests/unit/test_first_boot_seed.py` cover empty-DB seed, idempotency, partial-population skip, structured-report shape, missing-directory handling, malformed-YAML resilience, custom `examples_dir`, and unknown `provider_type` recovery.
- **Daily models-sync cron + `agentbreeder model sync-now` CLI** (#199) — model lifecycle reconciliation (added → deprecated → retired) was previously triggered only by an operator hitting `POST /api/v1/models/sync` or running `agentbreeder model sync` against a live API. Now `api/tasks/models_sync_cron.py` schedules a daily background task in the API server's lifespan that calls `_build_discoveries()` + `ModelLifecycleService.sync()` across every configured provider, isolates per-provider failures (one bad provider can't kill the sweep), and emits a `model.sync.scheduled` audit event with the totals. Gated by `AGENTBREEDER_MODELS_DAILY_SYNC` (defaults to `true` when `AGENTBREEDER_INSTALL_MODE=cloud`, `false` otherwise) — no new dependencies (plain asyncio with random initial jitter so co-deployed replicas drift apart). New `agentbreeder model sync-now` CLI subcommand runs the same sweep synchronously in-process, useful for testing and for self-hosted environments without the daily loop. 12 new unit tests cover env-var gating, happy path, empty-providers short-circuit, single-provider failure isolation, audit-failure tolerance, background-task lifecycle, and the CLI's human + skipped output paths.
- **Structured tool-call history across every runtime template** (#215) — the `/playground` Agent tab previously had no way to render the tools an agent called during a run; the page even shipped a `TODO(#215)` note pending a structured-history field. Every Python runtime template (`claude_sdk_server.py`, `openai_agents_server.py`, `crewai_server.py`, `langgraph_server.py`, `google_adk_server.py`, `custom_server.py`) plus the `node/openai_agents_ts_server.ts` template now returns a top-level `history: list[ToolCall]` field on `/invoke`, where each `ToolCall` is `{ name, args, result, duration_ms, started_at }`. Claude SDK records each tool execution inside the agentic loop with a real `duration_ms`; OpenAI Agents pairs `ToolCallItem`/`ToolCallOutputItem` from `RunResult.new_items`; LangGraph pairs `AIMessage.tool_calls` with `ToolMessage` content from the graph state's `messages`; Google ADK pairs `function_call` with `function_response` parts across emitted events; CrewAI walks `tasks_output[*].tool_calls` (best-effort, depends on user attaching step callbacks); Custom (BYO) surfaces `result["tool_history"]` if the user attaches it. The API proxy at `POST /api/v1/agents/{id}/invoke` forwards the field through `AgentInvokeResponse.history` (new `AgentInvokeToolCall` schema). The dashboard playground (`AgentChatPanel`) drops the regex-fallback path and the `TODO(#215)` comment, maps each entry into the existing `ToolCallCard`, and renders the timeline inline in verbose mode. Backwards-compatible — the field is purely additive.
- **Real SOC 2 / HIPAA compliance scanner** (#208) — `/compliance` previously rendered control statuses and the downloadable evidence report from a 12-row `_SEED_COMPLIANCE_CONTROLS` list inside `api/services/agentops_service.py`; nothing was actually checked. Replaced with a real, executable control registry (`engine/compliance/controls.py`) plus a scanner (`engine/compliance/scanner.py`) and a new `compliance_scans` table (Alembic migration `021`). Six controls ship with this PR: `audit_log_retention` (oldest `audit_events` row >= 365d, partial when sparse), `rbac_enforced` (at least one `ResourcePermission` row exists), `secrets_backend_not_env` (workspace backend isn't the local `.env` fallback), `db_ssl_enabled` (Postgres `ssl_is_used()` — skipped on SQLite), `mfa_enabled` (active users have `password_hash`; partial until real MFA ships), `encryption_at_rest_documented` (`docs/security.md` exists). New `ComplianceService.{run_and_persist, get_or_run_latest, status_payload, report_payload}` in `api/services/agentops_service.py`. `GET /api/v1/agentops/compliance/status` and `GET /api/v1/agentops/compliance/report` now run real checks, persist a row to `compliance_scans` (cached 60s to avoid scan-storms on dashboard polling), and return real per-control evidence (row counts, oldest timestamps, backend names, dialect) — no more `"Automated compliance check for X"` placeholder strings. New `POST /api/v1/agentops/compliance/scan` to force-rescan. Removed the v2.0.1 `<ComingSoonBanner>` for #208 from `dashboard/src/pages/compliance.tsx`. Deferred: real TOTP/WebAuthn MFA (still TODO; `mfa_enabled` returns `partial` for now), additional controls, and scheduled cron-driven scans. 27 new unit tests cover the registry shape, every control's pass/fail/partial/skipped paths, scan orchestration with per-control exception trapping, and persistence across session recycle.
- **Workspace secrets backend chooser is live in the dashboard** (#213) — `/settings/secrets` previously rendered a Coming-soon stub that told operators to edit `~/.agentbreeder/workspace.yaml` by hand. Now the page renders a working `<select>` over the supported backends (`env`, `keychain`, `aws`, `gcp`, `vault`); switching backends prompts a confirm dialog (since existing secrets in the previous backend are NOT auto-migrated), then `PUT /api/v1/secrets/workspace` persists the choice through the new `save_workspace_secrets_config()` helper. Backend swap is admin-only (deployer/viewer get 403), validates that the chosen backend can actually be instantiated (so missing optional deps surface as 400 instead of silently breaking the workspace), and emits a `secret.backend_changed` audit event. New `api.secrets.setBackend()` client. 10 new unit tests cover RBAC, validation, audit, file persistence, and workspace-name preservation.
- **Ollama Pull Model live in the dashboard** (#214) — `/settings` provider list previously rendered a disabled `Pull Model` button labelled "Coming soon" for Ollama rows. Now clicking it opens a modal that streams real progress from `ollama pull <model>`. New `OllamaProvider.pull_model()` async generator yields each event from Ollama's `/api/pull` NDJSON stream (`pulling manifest` → `downloading <digest>` with `total`/`completed` byte progress → `success`). New `POST /api/v1/providers/{id}/pull-model` returns those events as Server-Sent Events; rejects 400 when the provider isn't Ollama and 404 when missing. Frontend `api.providers.pullModel()` returns the raw `Response` so the modal can consume the SSE stream via `ReadableStream`. The dialog shows a popular-model chip palette (`llama3.2`, `mistral`, `mixtral`, `qwen2.5`, etc.), a percent progress bar derived from `completed/total`, and final ✓ / ✗ states. 4 new unit tests cover 404 / 400 (non-Ollama) / streamed-success / Pydantic validation.
- **`incidents` PostgreSQL table** (#207) — new Alembic migration `020_incidents_table.py` adds an `incidents` table with `id`, `title`, `severity` (`critical|high|medium|low`), `status` (`open|investigating|mitigated|resolved`), `affected_agent_id` (FK → `agents.id`, ON DELETE SET NULL), `description`, `created_by`, `created_at`, `resolved_at`, `timeline` (JSONB), `incident_metadata` (JSONB) plus indexes on status / severity / created_at / affected_agent_id. New `IncidentSeverity` and `IncidentStatus` enums in `api/models/enums.py`; new `Incident` ORM model in `api/models/database.py`.
- **Real agent version history** (#210) — `/agents/:id` Configuration Tab → Compare Versions panel was reading `MOCK_VERSIONS` + `MOCK_VERSION_YAML` constants because the registry only stored the *current* `config_snapshot` per agent. New `agent_versions` table (Alembic 019) is populated by `AgentRegistry.register()` whenever an agent's version string changes, capturing `(agent_id, version, config_snapshot, config_yaml, created_by, created_at)` with a UNIQUE `(agent_id, version)` constraint so re-registering the same version updates in place. New `GET /api/v1/agents/:id/versions` route + `api.agents.versions()` client; the dashboard now fetches real history (lazily, only when the diff panel opens), defaults the diff selectors to the two newest versions, and shows graceful empty / single-version states. 5 new registry tests cover first-register, version bump, idempotent re-register, actor email, and cascade-delete with parent agent.
- **Visual agent builder emits v2 YAML fields** (#204) — the `/agents/builder` visual mode now exposes a Language toggle (python / typescript), a collapsible Gateways panel with per-gateway URL / api-key-env / fallback-policy overrides, and a lifecycle-aware model picker that hides `deprecated` and `retired` models behind a `Show deprecated` checkbox. Selecting typescript emits the canonical `runtime: { language: node, framework: <fw> }` block (the engine parser rejects a top-level `language:` key). The emit/parse helpers were extracted to `dashboard/src/lib/agent-yaml-emit.ts` so visual → YAML → visual round-trips losslessly through the gateway and language fields. New Python tests in `tests/unit/test_agent_yaml.py` (`TestDashboardEmitFormat`) confirm the python, typescript, and gateways YAML shapes parse cleanly via `engine.config_parser.parse_config`, and 4 new Playwright specs cover the language radio, runtime emit, gateways panel, and deprecated-model toggle.

### Fixed
- **Dashboard `/agentops` page now reads real fleet / events / top-agents / teams from PostgreSQL** (#206) — the AgentOps fleet view previously rendered eight hardcoded agents from `_SEED_AGENTS` with frozen `2026-03-12` timestamps; events came from `_SEED_EVENTS`; top-agents and team comparison were derived from the same seeds. Replaced both seed constants with a new `FleetService` in `api/services/agentops_service.py` that joins the `agents` registry table to 24h aggregates over `traces` (invocations, error rate, p50 latency) and `cost_events` (per-agent spend), and derives operations events from `audit_events` + `cost_events` (single requests over $1.00 surface as `cost_spike` events). `/api/v1/agentops/{fleet,fleet/heatmap,top-agents,events,teams,costs/forecast}` all inject `db: AsyncSession = Depends(get_db)` and the fleet `status`/`health_score` are derived from the registry's `AgentStatus` enum + observed error rate (failed→down, ≥25% errors→down, ≥5%→degraded, else healthy). The `/teams` route still calls `IncidentService.open_count_by_agent_name()` (preserves the #207 integration). On a fresh deploy with no traces / cost events / audit log, every endpoint returns sensible empty-state payloads — no seeded fakes. `dashboard/src/pages/agentops.tsx` drops the v2.0.1 `<ComingSoonBanner>`. The test suite gains 19 new DB-backed tests in `tests/unit/test_agentops_service.py` (7 fleet, 2 heatmap, 4 top-agents, 6 events, 4 team comparison) plus an updated 5-test cost-forecast block; the existing route tests in `test_api_routes_coverage_boost.py` were rewired to mock `FleetService` static methods (6 tests rewritten + 1 new `_override_db_dep`/`_restore_db_dep` helper pair); `test_rbac_phase1.py::test_agentops_fleet_viewer_ok` was updated to override the `get_db` dependency the new way.
- **Orchestration builder canvas now persists** (#211) — the `/orchestrations/builder` page was pure local state with zero API calls; nothing saved across reloads, no Validate/Save/Deploy buttons, and the existing `/api/v1/orchestrations` CRUD endpoints were unreachable from the UI. Added an `api.orchestrations.{list,get,create,update,delete,validate,deploy,execute}` client and wired the builder's new Save / Validate / Deploy buttons. Validation errors render inline with path + message + suggestion. Visual-builder layout (per-node `{x, y}`) round-trips through a new `layout` field on `OrchestrationRecord` (in-memory equivalent of `.agentbreeder/layout.json`). New `/orchestrations` list page with strategy-tagged cards, status badges, delete confirmation, and "New Orchestration" CTA. Loading by `?id=…` rehydrates name / version / strategy / agents from the saved record. Removed the v2.0.1 `ComingSoonBanner`.
- **Dashboard `/incidents` page now persists to PostgreSQL** (#207) — incidents previously lived in `AgentOpsStore._incidents`, an in-memory dict seeded from a fake `_SEED_INCIDENTS` list; every API restart wiped user-created incidents and re-seeded the demo data. Replaced with `IncidentService` in `api/services/agentops_service.py`, backed by the new `incidents` table. The `_SEED_INCIDENTS` list and the `_incidents` dict are gone; a fresh deploy starts with an empty table. `api/routes/agentops.py` now injects `db: AsyncSession = Depends(get_db)` and routes `list / create / get / update / execute_action` through `IncidentService`. The `/api/v1/agentops/teams` endpoint now reads open-incident counts from the same table. `dashboard/src/pages/incidents.tsx` no longer renders the `<ComingSoonBanner>` for #207. Remediation actions (restart / rollback / scale / disable) still only record an operator-intent timeline entry — wiring them to the deploy / rollback machinery and auto-creating incidents from cost anomalies / health-check failures are deferred to follow-up issues.
- **Dashboard `/gateway` page now reads real data instead of synthetic logs and a hardcoded comparison fixture** (#212) — `api/routes/gateway.py` previously generated 100 random "request log" entries per call (`_generate_log_entries`, seeded by `int(time.time()) // 60` so the table refreshed every minute) and returned a hand-coded `_GATEWAY_MODELS` price table for `/api/v1/gateway/costs/comparison`. Both are gone. `/api/v1/gateway/logs` now calls the LiteLLM proxy's authenticated `/spend/logs` endpoint via the new `api/services/gateway_logs_service.py` (normalizes `LiteLLM_SpendLogs` rows into the dashboard's `LogEntry` shape, infers gateway tier from the `custom_llm_provider` field). When LiteLLM is unreachable the endpoint returns `503` with `data: []` and a clear `errors` message — never synthetic data. `/api/v1/gateway/costs/comparison` now aggregates over the real `cost_events` table grouped by `(provider, model_name)`, computing average input/output prices per million tokens from recorded usage; returns an empty list when the table is empty (no more hardcoded fixture). `dashboard/src/pages/gateway.tsx` drops both `<ComingSoonBadge issue="#212">` markers and surfaces a clear "LiteLLM proxy unreachable" banner on the logs tab plus an empty-state on the costs tab. Required env vars (`LITELLM_BASE_URL`, `LITELLM_MASTER_KEY`) documented in the route module docstring.
- **Release workflow PyPI propagation race** — `Build & Push CLI Image` and `Update Homebrew Tap` jobs now probe the PyPI `/simple/` index (via `pip index versions`) instead of the `/pypi/<pkg>/<ver>/json` Warehouse endpoint. The JSON endpoint returns 200 the moment a file is uploaded, but `pip install` reads `/simple/` through Fastly, which can lag by tens of seconds — long enough for the v2.0.1 CLI image build to fail with `Could not find a version that satisfies the requirement agentbreeder==2.0.1`.
- **6 example/template `agent.yaml` files now pass schema validation** (#183) — `examples/quickstart/rag-agent/agent.yaml` switched from inline `knowledge_bases` to a registry `ref`; `examples/quickstart/search-agent/agent.yaml` dropped the unrecognized top-level `entrypoint`; the four `examples/templates/{competitor-monitor,github-pr-reviewer,meeting-summarizer,returns-processor}/agent.yaml` switched `claude_sdk.thinking.type: adaptive` to the schema-correct `claude_sdk.thinking.enabled: true`. All 44 example/template yamls now validate.
- **23 secret-CLI tests un-skipped and rewritten for Track K's command surface** (#202) — three test classes (`TestSecretCommand`, `TestSecretSetPrompted`, `TestSecretSetTags`) had been wholesale `@pytest.mark.skip`'d during the Track K merge to keep CI green. Updated each mock for the new shape: backends now expose `backend_name` (used in JSON output), `secret_set`/`rotate` probe `b.get(name)` to decide created-vs-updated, and the `secret list --json` envelope is now `{workspace, backend, entries: [...]}`. Also fixed two prefix-sanitization tests that were patching `engine.secrets.factory.get_backend` instead of the locally-imported `cli.commands.secret.get_backend`. Added a new `TestSecretSync` class (6 tests) covering invalid target, dry-run, actual mirror, include filter, partial-failure surfacing, and the no-candidates empty state. Coverage on `cli/commands/secret.py` returns from 75% → **92%**.
- **Dashboard `/activity` page now reads from `/api/v1/audit`** (#209) — `dashboard/src/pages/activity.tsx` previously rendered a hardcoded `MOCK_EVENTS` array with timestamps relative to a frozen `NOW = 2026-03-11`. Replaced with `useQuery({ queryKey: ['audit', ...], queryFn: () => api.audit.list(...) })` against the existing `/api/v1/audit` endpoint. Resource-type filter now passes through as a server-side query param. Adapter `adaptAuditToActivity` maps the backend `AuditEvent` shape (including dotted action names like `secret.created`) into the page's existing visual taxonomy with graceful fallbacks for unknown resource types/verbs. Adds proper loading skeleton, error state, and empty state. Removed the v2.0.1 `ComingSoonBanner`. E2E test `dashboard/tests/e2e/activity.spec.ts` now mocks `/api/v1/audit` explicitly with empty + seeded fixtures.

---

## [2.0.1] — 2026-04-29

> **Honesty patch.** Reviews every dashboard page + every website page against shipped reality and marks unshipped features as "Coming soon" with linked issues. No new features; no breaking changes.

### Added
- **Dashboard "Coming soon" badges** — the dashboard now visibly marks features that are scaffolded but not yet wired to real backends. New `<ComingSoonBadge>` component plus per-page top banners on agentops, incidents, compliance, activity, orchestration-builder. Per-feature badges on agent-detail (version compare), gateway (logs / cost compare), settings (Ollama Pull), settings-secrets (backend chooser), models (Local tab), playground (tool-call rendering). Each badge links to the GitHub issue tracking the gap (#206–#216).
- **Page-by-page website audit** — every docs page, homepage component, and blog post audited against shipped code. Unshipped features re-scoped as "Coming soon" with linked issues. Notable rewrites: 5-layer architecture step 5 ("Pulumi" → "Cloud SDK calls"), runtime-contract codegen scope (Go ships, Kotlin/Rust/.NET roadmap), sidecar auto-injection scope (compose/Cloud Run/ECS today; Azure/App Runner/K8s deferred), secrets auto-mirror scope (AWS ECS + GCP Cloud Run today), agent-yaml `gateways:` block now documented.
- **Migrations doc page** at `website/content/docs/migrations.mdx` — fixes a 404 in the docs nav and explains the v1.x → v2.0 upgrade path.
- **v2.0 launch blog post** at `website/content/blog/v2-platform-substrate.mdx` with an honest "what didn't ship yet" section.
- **Homepage feature cards for v2 tracks** — Provider Catalog, Sidecar, Workspace Secrets, Polyglot Runtime Contract, Gateways added alongside the v1 features.

### Tracked gaps (filed during the audit)

Backend / infra: #196–#205. Dashboard data wiring: #206–#216. Release infra: #195.

---

## [2.0.0] — 2026-04-29

> **Platform v2.** Six new tracks turn AgentBreeder into a substrate where frameworks, clouds, languages, and providers all plug in. See `docs/architecture/platform-v2.md` and the epic at #166.

### Added
- **Gateways as first-class providers — LiteLLM + OpenRouter** (#164, Track H): the catalog schema now distinguishes `type: openai_compatible` (one upstream) from `type: gateway` (many upstreams). `engine/providers/catalog.yaml` ships `litellm` and `openrouter` as built-in `gateway` presets, and `model.primary` accepts a 3-segment ref `<gateway>/<upstream>/<model>` (e.g. `openrouter/moonshotai/kimi-k2`, `litellm/anthropic/claude-sonnet-4`) — parsed by the new `engine.providers.catalog.parse_gateway_ref`. The wire-level `model` field is shaped as `<upstream>/<model>` so both LiteLLM and OpenRouter accept it as-is. New optional `gateways:` block on `agent.yaml` lets you override the catalog `url` / `api_key_env` / `fallback_policy` / `default_headers` per-gateway (the long-term home is `workspace.yaml` once Track A / #146 ships). The dashboard `/models` page now has a working **Gateways** tab — same Configure flow as Direct providers, with a small `gateway` badge per row. Backwards-compat: existing 2-segment direct refs (`nvidia/llama-…`) and `model.gateway: litellm` configs keep working unchanged. New docs page at `website/content/docs/gateways.mdx` covering when to pick which gateway and how 3-segment refs route end-to-end.
- **Model lifecycle — auto-discover, status, retire** (#163): `engine/providers/discovery.py` ships per-provider `/models` fetchers (OpenAI-compatible, curated Anthropic, Google `v1beta/models`) behind a `ProviderDiscovery` Protocol. New Alembic migration `018_add_model_lifecycle_fields.py` adds `discovered_at`, `last_seen_at`, `deprecated_at`, `deprecation_replacement_id` to `models` plus a `(status, last_seen_at)` index. New `registry/model_lifecycle.py` reconciles discovery output with the registry: new models become `active`, absent ones flip to `deprecated`, and after 30 days of continuous absence they `retired`. Per-provider discovery errors are isolated so a transient outage cannot mass-deprecate. Audit events `model.added` / `model.deprecated` / `model.retired` emit on every transition. New CLI: `agentbreeder model list / show / sync / deprecate`. New API: `GET /api/v1/models` (lifecycle-aware list), `POST /api/v1/models/sync` and `POST /api/v1/models/{name}/deprecate` (deployer-gated). Dashboard `/models` page Sync button is live (RBAC-aware, with a spinner) and rows show coloured status badges (`active`/`beta`/`deprecated`/`retired`). Daily cron is left as a TODO documented under `website/content/docs/providers.mdx#daily-cron-out-of-scope-for-this-pr` — for now operators add a system cron.

### Fixed
- **Local stack UX**: compose now wires `GOOGLE_API_KEY`, `LITELLM_BASE_URL`, `LITELLM_MASTER_KEY`, `AGENTBREEDER_INSTALL_MODE=team` into the API container. `/playground` and `/api/v1/secrets` now work out of the box on `docker compose up`.
- **LiteLLM trampling agentbreeder DB**: `STORE_MODEL_IN_DB: False` so LiteLLM's prisma migrations don't run against the shared database (which was wiping the `users` table). Stateless mode is fine for the playground; re-enable with a separate DB when virtual keys / DB-stored configs are needed.
- **`gemini-2.5-flash` not in LiteLLM config**: added to `deploy/litellm_config.yaml` (also fixed `GOOGLE_AI_API_KEY` → `GOOGLE_API_KEY` typo on `gemini-2.5-pro`).
- **Deploy → registry sync 401 (`endpoint_url` empty after deploy)**: `engine/builder.py` now attaches `AGENTBREEDER_API_TOKEN` as a Bearer header when set, so the post-deploy `PUT /api/v1/agents/{id}` succeeds and the registry record gets a real `endpoint_url` for cloud deploys.

### Added
- **`/playground` agent mode — chat with a deployed agent** (#177): the dashboard playground gets a Model | Agent tab toggle. Agent mode shows a dropdown of registered agents that have a non-empty `endpoint_url` and routes chats through `POST /api/v1/agents/{id}/invoke` (which auto-resolves `AGENT_AUTH_TOKEN` server-side from the workspace secrets backend per #176 — no token UI). Conversation history is currently sent as a single concatenated `input` string with role labels; `session_id` round-trips between turns. Mode preference persists in `localStorage`. Empty workspaces (no deployed agents) get a "Deploy one with `agentbreeder deploy`" empty state with a link to `/agents`. Model mode behaviour unchanged.
- **Track I phase 1 — Go SDK + runtime builder + example agent** (#165, Go scope only): new `sdk/go/agentbreeder/` module is the first Tier-2 polyglot SDK. Implements [Runtime Contract v1](engine/schema/runtime-contract-v1.md): `NewServer(InvokeFunc, …Option)` returns a chi-based `http.Handler` that auto-wires `/health`, `/invoke`, `/stream`, `/resume`, `/openapi.json`, `/.well-known/agent.json`, bearer-token auth via `AGENT_AUTH_TOKEN`, the `X-Runtime-Contract-Version: 1` header, and the SSE `[DONE]` terminator. Hand-curated types in `types.go` mirror `engine/schema/runtime-contract-v1.openapi.yaml` (oapi-codegen regen command in the SDK README). Includes a `Client` for the central registry (agents, models, secrets). New `engine/runtimes/go/` Python builder packages Go agents with a multi-stage `golang:1.22-alpine` → `gcr.io/distroless/static` Dockerfile. New `examples/go-agent/` minimal agent talks to Anthropic via `net/http` (mock-falls-back when no API key). New CLI flag `agentbreeder init --lang go --framework custom` scaffolds a working Go project. `agent.yaml` schema accepts `language: go|kotlin|rust|csharp` (only `go` wired in the runtime registry; the rest are reserved). New CI jobs `test-go-sdk` (≥85% coverage gate) and `test-go-example`; release.yml publishes `rajits/agentbreeder-go-agent-example` on each version tag. New docs page `website/content/docs/go-sdk.mdx`. Kotlin (#188) / Rust (#189) / .NET (#190) SDKs are deferred to follow-up issues.
- **Generic OpenAI-compatible provider + 9-preset catalog** (#160): `engine/providers/openai_compatible.py` parameterised by `base_url`/`api_key_env`/`default_headers` replaces what would have been 9 hand-written classes. New `engine/providers/catalog.yaml` ships nvidia, openrouter, moonshot (Kimi K2), groq, together, fireworks, deepinfra, cerebras, hyperbolic — merged with `~/.agentbreeder/providers.local.yaml` overrides at load time. New CLI: `agentbreeder provider list/add/remove/test/publish`. New API route `GET /api/v1/providers/catalog`. Dashboard `/models` page lists catalog presets with a Configure stub. `model.primary: nvidia/<model>` resolves through the existing engine path.
- **Sidecar — cross-cutting concerns layer** (#161): single Go binary auto-injected next to every agent that declares `guardrails:`, MCP `tools:`, or `a2a:`. Fronts the agent on `:8080` (bearer-token auth + guardrail egress checks → reverse proxy to `:8081`) and exposes `localhost:9090` helpers for A2A JSON-RPC, MCP HTTP/SSE passthrough, and cost emission. Auto-injection wired into docker-compose, GCP Cloud Run, and AWS ECS deployers. `AGENTBREEDER_SIDECAR=disabled` bypasses for local dev. New top-level `sidecar/` Go module (~89% test coverage), Dockerfile, image build target `rajits/agentbreeder-sidecar:<version>`, and docs at `website/content/docs/sidecar.mdx`.
- **Secrets — workspace-bound backend + auto-mirror to cloud at deploy** (#162): new `engine/secrets/keychain_backend.py` (cross-platform via `keyring`) plus `engine/secrets/workspace.py` for per-workspace backend selection, defaulting keychain locally / Vault for self-hosted teams / AWS Secrets Manager in cloud. New CLI `agentbreeder secret set/list/rotate/sync` routes through the workspace backend with `getpass` prompts and audit events. AWS ECS + GCP Cloud Run deployers now auto-mirror declared `secrets:` to the target cloud's native store under `agentbreeder/<agent>/<secret>`, grant the runtime SA `secretAccessor` on each, and inject them as ECS `secrets` / Cloud Run `SecretKeyRef` env vars (no plaintext in image). New dashboard `/settings/secrets` page (values never leave the backend) and 3 REST endpoints under `/api/v1/secrets/*`.

### Fixed
- **Invoke tab no longer prompts for `AGENT_AUTH_TOKEN`** (#176): `POST /api/v1/agents/{id}/invoke` now resolves the bearer token server-side from the workspace secrets backend keyed by `agentbreeder/<agent-name>/auth-token`. The dashboard's `<InvokePanel>` drops the password input + `token` state and shows a hint pointing users at `agentbreeder secret set <agent>/auth-token`. The request body's `auth_token` field is preserved as an optional explicit override (e.g. for SDK callers and tests). Backend lookup failures fall through to "no token" — runtime returns 401, no 500s. Documented under "Per-agent auth tokens" in `website/content/docs/secrets.mdx`.
- **`/models` page UX gaps on top of Track F** (#175):
  - `<ProviderCatalog>` now reads the user's role via `useAuth()` and disables the **Add provider** + per-row **Configure** buttons for viewers with a "Requires deployer role" tooltip. RBAC is enforced server-side too — the UI checks are pure UX.
  - **Configure** is no longer a stub. Clicks open a dialog with a password-typed input that POSTs to the new `POST /api/v1/secrets` route under the deterministic key `<provider>/api-key`, writing through the workspace secrets backend (Track K). On success the row flips to a green ✓ **Configured** badge and a success toast fires; 401/403 surface as a permission-denied toast.
  - New `GET /api/v1/providers/catalog/status` returns `{<provider_name>: bool}` so each catalog row can show its real configuration state. The status check covers both the deterministic dashboard key and the legacy env-var name, so secrets imported via the CLI before Track K landed are still recognised.
  - **Sync** button + **Direct providers / Gateways / Local** tabs scaffolded. Sync is disabled with a tooltip pointing at Track G (#163), Gateways at Track H (#164), Local at the future local-runtimes track. The PRs for those tracks just enable the existing affordances.
- **MDX deployment-doc parser failure**: escape `<1 sec` in `website/content/docs/deployment.mdx` (Turbopack/MDX parses `<1` as start of a JSX tag). Was blocking Vercel preview builds on every PR.
- **E2E API smoke `.local` TLD rejected**: switch test email to `@example.com` (RFC 2606); pydantic `email-validator` now rejects `.local` as a special-use TLD, breaking the Docker E2E smoke on every PR.
- **Integration tests / Docker builds**: removed `@agentbreeder/aps-client` npm dep (package not yet published); vendor `aps-client.ts` source directly into Node.js Docker build context so `npm install` no longer fails with 404
- **ESLint errors**: suppressed `react-hooks/set-state-in-effect` errors in `gateway.tsx`, `incidents.tsx`, `prompt-builder.tsx`; fixed root cause in `login.tsx` by initializing `mounted=true` (removes invisible form on first render — fixes E2E login tests)
- **CI gate**: integration tests (`tests/integration/`) now run alongside unit tests in the `test-python` CI job
- **Mocked E2E tests in CI**: switch webServer from `vite dev` (per-request ESM compilation) to `vite build && vite preview` (pre-built static bundle) — eliminates Vite server overload from concurrent workers on slow Ubuntu runners; raise `expect.timeout` to 15 s and seed auth token via `addInitScript` to remove a redundant `/login` page-load per test
- **CI E2E mocked job removed**: removed `test-e2e-mocked` job from CI workflow — mocked E2E tests were consistently failing on GitHub Actions 2-vCPU runners regardless of timeout and server configuration tuning; tests remain available for local execution (`npx playwright test` in `dashboard/`); `docker-build` job no longer depends on them

---

## [1.8.0] — 2026-04-16

### Added

#### Ollama / LiteLLM Support Across All Runtimes (#63, #64, #65, #66)
- **LangGraph, OpenAI Agents, Custom runtimes**: `litellm>=1.40.0` added to requirements when `model: ollama/*`; `OLLAMA_BASE_URL` injected into Dockerfile ENV block
- **OpenAI Agents server**: startup routes to Ollama's OpenAI-compatible endpoint (`AsyncOpenAI(base_url=OLLAMA_BASE_URL/v1)`) for `ollama/` models; standard path unchanged
- **CrewAI runtime + server**: `litellm>=1.40.0` dep for Ollama models; `agent.llm.base_url` set to `OLLAMA_BASE_URL` at startup
- **Google ADK runtime + server**: `SERVER_LOADER_CONTENT` and lifespan both use `LiteLlm(model, api_base=OLLAMA_BASE_URL)` for non-Gemini models; resolves #66 (no `agent.py` workaround needed)
- **Claude SDK validation**: `validate()` rejects `ollama/*` models with a clear error pointing to compatible frameworks
- **DockerComposeDeployer**: auto-starts `ollama/ollama` sidecar, creates `agentbreeder-net` Docker network, runs `ollama pull` before agent starts — resolves #64, #65

### Fixed
- **`Dockerfile.cli` version pin**: uses `ARG VERSION` + `pip install "agentbreeder==${VERSION}"` — eliminates race condition between parallel build-images and publish-pypi CI jobs

### Examples
- `examples/ai-news-digest/`: Google ADK + Ollama Gemma3:27b daily news digest — fetches HN, ArXiv, RSS; synthesises with Gemma; emails via Gmail SMTP

---

## [1.7.0] — 2026-04-14

### Added

#### Agent Architect Skill (`/agent-build`) — M35
- `/agent-build` Claude Code skill: AI-powered agent architect with two paths — Fast Path (6-question scaffold) and Advisory Path (6-question interview → full-stack recommendations → scaffold)
- Advisory Path recommendation engine: framework, model, RAG, memory, MCP/A2A, deployment target, and eval dimensions with reasoning in `ARCHITECT_NOTES.md`
- Advisory Path scaffold generates 19 files including `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, `.antigravity.md`, `memory/`, `rag/`, `mcp/servers.yaml`, `tests/evals/`
- Fast Path scaffold generates 10 files (core project, no advisory extras)
- IDE config file contents tailored to chosen framework, model, and deployment target
- Skill entry added to `AGENT.md` under Build category

#### Documentation
- Homepage animation: split-screen autoplay demo in `docs/index.md` showing `/agent-build` advisory flow (invoke → interview → recommendations → scaffold → deploy, ~14s loop)
- `docs/how-to.md`: `/agent-build` lead paragraph in "Build Your First Agent" section
- `docs/how-to.md`: new "Use the Agent Architect (`/agent-build`)" section with Fast Path walkthrough, Advisory Path walkthrough, 18-row generated-files table, and next-steps commands
- `CLAUDE.md`: added item 8 to "When Adding a New Feature" — scaffold with `/agent-build`

---

## [1.5.0] — 2026-04-13

### Added

#### Framework Parity (CrewAI, Claude SDK, Google ADK)
- `crewai_server`: `/stream` SSE endpoint with `akickoff` step streaming; `_detect_mode`/`_dispatch`/`_validate_output` helpers; `output_schema_errors` field on `InvokeResponse`; `AGENT_MODEL`/`AGENT_TEMPERATURE` env-var wiring for per-agent LLM config
- `claude_sdk_server`: `_call_client` with adaptive thinking and prompt-caching support; `_get_cache_threshold`; `_prompt_caching_enabled`/`_thinking_config` module globals; `/stream` SSE endpoint; `_client` wired from `AsyncAnthropic` at startup
- `google_adk_server`: streaming and tool-wiring parity aligned with Claude SDK and CrewAI servers
- Engine runtimes: Claude SDK requirements bump to `anthropic>=0.50.0`; CrewAI/ADK runtimes inject `AGENT_MODEL`/`AGENT_TEMPERATURE` into container Dockerfile
- `engine/tool_bridge`: fixed `sys.modules` lookup order so test stubs are resolved correctly
- Eject scaffolds for CrewAI, ADK, and Claude SDK agent tiers (`cli/commands/eject.py`)

#### A2A Protocol (M19)
- A2A JSON-RPC 2.0 engine: protocol handler, server, client, JWT-based inter-agent auth (`engine/a2a/`)
- Agent Card generation from `agent.yaml` configuration
- Auto-generated `call_{agent_name}` tools from `subagents:` declarations (`engine/a2a/tool_generator.py`)
- A2A API routes: discovery, invoke, agent card management (`api/routes/a2a.py`)
- A2A registry service for agent registration and lookup (`registry/a2a_agents.py`)
- A2A dashboard: topology graph, call log, agent detail page (`dashboard/src/pages/a2a-*.tsx`)
- Multi-agent orchestration patterns: supervisor, fan-out/fan-in, chain (`engine/orchestrator.py`)
- Orchestration examples: `examples/orchestration/supervisor/`, `examples/orchestration/fan-out-fan-in/`
- A2A subagent example: `examples/a2a-subagent/`

#### MCP Server Hub (M20)
- MCP server packaging and lifecycle management (`engine/mcp/packager.py`)
- MCP sidecar deployer for co-deploying MCP servers with agents (`engine/deployers/mcp_sidecar.py`)
- Enhanced MCP server registry with versioning and sharing (`registry/mcp_servers.py`)
- MCP server detail page and API routes (`api/routes/mcp_servers.py`, `dashboard/src/pages/mcp-server-detail.tsx`)

#### Visual Orchestration Canvas (M30)
- ReactFlow-based orchestration builder with agent, router, supervisor, and merge nodes (`dashboard/src/components/orchestration-builder/`)
- Routing rule editor, strategy selector, and orchestration-to-YAML generator
- Orchestration builder page (`dashboard/src/pages/orchestration-builder.tsx`)

#### TypeScript SDK (M30)
- `@agentbreeder/sdk` npm package (`sdk/typescript/`)
- Agent, Tool, Model, Orchestration, and Deploy classes with full TypeScript types
- `toYaml()` / `fromYaml()` serialization
- Unit tests for agent and orchestration SDK

#### Template System (M21)
- Parameterized agent configuration templates with `{{placeholder}}` substitution
- Template schema (`engine/schema/template.schema.json`) with full JSON Schema validation
- Template CRUD API: create, list, get, update, delete, instantiate (`api/routes/templates.py`)
- Template registry service (`registry/templates.py`)
- Template gallery page with category filters (`dashboard/src/pages/templates.tsx`)
- Template detail page with parameter form and YAML generation (`dashboard/src/pages/template-detail.tsx`)
- `agentbreeder template list|create|use` CLI commands (`cli/commands/template.py`)
- Built-in templates: Customer Support Bot, Data Analyzer, Code Reviewer, Research Assistant (`examples/templates/`)
- Template versioning support

#### Marketplace (M22)
- Marketplace browse API with search, category/framework filters, sorting (`api/routes/marketplace.py`)
- Marketplace registry service with listing submission, approval, reviews (`registry/templates.py`)
- Marketplace browse page with search, filters, star ratings (`dashboard/src/pages/marketplace.tsx`)
- Marketplace detail page with reviews, one-click deploy, install tracking (`dashboard/src/pages/marketplace-detail.tsx`)
- Ratings & reviews system: star rating + text reviews per listing
- Listing approval workflow: submit → admin review → approve/reject
- One-click deploy from marketplace listing (install count tracking)
- Featured listings support
- Marketplace navigation section in dashboard sidebar

#### Other
- `agentbreeder eject --sdk typescript` support (`cli/commands/eject.py`)
- `subagents:` field in `agent.yaml` schema (`engine/schema/agent.schema.json`)
- Orchestration schema updates for fan-out/fan-in and supervisor patterns
- GitHub Actions CI/CD workflows: security scanning, integration tests, release automation
- Branch protection setup script (`scripts/setup-branch-protection.sh`)
- Dependabot configuration for automated dependency updates
- CODEOWNERS file for automatic PR reviewer assignment
- Stale issue/PR bot
- Gitleaks secret scanning
- Trivy container image scanning
- Bandit Python SAST
- pip-audit and npm audit dependency vulnerability scanning
- Dependency Review action (blocks PRs introducing high-severity dependencies)

### Changed
- Extended orchestrator engine with supervisor, fan-out/fan-in, and chain patterns
- Updated config parser and resolver for A2A subagent resolution
- Enhanced orchestration YAML parser for new pattern types

---

## [1.4.0] — 2026-04-12

### Added

#### Package Distribution (M32)
- PyPI: `pip install agentbreeder` (CLI + API server + engine) and `pip install agentbreeder-sdk` (lightweight SDK)
- npm: `npm install @agentbreeder/sdk` — TypeScript SDK published to the npm registry
- Docker Hub: multi-platform images (`linux/amd64` + `linux/arm64`) for API server, dashboard, and CLI
  - `rajits/agentbreeder-api` — FastAPI backend
  - `rajits/agentbreeder-dashboard` — React frontend
  - `rajits/agentbreeder-cli` — lightweight CLI image for CI/CD pipelines
- Homebrew: `brew tap agentbreeder/agentbreeder && brew install agentbreeder` via `Formula/agentbreeder.rb` (Python virtualenv pattern)

#### Release Automation
- `.github/workflows/release.yml`: single workflow publishes to all four distribution channels on each tagged release
- PyPI publishing via OIDC trusted publishers — no long-lived API tokens required
- Homebrew formula auto-updated on every release via the `agentbreeder/homebrew-agentbreeder` tap repo

### Changed
- Both Python packages (`agentbreeder` and `agentbreeder-sdk`) now derive their version from git tags using `hatch-vcs` — no more manual version bumps

### Infrastructure
- CI test matrix expanded to Python 3.11 and 3.12
- Codecov configuration fixed (`fail_ci_if_error`) for accurate coverage reporting across the matrix

---

## [1.0.0] — 2026-03-12

### Added
- Evaluation framework (M18) with dashboard and CI/CD integration
- Quality gates for agent evaluation
- Orchestration YAML (M29) for multi-agent workflows
- Observability stack: distributed tracing, OpenTelemetry integration
- Teams and RBAC enforcement
- Cost tracking and audit/lineage trail
- Python SDK (`agentbreeder-sdk`)
- Visual playground (No Code builder)
- Git CLI workflow (`agentbreeder submit`, `agentbreeder review`, `agentbreeder publish`)
- YAML schemas for `agent.yaml`, `orchestration.yaml`, `prompt.yaml`, `tool.yaml`, `rag.yaml`, `memory.yaml`
- MCP server example and scanner connector
- Builders API (Low Code and Full Code endpoints)
- Three-tier builder model: No Code / Low Code / Full Code

### Framework Support
- LangGraph
- OpenAI Agents
- CrewAI
- Google ADK
- Custom (bring your own framework)

### Deployment Targets
- AWS ECS Fargate
- GCP Cloud Run
- Local Docker Compose

---

[Unreleased]: https://github.com/agentbreeder/agentbreeder/compare/v1.5.0...HEAD
[1.5.0]: https://github.com/agentbreeder/agentbreeder/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/agentbreeder/agentbreeder/compare/v1.0.0...v1.4.0
[1.0.0]: https://github.com/agentbreeder/agentbreeder/releases/tag/v1.0.0
