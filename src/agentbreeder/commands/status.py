"""`agentbreeder status`: show a deployment and re-verify its principal."""

from __future__ import annotations

from agentbreeder import cli

NAMES = ("status",)


def register(commands) -> None:
    status = cli.stateful_parser(
        commands, "status", "Show a deployment and re-verify its principal", "id"
    )
    status.add_argument("--no-verify", action="store_true", help="Skip principal re-resolution")
    cli.add_stack(status, environment=True)


def run(args) -> int:
    def action(service, operator) -> int:
        return cli._print_deployment(
            service.status(args.id, verify=not args.no_verify), args, "status"
        )

    return cli.run_stateful(args, action, existing=True)
