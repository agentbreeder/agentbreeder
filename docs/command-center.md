# Local console and the Kubernetes target (preview)

`agentbreeder up` starts a web UI on `127.0.0.1` for one person on one machine. From it you onboard
an agent project, start its backing services, run the eval-gated release to local Docker, and deploy
that same image to a local Kubernetes cluster.

`3.0.0a2` and earlier call it the command center, and the runs recorded below used that name; on
`main` it is the console (#816). This page keeps its file name, `command-center.md`, because
links point to it.

**What has been run.** From a source checkout on macOS with Docker Desktop, on 2026-11-10, against
`kind` 0.33 and Kubernetes 1.37: Northwind onboarded through the command center's API, services
started after a grant review, the release passed 61/61, and `promote` deployed the image by digest
and passed 61/61 on the cluster. Remove, the kill switch, `reconcile`, network policy and secret
handling were measured on that run (below). After code review the whole walkthrough ran again from
an empty command center, 61/61 on both targets, through full teardown. A headless browser rendered
sign-in, the Project page and the Deployments page; the other panels were driven through the same
API but not clicked through.

**From the published `3.0.0a2`**, on macOS with Docker Desktop on 2026-11-11, in a clean sandbox
(fresh `HOME` with `~/.docker` linked in for the Docker context, empty package caches, no checkout; Docker images on the host were not cleared): `up` started through both uvx and npx. Through
`npx agentbreeder@3.0.0-alpha.2 up`, driven by a script through the command center's API as the UI
does, with no browser,
Northwind onboarded from the packaged example, services started after the grant review, the release
passed 61/61, `kind` 0.33 was connected and its grant change reviewed, and `promote` passed 61/61 on
the cluster. Both deployments, the cluster and the services were then removed through the same API,
leaving no containers running, and no provider key value appeared in any job or server log. It has
not run on Linux, and **no production cluster has been tested**.

## Run it

```
npx agentbreeder@3.0.0-alpha.2 up                               # needs Node 18+ and uv
uvx --from "agentbreeder[eval]==3.0.0a2" agentbreeder up        # needs uv
```

Each prints a one-time link and opens it. Pin the version: a bare `pip install agentbreeder` installs
the unmaintained 2.x. With pip, install `"agentbreeder[eval]==3.0.0a2"` in a virtual environment
and run `agentbreeder up` (on `3.0.0a2` this path has run only in the TestPyPI rehearsal, with
`--no-browser`); from a checkout, `pip install -e ".[eval]"`. The Northwind eval gate
needs the `[eval]` extra, which the uvx and npx commands include. The cluster steps also need
`kind` 0.33 or newer and `kubectl` on `PATH`.

Follow the steps in the left column. If the browser did not open or the link expired (5 minutes,
single use), press Enter in the terminal for a new one. `--port`, `--no-browser` and
`--environment` are available; with a project directory, `agentbreeder up DIR --yes` also confirms
the grant and starts its services. Stopping the server leaves services and deployments running; use
`agentbreeder services down` and `agentbreeder remove`.

The same steps work without the UI, with `agentbreeder` on your `PATH` (a pip install or a
checkout; `uv tool install "agentbreeder[eval]==3.0.0a2"` also adds the command, but only
`--version`, `doctor` and `quickstart` have been run that way). `npx` and `uvx` do not install that
command; with them, repeat the prefix for each command. These commands ran from a checkout on
2026-11-10; on `3.0.0a2` they ran only as command-center jobs.

```
agentbreeder services up DIR --environment dev --env-file ~/.env
agentbreeder release DIR/agent.yaml --stack DIR --environment dev
agentbreeder cluster up --stack DIR --environment dev --yes      # needs kind >= 0.33 and kubectl
agentbreeder promote DIR/agent.yaml --stack DIR --environment dev --to kubernetes
agentbreeder list --stack DIR --environment dev
agentbreeder forward ID --stack DIR --environment dev            # loopback port to a cluster deployment
agentbreeder remove ID --cluster --stack DIR --environment dev
agentbreeder cluster down --stack DIR
```

`deploy` and `remove` take `--local` or `--cluster`, which must match the manifest's target.
`cluster down` also removes the registry container's anonymous volume, and needs `kind` on `PATH`
(on `main`, not in a published preview).

### The console shell (#816): on `main`, not in a published preview

On `main` the walkthrough column is replaced by a console shell (Preact + htm, no build step):

- **Sidebar** with one entry per screen that works: Home, Services, Build and release, Deployments,
  Targets ▸ Kubernetes, Governance ▸ Audit. Each screen has its own address (`#/deployments`), page
  title and `h1`. Changing screen moves focus to that `h1`, and a skip link goes straight to it.
- **Top bar**: a scope switcher ("All projects" or one project; a project screen under "All
  projects" asks which), an activity pill with the number of running jobs that opens the **Jobs**
  dock, and a user menu (theme, sign out of this tab).
- **Jobs dock**: every job this tab follows, several at once (one per project), including jobs
  started elsewhere. Starting a job opens it. The log only appends lines. A finished job collapses
  to one line ("The eval gate failed. Production is unchanged.") with its log one click away.
  Cancel is offered once; "Stop now" appears only when the server allows it, behind a confirm.
  Closing the dock never stops a job.
- **Sign-in states**: the one-time link is consumed before anything else and removed from the
  address bar. "Session ended" and "Sign in" pages say how to get a new link, and signing in again
  returns to the screen you were on. "Console not reachable at this address" (no answer at all)
  is a separate page that retries every 5 seconds.
- **Walkthrough fixes (#811)**:
  - Audit shows each row's time (from `at`), agent deployment, mode, reason codes and whether the
    actor was verified. Verify is one sentence ("Chain intact: 19 rows checked, head 63639e…" or
    "Chain broken at row 5: hash mismatch."), plus incomplete attempts in words.
  - A missing `kind` or `kubectl` names its install command and says to restart `up`, since the
    console reads `PATH` when it starts.
  - Every disabled button says why underneath. When several share a reason (a job is running in
    this project), it is shown once.
  - Confirm dialogs focus Cancel and give focus back to the button that opened them.
  - A service without host ports says "internal only".
  - Release candidates carry a violet "candidate, temporary" label.
  - Notices belong to their screen and clear when you leave it.
  - Measured cost figures appear only for the onboarded Northwind example.
- **GRANT_PENDING**: after Connect local cluster, the Kubernetes screen shows "Next: accept the
  grant change" with a button to Services. The screen works this out from `cluster status` (the
  cluster is attached but not in the accepted grant), the same condition `next --json` reports as
  GRANT_PENDING. It does not call `next`. Deploy to cluster stays off, with that reason, until the
  reviewed grant has been started (`services up`), so the console no longer offers a `promote`
  that would fail with `KUBERNETES_UNCONFIGURED`.

Tested with Playwright against the built wheel with a fake CLI: in Chromium in CI, and in the
installed Chrome on macOS 13, where Playwright's Chromium is not offered. The tests cover every
screen, each fix above, focus after each action, two projects with jobs at once, sign-in,
session-ended and unreachable states, zero CSP violations, and axe in light and dark. Axe fails the
run only on serious or critical findings; the last run reported none at any level. Not tested:
"Stop now" (the server offers it only after a long cancel wait), and narrow screens. The fake CLI never runs Docker or a model; the
acceptance run with real services is recorded in `docs/superpowers/verification/M0b.md`: from a
wheel built from `main` in a clean sandbox on macOS (2026-11-13), Northwind passed 61/61 on local
Docker and on `kind` through the console's API, every screen was opened in Chrome, and the teardown
ran from the UI (three leftover Docker objects of the stack were then removed by hand). Onboarding,
the grant review, a full release and the cluster connect and deploy were not clicked in the browser.

## How it stays the same engine

Every action starts the CLI in a child process, with a fixed argument list. The UI does not open the
deployment database. The identity gate, policy, approval, audit and the eval gate run exactly as
they do for a CLI user.

- **The eval gate is orchestration.** Someone with deploy rights can still run a plain `deploy`.
  `promote` refuses any image without an audited release at the threshold in `agent.yaml`, but that
  does not stop a person deploying a manifest by hand.
- **Approvals are not in the UI.** A second person's verified identity is what a single-user session
  cannot provide. A job that needs one shows the command.
- **Stacks with MCP servers** are refused from the UI, so their tools are reviewed with the CLI.

## What protects the local server

It binds `127.0.0.1` only, checks the exact `Host`, needs a per-tab session from the one-time link
(a header, because browsers send cookies to every port on `127.0.0.1`), and on every request a
custom header, same-origin `Origin` for writes, JSON bodies and a strict content
security policy. Provider keys go to a private file for the one job that needs it. Job logs go through
the CLI's redactor (`agentbreeder.redaction`; on `main`, not in a published preview): a key's value
(4 characters or more) and its base64, URL-encoded and JSON-escaped forms, and the password in
any `scheme://user:password@host`, become `[redacted]`; another encoding of a value echoed by a
tool would not be caught.

### Console foundation (#815): on `main`, not in a published preview

- **One route table.** Every API request passes the same checks, in this order, before any handler
  runs: the exact `Host`; the `X-AgentBreeder-Center` header and `Sec-Fetch-Site: same-origin`
  (so a page on another `127.0.0.1` port, which is same-site, is refused); for any method other
  than `GET`, the console's own `Origin` and `Content-Type: application/json`; the session (only
  sign-in itself skips it). Only then does an unknown path get `404` and a method no route uses
  `405`, and the route's body cap apply (64 KiB for JSON, up to 1 MiB for editor payloads;
  larger is `413`). A job-log poll does not keep an idle session alive.
