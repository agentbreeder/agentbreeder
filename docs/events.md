# Events and conditions

On `main`, not in a published preview (#827, M11).

AgentBreeder reports two kinds of things that need attention:

- **Conditions** describe the current state and clear themselves: a blocked guided-path step, a
  `failed` or noncompliant deployment (as recorded at the last reconcile), a stack service that is
  down or unhealthy, or production running an image that no release promoted. They are exactly
  what `agentbreeder next` prints, are computed by the same function, and are never stored.
- **Events** record things that happened and stay true afterwards. They are stored.

## Stored events

| Kind | Written by | Fields |
|---|---|---|
| `job_failed` | the console, when a job fails or is cancelled (not wired yet) | job kind, exit code, the CLI's error code |
| `gate_failed` | `release`, when the eval gate fails (exit 5) | agent, passed/total, failed case ids, report path |
| `container_died` | `watch --record`, when a container exits by itself | service or agent, exit code, out of memory |
| `container_unhealthy` | `watch --record`, when a health check starts failing | service or agent |

Nothing stored is free text. The CLI's output is reduced to its error code (one matching
`[A-Z0-9_]{3,40}`), and the judge's reasons and check names are not kept. Case ids are capped at
80 characters with control characters removed, and at most 50 are kept (the rest are counted).
Messages are made from a fixed template when the events are read. A `restarted` container is not
stored: the condition clearing already shows the recovery.

Stopping a container never stores an event. `docker stop`, `docker kill`, `remove`, a release
replacing its candidate and `services down` all stop containers on purpose (see
[`watch`](commands/watch.md)). A clean release therefore stores nothing, and so does a `services down`.

Events are kept in `~/.agentbreeder/center/events.sqlite3`, or under `AGENTBREEDER_CENTER_DIR`
when it is set. The directory is 0700 and the file 0600. Only the newest 500 events are kept.
`services down --purge` does not remove the file; delete it to clear the history.

## `agentbreeder events`

```
agentbreeder events [--project DIR] [--environment ENV] [--limit N] [--unread]
                    [--mark-read ID ...] [--mark-all-read] [--json]
```

Lists stored events, newest first (default 50, at most 500). `--project DIR` keeps that
project's events only (the resolved directory of the stack or agent) and adds its live
conditions from `next`'s checks for `--environment` (default `dev`). `--mark-read ID`
(repeatable) and `--mark-all-read` (with `--project`, that project's only) mark events read
before they are listed. Listing opens the store read-only and creates nothing.

`--json` prints:

```json
{
  "schema_version": 1,
  "events": [
    {
      "id": 12,
      "time": "2026-11-13T10:00:00Z",
      "project": "/path/to/project",
      "kind": "container_died",
      "source": "service",
      "name": "postgres",
      "deployment": null,
      "exit_code": 1,
      "code": null,
      "data": {"oom": false},
      "read": false,
      "message": "service postgres exited (exit 1)"
    }
  ],
  "unread": 1,
  "conditions": [{"code": "SERVICES_UNHEALTHY", "message": "postgres is exited (none)"}]
}
```

`conditions` appears only with `--project`. It is `null`, with a `conditions_error` of
`{"code", "message"}`, when the checklist could not be computed (for example `STATE_BUSY` while
a job holds the state). `data` depends on the kind:

- `job_failed`: `{"cancelled"}`
- `gate_failed`: `{"passed", "cases", "failed", "more", "report"}`, where `more` counts the
  failed cases beyond the 50 listed
- `container_died`: `{"oom"}`
- `container_unhealthy`: `{}`

Errors are `[CODE] message` on stderr, exit 2: `EVENTS_UNREADABLE` (also when a writer held
the store for more than 5 s; try again), `EVENTS_TOO_NEW`, `EVENTS_UNSAFE` (the directory is
not a real directory you own, or the file is a symlink) or `ENVIRONMENT_INVALID`. With `--json` the error is
also printed as `{"schema_version": 1, "error": {"code", "message"}}`.

## Recording

`watch --stack DIR --record` also stores the `died` and `unhealthy` events it prints. It fails
with `EVENTS_UNSAFE` or `EVENTS_UNREADABLE` when the store cannot be opened. `release` stores
`gate_failed` itself, whether it was run from a terminal or by the console. For the console's job
runner, `agentbreeder.events.job_failed` builds a `job_failed` event, but the console does not
call it yet. A job waiting for approval (exit 3) is not an event,
and neither is a release the gate stopped (exit 5), which is stored as `gate_failed`. Storing an
event never fails the command that stores it.

Labels on containers are not authenticated, so container events are best effort. AgentBreeder
sends nothing off the machine.
