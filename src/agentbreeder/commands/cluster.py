"""`agentbreeder cluster up|status|down`: a local kind cluster for one stack (preview)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentbreeder import cli, registry

NAMES = ("cluster",)


def register(commands) -> None:
    cluster = commands.add_parser("cluster", help="A local kind cluster for one stack (preview)")
    cluster_commands = cluster.add_subparsers(dest="cluster_command", required=True)
    for name, text in (
        ("up", "Create the cluster and registry, and add them to the stack's grant"),
        ("status", "Show the cluster, the registry and the tools they need"),
        ("down", "Delete the cluster and registry (refused while something is deployed there)"),
    ):
        item = cluster_commands.add_parser(name, help=text)
        item.add_argument("--stack", type=Path, default=Path("."), metavar="DIR")
        item.add_argument("--json", action="store_true")
        if name == "up":
            item.add_argument("--environment", required=True)
            item.add_argument("--yes", action="store_true", help="Confirm the changed grant")
            item.add_argument("--expect-grant", metavar="SHA256")


def run(args) -> int:
    from agentbreeder import cluster
    from agentbreeder.services import plan as stack_plan
    from agentbreeder.services.schema import ServiceError

    command = args.cluster_command
    log = (lambda message: None) if args.json else (lambda text: print(text, file=sys.stderr))
    try:
        if command == "up":

            def confirm(grant: dict) -> bool:
                print(stack_plan.grant_text(grant), file=sys.stderr)
                return True

            result = cluster.up(
                args.stack,
                args.environment,
                confirm=confirm if args.yes else None,
                expect_grant=args.expect_grant,
                log=log,
            )
        elif command == "status":
            result = cluster.status(args.stack)
        else:
            result = cluster.down(args.stack)
    except (ServiceError, registry.RegistryError) as error:
        print(cli._message(error), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0
