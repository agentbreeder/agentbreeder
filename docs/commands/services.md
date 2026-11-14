# `agentbreeder services`

Local backing services from stack.yaml (local-only preview).

Local-only preview (#734): `services up|status|logs|env|down` start a project's `stack.yaml` backing services with Docker Compose and write a demo operator config under `~/.agentbreeder/stacks/` (see [`services.md`](../services.md)); `--stack DIR` on `build`, `deploy`, `eval`, `release`, `status`, `remove` and `reconcile` uses it.

`services up DIR --environment ENV [--locked | --update] [--env-file FILE] [--yes]` shows the grant and needs `--yes` when it is new or changed (`--update` accepts new hashes for edited project files such as an `init_sql`, never for a published service version); `services down [--volumes | --purge] --yes` deletes the stack's volumes, or with `--purge` everything `up` made (volumes, built service images, generated secrets, operator config and history).

Verified end to end with `src/agentbreeder/examples/northwind` (built-in `postgres-pgvector`, `litellm` and `phoenix`, two project services built from source, least-privilege database roles for the agent and the ops service; release 61/61, 2026-11-08 from a checkout and 2026-11-10 from PyPI `3.0.0a1`).

`services logs` now uses the same redactor as [`logs ID`](logs.md) (literal, base64, URL-quoted and JSON-escaped forms, URL passwords) and also redacts the stack's deployments' secret copies (on `main`, not in a published preview).

`services logs [DIR] NAME [--tail N] [--since D] [--follow] [--json] [--raw]` reads a stack service with `docker compose logs --no-color --no-log-prefix --timestamps` (`--tail` default 200, at most 10 000; a 4 MiB cap), joins Docker's 16 KiB partial records, redacts every line and prints nothing when a value it needs cannot be read (`REDACTION_INCOMPLETE`, as `logs ID`); controls are shown as visible escapes unless `--raw`. `--json` prints `{"schema_version": 1, "source": "service", "target": "local", "name", "lines", "truncated"}`, the same shape as `logs ID`, and `--follow` streams with the same stop rules, NDJSON lines (`source`, `target`, `name`, `line`) and end record. Lines start with Docker's timestamp (no `name |` prefix). A follow carries on across the service's container restarts and stops compose and its plugin together. Errors are coded (`DOCKER_LOGS_FAILED` when compose cannot read the logs), with the JSON error object under `--json`; compose's own stderr is never shown (on `main`, not in a published preview).

`services logs` takes `--level warning|error` as `logs ID` does; see [`logs`](logs.md).

`services usage [DIR] [--follow] [--json]` shows one stack's resource use, reading only: its containers are matched as `watch` matches them (stack services by label, agent containers by this stack's deployment IDs) and listed again every 10 s, and one streaming `docker stats` child runs over exactly those container IDs (replaced when the set changes; no child at all when nothing is running).

`--json` prints one object per line, each with `schema_version` and `event`: `sample` (`time`, `containers` with `source`, `name`, `deployment`, `role` for a sidecar, `cpu_percent`, `memory_used`, `memory_limit`, `memory_percent`, `network_rx`, `network_tx`, `block_read`, `block_write` and `pids`; bytes as integers, null where Docker has no value), at most one every 2 s; `disk` (`as_of`, `stale`, `volumes` with `name` and `size`, `containers` with their writable-layer `size`) from `docker system df -v` every 60 s, read on a worker thread and cached, so `as_of` says how old it is and `stale` that the latest reading failed; and, when it is stopped, the end record `{"event": "end", "reason": "stdin|signal|interrupt", "dropped"}`. CPU and memory are of the Docker VM on Docker Desktop. Without `--follow` it prints one sample and one disk reading and exits (`DOCKER_STATS_FAILED` if they do not come within 30 s).

`--follow` stops, with its `docker stats` child, on the end of a non-terminal stdin, SIGTERM, SIGHUP or Ctrl-C (`docker stats` ignores SIGTERM, so it is killed). `docker stats` exits at once when a listed container is already gone, so a child that ends is replaced after a new listing; when three in a row end, or the containers cannot be listed three times in a row, it fails with `DOCKER_STATS_FAILED`. Docker's container names, labels and mounts are never printed; labels are not authenticated, so usage is best effort (on `main`, not in a published preview).

Flags: `agentbreeder services --help`.
