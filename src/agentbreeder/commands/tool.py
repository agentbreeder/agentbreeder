"""`agentbreeder tool log REF`: a registry tool's calls from the stack's database (M12 spec
delta §4, #828). Read-only: one read-only transaction per credential, nothing written.

The query and the credential choice live in `agentbreeder.toollog`; the stack, environment,
host-form secrets and read-only connection in `agentbreeder.dbread`. This module adds the
Phoenix URL and prints.
Errors are `[CODE] message` on stderr (with `--json` also the `{"schema_version": 1, "error"}`
object on stdout), exit 2.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from agentbreeder import dbread, registry, safe_text, toollog
from agentbreeder.redaction import Redactor
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import StackState
from agentbreeder.toollog import ToolLogError

NAMES = ("tool",)
PHOENIX = "service/phoenix@"
CONTENT_NOTE = "Arguments are customer data."


def register(commands) -> None:
    tool = commands.add_parser(
        "tool", help="List a registry tool's recorded calls from the stack's database (read-only)"
    )
    tool_commands = tool.add_subparsers(dest="tool_command", required=True)
    log = tool_commands.add_parser(
        "log",
        help="List a tool's recorded calls from the stack's database (read-only)",
        description=(
            "List the calls the agent runtime recorded for a registry tool, newest first. The "
            "log is what the runtime claims it called; results are not stored (see the trace)."
        ),
    )
    log.add_argument("ref", metavar="REF", help="tool/NAME (every version) or tool/NAME@X.Y.Z")
    log.add_argument(
        "--project", type=Path, default=Path("."), metavar="DIR", help="The stack's directory"
    )
    log.add_argument(
        "--environment", metavar="ENV", help="Whose stack state to use (default: the only one)"
    )
    log.add_argument(
        "--since",
        metavar="D",
        help="90s, 10m, 2h or 2026-11-11T09:00:00Z (default 24h, at most 720 hours back)",
    )
    log.add_argument("--agent", metavar="NAME", help="Only this agent's calls (runtime >= 1.2.0)")
    log.add_argument("--limit", default=str(toollog.DEFAULT_LIMIT), help="1 to 1000 (default 100)")
    log.add_argument("--content", action="store_true", help="Also show arguments and error text")
    log.add_argument("--json", action="store_true")


def run(args) -> int:
    try:
        return _log(args)
    except ToolLogError as error:
        print(str(error), file=sys.stderr)
        if args.json:
            message = str(error).removeprefix(f"[{error.code}] ")
            body = {"code": error.code, "message": message}
            print(json.dumps({"schema_version": 1, "error": body}), flush=True)
        return 2


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _phoenix_url(stack, state: StackState | None) -> str | None:
    if state is None:
        return None
    ports = state.ports()
    for name, service in sorted(stack.services.items()):
        port = ports.get(name, {}).get("http")
        if service.ref.startswith(PHOENIX) and port:
            return f"http://127.0.0.1:{port}"
    return None


def _log(args) -> int:
    target = toollog.parse_ref(args.ref)
    now = _now()
    since = toollog.parse_since(args.since, now)
    limit = toollog.check_limit(args.limit)
    project = args.project.resolve()
    stack = dbread.load_stack(project)
    if args.agent is not None and args.agent not in stack.environment.agents:
        raise ToolLogError(
            "AGENT_NOT_FOUND",
            f"{safe_text.excerpt(args.agent, 63)!r} is not in the stack's environment.agents "
            f"({', '.join(sorted(stack.environment.agents))})",
        )
    db = dbread.resolve(stack, project, args.environment, prefix=toollog.PREFIX)
    try:
        state = StackState.open_directory(db.directory)
    except ServiceError as error:
        raise ToolLogError(error.code, str(error)) from None
    try:
        phoenix = _phoenix_url(stack, state)
    finally:
        if state is not None:
            state.close()
    query = toollog.Query(
        target=target,
        since=since,
        limit=limit,
        content=args.content,
        agent=args.agent,
        legacy=toollog.legacy_actions(target, registry.roots(project)),
    )
    if args.content:
        print(f"Note: {CONTENT_NOTE}", file=sys.stderr)
    redact = Redactor(dbread.redaction(db) if args.content else [])
    result = toollog.fetch(db.agent_url, db.console_url, query)
    calls = [toollog.shape(row, args.content, redact) for row in result.rows]
    notes = _notes(calls, result, args.agent)
    document = {
        "schema_version": 1,
        "ref": target.ref,
        "environment": db.environment,
        "database": db.database,
        "credential": result.credential,
        "schema": result.schema,
        "window": {"since": _iso(since), "until": _iso(now)},
        "limit": limit,
        "truncated": result.truncated,
        "content": args.content,
        "db_role_verified": result.db_role_verified,
        "phoenix_url": phoenix,
        "phantom_rows": result.phantom_rows,
        "notes": notes,
        "calls": calls,
    }
    if args.json:
        print(json.dumps(document, indent=2))
    else:
        _print(document, args.since)
    return 0


def _notes(calls: list[dict], result: toollog.Result, agent: str | None) -> list[str]:
    notes = []
    old = sum(1 for call in calls if call["agent"] is None)
    if old:
        notes.append(
            f"{old} row{'s' if old != 1 else ''} without an agent (written before runtime/adk "
            "1.2.0, or not by the runtime) ha" + ("ve" if old != 1 else "s") + " no agent or "
            "latency"
        )
    if agent:
        notes.append("--agent leaves out rows written before runtime/adk 1.2.0 (no agent)")
    if result.phantom_rows:
        notes.append(
            f"{result.phantom_rows} duplicate ok row{'s' if result.phantom_rows != 1 else ''} "
            "after failed calls from runtime/adk 1.0.6/1.1.0 left out (#922)"
        )
    if any(call["tool"] is None and call["status"] == "error" for call in calls):
        notes.append(
            "Failed calls from before runtime/adk 1.2.0 carry no tool version (shown as -) and "
            "are matched by function name, which another tool may share"
        )
    if result.credential == "agent":
        notes.append("Read with the agent's own credential (the agent owns these tables)")
    if result.schema != "public":
        notes.append(
            f"Read from the agent role's own schema {result.schema}, which it controls; "
            "tables of the same name in public are not shown"
        )
    if not result.db_role_verified:
        notes.append("db_role is not verified: no owner trigger in force now")
    return notes


def _cell(value) -> str:
    return "-" if value is None else str(value)


def _print(document: dict, since: str | None) -> None:
    text = since or toollog.DEFAULT_SINCE
    window = f"since {document['window']['since']}" if "T" in text else f"last {text}"
    print(
        f"{document['ref']}, {window}, newest first (database {document['database']}, schema "
        f"{document['schema']}, environment {document['environment']}, "
        f"{document['credential']} credential)"
    )
    columns = ("time", "agent", "version", "function", "status", "latency", "turn id")
    columns += ("trace id", "db role")
    rows = []
    for call in document["calls"]:
        version = call["tool"].split("@", 1)[1] if call["tool"] else None
        role = call["db_role"]
        if role == dbread.CONSOLE_ROLE:
            role = f"{role} (console)"
        latency = f"{call['latency_ms']} ms" if call["latency_ms"] is not None else None
        rows.append(
            [
                call["at"],
                call["agent"],
                version,
                call["function"],
                call["status"],
                latency,
                call["turn_id"],
                call["trace_id"],
                role,
            ]  # fmt: skip
        )
    if not rows:
        print("No calls in this window.")
    else:
        cells = [list(columns)] + [[_cell(v) for v in row] for row in rows]
        widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
        for row in cells:
            print("  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)).rstrip())
    if document["content"]:
        for call in document["calls"]:
            print(f"\n{_cell(call['at'])} {_cell(call['function'])}")
            print(f"  args: {json.dumps(call['args'], ensure_ascii=True)}")
            if call["error"] is not None:
                print(f"  error: {json.dumps(call['error'], ensure_ascii=True)}")
    if document["truncated"]:
        print("… more; narrow --since or raise --limit")
    for note in document["notes"]:
        print(f"Note: {note}")
    if document["phoenix_url"]:
        print(f"Phoenix: {document['phoenix_url']} (look up a trace id there)")
