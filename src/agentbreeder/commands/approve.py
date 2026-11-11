"""`approve`/`reject` (pending deploy approvals) and `approve-call`/`reject-call` (held MCP tool
calls): second-person decisions."""

from __future__ import annotations

import json

from agentbreeder import cli
from agentbreeder.deployer import resolve_deployer

NAMES = ("approve", "reject", "approve-call", "reject-call")


def register(commands) -> None:
    for name in ("approve", "reject"):
        cli.stateful_parser(
            commands, name, f"{name.title()} a pending approval as a second person", "approval_id"
        )
    for name in ("approve-call", "reject-call"):
        verb = name.split("-")[0]
        cli.stateful_parser(
            commands, name, f"{verb.title()} one held MCP tool call as a second person", "held_id"
        )


def run(args) -> int:
    def action(service, operator) -> int:
        if args.command in ("approve", "reject"):
            result = service.decide(
                args.approval_id, resolve_deployer(operator), approve=args.command == "approve"
            )
            print(
                json.dumps(result, indent=2)
                if args.json
                else f"{result['id']}  {result['state']} by {result['checker']}"
            )
            return 0
        result = service.decide_call(
            args.held_id, resolve_deployer(operator), approve=args.command == "approve-call"
        )
        print(
            json.dumps(result, indent=2)
            if args.json
            else f"{result['id']}  {result['server']}/{result['tool']}  {result['state']} by "
            f"{result['checker']}"
        )
        return 0

    return cli.run_stateful(args, action, existing=True)
