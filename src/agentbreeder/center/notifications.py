"""Notifications for the console (#827 M11, console half; console notifications spec §2).

The bell's condition count is `next --json`'s `conditions`, which the console already reads for
the guide (`Center.guide`): nothing here computes or stores a condition. This module is the
stored events' side: `agentbreeder events --json` with fixed argv (never `--project`, so `next`'s
checks do not run a second time), its answer allow-listed per field and kept only for registered
projects, and the mark-read request.

The CLI stores no free text (`docs/events.md`): every value is a validated field and `message` is
its fixed template. This module checks each field again against the same rules and drops an event
that breaks one, so the browser never gets a field outside the contract, a control, bidi or
invisible character, or an event of a project this console does not know.

Pure; imports only `re` and `center.files` (on the server's allowlist, see
`tests/test_center_reads.py`).
"""

from __future__ import annotations

import re

from agentbreeder.center.files import BadRequest

READ_LIMIT = 200  # the newest events read per poll; the CLI keeps 500
MAX_IDS = 50  # ids in one mark-read request
MAX_MESSAGE = 300
MAX_CASES = 50
MAX_CASE_ID = 80
MAX_PATH = 1024
SCHEMA_VERSION = 1
TIMEOUT = 30

KINDS = ("job_failed", "gate_failed", "container_died", "container_unhealthy")
SOURCES = {
    "job_failed": ("job",),
    "gate_failed": ("release",),
    "container_died": ("service", "agent"),
    "container_unhealthy": ("service", "agent"),
}
# The CLI's patterns (`events._TIME`, `_JOB`, `_NAME`, `_CODE_VALUE`), tied by a test.
TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
JOB = re.compile(r"[a-z][a-z-]{0,31}")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}")
CODE = re.compile(r"[A-Z0-9_]{3,40}")
# C0 and C1 controls, bidi controls, separators and invisible format characters.
UNSAFE = re.compile(
    "[\x00-\x1f\x7f-\x9f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff"
    "\U000e0000-\U000e007f]"
)
# `release` saves `<agent>.eval-<run>.json` (eval_reports.RUN; center/evals.py's copies).
REPORT = re.compile(
    r"([a-z0-9](?:[a-z0-9-]{0,51}[a-z0-9])?)\.eval-([0-9]{8}-[0-9]{6}-[0-9a-f]{6})\.json"
)


def events_argv() -> list[str]:
    """`events --limit N --json`: every project's newest events, read-only (no `--project`, so
    the live conditions are not computed again; the guide read has them)."""
    return ["events", "--limit", str(READ_LIMIT), "--json"]


def mark_request(body: object) -> list[int]:
    """`{ids: [int, …]}` (1 to MAX_IDS positive ids, each once) or BadRequest."""
    if not isinstance(body, dict) or set(body) != {"ids"}:
        raise BadRequest("Send {ids: [...]}", "EVENTS_READ_INVALID")
    ids = body["ids"]
    if not isinstance(ids, list) or not 0 < len(ids) <= MAX_IDS:
        raise BadRequest(f"Send 1 to {MAX_IDS} event ids", "EVENTS_READ_INVALID")
    for value in ids:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2**63:
            raise BadRequest("An event id is a positive whole number", "EVENTS_READ_INVALID")
    return list(dict.fromkeys(ids))


def mark_argv(ids: list[int]) -> list[str]:
    """`events --mark-read ID … --limit 1 --json`: each id its own word after its own flag."""
    argv = ["events"]
    for value in ids:
        argv += ["--mark-read", str(value)]
    return [*argv, "--limit", "1", "--json"]


def _text(value: object, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and not UNSAFE.search(value)


def _count(value: object, high: int = 10**6) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= high


def _gate_data(data: object, project: str) -> dict | None:
    if not isinstance(data, dict):
        return None
    cases, passed, more, failed = (data.get(k) for k in ("cases", "passed", "more", "failed"))
    if not (_count(cases) and _count(passed, cases if _count(cases) else 0) and _count(more)):
        return None
    if not isinstance(failed, list) or len(failed) > MAX_CASES:
        return None
    if not all(_text(case, MAX_CASE_ID) for case in failed):
        return None
    report = data.get("report")
    if report is not None and not (_text(report, MAX_PATH) and report.startswith("/")):
        return None
    shown = {"cases": cases, "passed": passed, "failed": list(failed), "more": more}
    shown["report"] = report
    # The report viewer reads only the project's default folder (`DIR/deploy`): a report saved
    # there by name offers the summary read; any other path is shown as text only.
    match = None
    if report is not None and report.startswith(project.rstrip("/") + "/deploy/"):
        match = REPORT.fullmatch(report[len(project.rstrip("/")) + len("/deploy/") :])
    shown["report_agent"] = match[1] if match else None
    shown["report_run"] = match[2] if match else None
    return shown


def _event(item: object, projects: dict[str, str]) -> dict | None:
    """One event as the browser may see it, or None when any field breaks its rule or the
    project is not registered here."""
    if not isinstance(item, dict):
        return None
    project = item.get("project")
    if not isinstance(project, str) or project not in projects:
        return None
    kind, source, name = item.get("kind"), item.get("source"), item.get("name")
    if kind not in KINDS or source not in SOURCES[kind]:
        return None
    identifier = item.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
        return None
    if not isinstance(item.get("time"), str) or not TIME.fullmatch(item["time"]):
        return None
    if not isinstance(name, str) or not (JOB if kind == "job_failed" else NAME).fullmatch(name):
        return None
    exit_code, code = item.get("exit_code"), item.get("code")
    if exit_code is not None and not _count(exit_code, 255):
        return None
    if code is not None and not (isinstance(code, str) and CODE.fullmatch(code)):
        return None
    if not isinstance(item.get("read"), bool) or not _text(item.get("message"), MAX_MESSAGE):
        return None
    data = item.get("data")
    event = {
        "id": identifier,
        "project_id": projects[project],
        "time": item["time"],
        "kind": kind,
        "source": source,
        "name": name,
        "exit_code": exit_code,
        "code": code,
        "read": item["read"],
        "message": item["message"],
    }
    if kind == "gate_failed":
        gate = _gate_data(data, project)
        if gate is None:
            return None
        event["gate"] = gate
    return event


def events_answer(data: object, projects: dict[str, str]) -> dict | None:
    """`events --json`'s answer as {events, unread, dropped} for the registered projects
    (`projects`: path -> console id), or None when the envelope is not the CLI's contract."""
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    items = data.get("events")
    if not isinstance(items, list) or len(items) > READ_LIMIT:
        return None
    events, dropped = [], 0
    for item in items:
        event = _event(item, projects)
        if event is None:
            # Another project's events are not dropped by mistake: they are not this console's.
            dropped += int(isinstance(item, dict) and item.get("project") in projects)
            continue
        events.append(event)
    return {
        "events": events,
        "unread": sum(1 for event in events if not event["read"]),
        "dropped": dropped,
    }
