"""`agentbreeder disable` / `enable`: the principal kill switch."""

from __future__ import annotations

import json

from agentbreeder import cli
from agentbreeder.deployer import resolve_deployer

NAMES = ("disable", "enable")


def register(commands) -> None:
    for name, help_text, local in (
        ("disable", "Persist a principal blocker and stop its workloads", True),
        ("enable", "Clear a principal blocker (starts nothing)", False),
    ):
        command = cli.stateful_parser(commands, name, help_text, "target", local=local)
        command.add_argument("--environment")
        command.add_argument("--provider", help="Provider connection (with --ref)")
        command.add_argument("--ref", help="Principal reference (with --provider)")
        if name == "disable":
            command.add_argument("--reason", default="administrative disable")
            command.add_argument(
                "--idp", action="store_true", help="Also disable in the IdP (opt-in write)"
            )


def run(args) -> int:
    def action(service, operator) -> int:
        options = {"environment": args.environment, "provider": args.provider, "ref": args.ref}
        actor = resolve_deployer(operator)
        if args.command == "disable":
            result = service.disable(
                args.target, actor, reason=args.reason, idp=args.idp, **options
            )
        else:
            result = service.enable(args.target, actor, **options)
        print(json.dumps(result, indent=2) if args.json else _summary(result))
        return 1 if result["state"] == "failed" else 0

    # disable may set a blocker before any deployment exists; enable needs existing state.
    return cli.run_stateful(args, action, existing=args.command == "enable")


def _summary(result: dict) -> str:
    lines = [result["state"]]
    for key in ("stopped", "failed"):
        if result.get(key):
            lines.append(f"{key}: {json.dumps(result[key])}")
    return "\n".join(lines)
