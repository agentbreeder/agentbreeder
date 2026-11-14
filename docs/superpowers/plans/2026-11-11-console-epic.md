# Master plan: AgentBreeder Console and guided path (epic #812)

Status: r3, 2026-11-11 (r1 and r2 reviewed by Fable; findings applied). Plan of record for epic
#812. **Precedence: where this plan differs from console spec §4–§8 or guided-path spec §9/§14,
this plan wins**; it supersedes console §6, §7, §8.7 and guided §14.1 entirely.
`2026-11-11-guided-path.md` is kept as an **appendix**: superseded for scope and order, but its
per-task files and tests (tasks 1.2–1.10, 2.1–2.8) remain valid detail for M3–M5, M7, M8 and M11.

Terminology: **G-F1…G-F12** are the guided-path friction points (guided §1); **A-F1…A-F17** are
the architect review findings (console §8). "Console" replaces "command center" in UI, CLI help
and docs (renamed in M0b).

## Owner decisions (2026-11-11)

1. One large epic, executed milestone by milestone; each part tested on its own before the next.
2. **One published preview at the end** (milestone R). Milestones are verified from a wheel built
   from `main`; R is verified from PyPI and npm.
3. **Docs in every PR**: `docs/` reference and `CHANGELOG.md` `[Unreleased]`, each unreleased
   feature marked "on `main`, not in a published preview". **README, `website/`, what-ships and
   `CLAUDE.md`'s "what ships" change only at R.** Exemption: M0c changes design tokens only (no
   visual or claim change; the site stays forced dark).
4. Every milestone ships CLI tests and UI tests (Playwright; Chrome click-through as evidence).
5. Sign-in: password set on first run (`admin@localhost`), no shared default; header-bearer token,
   no cookie (A-F1).
6. Short-term memory (ADK sessions): view and delete; long-term notes: full edit.
7. People RBAC/ABAC and roles: after this epic, in #742/#775 (not a milestone here).
8. Frontend: Preact + htm as one vendored standalone module, no build step.

## How every milestone is run

1. Spec delta (if any) → different-model review → plan delta. Milestones with their own spec
   (M9b, M13, M19) also get architecture, UX and `/security` design reviews.
2. **Tests first.** CLI: pytest unit + CLI end-to-end (`python -m agentbreeder …` subprocess
   against fixtures; real Docker behind `AGENTBREEDER_DOCKER_TEST=1`, run without `--cov` until
   #732 is fixed). UI: `node --test` for pure modules; Playwright (pytest-playwright, `tests/ui/`,
   marker-gated, one CI job on one Python, axe in a CSP-bypass context plus a separate
   CSP/Trusted-Types violation test) against a wheel-installed server started by a test launcher
   with a fake CLI runner and fixture projects.
3. Small PRs; each: different-model review until approved, the quality gate, owner approval.
   Schema, flag or `agent.lock` changes: grep `agentbreeder-cloud`, update the JSON Schema and
   Cloud `CLAUDE.md` (cross-repo sync).
4. **Milestone verification** from a wheel built from `main` in a clean sandbox (fresh `HOME`,
   empty caches): the acceptance script, timings, leak scan, teardown to zero, and a **Chrome
   click-through** with the Claude in Chrome extension (GIF attached to the issue, not committed,
   checked for home paths and keys). Flows needing a model key run here, not in CI. Recorded in
   `docs/superpowers/verification/<milestone>.md`.
5. A milestone starts when its dependencies are merged and verified.

## Parallel lanes (separate harnesses such as Codex or Cursor)

Each lane works in its own git worktree and branch. A lane "owns" a hot file while its milestone
is open; another lane edits it only after that merge, or through the owner's review.

| Lane | Milestones | Owns |
|---|---|---|
| **A: CLI / engine** | M3, M4 (CLI), M5 (CLI), M6 (usage CLI), M7 (`list --all`), M11 (events), M13 | `release.py`, `build.py`, `store.py`, `runtime.py`, `deployment.py`, `services/**`, `npm/bin/agentbreeder.js`; `lock.py` until M9b, then lane C |
| **B: console** | M0a, M0b, M2 (UI), then every Console section | `center/**` (M0b leaves one file per screen plus a one-line route and nav registration, so M14/M15 screens can be added in parallel) |
| **C: registry** | M8 (CLI), M9a, M10 (CLI), M9b | `registry.py`, `registry` command modules, `lock.py` from M9b |
| **D: data** | M12 (role), M16 (CLI) | runtime memory code, the new `postgres-pgvector` and `runtime/adk` versions, stack templates |
| **E: governance** | M18 (CLI) | `operator_config.py`, `policy/` |
| **F: design** | M0c | `website/brand.css`, `center/static/brand.css` snapshot (by agreement with lane B) |

