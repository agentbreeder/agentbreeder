"""`agentbreeder audit verify|list`: inspect and verify the local audit chain.

Both first import the MCP relay's new tool-call records for a stack environment's state
(`relay_audit`, #1068); a failed import is a warning on stderr, never a failed read."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from agentbreeder import cli, relay_audit
from agentbreeder.store import Store

NAMES = ("audit",)


def register(commands) -> None:
    audit = commands.add_parser("audit", help="Inspect and verify the local audit chain")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    for name in ("verify", "list"):
        command = audit_commands.add_parser(name)
        command.add_argument("--state-dir", type=Path, default=cli.DEFAULT_STATE)
        command.add_argument("--json", action="store_true")
        if name == "list":
            command.add_argument("--limit", type=int, default=20)
            command.add_argument("--attempt")
            command.add_argument(
                "--before",
                metavar="SEQ",
                help="Only rows older than this seq (the next page after a row's seq)",
            )


SEQ = re.compile(r"[1-9][0-9]{0,17}")


def run(args) -> int:
    if args.audit_command == "list" and args.before is not None:
        if not SEQ.fullmatch(args.before):
            return _refuse("BEFORE_INVALID", "--before must be a positive whole number (a seq)")
        if args.attempt:
            return _refuse("BEFORE_INVALID", "--before pages the whole log; drop --attempt")
    return cli.run_stateful(args, lambda store: _audit(store, args), existing=True, service=False)


def _audit(store: Store, args) -> int:
    report, warning = relay_audit.safe_import(store)
    if warning:
        print(f"Warning: {warning}", file=sys.stderr)
    elif report and report.invalid:
        print(
            f"Warning: {report.invalid} MCP relay line(s) were not relay records and were not "
            f"imported (recorded as {relay_audit.INVALID_ACTION} rows)",
            file=sys.stderr,
        )
    if args.audit_command == "verify":
        report = store.verify_audit()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(
                f"{'OK' if report['ok'] else 'INVALID'}: {report['rows']} rows, head "
                f"{report['head'][:16]}…"
                + (
                    f"; first invalid seq {report['first_invalid_seq']} ({report['problem']})"
                    if not report["ok"]
                    else ""
                )
            )
            if report["incomplete_attempts"]:
                print(f"Incomplete attempts: {', '.join(report['incomplete_attempts'])}")
            print(f"Limitation: {report['limitations']}")
        return 0 if report["ok"] else 1
    before = int(args.before) if args.before is not None else None
    rows = store.audit_rows(
        limit=max(1, min(args.limit, 1000)), attempt_id=args.attempt, before=before
    )
    if args.json:
        # `agent`: the name of the row's deployment (removed ones included), else null.
        names = {row["id"]: row["name"] for row in store.summaries()}
        for row in rows:
            row["agent"] = names.get(row["deployment_id"]) if row["deployment_id"] else None
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            reason = (row["reasons"] or [{}])[0].get("code", "")
            print(
                f"{row['seq']:>5} {row['at']} {row['attempt_id'][:8]} {row['action']} "
                f"{row['decision']} {reason} actor={row['actor']}"
                f"{'' if row['actor_verified'] else ' (unverified)'}{_tool(row)}"
            )
    return 0


def _tool(row: dict) -> str:
    """` mcp=<server>/<tool>` for a relay tool call (an ungranted tool by its hash prefix)."""
    if row["action"] != relay_audit.ACTION or not isinstance(row.get("context"), dict):
        return ""
    context = row["context"]
    tool = context.get("tool") or f"ungranted:{context.get('tool_sha256')}"
    return f" mcp={context.get('server')}/{tool}"


def _refuse(code: str, message: str) -> int:
    print(f"[{code}] {message}", file=sys.stderr)
    return 2
