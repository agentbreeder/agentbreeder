"""`agentbreeder remove`: remove a deployment."""

from __future__ import annotations

from agentbreeder import cli
from agentbreeder.deployer import resolve_deployer

NAMES = ("remove",)


def register(commands) -> None:
    remove = cli.stateful_parser(commands, "remove", "Remove a deployment", "id", cluster=True)
    cli.add_stack(remove, environment=True)


def run(args) -> int:
    def action(service, operator) -> int:
        return cli._print_deployment(
            service.remove(args.id, local=args.local, actor=resolve_deployer(operator)),
            args,
            "remove",
        )

    return cli.run_stateful(args, action, existing=True)
