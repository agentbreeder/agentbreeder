"""`agentbreeder audit verify|list`: inspect and verify the local audit chain."""

from __future__ import annotations

import json
from pathlib import Path

from agentbreeder import cli
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


def run(args) -> int:
    return cli.run_stateful(args, lambda store: _audit(store, args), existing=True, service=False)


def _audit(store: Store, args) -> int:
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
    rows = store.audit_rows(limit=max(1, min(args.limit, 1000)), attempt_id=args.attempt)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            reason = (row["reasons"] or [{}])[0].get("code", "")
            print(
                f"{row['seq']:>5} {row['at']} {row['attempt_id'][:8]} {row['action']} "
                f"{row['decision']} {reason} actor={row['actor']}"
                f"{'' if row['actor_verified'] else ' (unverified)'}"
            )
    return 0