**Day one:** M-pre first (M3 and the M8 CLI branch after it merges, the same day), in parallel with S, M3 (A), M8 CLI (C), M0c (F), and the
M9b and M19 specs. M0a's tasks not touched by S start once M-pre merges.

## Milestones

Each section below becomes one GitHub issue (title, dependencies, lanes, spec map, tasks grouped
by lane, acceptance, type). Types decide the common checklist: `cli` (no UI tests), `full`,
`design`, `review`, `release`.

### M-pre: Split `cli.py` command registration into per-command modules
- Depends: none
- Type: cli
- Lanes: A (mechanical refactor; M3 and the M8 CLI branch after it merges, the same day)
- Spec: none (refactor)
- Tasks:
  - CLI: Move each command's parser setup and handler into `src/agentbreeder/commands/<name>.py`; `cli.py` keeps the entry point, shared helpers (`_message`, output) and a registry of command modules
  - CLI: No behaviour change: every existing CLI test passes unchanged; help output identical (golden test)
- Acceptance: `agentbreeder --help` and every subcommand's `--help` are byte-identical to before; full test suite green; `cli.py` under 300 lines.

### S: Security design review of the console
- Depends: none
- Type: review
- Lanes: any (review only)
- Spec: console §4, §5, §8 (all), guided §14.3–§14.4
- Tasks:
  - Review: password auth and session token (M2), memory edits (M16), tool-code edits (M12), policy edits (M18), registry import and onboarding of code-bearing kinds (M9a, M7), knowledge upload (M14), Try it (M7), the log viewer and redaction (M5), long-lived children (M5, M6, M11), lock scopes (M0a)
  - Review: Record findings and decisions in a new console spec §10 "Security review"; different-model reviewer plus owner approval
- Acceptance: console spec §10 lists every finding with its resolution or the milestone that carries it; the reviewer approves and the owner signs off.

### M0a: Console backend foundation
- Depends: M-pre (S findings folded in when available)
- Type: full
- Lanes: B
- Spec: console §8.1 #2–#7, §8.8; guided §14.2 (read-only opens), §14.9 R7
- Tasks:
  - Console: Route table with typed handlers and a `{data, error, stale}` envelope, replacing the regex chains in `center/server.py`
  - Console: Lock scopes. Machine: `cluster up/down`, promote to kind. Project: build, release, services, `agent set-ref`. Policy save refused while any engine job runs. Registry publish/import and memory edits independent. Short-write timeouts per kind (60 s default; 300 s for tool publish with dependency re-lock)
  - Console: Several running jobs: overview, activity, cancel and shutdown handle a list; shutdown kills every child (jobs, follows, watch, usage)
  - Console: Single-instance `flock` in the center directory (a second `up` prints the running URL and exits); `up --set-password`/`--reset-password` are exempt
  - Console: `up` re-execs isolated (`-I`), keeping the TTY for the Enter handler; imports eagerly at start
  - Console: In-process read layer (registry, lock, agentfile, used-by) with a `sys.modules` guard test forbidding state, secret, Docker, operator-config and audit modules in the server process
  - Console: `center.sqlite3` `schema_version` table and migrations; the `CenterState` docstring updated (holds password hashes, never a secret value)
  - Console: Port scan 8080–8099 with the URL printed; one host form (`127.0.0.1`); CHANGELOG note that a2 used 8900
  - Console: Per-route body caps (JSON 64 KiB; editor payloads up to 1 MiB); static allow-list built at start from the package tree, with `.woff2` MIME
- Acceptance: existing UI and `tests/test_center.py` stay green; two projects run engine jobs concurrently while two cluster jobs serialise machine-wide; a second `up` exits with the running URL; the port scan picks 8081 when 8080 is taken; an oversized body gets 413; starting with a2 state migrates `center.sqlite3` and keeps projects and job history; `python -I` confirmed in the process list.

### M0b: Console shell, with the walkthrough fixes
- Depends: M0a (uses the a2 tokens until M0c lands, then re-snapshots)
- Type: full
- Lanes: B
- Spec: console §8.5; guided §14.8; #811 items 1, 2, 3, 5, 8, 9
- Tasks:
  - Console: Preact + htm as one standalone module at an exact version (source URL, upstream hash, SHA-256 test, MIT notice in the wheel)
  - Console: CSP `default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; frame-ancestors 'none'; trusted-types 'none'; require-trusted-types-for 'script'`; a spike test asserting zero CSP violations
  - Console: Source guard outside `vendor/`: no innerHTML, outerHTML, insertAdjacentHTML, document.write, dangerouslySetInnerHTML, srcdoc, eval, new Function, createContextualFragment, DOMParser, or string timers; a test that no static file references an external origin
  - Console: Hash router (`#t=` token consumed first; re-auth returns to the route; per-route title; focus to the `h1`; skip link)
  - Console: App shell. Sidebar of nouns with entries only for screens that work; top bar with the scope switcher ("All projects" or one), activity pill and dock for several jobs, user menu; "console not reachable at this address" versus "session ended" states
  - Console: Port every a2 screen with the walkthrough fixes built in: audit time from `at`; Verify in words; kind guidance with the install command and "restart `up`"; a reason under every disabled control; confirms focus Cancel and return focus; the job log appends instead of rebuilding; screen-scoped notices and a collapsing finished job; "internal only" ports; candidates violet and labelled temporary; Northwind's cost figures only for Northwind
  - Console: Rename "command center" to "console" in the UI, CLI help and `docs/`
  - Console: Recursive `center/static/**` package data and a packaging test
  - Console: Test harnesses: `node --test` in CI; Playwright (pytest-playwright) job with the fake-runner test launcher and fixture projects; `docs/superpowers/verification/` template
