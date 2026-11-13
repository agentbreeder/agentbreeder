# M0b verification: console shell, with the walkthrough fixes (#816)

Status: **verified from `main`, partial in the browser** (356e145, after #874 merged, 2026-11-13,
macOS 13.7.8, Docker Desktop engine 27.5.1). From a wheel built from `main` in a clean sandbox,
every a2 capability ran through the console's API: Northwind onboarded, services started after
the grant review, the release passed 61/61, `kind` was connected and its grant change accepted,
`promote` passed 61/61 on the cluster, and the audit chain verified. In Chrome (Claude in Chrome)
every screen was opened and the deployments, the cluster and the services were removed or stopped
from the UI; onboarding, the grant review, a full release, Connect local cluster and Deploy to
cluster were not clicked in the browser (they ran through the API). No key value or one-time link
appeared in any saved output, and Docker ended at the pre-run snapshot after three leftovers of
the run's stack were removed by hand (see Leak scan and teardown).

This is on `main`, not in a published preview.

## What changed (three PRs)

| PR | Merged | Content |
|---|---|---|
| #871 | b5515c6 | CSP with Trusted Types (`trusted-types 'none'; require-trusted-types-for 'script'`), vendored Preact + htm (htm 3.1.1 standalone module, SHA-256 pinned, licences in the wheel), the source guard outside `vendor/`, S-35 visible control characters, `node --test` and Playwright harnesses (fake CLI, axe in light and dark, zero CSP violations) |
| #873 | e373754 | The shell (hash router with the `#t=` token consumed first, per-route title, focus to the `h1`, skip link; sidebar, scope switcher, activity pill and Jobs dock, user menu; "Session ended", "Sign in" and "Console not reachable" states) and every a2 screen with the #811 walkthrough fixes, GRANT_PENDING on the Kubernetes screen, S-35 `<Text>` for every project, job and CLI string |
| #874 | 91b828a | "Command center" renamed "console" in the UI, CLI help and `docs/` (the page keeps its file name, `docs/command-center.md`); golden help regenerated on 3.12 and 3.13; the verification template `TEMPLATE.md` |

Every PR had a different-model review (Claude Sonnet, read-only) until it approved: #871 and
#873 in two areas each (security; correctness, CI and UX), two rounds each; #874 in one round
(approved with nits, two fixed). The review tables are in the PR bodies. Per the #874 PR body, the
full suite on the #874 branch: 2848 passed, 34 skipped (opt-in live and real-Docker tests), in 300 s; `node --test`
44/44; the `console-js` and `console-ui` CI jobs passed on #874, and the `Focused baseline`
workflow passed on the push to `main` (356e145). Coverage of the changed modules in #874: `center/server.py` 95%,
`commands/up.py` 94%.

## Wheel from main (clean sandbox)

`main` at 356e145, no tracked changes. Wheel built with `python -m build --wheel`
(`agentbreeder-3.0.0a2-py3-none-any.whl`); it holds the 27 tracked files of
`center/static/` and nothing else there. Every command ran under `env -i` with an empty `HOME`
inside the sandbox, `TMPDIR` inside the sandbox, `PIP_NO_CACHE_DIR=1`, a fresh Python 3.12
venv with `agentbreeder[eval]` from the wheel (9 s to install), and a `PATH` of the venv,
a sandbox `bin/` (links to `kind` and `uv` only), `/usr/local/bin` and the system directories.
AgentBreeder refuses `DOCKER_HOST`, so the script created a Docker context named `sandbox` in the
sandbox `HOME` pointing at the engine socket. `doctor` passed (Docker, Compose v2, this install on
`PATH`). Provider keys were read from `~/.env` by the driver and sent only in the `services-up`
request body (Gemini and Tavily; OpenRouter was not set).

The scripts were `run.sh` (sandbox, install, console start and stop, leak scan, Docker snapshot)
and `accept.py` (stdlib only, driving the real console over HTTP with the same headers as the UI).
Each phase started its own console (`agentbreeder up --no-browser --port 8095`) for a fresh
one-time link: the API phase, the first browser pass, the cluster phase, and the second browser
pass. Stopping a console left the services and deployments running, as documented. Saved outputs:
the acceptance logs, each job's log and each view's JSON, and the four console logs.

Another lane's verification shared the Docker engine and the single `kind` cluster that night, so
the cluster steps waited until that lane had run `cluster down` (the API phase ran with `kind`
removed from the sandbox `PATH`, which is why its cluster steps fail below).

### API phase (one console)

