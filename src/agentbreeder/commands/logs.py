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
stdin. The `docker logs` child is stopped with it, and it stays in the CLI's process group, so a
terminal hangup or a supervisor's group kill reaches it too (S-10); only a SIGKILL of the CLI
alone leaves it running until its next write. Run with stdin from `/dev/null`, a follow ends at
once. A line over 1 MiB is dropped whole, never printed in part. Docker's 16 KiB partial records
of a long line are joined before redaction (`runtime.join_partials`).

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
        "--raw",
        action="store_true",
        help="Print control characters as they are (unsafe; --json always escapes)",
    )
    cli.add_stack(logs, environment=True)


def _fail(args, error: LogsError) -> int:
    print(str(error), file=sys.stderr)
    if args.json:
        message = str(error).removeprefix(f"[{error.code}] ")
        body = {"code": error.code, "message": message}
        # One line under --follow, so an NDJSON reader can parse every line.
        indent = None if getattr(args, "follow", False) else 2
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": body}, indent=indent))
    return 2


def run(args) -> int:
    try:
        if (args.id is None) == (args.failed is None):
            raise LogsError("USAGE", "give a deployment ID or --failed NAME (one of them)")
        if args.failed is not None:
            if args.tail is not None or args.since is not None or args.follow:
                raise LogsError("USAGE", "--tail, --since and --follow go with a deployment ID")
            return _failed(args)
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
        if target != "local":
            raise LogsError(
                "NOT_LOCAL",
                f"{args.id} runs on {safe_text.excerpt(target, 40)}; `logs` reads local "
                "deployments only in this version",
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
    runtime = _runtime()
    try:
        runtime.authorize(record["context"])
    except DeploymentError as error:
        raise LogsError("DOCKER_UNAVAILABLE", str(error)) from error
    try:
        info = runtime.inspect(record)
    except DeploymentError as error:
        raise LogsError(
            "CONTAINER_MISMATCH", f"the container of {args.id} is not the recorded one: {error}"
        ) from error
    if info is None:
        raise LogsError("CONTAINER_GONE", f"the container of {args.id} no longer exists")
    return record, runtime, info, redaction.Redactor(found.values)


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


def _deployment(args) -> int:
    from agentbreeder.store import DeploymentError

    record, runtime, info, redact = _prepare(args)
    tail = args.tail or DEFAULT_TAIL
    if args.follow:
        return _follow(args, record, runtime, info, redact, tail)
    try:
        text, truncated = runtime.logs(info, tail, args.since)
    except DeploymentError as error:
        raise LogsError("DOCKER_LOGS_FAILED", str(error)) from error
    lines = _lines(redact(text))
    if args.json:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "source": "agent",
                    "deployment": args.id,
                    "name": record["name"],
                    "lines": lines,
                    "truncated": truncated,
                },
                indent=2,
            )
        )
        return 0
    for line in lines:
        sys.stdout.write(_shown(line, args.raw) + "\n")
    if truncated:
        print("Note: older lines were dropped (4 MiB cap); use --tail or --since", file=sys.stderr)
    return 0


STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


class _Follow:
    """The follow child and how it is stopped: by the end of stdin (a watcher thread), by
    SIGTERM/SIGHUP (handlers that only record the reason and stop the child), by Ctrl-C or by
    a closed stdout. The lock is re-entrant because a signal handler runs on the main thread,
    possibly while it holds the lock to start the child; the child is signalled through Popen
    only while it is unreaped. The first reason wins."""

    def __init__(self) -> None:
        self.process = None
        self.reason: str | None = None
        self.lock = threading.RLock()

    def stop(self, reason: str) -> None:
        with self.lock:
            self.reason = self.reason or reason
            process = self.process
            if process is not None and process.poll() is None:
                try:
                    process.terminate()  # its stdout ends, so the read loop ends
                except OSError:
                    pass


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


def _follow(args, record, runtime, info, redact, tail: int) -> int:
    """Stream until stopped. With --json each line is
    `{"schema_version", "source": "agent", "name", "deployment", "line"}` and the stream ends
    with `{"schema_version", "event": "end", "reason", "dropped"}` (reason: container, stdin,
    signal or interrupt), or with an error object when `docker logs` itself failed."""
    from agentbreeder.runtime import join_partials, stop_follow
    from agentbreeder.store import DeploymentError

    follow = _Follow()
    previous = _handlers(follow)
    dropped = False
    process = None
    try:
        with follow.lock:
            if follow.reason is None:  # a stop signal may arrive before the child starts
                try:
                    follow.process = process = runtime.follow(info, tail, args.since)
                except DeploymentError as error:
                    raise LogsError("DOCKER_LOGS_FAILED", str(error)) from error
                if follow.reason is not None:  # a stop signal came while the child was starting
                    follow.stop(follow.reason)
        if process is not None:
            descriptor = _stdin_descriptor()
            if descriptor is not None:
                threading.Thread(
                    target=_watch_stdin, args=(follow, descriptor), daemon=True
                ).start()
            try:
                while True:
                    line = process.stdout.readline(LINE_LIMIT)
                    if not line:
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
                    if args.json:
                        entry = {
                            "schema_version": SCHEMA_VERSION,
                            "source": "agent",
                            "name": record["name"],
                            "deployment": args.id,
                            "line": text,
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
        for number, handler in previous.items():
            signal.signal(number, handler)
    reason = follow.reason
    if reason == "pipe":
        return 0
    if reason is None:
        returncode = process.returncode if process is not None else 0
        if returncode == -signal.SIGINT:  # the terminal's Ctrl-C reached the child first
            reason = "interrupt"
        elif returncode in (-signal.SIGTERM, -signal.SIGHUP):  # a group signal reached it first
            reason = "signal"
        elif returncode not in (0, None):
            raise LogsError(
                "DOCKER_LOGS_FAILED", f"`docker logs` ended with exit code {returncode}"
            )
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
