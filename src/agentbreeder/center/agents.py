"""The Agents screens' answers (#823, M7): functions over CLI JSON; the only I/O is resolving
recorded project paths (`os.path.realpath`).

- `link_projects` marks each row of `list --all --json` with the console project it belongs to
  (same resolved project path and environment), so the browser can open its detail; a row whose
  stack is not registered here keeps `console_project: null` ("not registered").
- `releases` turns `audit list --json` rows into the project's release runs. `release` writes
  `started` (with the built image) and then one outcome: `promoted`, `eval_failed` or `failed`
  (`release.py`); the outcome's reasons carry the eval run and score (`RELEASE_EVAL "RUN n/N"`),
  the image, the manifest digest and the threshold, except a `failed` eval run, which carries
  only its error. `release --rollback` writes `rolled_back` or `rollback_failed`. Release rows
  name no agent: they are the runs of the project's `agent.yaml`, whose name the server adds.
"""

from __future__ import annotations

import os
import re

# `RELEASE_EVAL`'s message: "<run id> <passed>/<cases>".
_EVAL = re.compile(r"^(?P<run>[A-Za-z0-9._-]{1,64}) (?P<passed>[0-9]{1,6})/(?P<cases>[0-9]{1,6})$")
# A gate run ends in one of these; `started` (written once the image is built, with
# RELEASE_IMAGE) opens it. `rolled_back`/`rollback_failed` are `release --rollback` runs.
_OUTCOMES = {"promoted", "eval_failed", "failed"}
_ROLLBACKS = {"rolled_back", "rollback_failed"}
_TEXT_LIMIT = 300


class BadListing(ValueError):
    """`list --all --json` printed something other than its documented shape."""


def _real(path) -> str | None:
    if not isinstance(path, str) or not os.path.isabs(path):
        return None
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return None


def link_projects(listing, projects: list[dict]) -> dict:
    """`list --all --json` with `console_project` (a registered project's id, or None) on each
    deployment: the row's recorded project path, resolved, and its environment must match a
    registered project's. Raises BadListing for anything but schema 1's shape, so a changed or
    broken answer is an error, never an empty list."""
    if (
        not isinstance(listing, dict)
        or listing.get("schema_version") != 1
        or not isinstance(listing.get("deployments"), list)
        or not isinstance(listing.get("skipped", []), list)
    ):
        raise BadListing("`list --all --json` printed an unexpected answer")
    known = {}
    for project in projects:
        path = _real(project.get("path"))
        if path:
            known.setdefault((path, project.get("environment")), project["id"])
    rows = []
    for row in listing["deployments"]:
        if not isinstance(row, dict):
            continue
        path = _real(row.get("project"))
        linked = known.get((path, row.get("environment"))) if path else None
        rows.append({**row, "console_project": linked})
    skipped = [entry for entry in listing.get("skipped", []) if isinstance(entry, dict)]
    return {"deployments": rows, "skipped": skipped}


def _text(value) -> str | None:
    return value[:_TEXT_LIMIT] if isinstance(value, str) else None


def _release(row: dict) -> dict:
    reasons = [r for r in row.get("reasons") or [] if isinstance(r, dict)]
    by_code = {r.get("code"): r.get("message") for r in reasons}
    evaluation = None
    match = _EVAL.match(by_code.get("RELEASE_EVAL") or "")
    if match:
        evaluation = {
            "run": match["run"],
            "passed": int(match["passed"]),
            "cases": int(match["cases"]),
        }
    threshold = None
    try:
        threshold = float(by_code.get("RELEASE_THRESHOLD") or "")
    except ValueError:
        pass
    problem = None
    if row.get("decision") == "failed":
        first = next(
            (r for r in reasons if not str(r.get("code", "")).startswith("RELEASE_")), None
        )
        if first:
            problem = {"code": _text(first.get("code")), "message": _text(first.get("message"))}
    return {
        "seq": row.get("seq"),
        "started": None,
        "at": _text(row.get("at")),
        "decision": row.get("decision"),
        "actor": _text(row.get("actor")),
        "actor_verified": bool(row.get("actor_verified")),
        "score": evaluation,
        "threshold": threshold,
        "image": _text(by_code.get("RELEASE_IMAGE")),
        "manifest": _text(by_code.get("RELEASE_MANIFEST")),
        "problem": problem,
    }


def releases(rows) -> list[dict]:
    """Release runs from `audit list --json` rows, newest first. Each outcome row is paired with
    the `started` row before it (a release holds the release lock, so runs do not interleave):
    a `failed` row records only its error, and the image comes from `started`. A `started` row
    with no outcome after it is `incomplete` (the candidate failed to start, the run was
    interrupted, or it is still running). Rollbacks are their own entries."""
    if not isinstance(rows, list):
        return []
    ordered = sorted(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("action") == "release"
            and isinstance(row.get("seq"), int)
        ),
        key=lambda row: row["seq"],
    )
    found: list[dict] = []
    opened: dict | None = None
    for row in ordered:
        decision = row.get("decision")
        if decision == "started":
            if opened is not None:
                found.append({**opened, "decision": "incomplete"})
            opened = _release(row)
        elif decision in _OUTCOMES:
            entry = _release(row)
            if opened is not None:
                entry["image"] = entry["image"] or opened["image"]
                entry["started"] = opened["at"]
            found.append(entry)
            opened = None
        elif decision in _ROLLBACKS:
            found.append(_release(row))
    if opened is not None:
        found.append({**opened, "decision": "incomplete"})
    return sorted(found, key=lambda entry: entry["seq"], reverse=True)
