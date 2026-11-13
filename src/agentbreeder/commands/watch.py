"""`agentbreeder watch --stack DIR [--json]`: one stack's container events and status, until
stopped (#821 M5; guided §14.4, §14.9 R1, R5). The console runs one per open project.

It reads, never writes: the stack state and every environment's deployment state are opened
read-only. It starts one `docker events` child (container events, filtered by action; the
same scrubbed environment as every other docker call) and prints, as one JSON object per line
with `--json`:

- `{"event": "status", "services": [...], "deployments": [...]}` once the child runs and every
  RESYNC seconds: one `docker compose ps` for the services (`name`, `state`, `health`, each
  validated or null) and the deployment rows (`id`, `name`, `target`, `state`), with a pod
  check for each `ready` Kubernetes (local kind) deployment (`pod`: `running`, `reason`; null
  when the cluster could not be read). It is read on a worker thread, so a stop never waits
  for it. Container events cover local targets only (R5).
- `died`, `unhealthy` and `restarted` events (`watch.Events`; a stop, `remove`, a release or
  `services down` reports nothing).
- `{"event": "end", "reason", "dropped"}` when it stops: stdin ended (the console closes it),
  SIGTERM, SIGHUP or Ctrl-C; `dropped` counts records lost because more than QUEUE_LIMIT were
  waiting. If `docker events` itself ends, an error object instead (exit 2).

Stopping follows S-10, as `logs --follow`: the child stays in the CLI's process group and is
stopped with its descendants. Errors are `[CODE] message` (with `--json` also the one-line
`{"schema_version": 1, "error": {...}}`), exit 2.
"""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from agentbreeder import safe_text
from agentbreeder.commands import logs as logs_command
from agentbreeder.commands.logs import LogsError

NAMES = ("watch",)
RESYNC = 60.0
LINE_LIMIT = 256 * 1024  # a docker event record is far smaller; a longer line is dropped
QUEUE_LIMIT = 1000  # records waiting for the main loop; more are dropped and counted
ENDED = {**logs_command.ENDED, "events": "docker events ended"}


def register(commands) -> None:
    watch = commands.add_parser(
        "watch",
        help="Report one stack's container events (died, unhealthy, restarted) until stopped",
    )
    watch.add_argument(
        "--stack", type=Path, required=True, metavar="DIR", help="The stack's project directory"
    )
    watch.add_argument("--json", action="store_true", help="One JSON object per line")


def run(args) -> int:
    try:
        return _watch(args)
    except LogsError as error:
        print(str(error), file=sys.stderr)
        if args.json:
            message = str(error).removeprefix(f"[{error.code}] ")
            body = {"code": error.code, "message": message}
            print(json.dumps({"schema_version": 1, "error": body}), flush=True)
        return 2


def _message(error) -> str:
    """A ServiceError's message without its own `[CODE] ` prefix, bounded and escaped."""
    return safe_text.excerpt(str(error).removeprefix(f"[{error.code}] "), 400)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_stack(directory: Path):
    """(stack state, opened read-only) for DIR, or LogsError."""
    from agentbreeder.services.schema import ServiceError, load_stack
    from agentbreeder.services.state import StackState

    project = directory.resolve()
    try:
        stack = load_stack(project / "stack.yaml")
        state = StackState.open_readonly(stack.name, project)
    except ServiceError as error:
        raise LogsError(error.code, _message(error)) from error
    except (OSError, ValueError) as error:
        raise LogsError("STACK_UNREADABLE", "cannot read the stack's state") from error
    if state is None:
        raise LogsError("STACK_NOT_UP", "this stack was never brought up; run `services up`")
    return state


def _reader(state):
    """A function returning this stack's deployment rows (id -> row), read-only, from every
    environment's state. A state that cannot be read keeps the rows last read from it."""
    import sqlite3

    from agentbreeder.store import DeploymentError, Store

    last: dict[str, dict[str, dict]] = {}

    def read() -> dict[str, dict]:
        for environment in state.environments():
            directory = state.deploy_state(environment)
            if not (directory / "deployments.sqlite3").is_file():
                last[environment] = {}
                continue
            try:
                store = Store.open_readonly(directory)
                try:
                    rows = store.active_all()
                finally:
                    store.close()
            except (DeploymentError, OSError, sqlite3.Error):
                continue  # busy or needs migration: keep what we had
            last[environment] = {row["id"]: {**row, "environment": environment} for row in rows}
        merged: dict[str, dict] = {}
        for rows in last.values():
            merged.update(rows)
        return merged

    return read


