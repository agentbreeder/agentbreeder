"""Stored events (#827, M11; guided §11, §14.4, §14.9 R1, R8).

Events are things that happened and stay true afterwards: a job failed, the eval gate failed, a
container exited by itself or became unhealthy. They are kept in `events.sqlite3` next to the
console's state (`~/.agentbreeder/center`, or `AGENTBREEDER_CENTER_DIR`), 0600 in a 0700
directory, newest KEEP only.

Nothing stored is free text. Each event is one of KINDS with validated fields: names that match
fixed patterns, an exit code, an error code matching `\\[[A-Z0-9_]{3,40}\\]`, the eval gate's
counts and case ids (at most 80 characters, control characters stripped; §14.9 R8) and the
report path. The message is rendered from a fixed template when read, never stored. No text
from logs, stderr or models is kept: a job's output is reduced to its error code alone.

Conditions (blocked steps, failed deployments, services down) are not events: they are
computed live by `guide_checks.compute`, the function behind `agentbreeder next`, and clear
themselves (`conditions`). A `restarted` container is not stored either: the condition it
cleared already shows the recovery, and the CLI cannot tell a restart from a console job's
`services up` (§14.4).

Writers: `watch --record` (container events), `release` (gate failures) and the console's job
runner (`job_failed`). `record` never raises: storing an event must not fail the command.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from agentbreeder import safe_text

SCHEMA_VERSION = 1
FILENAME = "events.sqlite3"
KEEP = 500
MAX_CASES = 50
MAX_CASE_ID = 80
MAX_PATH = 1024

KINDS = ("job_failed", "gate_failed", "container_died", "container_unhealthy")
SOURCES = {
    "job_failed": ("job",),
    "gate_failed": ("release",),
    "container_died": ("service", "agent"),
    "container_unhealthy": ("service", "agent"),
}
CODE = re.compile(r"\[([A-Z0-9_]{3,40})\]")
_CODE_LINE = re.compile(r"^\[([A-Z0-9_]{3,40})\] ")
_CODE_VALUE = re.compile(r"[A-Z0-9_]{3,40}")
_JOB = re.compile(r"[a-z][a-z-]{0,31}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
_DEPLOYMENT = re.compile(r"[0-9a-f]{32}")
_TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
# Every character `safe_text` treats as a control (C0, C1, bidi, separators, lone surrogates),
# plus invisible formatting characters: removed from case ids and paths before they are stored.
_CONTROL = re.compile(
    safe_text._ANY_CONTROL.pattern[:-1]
    + "\u00ad\u200b-\u200f\u2060-\u2064\ufeff\U000e0000-\U000e007f]"
)
FIELDS = ("time", "project", "kind", "source", "name", "deployment", "exit_code", "code", "data")
PLACEHOLDERS = ", ".join("?" * len(FIELDS))


class EventError(Exception):
    """`[CODE] message`; the message never holds a path or a database error's text."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


def directory() -> Path:
    value = os.environ.get("AGENTBREEDER_CENTER_DIR")
    return Path(value).expanduser() if value else Path.home() / ".agentbreeder" / "center"


def _private_dir(path: Path) -> Path:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.lstat()
    except OSError as error:
        raise EventError("EVENTS_UNSAFE", "cannot create the events directory") from error
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise EventError("EVENTS_UNSAFE", "the events directory must be a directory you own")
    if info.st_mode & 0o077:
        try:
            path.chmod(0o700)
        except OSError as error:
            raise EventError(
                "EVENTS_UNSAFE", "cannot make the events directory private"
            ) from error
    return path


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(text: object, limit: int) -> str:
    """One line, control and bidi characters removed, at most `limit` characters."""
    return _CONTROL.sub("", str(text))[:limit].strip()


# -- validation and templates --------------------------------------------------------------------


def _invalid(what: str) -> EventError:
    return EventError("EVENT_INVALID", f"event field `{what}` is invalid")


