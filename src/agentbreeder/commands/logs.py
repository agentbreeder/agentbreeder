"""`agentbreeder logs ID | --failed NAME`: a local deployment's container logs, or the saved tail
of the last failed release candidate.

`logs ID [--tail N] [--since D] [--follow] [--raw] [--json]` (M5, guided §14.3): the deployment
must be a local one in this state (opened read-only). Docker is asked for its container by the
recorded name and the ownership labels are checked (`Docker.inspect`), then `docker container
logs` reads it by ID, stdout and stderr merged, timestamped (`Docker.logs`, 4 MiB cap; or
`Docker.follow`). Every line is redacted before it is printed, with the values that
`redaction.collect` finds (the stack's stored secrets, every non-removed deployment's secret
copies, `<state>/secrets`); if any of them cannot be read nothing is printed
(REDACTION_INCOMPLETE). Logs can still hold customer messages and model output.

Controls in the text (ESC, CSI and OSC sequences, C1, bidi, lone surrogates) are shown as
visible escapes unless `--raw` (security finding S-08); `--json` escapes by construction.

`--follow` streams until the container stops, Ctrl-C, SIGTERM, SIGHUP or, when stdin is not a
terminal, the end of stdin: a supervising process (the console) stops a follow by closing its
stdin. The reader (`docker logs`, `kubectl logs`, `docker compose logs`) stays in the CLI's
process group, so a terminal hangup or a group kill of the CLI (the console's S-10 sweep)
reaches it too. The CLI's handlers stop the reader and its descendants (compose's plugin,
which ignored one SIGTERM while starting): SIGTERM, then SIGKILL after 2 s. A SIGKILL of the
CLI process alone leaves the reader until its next write. Run with stdin from `/dev/null`, a
follow ends at once. A line over 1 MiB is dropped whole, never printed in part. Docker's 16 KiB
partial records of a long line are joined before redaction (`runtime.join_partials`).

`--level warning|error` keeps only the records at that level or above (`loglevels.py`, a
heuristic). An agent's format is its runtime's: with `--stack DIR`, `DIR/deploy/NAME.build.json`
names it (`runtime/adk`); without one, or for any other image, `--level` fails with
LEVELS_UNAVAILABLE. `--json` always carries `format` and `levels` (one per line, null when
unclassified), and under `--follow` each line's `level`.

`logs --failed NAME` reads only the file `release` saved (no Docker, no state database).

Exit 0 when the logs were read; 1 for `--failed` when there is no saved tail; 2 with
`[CODE] message` when nothing was read or `docker logs` failed (with `--json` also
`{"schema_version": 1, "error": {"code", "message"}}` on stdout, on one line under `--follow`).
"""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
from pathlib import Path

from agentbreeder import cli, safe_text
from agentbreeder.agentfile import NAME_PATTERN

NAMES = ("logs",)
READ_LIMIT = 8 * 1024 * 1024
SCHEMA_VERSION = 1
DEFAULT_TAIL = 200
LINE_LIMIT = 1024 * 1024
DEPLOYMENT_ID = re.compile(r"[0-9a-f]{32}")


class LogsError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


def _tail(text: str) -> int:
    from agentbreeder.runtime import MAX_TAIL

    try:
        value = int(text)
    except ValueError:
        value = 0
    if not 1 <= value <= MAX_TAIL:
        import argparse

        raise argparse.ArgumentTypeError(f"must be 1 to {MAX_TAIL}")
    return value


def register(commands) -> None:
    logs = cli.stateful_parser(
        commands,
        "logs",
        "Show a local deployment's logs (redacted), or the saved tail of a failed candidate",
    )
    logs.add_argument(
        "id", nargs="?", metavar="ID", help="Deployment ID (from `list`): its container's logs"
    )
    logs.add_argument(
        "--failed",
        metavar="NAME",
        help="Agent name: show its last failed release candidate's log tail (redacted)",
    )
    logs.add_argument(
        "--tail",
        type=_tail,
        default=None,
        metavar="N",
        help=f"With ID: the last N lines (default {DEFAULT_TAIL}, at most 10000)",
    )
    logs.add_argument(
        "--since", metavar="D", help="With ID: only lines since D (10m, 2h, 2026-11-11T09:00:00Z)"
    )
    logs.add_argument(
        "--follow",
        action="store_true",
        help=(
            "With ID: keep printing new lines until the container stops, Ctrl-C, SIGTERM, "
            "SIGHUP or the end of a non-terminal stdin"
        ),
    )
    logs.add_argument(
        "--previous",
        action="store_true",
        help="With a Kubernetes ID: the last restarted container's output (a crash loop)",
    )
    add_level(logs, "With ID: only")
    logs.add_argument(
        "--raw",
        action="store_true",
        help="Print control characters as they are (unsafe; --json always escapes)",
    )
    cli.add_stack(logs, environment=True)