- **One reply shape.** Every API reply, including the server's own errors for a malformed
  request, is `{"data", "error", "stale"}`. `error` is `null` or `{"code", "message"}`; when a
  CLI read fails, the console forwards only the CLI's final `[CODE] message` line, never other
  stderr. (Job logs still show a job's whole output, redacted as described above.) `stale` marks the last good data shown after a
  failed read.
- **Static files** are an allow-list built at start from the installed `center/static/` tree:
  regular files of known types (HTML, JS, CSS, `woff2` fonts, plain text such as the font
  licence, SVG and PNG), no dotfiles, no links. Nothing else is served.
- **Projects only from directories you alone control.** Adding a project, onboarding an example
  and every job start refuse a project directory, `agent.yaml` or `stack.yaml` that another user
  owns or that is group- or world-writable, and any parent directory up to `/` owned by someone
  other than you or root, writable by others unless it is sticky (like `/tmp`), or a link. A
  job start also refuses a project whose path now resolves somewhere else. The error names the
  path, and for a writable one the `chmod go-w` that fixes it (a project cloned under a umask of
  `002` needs this). On a macOS volume mounted with "Ignore ownership on this volume", every file
  reports your user, so the check passes: keep projects on the system volume.

- **Several jobs at once.** Engine jobs (build, release, rollback, services, remove,
  reconcile) run one at a time per project, so two projects can run at once. Jobs that touch the
  one local cluster (`cluster up`, `cluster down`, promote to kind) also run one at a time on the
  machine. Short writes in later milestones lock their target and time out after 60 s (300 s
  for a tool publish, which re-locks dependencies): registry publish and import and memory edits
  are not blocked by engine jobs; `agent set-ref` also holds its project, since it rewrites
  `agent.yaml`; a policy save is refused while any engine job runs, and no engine job starts
  during one. A refused start names the job holding the lock (`409`, `[BUSY]`); a refused save
  answers `409`, `[WRITE_BUSY]`.
- **These locks only order the console's own jobs.** A command typed in a terminal does not see
  them: `agentbreeder services up` in a terminal can run beside a console job. Correctness comes
  from the CLI's own file locks (the deployment store and `release` have one today; `cluster`
  and `services` get theirs in later milestones).