| Check (in the order run) | Result | Time |
|---|---|---|
| Sign in with the one-time link | session issued | 0.0 s |
| Onboard Northwind (packaged example) | 200, project `ae0a5180a290` | 0.6 s |
| Grant review (`services plan`) | digest `078a7f6f34f2…`; keys asked: Anthropic, Gemini, OpenAI, OpenRouter, Tavily | 0.8 s |
| `services-up` with the reviewed digest and two keys | succeeded, exit 0 | 186.6 s |
| Services view | 200, 5 services | 1.3 s |
| `release` | succeeded: `61/61 evals passed`, image `sha256:aafd093f…` | 172.1 s |
| Deployments view | 200, 1 row (local) | 1.2 s |
| `cluster-up` with `kind` off `PATH` | failed, exit 2: `[CLUSTER_TOOL_MISSING] kind is not on PATH; https://kind.sigs.k8s.io/…` | 1.0 s |
| Cluster view | 200, no error | 0.7 s |
| Grant review again | digest unchanged, `078a7f6f34f2…` (no cluster attached) | 0.7 s |
| `services-up` with that digest | succeeded, exit 0 | 14.6 s |
| `promote` without a cluster | failed, exit 2: `[KUBERNETES_UNCONFIGURED] Environment dev has no kubernetes block` | 1.0 s |
| Deployments view | 200, 1 row (local) | 0.9 s |
| Audit view | 200, 19 rows; verify `ok`, 19 rows | 2.4 s |
| Activity | 200, nothing running, 5 recent jobs | 0.0 s |

The phase took 384 s in all.

### Cluster phase (a new console, after the other lane's `cluster down`)

| Check (in the order run) | Result | Time |
|---|---|---|
| Sign in with the new console's one-time link | session issued | 0.0 s |
| `cluster-up` | succeeded, exit 0 | 52.4 s |
| Cluster view | 200, no error | 0.9 s |
| Grant change after `cluster up` | new digest `99a78641e004…` | 0.8 s |
| `services-up` with the new digest | succeeded, exit 0 | 12.8 s |
| `promote` | succeeded: `localhost:5001/agentbreeder/northwind-support@sha256:dfeb0a3d…` promoted, `61/61 evals passed` | 181.5 s |
| Deployments view | 200, 2 rows: local ready, kubernetes ready | 0.5 s |
| Audit view | 200, 29 rows; verify `ok`, 29 rows | 1.6 s |

The phase took 251 s.

### Jobs recorded by the console

`center.sqlite3` (schema version 2) kept all 14 jobs across the four console starts:

| Job | Started from | Result | Time |
|---|---|---|---|
| services-up | API | succeeded | 185.0 s |
| release | API | succeeded | 171.8 s |
| cluster-up | API | failed (no `kind`) | 0.5 s |
| services-up | API | succeeded | 12.8 s |
| promote | API | failed (no cluster) | 0.7 s |
| build | Chrome | succeeded | 1.0 s |
| reconcile | Chrome | succeeded | 0.6 s |
| cluster-up | API | succeeded | 51.5 s |
| services-up | API | succeeded | 11.9 s |
| promote | API | succeeded | 181.2 s |
| remove (kubernetes) | Chrome | succeeded | 33.6 s |
| remove (local) | Chrome | succeeded | 1.4 s |
| cluster-down | Chrome | succeeded | 4.8 s |
| services-down | Chrome | succeeded | 11.9 s |

## Browser click-through

Claude in Chrome, in the installed Chrome on macOS 13.7.8, against the console from the wheel.
Two GIFs (25 and 50 frames) go on #816, not in the repository. Both were checked frame by frame
before upload: no provider key, no one-time link (the action labels show only the action name),
and no home directory. They do show the sandbox path, which contains the macOS user name, on
Home, and the audit actor `os:<user> (unverified)`.

**Pass 1** (after the API phase, `kind` off `PATH`), 2026-11-13 01:04–01:05:

- The one-time link signed in and was gone from the address bar (`#/`).
- Home: the project, its environment and last job; the Northwind example and add-a-project cards.
- Services, under "All projects": "This screen works on one project. Choose one:", then the
  five services (the proxy "internal only") and "27 versions available. All passed verification".
- Build and release: the violet "temporary" candidate label and Northwind's measured cost
  ("61 evals, about 4 minutes and about $0.24"). **Build only** started a real build job: the
  Jobs dock opened and the finished job collapsed to "Build finished."
- Deployments: the local deployment, ready. **Re-verify (reconcile)** ran and its identity check
  changed from "not checked" to "compliant".
- Kubernetes: "kind is not on the console's PATH. Install it: … Then stop and restart
  agentbreeder up: the console reads PATH when it starts", and a reason under each disabled button.