def add_level(command, prefix: str = "Only") -> None:
    from agentbreeder import loglevels

    command.add_argument(
        "--level",
        choices=loglevels.CHOICES,
        help=(
            f"{prefix} records at this level or above, for a known log format "
            "(a heuristic; continuation lines stay with their record)"
        ),
    )


def _fail(args, error) -> int:
    """A LogsError or ServiceError through `cli.fail`: one `[CODE] message` line on stderr
    (control characters and `\\n` escaped, so a multi-line Docker or kubectl message cannot
    forge a later `[CODE]` line) and, with `--json`, the error object (one line under
    `--follow`)."""
    return cli.fail(args, error.code, str(error))


def run(args) -> int:
    try:
        if (args.id is None) == (args.failed is None):
            raise LogsError("USAGE", "give a deployment ID or --failed NAME (one of them)")
        if args.failed is not None:
            if (
                args.tail is not None
                or args.since is not None
                or args.follow
                or args.previous
                or args.level is not None
            ):
                raise LogsError(
                    "USAGE",
                    "--tail, --since, --follow, --previous and --level go with a deployment ID",
                )
            return _failed(args)
        if args.previous and args.follow:
            raise LogsError("USAGE", "--previous shows a finished container; it cannot --follow")
        return _deployment(args)
    except LogsError as error:
        return _fail(args, error)


# -- logs --failed NAME --------------------------------------------------------------------------


def _read(path: Path) -> str | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"{path} must be a regular file") from error
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError(f"{path} must be a 0600 file you own")
        return handle.read(READ_LIMIT).decode("utf-8", "replace")


def _failed(args) -> int:
    from agentbreeder.release import candidate_tail_path

    name = args.failed
    if not re.fullmatch(NAME_PATTERN, name):
        raise LogsError("AGENT_NAME", "NAME must be an agent name from agent.yaml")
    path = candidate_tail_path(args.state_dir.absolute(), name)
    try:
        text = _read(path)
    except ValueError as error:
        raise LogsError("STATE_UNSAFE", str(error)) from error
    if text is None:
        print(
            f"No failed-candidate log for {name} in {args.state_dir}: none failed yet, or the "
            "last release passed (a pass discards it)",
            file=sys.stderr,
        )
        return 1
    if args.json:
        body = {
            "schema_version": SCHEMA_VERSION,
            "agent": name,
            "path": str(path),
            "lines": _lines(text),
        }
        print(json.dumps(body, indent=2))
    elif args.raw:
        sys.stdout.write(text)
    else:
        sys.stdout.write(safe_text.visible(text))
    return 0


# -- logs ID -------------------------------------------------------------------------------------


def _runtime():
    from agentbreeder.runtime import Docker

    return Docker()


def _open(state_dir: Path):
    import sqlite3

    from agentbreeder.store import DeploymentError, StateBusy, StateNeedsMigration, Store

    if not (state_dir / "deployments.sqlite3").is_file():
        raise LogsError(
            "NO_STATE",
            f"no AgentBreeder state at {safe_text.excerpt(state_dir, 300)}; check --state-dir "
            "or --stack",
        )
    try:
        return Store.open_readonly(state_dir)
    except StateNeedsMigration as error:
        code, message = "STATE_NEEDS_MIGRATION", str(error)
    except StateBusy as error:
        code, message = "STATE_BUSY", str(error)
    except DeploymentError as error:
        code, message = "STATE_UNREADABLE", str(error)
    except (OSError, sqlite3.Error):
        code, message = "STATE_UNREADABLE", "cannot read the local state"
    raise LogsError(code, safe_text.excerpt(message, 400))