def _pod(row: dict) -> dict | None:
    """{"running", "reason"} for a ready Kubernetes deployment, or None if unreadable."""
    try:
        runtime = logs_command._kubernetes()
        runtime.authorize(row["context"])
        info = runtime.inspect(row)
        if info is None:
            return {"running": False, "reason": "GONE"}
        pod = runtime.pod(info)
        return {"running": pod["reason"] is None and pod["readable"], "reason": pod["reason"]}
    except Exception:  # noqa: BLE001 - kubectl missing, cluster down: the pod is unknown
        return None


def _target(row: dict) -> str:
    """The recorded manifest's target (`local` unless it says `kubernetes`)."""
    try:
        manifest = json.loads(row.get("manifest") or "{}")
    except (TypeError, ValueError):
        return "local"
    target = manifest.get("target") if isinstance(manifest, dict) else None
    return "kubernetes" if target == "kubernetes" else "local"


_WORD = re.compile(r"[a-z][a-z ]{0,23}")


def _word(value) -> str | None:
    """A compose state or health word (`running`, `exited`, `healthy`, …), else None."""
    return value if isinstance(value, str) and _WORD.fullmatch(value) else None


def _status(state, docker, deployments: dict[str, dict]) -> dict:
    """The status record. Runs on a worker thread: `compose ps` and kubectl may be slow, and
    a stop must not wait for them."""
    from agentbreeder.watch import _SERVICE

    try:
        rows = docker.containers(state)
        services = [
            {"name": name, "state": _word(row.get("state")), "health": _word(row.get("health"))}
            for name, row in sorted(rows.items())
            if isinstance(name, str) and _SERVICE.fullmatch(name) and isinstance(row, dict)
        ]
    except Exception:  # noqa: BLE001 - compose could not be read this time
        services = None
    shown = []
    for identifier, row in deployments.items():
        entry = {
            "id": identifier,
            "name": row["name"],
            "target": _target(row),
            "state": row["state"],
        }
        if entry["target"] == "kubernetes" and row["state"] == "ready":
            entry["pod"] = _pod(row)
        shown.append(entry)
    return {
        "schema_version": 1,
        "event": "status",
        "time": _now(),
        "services": services,
        "deployments": shown,
    }


def _status_text(status: dict) -> str:
    services = status["services"]
    parts = (
        ["services unreadable"]
        if services is None
        else [f"{s['name']} {s['health'] or s['state'] or '?'}" for s in services]
    )
    parts += [f"{d['name']} ({d['target']}) {d['state']}" for d in status["deployments"]]
    return f"{status['time']} status: " + (", ".join(parts) or "nothing running")


def _events_argv(docker) -> list[str]:
    from agentbreeder.watch import ACTIONS

    filters = ["--filter", "type=container"]
    for action in ACTIONS:
        filters += ["--filter", f"event={action}"]
    return docker._argv(["events", "--format", "{{json .}}", *filters])


class _Records:
    """Between the reader thread, the status worker and the main loop: a bounded queue of
    this stack's candidate records (`watch.relevant`), status records (`("status", dict)`) and
    the end marker (`None`). Records beyond QUEUE_LIMIT are dropped and counted."""

    def __init__(self) -> None:
        self.queue: queue.Queue = queue.Queue(maxsize=QUEUE_LIMIT)
        self.dropped = 0

    def offer(self, record: dict) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1

    def put(self, item) -> None:
        self.queue.put(item)  # status and the end marker always arrive


def _pump(process, records: _Records) -> None:
    """Read the child's records; `None` marks its end."""
    from agentbreeder.watch import relevant

    try:
        while True:
            line = process.stdout.readline(LINE_LIMIT)
            if not line:
                break
            if not line.endswith(b"\n"):
                while (rest := process.stdout.readline(LINE_LIMIT)) and not rest.endswith(b"\n"):
                    pass  # an over-long record: dropped whole
                continue
            try:
                record = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if relevant(record):
                records.offer(record)
    except (OSError, ValueError):
        pass
    finally:
        records.put(None)


def _status_worker(state, docker, deployments: dict[str, dict], records: _Records) -> None:
    """Always posts a status, so a failure here never stops later resyncs."""
    try:
        status = _status(state, docker, deployments)
    except Exception:  # noqa: BLE001 - e.g. a malformed row: report nothing readable
        status = {
            "schema_version": 1,
            "event": "status",
            "time": _now(),
            "services": None,
            "deployments": [],
        }
    records.put(("status", status))