- **Cancel and shutdown.** Cancel sends one SIGINT to the job's CLI process, so `release` and
  `promote` finish or undo a replace step. It is sent no sooner than 0.2 seconds after the
  job's first output line, or 2 seconds after the start for a job that has printed nothing, because a SIGINT
  sent while the CLI is starting, or just as it prints, can be lost or held back. While it
  works the job shows "Cancelling…"; Cancel is not offered twice, since a second SIGINT can
  interrupt the restore. If the job is still running 15 seconds later, "Stop now" (asked
  first) ends its whole process group and may skip that restore: check Deployments after it.
  Stopping `up` (Ctrl-C, SIGTERM or closing the
  terminal) sends the same SIGINT to every running job and long-lived child, waits 5 seconds
  (Ctrl-C again skips the wait), then sends SIGTERM to each one's process group, which also stops
  the `docker`, `kind` or `kubectl` processes it started, and waits up to 5 more seconds for them
  to end (at most 10 seconds in all). A read or short write in progress is not stopped; it ends
  or times out on its own.

- **One console per user, isolated.** `up` runs its Docker checks, then replaces itself (same
  process, same terminal) with `python -I -m agentbreeder.center`, so the server ignores
  `PYTHONPATH`, the user site and the current directory, and never loads the deployment state,
  Docker, secret, operator-config or audit modules. It holds a lock (`up.lock`) in the console
  directory (`~/.agentbreeder/center`): a second `up` prints the running console's URL and exits
  (with a project directory or `--yes`, it says nothing else was done and exits 1).
  Once it holds the lock, `up` deletes provider-key and editor temp files a crashed console left
  there. Because of `-I`, `up` needs AgentBreeder installed (from a checkout, `pip install -e .`);
  it refuses to hand over to a different installed copy (another version or another
  directory). The lock needs macOS or Linux.
