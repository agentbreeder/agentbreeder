# `agentbreeder next --json`: the guided-path contract

Status: **on `main`, not in a published preview** (#820). `agentbreeder next` prints this; the
local console builds its checklist, "Next" pill, empty-state buttons and job result rows from it.
The models live in `src/agentbreeder/guide.py` and the checks in `src/agentbreeder/guide_checks.py`;
`docs/next.schema.json` is generated from the models and a test keeps the two equal; another test
validates every JSON example on this page.

## The command

```
agentbreeder next [DIR] [--environment ENV] [--output DIR] [--json]
```

`DIR` is the project directory (default `.`): the one holding `agent.yaml` and `stack.yaml`.
`--environment` defaults to `dev`. `--output` is where `build --output` wrote the build and
deploy manifests (default `DIR/deploy`; it must be inside `DIR`). `next` only reads: it never builds, starts, deploys or writes
anything (local state is opened read-only, the stack's grant is never recomputed, no `services`
preview and no MCP probe run), and it runs at most one `docker compose ps`. It never prints a
secret value: key **names** appear, values never.

**Exit codes.** The text form exits 0 when every required step is done and 1 when steps remain.
`--json` exits **0 whenever the checklist was computed**, whatever `complete` says (the console's
runner discards stdout on a non-zero exit). Docker being unreachable, a missing or invalid
project, state that needs `migrate` and a pending approval are all reported as steps, with exit 0.
Exit 2 is left for a checklist that could not be computed: a usage error (argparse's message), or
one `[CODE] message` line at the end of stderr and nothing on stdout, for `DIR` that is not a
directory (`PROJECT_DIR_MISSING`), local state locked by a running job for longer than the read
timeout (`STATE_BUSY`), unreadable state (`STATE_UNREADABLE`), or a checklist that breaks this
contract (`GUIDE_OUTPUT_INVALID`; the message names the field, never its value). Also exit 2: an
unreadable project or state file (`PROJECT_UNREADABLE`), a local state database locked by a
running job past the read timeout during a query (`STATE_BUSY` as well), an `--output` outside
`DIR` once symlinks are resolved (`OUTPUT_OUTSIDE_PROJECT`), an invalid environment name
(`ENVIRONMENT_INVALID`), and anything unexpected (`NEXT_FAILED`, never a traceback). A project
path that cannot be printed under the character rule below (for example a directory name with a
zero-width joiner, a no-break space or an emoji variation selector) is checked first and is
`PROJECT_PATH_UNSUPPORTED`; rename the directory. An `--environment` that is not a valid name
(`[a-z0-9-]`, at most 32 characters) is `ENVIRONMENT_INVALID`; a valid one the stack was never brought up
for is the `services` step's `ENVIRONMENT_MISSING`. That line names no path beyond the
argument given and carries no child process's stderr. The console shows its last result as stale
on a non-zero exit.

## Output

The first example is a project whose services are not up yet:

```json
{
  "schema_version": 1,
  "project": "/home/me/agentbreeder-projects/hello",
  "environment": "dev",
  "complete": false,
  "next": "services",
  "steps": [
    {"id": "prerequisites", "title": "Prerequisites", "required": true, "state": "done",
     "reason": null, "fix": null,
     "data": {"checks": [
       {"name": "Docker", "passed": true, "required": true, "message": "Docker engine 27.5.1 is reachable"},
       {"name": "agentbreeder on PATH", "passed": false, "required": false,
        "message": "No agentbreeder on PATH; expected with npx or uvx"}]}},
    {"id": "project", "title": "Project", "required": true, "state": "done",
     "reason": null, "fix": null,
     "data": {"agent": "hello", "agent_file": "/home/me/agentbreeder-projects/hello/agent.yaml",
              "stack_file": "/home/me/agentbreeder-projects/hello/stack.yaml", "error_code": null,
              "newer": [{"ref": "prompt/hello-prompt@0.1.0", "latest": "0.1.1",
                         "file": "/home/me/agentbreeder-projects/hello/agent.yaml", "line": 9}]}},
    {"id": "keys", "title": "Model keys", "required": true, "state": "done",
     "reason": null, "fix": null,
     "data": {"required": ["GEMINI_API_KEY"], "present": ["GEMINI_API_KEY"], "stored": [],
              "missing": []}},
    {"id": "services", "title": "Services", "required": true, "state": "next",
     "reason": {"code": "SERVICES_DOWN", "message": "The stack's services are not running"},
     "fix": {"action": "services-up",
             "command": "agentbreeder services up /home/me/agentbreeder-projects/hello --environment dev --yes",
             "note": null, "url": null},
     "data": {"services": [
                {"name": "postgres", "kind": "service", "state": "absent", "health": "none"},
                {"name": "litellm", "kind": "service", "state": "absent", "health": "none"}],
              "grant_recorded": false, "environment_written": false}},
    {"id": "build", "title": "Build", "required": true, "state": "todo",
     "reason": {"code": "NOT_BUILT", "message": "No build manifest at deploy/hello.build.json"},
     "fix": {"action": "build",
             "command": "agentbreeder build /home/me/agentbreeder-projects/hello/agent.yaml --stack /home/me/agentbreeder-projects/hello --environment dev",
             "note": null, "url": null},
     "data": {"image": null, "build_manifest": null, "environment": null,
              "agent_yaml_current": false, "lock_current": true}},
    {"id": "release", "title": "Release (eval gate)", "required": true, "state": "todo",
     "reason": {"code": "NOT_RELEASED", "message": "No production deployment"},
     "fix": {"action": "release",
             "command": "agentbreeder release /home/me/agentbreeder-projects/hello/agent.yaml --stack /home/me/agentbreeder-projects/hello --environment dev",
             "note": null, "url": null},
     "data": {"production": null, "released": false, "blocker": false, "gate_failed": false, "failed_tail": false}},
    {"id": "try", "title": "Try it", "required": true, "state": "todo",
     "reason": {"code": "NOT_CHECKED", "message": "Release first"},
     "fix": null, "data": null}
  ],
  "conditions": []
}
```

### Top level

| Field | Meaning |
|---|---|
| `schema_version` | `1`. Changes only on a breaking change (see "Compatibility"). |
| `project` | The project directory, absolute. |
| `environment` | The environment the checks used (`[a-z0-9-]`, at most 32 characters). |
| `complete` | `true` exactly when every `required` step is `done`. |
| `next` | The id of the step in state `next`, or `null` when nothing is runnable now: everything is done, or the first step not done is `blocked`. |
| `steps` | Every step, in the order below. `kubernetes` is present only when the stack has a cluster attached (`cluster up`; it disappears after `cluster down`). |
| `conditions` | Live problems (below); empty when there are none. |

Every field is always present; a field with no value is `null` (or `[]` for a list), never
omitted, so there is one shape to read.

### Steps

Steps always appear in this order. Each has `id`, `title` (fixed per id: the table's first
column, capitalised as shown in the examples), `required` (`false` only for `kubernetes`),
`state`, `reason`, `fix` and `data` (shape fixed per id).

| `id` | Required | Done when | Reason codes when not done (blocking ones in bold) | `fix.action` |
|---|---|---|---|---|
| `prerequisites` | yes | every required `doctor` check passes (the PATH check is advisory: `required: false` in `data.checks`) | **`PREREQUISITE_FAILED`** | none (the failing check's remedy is in `note`) |
| `project` | yes | `agent.yaml` and `stack.yaml` exist in `DIR`, validate, and every ref resolves | `PROJECT_MISSING`, **`PROJECT_INVALID`**, **`REF_UNRESOLVED`** | `new-agent` or `example` for `PROJECT_MISSING` |
| `keys` | yes | every provider key the stack's LiteLLM config names for the aliases `environment.models` lists (each such model's `api_key: os.environ/NAME`; a key only a fallback alias uses is optional; never the generated `LITELLM_MASTER_KEY` or `DATABASE_URL`) is set in the environment of `next` or stored by an earlier `services up` | `KEYS_MISSING`, **`KEYS_CONFIG_INVALID`** | `set-keys` |
| `services` | yes | every service and MCP relay container is `running` and not `unhealthy` (one `compose ps`), and `services up` has written this environment's operator config | **`SERVICES_UNREADABLE`**, `SERVICES_DOWN`, `SERVICES_UNHEALTHY`, `ENVIRONMENT_MISSING` | `services-up`, except none for `SERVICES_UNREADABLE` |
| `build` | yes | `agent.yaml` has no `path:` ref, and `NAME.build.json` in the output directory was written for this environment and records the current `agent.yaml` and `agent.lock` hashes, and the lock's keys (`path:` keys included) are exactly the agent's refs | `REF_UNPINNED`, `LOCK_STALE`, `NOT_BUILT`, `BUILD_STALE` | `build` for `NOT_BUILT` and `BUILD_STALE`; none for `REF_UNPINNED` and `LOCK_STALE` |
| `release` | yes | the active local production deployment is `ready`, runs the image and deploy manifest the current build wrote, **and** a matching `release/promoted` audit row exists | **`STATE_NEEDS_MIGRATION`**, **`APPROVAL_PENDING`**, **`PRODUCTION_DISABLED`**, `RELEASE_GATE_FAILED`, `NOT_RELEASED`, `PRODUCTION_STALE`, `NO_RELEASE_EVIDENCE`, `RELEASE_THRESHOLD_LOWERED`, `RELEASE_EVALSET_CHANGED`, `PRODUCTION_NOT_READY` | `release`, except none for the blocking codes |
| `try` | yes | exactly when `release` is done (no request is sent) | only `NOT_CHECKED` | `invoke`, as a suggestion on the done step |
| `kubernetes` | no | (`NOT_CHECKED` while Docker is unreachable) an active, `ready` kubernetes deployment exists and its `promote/promoted` audit row's image is production's image | **`APPROVAL_PENDING`**, `CLUSTER_DOWN`, `NOT_PROMOTED`, `PROMOTION_STALE` | `cluster-up` for `CLUSTER_DOWN`; `promote` for `NOT_PROMOTED` and `PROMOTION_STALE` |

Any step can also carry **`NOT_CHECKED`**: it cannot be checked until an earlier step is done (no
agent name or stack to read, Docker not reachable, state that needs `migrate`). Its `data` is
`null`, its state is `todo` and its `fix` is `null`. `data` is also `null` with
`STATE_NEEDS_MIGRATION`, whose state could not be read.

When several codes apply, the step reports the first in the table's order. Notes on some codes:

- `SERVICES_DOWN`: no container of the stack is running (never brought up, or after
  `services down`). `SERVICES_UNHEALTHY`: some are running and at least one is not running or is
  `unhealthy`. `SERVICES_UNREADABLE`: Docker is reachable but `compose ps` failed (Compose
  missing, a Docker context mismatch, a timeout); `note` names the cause.
- `REF_UNPINNED`: `agent.yaml` has a `path:` ref, which `release` refuses (it builds
  `--locked`). `command` is `registry publish DIR`, and `note` says to change the ref to the
  published version, then `build`. The step is not done while a `path:` ref remains, even if a
  plain `build` succeeded.
- `LOCK_STALE` (the lock no longer matches `agent.yaml`'s refs, the normal edit loop) is `next`,
  not blocked: a plain `build` from the CLI fixes it. It has no console action because the
  console's build job is `build --locked`, which refuses it; the console shows `command` instead. It is
  reported before `NOT_BUILT` and `BUILD_STALE`, whose Build action would fail on a stale lock.
- `RELEASE_GATE_FAILED`: the latest `release` audit row for the current build's image, eval set
  hash and threshold (the row's `RELEASE_IMAGE`, `RELEASE_EVALSET` and `RELEASE_THRESHOLD`) is
  `eval_failed`. Publishing new evals or changing the threshold makes a new combination, so the
  code clears even when the image ID is unchanged. The action is `release` (LLM-judged cases can
  pass on another run); `note` says to edit the prompt or evals, publish a new version, change
  the ref, `build` and `release`, and `command` is `logs --failed NAME …` when `data.failed_tail`
  is true, otherwise `release …`.
- `PRODUCTION_STALE`: production runs another image or another deploy manifest than the current
  build wrote (a config-only change shows the same image with `manifest_current: false`).
- `PRODUCTION_NOT_READY`: production is recorded `pending`, `starting`, `failed`, `removing` or
  `disabled` with no blocker left (after `enable`, which starts nothing); `release` deploys it
  again.
- `PRODUCTION_DISABLED`: the kill switch blocks production's principal in this environment (a
  `blockers` row: the release step's `data.blocker`); `release` would be denied. `command` is
  `agentbreeder enable ID --state-dir <stack state> …`, which only an authorised operator can run,
  with the stack's operator config (`note` names the `AGENTBREEDER_OPERATOR_CONFIG` value). Once the blocker is gone
  the code becomes `PRODUCTION_NOT_READY` with a Release action. A principal blocked before any production
  exists is not reported (there is no production to name), so the step shows `NOT_RELEASED` with
  a Release action that the gate denies; the job's output then explains it.
- `APPROVAL_PENDING`: an unexpired pending row in the local `approvals` table names this agent or
  `NAME-candidate` (a deploy waiting for a second person: `agentbreeder approve ID`; `release`
  stops with exit 3 on either). Rows are matched by agent name, environment, state and expiry:
  their `revision` is the gate's revision digest (manifest, image, principal, deployer, policy),
  not something `next` can recompute. The table has no target column, so a `NAME-candidate` row
  is always the release step's, and a `NAME` row is the `kubernetes` step's only when the stack
  has a cluster attached and the release step is otherwise done; otherwise it is the release
  step's. `note` says who must run `approve` and that it needs this stack's state and operator
  config (`approve`, like `enable`, has no `--stack` yet; a follow-up issue adds it, and these
  notes will then print `--stack`). Matching by name makes this a hint: a pending approval for an
  earlier revision of the same agent matches too, so the message says only that a deploy of this
  agent is waiting, not that it is for the current build. When policy requires
  approval, `release` stops before it creates a request, so nothing is recorded and `next` shows
  `NOT_RELEASED` with a Release action that exits 3; the job's output then explains it.
- A leftover `NAME-candidate` deployment is ignored by every step and condition.
- `STATE_NEEDS_MIGRATION`: the local state is from an older version or an interrupted write left
  a journal; `command` is `agentbreeder migrate …`. `try` and `kubernetes` are then `NOT_CHECKED`.
- `ENVIRONMENT_MISSING`: the stack was brought up for other environments, not this one.
- `KEYS_CONFIG_INVALID`: the LiteLLM config fails AgentBreeder's review (`services up` would
  refuse it); `message` names the field, never its value.
- `PROJECT_INVALID` and `REF_UNRESOLVED`: `data.error_code` is the validator's code; `message`
  names a file or ref, never file content or a child process's stderr. `REF_UNRESOLVED` names the
  file that holds the ref, and its fix is a note (publish it or fix the ref), since `validate`
  does not resolve refs. An agent without an `evals` ref is `PROJECT_INVALID` with `error_code`
  `EVALS_MISSING`: `release` refuses it. It blocks the whole path, so `services up` and `build`
  are not offered for it either, since the path cannot finish without evals.
- `PROJECT_MISSING`: the fix is `new agent` when this install has it, otherwise `example
  northwind` (into `DIR` when it is empty). `try` suggests `invoke` only when this install has
  it; otherwise a done `try` has no fix.

### States

- `done`: the step's check passes. `reason` is `null`; `fix` is `null` except on `try`. A done
  `build` names its image and build manifest, built for this environment from the current files;
  a done `release` has a `ready` production on that image with `manifest_current`, `released`
  true and `gate_failed` false.
- `next`: the first step not done (required steps first, then `kubernetes`) when its reason is
  not a blocking one. Only one step is `next`, and the top-level `next` names it. It always has a
  `fix`.
- `blocked`: that same first step when its reason is a blocking one (bold above): nothing can be
  run from this step until it is fixed by hand. It always has a `fix` with `action: null`
  (`command` and/or `note` say what to do), the top-level `next` is `null`, and a `STEP_BLOCKED`
  condition names it.
- `todo`: every other step not done. It is still checked where it can be, so its `reason` and
  `data` are real (a later step can even be `done` while an earlier one is not, for example
  production stays recorded while the services are down); otherwise it is `NOT_CHECKED`.

`kubernetes` is `next` only when every required step is done; `complete` is then already `true`.
A stack without a LiteLLM config has `keys` done with empty lists.

### `fix`

| Field | Meaning |
|---|---|
| `action` | What a console button does. `services-up`, `build`, `release`, `cluster-up` and `promote` are the console job kinds of the same name (the console supplies the job's arguments itself, including the grant review for `services-up`). `new-agent` (Create starter), `example` (Onboard Northwind), `set-keys` (the key fields of the services form) and `invoke` (Try it) are console actions, not jobs. `null`: no button; show `command` and `note`. Each step allows only the actions in the table above. |
| `command` | The CLI command in the form of the process that ran `next` (`agentbreeder`, `npx agentbreeder@<ver>`, `uvx --from … agentbreeder` or `python -m agentbreeder`), one line, every interpolated value shell-quoted, values only as placeholders (`<your key>`). **Display only**: neither the CLI nor the console runs it. |
| `note` | One extra line, e.g. `export GEMINI_API_KEY=<your key>` or a `doctor` remedy. |
| `url` | A link from a fixed table built into the CLI (today only `https://aistudio.google.com/apikey`), never from project, registry or state content. |

At least one of `action`, `command` and `note` is set.

### `data`

Names, flags and paths only; never a secret value. Paths are absolute and inside `project`. Image
IDs are `sha256:<64 hex>`; a deployment's `image` may also be `repo[:tag]@sha256:…` (a
hand-deployed manifest). Endpoints are `http://127.0.0.1:<port>`; Kubernetes deployments are not
reachable that way (use `forward`).

- `prerequisites`: `checks`: `doctor`'s checks (`name`, `passed`, `required`, `message`).
- `project`: `agent` (`agent.yaml`'s `name`, when it parses), `agent_file`, `stack_file`,
  `error_code`, and `newer`: pinned refs with a newer published version (`ref`, `latest`, the
  `file` and `line` to change, then `build`). `newer` is advisory and never changes the state.
- `keys`: `required`, `present`, `stored`, `missing`: provider key names. `present` and `stored`
  are subsets of `required`; `missing` is `required` minus both. `present` reads the environment
  of the process that ran `next`; the console runs `next` with provider keys stripped from its
  environment, so there `present` is always empty and only `stored` counts. The CLI and the
  console can therefore differ on this step until `services up` has stored the key.
- `services`: `services` (`name`; `kind`: `service` or `mcp-relay`; `state`: Compose's state or
  `absent`; `health`: `healthy`, `unhealthy`, `starting` or `none`), `grant_recorded` (a
  `services up` has completed for this stack; `next` does not recompute whether the grant has
  changed since, because that needs the stack plan, which writes), `environment_written`.
- `build`: `image` (the image ID built), `build_manifest`, `environment` (the one it was built
  for), `agent_yaml_current`, `lock_current`.
- `release`: `production` (`id`, `image`, recorded `state`, `endpoint`, `manifest_current`) or
  `null` (its `state` is the stored one: `pending`, `starting`, `ready`, `failed`, `removing` or
  `disabled`); `released` (a `release/promoted` row whose `RELEASE_IMAGE` and `RELEASE_MANIFEST`
  are the image and manifest digest production runs, whatever that release's threshold or eval
  set: `RELEASE_THRESHOLD_LOWERED` and `RELEASE_EVALSET_CHANGED` leave it true, and a rebuild
  that makes production `PRODUCTION_STALE` does not change it; when `evidence.released` itself
  rejects that row, for example a malformed threshold, or the agent has no evals, `released` is
  false and the reason is `NO_RELEASE_EVIDENCE`: unreadable evidence is no evidence); `blocker` (a kill-switch blocker
  for production's principal); `gate_failed` (as
  `RELEASE_GATE_FAILED` defines it); `failed_tail` (a
  saved failed-candidate log tail exists; it is best effort and is not tied to an image).
- `try`: `deployment`, `endpoint`: production's. When `release` is not done, `try` is
  `NOT_CHECKED` and the guide gives no endpoint; the console's Try it reads state itself.
- `kubernetes`: `cluster` (name), `cluster_up`, `deployment` (the active kubernetes deployment),
  `promoted_image` (the image recorded by its `promote/promoted` audit row), `production_image`.

### `conditions`

Live problems, computed on every run and never stored (guided spec §14.4); they clear themselves
when the cause goes away. The console's notifications show the same set. Each has `code`,
`message`, `step`, `deployment` and `service` (the last three `null` when they do not apply;
`STEP_BLOCKED` always names a `step`, `DEPLOYMENT_*` and `PRODUCTION_UNGATED` a `deployment`,
`SERVICES_UNHEALTHY` a `service`).

| Code | When |
|---|---|
| `STEP_BLOCKED` | exactly when the first step not done is `blocked` (`step` names it) |
| `DEPLOYMENT_FAILED` | an active deployment of this agent (not `NAME-candidate`) is recorded `failed`; `deployment` names it |
| `DEPLOYMENT_NONCOMPLIANT` | an active deployment's recorded identity compliance, as of the last `reconcile`, starts with `noncompliant` (never reconciled, and `unbound`, are not conditions) |
| `SERVICES_UNHEALTHY` | Docker is reachable, `grant_recorded` is true, at least one container of the stack exists, and a service is not `running` or is `unhealthy` (an `absent` one included); one condition per service, `service` names it. A stack with no container at all (never up, or after `services down`) is the `services` step, not a condition. |
| `PRODUCTION_UNGATED` | exactly when production exists and `released` is false, whatever the release step's reason (a hand-deployed image that then failed the gate included); `deployment` names production |

A step in `todo` or `next` is not a condition.

The second example: Docker is stopped after a release. `services` cannot be checked; the release
is still recorded.

```json
{
  "schema_version": 1,
  "project": "/home/me/agentbreeder-projects/hello",
  "environment": "dev",
  "complete": false,
  "next": null,
  "steps": [
    {"id": "prerequisites", "title": "Prerequisites", "required": true, "state": "blocked",
     "reason": {"code": "PREREQUISITE_FAILED", "message": "Docker is not reachable"},
     "fix": {"action": null, "command": "agentbreeder doctor",
             "note": "Start Docker Desktop, then run doctor again", "url": null},
     "data": {"checks": [{"name": "Docker", "passed": false, "required": true, "message": "Docker is not reachable"}]}},
    {"id": "project", "title": "Project", "required": true, "state": "done", "reason": null, "fix": null,
     "data": {"agent": "hello", "agent_file": "/home/me/agentbreeder-projects/hello/agent.yaml",
              "stack_file": "/home/me/agentbreeder-projects/hello/stack.yaml", "error_code": null, "newer": []}},
    {"id": "keys", "title": "Model keys", "required": true, "state": "done", "reason": null, "fix": null,
     "data": {"required": ["GEMINI_API_KEY"], "present": [], "stored": ["GEMINI_API_KEY"], "missing": []}},
    {"id": "services", "title": "Services", "required": true, "state": "todo",
     "reason": {"code": "NOT_CHECKED", "message": "Docker is not reachable"},
     "fix": null, "data": null},
    {"id": "build", "title": "Build", "required": true, "state": "done", "reason": null, "fix": null,
     "data": {"image": "sha256:4f1c0a6c6d3be1a1f4e3c1b5e7a2a0f8d4c3b2a19087f6e5d4c3b2a190876543",
              "build_manifest": "/home/me/agentbreeder-projects/hello/deploy/hello.build.json",
              "environment": "dev", "agent_yaml_current": true, "lock_current": true}},
    {"id": "release", "title": "Release (eval gate)", "required": true, "state": "done", "reason": null, "fix": null,
     "data": {"production": {"id": "3f2a9c1d0b8e4a7f9c6d5e4f3a2b1c0d",
                             "image": "sha256:4f1c0a6c6d3be1a1f4e3c1b5e7a2a0f8d4c3b2a19087f6e5d4c3b2a190876543",
                             "state": "ready", "endpoint": "http://127.0.0.1:52011", "manifest_current": true},
              "released": true, "blocker": false, "gate_failed": false, "failed_tail": false}},
    {"id": "try", "title": "Try it", "required": true, "state": "done", "reason": null,
     "fix": {"action": "invoke",
             "command": "agentbreeder invoke hello Hello --stack /home/me/agentbreeder-projects/hello --environment dev",
             "note": null, "url": null},
     "data": {"deployment": "3f2a9c1d0b8e4a7f9c6d5e4f3a2b1c0d", "endpoint": "http://127.0.0.1:52011"}}
  ],
  "conditions": [
    {"code": "STEP_BLOCKED", "message": "Prerequisites: Docker is not reachable",
     "step": "prerequisites", "deployment": null, "service": null}
  ]
}
```

The third example: an empty directory. Nothing past `project` can be checked.

```json
{
  "schema_version": 1,
  "project": "/home/me/work/empty",
  "environment": "dev",
  "complete": false,
  "next": "project",
  "steps": [
    {"id": "prerequisites", "title": "Prerequisites", "required": true, "state": "done",
     "reason": null, "fix": null,
     "data": {"checks": [{"name": "Docker", "passed": true, "required": true, "message": "Docker engine 27.5.1 is reachable"}]}},
    {"id": "project", "title": "Project", "required": true, "state": "next",
     "reason": {"code": "PROJECT_MISSING", "message": "No agent.yaml in this directory"},
     "fix": {"action": "new-agent", "command": "agentbreeder new agent hello --model gemini",
             "note": null, "url": null},
     "data": {"agent": null, "agent_file": null, "stack_file": null, "error_code": null, "newer": []}},
    {"id": "keys", "title": "Model keys", "required": true, "state": "todo",
     "reason": {"code": "NOT_CHECKED", "message": "Create a project first"}, "fix": null, "data": null},
    {"id": "services", "title": "Services", "required": true, "state": "todo",
     "reason": {"code": "NOT_CHECKED", "message": "Create a project first"}, "fix": null, "data": null},
    {"id": "build", "title": "Build", "required": true, "state": "todo",
     "reason": {"code": "NOT_CHECKED", "message": "Create a project first"}, "fix": null, "data": null},
    {"id": "release", "title": "Release (eval gate)", "required": true, "state": "todo",
     "reason": {"code": "NOT_CHECKED", "message": "Create a project first"}, "fix": null, "data": null},
    {"id": "try", "title": "Try it", "required": true, "state": "todo",
     "reason": {"code": "NOT_CHECKED", "message": "Create a project first"}, "fix": null, "data": null}
  ],
  "conditions": []
}
```

## Compatibility

- `schema_version` changes only when a field is removed, renamed or retyped, or a meaning
  changes. Within a version, fields, reason codes and condition codes may be **added**.
- Consumers ignore fields they do not know, and show `message` for a reason or condition code
  they do not know. Step ids, states and `fix.action` values are closed within a version; a new
  one is a new version. A console that meets an `action` it does not know treats it as `null`.
- `docs/next.schema.json` leaves objects open (no `additionalProperties: false`), so validating a
  later 1.x output with it still passes. The producer itself is stricter: its models refuse
  undeclared fields, so it cannot print one by accident. Its patterns use `$` as "end of string"
  (Rust regex semantics, as Pydantic runs them); a Python consumer re-checking a pattern should
  use `re.fullmatch`.
- Every string is one line and is capped (messages and notes at 300 characters, commands at
  2000). It has no control or format characters (bidi, zero-width), surrogates, private-use or
  unassigned code points, line or paragraph separators, spaces other than U+0020, or characters
  that render as nothing (fillers, variation selectors). The producer fails rather than print one
  that breaks this (`GUIDE_OUTPUT_INVALID`). This rule and the cross-field rules (states,
  evidence, paths inside `project`) are enforced by the models, not expressed in the JSON Schema;
  a console in the same install should validate what it reads with `guide.Guide.model_validate`
  before using it.

## What the console must not do with it

The console builds job arguments only from the registered project row and its own validated
options (`center/jobs.py` `argv_for`), and runs `next` with fixed arguments (never `--output` or
`DIR` from request data). Nothing in this output is a job argument, a fetch target
or a file to read: not `command`, `project`, paths, `endpoint` (an unauthenticated agent endpoint,
#780; `invoke` re-reads and re-validates state itself), deployment ids or `url` (opened only on a
click, with `rel="noopener noreferrer"`, host shown). The console discards a result whose
`project` is not its registered path, and places all text with `textContent`. Absolute paths show
the home directory and user name; mind that when pasting output into an issue.