- Acceptance: every a2 capability (onboard Northwind, services, release, deployments, kind, audit) works in the new shell through the API and in a browser; the owner re-walks the screens from the 2026-11-11 screenshots; both harnesses green in CI; the wheel contains every static file.

### M0c: Brand tokens
- Depends: none
- Type: design
- Lanes: F
- Spec: console §8.5 (visual)
- Tasks:
  - Design: `website/brand.css` `:root` light palette; text-muted token passing AA (`#a1a1aa` on dark); 3:1 control-border token; success, warning and info tokens (token changes only; the site stays forced dark)
  - Design: Console snapshot `center/static/brand.css` and a new OSS drift test between the two
  - Design: Cloud companion PR re-vendoring the tokens (its drift check green)
  - Design: Geist and Geist Mono woff2 (OFL notices) prepared for `center/static/fonts/`
- Acceptance: the website looks unchanged in dark (visual check of the home and docs pages); the light palette passes contrast checks; OSS and Cloud drift tests green.

### M2: Sign-in
- Depends: M0a (UI: M0b)
- Type: full
- Lanes: B (CLI password commands may run in parallel after M0a)
- Spec: console §4.1 (cookie struck), §8.1 #1, §8.2; guided §14.9 R7
- Tasks:
  - CLI: `up --set-password` and `up --reset-password` read from a TTY or stdin, never argv; password policy at least 12 characters plus a common-password list
  - Console: Users in `center.sqlite3`; scrypt with explicit `n=2**15, r=8, p=1, maxmem` stored per row; constant-time compare; a dummy hash for unknown emails
  - Console: `POST /api/session {email, password}` mints the header token (sessionStorage); no cookie
  - Console: Backoff checked before hashing, global, capped at 5 minutes; hashing single-flight
  - Console: Credential version re-read per request: a reset ends all sessions, even while running; a restart signs everyone out
  - Console: Once a password exists, `up` opens the sign-in page; the one-time link only on demand (Enter or `up --link`)
  - Console: `localhost:<port>` redirects to `127.0.0.1:<port>` on GET of static paths only
  - Console: Settings (change password, sign out everywhere, session length, theme); re-auth dialog over the page; drafts in sessionStorage; polls do not refresh the idle timer
  - Console: a2 upgrade path (existing state, no password, goes to setup)
  - Docs: what the password protects (other users and browsers) and what it does not (your own OS account)
- Acceptance: fresh state reaches setup, then sign-in; a2 state reaches setup with projects kept; the sixth wrong password waits and the wait never exceeds 5 minutes; `--reset-password` while running ends open sessions; a 401 during an edit keeps the draft and returns to the route.

