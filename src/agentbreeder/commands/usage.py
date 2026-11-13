"""`agentbreeder services usage [DIR] [--follow] [--json]`: one stack's resource usage (#821 M5
S-10, M6; console §4.2, §8.1 #6). The console runs one `--follow --json` child.

It reads, never writes: the stack state and every environment's deployment state are opened
read-only. Which containers are the stack's is resolved as `watch` matches them
(`usage.resolve`: two `docker ps` calls) once at the start and every RESOLVE seconds after, on a
worker thread. Their numbers come from ONE streaming `docker stats` child over exactly those
container IDs (never an empty list: `docker stats` alone shows every container on the engine);
when the set changes, the child is replaced. Disk comes from `docker system df -v` every DISK
seconds, on a worker thread, and the last good reading is kept: each `disk` record carries its
`as_of` time and `stale` when the latest reading failed. With `--json`, one object per line:

- `{"event": "sample", "time", "containers": [{"source", "name", "deployment"[, "role"],
  "cpu_percent", "memory_used", "memory_limit", "memory_percent", "network_rx", "network_tx",
  "block_read", "block_write", "pids"}]}`: bytes as integers, null where Docker printed `--`
  (a container that stopped since the last resolution). CPU and memory are of the Docker VM.
- `{"event": "disk", "as_of", "stale", "volumes": [{"name", "size"}], "containers": [...]}`.
- `{"event": "end", "reason", "dropped"}` when it stops: stdin ended (the console closes it),
  SIGTERM, SIGHUP or Ctrl-C. `docker stats` exits at once when a listed container is
  already gone (removed between the listing and its start), so a child that ends is
  replaced after a new listing; when RESOLVE_FAILURES children in a row end, or the
  containers cannot be listed RESOLVE_FAILURES times in a row (Docker went away), an
  error object instead (exit 2).

Without `--follow` it prints the first sample and the first disk reading, then exits (or
fails after ONCE_TIMEOUT seconds without them); a stop signal still ends it with the end record.

Stopping follows S-10, as `logs --follow` and `watch`: the child stays in the CLI's process
group and is stopped with its descendants; `docker stats` ignores SIGTERM (Docker 27.5,
2026-11-12) and holds nothing worth a grace period, so a replaced or stopped child is killed at
once (within a second of a stop: the main loop polls at least once a second). Every child
started is kept until it is confirmed stopped, so a Ctrl-C during a replacement still stops
the old one. Not covered: the short-lived `docker ps` and `docker system df` calls on the
worker threads are not waited for at exit; each has its own timeout, but a call in flight
when the CLI exits runs on, in the CLI's process group (as `watch`'s `compose ps` does).
Errors
are `[CODE] message` (with `--json` also the one-line `{"schema_version": 1, "error": {...}}`),
exit 2.
"""

from __future__ import annotations

import json
import queue
import signal
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from agentbreeder import safe_text, usage
from agentbreeder.commands import logs as logs_command
from agentbreeder.commands import watch as watch_command
from agentbreeder.commands.logs import LogsError

RESOLVE = 10.0
DISK = 60.0
RESOLVE_FAILURES = 3  # listings in a row that fail, or stats children in a row that end
ONCE_TIMEOUT = 30.0  # without --follow: seconds to wait for one sample and one disk reading
LINE_LIMIT = 64 * 1024  # a stats record is far smaller; a longer line is dropped
QUEUE_LIMIT = 1000
SAMPLE_EVERY = 2.0  # seconds between samples: Docker refreshes about twice a second
ENDED = logs_command.ENDED


def register(service_commands) -> None:
    command = service_commands.add_parser(
        "usage", help="Show the stack's CPU, memory, network, block IO and disk use"
    )
    command.add_argument("dir", nargs="?", type=Path, default=Path("."))
    command.add_argument(
        "--follow",
        action="store_true",
        help=(
            "Keep printing samples until Ctrl-C, SIGTERM, SIGHUP or the end of a non-terminal "
            "stdin"
        ),
    )
    command.add_argument("--json", action="store_true", help="One JSON object per line")


