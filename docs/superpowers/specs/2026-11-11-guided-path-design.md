# One guided path: quickstart → your own agent → release (CLI, command center, docs)

Status: draft r3, 2026-11-11. Epic: #807. **§9 supersedes §3–§8; §14 supersedes §9–§13** (Fable
design reviews r1 and r2: CHANGES REQUESTED; owner decisions in §9.1). Owner request (2026-11-11): a user runs
`npx agentbreeder@3.0.0-alpha.2 quickstart`, and from there the CLI's help leads them, step by
step, through keys, services, creating an agent with `agent.yaml`, build, evals, release, a local
deploy and a test call; it checks their state, says what is wrong and how to fix it. The command
center and the CLI must follow **the same path**. The docs become crisp: only the commands needed.

## 1. Evidence: the path walked by hand on 3.0.0a2

Run 2026-11-11 on macOS + Docker Desktop, fresh `HOME`, `uv tool install "agentbreeder[eval]==3.0.0a2"`,
an agent written from scratch (prompt + 2 evals, no tools, no knowledge), Gemini key only:

| Step | Command | Result |
|---|---|---|
| Scaffold prompt and evals | `registry new prompt hello-prompt`, `registry new eval hello-evals`, edit, `registry publish` | OK |
| Validate | `validate agent.yaml`, `validate stack.yaml`, `services plan --environment dev` | OK |
| Services (postgres + litellm) | `services up --environment dev --env-file ~/.env --yes` | OK, 74 s |
| Build | `build agent.yaml --environment dev --stack .` | OK, 1 s (cached layers) |
| Try it | `deploy deploy/hello.deploy.yaml --local --environment dev --stack .`, `curl …/invoke` | OK, real Gemini reply |
| Evals | `eval agent.yaml --endpoint URL --environment dev --stack .` | 2/2 |
| Release | `release agent.yaml --stack . --environment dev` | 2/2, promoted, 24 s |
| Gate blocks a bad change | broken prompt published as 0.1.1, `build`, `release` | exit 5, production unchanged |
| Teardown | `remove ID --local --stack .`, `services down --purge --yes` | 0 containers, 0 volumes, no key in logs |

Friction found (each becomes a fix in §4):

- **F1** `build` prints `Next: agentbreeder deploy … --local --environment dev` **without `--stack .`**;
  following it fails with `[CONTEXT_ENVIRONMENT_UNKNOWN]`.
- **F2** `eval` without `--stack` fails with `[MODEL_ACCESS_UNCONFIGURED] Environment dev has no gateway`,
  no hint. Every stack-using command needs `--stack .` even when `stack.yaml` is in the current directory.
- **F3** A manual `deploy` of the production manifest becomes production; a later `release` says
  "already in production" and **skips the eval gate** silently.
- **F4** `release` refuses a `{path: ./prompt.md}` prompt (`REF_UNPINNED`), correctly, but does not say
  how to publish it.
- **F5** After changing a ref, `release` fails `[LOCK_STALE] … run without --locked`; `release` has no
  such option. The fix is `build` first.