def _prepare(args):
    """(record, verified container info, redactor) for `args.id`, or LogsError."""
    import sqlite3

    from agentbreeder import redaction
    from agentbreeder.runtime import SINCE
    from agentbreeder.store import DeploymentError

    if not DEPLOYMENT_ID.fullmatch(args.id):
        raise LogsError(
            "ID_INVALID", "ID must be a deployment ID (32 hex characters, from `list`)"
        )
    if args.since is not None and not SINCE.fullmatch(args.since):
        raise LogsError("SINCE_INVALID", "--since must look like 10m, 2h or 2026-11-11T09:00:00Z")
    store = _open(args.state_dir)
    try:
        try:
            record = store.get(args.id)
            manifest = store.manifest(args.id)
            target = manifest.get("target", "local")
        except DeploymentError as error:
            raise LogsError("NOT_FOUND", f"no deployment {args.id} in this state") from error
        except sqlite3.Error as error:
            raise LogsError("STATE_UNREADABLE", "cannot read the local state") from error
        if target not in TARGETS:
            raise LogsError(
                "TARGET_UNSUPPORTED",
                f"{args.id} runs on {safe_text.excerpt(target, 40)}; `logs` reads local and "
                "Kubernetes deployments",
            )
        if args.previous and target != "kubernetes":
            raise LogsError(
                "USAGE", "--previous is for a Kubernetes deployment (a restarted container)"
            )
        if record["state"] == "removed":
            raise LogsError("REMOVED", f"{args.id} was removed; its container is gone")
        found = redaction.collect(args.state_dir, store=store)
        redaction.deployment_copies(args.state_dir.absolute(), args.id, found)
        copies = args.state_dir.absolute() / "deployments" / args.id / "secrets"
        if _has_copies(manifest) and not copies.is_dir():
            # A failed deploy deletes them but keeps the container: values only they held (the
            # minted gateway key, MCP tokens, a rotated secret) could not be redacted.
            found.problems.append(
                "this deployment's secret copies are gone (a failed deploy deletes them); for "
                "a release candidate use `logs --failed NAME`"
            )
    finally:
        store.close()
    if found.problems:
        raise LogsError(
            "REDACTION_INCOMPLETE",
            "nothing printed, because these values could not be read to redact the logs: "
            + safe_text.excerpt("; ".join(sorted(set(found.problems))), 400),
        )
    codes = TARGETS[target]
    runtime = _kubernetes() if target == "kubernetes" else _runtime()
    try:
        runtime.authorize(record["context"])
    except DeploymentError as error:
        # Kubernetes messages name the recorded namespace: excerpt, as other state-derived text.
        raise LogsError(codes["unavailable"], safe_text.excerpt(str(error), 400)) from error
    try:
        info = runtime.inspect(record)
    except DeploymentError as error:
        raise LogsError(
            "CONTAINER_MISMATCH", f"the container of {args.id} is not the recorded one: {error}"
        ) from error
    if info is None:
        raise LogsError("CONTAINER_GONE", f"the container of {args.id} no longer exists")
    return record, target, runtime, info, redaction.Redactor(found.values)


# Per target: the error codes for an unreachable engine and a failing log read.
TARGETS = {
    "local": {"unavailable": "DOCKER_UNAVAILABLE", "failed": "DOCKER_LOGS_FAILED"},
    "kubernetes": {"unavailable": "CLUSTER_UNAVAILABLE", "failed": "KUBECTL_LOGS_FAILED"},
}


def _kubernetes():
    from agentbreeder.kubernetes import Kubernetes

    return Kubernetes()


def _has_copies(manifest: dict) -> bool:
    """Whether a deploy of this manifest writes per-deployment secret copies: mounted secrets,
    a minted gateway key (`model_access`) or MCP relay tokens (`mcp_access`)."""
    return bool(
        manifest.get("secrets") or manifest.get("model_access") or manifest.get("mcp_access")
    )


def _lines(text: str) -> list[str]:
    """Split on newlines only (`splitlines` also breaks on \\r, \\x0b, \\x1c and U+2028,
    which would forge lines without a timestamp; those are escaped instead)."""
    lines = text.split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines


def _shown(line: str, raw: bool) -> str:
    return line if raw else safe_text.visible(line)


