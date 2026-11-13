"""`agentbreeder events [--project DIR] [--limit N] [--unread] [--json]`: stored events, newest
first (#827, M11; guided §14.4). `--mark-read ID` (repeatable) and `--mark-all-read` mark them
read. With `--project`, the JSON also carries that project's live conditions, computed by the
function behind `agentbreeder next` and never stored (`conditions`; null with
`conditions_error` when the checklist could not be computed).

Listing opens the store read-only and creates nothing. Errors are `[CODE] message` on stderr,
with `--json` also `{"schema_version": 1, "error": {...}}`, exit 2. See `docs/events.md`.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

NAMES = ("events",)
SCHEMA_VERSION = 1
ENVIRONMENT = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")


def _limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number") from None
    if not 1 <= number <= 500:
        raise argparse.ArgumentTypeError("must be from 1 to 500")
    return number


def _identifier(value: str) -> int:
    if not value.isascii() or not value.isdigit() or len(value) > 12:
        raise argparse.ArgumentTypeError("must be an event id")
    return int(value)


def register(commands) -> None:
    command = commands.add_parser(
        "events",
        help="List stored events (failed jobs, eval gate failures, containers that exited)",
    )
    command.add_argument("--project", type=Path, metavar="DIR", help="Only this project's events")
    command.add_argument(
        "--environment",
        default="dev",
        help="Environment for the project's conditions (default: dev)",
    )
    command.add_argument(
        "--limit", type=_limit, default=50, help="Most events to list, 1-500 (default: 50)"
    )
    command.add_argument("--unread", action="store_true", help="Only unread events")
    command.add_argument(
        "--mark-read", type=_identifier, action="append", metavar="ID", help="Mark an event read"
    )
    command.add_argument(
        "--mark-all-read",
        action="store_true",
        help="Mark every event read (with --project, that project's)",
    )
    command.add_argument("--json", action="store_true", help="Print JSON")


def _fail(args, code: str, message: str) -> int:
    print(f"[{code}] {message}", file=sys.stderr)
    if args.json:
        body = {"code": code, "message": message}
        print(json.dumps({"schema_version": SCHEMA_VERSION, "error": body}))
    return 2


def run(args) -> int:
    from agentbreeder import events

    if not ENVIRONMENT.fullmatch(args.environment):
        return _fail(
            args, "ENVIRONMENT_INVALID", "--environment must be [a-z0-9-], at most 32 characters"
        )
    project = str(args.project.resolve()) if args.project is not None else None
    try:
        if args.mark_read or args.mark_all_read:
            store = events.EventStore.open_readonly()
            if store is not None:
                store.close()
                store = events.EventStore.open()
                try:
                    store.mark_read(None if args.mark_all_read else args.mark_read, project)
                finally:
                    store.close()
        shown = events.read_recent(None, project, args.limit, args.unread)
        unread = events.read_unread(None, project)
    except events.EventError as error:
        return _fail(args, error.code, str(error).removeprefix(f"[{error.code}] "))
    except (sqlite3.Error, OSError):
        return _fail(args, "EVENTS_UNREADABLE", "cannot read the events store")
    body: dict = {"schema_version": SCHEMA_VERSION, "events": shown, "unread": unread}
    if project is not None:
        body["conditions"], error = _conditions(args.project, args.environment)
        if error:
            body["conditions_error"] = error
    if args.json:
        print(json.dumps(body, indent=2))
        return 0
    print(text(body))
    return 0


def _conditions(project: Path, environment: str) -> tuple[list | None, dict | None]:
    from agentbreeder import events, guide_checks

    try:
        return events.conditions(project, environment), None
    except guide_checks.GuideError as error:
        message = str(error).removeprefix(f"[{error.code}] ")
        return None, {"code": error.code, "message": message}
    except Exception as error:  # noqa: BLE001 - the type only, never a message with paths
        return None, {
            "code": "NEXT_FAILED",
            "message": f"the checklist could not be computed ({type(error).__name__})",
        }


def text(body: dict) -> str:
    from agentbreeder import safe_text

    lines = []
    if body.get("conditions"):
        lines.append("Needs attention:")
        lines += [f"! {safe_text.visible(c['message'])}" for c in body["conditions"]]
    elif body.get("conditions_error"):
        error = body["conditions_error"]
        lines.append(
            safe_text.visible(f"Needs attention: unknown ([{error['code']}] {error['message']})")
        )
    if body["events"]:
        lines.append(f"Recent events ({body['unread']} unread):")
        for event in body["events"]:
            mark = " " if event["read"] else "*"
            line = f"{mark} {event['id']:>5} {event['time']} {event['message']}"
            lines.append(safe_text.visible(line))
    else:
        lines.append("No events.")
    return "\n".join(lines)