- **Port.** `up` listens on `127.0.0.1` only, on port 8080 by default (3.0.0a2 used 8900), and
  prints the URL: open only that URL. A port counts as busy when anything answers on
  `127.0.0.1` there, including a program listening on all addresses. Before a console password
  exists (sign-in arrives in a
  later milestone), a busy port moves it to the next free one up to 8099, and it remembers the
  port it used. Once a password exists, it does not move: if the port is busy, `up` names the
  port and stops, so a program squatting the port cannot pass for the console at a bookmarked
  address. `--port N` uses exactly `N`.
- **What records a console action (S-30).** A `release`, rollback, `promote`, `remove` or
  `reconcile` job writes rows to the deployment audit chain, as the same CLI command does.
  `build`, `services up`/`down` and `cluster up`/`down` write none; the console's job history in
  `center.sqlite3` is their only record: kind, project, start, end, exit code and the redacted log
  (at most 20,000 lines of 4,000 characters), for the last 100 finished jobs, not chained, so a
  local user can edit it. Job options (such as which deployment a `remove` targeted) are not
  stored. Sign-in events arrive with the password milestone. The registry and `set-ref` edits of
  later milestones will change only the project's files (and git history, if you commit them);
  the console will keep no record of them.
- **Reads of project files run in the server.** Registry versions (`registry list`),
  `agent.yaml`, `agent.lock` and where a version is declared and locked (`registry used-by`) are
  read in process with the same functions as the CLI. Each file is checked first: a regular file
  (not a link or FIFO), opened without blocking, size-capped. Answers are cached by each file's
  inode, change and modification time and size, and dropped when a job ends. `registry verify`
  opens and hashes every file, so it runs as a CLI child with a 5-minute timeout in the
  background: the registry card says "verifying" or shows the last answer until it finishes.
  Which deployments run a version reads deploy state, so that part of `used-by` is a CLI child
  too, as is everything that touches deployment state, a stack's database or secrets, Docker, the
  operator config or the audit chain. A test runs the real server process through every read and
  checks that it loads only an allowlist of modules. Relative `AGENTBREEDER_REGISTRY_PATH` entries
  are made absolute against the directory `up` ran in, so the console and its CLI children search
  the same registries.
- **Its database** (`center.sqlite3`, mode 0600) holds projects, job history and settings, and
  from the sign-in milestone password hashes; never a password, key or other secret value. Its
  schema is versioned and migrated at start; a 3.0.0a2 database keeps its projects and job
  history.

**The limits.** Anyone who controls the local Docker has full control: the gate prevents mistakes and
policy drift, not a local admin. A project is code. Building it runs its Dockerfile in Docker, so add
only projects you trust. Local endpoints are unauthenticated.

### Browser-side guards (#816): on `main`, not in a published preview

A console session can build and run project code, so whatever reaches the page must not be able to
run as script. These guards are a primary control, not hardening (console spec §10.1 I1):

