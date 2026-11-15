"""Log follows for the console (#821, M5 console half, part 2; console §8.1, S-10).

A tab that follows a log starts one `services logs DIR NAME --follow --json` or `logs ID --stack
DIR --environment ENV --follow --json` child (`POST .../logs/KIND/NAME/follow`), then polls
`GET /api/follows/ID?after=SEQ` for the lines printed since its last poll. Each follow belongs to
the session that started it: another session gets NOT_FOUND for it. At most MAX_PER_SESSION run
per session, on top of the Runner's MAX_FOLLOWS across the console (shared with the usage
children). A follow is stopped IDLE_AFTER seconds after its last poll (the tab closed, or the
browser stopped its timers), on `POST /api/follows/ID/stop`, and on shutdown (`Runner.shutdown`).

The child plumbing (stdin a pipe, its own process group, the 0600 PID file, the line cap) is
`Runner.follow` in `jobs.py`, as for usage. The CLI redacts every line and fails closed; each
record is checked against its contract (`docs/commands/logs.md`) and only `line` and `level` pass.
Lines are held in memory only, in a ring bounded by count and by bytes; a tab that polls too
slowly is told how many lines it missed. Nothing is written to disk or the database.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from agentbreeder.center.jobs import Follow, FollowRefused, Runner, error

MAX_PER_SESSION = 8  # S-10: follows per signed-in session (the Runner caps 16 overall)
IDLE_AFTER = 15.0  # seconds after the last poll before the child is stopped
FORGET_AFTER = 60.0  # seconds an ended follow's last lines and reason stay readable
MAX_LINE = 1_200_000  # one JSON record: the CLI drops a log line over 1 MiB itself
RING_LINES = 5000  # lines held per follow for the tab's next poll
RING_BYTES = 4 * 1024 * 1024  # and their size, as the 4 MiB cap of a snapshot read
READ_LINES = 2000  # lines per poll; `more` says to poll again at once
READ_BYTES = 1024 * 1024
REAP_EVERY = 1.0
SCHEMA_VERSION = 1
LEVELS = (None, "debug", "info", "warning", "error")
REASONS = ("container", "stdin", "signal", "interrupt")


class FollowNotFound(Exception):
    """No follow with that ID for this session (never started, another session's, or forgotten)."""


@dataclass
class LogFollow:
    """One tab's follow of one log and the lines it has printed."""

    session: bytes
    source: str  # "service" or "deployment"
    name: str  # the service name or the deployment ID
    lines: deque = field(default_factory=deque)  # (seq, line, level, size)
    size: int = 0  # bytes held in `lines`
    seq: int = 0  # the last line's sequence number (the first line is 1)
    follow: Follow | None = None
    polled: float = 0.0
    ended: float | None = None  # when the child exited (or was refused)
    reason: str | None = None  # the end record's reason
    problem: dict | None = None  # the child's coded error, or a refusal to start it
    dropped: int = 0  # records that did not match the contract
    cli_dropped: bool = False  # the CLI dropped a line over 1 MiB
    stopping: bool = False  # stopped by the tab or as idle: not a failure

    def running(self) -> bool:
        return self.ended is None


def parse(line: str, source: str, name: str) -> dict | None:
    """A checked line record `{"line", "level"}`, `{"event": "end", "reason", "dropped"}`,
    `{"event": "error"}` (the stdout copy of the coded error that `Follow.error` reads from
    stderr), or None for anything else: not JSON, another schema version, another source or
    name than the one followed, or a field of the wrong type."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        return None
    if "error" in record and "event" not in record:
        return {"event": "error"}
    if "event" in record:
        reason, dropped = record.get("reason"), record.get("dropped")
        if record["event"] != "end" or reason not in REASONS or not isinstance(dropped, bool):
            return None
        return {"event": "end", "reason": reason, "dropped": dropped}
    if source == "service":
        if record.get("source") != "service" or record.get("name") != name:
            return None
    elif record.get("source") != "agent" or record.get("deployment") != name:
        return None
    text, level = record.get("line"), record.get("level")
    if not isinstance(text, str) or level not in LEVELS:
        return None
    return {"line": text, "level": level}


class LogFollows:
    """Every open log follow. `start`, `read` and `stop` are the routes; a reaper thread stops
    idle children and forgets ended follows."""

    def __init__(self, runner: Runner, clock: Callable[[], float] = time.monotonic):
        self.runner = runner
        self.clock = clock
        self.mutex = threading.Lock()
        self.follows: dict[str, LogFollow] = {}
        self.reaper: threading.Thread | None = None

    def start(self, session: bytes, source: str, name: str, argv: list[str]) -> dict:
        """Start a follow for `session` with the CLI argv (checked by the caller). A refusal
        (FOLLOWS_FULL, STOPPING, COMMAND_FAILED) is the envelope's error, with no follow."""
        with self.mutex:
            mine = [f for f in self.follows.values() if f.session == session and f.running()]
            if len(mine) >= MAX_PER_SESSION:
                return _refused(
                    "FOLLOWS_FULL",
                    f"{MAX_PER_SESSION} logs are already followed in this session; stop one "
                    "and retry",
                )
            identifier = secrets.token_hex(16)
            entry = LogFollow(session, source, name, polled=self.clock())
            self.follows[identifier] = entry  # counted against the cap while it starts
            self._ensure_reaper()
        try:
            follow = self.runner.follow(
                argv,
                on_line=lambda line: self._line(entry, line),
                on_end=lambda follow: self._ended(entry, follow),
                max_line=MAX_LINE,
            )
        except FollowRefused as refused:
            problem = error(refused.code, refused.message)
        except Exception:
            problem = error("COMMAND_FAILED", "The log command could not start")
        else:
            problem = None
        with self.mutex:
            if problem is not None:
                del self.follows[identifier]
                return _refused(problem["code"], problem["message"])
            if entry.ended is None:  # a child that ended at once has recorded it already
                entry.follow = follow
        # No stop can race this: the tab learns the ID only from this answer, and shutdown
        # stops every child `Runner.follow` started.
        return {"data": {"id": identifier}, "error": None, "stale": False}

    def read(self, session: bytes, identifier: str, after: int) -> dict:
        """The lines after `after` (at most READ_LINES or READ_BYTES; `more` when there are
        others), whether the child still runs, how it ended, and `missed`: lines that left the
        ring before this poll asked for them."""
        with self.mutex:
            entry = self._mine(session, identifier)
            entry.polled = self.clock()
            first = entry.lines[0][0] if entry.lines else entry.seq + 1
            missed = max(0, min(first, entry.seq + 1) - after - 1)
            rows, size, more = [], 0, False
            for seq, line, level, length in entry.lines:
                if seq <= after:
                    continue
                if rows and (len(rows) >= READ_LINES or size + length > READ_BYTES):
                    more = True
                    break
                rows.append({"seq": seq, "line": line, "level": level})
                size += length
            running = entry.running()
            data = {
                "running": running,
                "lines": rows,
                "next": rows[-1]["seq"] if rows else max(after, first - 1),
                "more": more,
                "missed": missed,
                "dropped": entry.dropped,
                "cli_dropped": entry.cli_dropped,
                "reason": None if running else entry.reason,
                "stopped": entry.stopping,
            }
            problem = None if running or entry.stopping else entry.problem
        return {"data": data, "error": problem, "stale": problem is not None}

    def stop(self, session: bytes, identifier: str) -> dict:
        """Stop the tab's follow (Stop following, or the page closed). The child is ended off
        the request thread; its lines stay readable until it is forgotten."""
        with self.mutex:
            entry = self._mine(session, identifier)
            follow = None
            if entry.running():  # an ended follow keeps how it ended (its coded error too)
                entry.stopping = True
                follow = entry.follow
        if follow is not None:
            threading.Thread(target=self.runner.end_follow, args=(follow,), daemon=True).start()
        return {"data": {"stopped": True}, "error": None, "stale": False}

    def _mine(self, session: bytes, identifier: str) -> LogFollow:
        entry = self.follows.get(identifier)
        if entry is None or not secrets.compare_digest(entry.session, session):
            raise FollowNotFound(identifier)
        return entry

    def _line(self, entry: LogFollow, line: str) -> None:
        record = parse(line, entry.source, entry.name)
        with self.mutex:
            if record is None:
                entry.dropped += 1
            elif record.get("event") == "end":
                entry.reason, entry.cli_dropped = record["reason"], record["dropped"]
            elif record.get("event") is None:
                length = len(json.dumps(record["line"])) + 32  # as sent: JSON-escaped
                entry.seq += 1
                entry.lines.append((entry.seq, record["line"], record["level"], length))
                entry.size += length
                while entry.lines and (len(entry.lines) > RING_LINES or entry.size > RING_BYTES):
                    entry.size -= entry.lines.popleft()[3]

    def _ended(self, entry: LogFollow, follow: Follow) -> None:
        problem = follow.error()
        with self.mutex:
            entry.ended, entry.follow = self.clock(), None
            if entry.reason is None and problem is None:
                entry.reason = "container" if not entry.stopping else "stdin"
            entry.problem = problem

    def _ensure_reaper(self) -> None:
        if self.reaper is None or not self.reaper.is_alive():
            self.reaper = threading.Thread(target=self._reap, daemon=True)
            self.reaper.start()

    def _reap(self) -> None:
        """Stop each follow IDLE_AFTER seconds after its last poll; forget one FORGET_AFTER
        seconds after it ended; exit when none is left."""
        while True:
            time.sleep(REAP_EVERY)
            now = self.clock()
            idle = []
            with self.mutex:
                for identifier, entry in list(self.follows.items()):
                    if entry.running():
                        if (
                            entry.follow is not None
                            and not entry.stopping
                            and now - entry.polled > IDLE_AFTER
                        ):
                            entry.stopping = True
                            idle.append(entry.follow)
                    elif now - max(entry.ended, entry.polled) > FORGET_AFTER:
                        del self.follows[identifier]
                if not self.follows and not idle:
                    self.reaper = None
                    return
            for follow in idle:  # each on its own thread: one slow stop delays no other
                threading.Thread(
                    target=self.runner.end_follow, args=(follow,), daemon=True
                ).start()


def _refused(code: str, message: str) -> dict:
    return {"data": None, "error": error(code, message), "stale": False}
