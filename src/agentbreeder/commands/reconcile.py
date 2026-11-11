"""`agentbreeder reconcile`: re-verify principals and retry blocked cleanup."""

from __future__ import annotations

import json

from agentbreeder import cli
from agentbreeder.deployer import resolve_deployer

NAMES = ("reconcile",)


def register(commands) -> None:
    reconcile = cli.stateful_parser(
        commands, "reconcile", "Re-verify principals and retry blocked cleanup", local=True
    )
    cli.add_stack(reconcile, environment=True)


def run(args) -> int:
    def action(service, operator) -> int:
        results = service.reconcile(resolve_deployer(operator))
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            for item in results:
                print(f"{item['id']}  {item['name']}  {item['compliance']}")
        return _reconcile_exit_code(results)

    return cli.run_stateful(args, action, existing=True)


def _reconcile_exit_code(results: list[dict]) -> int:
    """1 when cleanup still fails (a blocked deployment, or gateway keys that could not be
    revoked); 4 when any deployment is noncompliant or cannot be verified (so scripts and CI can
    gate on drift); otherwise 0."""
    compliance = [r["compliance"] or "" for r in results]
    if any(
        c.endswith(":cleanup_failed")
        or c.startswith("gateway_unreachable:")
        or c == "gateway_credential_error"
        for c in compliance
    ):
        return 1
    if any(c.startswith(("noncompliant:", "unverifiable:")) for c in compliance):
        return 4
    return 0