def agent_format(stack: Path | None, name: str) -> str | None:
    """The log format of deployment `name` (an agent or its `-candidate`): the runtime its
    build record names, read from `STACK/deploy/AGENT.build.json` (bounded, no symlink), when
    the record's locked refs say that runtime came from the built-in registry."""
    from agentbreeder import guide_checks, loglevels

    if stack is None:
        return None
    agent = name.removesuffix("-candidate")
    if not re.fullmatch(NAME_PATTERN, agent):
        return None
    record = guide_checks._read_json(stack / "deploy" / f"{agent}.build.json")
    if record is None or record.get("name") != agent or not isinstance(record.get("runtime"), str):
        return None
    refs = record.get("refs")
    builtin = isinstance(refs, list) and any(
        isinstance(entry, dict)
        and entry.get("ref") == record["runtime"]
        and entry.get("source") == "builtin"
        for entry in refs
    )
    return loglevels.format_for(record["runtime"], builtin=builtin)


def check_level(args, fmt: str | None, what: str) -> None:
    if getattr(args, "level", None) is not None and fmt is None:
        raise LogsError(
            "LEVELS_UNAVAILABLE",
            f"--level needs a known log format and {what} has none (Postgres, LiteLLM, Phoenix "
            "and runtime/adk are known)",
        )


def leveled(lines: list[str], fmt: str | None, threshold: str | None):
    """(lines, their levels), keeping only the records `--level` shows."""
    from agentbreeder import loglevels

    levels = loglevels.classify(fmt, lines)
    kept = [
        (ln, lv) for ln, lv in zip(lines, levels, strict=True) if loglevels.shown(lv, threshold)
    ]
    return [ln for ln, _ in kept], [lv for _, lv in kept]


def _deployment(args) -> int:
    from agentbreeder.store import DeploymentError

    record, target, runtime, info, redact = _prepare(args)
    fmt = agent_format(getattr(args, "stack", None), record["name"])
    check_level(
        args, fmt, "this deployment (pass --stack DIR so its build record names the runtime)"
    )
    codes = TARGETS[target]
    tail = args.tail or DEFAULT_TAIL
    pod = {"name": None, "reason": None, "readable": True}
    if target == "kubernetes":
        # One pod owned by the verified Deployment (never a stray pod carrying the label).
        try:
            pod = runtime.pod(info)
        except DeploymentError as error:
            raise LogsError(codes["unavailable"], "cannot read the pod's status") from error
    readable = pod["readable"] or (args.previous and pod["name"] is not None)
    if args.follow:
        if not pod["readable"]:
            raise LogsError(
                "POD_NOT_RUNNING",
                f"the pod of {args.id} has no output yet ({pod['reason']}); nothing to follow",
            )
        options = {"pod": pod["name"]} if target == "kubernetes" else {}
        return _follow(
            args, record, runtime, info, redact, tail, codes["failed"], options, target, fmt=fmt
        )
    text, truncated = "", False
    if readable:
        from agentbreeder.kubernetes import LogUnavailable

        options = {"pod": pod["name"], "previous": args.previous} if target == "kubernetes" else {}
        try:
            text, truncated = runtime.logs(info, tail, args.since, **options)
        except LogUnavailable as error:
            raise LogsError("LOG_UNAVAILABLE", str(error)) from error
        except DeploymentError as error:
            raise LogsError(codes["failed"], str(error)) from error
    lines, levels = leveled(_lines(redact(text)), fmt, args.level)
    if args.json:
        body = {
            "schema_version": SCHEMA_VERSION,
            "source": "agent",
            "target": target,
            "deployment": args.id,
            "name": record["name"],
            "lines": lines,
            "levels": levels,
            "format": fmt,
            "level": args.level,
            "truncated": truncated,
            "previous": bool(args.previous),
            "pod_reason": pod["reason"],
        }
        print(json.dumps(body, indent=2))
        return 0
    for line in lines:
        sys.stdout.write(_shown(line, args.raw) + "\n")
    if not readable:
        print(
            f"Note: the pod has no output yet ({pod['reason']})",
            file=sys.stderr,
        )
    elif pod["reason"] is not None:
        print(
            f"Note: the pod is not running ({pod['reason']}); these are its last lines, and "
            "`--previous` shows the container before the last restart",
            file=sys.stderr,
        )
    if truncated:
        print("Note: older lines were dropped (4 MiB cap); use --tail or --since", file=sys.stderr)
    return 0


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


