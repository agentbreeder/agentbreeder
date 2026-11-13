"""Container events for one stack (`agentbreeder watch`, #821 M5; guided §14.4, §14.9 R1, R5).

`Events.feed` turns `docker events --format '{{json .}}'` records into the events the console
shows, and nothing else:

- `died`: a container exited by itself. A `die` is an event only when no `kill`, `stop` or
  `destroy` for that container came since it last started (R1): `docker stop`, `rm -f` and
  `compose down` all send `kill` or `stop` first, so a clean release, `remove` or `services
  down` reports nothing; so does `docker kill` (a user's stop is a stop). An agent container
  whose deployment is not `ready` (being deployed, failed, being removed) is the running job's,
  so its `die` is not an event either. An `oom` shortly before is carried on the event.
- `unhealthy`: a health check started failing (for an agent, only while it is `ready`).
- `restarted`: a container that `died` started again. The CLI cannot see the console's jobs:
  the console drops `restarted` while its own `services up` job runs (§14.4).

Matching (R5): stack services by the `io.agentbreeder.stack` label (one-shot `init` and
`bootstrap` steps carry `io.agentbreeder.step` and are skipped); agent containers carry no stack
label, so they match by `io.agentbreeder.deployment` against this stack's deployment IDs. Two
`label=` filters on `docker events` combine as AND (checked on Docker Desktop, 2026-11-12), so
the stream is filtered by type and action only, `relevant` drops everything without an
AgentBreeder label, and the rest is matched here. Labels are not authenticated: a container on
the same Docker engine can claim them, so events are best effort, never evidence. Nothing from
a container's labels is printed except a validated service name. Events are not stored
(stored events come later, M9).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime

SCHEMA_VERSION = 1
OOM_WINDOW = 30.0  # seconds: an `oom` this recent is carried on the `die` that follows it
REREAD = 2.0  # seconds between re-reads of the deployment rows for an unknown ID
REREAD_STATE = 0.5  # ... and for a known one whose cached state is not `ready`
MAX_TRACKED = 4096  # containers remembered per kind; the oldest are dropped beyond it
ACTIONS = ("start", "die", "kill", "stop", "destroy", "oom", "health_status")
INTENT = frozenset({"kill", "stop", "destroy"})
LABEL_STACK = "io.agentbreeder.stack"
LABEL_STEP = "io.agentbreeder.step"
LABEL_SERVICE = "com.docker.compose.service"
LABEL_DEPLOYMENT = "io.agentbreeder.deployment"
LABEL_ROLE = "io.agentbreeder.role"
_SERVICE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}")
_DEPLOYMENT = re.compile(r"[0-9a-f]{32}")
_ROLE = re.compile(r"[a-z]{1,16}")
_CONTAINER = re.compile(r"[0-9a-f]{12,64}")


def relevant(record: object) -> bool:
    """Whether a record could be this stack's (cheap; `Events.feed` does the matching)."""
    if not isinstance(record, dict) or record.get("Type") != "container":
        return False
    actor = record.get("Actor")
    labels = actor.get("Attributes") if isinstance(actor, dict) else None
    return isinstance(labels, dict) and (LABEL_STACK in labels or LABEL_DEPLOYMENT in labels)


def _remember(store: dict, key: str, value) -> None:
    store.pop(key, None)
    store[key] = value
    while len(store) > MAX_TRACKED:
        del store[next(iter(store))]  # insertion order: the oldest first


