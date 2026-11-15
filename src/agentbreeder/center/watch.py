"""Container events for the console (#821, M5; guided §14.4, §14.9 R1, R5; S-10).

One long-lived `watch --stack DIR --json --record` child per registered project, started by the
first `GET /api/projects/ID/watch` and stopped IDLE_AFTER seconds after the last one (and on
shutdown); the child plumbing is `usage.Feeds` and `Runner.follow`, shared with the usage feed
and the log follows. `--record` makes the CLI store `died` and `unhealthy` in `events.sqlite3`
(`agentbreeder events`, #827): that is the child's only write, and the console stores nothing of
its own from it.

Kept in memory only: the latest `status` record (each service's compose state and health, each
deployment's state, a pod check for a ready kind deployment) and the newest MAX_EVENTS events,
each with an id that only grows, so the browser can tell a new one. Only the fields of the CLI's
contract (`docs/commands/watch.md`) pass, each checked for its type and pattern; anything else is
counted as dropped. A `restarted` that arrives while a `services up` job of the same project runs
is dropped (§14.4: the CLI cannot see the console's jobs). The events and status survive a child
that stopped as idle, so a page opened again still shows them.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field

from agentbreeder.center.jobs import CONTROL
from agentbreeder.center.usage import (
    DEPLOYMENT,
    NAME_LIMIT,
    ROLE,
    SCHEMA_VERSION,
    SERVICE,
    TIME,
    Child,
    Feeds,
)

MAX_EVENTS = 50  # newest kept per project
MAX_LINE = 256 * 1024  # `commands.watch.LINE_LIMIT`: a status record with many deployments
MAX_ROWS = 256  # services or deployments in one status record
# The CLI's patterns (`commands.watch._WORD`, `kubernetes._WORD`), tied by a test.
WORD = re.compile(r"[a-z][a-z ]{0,23}")  # a compose state or health word
POD_REASON = re.compile(r"[A-Za-z]{1,64}")
STATE = re.compile(r"[a-z][a-z_]{0,31}")  # a deployment's recorded state
EVENTS = ("died", "unhealthy", "restarted")
TARGETS = ("local", "kubernetes")


def _word(value: object) -> bool:
    return value is None or (isinstance(value, str) and WORD.fullmatch(value) is not None)


def _agent_name(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= NAME_LIMIT and not CONTROL.search(value)


def _service(row: object) -> dict | None:
    if not isinstance(row, dict):
        return None
    name, state, health = row.get("name"), row.get("state"), row.get("health")
    if not (isinstance(name, str) and SERVICE.fullmatch(name)) or not (
        _word(state) and _word(health)
    ):
        return None
    return {"name": name, "state": state, "health": health}


def _pod(value: object) -> tuple[bool, dict | None]:
    """(valid, pod): null (the cluster could not be read) or {"running", "reason"}."""
    if value is None:
        return True, None
    if not isinstance(value, dict) or not isinstance(value.get("running"), bool):
        return False, None
    reason = value.get("reason")
    if reason is not None and not (isinstance(reason, str) and POD_REASON.fullmatch(reason)):
        return False, None
    return True, {"running": value["running"], "reason": reason}


def _deployment(row: object) -> dict | None:
    if not isinstance(row, dict):
        return None
    identifier, name, target, state = (row.get(k) for k in ("id", "name", "target", "state"))
    if not (isinstance(identifier, str) and DEPLOYMENT.fullmatch(identifier)):
        return None
    if not _agent_name(name) or target not in TARGETS:
        return None
    if not (isinstance(state, str) and STATE.fullmatch(state)):
        return None
    checked = {"id": identifier, "name": name, "target": target, "state": state}
    if "pod" in row:
        valid, pod = _pod(row["pod"])
        if not valid:
            return None
        checked["pod"] = pod
    return checked


def _rows(rows: object, check) -> list[dict] | None:
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        return None
    checked = [check(row) for row in rows]
    return None if any(row is None for row in checked) else checked


def _event(record: dict) -> dict | None:
    kind, when = record.get("event"), record.get("time")
    source, name, deployment = record.get("source"), record.get("name"), record.get("deployment")
    if when is not None and not (isinstance(when, str) and TIME.fullmatch(when)):
        return None
    if source == "service":
        if not (isinstance(name, str) and SERVICE.fullmatch(name)) or deployment is not None:
            return None
        if "role" in record:
            return None
    elif source == "agent":
        if not _agent_name(name):
            return None
        if not (isinstance(deployment, str) and DEPLOYMENT.fullmatch(deployment)):
            return None
    else:
        return None
    event = {"event": kind, "time": when, "source": source, "name": name, "deployment": deployment}
    if "role" in record:
        role = record["role"]
        if not (isinstance(role, str) and ROLE.fullmatch(role)):
            return None
        event["role"] = role
    if kind == "died":
        exit_code, oom = record.get("exit_code"), record.get("oom")
        if exit_code is not None and not (
            type(exit_code) is int and 0 <= exit_code <= 999  # `watch._exit_code`: 3 digits
        ):
            return None
        if not isinstance(oom, bool):
            return None
        event["exit_code"], event["oom"] = exit_code, oom
    return event


def parse(line: str) -> dict | None:
    """A checked `status` record or event, `{"event": "end"}`, `{"event": "error"}` (the CLI's
    error object, whose coded line `Follow.error` reads from stderr), or None for anything else
    (a line that is not JSON, another schema version, an unknown event or a bad field)."""
    try:
        record = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        return None
    if "error" in record and "event" not in record:
        return {"event": "error"}
    kind = record.get("event")
    if kind == "end":
        return {"event": "end"}
    if kind == "status":
        when = record.get("time")
        if not (isinstance(when, str) and TIME.fullmatch(when)):
            return None
        services = record.get("services")
        if services is not None:
            services = _rows(services, _service)
            if services is None:
                return None
        deployments = _rows(record.get("deployments"), _deployment)
        if deployments is None:
            return None
        return {"event": "status", "time": when, "services": services, "deployments": deployments}
    if kind in EVENTS:
        return _event(record)
    return None


@dataclass
class WatchFeed(Child):
    """One project's `watch` child and what it has printed."""

    project: str = ""
    status: dict | None = None
    events: deque = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    next_id: int = 1
    held: int = 0  # `restarted` events dropped while a services-up job of the project ran


class Watch(Feeds):
    """Every project's `watch` feed: the latest status and the newest events."""

    what = "watch"
    max_line = MAX_LINE

    def new_feed(self, project: dict) -> WatchFeed:
        return WatchFeed(project=project["id"])

    def argv(self, project: dict) -> list[str]:
        return ["watch", "--stack", project["path"], "--json", "--record"]

    def _services_up(self, project: str) -> bool:
        return any(
            job["project"] == project and job["kind"] == "services-up"
            for job in self.runner.running()
        )

    def on_line(self, feed: WatchFeed, line: str) -> None:
        record = parse(line)
        # Read outside our mutex: `Runner.running` takes the runner's.
        held = (
            record is not None
            and record["event"] == "restarted"
            and self._services_up(feed.project)
        )
        with self.mutex:
            if record is None:
                feed.dropped += 1
            elif record["event"] in ("error", "end"):
                pass
            elif record["event"] == "status":
                del record["event"]
                feed.status = record
            elif held:
                feed.held += 1
            else:
                feed.events.append({"id": feed.next_id, **record})
                feed.next_id += 1

    def data(self, feed: WatchFeed, now: float) -> dict:
        return {
            "status": feed.status,
            "events": list(reversed(feed.events)),  # newest first
            "held": feed.held,
        }