- **Content security policy** on every response: `default-src 'none'; script-src 'self';
  style-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; base-uri 'none';
  form-action 'none'; frame-ancestors 'none'; trusted-types 'none'; require-trusted-types-for
  'script'`. No inline script or style, no `eval`, nothing from another origin. Trusted Types are
  required and no policy may be created, so assigning a string to `innerHTML` (or to a script's
  text or URL) throws.
- **Source guard.** A test fails if any console file outside `vendor/` uses `innerHTML`,
  `outerHTML`, `insertAdjacentHTML`, `document.write`, `dangerouslySetInnerHTML`, `srcdoc`, `eval`,
  `Function(…)`, `createContextualFragment`, `DOMParser` or a string timer, or if any HTML, JS or
  CSS file names an external origin. Agent replies, log lines, notes and file contents are only
  ever placed as text.
- **Preact + htm, vendored** for the console shell (#816): htm 3.1.1's `preact/standalone.module.js` (htm and the Preact 10 build it bundles,
  one ES module), copied unchanged from the npm tarball and pinned by SHA-256 in a test, with no
  build step. Its licences (Apache-2.0 for htm, MIT for Preact) ship beside it in
  `center/static/vendor/`.
- **Visible control characters** (S-35). A shared text function shows C0/C1 controls and DEL as
  `\x1b`, and bidi controls and invisible format characters (zero-width space, BOM, soft hyphen,
  the Unicode tag block) as `U+202E`, so a log line cannot rewrite the terminal or read
  differently from its bytes, and Copy takes the escaped form. Zero-width joiners stay, since
  emoji and Persian text need them. Every text the shell shows from a project, a job or the CLI
  goes through it; escapes are highlighted, so a literal `\x1b` typed in a line still looks
  different from a real one.

Tests: `node --test 'tests/js/*.test.mjs'` for the browser modules; `tests/ui/` runs the console from the built
wheel with a fake CLI in Chromium (Playwright): zero CSP violations on load, HTML sinks throw, and
axe in light and dark (`AGENTBREEDER_UI_TEST=1`, dependencies in `tests/ui/requirements.txt`; on
macOS 13, where Playwright's Chromium is not offered, add `--browser-channel chrome`).

## The Kubernetes target

Local clusters only. `kind` is the verified path.

- `kubectl` always gets a dedicated kubeconfig and context; `KUBECONFIG` and `current-context` are
  ignored. The kubeconfig must be private, must use static credentials, must have no proxy, and
  must point at a loopback API server. The cluster's `kube-system` UID must match the operator config. That UID identifies a
  cluster, it does not authenticate one.
- The namespace must be labelled for AgentBreeder and enforce the `restricted` Pod Security level.
- The agent runs as UID 65532 with a read-only root filesystem, no capabilities, resource limits and
  no service account token. Secrets are mounted read-only and go to `kubectl` on stdin. Images are
  referenced by digest.
- Refused rather than weakened: `mcp_access` and runtime enforcement.

### Measured on the 2026-11-10 run

| Check | Result |
|---|---|
| Agent pod to API server (service address, node addresses), kubelet, local registry | Blocked; a control pod with no policy could reach each one |
| Agent to the model gateway | Reachable |
| Another pod to the agent | Blocked |
| Privileged pod in the namespace | Refused by Pod Security admission |
| 15 stack secrets and provider keys in the Deployment, job logs, terminal log, `ps`, `docker inspect`, generated manifest | None found |
| `remove` | No labelled object left; gateway key revoked |
| Kill switch (`disable`) | Stopped the local and cluster workloads |

The metadata address is blocked by policy too, but a `kind` cluster has no metadata service, so the
control pod could not reach it either and that row proves nothing.

### Limits of this topology

The stack's services stay in Docker. The `kind` node is attached to the stack's agents network, so
pods use the same service names as a local container. Attaching it also exposes the node's API
server and kubelet ports to agents on that network; both require credentials. The local registry on
`127.0.0.1:5001` is unauthenticated. Secrets are unencrypted in `kind`'s etcd. `kubectl port-forward`
is the only way in: there is no public endpoint. This shows the deployer working, it is not a
production design. Helm, external secret stores and real-cluster certification are tracked in #770.
