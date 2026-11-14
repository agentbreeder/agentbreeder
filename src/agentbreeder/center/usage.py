"""Service usage for the console (#822, M6; console §4.2, §8.1 #6, S-10).

One long-lived `services usage DIR --follow --json` child per registered project, started by the
first `GET /api/projects/ID/usage` and stopped IDLE_AFTER seconds after the last one (and on
shutdown, through `Runner.shutdown`). Its `sample` and `disk` records are checked against the
CLI's contract (`docs/commands/services.md`) and kept in memory only: a ring of the last
RING_SECONDS of samples, so a page opened later has five minutes of history for its sparklines,
and the latest disk reading with its `as_of` time. Nothing is written to disk or the database.

Only the fields of the contract pass, each checked for its type (strings for a fixed pattern
and length), so an unexpected value from Docker never reaches the browser. A child that fails
(Docker went away: `DOCKER_STATS_FAILED`) keeps its samples and its coded error; the next poll
at least RESTART_AFTER seconds later starts a new child. The child plumbing (stdin pipe, own
process group, PID file, caps) is `Runner.follow` in `jobs.py`, shared with M5's log follows.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from agentbreeder.center.jobs import CONTROL, Follow, FollowRefused, Runner, error

RING_SECONDS = 300.0  # five minutes of samples
MAX_SAMPLES = 160  # the CLI prints at most one sample every 2 s: 150 in five minutes
MAX_LINE = 64 * 1024  # `commands.usage.LINE_LIMIT`: a record is far smaller
IDLE_AFTER = 15.0  # seconds after the last poll before the child is stopped
RESTART_AFTER = 10.0  # a child that ended is not started again sooner than this
REAP_EVERY = 1.0
# The CLI's limits and patterns (`agentbreeder.usage`, `agentbreeder.watch`), tied by a test;
# the server process does not load those modules.
MAX_CONTAINERS = 256  # rows per sample or disk record
SCHEMA_VERSION = 1
NAME_LIMIT = 64  # an agent's name, cut with a trailing "…"
SERVICE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
VOLUME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
DEPLOYMENT = re.compile(r"[0-9a-f]{32}")
ROLE = re.compile(r"[a-z]{1,16}")
TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
NUMBERS = (
    "cpu_percent",
    "memory_used",
    "memory_limit",
    "memory_percent",
    "network_rx",
    "network_tx",
    "block_read",
    "block_write",
    "pids",
)


def _number(value: object) -> bool:
    return value is None or (
        type(value) in (int, float) and value >= 0 and value == value and value < 1e18
    )


def _source(row: object) -> dict | None:
    """Which container a row is: a stack service `{"source": "service", "name", "deployment":
    null}` or an agent `{"source": "agent", "name", "deployment"[, "role"]}`; None when off."""
    if not isinstance(row, dict):
        return None
    source, name = row.get("source"), row.get("name")
    deployment, role = row.get("deployment"), row.get("role")
    if not isinstance(name, str):
        return None
    if source == "service":
        if not SERVICE.fullmatch(name) or deployment is not None or role is not None:
            return None
        return {"source": source, "name": name, "deployment": None}
    if source != "agent" or not 0 < len(name) <= NAME_LIMIT or CONTROL.search(name):
        return None
    if not (isinstance(deployment, str) and DEPLOYMENT.fullmatch(deployment)):
        return None
    checked = {"source": source, "name": name, "deployment": deployment}
    if role is not None:
        if not (isinstance(role, str) and ROLE.fullmatch(role)):
            return None
        checked["role"] = role
    return checked


def _rows(rows: object, check: Callable[[object], dict | None]) -> list[dict] | None:
    if not isinstance(rows, list) or len(rows) > MAX_CONTAINERS:
        return None
    checked = [check(row) for row in rows]
    return None if any(row is None for row in checked) else checked


def _sample_row(row: object) -> dict | None:
    source = _source(row)
    if source is None or not all(_number(row.get(key)) for key in NUMBERS):
        return None
    return {**source, **{key: row.get(key) for key in NUMBERS}}


def _disk_container(row: object) -> dict | None:
    source = _source(row)
    if source is None or not _number(row.get("size")):
        return None
    return {**source, "size": row.get("size")}


def _volume(row: object) -> dict | None:
    if not isinstance(row, dict):
        return None
    name, size = row.get("name"), row.get("size")
    if not (isinstance(name, str) and VOLUME.fullmatch(name)) or not _number(size):
        return None
    return {"name": name, "size": size}


def parse(line: str) -> dict | None:
    """A checked `sample` or `disk` record, `{"event": "end"}`, `{"event": "error"}` (the CLI's
    error object, whose coded line `Follow.error` reads from stderr), or None for anything else
    (a line that is not JSON, another schema version, an unknown event or a bad field)."""
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        return None
    if "error" in record and "event" not in record:
        return {"event": "error"}  # the stdout copy of the coded error, read from stderr
    event = record.get("event")
    if event == "end":
        return {"event": "end"}
    if event == "sample":
        when, rows = record.get("time"), record.get("containers")
        if not isinstance(when, str) or not TIME.fullmatch(when):
            return None
        containers = _rows(rows, _sample_row)
        if containers is None:
            return None
        return {"event": "sample", "time": when, "containers": containers}
    if event == "disk":
        as_of, stale = record.get("as_of"), record.get("stale")
        if as_of is not None and not (isinstance(as_of, str) and TIME.fullmatch(as_of)):
            return None
        if not isinstance(stale, bool):
            return None
        volumes = _rows(record.get("volumes"), _volume)
        containers = _rows(record.get("containers"), _disk_container)
        if volumes is None or containers is None:
            return None
        return {
            "event": "disk",
            "as_of": as_of,
            "stale": stale,
            "volumes": volumes,
            "containers": containers,
        }
    return None


@dataclass
class Feed:
    """One project's usage child and what it has printed."""

    samples: deque = field(default_factory=lambda: deque(maxlen=MAX_SAMPLES))  # (at, sample)
    disk: dict | None = None
    follow: Follow | None = None
    polled: float = 0.0
    ended: float | None = None  # when the last child failed, or was refused, to run
    stopped: Follow | None = None  # a child the reaper stopped (idle): not a failure
    problem: dict | None = None  # the last child's coded error, or a refusal to start one
    dropped: int = 0  # lines that did not match the contract
    starting: bool = False  # a request is starting the child, outside the mutex
    token: object = None  # the current start: a late end of an older child is ignored

    def running(self) -> bool:
        """While it starts, and until its pump has recorded how it ended, so a poll in between
        cannot start a second one."""
        return self.starting or (self.follow is not None and self.follow.pump.is_alive())