def run(args) -> int:
    try:
        return _usage(args)
    except LogsError as error:
        print(str(error), file=sys.stderr)
        if args.json:
            message = str(error).removeprefix(f"[{error.code}] ")
            body = {"code": error.code, "message": message}
            print(json.dumps({"schema_version": 1, "error": body}), flush=True)
        return 2


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve(docker, state, read) -> dict[str, dict]:
    from agentbreeder.watch import LABEL_DEPLOYMENT

    deployments = read()
    services = docker.labelled_containers(usage.service_label(state.id), usage.SERVICE_KEYS)
    agents = docker.labelled_containers(LABEL_DEPLOYMENT, usage.AGENT_KEYS)
    return usage.resolve(services, agents, state.id, deployments)


def _resolve_worker(docker, state, read, records) -> None:
    """Always posts a result (None when Docker could not be read)."""
    try:
        found = _resolve(docker, state, read)
    except Exception:  # noqa: BLE001 - Docker unreachable, a malformed row: unknown this time
        found = None
    records.put(("ids", found))


def _disk_worker(docker, state, containers: dict[str, dict], records) -> None:
    try:
        reading = usage.disk(docker.disk_usage(), state.project, state.id, containers)
    except Exception:  # noqa: BLE001 - the cached reading stays, marked stale
        reading = None
    records.put(("disk", reading, _now()))


def _pump(process, generation: int, expected: frozenset[str], records) -> None:
    """Read one stats child's frames; ("end", generation) marks its end."""
    frames = usage.Frames()
    try:
        while True:
            line = process.stdout.readline(LINE_LIMIT)
            if not line:
                break
            if not line.endswith(b"\n"):
                while (rest := process.stdout.readline(LINE_LIMIT)) and not rest.endswith(b"\n"):
                    pass  # an over-long record: dropped whole
                continue
            for frame in frames.feed(line, expected):
                records.offer(("frame", generation, frame))
    except (OSError, ValueError):
        pass
    finally:
        records.put(("end", generation))


def _start(process_argv, environment):
    from agentbreeder.runtime import start_follow

    try:
        return start_follow(process_argv, environment)
    except OSError as error:
        raise LogsError("DOCKER_STATS_FAILED", "docker stats could not start") from error


def _next(due: float, pending: bool, period: float, now: float) -> float:
    """When the loop must next look at a worker: its due time, or for one still running
    past it, one period later (when a replacement starts), so the loop does not spin."""
    return due + period if pending and now >= due else due


def _emit(args, event: dict, text: str) -> None:
    if args.json:
        print(json.dumps(event), flush=True)
    else:
        print(safe_text.visible(text), flush=True)


