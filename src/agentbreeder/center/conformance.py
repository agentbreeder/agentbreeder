"""Conformance in the console (#1085 S3, console spec §6.3 and §8): functions over CLI JSON,
and the project's build record read through `registry.read_project_file`.

- `report` checks `registry conformance REF --json`'s answer (the newest report of an image built
  on that runtime version, from `~/.agentbreeder/conformance`, written by `agentbreeder
  conformance`) and reduces it to what the runtime detail shows. Every text is cleaned and capped.
- `images` reads the project's build record (`deploy/<agent>.build.json`, what `build` writes)
  for the image a conformance run may check: the console never takes an image from the browser
  that is not a built image of this project (never a free string). The record file itself must
  not be a link; `deploy/` is the owner's project, as for the CLI's own `conformance`.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from agentbreeder import registry

SCHEMA_VERSION = 1
RESULTS = ("pass", "fail", "incomplete")
STATUSES = ("pass", "fail", "not_run", "not_applicable")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
STAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
CHECK_ID = re.compile(r"[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,63}")
WORD = re.compile(r"[a-z][a-z0-9_-]{0,63}")
AGENT = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
VERSION = re.compile(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}[0-9A-Za-z.+-]{0,40}")
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
MAX_CHECKS = 100
MAX_GROUP = 60
MAX_TITLE = 200
MAX_DETAIL = 300
MAX_COUNT = 10000
RECORD_LIMIT = 256 * 1024  # a build record: refs and aliases, a few KiB


class BadOutput(ValueError):
    """The CLI printed something other than its documented answer."""


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise BadOutput
    return " ".join(CONTROL.sub(" ", value).split())[:limit]


def _check(row: object) -> dict:
    if not isinstance(row, dict):
        raise BadOutput
    identifier, status = row.get("id"), row.get("status")
    if not isinstance(identifier, str) or not CHECK_ID.fullmatch(identifier):
        raise BadOutput
    if status not in STATUSES:
        raise BadOutput
    detail = row.get("detail")
    return {
        "id": identifier,
        "group": _text(row.get("group"), MAX_GROUP),
        "title": _text(row.get("title"), MAX_TITLE),
        "status": status,
        "detail": _text(detail, MAX_DETAIL) if detail is not None else "",
    }


def _report(data: dict, ref: str) -> dict:
    if data.get("runtime") != ref or data.get("result") not in RESULTS:
        raise BadOutput
    image, finished = data.get("image"), data.get("finished")
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        raise BadOutput
    if not isinstance(finished, str) or not STAMP.fullmatch(finished):
        raise BadOutput
    started = data.get("started")
    if started is not None and (not isinstance(started, str) or not STAMP.fullmatch(started)):
        raise BadOutput
    agent, profile = data.get("agent"), data.get("profile")
    if not isinstance(agent, str) or not AGENT.fullmatch(agent):
        raise BadOutput
    if not isinstance(profile, str) or not WORD.fullmatch(profile):
        raise BadOutput
    protocol = data.get("protocol")
    if not isinstance(protocol, int) or isinstance(protocol, bool) or not 0 < protocol < 1000:
        raise BadOutput
    counts = data.get("counts")
    if not isinstance(counts, dict):
        raise BadOutput
    shown_counts = {}
    for status in STATUSES:
        value = counts.get(status)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_COUNT:
            raise BadOutput
        shown_counts[status] = value
    rows = data.get("checks")
    if not isinstance(rows, list) or len(rows) > MAX_CHECKS:
        raise BadOutput
    checks = [_check(row) for row in rows]
    if len({row["id"] for row in checks}) != len(checks):
        raise BadOutput
    version = data.get("agentbreeder")
    return {
        "runtime": ref,
        "mode": "declared",
        "result": data["result"],
        "image": image,
        "agent": agent,
        "profile": profile,
        "protocol": protocol,
        "started": started,
        "finished": finished,
        "agentbreeder": version
        if isinstance(version, str) and VERSION.fullmatch(version)
        else None,
        "counts": shown_counts,
        "checks": checks,
    }


def report(data: object, ref: str) -> dict | None:
    """`registry conformance REF --json`'s `report`, checked and reduced, or None when there is
    none. Only a declared-mode report counts: a runtime's status never comes from code mode.
    Raises BadOutput for anything else."""
    try:
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise BadOutput
        if data.get("ref") != ref:
            raise BadOutput
        found = data.get("report")
        if found is None:
            return None
        if not isinstance(found, dict) or found.get("mode") != "declared":
            raise BadOutput
        return _report(found, ref)
    except BadOutput:
        raise BadOutput("`registry conformance --json` printed an unexpected answer") from None


def images(project_dir: str, agent: str | None) -> list[dict]:
    """The project's built image (`deploy/<agent>.build.json`, what `build` wrote last), as
    `[{agent, image, runtime, built}]`: none when there is no record, or it is unreadable, a
    link, or not a build record. `build` keeps one record per agent, so this is at most one."""
    if not isinstance(agent, str) or not AGENT.fullmatch(agent):
        return []
    path = Path(project_dir) / "deploy" / f"{agent}.build.json"
    try:
        raw = registry.read_project_file(path, RECORD_LIMIT)
        info = path.lstat() if raw is not None else None
    except (registry.RegistryError, OSError):
        return []
    if raw is None or info is None:
        return []
    try:
        record = json.loads(raw)
    except (ValueError, RecursionError):
        return []
    if not isinstance(record, dict) or record.get("name") != agent:
        return []
    image, runtime = record.get("image"), record.get("runtime")
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        return []
    try:
        pinned = registry.Ref.parse(runtime) if isinstance(runtime, str) else None
    except registry.RegistryError:
        pinned = None
    if pinned is None or pinned.kind != "runtime":
        return []
    built = datetime.fromtimestamp(info.st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [{"agent": agent, "image": image, "runtime": str(pinned), "built": built}]