- **F6** A failed gate prints `1/2 passed`, not which case failed or why; the report (which has the
  failed check, the judge's explanation and the reply) must be opened by hand.
- **F7** Publishing a new version: `registry new` always scaffolds `0.1.0`; the user must copy files,
  edit the version and delete the scaffold directory.
- **F8** No `invoke` command: the user copies the endpoint from `deploy` output into `curl`.
- **F9** Every deploy prints two `WARNING` lines and "unavailable checks: policy, approval,
  deployer_verification"; normal for the demo identities, alarming without a word of explanation.
- **F10** `npx`/`uvx` leave no `agentbreeder` command; a stale one on `PATH` fails (#805).
- **F11** `quickstart` ends at "5/5 … History retained." with no next step.
- **F12** No starter for "my own agent": the only templates are `http-agent` (not an AI agent) and
  Northwind (needs Tavily, custom services, 61 evals).

## 2. Goal and non-goals

Goal: from `quickstart`, a new user reaches a released agent of their own, with a passing eval
gate, using only what the CLI tells them, and the command center shows the same steps, states and
fixes. Docs list the same steps and nothing else on the getting-started page.

Non-goals: new targets (production Kubernetes stays #770), multi-user (#775), authenticated
invocation (#780), Windows, an interactive TUI or prompts that block scripts.

## 3. The path (one definition, three surfaces)

Ordered steps. Each has a **check** (read-only), a **state** (`done`, `next`, `blocked`, `todo`),
a **reason** when not done, and a **fix**: the exact command, in the launcher form the user is
using (`agentbreeder …`, `npx agentbreeder@<ver> …` or `uvx --from … agentbreeder …`).

| # | Step | Check (read-only) | Fix shown |
|---|---|---|---|
| 1 | Prerequisites | `doctor` checks: Docker reachable, Compose v2, uv (for npx/uvx), `agentbreeder` on PATH is this version (#805) | the failing check's own remedy |
| 2 | Project | `agent.yaml` and `stack.yaml` in DIR | `agentbreeder new agent NAME` or `agentbreeder example northwind` |
| 3 | Model keys | every key the stack's gateway models need is in the environment, `--env-file`, or already stored by a previous `services up` | `export GEMINI_API_KEY=…` or `--env-file FILE`; where to get the key (link) |
| 4 | Services | `services status` all healthy, grant accepted, config current | `agentbreeder services up --environment dev [--env-file FILE] --yes` |
| 5 | Build | image exists, `agent.lock` current for `agent.yaml` | `agentbreeder build` |
| 6 | Release (eval gate) | production exists and runs the image the lock describes, released by `release` | `agentbreeder release`; on a failed gate: the failed cases, why, and "edit the prompt/evals, publish a new version, run release" |
| 7 | Try it | production healthy | `agentbreeder invoke NAME "message"` |
| 8 | Kubernetes (optional) | cluster up, promoted digest = production digest | `agentbreeder cluster up`, `agentbreeder promote` |
| 9 | Clean up (when asked) | — | `agentbreeder remove …`, `agentbreeder services down --purge --yes` |

**Single source:** a new module `src/agentbreeder/guide.py` defines the steps and their checks. It
is pure reads over existing functions (doctor, stack/lock state, store, eval reports); it never
starts, builds or deploys anything and never prints secret values.

- **CLI:** `agentbreeder next [DIR] [--environment dev] [--json]` prints the checklist (✓ done, → next,
  ✗ blocked with reason) and the one command to run next. Exit 0 when all required steps are done, 1
  otherwise (scriptable). `quickstart` ends with `Next: <launcher> new agent hello` and
  `<launcher> next hello`. Every command that ends a step prints `Next: <launcher> next`.
- **Command center:** `GET /api/projects/<id>/guide` runs `next --json` through the existing runner
  (same pattern as `services status`); `app.js` builds its step list, status chips and "Do this next"
  button from that JSON instead of the hard-coded `STEPS` and `stepStatus`. Each button starts the
  same CLI job it does today. A "New agent" form runs `new agent`.
- **Docs:** the getting-started page lists the same 9 steps with the same commands, generated text is
  not needed; a test asserts the docs page names every step id and command in `guide.py` so they
  cannot drift.

## 4. CLI changes

1. `agentbreeder new agent NAME [--model gemini|openai|anthropic|openrouter]` (#769): writes the
   §1 starter: `agent.yaml` (runtime/adk, one model alias, prompt ref), `stack.yaml` (postgres +
   litellm), `stack/litellm/config.yaml` for the chosen provider, and publishes
   `prompt/NAME-prompt@0.1.0` and `eval/NAME-evals@0.1.0` (3 starter cases) into `./NAME/registry`.
   Refuses a non-empty directory.
2. `agentbreeder next` and `guide.py` (§3).
3. `agentbreeder invoke NAME MESSAGE [--candidate] [--json]` (F8): finds the deployment's loopback
   endpoint in state, POSTs `/invoke`, prints the reply (and, with `--json`, the whole response).
4. **Stack auto-detect (F1, F2):** when `--stack` is absent and the agent/manifest directory (or the
   current directory) has `stack.yaml` with a running stack for the environment, use it and print
   `Using stack …` as today. `--stack` stays for explicit use. All "Next:" hints are produced by one
   helper that adds the right flags.
5. **Release safety (F3):** `release` that finds production deployed by plain `deploy` (no release
   journal for that image) says so: "production was deployed without the eval gate; run
   `agentbreeder release --reevaluate`" (evaluates production in place and records it) — or decide
   in review to refuse instead.
6. **Messages:** F4 (`REF_UNPINNED` on release → "publish it: `agentbreeder registry publish …`");
   F5 (`LOCK_STALE` in release → "run `agentbreeder build` first", or release runs the build with
   `--update` when the user passes `--update`); F6 (failed gate prints each failed case: id, failed
   checks, judge explanation, first 200 chars of the reply); F9 (demo-identity warnings get one
   explanatory line and a link, once per command).
7. `registry new KIND NAME --from-version X.Y.Z` (F7): copies the latest published version into an
   editable directory with the next patch version.
8. `doctor` PATH check and `quickstart` next step (#805, F10, F11).

## 5. Docs

`website/content/docs/` getting-started becomes one page, **Your first agent**, with §3's steps
and only their commands, each followed by what you should see (one line). Install, reference pages
(agent.yaml, services, identity gate, command center, local deployment) stay, trimmed, and move
below it in the sidebar. The local-deployment page is rewritten as reference (image rules, secrets,
exit codes) and loses its 40-line setup block. Claims follow the claim rule: each step's "what you
see" comes from the verification run of the released package.

**Before the next release** (docs only, for 3.0.0a2): a short "Your own agent on 3.0.0a2" page with
the exact commands from §1, including the `--stack .` and "build before release" workarounds.

## 6. Security

- `next` and the guide endpoint are read-only; they never echo key values, only key names and
  whether each is present. Env-file parsing reuses the strict parser.
- `invoke` talks only to loopback endpoints recorded in local state; it refuses others.
- The center's guide endpoint goes through the existing session and origin checks.
- `new agent` writes files only under the new directory, mode-preserving, refusing symlinks.

## 7. Acceptance

- From a clean machine state (fresh `HOME`, published packages), following only the CLI's `Next:`
  lines from `npx agentbreeder@<next> quickstart` reaches a released starter agent with a passing
  gate, a successful `invoke`, a blocked bad release with per-case reasons, and a clean teardown;
  the same run through the command center (driven through its API, and a manual browser
  click-through) shows the same steps and states. Both on macOS; Linux at least `next`, `new agent`,
  `services up`, `release` in a container.
- `guide.py` unit tests for every state of every step; docs-drift test; center UI test that the
  steps come from the API.
- Fable review converged; gate green; released as the next preview (3.0.0a3).

## 8. Slices (sub-issues)

1. Docs now: "Your own agent on 3.0.0a2" page + crisp index (a2 workarounds, verified commands).
2. `guide.py` + `agentbreeder next` + quickstart/command "Next:" helper + stack auto-detect.
3. `new agent` starter (#769) + `registry new --from-version`.
4. `invoke` + release messages (F3–F6, F9).
5. Command center driven by the guide (`/guide` endpoint, steps from JSON, New agent form).
6. Docs: Your first agent + trimmed reference pages + drift test; release 3.0.0a3 and verify.

## 9. r2: decisions and review changes (binding over §3–§8)

### 9.1 Owner decisions (2026-11-11)

- Ship an interim docs page for 3.0.0a2 now (slice 1).
- The guide command is `agentbreeder next`.
- `new agent` has no default provider: `--model` is required. In 3.0.0a3 only `gemini` is offered
  (the only provider run end to end; the eval judge needs strict `json_schema` output, unverified on
  others). Other providers are added only after a run.
- Production deployed without the gate: warn and evaluate, never remove automatically (as refined
  in 9.2 #5).

### 9.2 Review r1 findings applied

1. **`next --json` exits 0** whenever the checklist was computed (`"complete": false` in the JSON);
   the center's runner discards stdout on non-zero exit (`center/jobs.py:362-364`). Text form exits
   1 when steps remain.
2. **No stack auto-detect** (§4.4 dropped). Auto-detect could swap the operator's real identity gate
   for the stack's demo config (`cli.py:388-397`, `services/operator.py:7-9`). Instead: one hint
   helper always emits `--stack DIR --environment ENV`; `CONTEXT_ENVIRONMENT_UNKNOWN` and
   `MODEL_ACCESS_UNCONFIGURED` append "pass `--stack DIR`" when a `stack.yaml` sits beside the agent
   file. `build`'s hint (`cli.py:680`) points at `release`/`next`, not a plain production `deploy`.
3. **Slice 1 claims:** the page shows only commands run in the form shown (npx), the "try it" step
   uses the candidate manifest (never a production `deploy` before `release`), and no timings except
   as "one warm-cache run". The two "only `--version`, `doctor`, `quickstart`" sentences are updated
   in the same PR if `uv tool install` is used.
4. **Checks grounded in code** (no Docker writes, no project writes; about 1–2 s with Docker
   healthy, estimate):
   - *Build current:* `deploy/NAME.build.json` `agent_yaml_sha256` and `lock_sha256` match the files,
     lock keys equal the agent's refs (new `lock.status()`); `--output` honoured.
   - *Released:* active production digest equals the build manifest's digest **and** a
     `release/promoted` audit row matches it (reuse `promote._evidence`); never the journal. Open the
     store only if `deployments.sqlite3` exists.
   - *Keys:* names parsed from `os.environ/NAME` in the stack's LiteLLM `config` (LiteLLM-specific,
     stated); present in env, or stored; `IMPORT_DENIED` is `blocked`.
   - *Services:* one `compose ps` via `docker.containers()` + grant recorded + environment written;
     no `preview()` (it binds ports and copies files) and no MCP probes.
   - *Try it:* "ready (recorded)", optionally a bounded `probe_http`.
   - *Kubernetes:* only when `state.get("kubernetes")`; matched by the `promote/promoted` audit row.
   - *Prerequisites:* the PATH check is advisory (`required=False`), never blocks npx/uvx users.
   - *Project:* also "files validate and refs resolve" and "a newer published version of a referenced
     name exists" (with the `agent.yaml` line to change, then `build`).
5. **F3:** `release` returns "already in production" only when a matching `release/promoted` row
   exists. Otherwise it warns "production was deployed without the eval gate; evaluating it now",
   runs the normal candidate flow on the same image, and on pass records `release/promoted` without
   replacing production (digest unchanged); on fail exits 5 with "production is running an image
   that failed the gate: `release --rollback` or `remove ID`". No new flag; no in-place evals against
   production's key.
6. **Center:** `/guide` returns structured data (`{"step":"keys","missing":[…],"stored":[…]}`), and
   each surface writes its own fix text (the center's jobs never see shell exports; keys go through
   the form's private env file). The UI never runs a command string from JSON: each step maps to a
   fixed job kind in `KINDS`; text is placed with `textContent`. Keep the views nav; add a path
   checklist with a fixed step-id→panel map; show the stale note during jobs and refresh on job end.
   "New agent" in the center follows `onboard_example` (name validated with `agentfile.NAME_PATTERN`,
   fixed destination), not a job — deferred past a3.
7. **`invoke`:** endpoint must re-validate as `http://127.0.0.1:<port>` (refuse Kubernetes rows: "use
   `forward`"); no proxies (`ProxyHandler({})`); no redirects; container still owns the port
   (`docker inspect`) before sending; no `customer_id` by default, `--customer`/`--session` explicit
   with a "not authenticated (#780)" note; message from argv or `-` for stdin; control characters
   stripped from output; 180 s timeout. `--candidate` is the "debug a failed gate" route.
8. **`new agent`:** registry refs (evals have no path form, release refuses path prompts), plus
   editable source dirs `src/prompt/`, `src/evals/` and a comment in `agent.yaml` on the edit loop
   (edit → bump version → `registry publish` → change ref → `build` → `release`). Stated fields:
   `candidate: true`, `traces: none`, `agent_events: none`, judge alias in both the eval manifest and
   `environment.models`, a `DATABASE_URL` binding, `review_by` = scaffold date + 180 days, the run
   runtime version (`runtime/adk@1.0.6`), lenient starter cases with `retries: 1`. Files 0644, dirs
   0755. The number of shipped cases is the number verified.
9. **Launcher form:** bare `agentbreeder` when `which agentbreeder` resolves to this interpreter's
   script; `npx agentbreeder@<ver>` when `AGENTBREEDER_LAUNCHER=npx` (set by `npm/bin/agentbreeder.js`
   with `AGENTBREEDER_LAUNCHER_VERSION`, accepted only as an enum and a strict semver); uvx when
   `sys.prefix` is under uv's cache; else `python -m agentbreeder`. `Next:` lines never go to stdout
   in `--json` mode.
10. **Scope cut for 3.0.0a3:** slice 1; `guide.py` + `next` + hint helper + launcher rule; `new agent`
    (gemini); `invoke`; F3–F6, F9; center path checklist + "do this next" from `/guide` (panels
    unchanged). **Deferred:** center "New agent" form and try-it, `registry new --from-version`
    (F7), other providers, the Kubernetes step in the guide, trimming reference pages.
11. **F9:** the explanatory line appears only when the stack's generated demo config was selected;
    the WARNING and "unavailable checks" lines stay unchanged.
12. **F6:** failed-case excerpts are untrusted model text: control characters stripped, 200 chars
    max; docs note they land in center job logs.
13. **Docs timing:** "Your first agent" lands only after 3.0.0a3 is published and verified from the
    registries (like #803 → #804). The drift test matches subcommand tails. Linux stays "Not run"
    unless a container with a Docker socket runs it.
14. **Stale reads** during jobs: show the stale note; refresh on job end.

## 10. Logs for every service and agent (owner request, 2026-11-11)

Owner: "In command center we should show all the detailed logs for all services and agents." Today
`agentbreeder services logs [DIR] NAME --tail N` returns a service's logs, redacted with every value
the stack stored (`services/engine.py:706-715`, cap 10 000 lines). There is no logs command for a
deployed agent and no logs view in the command center. Scheduled by §14 (not 3.0.0a3).

**CLI**

- `agentbreeder logs ID [--tail N] [--since 10m] [--stack DIR] [--json]` for a deployment (agent or
  candidate). Local target: `docker logs` of the container, after the same ownership-label check
  `remove` uses. Kubernetes target: the pod's logs through the existing `kubernetes.py` client for the
  stack's `kind` cluster. `--tail` default 200, max 10 000.
- Redaction: the stack's stored values (as `services logs`), the deployment's own secret file values
  and its gateway key, applied before anything is printed. Control characters are stripped. Logs
  can still contain customer messages and model output; the docs say so.
- `services logs` gains `--since` and `--json` so both commands return the same shape:
  `{"source": "service|agent", "name", "lines": [...], "truncated": bool}`.

**Command center**

- A **Logs** view listing every service of the stack (from `services status`) and every active
  deployment (from `list`), each with its state. Selecting one shows the last 200 lines; a
  **Follow** toggle re-reads every 3 s while the view is open; a client-side text filter; a
  "Load more" that raises `--tail` up to the cap.
- Each read is a fixed argv through `runner.read` (`services logs PATH NAME --tail N --json` or
  `logs ID --stack PATH --tail N --json`). Names and IDs are accepted only if they appear in the
  current lists; `N` is an integer from a fixed set. No free text reaches argv.
- Lines are placed with `textContent`; the view keeps at most 10 000 lines in memory.
- Job logs (services up, release, promote) stay where they are. Each job's panel links to the logs
  of the service or deployment it touched.
- Errors are shown plainly ("can't reach the command center at this address; press Enter in the
  terminal running `up` for a new link", per the #807 comment).

**Verification**: on macOS, Northwind through the center: every service and both deployments
(local and `kind`) show logs, follow works during a release, and a leak scan of what the API
returned finds no `~/.env` value.

## 11. Error and warning notifications (owner request, 2026-11-11)

Owner: "also error and warnings notifications". The command center shows a notification for every
error and warning, from the same sources as the guide (§3) and the Logs view (§10), so the three
never disagree. Scheduled and reduced by §14.

**Sources** (each notification records source, severity, time, a short message and a link):

| Source | Error | Warning |
|---|---|---|
| Jobs | a job failed or was cancelled (exit code, last stderr line) | job finished with `WARNING:` lines (e.g. the gate's demo-identity warnings, once per kind) |
| Release | eval gate failed (exit 5) with the failed case ids and the judge's one-line reason (§9.2 #12) | production deployed without the gate (§9.2 #5) |
| Services | a service exited or is unhealthy in `services status` | a service restarted since the last check |
| Deployments | a deployment is `failed`, or `status` reports `noncompliant:` | `unverifiable:`, or a review date within 14 days (`REVIEW_EXPIRED` ahead) |
| Guide | a step is `blocked` (e.g. a missing key, `IMPORT_DENIED`) | an advisory check fails (e.g. old `agentbreeder` on `PATH`) |
| Logs | lines at level `ERROR`/`CRITICAL`/`Traceback` in a service or agent log | lines at level `WARNING`/`WARN` |

Log levels are matched per known format (Python logging, LiteLLM, Postgres `ERROR:`/`WARNING:`,
Phoenix); unknown formats are not classified. This is a heuristic and the UI says so.

**How**

- The center server polls the cheap reads every 10 s while a browser session is open (`services
  status`, `list`, job states, guide, the last 200 log lines per running service and deployment
  since the last poll), all through `runner.read` with fixed argv. Identical messages within 10
  minutes are folded into one with a count.
- Stored in `center.sqlite3`, newest 500 kept, redacted like logs, each with read/unread state.
- UI: a bell with the unread error and warning counts on every screen; a Notifications panel
  (filter by severity and source, mark read, "show in logs"/"open job"); errors also appear as a
  dismissible banner. Optional desktop notifications through the browser's Notification API, only
  after the user turns them on, with the message text only (no log excerpt).
- CLI, same data: `agentbreeder next` lists the current errors and warnings from the guide's
  checks; `agentbreeder logs`/`services logs` accept `--level warning|error` to show only
  classified lines. No background daemon outside `up`.

**Security**: messages are untrusted text (`textContent`, control characters stripped, 300 chars
max); no secret value is stored or shown (same redaction as logs); desktop notifications carry no
log content; nothing is sent off the machine.

**Verification**: on macOS with Northwind through the center: stop a service by hand (error
appears, then clears after restart), break an eval case (gate failure with reasons), let a job
emit the gate warnings (one folded warning), and leak-scan the stored notifications.

## 12. All agents, registry browser, agent.yaml generator (owner request, 2026-11-11)

Owner, with a screenshot of the center on Northwind: "we should show all agents running and we
should have capabilities to see all the registries, generate agent.yaml". Today the center is
scoped to one selected project (top-right picker); agents appear only in that project's
Deployments view, and the registry appears only as a count ("N versions available") on the
Services panel (`center/static/app.js:267-273`).

Same-path rule: every center capability below is a fixed CLI command with `--json`, and the CLI
command ships first.

**12.1 All agents (home screen)**

- A **Agents** home view, shown before any project is picked: every active deployment across all
  registered projects and environments, one row each: agent name, project, role (production or
  candidate), target (local Docker or `kind`), state (recorded), image digest (short), released by
  the gate or not (§9.2 #5 evidence), endpoint (loopback) or "use forward" for `kind`, last
  release time and result. Row actions: open project, logs (§10), status, remove (confirm).
- Candidates are labelled **temporary: removed when the release finishes**, and job logs mark
  candidate URLs the same way (the owner opened a release's candidate URL after it was removed and
  got "Failed to fetch").
- CLI: `agentbreeder list --all [--json]` reads every stack's state directory under
  `~/.agentbreeder/stacks/` plus the default state, without changing any of them (open each store
  only if its database exists, as `next` does).

**12.2 Registry browser**

- A **Registry** view per project: every version the project can resolve (project `./registry`,
  `AGENTBREEDER_REGISTRY_PATH` entries, built-ins), grouped by kind (runtime, prompt, knowledge,
  tool, mcp, eval, service): name, version, source root, hash, verify status, and which agents in
  the project reference it. Selecting a version shows its manifest and files read-only (text files
  up to 256 KiB, `textContent`; binaries listed, not shown). Eval sets show their case count and
  ids; prompts show the prompt text.
- CLI: `registry list --project DIR --json` (exists) and a new `registry show REF --project DIR
  [--json]` (manifest, file list, hash, verify result, text of small files).
- Read-only in this slice. Publishing new versions stays CLI-only until a later slice designs
  editing.

**12.3 Generate agent.yaml**

- A **New agent** form: name, model (Gemini only, §9.1), prompt (pick a published prompt or start
  from the starter text), evals (pick an eval set or start from starter cases), tools, knowledge and
  MCP servers picked from the registry browser's lists. It shows the resulting `agent.yaml` and
  `stack.yaml` as a read-only preview before writing.
- It runs one CLI command with fixed flags: `agentbreeder new agent NAME --model gemini
  [--prompt REF] [--evals REF] [--tool REF]… [--knowledge REF] [--mcp REF:tool,…] --dir
  ~/agentbreeder-projects/NAME`. Every REF is accepted only if it is in the registry listing
  just read; NAME matches `agentfile.NAME_PATTERN`; the destination is fixed; then the project is
  registered as `onboard_example` does (§9.2 #6). Validation errors come from `new agent` itself,
  so the CLI and UI show the same messages.
- Editing an existing `agent.yaml` from the UI is out of scope for this slice.

**Verification**: with Northwind and a generated starter agent both running, the Agents view lists
both projects' production deployments; the registry browser lists Northwind's 30-odd versions plus
built-ins and opens a prompt and an eval set; the form generates a starter that passes `validate`,
`services up`, `release` and appears in the Agents view. macOS; the UI clicked through in a real
browser, not only scripted.

## 13. Proposed release split (to confirm with the owner)

**Superseded by §14.1:** the owner chose to ship everything in 3.0.0a3.

The owner requests in §10–§12 roughly double the scope of §9.2 #10. Proposal:

- **3.0.0a3: guided path.** Slices 1–4 and the center checklist (§9.2 #10), plus §12.1's candidate
  labelling (small).
- **3.0.0a4: command center.** All agents (§12.1), registry browser (§12.2), agent.yaml generator
  (§12.3), logs (§10), notifications (§11).

## 14. r3: review r2 applied (binding over §9–§13)

### 14.1 Schedule


**Superseded scope (2026-11-11):** §14.1's schedule and the build order are replaced by the master
plan `docs/superpowers/plans/2026-11-11-console-epic.md` (epic #812), which wins where it differs
from §9 or §14.

**Owner decision (2026-11-11): everything below ships in 3.0.0a3**, one preview and one
verification run (the review recommended splitting; the owner chose one release). The table is now
the build order inside a3, each row merged behind the gate before the next starts; "Later" stays
out of a3.

Build order (originally proposed as separate previews):

| Build step | Contents |
|---|---|
| **1. Guided path** | §9.2 #10 (guided path: `next`, hint helper, launcher rule, `new agent` gemini, `invoke`, F3–F6, F9, center checklist), candidate labelling, failed-candidate log tail (14.3 #3), and the #811 bugs (audit time, verify wording, kind guidance) |
| **2. Command center** | `logs` CLI + Logs view (14.3), all agents (14.5), registry browser (14.6), notifications as conditions + events (14.4), #811 UX items |
| **Later** | log-level notifications, desktop notifications, the generator beyond name/model/prompt/starter evals |

### 14.2 Residuals on §9

- `promote._evidence` raises `RELEASE_THRESHOLD_LOWERED` / `RELEASE_EVALSET_CHANGED`; in `release`
  these mean "not released, evaluate again", in `guide.py` state `next` with the reason.
- **Read-only opens.** `Store()` creates and migrates the DB under `BEGIN IMMEDIATE`, and
  `StackState()` creates the stack directory (`store.py:84-149`, `services/state.py:98-103`). `next`,
  `list`, `list --all` and `logs` open `deployments.sqlite3` and `stack.db` with
  `file:…?mode=ro` (no mkdir, no migration, no write lock), so they never create phantom stacks or
  block a job's audit write.
- `invoke --candidate` only reaches a hand-deployed candidate (`release` removes its own); the
  failed-gate route is the saved log tail (14.3 #3) and the eval report.

### 14.3 Logs (replaces §10's mechanics)

1. A dedicated `Docker.logs(info, tail, since)` with `stderr` merged into stdout, `--timestamps`,
   addressed by container ID, a 4 MiB cap (`truncated`), after `authorize(record["context"])` and
   `inspect(record)`; the existing `command()` drops stderr. Kubernetes: `kubectl logs -l
   io.agentbreeder/deployment=<id> -c agent` through the recorded binding after `inspect`, never
   listing the shared namespace; `--previous` for crash loops; a pending pod returns no lines plus
   its failure reason. Claim: local kind only. Sidecar/egress containers are shown as separate
   sources, labelled (they see MCP call content).
2. **One redactor** (`redaction.py`) used by `logs`, `services logs` and job output: the stack's
   stored values, every non-removed deployment's secret copies (a new reader for the 0444 copies:
   `O_NOFOLLOW`, owner and 0700-parent checks), and for deployments without copies the
   `<state>/secrets/<name>` values its manifest names; literal, per-line (≥ 8 chars) for multi-line
   values, base64, URL-quoted and JSON-escaped forms, and the password part of URLs. The short-lived
   eval judge key is not covered; docs say "best effort".
3. **Failed candidate tail (a3).** When a candidate fails to start or fails the gate, `release`
   saves its last 500 redacted lines in the 0700 state directory beside the release journal and
   prints the path; `logs` and the job panel link to it.
4. **Follow:** `logs --follow` / `services logs --follow`; the center holds one pumped read-only
   child per open log (job pattern, `?after=`), an in-memory ring buffer (never `center.sqlite3`),
   killed 15 s after the last poll; `--since` strictly validated. Log reads are not cached by the
   runner (`cache=False`).
5. `services down --purge` also deletes anything log-derived the center kept for that stack.

### 14.4 Notifications (replaces §11's mechanics)

- **Conditions** are computed live and never stored, and clear themselves: exactly what `next`
  prints (blocked steps, failed or noncompliant deployments as *recorded*, services down or
  unhealthy, production not released through the gate). Guide steps in `todo`/`next` are not
  notifications. CLI and center show the same set (one function).
- **Events** are stored (500 kept): job failed (fixed template, exit code, error code matching
  `\[[A-Z0-9_]{3,40}\]`), container died with code N / OOM / health became unhealthy / restarted
  (not during a services-up job), eval gate failed (case ids and counts, link to the report).
  No free text from logs, stderr or models is stored.
- **Source:** one long-lived `agentbreeder watch --stack DIR --json` child per project while a
  client heartbeat arrived in the last 30 s: `docker events` filtered on the stack and deployment
  labels, a `compose ps` resync every 60 s, deployment rows re-read (read-only) when
  `deployments.sqlite3` changes; the guide is recomputed on job end and on events. Heartbeats and
  notification reads do not refresh the session idle timer. `Runner.read` gets single-flight per
  key and a small concurrency limit.
- `status` (re-verifies at the IdP, writes) is never polled; compliance shown is "as of last
  reconcile". The review-date warning is dropped until `review_by` is recorded at verify time.
- Log levels are a **filter and highlight** in the Logs view and `--level`, via one parser module
  keyed by built-in service ref, tested on fixtures captured from a Northwind run; continuation
  lines group with their record; project services stay unclassified. Not a notification source.
- Desktop notifications: later; fixed templates only; docs say "AgentBreeder sends nothing; your OS
  keeps notification history".
- One status function backs the guide, the Services view and `watch`.

### 14.5 All agents (replaces §12.1's mechanics)

`services up` records the project path in the stack's meta. The center lists registered projects
with `list --stack` (read-only); stacks without a recorded project show as "not registered".
`list --all` validates directory names, refuses symlinks, honours `AGENTBREEDER_STACKS_DIR`, and
skips (and reports) a store with an unsupported schema. One environment per center project, as
today. The row's "status" action is the reconcile job. Columns include the identity check, as the
Deployments table has.

### 14.6 Registry browser (replaces §12.2's mechanics)

`registry show REF` goes through `Ref.parse`, `resolve` and `_entries` (symlink, hardlink and size
checks), reads with `O_NOFOLLOW`, caps the response, strips control and bidi characters. Hash and
verify status come from `verify()`, cached per project until a job ends. Its own route; the roots
in use (including `AGENTBREEDER_REGISTRY_PATH` as `up` inherited it) are shown.

### 14.7 Generator (replaces §12.3; later)

When scheduled: name, model, prompt text and starter evals only; preview from
`new agent --dry-run --json` (the UI never renders YAML); prompt text through a private temp file,
not argv; errors through `--json` via `read` (the `onboard_example` pattern drops them). Picks of
existing versions only from roots the new project resolves (built-ins, `AGENTBREEDER_REGISTRY_PATH`),
or `new agent` re-publishes them into the new registry. Tools, knowledge and MCP are added per
combination after a run (the center also refuses `services-up` for MCP stacks today).

### 14.8 Owner walkthrough findings (#811)

Audit time blank (`app.js:335` reads `timestamp`, rows carry `at`); verify card in words; audit rows
with agent, mode and reasons; kind guidance with install command and "restart `up`"; collapse the
job drawer after a finished job and link its report and production URL; explain service rows with
no host port; Logs and Try-it entry points. Evidence: the owner's release through the center in a
real browser passed 61/61 (2026-11-11).

### 14.9 Re-review r3 applied

- **R1 Container stops are not "died" events.** A `die` is an event only when no `kill`, `stop` or
  `destroy` for that container precedes it in the same window, and never for a container the
  running job touches (release removing candidate/previous production, `remove`, `services down`,
  rollback). Verification: a clean release and a `services down` store zero events.
- **R2 Redactor split.** `redact(text, values)` (all encodings) is a pure function shared by the CLI
  and the center. Collecting values (`redaction_values(stack)`) is CLI-only and covers deployments
  in every environment of the stack. The center keeps redacting job output with the form's keys
  only; it still holds no secret and opens no deployment state.
- **R3 Build order.** The failed-candidate tail needs local `Docker.logs` and the redactor with its
  fallback path, so those land in build step 1. The tail is captured before the candidate is removed
  on every run and discarded on a pass; one per agent, overwritten, mode 0600, redacted before
  truncating; the center reads it through a fixed-argv CLI read, never by opening the state directory.
- **R4** 14.3 #5 is replaced by a docs sentence: job logs and events live in
  `~/.agentbreeder/center`, survive `--purge`, and how to delete them.
- **R5 `watch`:** agent containers carry no stack label, so events are matched against this stack's
  deployment IDs; container events cover local targets only, and kind deployments show recorded
  state plus a pod check in the 60 s resync; the run confirms whether two `label=` filters combine
  as OR, otherwise two streams; the center's shutdown kills watch and follow children too.
- **R6** Read-only opens "take no write lock" (without WAL a reader can briefly delay a commit). A
  read-only open cannot migrate or recover a hot journal: `next`, `list` and `logs` report that the
  state needs an upgrade and name the CLI command that does it, instead of failing.
- **R7** Log-follow polls, heartbeats and notification reads do not refresh the session idle timer
  (`session_valid` gains a parameter).
- **R8** The gate-failed event shows the report path as text and offers a fixed-argv CLI read of
  the report summary; case ids are capped at 80 characters with control characters stripped.
- **R9 Evidence.** The owner's 61/61 release in a real browser used the published
  `npx agentbreeder@3.0.0-alpha.2 up` (process list, 2026-11-11 12:37), not a checkout. Docs and
  CLAUDE.md change only when a3's verification records its own browser run. The `pods/log`
  requirement for a non-admin Kubernetes target belongs in #770/#772 (Helm RBAC).

### Implementation notes (not spec changes)

- **`watch` matching (R5, M5):** on Docker Desktop (2026-11-12) two `label=` filters on
  `docker events` combine as AND, while several `event=` filters combine as OR. `watch` runs one
  `docker events` stream filtered by type and action and matches each record in the CLI: stack
  services by `io.agentbreeder.stack`, agent containers by this stack's deployment IDs. Seen
  sequences: `docker stop` is kill, kill, stop, die; `rm -f` is kill, die, destroy; a process
  exiting by itself is a bare die, which is the only `died` event (R1). A kill or stop explains
  a later die until the container starts again (no time window), so `docker kill` reports
  nothing: the acceptance's "killed service" is a service whose process exits by itself (a
  crash, or Postgres stopped from inside). The CLI cannot see the console's jobs, so the
  console drops `restarted` while its own `services up` runs. Labels are not authenticated
  (another container on the same engine can claim them), so events are best effort.
- **`invoke` reply (§9.2 #7, M4):** control characters in the reply are shown as visible escapes
  (`safe_text.visible`, as `logs` does for S-08) rather than removed, so a reply is never silently
  altered; `--json` escapes by construction. Exit 1 also covers failures after the message was
  sent (timeout, invalid answer), since the agent may have run the turn; exit 2 means nothing was
  sent.
