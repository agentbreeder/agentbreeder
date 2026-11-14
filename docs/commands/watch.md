# `agentbreeder watch`

Report one stack's container events (died, unhealthy, restarted) until stopped.

`watch --stack DIR [--json]` reports one stack's container events until stopped, reading only (the stack state and every environment's deployment state opened read-only; one `docker events` child for container events): `died` when a container exits by itself (a `die` with no `kill` or `stop` for it since it last started, so a stop, `docker kill`, `remove`, a release or `services down` reports nothing; an agent container whose deployment is not `ready` is the running job's and reports nothing either; `exit_code` and `oom`), `unhealthy` when a health check starts failing, and `restarted` when a container that died starts again (the CLI cannot see the console's jobs, so the console drops `restarted` while its own `services up` runs).

Stack services match by the stack label (one-shot `init` and `bootstrap` steps are skipped), agent containers by this stack's deployment IDs. It prints a `status` record (`services` from one `docker compose ps`, null when compose could not be read; `deployments` with `id`, `name`, `target` and `state`, and for a ready Kubernetes (local `kind`) deployment a `pod` check, `running` and `reason`) at the start and every 60 s; container events cover local targets only.

`--json` prints one object per line (`schema_version`, `event`, `time`, `source`, `name`, `deployment`, and `role` for a sidecar) and ends with `{"event": "end", "reason": "stdin|signal|interrupt", "dropped"}` (`dropped` counts records lost when more than 1 000 were waiting). The status is read on a worker thread, so a stop never waits for `compose ps` or kubectl, and `docker events` runs with the same scrubbed environment as every other docker call.

Labels are not authenticated: a container on the same Docker engine can claim this stack's labels, so events are best effort. It stops, with its `docker events` child, on the end of a non-terminal stdin, SIGTERM, SIGHUP or Ctrl-C, as `logs --follow` does; if `docker events` ends by itself it fails with `DOCKER_EVENTS_FAILED`.

`--record` also stores `died` and `unhealthy` events (on `main`, not in a published preview).

Flags: `agentbreeder watch --help`.
