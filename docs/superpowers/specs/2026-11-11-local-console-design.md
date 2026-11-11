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