class Events:
    """Feed docker event records in order; each returns the events to print (often none)."""

    def __init__(
        self,
        stack_id: str,
        deployments: Callable[[], dict[str, dict]],
        clock: Callable[[], float],
    ):
        """`deployments()` reads this stack's deployment rows (id -> {name, state, …}),
        read-only; it is called again when an event names an unknown deployment ID or one whose
        cached state is not `ready` (its job may have finished since)."""
        self.stack_id = stack_id
        self._read = deployments
        self.deployments = deployments()
        self.clock = clock
        self._last_read = clock()
        self.intent: dict[str, bool] = {}  # containers killed/stopped since they last started
        self.oom: dict[str, float] = {}
        self.died: dict[str, bool] = {}  # containers reported dead, for `restarted`

    def _reread(self, wait: float) -> None:
        if self.clock() - self._last_read >= wait:
            self.deployments = self._read()
            self._last_read = self.clock()

    def _source(self, labels: dict, fresh: bool = False) -> dict | None:
        """{"source", "name", "deployment", "role", "state"} for this stack's container, else
        None. `fresh`: re-read a deployment whose cached state is not `ready` first."""
        deployment = labels.get(LABEL_DEPLOYMENT)
        if isinstance(deployment, str) and _DEPLOYMENT.fullmatch(deployment):
            if deployment not in self.deployments:
                self._reread(REREAD)  # a deployment made since we last read
            elif fresh and self.deployments[deployment].get("state") != "ready":
                self._reread(REREAD_STATE)
            row = self.deployments.get(deployment)
            if row is None:
                return None  # another stack's, or another state's
            role = labels.get(LABEL_ROLE)
            return {
                "source": "agent",
                "name": row["name"],
                "deployment": deployment,
                "role": role if isinstance(role, str) and _ROLE.fullmatch(role) else None,
                "state": row.get("state"),
            }
        if labels.get(LABEL_STACK) != self.stack_id or LABEL_STEP in labels:
            return None
        service = labels.get(LABEL_SERVICE)
        if not isinstance(service, str) or not _SERVICE.fullmatch(service):
            return None
        return {"source": "service", "name": service, "deployment": None, "role": None}

    def feed(self, record: dict) -> list[dict]:
        if not relevant(record):
            return []
        actor = record["Actor"]
        labels = actor["Attributes"]
        container = actor.get("ID")
        action = record.get("Action")
        if not isinstance(container, str) or not _CONTAINER.fullmatch(container):
            return []
        if not isinstance(action, str):
            return []
        judged = action in ("die", "health_status: unhealthy")
        source = self._source(labels, fresh=judged)
        if source is None:
            return []
        now = self.clock()
        if action in INTENT:
            if action == "destroy":  # gone for good: forget it
                for store in (self.intent, self.oom, self.died):
                    store.pop(container, None)
            else:
                _remember(self.intent, container, True)
            return []
        if action == "oom":
            _remember(self.oom, container, now)
            return []
        if action == "start":
            self.intent.pop(container, None)  # a later `die` is its own again
            if self.died.pop(container, None):
                return [self._event("restarted", source, record)]
            return []
        if judged and source["source"] == "agent" and source.get("state") != "ready":
            return []  # the running job's container (deploy, release, remove)
        if action == "die":
            if container in self.intent:
                return []  # stopped or removed on purpose
            _remember(self.died, container, True)
            oom = self.oom.pop(container, None)
            return [
                self._event(
                    "died",
                    source,
                    record,
                    exit_code=_exit_code(labels.get("exitCode")),
                    oom=oom is not None and now - oom <= OOM_WINDOW,
                )
            ]
        if action == "health_status: unhealthy":
            return [self._event("unhealthy", source, record)]
        return []

    def _event(self, kind: str, source: dict, record: dict, **extra) -> dict:
        stamp = record.get("time")
        when = (
            datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if isinstance(stamp, int) and 0 < stamp < 2**33
            else None
        )
        event = {
            "schema_version": SCHEMA_VERSION,
            "event": kind,
            "time": when,
            "source": source["source"],
            "name": source["name"],
            "deployment": source["deployment"],
        }
        if source["role"] not in (None, "agent"):
            event["role"] = source["role"]
        return {**event, **extra}


def _exit_code(value) -> int | None:
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 3:
        return int(value)
    return None


def text(event: dict) -> str:
    """One line for a terminal (every value is validated or from local state)."""
    what = f"{event['source']} {event['name']}"
    if event.get("role"):
        what += f" ({event['role']})"
    if event["event"] == "died":
        detail = f"exit {event['exit_code']}" if event.get("exit_code") is not None else "exited"
        if event.get("oom"):
            detail += ", out of memory"
        return f"{event['time'] or '-'} died: {what}, {detail}"
    return f"{event['time'] or '-'} {event['event']}: {what}"