class _Loop:
    """The main loop's state: the current containers and stats child (its generation), and
    the cached disk reading."""

    def __init__(self, args, docker, state, read, follow, records):
        self.args, self.docker, self.state, self.read = args, docker, state, read
        self.follow, self.records = follow, records
        self.containers: dict[str, dict] | None = None  # None until the first resolution
        self.generation = 0
        self.process = None
        self.failures = 0
        self.restarts = 0  # stats children in a row that ended before or after frames
        self.children: list = []  # every child started and not yet confirmed stopped
        self.disk: dict | None = None
        self.disk_as_of: str | None = None
        self.sampled = False
        self.last_sample: float | None = None
        self.disk_shown = False

    def replace(self, found: dict[str, dict]) -> None:
        """Point the stats child at `found` (none for an empty set). The old child is killed at
        once: `docker stats` holds nothing worth a grace period."""
        from agentbreeder.runtime import stop_follow
        from agentbreeder.services.compose import sanitised_environment

        with self.follow.lock:
            old, self.process = self.process, None
            self.follow.process = None
            self.generation += 1  # the old child's frames and end are ignored from here on
            self.containers = found
            self.last_sample = None  # a new set's first frame is shown at once
        if old is not None:  # first, so a child that fails to start never leaves it running
            stop_follow(old, grace=0)
            self.children.remove(old)  # only once stopped: a Ctrl-C before it is swept
        with self.follow.lock:
            if self.follow.reason is not None:
                return
            if found:
                argv = self.docker._argv(["stats", "--format", "{{json .}}", *sorted(found)])
                # The same scrubbed environment as every other docker call (no DOCKER_HOST or
                # TLS overrides, no keys); `--context` names the engine.
                self.process = _start(argv, sanitised_environment())
                self.children.append(self.process)
                self.follow.process = self.process
                threading.Thread(
                    target=_pump,
                    args=(self.process, self.generation, frozenset(found), self.records),
                    daemon=True,
                ).start()
        if not found:
            self.sample({}, force=True)  # say so once: nothing of this stack is running

    def sample(self, frame: dict[str, dict], force: bool = False) -> None:
        """Print a frame, at most one every SAMPLE_EVERY seconds (`force`: a new container set)."""
        now = time.monotonic()
        if not force and self.last_sample is not None and now - self.last_sample < SAMPLE_EVERY:
            return
        self.last_sample = now
        rows = [
            usage.sample_row(record, self.containers[identifier])
            for identifier, record in sorted(frame.items())
            if self.containers and identifier in self.containers
        ]
        rows.sort(key=lambda r: (r["source"], r["name"], r.get("role") or ""))
        event = {
            "schema_version": usage.SCHEMA_VERSION,
            "event": "sample",
            "time": _now(),
            "containers": rows,
        }
        _emit(self.args, event, usage.sample_text(event))
        self.sampled = True

    def show_disk(self, reading: dict | None, when: str) -> None:
        if reading is not None:
            self.disk, self.disk_as_of = reading, when
        cached = self.disk or {"volumes": [], "containers": []}
        event = {
            "schema_version": usage.SCHEMA_VERSION,
            "event": "disk",
            "as_of": self.disk_as_of,
            "stale": reading is None,
            **cached,
        }
        _emit(self.args, event, usage.disk_text(event))
        self.disk_shown = True


