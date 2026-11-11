# One guided path: quickstart → your own agent → release (CLI, command center, docs)

Status: draft r2, 2026-11-11. Epic: #807. **r2 supersedes §3–§8 where §9 says so** (Fable design
review r1: CHANGES REQUESTED, 3 blocking; owner decisions in §9.1). Owner request (2026-11-11): a user runs
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