def _emit(args, event: dict, text: str) -> None:
    if args.json:
        print(json.dumps(event), flush=True)
    else:
        print(safe_text.visible(text), flush=True)


def _quiet_stdout() -> None:
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())
    os.close(devnull)


def _watch(args) -> int:
    from agentbreeder import watch
    from agentbreeder.runtime import start_follow, stop_follow
    from agentbreeder.services.compose import DockerCLI, sanitised_environment
    from agentbreeder.services.schema import ServiceError

    state = _open_stack(args.stack)
    read = _reader(state)
    docker = DockerCLI()
    try:
        docker.authorize()
    except ServiceError as error:
        state.close()
        raise LogsError(error.code, _message(error)) from error
    events = watch.Events(state.id, read, time.monotonic)
    follow = logs_command._Follow()
    previous = logs_command._handlers(follow)
    process = None
    ended = None  # docker events' own exit code, read before we stop it
    records = _Records()
    try:
        with follow.lock:
            if follow.reason is None:
                try:
                    # The child gets the same scrubbed environment as every other docker call
                    # (no DOCKER_HOST/TLS overrides, no keys); `--context` names the engine.
                    follow.process = process = start_follow(
                        _events_argv(docker), sanitised_environment()
                    )
                except OSError as error:
                    raise LogsError(
                        "DOCKER_EVENTS_FAILED", "docker events could not start"
                    ) from error
        if follow.reason is not None:  # a stop signal came while the child was starting
            follow.stop(follow.reason)
        if process is not None:
            try:  # entered at once: a Ctrl-C from here on still stops the child
                threading.Thread(target=_pump, args=(process, records), daemon=True).start()
                descriptor = logs_command._stdin_descriptor()
                if descriptor is not None:
                    threading.Thread(
                        target=logs_command._watch_stdin, args=(follow, descriptor), daemon=True
                    ).start()
                pending = True  # the first status: events are already being read
                threading.Thread(
                    target=_status_worker,
                    args=(state, docker, dict(events.deployments), records),
                    daemon=True,
                ).start()
                due = time.monotonic() + RESYNC
                while True:
                    wait = max(0.05, min(1.0, due - time.monotonic()))
                    try:
                        item = records.queue.get(timeout=wait)
                    except queue.Empty:
                        item = False
                    if item is None:  # the child ended: stopped by us, or docker went away
                        if follow.reason is None:
                            ended = logs_command.exit_code(process)  # before we stop it
                        break
                    if isinstance(item, tuple):
                        pending = False
                        _emit(args, item[1], _status_text(item[1]))
                    elif item is not False:
                        for event in events.feed(item):
                            _emit(args, event, watch.text(event))
                    # A status still pending after two periods (a hung kubectl) does not hold
                    # up the next one.
                    stalled = time.monotonic() >= due + RESYNC
                    if (
                        follow.reason is None
                        and (not pending or stalled)
                        and time.monotonic() >= due
                    ):
                        events.deployments = read()
                        pending = True
                        threading.Thread(
                            target=_status_worker,
                            args=(state, docker, dict(events.deployments), records),
                            daemon=True,
                        ).start()
                        due = time.monotonic() + RESYNC
            except KeyboardInterrupt:
                follow.stop("interrupt")
            except BrokenPipeError:  # `watch | head`: the reader left; stop quietly
                follow.stop("pipe")
                _quiet_stdout()
            finally:
                try:
                    stop_follow(process)
                except KeyboardInterrupt:
                    follow.stop("interrupt")
                    stop_follow(process, grace=0)
    finally:
        follow.cancel()
        for number, handler in previous.items():
            signal.signal(number, handler)
        state.close()
    reason = follow.reason
    if reason == "pipe":
        return 0
    if reason is None:
        reason = logs_command.stop_reason(ended)  # the signal reached docker first
    if reason is None:
        raise LogsError("DOCKER_EVENTS_FAILED", "docker events ended unexpectedly")
    try:
        if records.dropped:
            print(
                f"Note: {records.dropped} records were dropped (too many at once)", file=sys.stderr
            )
        print(f"Watch ended: {ENDED[reason]}", file=sys.stderr)
        if args.json:
            end = {
                "schema_version": 1,
                "event": "end",
                "reason": reason,
                "dropped": records.dropped,
            }
            print(json.dumps(end), flush=True)
    except BrokenPipeError:
        _quiet_stdout()
    return 0