def stop_reason(returncode: int | None) -> str | None:
    """The stop a reader's exit code shows, when a signal for the whole group (the terminal's
    Ctrl-C, a supervisor's SIGTERM/SIGHUP) ended it before the CLI's own handler recorded a
    reason (a fallback: the handler usually wins): "interrupt", "signal", or None.

    A reader killed by the signal reports -N; one that catches it may exit 128 + N (#863
    review). Observed on Docker 27.5 (2026-11-13): `docker compose logs -f` exits 130 after
    SIGINT and after SIGTERM (so a SIGTERM reads as "interrupt" here), and -1 after SIGHUP;
    `docker logs -f` and `docker events` exit 1 after SIGINT or SIGTERM, which cannot be told
    from a failure and stays one; `docker stats` ignores both. kubectl was not checked."""
    if returncode in (-signal.SIGINT, 128 + signal.SIGINT):
        return "interrupt"
    if returncode in {code for number in STOP_SIGNALS for code in (-number, 128 + number)}:
        return "signal"
    return None


def exit_code(process, wait: float = 1.0) -> int | None:
    """A reader's own exit code once its output ended, read BEFORE the CLI stops it (so the
    CLI's own SIGTERM is never mistaken for the reader's); None if it has not exited within
    `wait` seconds."""
    if process is None:
        return None
    try:
        return process.wait(wait)
    except subprocess.TimeoutExpired:
        return None


class _Follow:
    """The follow child and how it is stopped: by the end of stdin (a watcher thread), by
    SIGTERM/SIGHUP (handlers that only record the reason and stop the child), by Ctrl-C or by
    a closed stdout. The lock is re-entrant because a signal handler runs on the main thread,
    possibly while it holds the lock to start the child. A stop signals the child and its
    descendants (`runtime.signal_follow`) and arms a SIGKILL after STOP_GRACE seconds, so a
    reader that ignores SIGTERM (docker while its compose plugin starts) cannot keep the read
    loop waiting. The first reason wins."""

    def __init__(self) -> None:
        self.process = None
        self.reason: str | None = None
        self.lock = threading.RLock()
        self.timer: threading.Timer | None = None
        self.finished = False  # set once the child was stopped and waited for

    def stop(self, reason: str) -> None:
        from agentbreeder.runtime import signal_follow

        with self.lock:
            self.reason = self.reason or reason
            process = self.process
            if process is None or self.finished:  # e.g. stdin ending after the follow ended
                return
            signal_follow(process, signal.SIGTERM)  # its stdout ends, so the read loop ends
            if self.timer is None:
                self.timer = threading.Timer(
                    STOP_GRACE, signal_follow, args=(process, signal.SIGKILL)
                )
                self.timer.daemon = True
                self.timer.start()

    def cancel(self) -> None:
        """The follow ended: no later stop may signal or arm a timer."""
        with self.lock:
            self.finished = True
            if self.timer is not None:
                self.timer.cancel()


STOP_GRACE = 2.0


def _watch_stdin(follow: _Follow, descriptor: int) -> None:
    """When stdin is not a terminal, its end stops the follow (the console closes it). Reads
    the raw descriptor: a thread blocked in the buffered reader would hold its lock and abort
    the interpreter at exit."""
    try:
        while os.read(descriptor, 4096):
            pass
    except OSError:
        pass
    follow.stop("stdin")