- Audit: each row's time, action, decision, deployment, mode, reasons and actor; Verify read
  "Chain intact: 21 rows checked, head 1b6f6ed…".

**Pass 2** (after the cluster phase), 2026-11-13 01:14–01:18:

- The tab from pass 1, whose console had been restarted, showed "Session ended". Signing in
  with the new link returned to the screen it was on (Kubernetes).
- Kubernetes: "Cluster: running. Registry: running. In the accepted grant: yes." and
  "On the cluster: northwind-support (ready)".
- Deployments: two rows (local and kubernetes). Remove opened a confirm with Cancel focused;
  confirming started the job, the pill read "1 running", Re-verify said "Remove is running in this
  project. Wait for it, or cancel it in Jobs.", and the log streamed into the dock. Both
  deployments were removed this way ("Nothing deployed.").
- Kubernetes: **Delete cluster** (confirm: "Refused while something is deployed there") deleted
  the cluster and registry.
- Services: **Stop services** (confirm: data and generated secrets are kept) stopped all five.
- Audit: "Chain intact: 39 rows checked", with each remove, gateway-key revocation and the
  promote listed.

The page console showed no CSP, Trusted Types or other error from the end of pass 1 through
pass 2, including the sign-in page load of pass 2 (Chrome's console tracking started at the end
of pass 1, so pass 1's own page load was not watched).

## Acceptance (from the issue)

| Check | Result |
|---|---|
| Every a2 capability (onboard Northwind, services, release, deployments, kind, audit) works in the new shell through the API and in a browser | Through the API: PASS (onboard Northwind, services with grant review and keys, release 61/61, deployments, `kind` connect, grant change and promote 61/61, audit with verify). In a browser: partial: every screen opened; build, reconcile, remove (both targets), cluster delete and services stop ran from the UI; onboarding, the grant review with keys, a full release, Connect local cluster and Deploy to cluster were not clicked (they ran through the API above) |
| The owner re-walks the screens from the 2026-11-11 screenshots | not run: the owner's step |
| Both harnesses green in CI | PASS: `console-js` and `console-ui` on #874 |
| The wheel contains every static file | PASS: the 27 tracked files under `center/static/`, nothing else |

Flows that need a model key ran here, not in CI: the two eval gates (61 cases each) with Gemini
for the agent and the judge, and Tavily for search.

## Leak scan and teardown

`run.sh` read the 6 values of 12 or more characters in `~/.env` (never printed) and searched
every saved file: the 21 acceptance files (the two acceptance logs, 15 job logs and view JSON
files, the deployments list, the Docker snapshots and the project id) and the four console logs.
No key value appeared anywhere, and no one-time link outside the console logs, where `up` prints
it. (The first scan skipped the job logs and views by mistake, through a file-name filter meant
for the console logs; the review caught it, and the scan above is the corrected re-run.) A second
scan of all 176 files under the sandbox `HOME` found key values only in the stack's own secret
files (`service-secrets/*`, mode 0600, and the read-only copies mounted into containers, mode
0444 inside a 0700 directory), which `services down` keeps by design; none in `center.sqlite3`
or any log. `center/login.html` (mode 0600) holds the last one-time link, already used.

Teardown ran from the browser (both removes, cluster delete, services stop). After it, compared
with the snapshot taken before the run (containers, networks, volumes, images, `kind` clusters):
three objects of this run's stack were left, the stack's Postgres volume (kept by `services down`
by design) and the `ops` and `proxy` service images; they were removed by hand. Docker and `kind`
then matched the snapshot. No console process was left, and ports 8095, 8080 and 8900 were
closed.

## Notes

- The API phase's `cluster-up` and `promote` failures are intended: `kind` was off the sandbox
  `PATH` while the other lane held the cluster. They show the missing-tool error the Kubernetes
  screen turns into install guidance, which pass 1 then showed in Chrome.
- The release candidate's endpoint and the CLI's `Next:` lines name sandbox paths; those lines
  are in the saved job logs, not in this record.
- Seen in the browser, to be filed as follow-ups (none blocks M0b):
  - "Session ended" says "(8 hours, or 1 hour idle)" when the cause was a console restart.
  - After both deployments were removed, Kubernetes said "Nothing released locally yet",
    although a release had passed; it reads the running local deployment, not the release
    history.
  - While a remove job ran, the row Remove buttons still looked enabled (Re-verify was disabled
    with its reason). A second start would be refused with `409 [BUSY]`; it was not clicked.
  - With the services stopped, the Services table still lists their last host ports and Stop
    services stays enabled.
- Narrow screens and "Stop now" were not tested here (as in the Playwright harness).
