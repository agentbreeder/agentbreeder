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
  returns to the screen you were on. Sessions live in the console's memory, so "Session ended"
  names both causes: the tab was open 8 hours or idle 1 hour, or the console was restarted
  (#879). "Console not reachable at this address" (no answer at all) is a separate page that
  retries every 5 seconds.
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
  instead of offering a `promote` that fails with `NO_LOCAL_RELEASE` (#880). The card asks the CLI
  which deployment that is rather than picking from the deployment list: the release step of the
  console's `next --json` read runs `promote`'s own lookup (this agent's active local deployment),
  and Deploy is on only when it is `ready`. Another agent's deployment, a release candidate or a
  removed deployment never turns it on (#902). While that read is running again, has failed or
  is a last known answer, Deploy stays off and the card says so.

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
runtime) for the project in scope. Prompts, eval sets and tool code are edited from the detail
(#826, #828, below), and a built-in or shared version is forked into the project first (#828);
import is a later milestone.

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
(`AGENTBREEDER_REGISTRY_PATH`) is not edited in place; the card forks it into the project first
(**Fork to edit**, #828, below).

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
else `CODE_NOT_CONFIRMED`). Tools use that tier (#828, below).

Tested with the real `registry edit` (server tests over a starter project: a saved case, and a
case without a reference refused as `EVAL_INVALID` at line 3) and with Playwright over Northwind's
61 cases: one case edited is the only changed line of the saved 1.0.2; a new case is checked
before review; Discard. The M10 acceptance (a broken case blocks a release) is batched with the
milestone checks.

### Tool code editing and "Fork to edit" (#828, M12): on `main`, not in a published preview

A tool's detail (`#/registry/tools/NAME`) has the same **Edit X.Y.Z** card for a project version,
for its **Python source under `src/`** only. `tool.yaml` (what the model sees) and other files are
listed as not editable here; use `agentbreeder registry edit` for those.

- **Review** runs `registry edit ... --dry-run`, which includes the CLI's tool check (allowed files,
  no module that shadows the standard library, the runtime or its locked packages, and every
  function a top-level `def` that parses: `TOOL_INVALID` with the CLI's message). The code is read,
  never imported.
- **Save** needs the code confirm: "This code runs in agents", with the note that building and
  releasing an agent that uses the new version runs it as you, with the stack's model key and
  database, and that the eval gate is a quality gate, not a security review. Then "I reviewed this
  code and trust it to run in agents" (`code_confirmed`, else `CODE_NOT_CONFIRMED`). The saved
  version runs nowhere until an agent is moved to it and a release passes the eval gate.

**Fork to edit.** A version that is built in or on `AGENTBREEDER_REGISTRY_PATH` (any kind the
console edits) is not edited in place. **Fork to edit as X.Y.Z** runs `registry fork REF --version
X.Y.Z --project DIR --dry-run --json` (the next free patch version): the review lists the files it
copies. **Fork as X.Y.Z** copies exactly what was reviewed (`--expect-digest`; a source changed
since the review is `DIGEST_MISMATCH`). Since M9a (#825, below), a fork of code that runs in agents
(a tool, an eval set) also needs the "This code runs in agents" confirm, and a fork can take
another name. The copy is the project's and opens in the editor; the original is unchanged.

Tested with the real `registry edit` and `registry fork` (server tests over a copy of Northwind: a
reviewed tool edit saved only with the confirm, a renamed function refused as `TOOL_INVALID`, only
`src/**/*.py` edited, a shared tool forked after a review and then edited, a fork refused when the
source changed after its review) and with Playwright over Northwind (the same edit and fork, the
tool check's refusal, axe in light and dark). Not yet run as the M12 acceptance (edit
`tool/orders`, move Northwind to it, release with the eval gate, 61/61): that is batched with the
milestone checks.

### Import a version by path (#825, M9a, first part): on `main`, not in a published preview

**Registry ▸ Import** (`#/registry/import`, also linked under every registry list) copies a
registry version directory from anywhere on this machine (another project, a shared folder, a
download) into the project's `./registry` as an immutable version. The fields are the directory's
absolute path and, optionally, the name and version to import it as (`--as`, `--version`).

- **Review import** runs `registry import --project DIR [--as NAME] [--version X.Y.Z] --dry-run
  --json -- PATH`. The review lists the staged copy's files (text capped and cleaned by the CLI, as
  `registry show`), where it lands and its hash. It writes nothing. A changed field needs a new
  review.
- **Import as REF** sends the reviewed digest. The server runs a fresh dry run, then the write
  with `--expect-digest`, so what is written is what was reviewed. A source changed since the
  review is `DIGEST_MISMATCH`, and a version that exists already is `VERSION_EXISTS`.
- **Code confirm.** Tool, service, eval, MCP server and runtime versions run in agents, so their
  import needs the "This code runs in agents" checkbox (the same three-tier confirm as the tool
  editor). The server refuses the write without it (`CODE_NOT_CONFIRMED`). Prompt and knowledge
  versions get a note that they steer the agent. An imported version runs nowhere until an
  `agent.yaml` references it and a release passes.
- **The path.** It must be absolute, with no control, bidi or invisible characters, and it reaches
  the CLI as one word after `--`. The console server never reads it. The CLI child copies it once
  into a private staging directory and refuses anything that is not a version directory before it
  lists a file: no valid `manifest.yaml` is `MANIFEST_INVALID`, and a link, hard link, special
  file, a directory over the size caps, or one that contains the project's registry is
  `ENTRY_UNSAFE`. A refusal can name the first offending entry's path inside the directory, never
  a file's contents. Review shows the text of every file in an accepted version directory,
  dotfiles included, so point it only at a version directory. The server checks the CLI's answer against the contract (fields, no raw
  control or bidi characters, the text cap) and shows anything else as `BAD_OUTPUT`.

Tested with the real `registry import` (server tests: a prompt imported from another directory
then resolved, an import under another name, a tool import refused without the confirm, a
source changed after the review, a digest not from a review refused before the write lock, a
directory without a manifest, a file, a symlinked or hard-linked file, a symlinked directory and
`/` refused without their contents in the answer, a manifest changed from prompt to tool after the
review, a control or bidi character in a file cleaned by the CLI, bad requests refused before any
CLI call; the answer check's refusal of raw control or bidi characters is unit-tested) and with
Playwright over Northwind (the main flows: prompt and tool imports, a refused directory, a source
changed after its review, the link from the registry lists, axe in light and dark). Not yet run as the M9a acceptance (an imported prompt used by an agent and released, a
built-in service forked, a tool import's confirm), which is batched with the milestone checks.
Forking from the registry browser is the second part (below).

### Fork in the registry browser (#825, M9a, second part): on `main`, not in a published preview

Every version outside the project registry (built in, or on `AGENTBREEDER_REGISTRY_PATH`) can be
forked into it, not only the kinds the console edits. Prompts, eval sets, tools and knowledge keep
**Fork to edit** in their Edit card. Service definitions, MCP servers and runtimes get a **Fork**
card that copies the version into the project; the console does not edit those files, so the copy
is changed with `agentbreeder registry edit` or by publishing a new version. Agent packages are not
forked here (`FORK_KIND`).

- **Fork under another name** (optional, `--as NAME`) forks the version under that name at the
  same version, and the console opens the new name's page. Without a name the copy is the next
  free patch version of the same name. One of the two must change (`FORK_UNCHANGED`).
- **The code confirm.** Forking a tool, service, eval set, MCP server or runtime brings code from
  outside the project into a version the project owns, as an import does, so it takes the same
  three-tier confirm: the review, the "This code runs in agents" checkbox, the button. The server
  runs its own fresh dry run before the write and refuses one without `code_confirmed`
  (`CODE_NOT_CONFIRMED`). A digest that does not match that dry run is `DIGEST_MISMATCH`, refused
  before the write lock. This replaces #828's rule that a fork needs no confirm.
- The CLI's answer is checked as an import's (the same object); one off the contract is
  `BAD_OUTPUT`.

Tested with the real `registry fork` over a copy of Northwind (server tests: the built-in
`service/postgres-pgvector@1.2.0` forked to 1.2.1 only with the confirm, then resolved from the
project registry; a prompt forked under another name without a confirm; a tool fork refused
without the confirm; a digest not from a review refused before the write lock; an answer off the
contract; bad requests refused before any CLI call) and with Playwright (the service fork with
the confirm, a runtime forked under another name opening its new page, the tool's Fork to edit
with the confirm, axe in light and dark). Not yet run as the M9a acceptance, which is batched with
the milestone checks.

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

The source is the Files list; the code is edited from the Edit card ("Tool code editing and Fork to
edit", above).
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

### Knowledge index status (#830, M14, first part): on `main`, not in a published preview

Below a knowledge version's facts, **Index status** compares the version's documents with the
table the runtime indexed them into: the state (**Fresh**, **Stale** or **Not indexed**, with
what changes it), the table, chunks indexed of expected (fresh, stale, missing, extra), documents
indexed of expected, the oldest and newest index time, and the embedding alias and dimensions
(and the table's, when they differ). The console runs `agentbreeder knowledge status REF
--project DIR --environment ENV --json` ([`docs/commands/knowledge.md`](commands/knowledge.md),
[`docs/knowledge.md`](knowledge.md)) as an uncached child, at most four at once; **Refresh**
reads it again.

- **It reads with the agent's own credential**, in a read-only session; the console server never
  reads a credential. **Chunk text is never read**: only counts, names, times and the
  embedding go to the browser, checked again by the console before it sees them.
- **Documents change through the version's Edit card** (below; or `registry edit` in a terminal)
  and are re-indexed after the next `release`, whose eval makes the runtime index the new version.
- **A refusal shows as "Unavailable: reason" with the next step**: for example, services not
  running (`SERVICES_DOWN`) or a refused credential (`DB_LOGIN_FAILED`) name `services up`; other
  codes show the CLI's message and the `knowledge status` command to run in a terminal.

Tested: server tests over a scripted `knowledge status` (fixed argv, never cached, bad refs
refused before the CLI, unknown keys dropped, hostile values nulled, a version of another name
refused, a version listed twice refused, at most four reads at once), the CLI's own `--json`
answers over a faked database session (fresh, stale, not indexed) and its refusals passed through
the same checks, and Playwright over a copy of Northwind with a
scripted `knowledge status` (fresh, stale with a note, a refusal, Refresh, no status on other
kinds, axe in light and dark). Not yet run against a real stack's database: that is the M14
acceptance, batched with the milestone checks.

### Knowledge documents (#830, M14, second part): on `main`, not in a published preview

A project knowledge version's **Edit** card changes its documents, `docs/NAME.md` or
`docs/NAME.txt` (the files the runtime indexes), the same way prompts are edited: every change
publishes a new version, and the old one never changes.

- **Edit** a document, **add** one (**New document**: a plain name, letters, digits, `.`, `_` and
  `-`, ending in `.md` or `.txt`, and not the name of another document in any case or extension,
  since the runtime's chunk ids are `<name>#<n>`; it is saved with `registry edit REF --set
  docs/NAME -`), or
  **remove** one (**Remove this document**: `registry edit REF --remove docs/NAME --version X.Y.Z
  --project DIR`, with nothing on stdin). Each is a dry run that shows the diff and the digest,
  then a write bound to that digest (`--expect-digest`; `DIGEST_MISMATCH` means review again).
- **The review warns before Save** that documents land in the project's registry, which is
  committed to git: no customer data or secrets.
- **Only documents are changed here**: the manifest and other files are listed as not editable,
  and a name outside the plain pattern is edited in a terminal. Only knowledge takes the remove
  mode (`EDIT_REMOVE` for other kinds). Text is up to 512 KiB; there is no binary upload.
- A saved version is indexed only after the agent is moved to it (**Move agent to this
  version**, as for prompts) and a release runs: the release's eval makes the runtime index it.
  A built-in or shared knowledge version is forked into the project first (**Fork to edit**).

Tested with the real `registry edit` (server tests over a copy of Northwind: an edited, an added
and a removed document, a removal refused with another digest or a missing file, paths and bad
removals refused before the CLI, nothing on stdin for a removal) and Playwright over a copy of
Northwind (edit, add with refused names, remove refused while there are unsaved changes, the
warning in each review, axe in light and dark). Not yet run: a release after the change and the
retrieval and status checks of the M14 acceptance, batched with the milestone checks.

### MCP grants (#831, M15, first part): on `main`, not in a published preview

**MCP grants** (`#/mcp`, next to Services) shows the project's MCP servers and who may use their
tools, and changes a grant (see the next section).

- **Per server** (`stack.yaml` `mcp:`): its version, and from `stack.lock` its origin
  (built-in, project or registry-path) and upstream origin; its relay `mcp-NAME` with its
  container state and a Logs link; and the tools `stack.lock` pinned, each with the first 16 hex
  digits of its hash, as the CLI's grant text shows them. Before the first `services up`, or after
  the version changed, there are no pins for it yet, and the screen says the next `services up`
  asks to confirm the server and pins its tools. Only a changed version is seen here: a changed
  origin or upstream (which also drops the pins) shows at `services up`, in its review.
- **Upstream drift**, from `services status --json` (`mcp`): a pinned tool that changed upstream
  is shown as hidden until repinned, one missing upstream as refused by the relay, and tools
  served upstream but never pinned are listed. A repin is reviewed in **Services** (Review and
  update, then Review repin; see "Repin" below), or in a terminal with `agentbreeder services
  pins DIR` and `services up DIR --environment ENV --repin --expect-pins DIGEST`, which the screen
  names with the project's path. A relay that is not running, or could not be checked, says so
  and claims nothing about its pins. A failed check names its code and cause
  (`MCP_PROBE_FAILED (cause UPSTREAM_AUTH)`), says in fixed words what a known cause means (the
  upstream refused the credential, timed out, answered with an error or an invalid answer, or
  speaks an unsupported protocol) and gives the `services logs DIR mcp-NAME` command (#1042).
- **Grants per agent** (`environment.agents.AGENT.mcp`): each agent's tools per server, a tool
  not pinned yet marked. A grant takes effect at the next `services up`, and an agent uses a tool
  only when its `agent.yaml` asks for it; the screen says both.

`stack.yaml` and `stack.lock` are read in the console process, through `read_project_file` (a
regular file, not a link, size-capped) and the same model and lock checks as `services`; a bad
file is an error (`STACK_INVALID`, `LOCK_INVALID`, `ENTRY_UNSAFE`), never a partial answer.
Nothing is probed upstream by this screen: `services status` asks the running relay.

Tested: server tests over copies of `tests/fixtures/mcp_github_project` (servers, pins and grants;
no lock yet; pins of another version not shown; bad and linked lock files; the lock checks equal
`StackLock.load`'s), JS tests of the merge with relay status, and Playwright over the fixture with
a scripted `services status` (pins and grants, drift naming the terminal repin, no pins before the
first `services up`, axe in light and dark). Not run against live relays: that is the M15
acceptance (Docker and a `GITHUB_MCP_TOKEN`), batched with the milestone checks.

### Grant and revoke MCP tools (#831, M15, second part): on `main`, not in a published preview

**Change a grant**, on the MCP grants screen, edits one agent's grant in `stack.yaml`
(`environment.agents.AGENT.mcp.SERVER`) through `agentbreeder mcp grant|revoke AGENT SERVER
TOOL... --json` ([`docs/commands/mcp.md`](commands/mcp.md)). The console never writes the file
itself.

- **Choose** an agent, a server, Grant or Revoke, and tools. To grant, it offers the tools the
  server's version declares and the agent doesn't have yet. The console reads them from that
  version's `mcp.yaml` (`registry show --json`, parsed as text and never run) and shows each
  one's `effect`. To revoke, it offers the agent's granted tools, including one a newer version
  no longer declares. A tool is marked "no longer declared" only after the declared tools have
  been read; until then it says "checking the declared tools", and "declared tools unavailable"
  if they can't be read (#1043). The CLI makes the real check (`MCP_TOOL_UNDECLARED`).
- **Review, then write exactly that**, as with "Move agent to this version". A dry run shows the
  diff and the tools the agent has after it. The write is bound to the digest of the
  `stack.yaml` it reviewed (`--expect-base`). If the file changed in between, the write is
  refused (`BASE_MISMATCH`) and you review again. The write holds the project, so it never lands
  while a console job (`services up`, build or release) of that project reads the stack; a
  command run in a terminal is not held. A write that times out or whose answer fails the
  console's check may have changed the file: the screen reads the grants again and says so.
  Asking for a grant that is already held, or a revoke of a tool not granted, writes nothing.
- **When it takes effect.** The review says that a grant reaches the relay only at the next
  `services up`, and that an agent uses a tool only when its `agent.yaml` asks for it. After a
  write the screen names the `services up` command, which Services runs from its review (next
  section).
- **A revoke an active deployment still holds** is written and reported, with the CLI's next
  steps. `services up` refuses to narrow the grant (`MCP_GRANT_IN_USE`) until you run
  `agentbreeder remove ID --local --stack DIR`. An unreadable grant file names `reconcile`.
- **Refusals a person can act on have fixed words**: an unknown agent (add it with its sponsor
  and review date by hand) or server, an undeclared or invalid tool, a rewrite that would change
  more of `stack.yaml` than the grant (`STACK_EDIT_AMBIGUOUS`: edit by hand), and a file that
  would go over 64 KiB. Other refusals show the CLI's message.

The console checks the request before any CLI child starts: the verb, the names against the
CLI's patterns, and 1 to 100 tools, passed after `--`. It checks the CLI's answer again and keeps
only the known fields. No audit row is written; the record is the file and git.

Tested: server tests over the real `mcp grant|revoke` on copies of
`tests/fixtures/mcp_github_project` (review then write; `BASE_MISMATCH`; the last tool dropping
the server; nothing to change; the CLI's refusals; bad requests never reaching the CLI; the
write held by a running job; the declared tools from `mcp.yaml`). Also tested: answer-shape and
pattern checks, JS tests of the choices, fixed words and next steps, and Playwright with the real
command (grant reviewed then written and listed; a stack edited after the review reviewed again),
a scripted in-use revoke, and axe in light and dark. Not run: the grant reaching a live relay
(`services up` with Docker and a `GITHUB_MCP_TOKEN`), batched with the M15 acceptance.

The relay's Logs link is hidden when its name is over the console log route's 31 characters (a
server name of 28 or more); read those logs with `agentbreeder services logs DIR mcp-NAME`.

### Starting services for a stack with MCP servers (#831, M15, third part): on `main`, not in a published preview

Services' **Review and update** used to refuse a stack with `mcp:` servers. The review now
lists them under **MCP servers**, from `services plan --json`. For each server it shows the
version, its origin (built-in, project or registry-path), its upstream, and which credential
goes where (`Credential GITHUB_MCP_TOKEN will be sent to https://api.githubcopilot.com:443`).
The credential is a key like any other in the form. **Confirm and start** runs the same
`services up DIR --environment ENV --yes --expect-grant DIGEST` job as before.

- **Consent for code that is not built in.** A project or registry-path server needs its own
  tick. The tick names the credential and its destination, or says the server gets none; it
  says the server's tools are pinned on first use and that its `effect` labels are untrusted.
  Each ticked server is passed as `--allow-project-mcp NAME`. Start stays off until every such
  server is ticked. The server checks the ticks against a fresh plan, after the same digest
  check as the grant: exactly the servers that are not built in (a repeated name counts once).
  Otherwise it refuses before any container starts (`MCP_PROJECT_CONSENT`,
  `MCP_CONSENT_INVALID`). The digest binds each server's version ref, origin, upstream, auth,
  credentials and grants. It does not hash the version's files, so a project version edited in
  place under the same ref is not caught by it; this is the same for `--allow-project-mcp` in a
  terminal. The
  console asks for every server that is not built in, not only those with a credential. `services
  up` pins a server's first tools without a terminal only for built-in or allowed servers, and it
  pins after the relays have started. A tick never carries over to a new review.
- **OAuth refresh.** For a server with `oauth_refresh` auth, the review and the tick also say
  that the access token it mints goes to the upstream; the refresh credential goes to the token
  host.
- **Pins.** After the relays start, `--yes` accepts first pins and new tools for built-in and
  allowed servers, as it does in a terminal (trust on first use). The job log shows every tool
  definition pinned. A **changed** definition is accepted only through the reviewed repin below;
  without it the relay keeps that tool hidden.
- **What the CLI still refuses** is shown as the job's failure: a narrowed grant still held by a
  deployment (`MCP_GRANT_IN_USE`), and an undeclared tool in a grant (`MCP_TOOL_UNDECLARED`).
- `agentbreeder up --yes` with a project directory now starts a stack whose MCP servers are all
  built in, as `services up --yes` does. A stack with any other server is not started, with the
  consent message, because `--yes` alone never allows one.

Tested:
- Server tests: a built-in-only stack starts with no consent flags; a project server is refused
  without its tick, with a wrong, extra or malformed one, and with a tick from an older plan;
  a tick is passed once; the flag is only a services-up option; the argv check is repeated.
- A parity test on a real plan, with `mcp/github` copied into the project registry: the
  console's consent list and "sends a credential" equal `mcp_stack.needs_project_consent`.
- JS tests, and Playwright with a scripted plan: a built-in stack starts; a project server is
  started only after its tick, which names the credential and destination and is passed to
  `services up`; drift names the terminal repin; axe in light and dark.

Not run: a real `services up` of an MCP stack from the console (Docker and a
`GITHUB_MCP_TOKEN`), batched with the M15 acceptance.

### Repin (#964, console half)

When `services status` reports a server in `drift`, the services review offers **Review repin**
(spec `docs/superpowers/specs/2026-11-14-mcp-repin-expect-pins.md` §7). It sits in the services
review, not on the MCP screen, because a repin is a `services up`: it needs the grant digest, the
consent ticks and the keys of that review. The MCP screen links there.

- **Read.** `POST /api/projects/ID/mcp/pins` (the session, Origin and Host checks of every POST;
  a web page can't make the relays contact their upstreams). The server runs `services pins DIR
  --json`, uncached and tracked (a timeout or shutdown stops the whole process group, including
  the `docker compose exec` that holds the CLI's write lock), with a timeout of 60 s plus 180 s
  per relay and stdout capped at 2 MiB (`PINS_REVIEW_TOO_LARGE`, naming the terminal command).
  The JSON parser refuses duplicate keys, NaN and Infinity. While it runs, Start is disabled.
- **Check.** Before anything is kept or shown, every definition must hash (`tool_hash`) to its
  pin, the display part must match the hashed `review` document (new and changed equal `accept`,
  `drop` equal, origin equal, the old pin equal, a previous definition hashing to it), and the
  digest must be that document's. Any failure is `PINS_REVIEW_INVALID`, never a 500. Only then
  is the digest kept, per project, in memory; a new read (or a failed one) replaces it.
- **Show.** The server renders each definition with the CLI's own `review_lines` and
  `review_diff` (ASCII-escaped sorted JSON, long strings wrapped with `↩`, a unified diff, "diff
  too large" or "previous definition unavailable"); a test keeps the copies equal to the CLI's.
  The browser sets the lines as text and never parses a definition, so `1.0` stays `1.0`. A
  dropped pin says "this pin is removed"; a server that isn't built in carries the untrusted
  `effect` warning. The tick "I reviewed these tool definitions" turns Start into "Confirm, repin
  and start"; a new read clears it.
- **Start.** `services-up` takes a `pins_digest` option. Under the console's lock and in one
  step the stored review is popped and compared (`PINS_REVIEW_STALE` when there is none or it
  differs), so two starts can't both use it and a page can't post a digest the server never
  showed. A refusal before the job launches (the grant changed, a busy project) puts it back,
  unless a newer review is there. The argv gets `--repin --expect-pins DIGEST` after
  `--expect-grant`, never `--repin` alone.
- **Outcome.** If the definitions changed after the review, `services up` refuses only the pins
  (`[PINS_CHANGED]`) after the rest of Start ran; the job's result says so and asks for a new
  review. On success the job log shows what was repinned and under which digest.

Tested: server tests (the copies equal the CLI's on hard inputs; 22 ways an answer can disagree
with its own review; the capped read answers, stops past its cap (exactly at it), stops a
grandchild on timeout, stays bounded when a process left the group, drains a stderr flood, keeps
the CLI's `[CODE]`, and a byte that isn't UTF-8 is an invalid review; the read's arguments; a
failed read keeps nothing; the review is used
once, restored after a pre-launch refusal and never over a newer one; eight concurrent starts
take it once), JS tests, and Playwright with a scripted `services pins`: a ticked review starts
with the digest, an unticked one without a repin, a refused read and a failing answer show
nothing to tick, Start waits for the read, axe in light and dark. Not run against live relays
(Docker and a `GITHUB_MCP_TOKEN`), batched with the M15 acceptance.

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
  and the trace id (with Copy). A case whose trace id is 32 lowercase hex characters also has the
  closed **Trace spans** disclosure of Try it (#907): opening it reads `traces --trace ID` through
  the same route. `traces --trace` searches the Phoenix projects of every deployment in the state,
  removed ones included, and a candidate shares its agent's project, so a case's trace is meant to
  be found after `release` removes the candidate; on Northwind against a live Phoenix (2026-11-15,
  `907-tracing.md`) a case's trace was read this way after the candidate was removed. A
  missing id, or one outside that rule, shows "none" and no disclosure.
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

### The gate-failed view (#826, third part): on `main`, not in a published preview

When an agent's latest release (the newest release run in the project's audit log, rollbacks
aside) ended `eval_failed`, the agent's **Overview** and **Build and release** start with two
cards. A later release that passes hides them.

- **Eval gate failed**: the saved score and "Production is unchanged.", links to **Edit the
  prompt** (the registry page of `agent.yaml`'s `prompt.ref`; a prompt given as a path says to
  edit it in the agent's folder) and to the **Full eval report** (the viewer above), and a
  **Failed checks** table with one row per failed check of each failed case: the case id, the
  check, what was expected, what happened and the judge's reason. Expected and actual come from
  the check's name as `release` records it (`matches:PATTERN`, `avoids:PATTERN`, `called:TOOL`,
  `did_not_call:TOOL`, `agent_responded`, `retrieved_expected_article`, `patterns_finished`), the
  tools the case called, the judge's correctness (a pass needs 0.70) and, for `judge_correct`,
  the case's reference answer from the eval set the run used (`registry show` of the report's
  `eval_set`; when that cannot be read, the table says so and shows the rule instead). "Show the
  agent's replies" reads the report again with `--content` and adds a closed Reply disclosure to
  each case's first row.
- **Edit case** (once per case) opens `#/registry/evals/NAME/case/ID`: the eval set's page with
  that case's row open in the editor, scrolled to and focused. A case id that is not one route
  segment (letters, digits, `.`, `_`, `-`) links to the eval set's page. The page shows the
  eval set's latest version; when that version has no case with the id (renamed or removed), it
  says so and opens nothing. The case opens once: after a save or another version it stays
  closed. Saving makes a new version as usual; move the agent to it, then release again.
- **Candidate log (saved tail)**: the lines `release` kept from the candidate before removing it
  (at most 500, redacted when saved; see `docs/agent-yaml.md`), shown as text with controls made
  visible, or why there is none (or that it is empty). The server reads
  `GET /api/projects/ID/logs/failed/NAME` with `logs --failed NAME --stack DIR --environment ENV
  --json` (fixed argv, never cached; the name must be an agent name, else `AGENT_INVALID`). Exit
  1 (no tail) answers `{saved: false}`, unless stderr holds a Python traceback (a crash also
  exits 1). An answer for another agent, or lines that are not strings, is refused with
  `LOGS_INVALID`. The file's path is not passed on, since it names the state directory.

`static/lib/gate.js` holds the run choice, the rows and the links as pure functions. Tested with
node (each check kind, rows and links), pytest (the argv against the real `logs` parser, exit 1,
no caching, off-contract answers, names that never reach the CLI) and Playwright over Northwind
with the fake CLI: the rows with the real eval set's reference answer, the tail, no tail,
replies on request, Edit case opening the case, a later pass hiding the view, Build and release,
and axe in light and dark. Not yet run after a real failed release; that is part of the M10
acceptance, batched with the milestone checks.

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

Follow, with Pause and "Jump to latest", is the second part, and container events from `watch`
the third, below. **Not yet**: the M5 console verification (both deployments, local and kind, and
the leak scan of API output).

Tested with Playwright against a fake CLI (10,000 lines windowed, the level and text filters,
search, wrap, copy, the coded empty states, a deployment's Previous container) with axe in light
and dark, and on the server with a fake CLI (argv, refused options, the answer's shape, no cache).
Not yet run against real services.

### Follow a log (#821, second part): on `main`, not in a published preview

**Follow** (beside Read again) streams the log as it is written. The server starts one CLI child
for that tab, the snapshot read's command with `--follow`:

- a service: `agentbreeder services logs PROJECT NAME --tail N [--since D] --json --follow`;
- a deployment: `agentbreeder logs ID --stack PROJECT --environment ENV --tail N [--since D]
  --json --follow` (never `--previous`: Follow is off, with its reason, for a previous container).

`POST /api/projects/ID/logs/service/NAME/follow` (or `.../deployment/ID/follow`) with `{tail,
since}` starts it and answers `{id}`; the page then reads `GET /api/follows/ID?after=SEQ` every
second (a poll: it does not keep the session alive) for `{running, lines: [{seq, line, level}],
next, more, missed, dropped, cli_dropped, reason, stopped}`, and **Stop following** sends `POST
/api/follows/ID/stop`. A follow belongs to the session that started it; another session gets 404
for its ID.

- Each record is checked against the CLI's contract (the source and name followed, `line` a
  string, `level` one of the known levels); only `line` and `level` pass, and a record that does
  not match is dropped and counted. The CLI has already redacted every line, failing closed, as
  for a read.
- Lines are held in memory only: up to 5,000 lines and 4 MiB per follow on the server, until the
  page reads them, and the newest 10,000 in the page. A page that reads too slowly is told how
  many lines it missed; the notes say how many the page dropped to stay fast.
- **Pause** holds new lines back (the view stays still for reading, searching and copying);
  scrolling up from the newest line pauses too. **Jump to latest (N new)** shows them and goes
  back to the newest line. While following, Read again is off ("Stop following to read a
  snapshot").
- The status line says how a follow ended: the container stopped or was removed, Stop following,
  a coded error from the CLI (`DOCKER_LOGS_FAILED`, `KUBECTL_LOGS_FAILED`, `POD_NOT_RUNNING`), or
  15 seconds without a read (a background tab), with **Follow again**.

Limits (S-10): at most 8 follows per session (`FOLLOWS_FULL`, "stop one and retry") and 16
long-lived children in the console, usage children included. A follow is stopped when its page
closes or moves away, 15 seconds after its last read, on Stop following and when the console
stops, each through the same plumbing as the usage feed: stdin a pipe the console holds, its own
process group, its PID in `children.json`. An ended follow's last lines stay readable for a
minute.

Tested on the server with a fake CLI (argv for both kinds, refused options, the contract check,
session ownership, both caps, the idle stop, a failed child's coded error, shutdown, the ring's
line and byte bounds) and with Playwright against a fake CLI (live lines, Stop, Pause and Jump to
latest, scroll-up pause, a stopped container, a failed follow, leaving the page, a deployment,
the previous-container refusal, axe in light and dark). Not yet run against real services: that
is the M5 acceptance, batched with the milestone verifications.

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
scripted gateway responses, and shown live on Northwind (2026-11-15, `906-cost.md`: "$0.003538
(LiteLLM's estimate, reported by the runtime)", within rounding of LiteLLM's spend log).

**Trace spans** (#907, on `main`, not in a published preview): a turn with a trace ID has a
second disclosure beside Turn details. The first time it is opened, the server runs
`traces --trace ID --stack DIR --environment ENV --json` (a fixed argv, never cached; see
[`traces.md`](traces.md)) through `GET /api/projects/ID/traces/TRACE_ID`. It shows the trace in
one line (status, latency, span count, models, total tokens, tool calls), how the trace was
attributed to a deployment (`exact` or by time window), and the span tree as nested lists: each
span's name, kind, status, latency, model, tool and tokens, and its error text. The total counts
each model call once; a span whose counts are also on a span under it says "counted in a span
under it" (#1009). A trace read before its root span reached Phoenix says it is still arriving,
with its spans so far and **Read again**, instead of a time-window attribution (#1010). Both
are tested with a fake CLI only. The server passes on
only those fields, and the Phoenix address, session and turn IDs stay on the server. `traces` never
reads prompts, replies or tool arguments and results. Error text is redacted and cut, but it is the
runtime's own text and can quote what the agent was given. At most 4 trace reads run at once
(`TRACE_BUSY` otherwise). Phoenix can receive a trace a few seconds after the reply, so
`TRACES_NOT_FOUND` says so and offers **Read again**; a stack without `service/phoenix` says it has
no traces. Tested with a fake CLI, and run against a live Phoenix on Northwind (2026-11-15,
`907-tracing.md`): the panel listed the turn's tool calls, but its first read, 2 s after the reply,
was partial (6 of 11 spans, no root, and wrongly "Matched by time"; #1010). A later read had the
full tree. The tree's token total counts each model call twice (#1009).

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
`agent.yaml` (or its directory), the agent's `name:` and a model provider (gemini, openai or
openrouter, which have been run end to end with their default models, or anthropic, which is
config-only), with an optional model id that replaces the default; with another model id for a
verified provider, the preview says only the default has been run end to end (openrouter and
openai since #1020, on `main`, not in a published preview). It is a three-tier
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

### Build an agent (#833, M17): on `main`, not in a published preview

Home's **Add a project** card links to **Build an agent** (`#/build`), a form beside a sticky
preview. The form has the agent's name, the model provider (gemini, openai and openrouter, the
latter two since #1020; anthropic is marked "not run end to end") with an optional model id (a
verified provider with another model id is created, with a note that only its default model has
been run end to end), the prompt, the eval
cases (the eval-set case editor's rows, without the cited-sources check, at most 100 cases), and,
under **Model calls and the eval gate**, model calls per turn (1 to 20), the pass threshold (0 to
1) and retries (0 to 3). It starts with one example case to edit. Knowledge is not on the form
yet. The browser never builds YAML: it sends the form as `new agent --from-spec`'s JSON spec
(`docs/commands/new.md`).

**Tools** and **MCP servers** are pickers over what a new project resolves: the
`AGENTBREEDER_REGISTRY_PATH` roots and the built-ins (`GET /api/builder/parts`, read in the server
process; CLI children inherit the same variable). Each tool name has a select, **Not used** or one
of its versions (newest first, with where it comes from); no `tool/` versions ship with
AgentBreeder, so tools appear only once one is published into a registry on that path. Each MCP
server name has a select and, once a version is picked, a checkbox for every tool its `mcp.yaml`
declares, with its read or write effect; at least one must be ticked, and a version change keeps
only the ticks the new version still declares. A server named like a stack service (`postgres`,
`litellm`, or a reserved name) is shown but cannot be picked, and so is one whose `mcp.yaml` is
not a regular file or whose `tools:` cannot be read (no tools, more than 100, a name or effect the
CLI refuses). The versions of a name whose directory is a link in any root, and a version with an
unreadable manifest, are left out and counted. This list is an offer, not a check: only `tools:` is read here, and the CLI validates the
whole `mcp.yaml` and resolves every pick again, refusing what it cannot (`MCP_INVALID`,
`REF_NOT_FOUND`, `REF_CONFLICT`, `MCP_TOOL_UNDECLARED`).

It is a three-tier confirm:

1. **Preview** writes the spec to a file only you can read (0600, in a fresh 0700 temporary
   directory, removed afterwards; the prompt never goes in argv), runs `agentbreeder new agent
   NAME --from-spec=FILE --dir=~/agentbreeder-projects/NAME --dry-run --json` and shows every file
   it would write, each with its text (`agent.yaml`, `stack.yaml`, the gateway config, the prompt
   and eval-set versions, one file at a time), the gateway aliases and model id, the keys the stack
   needs, `review_by`, and whether the combination has been run end to end. Nothing is written. A
   form edit marks the preview out of date (an answer still on its way for the old form is
   dropped); Create waits for a new one and sends the spec that was previewed.
2. When a picked part brings code from outside the built-in registry (`runs_code`), a **This code
   runs in agents** box names it and its checkbox must be ticked: a tool or MCP server from
   `AGENTBREEDER_REGISTRY_PATH`. The built-in MCP servers ship with AgentBreeder and do not ask.
   A form edit, a different pick included, clears the tick.
3. **Create agent and add project** is on only for a combination run end to end
   (`combination.verified`: today gemini with a prompt and evals, alone or with one path tool, on
   `main`, not in a published preview, #833, and openrouter with a prompt and evals alone,
   #1020; no combination with two or more tools or with MCP servers has been run end to end
   yet). Otherwise the preview names what has not been run
   (the provider, "a new agent with tools", "… with MCP servers") and says `agentbreeder new agent
   --from-spec` writes it anyway. Create sends the previewed `plan_sha256`; the server runs the dry
   run again with `--expect-digest`, refuses `COMBINATION_UNVERIFIED` or `CODE_CONFIRM_REQUIRED`
   on that fresh answer, then runs the write with `--expect-digest` (a spec, picked version or date
   that changed is `DIGEST_MISMATCH`: preview again) and registers the new folder. The project then
   opens on Home with its guided path.

Refusals show `new agent`'s own coded message (for example `SPEC_INVALID` for a case id it
refuses, or a destination that is not empty). The server checks the spec's shape first (known
fields, `schema_version` 1, the name, provider and model id, no knowledge: `SPEC_UNSUPPORTED`;
pinned `tool/` refs, one version per tool, at most 50; `mcp/` servers as `{ref, tools}`, one
version per server, 1 to 100 distinct tool names each, at most 10; at most 256 KiB) and runs
nothing when they fail. The preview and create
routes (`POST /api/projects/builder`, `/api/projects/builder/create`) take bodies up to 1 MiB;
the CLI checks the spec in full.

Tested on the server against the real `new agent --from-spec` (the argv, the spec file's modes
and removal, preview, create, a changed plan, an unverified provider, bad requests; the code
confirm's refusal with a stubbed plan; the pickers' list over a path registry, a symlinked and a
broken version, servers whose `mcp.yaml` is a link or cannot be read, a picked path tool
with an MCP server previewed as code and refused as unverified, the same tool alone created
only after the code confirm, a granted MCP server, an undeclared tool) and with Playwright and axe in light and dark, where the fake CLI hands
`new agent --from-spec` to the real command (the code confirm box with a path tool, the MCP grant
checkboxes). A built agent with a tool has not yet been taken through `services up` and `release` from the
console: that is the M17 verification. The same gemini spec with one path tool, alone and with one
knowledge version, was run end to end through `new agent --from-spec` (#833), which put it in
`agentspec.VERIFIED`. Create turned on for the tool-only spec with no console change; the form
still refuses knowledge.

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

### Traces (#907, console half): on `main`, not in a published preview

An agent's detail has a **Traces** tab after Logs. It lists one deployment's recent traces from
the stack's Phoenix through `traces ID --stack DIR --environment ENV --last N --json` (a fixed
argv, never cached; see [`traces.md`](traces.md)) and
`GET /api/projects/ID/deployments/DEPLOYMENT_ID/traces?last=N`.

- **Deployment**: one of this agent's deployments in the project, production when there is one,
  and **Show** the newest 10, 20 (the default) or 50. The console allows at most 50, though the CLI
  allows 200, because every trace in the answer carries its spans. A deployment ID outside 32
  lowercase hex characters, or a count outside 1 to 50, never reaches the CLI (404 or
  `LAST_INVALID`).
- **Each trace**: its start (UTC), status, latency, models, prompt and output tokens, span count,
  tool calls (name, error, latency) and trace ID with Copy. Its span tree opens under it in the
  same **Trace spans** disclosure as Try it (`traces --trace ID`), read only when opened.
- **What reaches the browser**: `center/traces.py` passes on only that summary, each value checked
  for type and size. It passes on no spans, Phoenix address, project name, or session and turn IDs.
  A trace the answer attributes to another deployment is left out and counted ("1 trace was left
  out"). `traces` never reads prompts, replies, or tool arguments and results, and it redacts every
  string as for `logs`. Everything is shown as text.
- **Attribution**: when the runtime does not record its deployment (`window`), the tab says so,
  and names how many other deployments of the agent overlapped. The CLI's notes (no Phoenix project
  yet, never ran) and a partial answer are shown. A trace whose root span is not in Phoenix yet
  is marked "still arriving", is not counted as matched by time, and the tab says to read again
  (#1010). A failed read shows the CLI's code, in words for
  `TRACES_NO_PHOENIX` and `TRACES_UNREACHABLE`, and **Read again** reads it again.
- List reads share Try it's bound of 4 trace reads at once (`TRACE_BUSY`).

Tested with pytest (the argv, the `last` and ID rules, no caching, only the allow-listed fields,
another deployment's trace dropped, coded failures, the CLI's own `Report` shape passing through,
the shared bound). It is also tested with Playwright and the fake CLI: the list and its span tree,
switching the deployment and the count, a failed read, no traces with a note, window attribution,
untrusted strings shown as text, an eval case's trace, an eval case with no trace ID or an invalid
one, and axe in light and dark. Run against a live Phoenix on Northwind (2026-11-15,
`907-tracing.md`): a Try it turn's trace showed its tool calls (the full 11-span tree on a read 8 s
after the reply; reads in the first seconds can be partial, #1010), and an eval case's trace was
reached from Releases, Eval report, the case's Trace spans. Token totals count each model call
twice (#1009).

### LLM cost (#906, console half): on `main`, not in a published preview

Home's **LLM cost** card shows one registered project's model spend: the project in scope first,
with a **Project** picker when there are several. It shows the total with its currency, the calls
and failed calls, the window in UTC days, the time it was read ("as of …"), and a table per model
(the gateway alias and the provider model, calls, tokens in and out, spend). An agent's
**Overview** has its own **LLM cost** card. It shows the agent's deployment keys (production,
candidates and removed deployments, by name) with their target and state, the agent's total, and
their models added up. Both cards pick a window of the last 7, 30 or 90 days, and **Refresh**
reads again.

The console runs `agentbreeder cost --stack DIR --environment ENV --since DAY --until DAY --json`
([`docs/cost.md`](cost.md), [`docs/commands/cost.md`](commands/cost.md)) as an uncached child,
at most two at once (`COST_BUSY` beyond that). The server works out the days from the window (the
last N UTC days ending today); nothing from the browser reaches the argv but the window choice.

- **It is LiteLLM's spend, not a bill.** The amounts are USD as LiteLLM computed them from its
  model price list. Spend logs arrive about a minute after a call, so the newest calls can be
  missing. Calls with no price, a partial read (`truncated`) and lists cut for the page (200 keys,
  100 models) are said under the table.
- **The gateway's master key stays with the CLI.** The console server never reads it. The answer
  is checked again before the browser sees it: the window must be the one asked for, the currency
  USD and the source LiteLLM's spend logs. Every count and amount is type-checked; one bad value
  refuses the whole answer (`COST_INVALID`) rather than showing zero. Text from the gateway loses
  its control and invisible characters, and unknown fields are dropped.
- **A refusal shows as "Unavailable: reason" with the next step**: no gateway or master key
  (`COST_NO_GATEWAY`) and a gateway that does not answer (`GATEWAY_UNREACHABLE`) name
  `services up`, and an old state names `migrate`. Other codes show the CLI's message and the
  `cost` command to run in a terminal.
- Try it shows a turn's own cost (above). The console neither shows nor changes a key's budget.
  Deploy mints each key with a 30-day budget (the manifest's `budget_usd`, else the environment's
  ceiling) for LiteLLM to enforce, and `status` shows spend against it; a key reaching its budget
  has not been run.

Tested: server tests over a scripted `cost` (fixed argv, never cached, bad windows refused before
the CLI, unknown fields dropped, control characters removed, eval and unknown keys, long lists
cut and counted, bad counts, amounts, currencies, sources and windows refused, coded refusals, at
most two reads at once), `llmcost.report`'s own answer and the real CLI's refusal passed through
the same checks, and Playwright with a fake `cost` (Home's card, the window picker, Refresh, a
refusal, no calls, an agent's own and removed deployments, axe in light and dark). Run on
Northwind against a live gateway (2026-11-15, `906-cost.md`): Home's card and the agent's Overview
card showed the same figures as `cost` and a SQL sum of LiteLLM's spend log (USD 0.264939, 260
calls; the agent's own keys USD 0.202319).

### Docker-down blocker (#822, M6, first part): on `main`, not in a published preview

While Docker is not reachable, every signed-in screen shows a blocker at the top of the page:
"Docker is not reachable.", doctor's own message, the remedy (start Docker, then run
`agentbreeder doctor`) and a Retry button. The page asks `GET /api/docker` every 5 seconds while
the tab is visible (at once when it comes back into view, and on Retry); the server runs
`doctor --json --only docker` (the engine check alone, which gives up after 10 s, so a hung engine
also shows the blocker, up to about 18 s after it hangs), never answers from its read cache, and
lets one check answer every tab for 3 seconds; Retry never takes an answer older than the click.
A check that itself fails keeps the last answer and says so under Retry. When Docker is back the
blocker clears on the next check, without a reload, and Home's doctor card is read again.
Polls of `/api/docker` do not keep a session alive, and signing out drops the last answer. Home's
doctor card lists the other failed checks; Docker's own failure is only in the blocker.

Before this, the console read `doctor --json` as a failure when a required check failed (doctor
exits 1 then), so it kept showing the last passing answer, or nothing, and Home's Docker notice
never appeared. `/api/overview` now keeps doctor's exit-1 answer too.

The service controls, the usage child and the Home services table follow below. The M6 acceptance (stop Docker with Northwind running: the blocker within 15 s; start it:
the blocker clears without a reload) is batched with the milestone verifications; the UI tests run
it against the fake CLI.

### Service controls (#822, M6, second part): on `main`, not in a published preview

Each service name on **Services** links to its detail, `#/services/NAME` ("Service litellm"; the
sidebar keeps Services current). It shows the service's **Config** from `services status --json`
(state, health, host ports, with "internal only" explained), a **Logs** link, and **Start**,
**Stop** and **Restart**. Each button runs `agentbreeder services start|stop|restart DIR NAME
--json` on that one container: nothing is rebuilt, recreated or pulled. A button that does not
apply says why (already running, not running, or no container yet, which needs Services' review
and start first). MCP relays (`mcp-NAME`) have the same detail and controls
(their Logs link works for names up to 31 characters, the Logs route's limit). After an
action, focus goes back to the button used, or to the first one that applies to the new state. While
the Docker blocker shows, the three buttons are off and say so.

- The request is `POST /api/projects/ID/services/NAME/start|stop|restart` with an empty JSON
  body, a short write that also holds the project, so it never overlaps a console job on the
  same stack (`WRITE_BUSY`). The CLI's per-stack lock stays the authority: `SERVICES_BUSY` is
  shown as "Another operation is running on this stack; retry when it finishes".
- **Stop** and **Restart** while deployments use the stack come back `SERVICE_IN_USE`. The
  console then asks ("Stop litellm anyway?"), naming the deployments from the CLI's message, and
  only on confirm sends the request again with `{"yes": true}` (the CLI's `--yes`). Cancel sends
  nothing more. `start` takes no `yes`.
- Every other refusal is the CLI's coded message as written: `PORT_BUSY` names the port another
  process holds, and `SERVICE_NOT_CREATED`, `SERVICE_UNKNOWN` and `DOCKER_FAILED` are passed
  through. Docker's own text never reaches the browser; an answer in an unexpected shape is
  `BAD_OUTPUT`, and only the service, action, state, health and `in_use` are passed on.

Tested with Playwright against a fake CLI (each button, the in-use confirm and its cancel, focus
after both, `PORT_BUSY`, buttons held while an action runs or Docker is down, an internal-only and an unknown
service), with axe in light and dark, and on the server against a fake CLI (argv, `--yes` only after the refusal,
malformed bodies and names, coded refusals, unexpected answers, a running job and a second control
on the project). Not yet run against real Docker; that is the M6 acceptance (`services restart
litellm` restarts only that container).

### Service usage feed (#822, M6, third part): on `main`, not in a published preview

`GET /api/projects/ID/usage` returns the project's usage history: `{running, window, samples,
disk, dropped}`. The first request starts one `agentbreeder services usage DIR --follow --json`
child for that project (see [`commands/services.md`](commands/services.md)); later requests read
what it has printed. Samples (at most one every 2 seconds) are kept in memory for the last 5
minutes (`window`, 300 seconds), so a page opened later has history at once; `disk` is the latest
disk reading with its `as_of` time. Nothing is written to disk. Only the contract's fields pass,
each checked (service and volume names, deployment IDs, numbers that are not negative); a line
that does not match is dropped and counted in `dropped` (the CLI's own error object is not
counted), and a line over 64 KiB is discarded without being parsed or counted. History is capped at 160 samples per project.
CPU and memory are of the Docker VM on Docker Desktop.

The child stops about 15 seconds after the last request (requests to this route are polls: they do not
keep a session alive) and when the console stops, and the next request starts a new one; the
history stays. A child that fails (for example `DOCKER_STATS_FAILED` when Docker goes away)
shows its coded error, with the history kept and marked stale, and is not started again for 10
seconds. There is one child per project and at most 16 follows (usage now, log follows later)
in the console (`FOLLOWS_FULL` past that).

Long-lived children (S-10): each runs with its stdin a pipe the console holds, so when the console
exits, even when it is killed, the CLI sees the end of its stdin and stops with its `docker stats`
reader. Each leads its own process group; when the CLI exits, anything it left in that group (a
reader, after a crash) is killed. Its PID and start time are kept in a 0600 `children.json` in
the console directory while it runs. The next `agentbreeder up` kills the process group of any
recorded PID whose start time still matches (a child that ignored its stdin) and says how many it
stopped; a group whose leader is already gone is not swept, and a child whose start time
could not be read is protected by its stdin alone. M5's log follows will use the same
plumbing.

The Home services table and the service detail's Usage tab, which read this route, follow below.
The tests run the route, the idle stop, the failure path and a SIGKILLed console against a fake CLI.

### Home services and the Usage tab (#822, M6, fourth part): on `main`, not in a published preview

**Home** has a **Services** table across every registered project, one row per stack service:
its name (a link to its detail, which also puts that project in scope), the project, a status
chip, its host ports and four usage figures. The chip is an icon, a word and a colour, and the
word alone carries the meaning: Healthy, Running, Starting, Unhealthy, Restarting, Paused,
Stopped, Created, Dead or Not created, from `services status --json`. A service without host
ports says "internal only" and why (reached by agents on the stack network, not from this
machine); a stopped one shows its last ports as not listening.

The figures are **CPU** and **Memory**, both headed "of the Docker VM" (Docker Desktop runs the
containers in one VM: CPU is a share of one of its cores, so a busy container can pass 100%), and
**Network** and **Block IO** as bytes per second, averaged between two samples, with the totals
since the container started. Each figure is a text value next to a sparkline of the last five
minutes; the sparkline is an image whose accessible name gives its low, high and newest value, so
neither colour nor shape is the only cue. A stopped service says "Not running: no usage."

Each project's figures come from `GET /api/projects/ID/usage`, asked every 3 seconds only while one
of the project's services is up and the page is visible (a hidden tab stops asking; the server
stops the child about 15 seconds later). When the usage command fails, its coded message shows
above that project's rows with "It is tried again in a few seconds", and the last figures stay
marked as the last reading. Status is read as on Services (again when one of the project's jobs
ends). When the newest sample and the status disagree, because a container was stopped or
started outside the console while another service of the project runs, status is read once more
for that disagreement, at most 3 times per visit to Home and 30 seconds apart: status reads are
not polls, so a page left open does not keep the session alive. With nothing of a project up,
usage is not asked for, so a service started from a terminal shows when one of the project's
jobs ends or the page is opened again.

The service detail (`#/services/NAME`) now has two tabs: **Overview** (Config and the controls,
as before) and **Usage** (`#/services/NAME/usage`). Usage shows one tile per figure (CPU, memory,
network in and out, block read and written, processes), each with its text value, what it is
measured against and a sparkline, then the **Disk** reading: the container's writable layer, the
stack's volumes and when the CLI last read them (every 60 seconds; "the last reading failed" when
the latest one did). Switching tabs keeps focus on the tab link.

Tested with Playwright against a fake `services usage --follow --json` through the real console
and its usage ring (figures updating within 10 seconds, one child per project, none while nothing
runs, a failed command, stopped and starting services, the tabs), with axe in light and dark, and
with `node --test` for the wording and sparkline shapes. The M6 acceptance with Northwind on
real Docker (its 5 services with usage updating within 10 seconds, the blocker within 15 s,
`services restart litellm` restarting only that container) is batched with the milestone
verifications and recorded in `docs/superpowers/verification/M6.md` once it has run.

### Container events and failed jobs (#821, third part): on `main`, not in a published preview

`GET /api/projects/ID/watch` returns `{running, status, events, held, dropped}`. The first
request starts one `agentbreeder watch --stack PROJECT --json --record` child for that project
(see [`commands/watch.md`](commands/watch.md)); later requests read what it has printed. It uses
the usage feed's plumbing and limits: stopped about 15 seconds after the last request (a poll, it
does not keep a session alive) and when the console stops, a failed child's coded error kept for
10 seconds before a restart, one child per project, counted in the 16 long-lived children.

- `status` is the latest status record (every 60 seconds: each service's compose state and
  health, each deployment's state, a pod check for a ready kind deployment). `events` are the
  newest 50 `died`, `unhealthy` and `restarted` events, newest first, each with an `id` that only
  grows. Both are kept in memory only and outlive an idle stop.
- Only the contract's fields pass, each checked (service names, deployment IDs, agent names,
  state words, pod reasons, exit codes); a line that does not match is counted in `dropped`.
- A `restarted` that arrives while a Start services job of the same project runs is not shown
  (counted in `held`): the CLI cannot see the console's jobs. This is best effort: it is checked
  when the line is read, so a restart read just after the job ended still shows.
- `--record` makes the CLI store `died` and `unhealthy` for `agentbreeder events`; the console
  stores nothing of its own from the child.

On **Home**, under each project's rows in the Services table, **Container events** lists the
newest five, each with its time and words for what happened ("postgres exited by itself (exit
0)", "became unhealthy", "started again"; colour is never the only cue), or "None since this
console started watching", and when status was last checked. It is asked for every 5 seconds
while one of the project's services is up and the page is visible. A new event reads the
project's status again, so the chips follow. A stop, a release, a remove or Stop services is not
an event.

**Failed jobs are stored as events.** When a job fails or is cancelled (Cancel, or the console
stopping), the job runner stores a `job_failed` event in the console directory's
`events.sqlite3` (see [`events.md`](events.md)): the job kind, its exit code, whether it was
cancelled, and the error code of its last output line only when that line is the CLI's own
`[CODE] message` (a job's output merges stdout and stderr, and the CLI prints its error last). No other text of the output is stored there. A job waiting for approval (exit 3)
and a release stopped by its eval gate (exit 5, which `release` stores as `gate_failed`) are not
events. A store that cannot be opened loses the event, never the job's result. Showing the stored
events in the console (the bell and its panel) is M11's console half.

Tested on the server with a fake CLI (argv, the contract check against the CLI's own records,
newest-first ids and the cap, held restarts, the idle stop, a failed child's coded error with no
free stderr, the shared cap, the stored `job_failed` fields for coded, uncoded, cancelled,
approval and gate exits, with a planted value never stored), with Playwright against a fake CLI
(events on Home, a new event re-reading status, a failed watch, axe in light and dark), and with
`node --test` for the wording. Not yet run against real services: that is the M5 acceptance.

### Coded failures in job rows (#1042): on `main`, not in a published preview

When a job fails and the CLI's last line is its own `[CODE] message`, the console keeps that
line with the job (`failure_code`, `failure_message` on the job rows: redacted as the log is,
control characters removed, the message capped at 300 characters; `center.sqlite3` schema version
3, so an older console refuses that file afterwards with "upgrade AgentBreeder"). The job row then says the code, the cause an `MCP_PROBE_FAILED` message names in parentheses, and the CLI's
message, which carries its own next step: "Start services failed (exit 2): MCP_PROBE_FAILED (cause
UPSTREAM_AUTH). the github relay couldn't list upstream tools (UPSTREAM_AUTH); see `agentbreeder
services logs mcp-github`.", followed by fixed words for a known cause. A release refused before
any approval reads "The release failed: DEPLOYER_UNVERIFIED. A verified deployer identity is
required (agentbreeder login); production unchanged.". An eval gate failure (exit 5) keeps its own
words, and a failure with no coded line still says "See its log.". The Home checklist shows `next`'s
`SERVICES_UP_FAILED` (the last confirmed `services up` of the environment failed, for example
at the MCP probe) with its code, cause and note, not "services up has not run".

Tested: server tests (the coded line kept on the job row and in the activity read, none for an
uncoded failure or a success, redaction and the cap), JS tests of the wording, and UI tests over
the fake CLI (a Start services job failing at the probe, a coded release failure, the MCP grants
card's cause and log command, and the Home checklist). Not yet seen against a real failing relay
in the console.

## How it stays the same engine

Every action starts the CLI in a child process, with a fixed argument list. The UI does not open the
deployment database. The identity gate, policy, approval, audit and the eval gate run exactly as
they do for a CLI user.

- **The eval gate is orchestration.** Someone with deploy rights can still run a plain `deploy`.
  `promote` refuses any image without an audited release at the threshold in `agent.yaml`, but that
  does not stop a person deploying a manifest by hand.
- **Approvals are not in the UI.** A second person's verified identity is what a single-user session
  cannot provide. A job that needs one shows the command.
- **Stacks with MCP servers** start from the services review only with consent for every server
  that is not built in (`--allow-project-mcp`), and accept a changed tool definition only through
  a reviewed repin bound to its digest (`--repin --expect-pins`, #964).

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