def _usage(args) -> int:
    from agentbreeder.runtime import stop_follow
    from agentbreeder.services.compose import DockerCLI
    from agentbreeder.services.schema import ServiceError

    state = watch_command._open_stack(args.dir)
    read = watch_command._reader(state)
    docker = DockerCLI()
    try:
        docker.authorize()
    except ServiceError as error:
        state.close()
        raise LogsError(error.code, watch_command._message(error)) from error
    follow = logs_command._Follow()
    previous = logs_command._handlers(follow)
    records = watch_command._Records()
    loop = _Loop(args, docker, state, read, follow, records)
    failed: str | None = None
    try:
        try:  # entered at once: a Ctrl-C from here on still stops the child
            descriptor = logs_command._stdin_descriptor()
            if args.follow and descriptor is not None:
                threading.Thread(
                    target=logs_command._watch_stdin, args=(follow, descriptor), daemon=True
                ).start()
            resolving = disking = True
            threading.Thread(
                target=_resolve_worker, args=(docker, state, read, records), daemon=True
            ).start()
            resolve_due = time.monotonic() + RESOLVE
            disk_due = None  # the first disk reading starts with the first resolution
            deadline = None if args.follow else time.monotonic() + ONCE_TIMEOUT
            while follow.reason is None:
                now = time.monotonic()
                # A pending worker past its due time is next looked at a period later.
                dues = [_next(resolve_due, resolving, RESOLVE, now)]
                if disk_due is not None:
                    dues.append(_next(disk_due, disking, DISK, now))
                if deadline is not None:
                    if now >= deadline:
                        failed = f"no reading within {ONCE_TIMEOUT:.0f} s"
                        break
                    dues.append(deadline)
                wait = max(0.05, min(1.0, min(dues) - now))
                try:
                    item = records.queue.get(timeout=wait)
                except queue.Empty:
                    item = ("idle",)
                kind = item[0]
                if kind == "ids":
                    resolving = False
                    found = item[1]
                    if found is None:
                        loop.failures += 1
                        if loop.failures >= RESOLVE_FAILURES or not args.follow:
                            failed = "the stack's containers could not be listed"
                            break
                    else:
                        loop.failures = 0
                        if loop.containers is None or set(found) != set(loop.containers):
                            loop.replace(found)
                        else:
                            loop.containers = found  # a renamed deployment keeps its child
                    if disk_due is None and loop.containers is not None:
                        threading.Thread(
                            target=_disk_worker,
                            args=(docker, state, dict(loop.containers), records),
                            daemon=True,
                        ).start()
                        disk_due = time.monotonic() + DISK
                elif kind == "frame" and item[1] == loop.generation:
                    loop.restarts = 0
                    loop.sample(item[2])
                elif kind == "end" and item[1] == loop.generation:
                    if follow.reason is not None:
                        break
                    stopped = logs_command.stop_reason(logs_command.exit_code(loop.process))
                    if stopped is not None:  # the group's signal reached `docker stats` first
                        follow.stop(stopped)
                        break
                    # `docker stats` exits at once when a listed container is already gone
                    # (removed between the listing and the start; Docker 27.5): list again
                    # now and start a new child, even for the same set.
                    loop.restarts += 1
                    if loop.restarts >= RESOLVE_FAILURES:
                        failed = "docker stats ended unexpectedly"
                        break
                    loop.containers = None
                    resolve_due = time.monotonic()
                elif kind == "disk":
                    disking = False
                    loop.show_disk(item[1], item[2])
                if not args.follow and loop.sampled and loop.disk_shown:
                    follow.stop("once")
                    break
                # A worker still pending after two periods (a hung docker) does not hold up
                # the next one.
                now = time.monotonic()
                if now >= resolve_due and (not resolving or now >= resolve_due + RESOLVE):
                    resolving = True
                    threading.Thread(
                        target=_resolve_worker, args=(docker, state, read, records), daemon=True
                    ).start()
                    resolve_due = now + RESOLVE
                if disk_due is not None and now >= disk_due:
                    if not disking or now >= disk_due + DISK:
                        disking = True
                        threading.Thread(
                            target=_disk_worker,
                            args=(docker, state, dict(loop.containers or {}), records),
                            daemon=True,
                        ).start()
                        disk_due = now + DISK
        except KeyboardInterrupt:
            follow.stop("interrupt")
        except BrokenPipeError:  # `usage --follow | head`: the reader left; stop quietly
            follow.stop("pipe")
            watch_command._quiet_stdout()
        finally:
            with follow.lock:
                children = list(loop.children)
            for process in children:  # the current child and any not yet confirmed stopped
                try:
                    stop_follow(process, grace=0)  # `docker stats` ignores SIGTERM
                except KeyboardInterrupt:
                    follow.stop("interrupt")
                    stop_follow(process, grace=0)
    finally:
        follow.cancel()
        for number, handler in previous.items():
            signal.signal(number, handler)
        state.close()
    if failed is not None:
        raise LogsError("DOCKER_STATS_FAILED", failed)
    reason = follow.reason
    if reason in ("pipe", "once"):
        return 0
    try:
        if records.dropped:
            print(
                f"Note: {records.dropped} records were dropped (too many at once)", file=sys.stderr
            )
        print(f"Usage ended: {ENDED[reason]}", file=sys.stderr)
        if args.json:
            end = {
                "schema_version": usage.SCHEMA_VERSION,
                "event": "end",
                "reason": reason,
                "dropped": records.dropped,
            }
            print(json.dumps(end), flush=True)
    except BrokenPipeError:
        watch_command._quiet_stdout()
    return 0