class Usage:
    """Every project's usage feed. `read` is the route; a reaper thread stops idle children."""

    def __init__(self, runner: Runner, clock: Callable[[], float] = time.monotonic):
        self.runner = runner
        self.clock = clock
        self.mutex = threading.Lock()
        self.feeds: dict[str, Feed] = {}
        self.reaper: threading.Thread | None = None

    def read(self, project: dict) -> dict:
        """The project's ring and latest disk reading (an envelope). Starts its child when none
        runs, unless one ended less than RESTART_AFTER seconds ago."""
        now = self.clock()
        token = None
        with self.mutex:
            feed = self.feeds.setdefault(project["id"], Feed())
            feed.polled = now
            if not feed.running() and (feed.ended is None or now - feed.ended >= RESTART_AFTER):
                token = feed.token = object()
                feed.starting, feed.problem, feed.ended = True, None, None
            self._ensure_reaper()
        if token is not None:  # the fork and the PID record happen outside the mutex
            self._start(project, feed, token)
        with self.mutex:
            self._trim(feed, now)
            data = {
                "running": feed.running(),
                "window": RING_SECONDS,
                "samples": [sample for _at, sample in feed.samples],
                "disk": feed.disk,
                "dropped": feed.dropped,
            }
            problem = None if data["running"] else feed.problem
        return {"data": data, "error": problem, "stale": problem is not None}

    def _start(self, project: dict, feed: Feed, token: object) -> None:
        """Start the child (without the mutex). A refusal is kept as the feed's error."""
        argv = ["services", "usage", project["path"], "--follow", "--json"]
        try:
            follow = self.runner.follow(
                argv,
                on_line=lambda line: self._line(feed, line),
                on_end=lambda follow: self._ended(feed, token, follow),
                max_line=MAX_LINE,
            )
        except FollowRefused as refused:
            problem = error(refused.code, refused.message)
        except Exception:  # never leave the feed "starting" for good
            problem = error("COMMAND_FAILED", "The usage command could not start")
        else:
            problem = None
        if problem is not None:
            with self.mutex:
                feed.starting, feed.follow = False, None
                feed.ended, feed.problem = self.clock(), problem
            return
        with self.mutex:
            feed.starting, feed.follow = False, follow

    def _line(self, feed: Feed, line: str) -> None:
        record = parse(line)
        with self.mutex:
            if record is None:
                feed.dropped += 1
            elif record["event"] == "error":
                pass
            elif record["event"] == "sample":
                del record["event"]
                feed.samples.append((self.clock(), record))
            elif record["event"] == "disk":
                del record["event"]
                feed.disk = record

    def _ended(self, feed: Feed, token: object, follow: Follow) -> None:
        """A child exited. Stopped as idle, the next poll starts a new one at once; otherwise
        (Docker went away, the stack is gone) its coded error is shown and kept for
        RESTART_AFTER seconds, so a failing child is not restarted on every poll."""
        problem = follow.error()
        with self.mutex:
            if feed.token is not token:
                return  # an older child: a newer one has started since
            if feed.stopped is follow:
                feed.ended, feed.problem = None, None
            else:
                feed.ended, feed.problem = self.clock(), problem

    def _trim(self, feed: Feed, now: float) -> None:
        while feed.samples and now - feed.samples[0][0] > RING_SECONDS:
            feed.samples.popleft()

    def _ensure_reaper(self) -> None:
        if self.reaper is None or not self.reaper.is_alive():
            self.reaper = threading.Thread(target=self._reap, daemon=True)
            self.reaper.start()

    def _reap(self) -> None:
        """Stop each child IDLE_AFTER seconds after its last poll; exit when none runs."""
        while True:
            time.sleep(REAP_EVERY)
            now = self.clock()
            idle = []
            with self.mutex:
                for feed in self.feeds.values():
                    self._trim(feed, now)
                    live = feed.follow is not None and feed.follow.pump.is_alive()
                    if live and not feed.starting and now - feed.polled > IDLE_AFTER:
                        feed.stopped = feed.follow
                        idle.append(feed.follow)
                if not any(feed.running() for feed in self.feeds.values()) and not idle:
                    self.reaper = None
                    return
            for follow in idle:
                self.runner.end_follow(follow)