def _int(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _check_data(kind: str, data) -> dict:
    if not isinstance(data, dict):
        raise _invalid("data")
    if kind == "job_failed":
        if set(data) != {"cancelled"} or not isinstance(data["cancelled"], bool):
            raise _invalid("data")
    elif kind == "container_died":
        if set(data) != {"oom"} or not isinstance(data["oom"], bool):
            raise _invalid("data")
    elif kind == "container_unhealthy":
        if data:
            raise _invalid("data")
    elif kind == "gate_failed":
        if set(data) != {"passed", "cases", "failed", "more", "report"}:
            raise _invalid("data")
        if not (_int(data["cases"], 0, 10**6) and _int(data["passed"], 0, data["cases"])):
            raise _invalid("data")
        failed = data["failed"]
        if not isinstance(failed, list) or len(failed) > MAX_CASES:
            raise _invalid("data")
        for case in failed:
            if not isinstance(case, str) or case != _clean(case, MAX_CASE_ID):
                raise _invalid("data")
        if not _int(data["more"], 0, 10**6):
            raise _invalid("data")
        report = data["report"]
        if report is not None and (
            not isinstance(report, str) or report != _clean(report, MAX_PATH) or not report
        ):
            raise _invalid("data")
    return data


def validate(event) -> dict:
    """The event if every field is one this module would build, else EVENT_INVALID."""
    if not isinstance(event, dict) or set(event) != set(FIELDS):
        raise _invalid("fields")
    kind = event["kind"]
    if kind not in KINDS:
        raise _invalid("kind")
    if event["source"] not in SOURCES[kind]:
        raise _invalid("source")
    if not isinstance(event["time"], str) or not _TIME.fullmatch(event["time"]):
        raise _invalid("time")
    project = event["project"]
    if (
        not isinstance(project, str)
        or not project.startswith("/")
        or project != _clean(project, MAX_PATH)
    ):
        raise _invalid("project")
    name_pattern = _JOB if kind == "job_failed" else _NAME
    if not isinstance(event["name"], str) or not name_pattern.fullmatch(event["name"]):
        raise _invalid("name")
    deployment = event["deployment"]
    if deployment is not None and (
        not isinstance(deployment, str) or not _DEPLOYMENT.fullmatch(deployment)
    ):
        raise _invalid("deployment")
    if event["exit_code"] is not None and not _int(event["exit_code"], 0, 255):
        raise _invalid("exit_code")
    code = event["code"]
    if code is not None and (not isinstance(code, str) or not _CODE_VALUE.fullmatch(code)):
        raise _invalid("code")
    _check_data(kind, event["data"])
    return event


def render(event: dict) -> str:
    """The fixed template for an event (every value in it was validated)."""
    kind, data = event["kind"], event["data"]
    if kind == "job_failed":
        if data["cancelled"]:
            return f"Job {event['name']} was cancelled"
        exit_code = event["exit_code"]
        text = f"Job {event['name']} failed" + (
            f" (exit {exit_code})" if exit_code is not None else ""
        )
        return text + (f" [{event['code']}]" if event["code"] else "")
    if kind == "gate_failed":
        text = f"Eval gate failed for {event['name']}: {data['passed']}/{data['cases']} passed"
        if data["failed"]:
            text += "; failed: " + ", ".join(data["failed"][:5])
            hidden = len(data["failed"]) - 5 + data["more"]
            if hidden > 0:
                text += f" (+{hidden} more)"
        return text
    what = f"{event['source']} {event['name']}"
    if kind == "container_unhealthy":
        return f"{what} became unhealthy"
    detail = f"exit {event['exit_code']}" if event["exit_code"] is not None else "no exit code"
    if data["oom"]:
        detail += ", out of memory"
    return f"{what} exited ({detail})"


# -- builders -------------------------------------------------------------------------------------


def _event(kind, project, source, name, deployment=None, exit_code=None, code=None, data=None):
    return {
        "time": _now(),
        "project": str(project),
        "kind": kind,
        "source": source,
        "name": name,
        "deployment": deployment,
        "exit_code": exit_code,
        "code": code,
        "data": data if data is not None else {},
    }


def from_watch(event: dict, project: str) -> dict | None:
    """The stored form of a `watch.Events` event; None for `restarted` and status records."""
    kind = {"died": "container_died", "unhealthy": "container_unhealthy"}.get(event.get("event"))
    if kind is None:
        return None
    stored = _event(
        kind,
        project,
        event.get("source"),
        event.get("name"),
        deployment=event.get("deployment"),
        exit_code=_exit_code(event.get("exit_code")) if kind == "container_died" else None,
        data={"oom": bool(event.get("oom"))} if kind == "container_died" else {},
    )
    if isinstance(event.get("time"), str) and _TIME.fullmatch(event["time"]):
        stored["time"] = event["time"]
    return stored


def project_dir(path: Path) -> str:
    """The project an event belongs to: the resolved directory (a file's parent)."""
    resolved = Path(path).resolve()
    return str(resolved.parent if resolved.is_file() else resolved)


def _exit_code(value) -> int | None:
    """A container's exit code, or None outside 0-255 (Docker never reports more)."""
    return value if _int(value, 0, 255) else None


def code_of(text: str | None) -> str | None:
    """The error code of a CLI's `[CODE] message` line, else None (nothing else is kept)."""
    if not isinstance(text, str):
        return None
    match = _CODE_LINE.match(text.strip())
    return match.group(1) if match else None


def job_failed(
    kind: str,
    project: str,
    returncode: int | None,
    last_coded_line: str | None,
    cancelled: bool = False,
) -> dict | None:
    """A failed or cancelled console job. None when it is not an event: success, waiting for
    approval (3), or a release the eval gate stopped (5; `release` stores `gate_failed`).

    `last_coded_line` must be the CLI's own last `[CODE] message` stderr line (as the console's
    `cli_error` finds it), never a line of a child's or an agent's output: the code is the only
    part kept, but a code-shaped token from other output could carry up to 40 characters."""
    if not cancelled and (
        returncode == 0 or returncode == 3 or (kind == "release" and returncode == 5)
    ):
        return None
    exit_code = returncode if _int(returncode, 0, 255) else None
    return _event(
        "job_failed",
        project,
        "job",
        kind,
        exit_code=exit_code,
        code=None if cancelled else code_of(last_coded_line),
        data={"cancelled": bool(cancelled)},
    )


def gate_failed(project: str, details: dict) -> dict:
    """From `release`'s outcome details: counts, case ids (no checks or judge text), report."""
    summary = details.get("eval") or {}
    cases = [_clean(case.get("id", ""), MAX_CASE_ID) for case in details.get("failed_cases") or []]
    cases = [case for case in cases if case]
    report = details.get("eval_report")
    report = _clean(report, MAX_PATH) if isinstance(report, str) else None
    return _event(
        "gate_failed",
        project,
        "release",
        details.get("agent"),
        data={
            "passed": summary.get("passed"),
            "cases": summary.get("cases"),
            "failed": cases[:MAX_CASES],
            "more": max(0, len(cases) - MAX_CASES),
            "report": report or None,
        },
    )


# -- the store ------------------------------------------------------------------------------------


class EventStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    @classmethod
    def open(cls, path: Path | None = None) -> EventStore:
        """Open for writing, creating the directory (0700) and the file (0600)."""
        folder = _private_dir(path or directory())
        file = folder / FILENAME
        if file.is_symlink():
            raise EventError("EVENTS_UNSAFE", "the events file must not be a symlink")
        try:
            descriptor = os.open(file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
            file.chmod(0o600)
            db = sqlite3.connect(file, timeout=5, isolation_level=None, check_same_thread=False)
        except (OSError, sqlite3.Error) as error:
            raise EventError("EVENTS_UNREADABLE", "cannot open the events store") from error
        store = cls(db)
        try:
            store._migrate()
        except sqlite3.Error as error:
            db.close()
            raise EventError("EVENTS_UNREADABLE", "cannot read the events store") from error
        except EventError:
            db.close()
            raise
        return store

    @classmethod
    def open_readonly(cls, path: Path | None = None) -> EventStore | None:
        """Open without creating anything; None when there is no store yet."""
        folder = path or directory()
        file = folder / FILENAME
        if not file.is_file():
            return None
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or file.is_symlink():
            raise EventError("EVENTS_UNSAFE", "the events store must be a file you own")
        try:
            db = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
            version = db.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.Error as error:
            raise EventError(
                "EVENTS_UNREADABLE", "cannot read the events store (busy or damaged; try again)"
            ) from error
        if version > SCHEMA_VERSION:
            db.close()
            raise _too_new(version)
        if version < SCHEMA_VERSION:
            db.close()
            return None  # never written yet: nothing to show
        return cls(db)

    def _migrate(self) -> None:
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise _too_new(version)
        if version == SCHEMA_VERSION:
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    time TEXT NOT NULL,
                    project TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    name TEXT NOT NULL,
                    deployment TEXT,
                    exit_code INTEGER,
                    code TEXT,
                    data TEXT NOT NULL,
                    read INTEGER NOT NULL DEFAULT 0
                )"""
            )
            self.db.execute("CREATE INDEX IF NOT EXISTS events_project ON events (project, id)")
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.db.close()

    def append(self, event: dict) -> int:
        """Store a validated event and drop all but the newest KEEP, in one transaction."""
        validate(event)
        row = [event[field] for field in FIELDS]
        row[-1] = json.dumps(event["data"], sort_keys=True)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.db.execute(
                f"INSERT INTO events ({', '.join(FIELDS)}) VALUES ({PLACEHOLDERS})", row
            )
            self.db.execute("DELETE FROM events WHERE id <= ?", (cursor.lastrowid - KEEP,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return cursor.lastrowid

    def recent(self, project: str | None = None, limit: int = 50, unread: bool = False) -> list:
        clauses, parameters = [], []
        if project is not None:
            clauses.append("project = ?")
            parameters.append(project)
        if unread:
            clauses.append("read = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.execute(
            f"SELECT id, read, {', '.join(FIELDS)} FROM events {where} ORDER BY id DESC LIMIT ?",
            (*parameters, limit),
        ).fetchall()
        shown = []
        for row in rows:
            event = dict(zip(FIELDS, row[2:], strict=True))
            try:
                event["data"] = json.loads(event["data"])
                validate(event)
            except (ValueError, TypeError, EventError):
                continue  # written by something else: never shown
            shown.append({"id": row[0], **event, "read": bool(row[1]), "message": render(event)})
        return shown

    def unread_count(self, project: str | None = None) -> int:
        if project is None:
            return self.db.execute("SELECT count(*) FROM events WHERE read = 0").fetchone()[0]
        return self.db.execute(
            "SELECT count(*) FROM events WHERE read = 0 AND project = ?", (project,)
        ).fetchone()[0]

    def mark_read(self, ids: list[int] | None, project: str | None = None) -> None:
        """Mark these ids read, or (ids None) every event, or every event of `project`."""
        if ids is not None:
            self.db.executemany("UPDATE events SET read = 1 WHERE id = ?", [(i,) for i in ids])
        elif project is not None:
            self.db.execute("UPDATE events SET read = 1 WHERE project = ?", (project,))
        else:
            self.db.execute("UPDATE events SET read = 1")


def _too_new(version: int) -> EventError:
    return EventError(
        "EVENTS_TOO_NEW",
        f"the events store is at schema version {version}, newer than this AgentBreeder",
    )


def record(
    event: dict | Callable[[], dict | None] | None, store: EventStore | None = None
) -> bool:
    """Store an event if there is one; False (never an exception) when it was not stored.
    `event` may be a function building it, so a builder's failure cannot escape either."""
    try:
        if callable(event):
            event = event()
        if event is None:
            return False
        if store is not None:
            store.append(event)
            return True
        handle = EventStore.open()
        try:
            handle.append(event)
        finally:
            handle.close()
        return True
    except Exception:  # noqa: BLE001 - storing an event must never fail the command
        return False


def read_recent(
    path: Path | None = None, project: str | None = None, limit: int = 50, unread: bool = False
) -> list[dict]:
    store = EventStore.open_readonly(path)
    if store is None:
        return []
    try:
        return store.recent(project, limit, unread)
    finally:
        store.close()


def read_unread(path: Path | None = None, project: str | None = None) -> int:
    store = EventStore.open_readonly(path)
    if store is None:
        return 0
    try:
        return store.unread_count(project)
    finally:
        store.close()


def conditions(project: Path, environment: str = "dev") -> list[dict]:
    """The live conditions for a project: exactly `next`'s (`guide_checks.compute`), never
    stored, so they clear themselves when the cause goes away."""
    from agentbreeder import guide_checks

    result = guide_checks.compute(project, environment)
    return [{"code": c.code, "message": c.message} for c in result.conditions]