### M3: CLI hints, messages, release evidence, logs core
- Depends: M-pre
- Type: cli
- Lanes: A
- Spec: guided §1, §9.2 #2, #5, #9, #11, #12, §14.2, §14.3 #1–#3, §14.9 R2, R3, R6; appendix tasks 1.2–1.6
- Tasks:
  - CLI: `launcher.py` decides the printed command form: bare `agentbreeder` when it resolves to this interpreter; `npx agentbreeder@<ver>` when `AGENTBREEDER_LAUNCHER=npx` (enum) and `AGENTBREEDER_LAUNCHER_VERSION` (strict semver incl. `3.0.0-alpha.N`) are set by `npm/bin/agentbreeder.js` in the spawn env; uvx when `sys.prefix` is under uv's cache; else `python -m agentbreeder`. Tests in `tests/` and `npm/test/`
  - CLI: Every `Next:` line goes through one hint helper; `build` points at `release` or `next`, never a production `deploy`; `services up` prints an absolute agent path
  - CLI: Messages in `cli._message`: a `--stack` hint when `CONTEXT_ENVIRONMENT_UNKNOWN` or `MODEL_ACCESS_UNCONFIGURED` fires and `stack.yaml` sits beside the agent file or one level above a manifest (G-F1, G-F2); REF_UNPINNED on release suggests `registry publish` (G-F4); LOCK_STALE says "run `agentbreeder build`" (G-F5, `lock.py:161`); a failed gate lists each failed case (id up to 80 characters, failed checks, judge reason up to 200, control characters stripped) (G-F6); the demo-identity explanation only when the stack's generated config was selected (G-F9)
  - CLI: Read-only state opens (`mode=ro`, no mkdir, no migration) for `list`, `next`, `logs`; an old schema reports "run `agentbreeder migrate` to upgrade" (a new command that only migrates the local state; `status` re-verifies at the identity provider and must not be used); tests for a held write lock and a hot journal
  - CLI: G-F3 release evidence: move `_evidence` to a neutral module taking a store; "already in production" only with a matching `release/promoted` audit row; otherwise warn and evaluate the same image as a candidate, record on pass, exit 5 on fail with advice that works (`remove ID`; `--rollback` only when a journal exists)
  - CLI: `Docker.logs(info, tail, since)`: stderr merged, `--timestamps`, 4 MiB cap, by container ID, after authorize and inspect
  - CLI: `redaction.py`: pure `redact(text, values)` (literal, each line of a multi-line value at least 8 characters, base64, URL-quoted, JSON-escaped, URL passwords) shared by CLI and console; CLI-only `redaction_values(state_dir, stack=None)` covering stack secrets, every non-removed deployment's 0444 secret copies (new reader: O_NOFOLLOW, owner and 0700-parent checks) and the `<state>/secrets` fallback, in every environment
  - CLI: Failed-candidate log tail: captured before the candidate is removed on every release, 500 lines, redacted then truncated, mode 0600, one per agent, discarded on pass; `agentbreeder logs --failed NAME` prints it
  - CLI: `doctor` advisory check that the `agentbreeder` on PATH is this version (#805)
  - Docs: `docs/commands.md`, `docs/agent-yaml.md` release section, CHANGELOG
- Acceptance: repeating the guided-path §1 hand-run (fresh `HOME`, starter files from `your-own-agent.mdx`), G-F1–G-F6, G-F9 and G-F10 no longer occur or print a message naming the fix; a hand-deployed production makes `release` evaluate it; a broken prompt's failed release prints its failed cases and `logs --failed hello` shows the redacted tail; the leak scan of all output is clean.

### M4: Starter agent, invoke, next, console checklist
- Depends: M3 (Console: M0b)
- Type: full
- Lanes: A (CLI), B (Console); the `next --json` contract is fixed first
- Spec: guided §3, §9.2 #4, #6–#9, §14.2; appendix tasks 1.7–1.10; #769
- Tasks:
  - CLI: `next --json` schema written and reviewed first (steps, states, reasons, fix data, `complete`)
  - CLI: `new agent NAME --model gemini [--dir DIR] [--dry-run --json]` (#769): templates as string constants in `newagent.py`; writes `agent.yaml` (runtime/adk@1.0.6, `traces: none`, `agent_events: none`), `stack.yaml` (postgres + litellm, judge alias, `DATABASE_URL`, `candidate: true`, `review_by` today + 180 days), `stack/litellm/config.yaml`, `src/prompt/`, `src/evals/` (the two verified cases, `retries: 1`), publishes `prompt/NAME-prompt@0.1.0` and `eval/NAME-evals@0.1.0`, and writes `agent.lock` so `build --locked` passes; refuses non-empty dirs and symlinks; `--model` required, only `gemini` offered
  - CLI: `invoke NAME MESSAGE|-`: loopback `http://127.0.0.1:<port>` only, Kubernetes rows refused ("use `forward`"), no proxy, no redirects, the container still owns the port, 180 s timeout, reply stripped; `--customer`/`--session` with a "not authenticated (#780)" note
  - CLI: `guide.py` + `next [--json]`: steps from guided §3 / §9.2 #4 (prerequisites advisory, project validates and refs resolve, keys from the LiteLLM config, services via one `compose ps`, build current via `build.json` and the lock, released via audit evidence, try it); the Kubernetes step only when configured; text exits 1 when steps remain, `--json` always 0; call-count test (one `compose ps`, no `preview`, no MCP probe); tree-snapshot no-write test
  - CLI: `quickstart` ends with `Next: <launcher> new agent hello --model gemini`; `scripts/install_smoke.py` also runs `next --json` and `new agent --dry-run --json`
  - Console: Next pill, Home checklist, empty-state primary buttons and finished-job result rows from `next --json`; a stale lock shows the CLI instruction instead of a button
  - Console: Create starter action (name only; fixed destination `~/agentbreeder-projects/NAME`; `new agent` run through a CLI read with `--json` errors; then registered)
  - Docs: `docs/your-first-agent.md` built from the guide's steps, with a drift test matching subcommand tails; `docs/commands.md`; CHANGELOG
- Acceptance: from a fresh `HOME` with the wheel, `agentbreeder quickstart` and then only the printed `Next:` lines reach a released starter agent and an `invoke` reply; the same path in the console from Create starter to the first reply; `next --json` matches the console checklist at every step.

### M5: Logs and the watch core
- Depends: M3 (Console: M0b)
- Type: full
- Lanes: A (CLI), B (Console)
- Spec: guided §10, §14.3, §14.4 (watch), §14.9 R1, R5, R7; appendix tasks 2.1–2.3
- Tasks:
  - CLI: `logs ID [--tail N] [--since D] [--follow] [--json] [--stack DIR]` for local deployments (M3's `Docker.logs`)
  - CLI: Kind deployments: `kubectl logs -l io.agentbreeder/deployment=<id> -c agent` through the recorded binding after `inspect`; `--previous` for crash loops; a pending pod returns no lines plus its reason; sidecar and egress containers as labelled sources
  - CLI: `services logs` gains `--since`, `--follow`, `--json` (same shape as `logs`)
  - CLI: Log-level parser keyed by built-in service ref (postgres incl. FATAL/PANIC, litellm, phoenix, runtime/adk), continuation lines grouped, fixtures captured from a Northwind run; `--level` filter
  - CLI: `watch --stack DIR --json`: docker events filtered by the stack label and matched to this stack's deployment IDs; `die` is an event only without a preceding `kill`/`stop`/`destroy` and never for a container the running job touches; 60 s resync with one `compose ps` plus a pod check; no stored events yet
  - Console: Log viewer: virtualised, follow and pause with "Jump to latest", text filter, level filter, search, wrap, copy, redaction and truncation notes
  - Console: One pumped child per open log and one `watch` per project, capped in number, killed 15 s after the last poll and on shutdown; reads with `cache=False`
- Acceptance: logs for every Northwind service and both deployments (local and kind) in the CLI and the console; following during a release shows lines live; `watch` reports a killed service and nothing for a clean release or `services down`; the leak scan of API output is clean.

### M6: Home and usage
- Depends: M5
- Type: full
- Lanes: A (usage CLI), B (Console)
- Spec: console §4.2, §8.1 #6
- Tasks:
  - CLI: `services usage --follow --json`: one streaming `docker stats` over container IDs from `watch`; disk every 60 s from `docker system df -v`, cached, with an "as of" time
  - CLI: `services restart|stop|start NAME` (new per-service commands)
  - Console: Home table across projects: status chips (icon, word, colour), ports with "internal only" explained, usage tiles and sparklines from a 5-minute in-memory ring (text value and aria-label each), CPU and memory labelled "of the Docker VM"
  - Console: Service detail panel (Logs, Usage, Config) with start, stop, restart
  - Console: Docker-down page blocker with the `doctor` remedy and Retry; port-busy errors name the port
- Acceptance: with Northwind running, Home shows each of its 5 services with CPU, memory, network and block IO updating within 10 s; stopping Docker shows the blocker within 15 s and starting it again clears it without a reload; `services restart litellm` restarts only that container.

### M7: Agents and Try it
- Depends: M4, M5
- Type: full
- Lanes: A (`list --all`, stack meta), B (Console)
- Spec: console §4.5, §8.5; guided §14.5
- Tasks:
  - CLI: `services up` records the project path in stack meta; `list --all [--json]` reads every stack read-only (validated directory names, no symlinks, `AGENTBREEDER_STACKS_DIR`, an unsupported schema reported and skipped)
  - CLI: `new agent NAME --stack-for agent.yaml` writes a matching `stack.yaml` for an existing `agent.yaml` (onboarding an `agent.yaml` alone)
  - Console: Agents list (production and candidates in violet, target, state, digest, gated, endpoint, last release, identity "as of last reconcile")
  - Console: Agent detail with the gate strip (Build, Candidate, Evals n/N, Identity, Production) and tabs Overview, Try it, Releases, Logs
  - Console: Try it chat: production by default, candidates excluded, per-turn inspector (tools, sources, latency, tokens, trace link), notes on cost and the unauthenticated endpoint; onboarding of a code-bearing project uses the three-tier confirm
- Acceptance: Northwind and a starter agent are both listed with correct state; a chat message on Northwind production returns a reply with its tools and sources; onboarding an `agent.yaml` alone produces a project that releases.

### M8: Registry browser
- Depends: CLI: M-pre; server reads: M0a; Console: M0b
- Type: full
- Lanes: C (CLI), B (Console)
- Spec: console §4.3, §8.4; guided §14.6
- Tasks:
  - CLI: `registry show REF [--json]` through `Ref.parse`, `resolve` and `_entries` (O_NOFOLLOW, size caps, control and bidi characters stripped)
  - CLI: `registry diff REF REF [--json]` (ordered, with context)
  - CLI: `registry used-by REF --project DIR… [--json]` reporting declared (`agent.yaml`), locked (`agent.lock`) and deployed (running image) separately
  - Console: Registry list per kind (prompt, eval, tool, knowledge, mcp, service, runtime): name, latest, versions, root, verify status, used by; roots in use shown
  - Console: Detail with version timeline, diff between any two versions and used-by; in-process cached reads, `verify` off the request thread
  - Console: Audit view: agent, enforcement mode and reason codes per row, paging past 50, links to the eval report and the production endpoint
- Acceptance: Northwind's prompt, eval, tool, knowledge and service versions and every built-in are listed; the diff of `prompt/northwind-support` 1.0.0 → 1.0.1 equals `diff` of the files; used-by for `tool/orders@1.0.0` shows Northwind as declared, locked and deployed.

### M9a: Import and fork
- Depends: M8
- Type: full
- Lanes: C (CLI), B (Console)
- Spec: console §4.3 (onboard from another location), §8.4, §8.5 (three-tier confirm)
- Tasks:
  - CLI: `registry import PATH [--as NAME] [--version X.Y.Z]` with the same checks as `publish`
  - CLI: `registry fork REF [--as NAME]` copies a built-in or path-root version into the project registry as a new version
  - Console: Import by path and fork actions; the three-tier confirm ("this code runs in agents") for tool, service and eval imports
- Acceptance: importing a prompt from another directory makes it resolvable; forking a built-in service version gives an editable project copy; a tool import asks for the code-execution confirm.

### M10: Prompt and eval editing
- Depends: M9a, M3
- Type: full
- Lanes: C (CLI), B (Console)
- Spec: console §4.3, §8.4
- Tasks:
  - CLI: Edit flow: copy version N to a private temp dir, apply, bump (patch, minor or major), `publish` atomically; a rename failure reports "version exists" (two tabs)
  - CLI: `agent set-ref OLD NEW [--json]`: exact-string replacement in `agent.yaml`, refusing ambiguity, printing the diff first
  - Console: Prompt editor (mono textarea with gutter, server diff, dirty guard, Cmd/Ctrl+S opens review, "Save as X.Y.Z"); "Move agent to this version" (set-ref, then build and release)
  - Console: Eval-set case editor (id, turns, checks, reference) with the same save flow
  - Console: Gate-failed view: failed-case table (id, check, expected and actual, judge reason), edit links to the prompt and the case, the saved candidate tail
- Acceptance: editing the starter's prompt saves 0.1.1, moving the agent to it and releasing passes; breaking an eval case blocks the release and the console shows the failing case with its reason and tail.

### M11: Notifications
- Depends: M5, M4
- Type: full
- Lanes: A (events), B (Console)
- Spec: guided §11, §14.4, §14.9 R1, R5, R7, R8
- Tasks:
  - CLI: Stored events (newest 500) from `watch` and jobs: fixed templates, exit codes, error codes matching `\[[A-Z0-9_]{3,40}\]`, eval-gate case ids and counts; no free text from logs, stderr or models
  - CLI: Conditions computed by the same function as `next`, never stored
  - Console: Bell (count of conditions), panel with "Needs attention" and "Recent events", failure toasts; the eval report path as text with a fixed-argv summary read
- Acceptance: a clean Northwind release and a `services down` store zero events; killing a service stores one event and raises a condition that clears after restart.

### M12: Tools, and the read-only console role
- Depends: M10, M4; and lane A writing host-form `agent-database`/`console-database` secrets at `services up` (M12 delta §3.5)
- Type: full
- Lanes: C (role, versions and CLI; lane D folded into C), A (the `services up` secrets), B (Console)
- Spec: console §4.3, §8.3
- Tasks:
  - Data: New `service/postgres-pgvector@1.2.0` and `runtime/adk@1.2.0`; one version each carries what M12, M14 and M16 need, so M14 and M16 add no new service version. Decided in the M12 spec delta (`docs/superpowers/specs/2026-11-13-m12-tools-console-role-design.md` §3): one login role `agentbreeder_console` per stack (`console_database` param, `operator_only` output), granted **only on tables the database owner made** (`turns`, `audit_events`, `notes`, `tickets`, incl. DELETE on `turns`/`tickets` for M16's `forget`) by the owner's `init_sql` with guarded plain `GRANT`s, never `ALTER DEFAULT PRIVILEGES`; agent-owned objects (session and knowledge tables, everything in the starter, where the agent owns the database) are read with the agent's own credential, so the runtime grants nothing (a console grant on them would lend its rights to agent-planted triggers). The starter template and Northwind are relocked (Northwind with `console_database: support`); `services up --update` applies an existing stack's changed `init_sql`
  - CLI: `tool log REF --project DIR [--since] [--agent NAME] [--limit N] [--content] [--json]` reads `audit_events` (one row per tool call) joined to `turns` (console role on owner-managed stacks, else the agent's credential; M12 delta §4; `turns.tools` holds function names only), time-windowed with a LIMIT, default columns time, agent, version, function, status, latency, turn id, trace id; arguments and error text only with `--content` (results are not stored; the trace holds them)
  - CLI: Tool checks (`TOOL_INVALID`: allowed files only, no shadowing of stdlib/runtime/locked packages, functions resolve, `ast.parse` only) and `registry show` tool `functions` with `ast` signatures (delta §5)
  - Data: S-32 as a `BEFORE INSERT` trigger setting `audit_events.db_role` to `session_user` (nullable; older rows stay NULL); `runtime/adk@1.2.0` also writes `detail.tool` on errors, `detail.agent` and `detail.latency_ms` (delta §3.4, §4.5)
  - Console: Tool detail (docs from `tool.yaml`, function signatures, source, "no dependencies of its own; the runtime's locked packages", grants)
  - Console: Tool code editor with the three-tier confirm ("this code runs in agents"; the eval gate is a quality gate, not a security review); save as a new version
  - Console: Execution log tab (customer content behind the customer-data marker); on an owner-managed stack without the console role, unavailable with the reason
- Acceptance: editing Northwind's `tool/orders` (project-owned; a built-in tool is forked first, covered by a CLI end-to-end test) and moving Northwind to the new version publishes and releases; its execution log lists recent calls without arguments by default; Northwind still passes 61/61 after the relock.

### M9b: Remote registry sources
- Depends: M10 (lane order, not a technical dependency)
- Type: full
- Lanes: C
- Spec: own spec to write (standard library, organization registry repo, agent repos, remote sources), with architecture, UX and `/security` reviews
- Tasks:
  - Spec: remote sources, cache, trust and pinning, the `agent.lock` format change, cross-repo sync
  - CLI: `registries:` sources (git URL at a commit, OCI, HTTP index) fetched into a cache and pinned by hash in `agent.lock`; REF_CONFLICT across sources
  - CLI: Standard-library additions, each run end to end before it ships
  - Console: Sources shown per version in the registry browser
- Acceptance: a tool from a separate git repo builds into an agent; changing the remote does not change a locked build; an unreachable source fails with a clear message.

### M13: Agent package (reuses #763)
- Depends: M3, M9b
- Type: cli
- Lanes: A
- Spec: own spec designing the `agent` kind once, including A2A's reference needs (M19); #762, #763
- Tasks:
  - Spec: package format (lock, image digest, eval result, manifest), the `agent` kind, OCI artifact layout
  - CLI: `release` writes `agent/<name>@X.Y.Z`
  - CLI: `deploy agent/<name>@X.Y.Z`; an OCI artifact next to the image where a registry exists (kind's `127.0.0.1:5001`); the local target records the image ID
- Acceptance: a release writes a package; deploying it by ref on kind reproduces the same digest; the local-target behaviour is documented.

### M14: Knowledge (RAG)
- Depends: M10, M12
- Type: full
- Lanes: C (CLI; no grants, M12 delta §3.1), B (Console)
- Spec: console §4.3; #766 for lineage alignment
- Tasks:
  - Data: Read knowledge tables with the agent's own credential (M12 delta §3.1; no grant, no new service version)
  - CLI: `knowledge status REF --project DIR [--json]`: documents, chunk count and freshness per agent DB, embedding model and dimensions
  - CLI: Add or edit documents by path through the M10 edit flow; re-index at the next release
  - Console: Knowledge list and detail; browser upload as a streamed `PUT` with a per-kind cap; a warning that documents land in a git-tracked registry
- Acceptance: adding a document and releasing makes retrieval return it; status shows the new chunk count; Northwind still passes 61/61.

### M15: MCP
- Depends: M8, M4
- Type: full
- Lanes: C (CLI), B (Console)
- Spec: console §4.3
- Tasks:
  - CLI: `mcp grant|revoke AGENT SERVER TOOL… [--json]` editing the stack's grants
  - Console: Allow `services-up` for stacks with MCP servers through the existing review (`--repin` for changed tool pins)
  - Console: Grants per agent, tool pins, relay status
  - Fixture: a verified MCP fixture project (starter + `mcp/github` with a read-only tool)
- Acceptance: on the fixture, granting a tool and updating services makes the agent list it; revoking removes it (verification needs a GitHub credential).

### M16: Memory
- Depends: M2, M12
- Type: full
- Lanes: C (CLI; lane D folded into C), B (Console)
- Spec: console §4.4, §8.3
- Tasks:
  - Data: Use the console-role grants already shipped in M12's versions (notes SELECT/INSERT/UPDATE/DELETE, DELETE on `turns` and `tickets`, INSERT on `audit_events`); list and delete sessions with the agent's own credential (M12 delta §3.1)
  - Data: The M12 `runtime/adk` version also declares its memory schema version in its manifest; the CLI refuses unknown versions
  - Data: `forget` and session deletes span two credentials (sessions with the agent's, notes/turns/tickets and audit rows with the console role; M12 delta §3.1): the M16 spec fixes the order (an intent audit row first, then the deletes, then a completion row), idempotent retry and partial-failure reporting; S-32 attribution holds on owner-managed stacks only (delta §3.4)
  - CLI: `memory sessions list|show|delete` and `memory notes list|add|edit|delete` with `--json`; actor `console:admin@localhost`; every edit and each session view writes an `audit_events` row (hashes, not text)
  - CLI: `memory forget CUSTOMER` deleting per table (notes, sessions, turns, tickets); `audit_events` rows remain
  - Console: Sessions (view, delete) and notes (full edit) under a customer-data strip; typed confirmation for deletes
- Acceptance: notes edited and deleted with audit rows; a session is viewable and deletable; `forget` leaves only audit rows; Northwind still passes 61/61.

### M17: Agent builder
- Depends: M4, M10, M12, M14, M15
- Type: full
- Lanes: A (`new agent --from-spec`), B (Console)
- Spec: console §4.5, §8.5; guided §14.7
- Tasks:
  - CLI: `new agent --from-spec FILE` (JSON on a private temp file) and `--dry-run --json` preview (server-produced text, never YAML built in the browser)
  - Console: Builder form (name, model, prompt, evals, then only verified tools, knowledge and MCP) with a sticky preview
- Acceptance: an agent with gemini, a prompt, evals and one verified tool is created from the form and released.

### M18: Identity and policy
- Depends: M2
- Type: full
- Lanes: E (CLI), B (Console)
- Spec: console §4.7, §8.6; #701
- Tasks:
  - CLI: `operator-config validate|show|save FILE` with an atomic write of the live `AGENTBREEDER_OPERATOR_CONFIG`, prior copies kept beside it, an audit row in the default state's chain
  - Console: Providers and policy file editors (Cedar, OPA, AuthZEN) with validation and diff; the note that one user editing policy and deploying is not separation of duties
- Acceptance: a Cedar change denies a deploy, reverting allows it, and both edits have audit rows.

### M19: A2A
- Depends: M13, #780
- Type: full
- Lanes: A
- Spec: own spec (the Agent Card path checked against the current A2A spec), with architecture, UX and `/security` reviews
- Tasks:
  - Spec: Agent Cards, `message/send`, references through the `agent` kind from M13, governance like MCP tools (grant, relay, audit)
  - Runtime: a new runtime version serving the Agent Card and governed A2A calls
  - Console: Agent Cards and A2A links on the agent detail
- Acceptance: two agents call each other through governed A2A, with grants and audit rows.

### R: Release 3.0.0a3
- Depends: every milestone the owner includes (M20 is out of this epic)
- Type: release
- Lanes: serial
- Spec: plan "How every milestone is run"; `release.yml`; #794
- Tasks:
  - Release: prep PR (version, CHANGELOG, pins, `sync_npm --write` then the tag within 7 days, `content_manifest`)
  - Release: tag, `release.yml`, TestPyPI approval, npx `Next:` pre-check against TestPyPI, PyPI approval
  - Release: npm from CI with provenance if #794 is ready (re-add the trusted publisher; the pending one lapses 2026-11-12 22:46 UTC), otherwise by the owner
  - Release: verification from PyPI and npm on macOS (every milestone's acceptance, browser click-through) and Linux (install, `next`, `new agent`, `services up`, `release` in a container with a Docker socket, else "Not run")
  - Docs: website, README, what-ships, footer, `install.mdx`, OSS and Cloud `CLAUDE.md`, `ROADMAP.md`, the purge note for `~/.agentbreeder/center`
- Acceptance: every included milestone's acceptance passes from the published packages; the website claims only what that run covered.

## Not milestones here

- **People RBAC/ABAC and roles:** #742 and #775, after this epic.
- **Issue dispositions:** #807, #809, #810, #811 closed as superseded with a mapping (after PR #808
  merges for #807); #805 and #769 close with M4; #763 is reused as M13; #762, #765, #766, #775,
  #780, #794, #732, #648 get linking comments; the rest are untouched.

## Estimate

Architect estimate for the console scope: about 76–115 focused days, plus roughly 10–15 for M9b
and 5–8 for M13. One preview at the end means no published verification until R; each
milestone's checkout verification is the interim evidence.