def _stdin_descriptor() -> int | None:
    """stdin's descriptor when it is not a terminal (watched for its end), else None."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        return sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        return None


def _handlers(follow: _Follow) -> dict:
    """SIGTERM and SIGHUP stop the follow; only the main thread may install handlers (an
    in-process caller on another thread keeps the defaults)."""
    if threading.current_thread() is not threading.main_thread():
        return {}
    return {
        number: signal.signal(number, lambda n, f: follow.stop("signal"))
        for number in STOP_SIGNALS
    }


ENDED = {
    "container": "the container stopped or was removed",
    "stdin": "stdin ended",
    "signal": "stopped by a signal",
    "interrupt": "interrupted",
}


def _follow(
    args,
    record,
    runtime,
    info,
    redact,
    tail: int,
    failed: str = "DOCKER_LOGS_FAILED",
    options=None,
    target: str = "local",
    label: dict | None = None,
    fmt: str | None = None,
) -> int:
    """Stream until stopped. With --json each line is `{"schema_version", **label, "line"}`
    (for a deployment, `label` is `{"source": "agent", "target", "name", "deployment"}`; for a
    stack service, `{"source": "service", "target", "name"}`) and the stream ends with
    `{"schema_version", "event": "end", "reason", "dropped"}` (reason: container, stdin, signal
    or interrupt), or with an error object when the log reader itself failed. `runtime.follow`
    must raise DeploymentError for a reader that cannot start. Each line is classified as it
    arrives (`fmt`); `--level` drops the records below it, and `--json` carries `level`."""
    from agentbreeder import loglevels
    from agentbreeder.runtime import join_partials, stop_follow
    from agentbreeder.store import DeploymentError

    classifier = loglevels.Classifier(fmt)
    if label is None:
        label = {"source": "agent", "target": target, "name": record["name"]}
        label["deployment"] = args.id
    follow = _Follow()
    previous = _handlers(follow)
    dropped = False
    process = None
    ended = None  # the reader's own exit code, read before we stop it
    try:
        with follow.lock:
            if follow.reason is None:  # a stop signal may arrive before the child starts
                try:
                    follow.process = process = runtime.follow(
                        info, tail, args.since, **(options or {})
                    )
                except DeploymentError as error:
                    raise LogsError(failed, str(error)) from error
                if follow.reason is not None:  # a stop signal came while the child was starting
                    follow.stop(follow.reason)
        if process is not None:
            try:  # entered at once: a Ctrl-C from here on still stops the reader's group
                descriptor = _stdin_descriptor()
                if descriptor is not None:
                    threading.Thread(
                        target=_watch_stdin, args=(follow, descriptor), daemon=True
                    ).start()
                while True:
                    line = process.stdout.readline(LINE_LIMIT)
                    if not line:
                        if follow.reason is None:  # it ended by itself (or a group signal)
                            ended = exit_code(process)
                        break
                    if not line.endswith(b"\n") and len(line) >= LINE_LIMIT:
                        while (rest := process.stdout.readline(LINE_LIMIT)) and not rest.endswith(
                            b"\n"
                        ):
                            pass  # the rest of an over-long line: dropped whole
                        dropped = True
                        continue
                    joined = join_partials(line.rstrip(b"\n"))
                    text = redact(joined.decode("utf-8", "replace"))
                    level = classifier.feed(text)
                    if not loglevels.shown(level, getattr(args, "level", None)):
                        continue
                    if args.json:
                        entry = {
                            "schema_version": SCHEMA_VERSION,
                            **label,
                            "line": text,
                            "level": level,
                        }
                        print(json.dumps(entry), flush=True)
                    else:
                        print(_shown(text, args.raw), flush=True)
            except KeyboardInterrupt:
                follow.stop("interrupt")
            except BrokenPipeError:  # `logs ID --follow | head`: the reader left; stop quietly
                follow.stop("pipe")
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, sys.stdout.fileno())
                os.close(devnull)
            finally:
                try:
                    stop_follow(process)
                except KeyboardInterrupt:  # a second Ctrl-C while stopping: finish stopping
                    follow.stop("interrupt")
                    stop_follow(process, grace=0)
    finally:
        follow.cancel()  # the group is gone (or was waited for); never fire on a reused id
        for number, handler in previous.items():
            signal.signal(number, handler)
    reason = follow.reason
    if reason == "pipe":
        return 0
    if reason is None:
        returncode = ended if ended is not None else (process.returncode if process else 0)
        stopped = stop_reason(ended)  # the signal reached the reader first (never our SIGTERM)
        if stopped is not None:
            reason = stopped
        elif returncode not in (0, None):
            raise LogsError(failed, f"the log stream ended with exit code {returncode}")
        else:
            reason = "container"
    try:
        if dropped:
            print("Note: lines over 1 MiB were dropped", file=sys.stderr)
        print(f"Follow ended: {ENDED[reason]}", file=sys.stderr)
        if args.json:
            end = {
                "schema_version": SCHEMA_VERSION,
                "event": "end",
                "reason": reason,
                "dropped": dropped,
            }
            print(json.dumps(end), flush=True)
    except BrokenPipeError:  # the reader left just as the stream ended
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    return 0
