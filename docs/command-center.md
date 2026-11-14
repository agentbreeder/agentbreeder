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
  Logs, Registry ▸ Prompts, Eval sets, Tools, Knowledge, MCP servers, Service definitions and Runtimes,
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
  - A service with no running container (after `services down`, say) shows its last host ports
    as "not listening (last: …)". Stop services is off with "The services are stopped." when
    every service is absent; it stays on for exited containers, which `services down` also
    removes (#882, on `main`, not in a published preview).
  - Release candidates carry a violet "candidate, temporary" label.
  - Notices belong to their screen and clear when you leave it.
  - Measured cost figures appear only for the onboarded Northwind example.
- **GRANT_PENDING**: after Connect local cluster, the Kubernetes screen shows "Next: accept the
  grant change" with a button to Services. The screen works this out from `cluster status` (the
  cluster is attached but not in the accepted grant), the same condition `next --json` reports as
  GRANT_PENDING. It does not call `next`. Deploy to cluster stays off, with that reason, until the
  reviewed grant has been started (`services up`), so the console no longer offers a `promote`
  that would fail with `KUBERNETES_UNCONFIGURED`.
- **Nothing running locally**: Deploy to cluster promotes the release that is running locally
  (`promote` reads the agent's ready local deployment, not the release history). When no local
  deployment is ready (for example after it was removed), the card says "No ready release is
  running locally." and Deploy to cluster stays off with "Needs a release running locally"
  instead of offering a `promote` that fails with `NO_LOCAL_RELEASE` (#880).

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

### The registry browser (#824): on `main`, not in a published preview

Registry ▸ one page per kind (prompt, eval set, tool, knowledge, MCP server, service definition,
runtime) for the project in scope. Prompts are edited from the detail (#826, below); import and
fork are later milestones.

- **List**: one row per name with its latest version, how many versions there are, the root the
  latest resolves from (project, shared path from `AGENTBREEDER_REGISTRY_PATH`, or built-in), its
  verify status as an icon and a word, and which versions this project declares (`agent.yaml`,
  `stack.yaml`) or locks (`agent.lock`, `stack.lock`). A version that also exists in a later root
  is resolved from the first one, as `build` does; the later copies are counted as shadowed. The
  search roots are listed in resolve order.
- **Detail** (`#/registry/prompts/NAME`): the version timeline, newest first, with root, verify
  status and use per version; one version's description, version hash, path, manifest and files
  (`registry show --json`: text files capped, binary files not shown, removed control, bidi or
  invisible characters noted); the diff between any two versions (`registry diff --json`, the
  same hunks as `diff -u` of the files, with the reason a changed file has no text hunks); and
  used-by among the projects registered in the console for the same environment
  (`registry used-by --json`):
  declared, locked and deployed separately. Deployed is recorded local state, not a live check.
- **How it reads**: the list, the roots and the declared and locked columns are in-process reads
  cached by what they read. `registry show` and `registry diff` open and hash every file of a
  version, so they run as CLI children (60-second timeout), and only a successful answer is cached:
  for at most a minute, and less when the registries change or a job ends. `registry verify` stays a background child; the pages
  say "Verifying…" until it answers, then show the last answer while a new run goes.

Tested with Playwright (in CI against the built wheel) over a copy of Northwind, with the real `registry
show` and `registry diff` and a fake for the deployed part of `used-by`: every kind lists
Northwind's versions and the built-ins, the `prompt/northwind-support` 1.0.0 → 1.0.1 diff matches
Python's `difflib.unified_diff` of the files, `tool/orders@1.0.0` shows Northwind as declared, locked and
deployed, a failed verification shows on the list and the version, and axe passes in light and dark.
Not yet run against a real deployment: that is the M8 console verification.

### Prompt editing and "Move agent to this version" (#826, first part): on `main`, not in a published preview

A prompt's detail page (`#/registry/prompts/NAME`) has an **Edit X.Y.Z** card for the version
shown, when that version is in the project's `./registry`. A built-in or shared-path version
(`AGENTBREEDER_REGISTRY_PATH`) is not edited in place; the card shows the `registry fork` command
to make a project copy first.

- **Edit**: the version's text files (not `manifest.yaml`; not binary, truncated or
  control-character files, which are named with the reason) open in a mono textarea with a line
  gutter. The new version is picked as patch, minor or major and skips versions the name already
  has. Closing or reloading the tab with unsaved changes asks first, as do Discard, switching
  files and showing another version; moving to another page of the console does not ask yet.
  A file with CRLF line endings is flagged: the browser edits it with LF endings.
- **Review, then save** (S-04): Review changes (or Cmd+S or Ctrl+S in the text) runs
  `registry edit REF --set PATH - --version X.Y.Z --dry-run --json` with the text on stdin
  (S-12: never in argv) and shows its diff. **Save as X.Y.Z** sends the same text with
  `--expect-digest` of the reviewed copy, so what is written is what was reviewed. While a request
  is in flight the shortcut does nothing. A version that appeared meanwhile (another tab or a
  terminal) is `VERSION_EXISTS`: the console re-reads the registry, keeps the text, and the next
  review offers the next free version. `REF` itself never changes, and the version shown stays
  pinned when the registry is re-read.
- **Move agent to this version**: when the project's `agent.yaml` declares another version of the
  name, the card shows the `agent set-ref OLD NEW --dry-run` diff; **Write agent.yaml** runs it
  again with `--expect-base` (the digest of the file that was reviewed), so a file changed since
  is refused (`BASE_MISMATCH`). Then **Build and update agent.lock** starts a build without
  `--locked` (Build only and `release` build with `--locked`, which refuses a lock that lacks the
  new ref), and Build and release runs the eval-gated release.
- **Locks**: an edit save is a short write locked per project and name; a running job does not
  block it. The `agent.yaml` write also holds the project, so it waits for (is refused during) a
  build or release of that project.
- **Record**: neither write adds an audit-chain row; the record is the new version directory,
  `agent.yaml` and your git history (spec S-30).

Tested with the real `registry edit` and `agent set-ref` (server tests over a starter project) and
with Playwright over a copy of Northwind: edit, review, save 1.0.2, move the agent and start the
build; a second save of an existing version; Discard. Not yet run as the M10 acceptance (edit the
starter's prompt, move, release with the eval gate): that is batched with the milestone checks.

### Eval-set case editor (#826, second part): on `main`, not in a published preview

An eval set's detail page (`#/registry/evals/NAME`) has the same **Edit X.Y.Z** card, with the
same review and save (`registry edit … --dry-run`, then `--expect-digest`), the same version
picker and the same Move agent card. What differs:

- **Files**: only the cases file the set's manifest names (`cases:`, default `golden.jsonl`,
  the file the review's load checks) opens; the server refuses any path that is not a
  `NAME.jsonl` at the top of the version (`EDIT_PATH`). `fixtures.sql` and other files are listed
  as not editable here.
- **Cases, not text**: each case is a collapsible row (its number, id, first turn and anything
  to fix). Opened, it edits the id, the turns (a turn that starts a new session is labelled and
  keeps its `new_session` key), the checks (reply matches, reply does not match, tools called, tools
  not called, sources cited: one item per line, kept as typed; an emptied list is left out) and
  the reference answer. A field you do not change keeps its original value exactly (a number id,
  a pattern's spaces). Add a case and Remove case change the set.
- **Byte for byte**: the editor rebuilds the file from the cases. A case that was not changed is
  written back exactly as it was read (blank lines too), so an untouched file is "No changes" and
  the review shows only the edited cases, and a case edited back to what it was is written as
  its original line. A changed or new case is written the way Python's `json.dumps` writes it,
  keeping its other keys (`category`, other `expect` keys) in their order; in that case a
  number such as `1.0` is written as `1`, and keys that look like integers come first (the
  browser's JSON).
- **Checks before review**: a case with no id, the same id as another, no turn or no reference
  answer is marked with "!" and the reason (the field is marked invalid), and Review changes
  stays off until it is fixed; so does a set with no case or more than 1000. The review then loads the edited set as `release`
  does: the CLI's `EVAL_INVALID` (for example `golden.jsonl:3 needs id, turns, reference`) is
  shown at its case, which opens, as well as in the notice; the next edit clears it.
- A file that is not one JSON case per line opens as text, with the line that does not parse.

The checks are the eval gate's: a release of an agent that uses the new version must pass them.
For later kinds the editor takes a spec per kind (`center/edits.py` `KINDS`, `lib/edit.js`
`EDIT_KINDS`): the files it opens, the body (text or cases) and a confirm tier; a kind whose
files run in agents needs the "this code runs in agents" checkbox before Save (`code_confirmed`,
else `CODE_NOT_CONFIRMED`). No kind uses that tier yet.

Tested with the real `registry edit` (server tests over a starter project: a saved case, and a
case without a reference refused as `EVAL_INVALID` at line 3) and with Playwright over Northwind's
61 cases: one case edited is the only changed line of the saved 1.0.2; a new case is checked
before review; Discard. The M10 acceptance (a broken case blocks a release) is batched with the
milestone checks.

### Tool detail (#828, M12): on `main`, not in a published preview

A tool's version card in Registry ▸ Tools (`#/registry/tools/NAME`) adds, above its files:

- **Functions**: one row per `tool.yaml` function with its module and Python function, the
  signature the CLI read from the module with `ast` (`registry show --json` `functions`, #930: the
  source is parsed, never imported, and size-capped), the description and the parameters (what the
  model sees). A function with no signature says why (the module is over the size cap, does not
  parse, or the module or function was not found); an unreadable `tool.yaml` says so instead of a
  table. Everything from `tool.yaml` renders as text.
- **Dependencies**: none of its own; a tool runs with the locked packages of its agent's runtime
  (`runtime/adk`, linked).
- **Grants**: available to the agents that reference it in `agent.yaml` (the Used by card). MCP
  grants are M15's.

The source is the Files list. The code editor comes in a later M12 PR.
Tested with Playwright over a copy of Northwind with the real `registry show`: `tool/orders`
shows both functions and their signatures, a module whose top level raises still shows its
signatures (nothing is imported), a renamed function says why it has no signature, other kinds
show no Functions, and axe passes in light and dark.

### Tool execution log (#828, M12): on `main`, not in a published preview

Below a tool version's Functions, **Execution log** lists the calls the agent runtime recorded,
newest first: time, agent, version, function, status, latency, turn id, trace id and database
role (console-inserted rows marked `(console)`). The console runs `agentbreeder tool log REF
--project DIR --environment ENV --json` ([`docs/commands/tool.md`](commands/tool.md)) as an
uncached child; the console server never reads a database credential. Choices: this version or
every version of the name, the last hour, 24 hours, 7 days or 30 days, 50 to 1000 rows, and one
agent (from the agents seen in the log).

- **It is what the runtime claims it called.** The agent's database role can insert any row; the
  CLI validates every value and the console checks the answer again before the browser sees it.
- **Arguments are customer data.** They and the error text are read only after **Show
  arguments** (marked "Customer data"), for that view only: never cached, stored, logged or put
  in the page address, and taken off the screen at once on **Hide arguments** or any change of
  choice, before the next read returns. They are
  redacted of the stack's secrets by the CLI. Results are not stored; the trace holds them, and
  the stack's Phoenix URL is linked.
- **A refusal shows as "Unavailable: reason" with the next step**: for example, an owner-managed
  database without the console role (`CONSOLE_ROLE_MISSING`) names
  `service/postgres-pgvector@1.2.0` with `console_database`, then `services up --update`. "No
  calls recorded yet" (`TOOL_LOG_NO_TABLES`) is not a refusal.

Tested: server tests over a scripted `tool log` (fixed argv, never cached, bad queries refused
before the CLI, arguments dropped unless asked for, hostile values nulled, at most four reads at
once), the real CLI's `--json` answers and refusal passed through the same checks, and Playwright
over a copy of Northwind with a scripted `tool log` (calls without arguments until asked,
arguments gone on Hide while a slow read runs, scope, window and agent reach the CLI, a refusal
and an empty log, axe in light and dark). Not yet run against a real stack's database: that is
the M12 acceptance, batched with the milestone checks.

### The Audit view (#824, part 2): on `main`, not in a published preview

Governance ▸ Audit shows the verify result in words and the project's audit rows, 50 to a page,
newest first. Older and Newer step through pages with `audit list --before SEQ`: the server asks
for 51 rows to know whether an older page exists.

- **Agent**: `audit list --json` names the agent of every row that has a deployment, including
  removed ones such as release candidates (shown as the agent, marked "candidate"). A row with no
  deployment takes the agent of its attempt's other rows on the same page (an `attempt_started`
  row comes before its deployment exists). A `release` row has no deployment either. It belongs
  to the active deployment running the image in its `RELEASE_IMAGE` reason (the check `promote`
  makes), or to the candidate that ran that image on the same page. Otherwise it says "not
  recorded". A known agent's name links to its detail (`#/agents/NAME`); a candidate's row links
  to the agent it belongs to.
- **Mode and reasons**: each row's enforcement mode and reason codes. A release row also shows
  its eval run (`Evals 12/12, run ID`) and, when its agent is known, the report file `release`
  wrote, as a path relative to the project (`deploy/NAME.eval-RUN.json`, the default output).
  The run id links to the eval report viewer (below).
- **Production**: an agent with a ready local production deployment links to Try it and shows
  its endpoint as text, once, on that agent's newest row on the page. The link is today's
  deployment, not the one running at the row's time. Candidates never link to Try it.
- **Paging**: focus moves to the page's status line, which says which page is shown, that it is
  loading, or that it could not be read (with Retry). Newest and Newer stay available after a
  failure.

Tested with Playwright: the agent, mode, reasons and release evidence of a release story; paging
over 120 rows and back; focus after paging; a failed page and Retry; one Production link per agent;
exactly 50 rows has no older page; untrusted reason and agent text shows as text; axe passes in
light and dark on the first and second page. Not yet run against a real deployment: that
is the M8 console verification.

### The eval report viewer (#932): on `main`, not in a published preview

An Audit release row's run id, and each scored release on an agent's Releases tab ("Eval
report"), open `#/audit/evals/RUN/AGENT` (or `#/audit/evals/RUN` when the agent is not known).
The server reads `evals show RUN --project DIR --state-dir STATE [--agent NAME] [--content]
--json` with the registered project's path and its stack's state directory (for redaction); only
the run id (`YYYYMMDD-HHMMSS-<6 hex>`), the agent (the agent name rule) and `content=1` come from
the request. Reads are never cached, and an answer that does not match the contract
(`docs/evals.md`, schema version 1, the requested run, an agent name) is refused with
`EVALS_INVALID`. The redaction needs the stack's state directory, so without a running stack the
server reads nothing (`STACK_NOT_UP`); `agentbreeder evals show` in a terminal still works. A
route whose agent is not an agent name reads nothing either, rather than dropping the agent.

- **Eval run**: the score in words ("2 of 3 cases passed (66.7%)"), shown as passed only when
  the saved header says every case passed (it counts cases skipped as malformed); the eval set,
  judge, mean correctness, median latency, the endpoint evaluated (the candidate, which
  `release` removes afterwards), the report file, and the report's notes.
- **Cases**: failed first, then passed after a retry, then passed, each group in the report's
  order: case id and category, result, failed checks, correctness, the judge's reason, latency
  and the trace id (with Copy; read it with `agentbreeder traces --trace ID`).
- **Replies and explanations** are read only after "Show replies and explanations"
  (`--content`): each case then has a closed "Reply and explanation" disclosure. They are model
  output, redacted and capped by the CLI, and shown as text with controls made visible. If that
  read fails, Retry and "Show the report without replies" are offered.

`GET /api/projects/ID/evals` (`evals list`, the project's saved reports, newest first) is there
for later screens; no screen lists reports yet. `static/lib/evals.js` holds the paths, the route
and the case ordering as pure functions, with its exports listed at the top.

Tested with pytest (the argv against the real `evals` parser, bad run ids and agents never reach
the CLI, no caching, off-contract answers refused) and Playwright with the fake CLI: the Audit
and Releases links, the summary and case order, the tone following the saved header, content on
request, a failed read with Retry, a failed content read and the way back, a bad run id or agent
name reading nothing, and axe in light and dark. Not yet run against a real release; that
is part of the M8 console verification.

### The guided path and Create starter (#820): on `main`, not in a published preview

The console reads `agentbreeder next PROJECT --environment ENV --json` (the contract in
`next.md`) for the project in scope, once when the scope changes and again when one of its jobs
ends. The server checks the answer with the CLI's own models (`guide.Guide`) and refuses one that
names another project (`GUIDE_INVALID`); on a failed run it keeps the last good answer, marked as
last known. The console never runs a command from the answer: each `fix.action` maps to a fixed
console move (Build starts the build job; services, keys and release open their screens, where
the grant review and the cost note come first; Kubernetes steps open Targets ▸ Kubernetes).
`command`, `note` and paths are shown as text to copy, and the one link the CLI can give (the
Gemini key page) opens only on a click, with its host shown.

- **Home checklist**: every step with its state as an icon and a word (Done, Next, Blocked, To
  do), its reason, and for the current step its button, command and note. A stale `agent.lock`
  (`LOCK_STALE`, which `next` gives no action) gets **Build and update agent.lock**: the build job
  without `--locked`, the plain `build` that rewrites the lock to `agent.yaml`'s refs. The CLI
  command stays to copy; the guide's note that the console's build is `--locked` is not shown.
  While a job runs in the project, the list says it is checked again when the job ends.
- **A busy stack**: `services plan` is a services write, so while another one (`up`, `down`,
  `start`, `stop`, `restart`, `cluster up|down`) runs on the stack the grant review fails with
  `SERVICES_BUSY`. The Services screen then says "Another operation is running on this stack;
  retry when it finishes" and shows no grant (not the last one read), and starting services is
  refused with the same code and words.
- **Next pill** in the top bar ("Next: Services", "Blocked: …" or "All steps done"), for the
  project in scope; it opens the Home checklist.
- **Result row**: a finished job of that project shows where the path stands now ("Next step:
  Release (eval gate). …").
- **Empty states**: Home with no project offers Create starter as its primary action; an empty
  Deployments screen offers the guide's next step.
- **Create starter**: a name only. The server checks it with `new agent`'s rule, checks
  `~/agentbreeder-projects` as Onboard Northwind does, runs `agentbreeder new agent NAME --model
  gemini --dir ~/agentbreeder-projects/NAME --json` and registers the result. A refusal shows
  `new agent`'s own coded message (for example a folder that is not empty).
- **Try it**: the Try it step's button opens Try it for the project's agent (#823, below), with
  the `invoke` command to copy as well. So the console path goes from Create starter to the
  first reply. That path has not yet been run end to end against a real deployment.

Tested with Playwright against a fake CLI that serves `next --json` answers checked by the same
models (every step's state, title and reason match the JSON; a stale lock; a failed read; the job
result row after a build), with axe in light and dark. Not yet run against real services: that is
the M4 verification (`superpowers/verification/M4.md`).

### Logs (#821, first part): on `main`, not in a published preview

**Logs** (in the sidebar, and a **Logs** link on every Services and Deployments row) reads one
snapshot of a stack service's or a deployment's logs:

- a service: `agentbreeder services logs PROJECT NAME --tail N [--since D] --json`;
- a deployment: `agentbreeder logs ID --stack PROJECT --environment ENV [--previous] --tail N
  [--since D] --json`. `--previous` (the container before its last restart) is offered for
  Kubernetes deployments only.

Only the name or deployment ID, the line count (Last 200, 1000, 5000 or 10000), `--since` and
`--previous` come from the browser, and the server checks each against the CLI's own rules before
anything runs (`TAIL_INVALID`, `SINCE_INVALID`, `PREVIOUS_INVALID`; a name or ID of the wrong
shape is a 404). The CLI redacts every line, failing closed (`REDACTION_INCOMPLETE`), and escapes
controls. The server checks the answer's shape (`LOGS_INVALID` otherwise) and passes on only the
known keys. A log read is never cached, never stored in `center.sqlite3` and never shown stale: a
failed read shows its own coded error.

In the viewer:

- **Filter** keeps the lines that contain the text. **Level** keeps warnings and errors, or errors
  only, from the levels the CLI gave each line (`loglevels.py`, a heuristic). It filters the lines
  already read; it is off, with its reason, when the log's format is not known. **Search** marks
  matches, counts them and steps through them with Previous match and Next match (Enter and
  Shift+Enter). **Wrap lines** wraps long lines. **Copy** copies the lines shown, in their escaped
  form (a control character is never put on the clipboard as itself).
- Unwrapped, only the rows in view (plus a margin) are in the page, even with 10,000 lines.
  Wrapped rows are all in the page; the browser skips laying out the ones out of view.
- Notes under the lines: how many lines were read, that known secret values show as `[redacted]`
  (logs can still hold customer messages and model output), when the read's 4 MiB limit cut it,
  and when the lines are from the previous container. With a filter on, how many lines are shown.
- Empty states, not errors: no lines in the range; a pod with no output yet (its reason, or
  `POD_NOT_RUNNING`); and `LOG_UNAVAILABLE`, when Kubernetes no longer has the container's log.
  An address that names no service or deployment says so.

**Not yet**: Follow (with pause and "Jump to latest") needs one CLI child per open log, with the
S-10 limits; it is the next part of #821. Then `watch` per project, and the M5 console
verification (both deployments, local and kind, and the leak scan of API output).

Tested with Playwright against a fake CLI (10,000 lines windowed, the level and text filters,
search, wrap, copy, the coded empty states, a deployment's Previous container) with axe in light
and dark, and on the server with a fake CLI (argv, refused options, the answer's shape, no cache).
Not yet run against real services.

### Try it (#823, first part): on `main`, not in a published preview

**Try it** chats with a project's production deployment. It opens from a **Try it** link on a
Deployments row (production, local and `ready` only) and from the guided path's Try it step, at
`#/agents/NAME/try`. Each message runs:

`agentbreeder invoke NAME - --stack PROJECT --environment ENV --json [--session ID] [--customer ID]`

- **The message goes to `invoke` on stdin, never in argv**, which every local user can read
  through `ps` (S-12). It is at most 4,000 characters (`invoke`'s own limit).
- **Production only.** A name ending in `-candidate` is refused (`CANDIDATE_REFUSED`), and
  `--candidate` is never sent. A Kubernetes deployment, or one that is not `ready`, shows why
  instead of the chat (`invoke` refuses them the same way).
- **Not a job, and not stored.** The turn is an uncached read, with no stale answer, `invoke`'s
  180 s deadline plus its state checks (200 s), and a child that a console shutdown stops.
  Nothing goes into `center.sqlite3`, which survives `--purge`. The conversation is kept in the
  tab's memory: it survives moving between screens, a reload clears it, and it never goes into
  browser storage.
- **One message in flight per tab.** The console as a whole has at most 4 at once
  (`TRY_BUSY`), and a refusal like that is "Not sent".
- **Replies are plain text.** There is no Markdown and no auto-linking, and no link is ever built
  from agent output. Controls show as visible escapes.

The server passes on only these checked fields of `invoke --json`'s answer:

- the reply (at most 65,536 characters shown);
- session, customer and turn IDs (the runtime's ID rule);
- the model alias and runtime ref;
- tools and sources (names and titles, at most 50 of each);
- the model-call and token counts.

It adds the latency it measured. Each reply has a **Turn details** inspector with the tools,
sources, latency, tokens (prompt and output, over N model calls) and the trace ID as text. There
is no trace link (the answer's `trace.url` stays null). **Cost** is the turn's cost in USD that
`runtime/adk@1.2.0` and later report (`cost_usd`, the sum of LiteLLM's per-call estimates; see
[`cost.md`](cost.md#per-turn-cost-runtimeadk120)), shown in the inspector and its summary, such as
"$0.000444". The server passes it on only as a finite number from 0 to under 1,000,000, otherwise
null. Older runtimes don't report it, and neither does 1.2.0 when a call in the turn had no cost
header: the inspector then says "Not measured" and why. Tested with a fake CLI and with the runtime's
scripted gateway responses; no live gateway cost has been shown yet.

**Trace spans** (#907, on `main`, not in a published preview): a turn with a trace ID has a
second disclosure beside Turn details. The first time it is opened, the server runs
`traces --trace ID --stack DIR --environment ENV --json` (a fixed argv, never cached; see
[`traces.md`](traces.md)) through `GET /api/projects/ID/traces/TRACE_ID`. It shows the trace in
one line (status, latency, span count, models, total tokens, tool calls), how the trace was
attributed to a deployment (`exact` or by time window), and the span tree as nested lists: each
span's name, kind, status, latency, model, tool and tokens, and its error text. The server passes on
only those fields, and the Phoenix address, session and turn IDs stay on the server. `traces` never
reads prompts, replies or tool arguments and results. Error text is redacted and cut, but it is the
runtime's own text and can quote what the agent was given. At most 4 trace reads run at once
(`TRACE_BUSY` otherwise). Phoenix can receive a trace a few seconds after the reply, so
`TRACES_NOT_FOUND` says so and offers **Read again**; a stack without `service/phoenix` says it has
no traces. Tested with a fake CLI; the panel has not been run against a live Phoenix yet.

A later message continues the conversation's session (`--session`); **New conversation** starts a
new one. **Customer ID** is optional and sent as `--customer`.

The screen says three things before you send:

- each message calls the model and costs money;
- the local endpoint is unauthenticated, so anything on this machine that can reach its port can
  call it and name any customer or session ID (#780);
- replies are plain text, and the conversation stays in the tab.

A failure says whether the message was sent. "Not sent" means nothing reached the agent. "No
usable reply" means the agent may have run the turn, so sending it again is a second turn. This
follows `invoke`'s `sent` flag. A request the console itself refuses (a 4xx such as `TRY_BUSY`)
and an `invoke` exit 2 without its JSON (a usage error) also count as "Not sent". No answer at
all, a timeout or a crash count as possibly sent.

Try it is also the **Try it** tab of the agent's detail (below), at the same address.

### Onboard an agent.yaml alone (#823, third part): on `main`, not in a published preview

Home's **Add a project** card has a fourth form, **An agent.yaml alone**: the path of an
`agent.yaml` (or its directory), the agent's `name:` and a model provider (gemini, or anthropic,
openai and openrouter, which are config-only), with an optional model id. It is a three-tier
confirm:

1. **Preview** runs `agentbreeder new agent NAME --model PROVIDER [--model-id=ID]
   --stack-for=PATH --dry-run --json` and shows what it would write (`stack.yaml` and
   `stack/litellm/config.yaml`, with their digests), the gateway aliases, the key the stack needs,
   `review_by`, any warning, and every registry ref the agent uses: where it resolved (this
   project's registry, `AGENTBREEDER_REGISTRY_PATH` or built in) and whether its kind is code
   (`tool`, `service`, `eval`, `mcp`, `runtime`). Nothing is written. Editing any field drops the
   preview.
2. When a code ref comes from outside the built-in registry (a starter's own eval set, for
   example), a **This code runs in agents** box names it and says that building and releasing
   runs it as you, with the stack's model key and database, and that the eval gate is a quality
   gate, not a security review. Its checkbox must be ticked.
3. **Write stack.yaml and add project** sends the previewed `agent_sha256`. The server runs the
   dry run again with `--expect-digest`, refuses `CODE_CONFIRM_REQUIRED` if that run reports code
   and the box was not ticked, then runs the write with `--expect-digest` (a changed `agent.yaml`
   is `DIGEST_MISMATCH`: preview again) and registers the directory of the path it checked,
   which applies the ownership check again (a `stack.yaml` others can write is refused). The
   project then opens on Services. If the files were written but the project was not added, the
   error says so: fix the cause, then add the directory under **An existing project** (previewing
   again would meet `STACK_EXISTS`).

The confirm is a prompt, not a security boundary: anyone with a session can already run project
code. Refusals show `new agent`'s own coded message (for example `NAME_MISMATCH`,
`STACK_EXISTS`, `STACK_FOR_UNSUPPORTED` for an agent with tools, knowledge or MCP servers).
Request text reaches the command only as checked `--flag=value` words.

Tested on the server against a fake CLI and once against the real `new agent --stack-for` (a
starter's `agent.yaml`: preview, refused without the confirm, written and registered with it),
and with Playwright and axe in light and dark, where the fake CLI hands `new agent --stack-for`
to the real command (every other read is faked). An onboarded
project has not yet been taken through `services up` and `release` from the console: that is the
M7 verification.

Tested with Playwright against a fake CLI with axe in light and dark, and on the server with a
fake `invoke` (argv and stdin, refused requests, the answer's allow-list, no stale answer, nothing
stored, the in-flight cap, shutdown). Not yet run against a real deployment; that is the M7
verification.

### Agents (#823, second part): on `main`, not in a published preview

**Agents** (in the sidebar, under Home) lists every deployment of every stack on this machine
from one `agentbreeder list --all --json`, which reads the default state and every stack's state
without creating, migrating or locking anything. An answer that is not its documented shape
(`schema_version` 1) is shown as an error, never as an empty list. Each row shows the agent, its
project, target, state, image, identity check, endpoint and last release:

- **Candidates** (`NAME-candidate`) are marked "candidate, temporary" in violet: a release
  removes its candidate when it ends.
- **Project**: a stack whose recorded project path (resolved, so a symlinked path matches) and
  environment match a project registered in this console links to the agent's detail. Any other
  stack shows "not registered" (add its project on Home to open it), and the default state shows
  "default state". Stacks that could not be read are listed under **Not read** with their code
  (`STATE_BUSY`, `STATE_NEEDS_MIGRATION`, …). A modified click on an agent opens a new tab.
- **Identity check** is the value recorded at deploy or by the last `reconcile` (`compliant`,
  `noncompliant: CODE`, `unverifiable: CODE`, `unbound`), with the time the deployment last
  changed. Nothing here calls the identity provider.
- **Last release** is the newest release entry of the project's audit log ("passed 5/5", "eval
  failed 2/5", "failed (CODE)", "no outcome recorded", "rolled back"), with its time. Audit rows
  of a release name no agent, so they count as the runs of the project's `agent.yaml` only: any
  other agent deployed in that stack shows "unknown", as do unregistered projects. The list reads
  each registered project's releases one after another (each is a `services env` and an `audit
  list` child), again whenever the list is read again.

Opening an agent from "All projects" switches the scope to its project. The detail
(`#/agents/NAME`) has:

- **The gate strip**: Build → Candidate → Evals n/N → Identity → Production. Build, Candidate and
  Evals come from the newest release run in the audit log. A run is the `started` row, which
  `release` writes once the image is built, paired with its outcome (`promoted`, `eval_failed`, or
  `failed`, which records only its error). So an eval run that crashed shows Build and Candidate
  passed and Evals failed with its code, and a run with no outcome (the candidate failed to
  start, the run stopped, or it is still running) leaves Candidate and Evals "not run". A running
  `NAME-candidate` shows Candidate in progress. Identity and Production come from the production
  deployment. Production running another image than the newest promoted or rolled-back one (for
  example after a plain `deploy`) shows "differs from the last release"; a failed run, or one with
  no outcome, is not counted as a release. A release that failed at promotion, after its evals passed, says so
  under Production. Each step has an icon and a word, never
  colour alone. An agent deployed with a plain `deploy` and never released shows the first three
  steps as "not run".
- **The identity strip** shows what `stack.yaml` declares for the agent
  (`environment.agents.NAME`): that it is declared (the static demo principal `services up`
  writes for a declared agent), its sponsor and its review date (a date before today, where the
  browser is, means the gate refuses deploys), and the recorded identity check. Nothing checks
  that `services up` has run. `stack.yaml` is read in the console process with the same model as
  `services`, through the same safe file read as `agent.yaml` (a regular file, no symlink, at most
  64 KiB); an invalid file shows "unknown" with a message that names fields, never values. The
  principal's own fields (owner, roles, granted actions) wait for #909, which adds them to
  `list --json`.
- **Tabs** (a navigation list with the current tab marked): **Overview** (the production
  deployment and what `agent.yaml` declares: runtime, model alias, prompt, tools, knowledge, MCP
  servers, evals and threshold), **Try it** (above), **Releases** and **Logs** (a link to the log
  viewer for each running deployment of the agent, candidates included). Switching tabs keeps
  focus on the tab and does not read the views again. **Releases** lists the release runs and
  rollbacks among the newest 1,000 audit rows (`audit list --limit 1000 --json`, the CLI's
  maximum), newest first: time, result, image and who ran it. When that limit was reached it says
  older releases are not shown. For an agent that is not the project's `agent.yaml`, it says the
  project's releases are another agent's and shows none.

The heading and the page title name the agent ("Agent demo"), and the sidebar keeps Agents
current on the detail. Going straight from one agent's detail to another's (back and forward)
keeps focus where it was.

Tested with Playwright against a fake CLI (the list across stacks, opening from "All projects",
both strips, every tab, an agent with nothing deployed, another agent's releases), with axe in
light and dark, and on the server against a fake CLI (argv, linking rows to projects, unexpected
listings, pairing release rows, rollbacks, the 1,000-row cut, `stack.yaml` errors that never echo
values, a symlinked `stack.yaml`). Not yet run against real deployments; that is the M7
verification.

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
  stored. Sign-in events arrive with the password milestone. `registry edit` and `agent set-ref`
  (CLI on `main`; the console's editors come later) change only the project's files (and git
  history, if you commit them); the console will keep no record of them.
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
