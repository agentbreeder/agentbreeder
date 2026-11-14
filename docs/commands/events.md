# `agentbreeder events`

List stored events (failed jobs, eval gate failures, containers that exited).

`events [--project DIR] [--environment ENV] [--limit N] [--unread] [--mark-read ID] [--mark-all-read] [--json]` lists stored events (failed jobs, eval gate failures with case ids and counts, containers that exited by themselves or became unhealthy; fixed templates, no free text, the newest 500 kept in `~/.agentbreeder/center/events.sqlite3`), and with `--project` that project's live conditions from `next`'s checks; `release` stores a gate failure itself. See [`events.md`](../events.md) (on `main`, not in a published preview).

Flags: `agentbreeder events --help`.
