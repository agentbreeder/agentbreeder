# AgentBreeder Console (local): one place to run services, author registries and agents, deploy

Status: draft r2, 2026-11-11. Epic: #812. **§8 supersedes §4–§7** (architect, frontend-design and
UI/UX Pro Max reviews r1). Builds on the command center
(`agentbreeder up`, #783) and the guided path (#807, spec `2026-11-11-guided-path-design.md` r3,
whose §14 mechanics — read-only opens, redaction, `watch`, fixed argv — this spec reuses). The
closed Studio epic (#718) is history; this replaces it.

## 1. Owner's request (2026-11-11, verbatim intent)

1. Install AgentBreeder; it tells the user to run `agentbreeder up`.
2. `up` opens the console at `localhost:8080`, or the next free port.
3. Sign in with email and password; email `admin@localhost`; password changeable in Settings.
4. Home shows every service (postgres, litellm, phoenix, proxy, ops, …): click one for its logs,
   and see its CPU, memory, disk and IO.
5. **Prompt registry**: every prompt with details, versions, which agents use it; editable, and
   saving creates a new version.
6. **Tool registry**: documentation, code, access control, groups, which agents use it, and its
   execution logs. Onboard a tool from any other location.
7. **RAG**: the knowledge sets.
8. **Memory**: short-term and long-term.
9. **Agents**: onboard with an `agent.yaml`, create one, build a brand-new one; deploy locally now,
   to the cloud later.

Owner decisions (2026-11-11):

- **Sign-in:** the password is set on first run; no shared default ever exists.
- **Priority:** the guided CLI path and the console are built in parallel for 3.0.0a3.
- **Editable in the UI:** prompts, eval sets, tool code, knowledge (RAG) documents, MCP servers,
  memory (short and long term), A2A agents and current agent standards, identity, RBAC, ABAC, roles
  and policies.
- **Memory:** read and edit.

## 2. What exists today (code facts)

| Area | Today | Where |
|---|---|---|
| Server | loopback HTTP server, one-time link → header session token, 1 h idle, default port 8900 | `center/server.py` |
| Actions | every action is a CLI child with fixed argv; job log streaming | `center/jobs.py` |
| Services | `services up/status/logs/down`; status via compose; logs redacted | `services/` |
| Registries | file-backed immutable versions, kinds runtime, prompt, knowledge, tool, mcp, eval, service; `registry new/publish/list/verify` | `registry.py` |
| Agents | `agent.yaml` → `build` → `release` (eval gate) → local Docker or `kind` | `build.py`, `release.py`, `promote.py` |
| Tool executions | per turn in the agent DB `turns.tools` (JSONB), plus `audit_events` and Phoenix traces | runtime `memory.py` |
| Long-term memory | `notes(customer, note, created)` in the agent DB | runtime `memory.py` |
| Short-term memory | ADK sessions in schema `adk_sessions` of the agent DB | runtime `config.py`, `agent.py` |
| Knowledge | registry docs; chunks + embeddings in a pgvector table | runtime `rag.py` |
| Identity / policy | deploy-time identity gate (static, Entra, Keycloak, Okta, AWS), Cedar/OPA/AuthZEN, approvals, kill switch, audit chain | `identity/`, `policy/`, `gate.py` |
| Not built | RBAC/ABAC for people (#742), multi-user control center (#775), authenticated invocation (#780), A2A, cloud targets (#770) | — |

## 3. Principles

- **Same path.** Every console action is a CLI command with `--json`; the CLI command ships first
  and is what the console runs. Nothing the console can do is impossible from the CLI.
- **Loopback, single user per machine** until #775. Sign-in protects the console from other local
  users and processes; it is not multi-tenant access control.
- **Registries stay immutable.** "Save" publishes a new version (`0.1.0 → 0.1.1`, or a chosen bump);
  agents move to it only when the user changes the ref (one click, then `build` and `release`).
- **Claim rule.** Docs and the site claim each capability only after it has run end to end from the
  published package.

## 4. Design

### 4.1 Start and sign-in

- `agentbreeder up` binds `127.0.0.1:8080`, or the next free port up to 8099, and prints the URL.
  The install's last line and `quickstart`'s last line say `Next: <launcher> up`.
- **First run:** the terminal prints a one-time setup link (today's mechanism). The setup page asks
  for a password (≥ 12 characters, checked against a short common-password list) for
  `admin@localhost`. `agentbreeder up --set-password` does the same in the terminal for headless use.
- **Later runs:** email + password form. Passwords are stored as scrypt hashes (stdlib
  `hashlib.scrypt`, per-user salt) in `~/.agentbreeder/center/users.sqlite3` (0600). Sessions are
  `HttpOnly; SameSite=Strict; Path=/` cookies plus the existing custom header and `Origin`/`Host`
  checks (CSRF and DNS-rebinding defences stay). 5 failed attempts → 30 s backoff, doubling.
- **Settings:** change password (needs the current one), sign out everywhere, session length.
  Forgotten password: `agentbreeder up --reset-password` in the terminal (the machine owner).

### 4.2 Home: services

- Every service of every registered project: name, project, image, state/health, host ports, and
  **resource usage**: CPU %, memory (used/limit), network IO, block IO from `docker stats
  --no-stream --format json` filtered by the stack's labels; disk from the volume sizes in
  `docker system df -v`. Refreshed every 5 s while visible (one `docker stats` call for all).
- Click a service → its **logs** (spec r3 §14.3: redacted, follow, filter, level highlight).
- Actions: start/stop/restart a service, review and update the grant (existing job).
- CLI: `services status --usage --json`, `services logs`.

### 4.3 Registries (one page per kind, shared layout)

List: name, latest version, versions, source root (project, path root, built-in), verify status,
**used by** (agents whose `agent.yaml`/`agent.lock` reference it). Detail: manifest, files,
version history with diffs between versions, used by, actions.

| Kind | Detail | Edit → new version |
|---|---|---|
| Prompt | text, variables, diff between versions | text editor |
| Eval set | cases table (id, turns, checks, reference), last results per agent | add/edit/remove cases |
| Tool | `tool.yaml` docs, function signatures, source files, dependencies (hash-locked), grants, **execution log** (from `turns.tools` and `audit_events` of every agent using it, redacted, plus Phoenix trace links) | edit docs and code; dependency changes re-lock |
| Knowledge (RAG) | documents, chunk count and index freshness per agent DB, embedding model and dimensions | upload/edit/remove documents; re-index happens at the next `build`/`release` |
| MCP | server, transport, declared tools with pinned definitions, grants per agent, relay status | edit grants and tool pins (existing `--repin` flow) |
| Service | image, ports, params, secrets (names only), used by stacks | read-only (services are trusted code) |
| Runtime | version, framework, capabilities | read-only |

- **Onboard from another location:** `registry import PATH [--as NAME] [--version X.Y.Z]` copies a
  version directory (any kind) into the project registry after the same checks as `publish`
  (symlinks, hardlinks, size caps, hashes). The console offers a path field and a file upload.
- **Tool code is code execution by design** (it runs inside agents with their privileges). Edits
  are shown as a diff before saving, saved as a new version, and only take effect after `release`
  passes the eval gate. The console says this next to the editor.
- **Access control and groups** for tools: today a tool is available to an agent that references
  it, and MCP tools also need a stack grant. The console shows exactly that. People-level roles and
  groups ("who may edit or use this tool") need RBAC/ABAC (§4.7).
- CLI: `registry show`, `registry diff REF REF`, `registry import`, `registry publish` (exists),
  `registry used-by REF`.

### 4.4 Memory (per agent)

- **Short-term:** ADK sessions from `adk_sessions` in the agent DB: session id, customer, start,
  last activity, turn count; open one to see its turns (from `turns`: message, reply, tools,
  sources, latency, tokens, trace link).
- **Long-term:** `notes` per customer: list, add, edit, delete; delete everything for a customer.
- Edits go through a CLI command (`memory notes add|edit|delete`, `memory sessions delete`) that
  connects with the stack's owner credentials, writes an `audit_events` row (actor = the console
  user, action, before/after hashes, not the text) and never touches the append-only audit table
  itself. Content is untrusted text: `textContent`, control characters stripped.
- This is customer data. The console marks the page as such; nothing is exported or sent off the
  machine; the docs say who can see it (whoever can sign in, or who owns the database).

### 4.5 Agents

- **List:** every agent across projects (guided-path r3 §14.5): production and candidates
  (temporary), target, state, digest, gated, endpoint, last release, identity check.
- **Onboard:** path to a project with `agent.yaml` and `stack.yaml` (exists), or an `agent.yaml`
  alone (the console offers to generate a matching `stack.yaml`).
- **Create from a template:** the starter (guided path `new agent`), Northwind, and later templates.
- **Build brand-new:** a form-based builder: name, model alias, prompt (pick or write), tools,
  knowledge, MCP servers, memory, evals (pick or write) → preview `agent.yaml` + `stack.yaml` →
  `new agent --from-spec` (JSON on a private temp file) → services up → release. Each combination is
  enabled in the builder only after it has been run (gemini + prompt + evals first; then tools,
  knowledge, MCP as each is verified).
- **Deploy:** release locally (eval gate), promote to `kind`; cloud targets later (#770).
- **Try it:** chat box per deployment (`invoke`), with the turn's tools, sources and trace link.
- **Logs** per deployment (r3 §14.3).

### 4.6 A2A and current agent standards

- **A2A:** each deployed agent can publish an Agent Card (`/.well-known/agent.json`) and accept A2A
  `message/send` through the runtime; registry kind `agent` lets an `agent.yaml` reference another
  agent as a tool (`{ref: agent/billing@1.2.0}`), with the call governed like an MCP tool (grant,
  relay, audit). Needs a runtime version (`runtime/adk@1.2.0`) and its own spec; the console shows
  Agent Cards and A2A links once that exists.
- **MCP** is covered by §4.3. Other standards (OpenTelemetry GenAI conventions for traces, AGNTCY/
  agent identity drafts) are tracked in that spec, not built here.

### 4.7 Identity, RBAC, ABAC, roles, policies

- **Today, shown read-only:** identity providers and principals (sponsor, review date, grants),
  policy engine and policy files, approvals, kill switch, audit chain, per deployment
  compliance.
- **Editable in this console (single user):** the operator config's providers and policy files
  (Cedar/OPA/AuthZEN) as text with validation (`validate --operator-config`), new versions kept in
  the state directory with a diff and an audit row; the demo static principals file.
- **People RBAC/ABAC (roles, groups, attribute rules for who may edit, release, deploy, read memory)**
  is #742 and requires more than one person, so it lands with the multi-user control center (#775).
  This console's user model has one user per machine; the data model (`users`, `roles`) is created
  so #742 can add roles without a migration break.

### 4.8 Notifications and logs

As guided-path r3 §14.3–§14.4 (conditions live, events stored, `watch`, redaction).

## 5. Security summary

- Loopback only; Host/Origin/custom-header checks; cookie session; scrypt passwords; backoff; no
  default password.
- Every action is fixed argv to the CLI; refs, names and paths validated against current lists;
  free text (prompts, code, notes, cases) passes through private temp files, never argv.
- Tool code and eval fixtures are trusted code; edits are explicit, diffed, versioned, and only run
  after an eval-gated release.
- Memory is customer data: read/edit audited, no export, no network egress.
- Secrets: names only in the UI; values never shown; logs redacted (best effort).

## 6. Phasing (proposal; the owner chose "everything in 3.0.0a3, in parallel")

Everything in §4 is roughly four to six times the guided-path work. Proposal, each phase a
preview with its own verification from the registries:

| Phase | Contents |
|---|---|
| **A → 3.0.0a3** | Guided CLI path (spec r3 step 1), sign-in (§4.1), Home services with logs and usage (§4.2), Agents list/onboard/create starter/release/try/logs (§4.5 minus the full builder), registry browser for all kinds + prompt and eval-set editing (§4.3 rows 1–2), import (§4.3) |
| **B → 3.0.0a4** | Tool docs/code editing and execution logs, knowledge upload and index status, MCP grants and pins, memory read/edit (§4.4), notifications, full builder for verified combinations |
| **C → 3.0.0a5** | Identity and policy editing (§4.7 single user), A2A runtime + Agent Cards (§4.6, own spec) |
| **Later** | People RBAC/ABAC and roles (#742) with multi-user (#775); cloud deploy (#770) |

## 7. Acceptance (phase A)

From the published package on a clean macOS machine: install → `up` opens on 8080 (or next free)
→ set password → home lists the Northwind services with live CPU/memory/IO and logs per service →
create the starter agent from the console → services up → release passes → try it in the chat box →
edit its prompt, save as 0.1.1, move the agent to it, release (gate passes) → edit an eval case to
fail, release blocked with the reason shown → import a prompt from another directory → sign out,
sign in, change password. Every step also runs from the CLI. Browser click-through recorded.

## 8. r2: design reviews applied (binding over §4–§7)

Reviews (2026-11-11, Fable, read-only): `/architect` CHANGES REQUESTED (3 blocking), frontend-design
"changes required before feature work", UI/UX Pro Max (8 major heuristic problems, no blocker on the
release path). Measured: CLI child ≈ 0.4 s; `registry list --json` 0.41 s; `docker stats
--no-stream` 2.56 s; `docker system df -v` 0.37 s.

### 8.1 Architecture

1. **Session (F1, blocking):** no cookie. Cookies are not port-scoped on 127.0.0.1, so a console
   cookie would reach Phoenix, LiteLLM, project services and agent containers' pages. The password
   only mints today's header-bearer token (`POST /api/session {email, password}`), kept in
   `sessionStorage`; one sign-in per tab. The one-time terminal link stays as an always-available
   path. The console redirects `localhost:<port>` to `127.0.0.1:<port>` and accepts one host form.
2. **Work classes (F2, blocking):** engine jobs (build, release, promote, services, cluster) one
   at a time **per project**, streamed; short writes (registry publish/import, `agent set-ref`,
   memory edits, policy save) as synchronous CLI children with a 60 s timeout and a per-target lock,
   not blocked by engine jobs; reads per 8.1 #3.
3. **Reads (F3):** a hybrid. The server imports only file-reading modules (`registry`, `lock`,
   `agentfile`, used-by) and its own `center.sqlite3`, calling the same functions as the CLI's
   `--json` commands, cached by (path, mtime, size) and invalidated on job end; `verify` runs off
   the request thread. Everything touching `deployments.sqlite3`, `stack.db`, secrets, Docker, the
   operator config or an agent DB stays a CLI child (single-flight, cached). `up` runs isolated
   (`-I`) and imports eagerly at start. The guard test's forbidden list gains `services.state`,
   `secret_files`, `audit`, `operator_config`. Principle §3 becomes "same function; every write is
   a CLI command".
4. **Single instance (F4):** `flock` on the center directory; a second `up` prints the running URL
   and exits. Port: scan 8080–8099, print the URL, CHANGELOG note (a2 used 8900).
5. **Plumbing (F5):** a route table with typed handlers and a `{data, error, stale}` envelope;
   package-data glob made recursive for `static/**` with a packaging test; a3 offers import **by
   path only** (no browser upload; stdlib has no multipart parser); editors are `<textarea>` plus a
   server-computed diff (CSP stays strict).
6. **Usage (F13):** one long-lived `services usage --follow --json` child (streaming `docker stats`
   over IDs resolved by label and deployment ID, re-resolved on `watch` events), heartbeat-scoped,
   latest samples in an in-memory ring (so sparklines have 5 min of history on load); disk every
   60 s, cached, with an "as of" time; CPU and memory labelled "of the Docker VM".
7. **`center.sqlite3`** gets a `schema_version` table and migrations; users live there (no second
   file); a2 upgrade path: existing state, no password → first-run setup.

### 8.2 Auth details (F6)

scrypt with explicit `n=2**15, r=8, p=1, maxmem` stored per row; constant-time compare; dummy hash
for unknown emails; global backoff capped at 5 minutes, the terminal link always works;
`--set-password`/`--reset-password` read from a TTY or stdin, never argv; a credential version in
the DB invalidates sessions on reset, even while running; sessions are in memory (restart signs
everyone out); polls do not refresh the idle timer; on a 401 a sign-in dialog opens over the page
and editor drafts persist in `sessionStorage`. The docs say plainly: processes running as the same
OS user can read the console's files and run the CLI; the password protects against other users
and browsers, not against your own account.

### 8.3 Memory and tool logs (F7–F10)

- A dedicated **console role** per stack through the existing `roles` mechanism: SELECT on `turns`
  and the session schema; SELECT/INSERT/UPDATE/DELETE on `notes`; DELETE on sessions; INSERT on
  `audit_events`. Shipped in the starter and Northwind stacks; without it the page is read-only and
  says why. Never the owner role. Memory access runs in a CLI child (needs `psycopg`, `[eval]`).
- Actor `console:admin@localhost`, shown as unverified. Edits are audited, plus one "viewed
  session" event per session opened. "Delete a customer" is defined per table (notes, sessions,
  turns, tickets); `audit_events` rows remain and the UI says so; typed confirmation. Never called
  tamper-evident.
- The runtime manifest declares its memory schema version; the CLI refuses unknown versions.
- **Owner decision needed:** short-term memory (ADK sessions, a third-party layout) is **view and
  delete**, not edit; long-term notes are fully editable.
- Tool execution log: default columns time, agent, status, latency, turn id, trace link; arguments
  and results behind the customer-data marking and the console role; time-window queries with a
  LIMIT; per-agent fan-out on demand.

### 8.4 Registry (F11, F12)

Edit = copy version N to a private temp dir → change → bump → `publish` (atomic; a rename failure is
a "version exists" conflict, e.g. two tabs). Built-in and `AGENTBREEDER_REGISTRY_PATH` versions get
"fork into project registry", not edit. "Move agent to this version" is a CLI command
`agent set-ref OLD NEW` doing an exact-string replacement in `agent.yaml` (refusing on ambiguity,
diff shown first), followed by `build`. The console never deletes a version. "Used by" shows
declared / locked / deployed separately, "among registered projects". The A2A `agent` kind is
designed once, together with the release-bundle epic's `agent/<name>@ver`.

### 8.5 Frontend

- **Preact + htm**, vendored as pinned ES modules (SHA-256 checked in a test), **no build step**.
  Keyed incremental rendering fixes the whole-page rebuild (lost input, focus, scroll; unreadable
  live log). Guard: no `innerHTML`, `outerHTML`, `document.write` or `dangerouslySetInnerHTML` in
  any non-vendor file; CSP adds `require-trusted-types-for 'script'`; a test that no static file
  references an external origin.
- **Tests:** `node --test` for pure modules (router, API client, diff, formatters) in CI; one
  Playwright + axe smoke over the acceptance path, light and dark, as a separate job.
- **Shell:** sidebar of nouns (Home, Agents, Registry ▸ Prompts/Eval sets/Tools/Knowledge/MCP/
  Services/Runtimes, Memory, Governance ▸ Identity and policy/Audit, Targets, Settings); top bar with
  scope switcher ("All projects" or one; environment as a chip), a "Next: …" pill from `next
  --json`, an activity pill (running job; opens a dock; closing never stops polling), the
  notifications bell, the user menu. Hash routes (`#/agents/<id>/try`), per-route title, focus to
  the `h1`, skip link. The guide's steps leave the nav and become a Home checklist plus empty-state
  primary buttons plus the result row of finished jobs.
- **Visual:** brand zinc + green; green only for the primary action, healthy and gate passed;
  violet for candidates/temporary everywhere; neutral/blue chart strokes. Geist and Geist Mono
  vendored as woff2 (no Google Fonts). 14 px UI, 13 px tables, 12.5 px logs, tabular numerals,
  32 px rows, 8 px grid, fluid to 1440 px. **`brand.css` gains a light palette, a text-muted token
  that passes AA (`#a1a1aa` on dark) and a 3:1 control-border token**; the console consumes a
  snapshot under the existing drift check (a cross-repo change: Cloud and website).
- **Signature element:** the gate strip on each agent: Build → Candidate → Evals n/N → Identity →
  Production.
- **States:** every screen has empty, loading (skeletons, `aria-busy` per region) and error states
  (Docker down = page blocker with the `doctor` remedy; port busy; missing key inline with typed
  values preserved; gate failed = failed-case table with edit links and the saved candidate tail;
  "console not reachable" vs "session ended").
- **Walkthrough fixes** (#811) plus: confirm dialogs focus Cancel and return focus; typed names for
  purge and cluster delete; a three-tier confirm with a "this code runs in agents" checkbox for tool
  edits, onboarding and import; a visible reason under every disabled control; the job log appends
  instead of rebuilding; Northwind's cost figures only for Northwind.
- **Try it:** production by default, candidates excluded and violet; per-turn inspector (tools,
  sources, latency, tokens, trace); note "each message calls the model and costs money; the local
  endpoint is unauthenticated".
- §4.5's "preview agent.yaml" means server-produced text, never generated in the browser.

### 8.6 Identity, RBAC, A2A (F15, F16)

- Policy editing writes the live `AGENTBREEDER_OPERATOR_CONFIG` file atomically after `validate`,
  keeping prior copies beside it; the audit row goes to the default state's chain; the UI says the
  same single user can edit policy and run the deploy it gates — not separation of duties.
- **Owner sign-off needed:** people RBAC/ABAC and roles are not editable until #742/#775. The
  speculative `roles` table is dropped.
- A2A needs authenticated invocation (#780) first; the well-known Agent Card path is checked
  against the current A2A spec when its own spec is written; no nav entry until it runs.
- Tool-code wording: the eval gate is a quality gate, not a security review.

### 8.7 Effort and cut (F14)

Architect's estimate in focused days (including this repo's pipeline): foundation 4–6; guided
step 1 5–8; guided step 2 8–12; sign-in 2–3; Home usage 2–3; agents create/try 3–4; prompt + eval
editing, diff, set-ref, import 5–7; verification and docs 2–3 → **phase A 31–46**; phase B 25–37;
phase C 20–32; **everything ≈ 76–115**.

Recommended 3.0.0a3: foundation; guided step 1; sign-in; Home (services, logs, usage); all agents +
create starter + release + try it; read-only registry browser for all kinds; **prompt editing**
(proving save → new version → set-ref → release once); conditions (no stored events); import by
path. 3.0.0a4: eval-set editing, upload, events and the bell's history, then phase B. **Owner to
decide** between this cut and one long a3 with interim unpublished verification after each block.

### 8.8 Foundation (built first, before any page)

1. Route table + envelope; 2. job classes, per-project locks, single-flight reads, single-instance
lock; 3. in-process read layer with `-I`; 4. `center.sqlite3` schema versioning; 5. frontend shell
(Preact + htm vendored, router, API client, recursive package data); 6. test harness (`node --test`
in CI, Playwright + axe smoke job).

## 9. r3: owner decisions and plan of record (2026-11-11)

The master plan `docs/superpowers/plans/2026-11-11-console-epic.md` (r2) is the plan of record and
**supersedes §6, §7 and §8.7 here and guided-path §14.1** (scope, phasing, previews). Owner
decisions: one epic built milestone by milestone; **one published preview at the end**; docs in
every PR, **website only at the release**; short-term memory **view and delete**; people RBAC/ABAC
and roles **last** (with #742/#775). §4.1 and §5's "cookie" are struck: the session is the
header-bearer token (§8.1 #1). The guide's steps leave the nav (console §8.5 wins over guided
§9.2 #6). A `/security` design review of this spec runs before M0a.

## 10. Security review (S, #814)

Status: r2, 2026-11-11, design review only (no code changed). Reviewed: this spec (§4–§9), the
plan of record `docs/superpowers/plans/2026-11-11-console-epic.md` r3 (wins where it differs),
guided-path §6, §14.3–§14.4 and §14.9, and the code on `main` at `f7b521f`:
`center/server.py`, `center/jobs.py`, `center/static/app.js`, `cli.py`, `secret_files.py`,
`services/`, `release.py`, `evals.py`, `registry.py`, `lock.py`, `store.py`, `operator_config.py`,
`runtime.py` and the runtime under `builtin_registry/runtime/adk/1.1.0/runtime/`. Method: STRIDE
per surface, with the local adversaries named in #814: other OS users, other local processes,
browser pages on other `127.0.0.1` ports, containers reaching the host's loopback, and prompt
injection through agent output into the UI. Line numbers refer to `f7b521f`.

**Scope.** The ten surfaces #814 names (§10.2). Not reviewed here: M9b, M13 and M19 get their own
`/security` reviews under the plan ("How every milestone is run" #1); M15 (grant edits,
`services-up` for MCP stacks) and M17 (`new agent --from-spec`) get a security delta review as a
task on their issues (S-34).

### 10.1 Threat model and invariants

- **Trusted:** the OS account that runs `up` and every process running as it. They can read
  `~/.agentbreeder`, run the CLI and attach to Docker; the console does not defend against them
  and the docs say so (§8.2).
- **Untrusted:** other OS users; browser pages on any origin, including the other `127.0.0.1`
  ports this product opens (agent chat pages, Phoenix, LiteLLM, project services); any process
  inside a container (agent tool code, MCP servers, project services), all of which prompt
  injection can steer; every byte of agent output, model output, log line, registry file, note,
  eval case and knowledge document.
- **I1. A console session is code execution as the owner.** A session can register a project and
  run `services up`, `build` and `release`, which build and run project code. Every bug that
  leaks or mints a session for someone else (XSS, a token in a log or argv, claiming the first
  password) is code execution; the browser-side guards (CSP with Trusted Types, the source guard,
  no HTML sinks; plan M0b) are a primary control, not hardening.
- **I2. Browser checks are not authentication.** `Host`, `Origin`, `Sec-Fetch-Site` and
  `X-AgentBreeder-Center` (`center/server.py:269-289`) stop browsers (DNS rebinding, cross-site
  and same-site pages); any non-browser client sets them freely. Only the session token and the
  password stand between a local process or a container and the API.
- **I3. The server holds no secret value** (§8.1 #3, R2). Key values pass through it once on
  their way to a 0600 env file; everything that reads secrets, Docker, state or an agent DB is a
  CLI child.
- **I4. Audit rows are evidence, not proof.** The deploy chain detects corruption but its owner
  can recompute it; the runtime's `audit_events` is append-only for the application role
  (runtime `memory.py:39-40`) and its database owner can drop the trigger. Nothing here is
  tamper-evident, and the docs never say so.

Severity: **High**: a realistic path, without a further precondition, to code execution, a
secret or customer data for an untrusted party. **Medium**: the same with a precondition (the
owner acts on untrusted input, a particular platform, a race), or a control the plan relies on
that is missing. **Low**: defence in depth, hygiene, or a path that only a session holder (who
already has code execution, I1) can use. **Info**: checked; the plan already resolves it.

### 10.2 Surface × STRIDE

| Surface | Spoofing | Tampering | Repudiation | Info disclosure | DoS | Elevation |
|---|---|---|---|---|---|---|
| Password auth and session (M2) | S-28, S-29, S-02 | S-24 | S-30, S-31 | S-15 | S-14, S-24 | S-28 (first password = I1) |
| Memory edits (M16) | S-31 (actor self-asserted) | S-07, S-12 | S-30, S-32 | S-19, S-35 | none: per-note cap (S-07) and LIMIT reads (§8.3) | S-32 (role writes audit rows) |
| Tool-code edits (M12) | none: only the owner's session edits (I1) | S-04, S-06 | S-30 (registry writes unaudited) | S-22, S-35 | none: publish caps `registry.py:47-48` | S-06, S-33 |
| Policy edits (M18) | none: session-gated (I1) | S-04, S-21 | S-30 (audited in the default chain, M18) | S-20 | none: atomic replace | S-20 (Low, I1) |
| Registry import and code-bearing onboarding (M9a, M7) | S-01 (planted project) | S-01, S-04 | S-30 | S-27 | none: `MAX_FILES`/`MAX_BYTES` | S-05 |
| Knowledge upload (M14) | none: session-gated | S-03, S-13 | S-30 | S-13 (git-tracked), S-35 | S-13 | none: documents are data, steering only (S-05) |
| Try it (M7) | S-12(c) (any `customer_id`, #780) | S-12(b) | none: turns recorded by the runtime | S-12(a), S-35 | none: one in-flight message per tab, runtime caps | none: loopback-only `invoke` (guided §6) |
| Log viewer and redaction (M5) | none: read-only | S-08, S-35 | none: reads | S-09 | S-10 | none: text only (S-23) |
| Long-lived children (M5, M6, M11) | none: fixed argv | none: fixed argv | none | S-17 | S-10, S-18 | none: no free text in argv |
| Lock scopes (M0a) | none | S-11 | none | none | S-11 (busy errors) | none |

### 10.3 Findings

"Carried by" is the milestone issue that gets the task line; a finding closes when that
milestone's verification shows it.

| ID | Sev | Surface | Finding | Carried by |
|---|---|---|---|---|
| S-28 | High | Sign-in | Whoever reaches the setup route first sets the first password | M2 #818 |
| S-01 | Medium | Onboarding | Registration accepts paths other OS users control | M0a #815, M7 #823 |
| S-02 | Medium | Sign-in | Containers reach the console through `host.docker.internal` on Docker Desktop | M2 #818 |
| S-03 | Medium | Routing | Guards are per-method code; new PUT/DELETE routes could skip them | M0a #815 |
| S-04 | Medium | Edits | Reviewed bytes are not bound to written bytes (publish, import, set-ref, policy save) | M9a #825, M10 #826, M12 #828, M18 #834 |
| S-05 | Medium | Import/onboarding | The code-bearing kind list omits `mcp` and `runtime` | M9a #825 |
| S-06 | Medium | Tools | Signatures must be read with `ast`, never by import | M12 #828 |
| S-07 | Medium | Memory | A note edit is an ungated write into the agent's instructions | M16 #832 |
| S-08 | Medium | Logs | Container output reaches the terminal raw (escape injection) | M5 #821, M3 #819 |
| S-09 | Medium | Job output | Job lines are stored with only the form's keys redacted | M3 #819, M5 #821 |
| S-10 | Medium | Children | Children outlive a killed console | M5 #821 (+M6, M11) |
| S-11 | Medium | Lock scopes | Console locks are in-process; `services` and `cluster` have no file lock | M0a #815, M3 #819, M6 #822 |
| S-12 | Medium | Try it, editors | Free text in argv; links from agent output; any `customer_id` | M7 #823, M10 #826, M16 #832 |
| S-13 | Medium | Knowledge upload | Names, sizes, disk, archives, serving, git-tracked destination | M14 #830 |
| S-29 | Medium | Sign-in | Another OS user squats the preferred port and serves a fake sign-in | M0a #815, M2 #818 |
| S-32 | Medium | Memory, tools | The console role's `audit_events` INSERT lets its holder forge runtime rows | M12 #828 |
| S-33 | Medium | Tools | Dependency re-lock could build sdists on the host | M12 #828 |
| S-14 | Low | Sign-in | The global backoff lets anyone local lock the owner out | M2 #818 |
| S-15 | Low | Sign-in | Link and password channels when not on a TTY | M2 #818 |
| S-16 | Low | Files at rest | Key env files and editor temp files survive a crash | M0a #815 |
| S-17 | Low | Errors | Read failures return the CLI's last stderr line verbatim | M0a #815 |
| S-18 | Low | Cancel/shutdown | SIGINT reaches only the direct child | M0a #815 |
| S-19 | Low | Memory | `forget` is not erasure | M16 #832 |
| S-20 | Low | Policy | A policy edit can repoint credential files and provider URLs | M18 #834 |
| S-21 | Low | Policy | Config read more than once per command could mix versions | M18 #834 |
| S-22 | Low | Tools | The console role's credential handling is unspecified | M12 #828 |
| S-30 | Low | All writes | Which console writes are audited is unspecified | M0a #815, M2 #818, M10 #826 |
| S-31 | Low | Memory, policy | The actor `console:admin@localhost` is asserted by a CLI child | M16 #832 |
| S-35 | Low | Rendering | Control and bidi characters in every text view and in copy | M0b #816 |
| S-23 | Info | Browser | XSS / HTML sinks: resolved by M0b's CSP, Trusted Types and source guard | M0b #816 |
| S-24 | Info | Sign-in | Scrypt exhaustion, enumeration, fixation, idle refresh: resolved by M2 and R7 | M2 #818 |
| S-25 | Info | Same-site pages | Other `127.0.0.1` ports: blocked; keep a regression test | M0a #815 |
| S-26 | Info | Notifications | Stored events carry fixed templates only | M11 #827 |
| S-27 | Info | Registry show | Control/bidi, symlinks, size caps: resolved by M8, plus file names | M8 #824 |
| S-34 | Info | Out of scope | M15 and M17 need a security delta review | M15 #831, M17 #833 |

Counts: High 1, Medium 16, Low 12, Info 6 (35 findings).

### 10.4 Detail per surface

**Sign-in and session (M2).**

- **S-28 (High; Spoofing, Elevation).** M2 has a setup page that sets the first password for
  `admin@localhost`, and an a2-upgrade path (state without a password goes to setup). If that
  route accepts any caller, a container (S-02) or another OS user that reaches the port before the
  owner sets the password, or while an upgraded a2 state has none, becomes the console user, which
  is code execution as the owner (I1). Not a narrow race: the window lasts as long as `up` runs
  without a password, including any time an a2-upgraded state is being served. **Resolution (M2):** setting a password requires a session
  minted from the one-time terminal link (or `up --set-password` on the terminal); once a password
  exists, changing it requires the current password, and `--reset-password` is terminal-only. Tests:
  a setup request without a link-minted session gets 401; with a password set, a change without the
  current password is refused; the a2-upgrade path behaves the same.
- **S-29 (Medium; Spoofing).** The port scan (plan M0a: 8080–8099, today `make_server` falls back
  to any free port, `center/server.py:392-404`) lets another OS user bind 8080 first, so `up` moves
  to 8081 while a bookmarked or stale tab on `127.0.0.1:8080` shows the squatter's fake sign-in
  form, which collects the password. A squatter on the old origin is also same-origin with the old
  tab's `sessionStorage` (drafts; the token is per tab and the old server's sessions die with it).
  **Resolution:** once a password exists, `up` refuses to scan past a taken preferred port and
  prints which port is busy and `--port` (M0a, M2); the first run may scan. This amends plan M0a's
  task ("Port scan 8080–8099") and acceptance ("picks 8081 when 8080 is taken"): the scan runs only
  in first-run state (owner decision §10.5 #5). Docs: open only the URL
  `up` prints. Drafts in `sessionStorage` are restored only after sign-in succeeds against the
  current server (M2).
- **S-02 (Medium; Spoofing, DoS).** On Docker Desktop, `host.docker.internal` forwards to the
  host's loopback, so every container can reach the console port; the egress code already says so
  (`egress/host.py:416-418`). Per I2, a client that sends the expected headers passes
  `_host_ok`/`_api_ok`. **Resolution (M2):** the password and token are the boundary: keep the
  ≥ 12-character policy, the common-password list and the backoff. M2's verification, on macOS,
  from inside a Northwind container: `curl -H 'Host: 127.0.0.1:<port>' -H
  'X-AgentBreeder-Center: 1' http://host.docker.internal:<port>/api/overview` returns 401; a
  `POST /api/session` with those headers plus `Origin: http://127.0.0.1:<port>` and
  `Content-Type: application/json` and a wrong password hits the backoff; a bare `curl` gets 403
  from `_host_ok` (`center/server.py:269-270`). On Linux, agents get `host-gateway`
  (`runtime.py:158-164`), which does not reach a `127.0.0.1` listener: recorded as by design, not
  run. Docs state that local containers can reach the sign-in form.
- **S-14 (Low; DoS).** The global backoff (plan M2) lets any local process or container keep the
  owner waiting up to 5 minutes per attempt. **Accepted:** the terminal link always works; the
  sign-in page says "too many attempts; press Enter in the terminal running `up` for a one-time
  link".
- **S-15 (Low; Information disclosure).** One-time links are bearer credentials; printed to a
  redirected stdout they land in log files. **Resolution (M2):** print a link only when stdout is a
  TTY, otherwise write it to the 0600 `login.html` (as `mint_login` does,
  `center/server.py:88-100`) and print that path; `--set-password`/`--reset-password` read with
  `getpass` on a TTY, or one stdin line otherwise; never argv, never echoed.
- **S-24 (Info).** Resolved by plan M2: backoff checked before hashing, single-flight hashing,
  dummy hash for unknown emails, constant-time compare, a new token per sign-in, credential
  version re-read per request, polls not refreshing the idle timer (R7; today `session_valid`
  refreshes on every call, `center/server.py:132`).

**Onboarding and project registration (M0a, M7).**

- **S-01 (Medium; Tampering, Elevation).** `Center.register` (`center/server.py:137-150`) resolves
  any path and only checks that `agent.yaml` and `stack.yaml` are regular files. Precondition: the
  owner registers a directory another OS user controls (a shared or `/tmp` path from a link or a
  doc). That user's code then runs as the owner at `services up` (project services built from
  source) and `release`, and the user can swap files between the grant review and the job.
  **Resolution:** registration and every job start require the project directory, `agent.yaml`
  and `stack.yaml` to be owned by the current uid and not group- or world-writable, and each
  ancestor up to `/` to be owned by the current uid or root and not group- or world-writable,
  except a sticky directory (mode `+t`, such as `/tmp`), which cannot rename entries it does not
  own. Refused with a message naming the path. M0a for `register`, job start and
  `onboard_example`'s destination; M7 for `new agent --stack-for`. On macOS volumes mounted with
  "Ignore ownership on this volume", every file reports the current uid, so the check passes; the
  docs say to keep projects on the system volume. Tests: a 0777 directory, a non-sticky writable
  ancestor and another uid (patched `os.stat`) are refused; a user-owned directory under `/tmp`
  passes.

**Routing and request handling (M0a, M14).**

- **S-03 (Medium; Tampering).** Only `do_GET` and `do_POST` exist, and each applies the guards in
  its own code (`center/server.py:291-317`); body limits and `Origin` live in `_body`
  (`:277-289`). M14 adds a streamed `PUT`. **Resolution (M0a):** the route table applies host, API
  header, `Sec-Fetch-Site`, session and, for every non-GET method, `Origin` and content-type checks
  before dispatch; unknown methods get 405; a table-driven test sends every route with each guard
  missing.
- **S-25 (Info).** A page on another `127.0.0.1` port is `same-site`, so `_api_ok`
  (`center/server.py:272-275`) rejects it, and the custom header forces a preflight the server
  never approves. **M0a:** keep a regression test for `Sec-Fetch-Site: same-site` with a valid
  session and header.

**Rendering and prompt injection into the UI (M0b, M5, M7, M12, M16).**

- **S-23 (Info).** Agent replies, log lines, notes, case text and file contents are hostile.
  Plan M0b's CSP with `require-trusted-types-for 'script'`, the source guard and text-only
  rendering resolve DOM injection; I1 is why they are mandatory. Today's UI keeps the token in
  `sessionStorage` and drops the `#t=` fragment with `replaceState`
  (`center/static/app.js:17-18`, `:352-354`).
- **S-35 (Low; Tampering, Spoofing).** Text-only rendering still shows bidi overrides (a log line
  or note that reads differently from its bytes) and keeps raw ESC sequences, which a "Copy"
  button then puts on the clipboard for a terminal. **Resolution (M0b):** one shared text component
  for the log viewer (M5), Try it (M7), the tool execution log (M12) and notes (M16) that shows C0/C1
  controls and bidi controls as visible escapes (`\x1b`, `U+202E`) and copies the escaped form.
- **S-12 (Medium; Information disclosure, Tampering).** (a) Free text goes by stdin or a private
  0600 temp file, never argv, which every local user can read through `ps`: Try it messages
  (`invoke NAME -`, M7), notes (`memory notes add|edit`, M16), eval cases (M10's edit flow) and the
  policy text (M18's `operator-config save FILE` names a private temp file, already a path). Try it
  turns are kept only in memory, not as job lines in `center.sqlite3`, which survives `--purge`
  (R4). Today's `Runner.read` does not fit: it sets `stdin=DEVNULL` (`center/jobs.py:354`), caches
  by argv and returns the last good answer on failure (`:361-364`), and times out at 30 s. M7 adds a
  read variant with `input=` (the message), `cache=False`, no stale fallback, and a 180 s timeout
  (matching `invoke`). (b) Agent output never becomes a link
  target: no Markdown rendering or auto-linking in a3; a trace link is built only from a
  `[0-9a-f]{32}` trace id and the stack's recorded Phoenix origin; sources show as registry refs or
  document names. (c) Try it can name any `customer_id`, because the endpoint is unauthenticated
  (runtime `server.py:120-127`, #780); the panel says so. **Carried by M7, M10, M16.**

**Logs and redaction (M3, M5).**

- **S-08 (Medium; Tampering).** Existing: `services logs` prints `engine.logs` output raw
  (`cli.py:488-490`, `services/engine.py:706-715`). Planned: `agentbreeder logs` (M5) and
  `logs --failed` (M3). A customer message or tool result echoed into a log can carry ESC/OSC
  sequences: OSC 52 writes the clipboard, OSC 8 forges links, CSI rewrites earlier lines.
  **Resolution:** on a TTY the CLI shows C0 (except tab) and C1 controls, ESC included, as visible
  escapes; `--raw` keeps bytes; `--json` escapes by construction. M5 for `logs` and the existing
  `services logs`, M3 for `logs --failed`.
- **S-09 (Medium; Information disclosure).** The job pump redacts only the keys typed in the form
  (`center/jobs.py:318-324`) and stores up to 20 000 lines per job in `center.sqlite3`. Any secret a
  job's CLI child prints is stored. Today's redactors differ: `compose.redact` is literal, ≥ 4
  characters (`services/compose.py:42-51`); `jobs.redact` adds base64 and URL forms
  (`center/jobs.py:81-92`). Checked on `f7b521f`: the CLI's Docker and compose children capture or
  discard their output (`runtime.py:38-45`, `services/compose.py:346-351`); none inherits the job's
  stdout. **Resolution:** M3's `redaction.py` is applied by the CLI to every child stream it
  prints, with a test that job commands start no child with inherited stdio; the server-side pump
  keeps redacting the form's keys as a second pass. M5's verification seeds known secrets, runs a
  Northwind release and services-up through the console, and scans `center.sqlite3` and API output
  for literal, base64, URL and JSON forms. Docs keep "best effort".

**Long-lived children (M5, M6, M11).**

- **S-10 (Medium; DoS).** Children run with `start_new_session=True` (`center/jobs.py:293`), so a
  console killed with SIGKILL leaves `docker logs -f`, `docker stats` and `docker events` running;
  shutdown cancels only the current job (`center/server.py:461-466`). **Resolution:** CLI (M5,
  lane A): `logs --follow`, `services logs --follow`, `watch` and `services usage --follow` exit on
  stdin EOF and kill their own Docker or kubectl grandchildren. Console (M5): those children get
  `stdin=PIPE`; `up` records child PIDs and start times in a 0600 file and kills matching leftovers
  at start; caps of 8 follows per session, 16 overall, one `watch` per project and one usage child.
  M6 and M11 use M5's pattern.
  *Implementation note (CLI, #866/#867):* the reader (`docker logs -f`, `docker compose logs
  -f`, `kubectl logs -f`) stays in the CLI's process group, so the sweep's group kill of a
  leftover CLI also reaches its reader; the console must start each CLI child as the leader of
  its own group (`start_new_session=True`, as jobs do) and kill that group. The CLI stops the
  reader and its descendants itself on stdin EOF, SIGTERM or SIGHUP (SIGTERM, then SIGKILL after
  2 s): `docker compose` is `docker` plus a plugin process, and a stop while it starts once hung
  a follow when only `docker` was signalled. A SIGKILL of the CLI process alone (not its group)
  leaves the reader until its next write; the sweep kills groups, never single PIDs.

**Lock scopes (M0a, M3, M6, M18).**

- **S-11 (Medium; Tampering).** The console's locks (plan M0a) live in the server process, so a
  terminal `services up` or `cluster down` runs alongside a console job. Existing file locks: only
  the store (`store.py:167`, `worker.lock`) and `release` (`release.py:121`, `release.lock`);
  `services up/down` and `cluster up/down` take none, and the planned `services
  restart|stop|start` (M6) need one. **Resolution:** M0a documents that console locks order the
  console's own jobs and correctness comes from CLI file locks. The console runs cluster jobs from
  M0a, so the machine-wide `flock` for `cluster up/down` goes in M3 (lane A, earliest); M6 adds
  the per-stack `flock` (stack state directory) to every `services` write command, with an
  "another services operation is running" error the console shows.
- **S-21 (Low; Tampering).** Policy save is refused while a console engine job runs, but a
  terminal deploy can overlap. **Resolution (M18):** the atomic replace keeps readers whole; a test
  asserts each command loads the operator config once and records that digest
  (`operator_config.py:418-434`) in its audit rows.

**Edits, import and code-bearing kinds (M9a, M10, M12, M18, M7).**

- **S-04 (Medium; Tampering).** Each edit shows a diff, then a separate request writes. Between
  the two, the source (import path, temp copy, live `agent.yaml`, live operator config) can change,
  so the bytes written are not the bytes reviewed, or overwrite a newer file. `publish` copies and
  hashes in one pass (`registry.py:293-316`). **Resolution:** the preview returns digests; the
  write fails on mismatch, as `services up --expect-grant` does today (`center/jobs.py:213-226`).
  `registry import`/`publish` take `--expect-digest` of the staged copy (M9a, M10, M12 for tool
  saves); `agent set-ref` takes `--expect-base` (the reviewed `agent.yaml` digest) (M10);
  `operator-config save` takes `--expect-digest` (reviewed new bytes) and `--expect-base` (the live
  config's digest) (M18).
- **S-05 (Medium; Elevation).** Plan M9a applies the "this code runs in agents" confirm to tool,
  service and eval imports. Also code-bearing: `mcp` (relay containers run as the operator's uid,
  `services/state.py:261-265`) and `runtime` (the agent server itself). Medium, not Low like S-20,
  because the precondition is the owner importing untrusted content without a warning, not an
  attacker who already holds a session. **Resolution (M9a):** add `mcp` and `runtime` to the confirm
  list; prompts and knowledge get a lighter "this text steers the agent" note. The confirm is a UX
  control, not a boundary (I1).
- **S-06 (Medium; Elevation).** Importing a tool module on the host to read its function
  signatures would run its top-level code. **Resolution (M12):** signatures come from `ast.parse` of
  the stored source, size-capped; a test with a module whose top level raises proves it is never
  imported.
- **S-33 (Medium; Elevation).** §4.3 says a tool's dependency change re-locks. Resolving Python
  dependencies on the host can build an sdist, which runs its `setup.py` or build backend as the
  owner: code execution the confirm does not describe. The runtime image already installs with
  `--require-hashes --only-binary=:all:` inside Docker (`runtime/adk/1.1.0/Dockerfile:7`).
  **Resolution (M12):** re-lock is wheels-only and hash-only on the host, or runs inside a
  container; an sdist-only dependency is refused with a message.
- **S-22 (Low; Information disclosure).** **Resolution (M12):** the console role's credential is a
  stack secret, read only by CLI children, included in `redaction_values`, never in the server
  process (guard test of §8.1 #3), never shown.

**Repudiation (all writes).**

- **S-30 (Low; Repudiation).** On `f7b521f`, deploy, release, promote and remove write rows to the
  deployment audit chain (writers `deployment.py`, `promote.py` and `egress/host.py`; `store.py`
  only holds the chain); `services up/down`, `cluster up/down` and
  registry publish write none, and console sign-in does not log. **Resolution:** the console's job
  history in `center.sqlite3` (not chained) is the record for services and cluster jobs (M0a);
  sign-in, failed sign-in, password set/change/reset and sign-out-everywhere are written to a
  `security_events` table there with time and outcome, no secrets (M2); registry edits and
  `set-ref` leave their record in the project's files and git (M10 docs); memory edits go to the
  agent DB's `audit_events` (M16); policy saves to the default state's chain (M18). Docs list this.
- **S-31 (Low; Repudiation).** The actor `console:admin@localhost` is a string the CLI child
  passes; any process running as the owner can write the same. **Resolution (M16):** shown as
  unverified (§8.3); docs say console rows record that the console asked, not who sat at it.
- **S-32 (Medium; Repudiation, Elevation).** M12's console role gets INSERT on `audit_events` so
  memory edits can be audited. Whoever holds that credential can insert rows that look like the
  runtime's own (`actor`, `action`, `detail` are free columns, `memory.py:34-37`). **Resolution
  (M12):** the new `runtime/adk` version adds a column the database fills (`db_role TEXT NOT NULL
  DEFAULT current_user`, the INSERT grant excluding that column, or a trigger that sets it), so
  console-role rows are distinguishable from runtime rows; the console shows it. Medium as "a
  control the plan relies on that is missing": §8.3 and M16 rely on `audit_events` to attribute
  memory edits, and without this column the attribution does not hold.

**Memory (M16).**

- **S-07 (Medium; Tampering).** Long-term notes are put into the model's instructions (runtime
  `agent.py:192-206`), only with `<`/`>` neutralised. A console note edit changes the agent's
  behaviour for that customer without the eval gate, and notes the agent saved from customer
  conversation are hostile text in the console. **Resolution (M16):** the editor says "notes are
  added to this customer's prompt"; per-note cap (proposal 2 000 characters) and control characters
  stripped on write; display through S-35's component; the audit row (hashes, not text).
- **S-19 (Low; Information disclosure).** `memory forget` deletes notes, sessions, turns and
  tickets, but `audit_events` is append-only for the application role (`memory.py:39-40`) and keeps
  customer ids and runtime-written details; Phoenix traces, eval reports and console job logs may
  also hold content. **Resolution (M16 docs):** list what `forget` does not delete; never called
  erasure.

**Knowledge upload (M14).**

- **S-13 (Medium; DoS, Tampering, Information disclosure).** **Resolution (M14):** the streamed
  `PUT` goes through S-03's guards; a per-kind cap and a free-disk check before writing; file names
  validated (one path component, NFC, no leading dot, no control or bidi characters, an extension
  allow-list); archives (zip, tar) refused, never unpacked; written into a private temp version
  directory, never into the registry tree directly; file content is returned only inside JSON
  (with `nosniff`, `center/server.py:54`), never served as a raw response; documents are steering
  text (S-05); the warning that they land in a git-tracked registry adds "do not upload customer
  data".

**Identity and policy editing (M18).**

- **S-20 (Low; Information disclosure).** Credentials in the operator config are `env:` or `file:`
  references (`operator_config.py:437-450`); a `file:` reference can name any 0600 file the owner
  has, and the provider URL is editable, so an edit can send a private file's contents to another
  host. Low: only a session holder can do it, who already has code execution (I1).
  **Resolution (M18):** the save diff calls out every changed credential reference and provider
  URL, and saving them needs the typed confirm; §8.6's separation-of-duties note stays.

**Files at rest, errors, shutdown (M0a).**

- **S-16 (Low).** Key env files (`center/jobs.py:308-316`) are removed only when the pump ends
  (`:326-328`); a crash leaves provider keys in a 0600 file; editor temp files (§5) likewise.
  **Resolution (M0a):** at start, only after the single-instance `flock` is acquired (so a
  second `up` never deletes a running console's files), `up` deletes `env-*` and editor temp files
  in the center directory.
- **S-17 (Low).** `Runner.read` returns the last stderr line of a failed read to the browser
  (`center/jobs.py:362-364`). **Resolution (M0a):** the envelope's `error` carries the CLI's
  `[CODE]` and message from `--json` errors; free stderr is not forwarded.
- **S-18 (Low; DoS).** `Runner.cancel` signals only the CLI child (`center/jobs.py:338-344`).
  **Resolution (M0a):** keep SIGINT to the child for graceful release and promote steps; shutdown
  sends SIGTERM to each child's process group after a grace period.

**Notifications and the registry browser (M11, M8).**

- **S-26 (Info).** Stored events carry fixed templates, exit codes, `[CODE]`s matching
  `\[[A-Z0-9_]{3,40}\]` and capped case ids, never free text from logs, stderr or models (guided
  §14.4, R8; plan M11). Nothing to add.
- **S-27 (Info).** Plan M8 already routes `registry show` through `Ref.parse`, `resolve` and
  `_entries` with `O_NOFOLLOW`, size caps and control/bidi stripping of content. The M8 task line
  adds the same stripping for **file names** in the listing (registry rejects non-UTF-8 names but
  not bidi characters in them).

**Not reviewed here.**

- **S-34 (Info).** M15 edits grants and allows `services-up` for MCP stacks (which the center
  refuses today, `center/server.py:234-238`); M17 writes projects from a JSON spec. Each gets a
  security delta review task on its issue.

### 10.5 Owner decisions (2026-11-11)

All five decided by the owner as proposed:

1. S-01: **refuse** registration on wrong ownership or mode (not warn).
2. S-05: **yes**, `mcp` and `runtime` are code-bearing.
3. S-14: **accept** the global backoff lockout with the terminal-link recovery.
4. S-12(b): **yes**, no Markdown or auto-linking of agent output in a3.
5. S-29: **refuse** to scan past a taken preferred port once a password exists. This amends plan
   M0a's task and acceptance: the 8080–8099 scan runs only in first-run state.

### 10.6 Review log

- **r1:** written by Claude Opus 5.5.
- **Round 1 review, Claude Fable 5.2: CHANGES REQUESTED.** Each point checked against the code by
  the author. Applied: (1) S-02 verification now sends the `Host`, API, `Origin` and content-type
  headers (a bare `curl` gets 403 from `_host_ok`, `center/server.py:269-270`); Linux recorded as
  by design, not run. (2) New S-28 (first password needs a link-minted session). (3) New S-29 (port
  squatting; `make_server` already falls back to another port, `center/server.py:392-404`). (4)
  Surface × STRIDE matrix (§10.2); new S-30, S-31, S-32; "append-only for the application role"
  wording (I4). (5) S-01 lowered to Medium with its precondition, the circular vector dropped,
  sticky ancestors allowed, the macOS "Ignore ownership" behaviour stated. (6) S-10 CLI tasks on M5
  (lane A). (7) S-12(a) extended to notes, eval cases and policy text; Try it turns are in-memory
  reads (`center/jobs.py:346-370`). (8) S-04 extended to `set-ref` and policy save with
  `--expect-base`; M12 added. (9) New S-33 (dependency re-lock). (11) New S-35 (shared text
  component, M0b). (12) Scope paragraph and S-34. (13) S-05/S-20 reasoning aligned; the M9a line
  adds only `mcp` and `runtime`. (14) Citations corrected. (15) S-08 and S-11 separate existing
  from planned commands (`cli.py:488-490`). (16) S-26 and S-27 detail added. (17) The `cluster`
  flock moved to M3, since console cluster jobs exist from M0a. (18) S-13 states JSON-only serving
  and refused archives.
  Applied in part: (10) checked on `f7b521f`: no CLI child inherits stdio (Docker runs capture
  output, `runtime.py:38-45`; compose one-shots pipe or discard, `services/compose.py:346-351`),
  so the bypass does not exist today; kept as a guard test on M3 plus the server-side pump as a
  second pass.
  Rejected: none.
- **Round 2 review, Claude Fable 5.2: APPROVED** (point-10 counter-claim confirmed). Last items,
  each checked against the code and plan by the author, all applied: S-29 and §10.5 #5 state that
  they amend plan M0a's port-scan task and acceptance (`plan:105`, `:107`); S-12(a) specifies a
  Try it read variant (`input=`, `cache=False`, no stale fallback, 180 s), since `Runner.read` uses
  `stdin=DEVNULL` and a stale-answer cache (`center/jobs.py:354`, `:361-364`); S-32 keeps Medium
  with its justification; S-28 states the window is not a narrow race; S-30 names the audit
  writers (`deployment.py`, `promote.py`, `egress/host.py`); S-16 deletes leftovers only after the
  single-instance `flock`. Different-model approval recorded.
- **Owner (2026-11-11):** decided §10.5 #1–#5 as proposed.
