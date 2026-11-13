"""`agentbreeder release`: build, deploy a candidate, run the eval gate and promote the exact
image if it passes (or `--rollback` to the previous production)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentbreeder import agentfile, cli, hints, registry, secret_files
from agentbreeder.deployer import DeployerError, os_deployer, resolve_deployer
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.operator_config import OperatorConfigError, load_operator_config
from agentbreeder.runtime import Docker
from agentbreeder.store import DeploymentError, Store

NAMES = ("release",)


def register(commands) -> None:
    rel = commands.add_parser(
        "release",
        help="Build, deploy a candidate, run the eval gate, promote the exact image if it passes",
    )
    rel.add_argument("agent")
    rel.add_argument("--environment")
    rel.add_argument("--threshold", type=float, default=None)
    rel.add_argument("--retries", type=int, default=None)
    rel.add_argument("--rollback", action="store_true", help="Redeploy the previous production")
    rel.add_argument("--state-dir", type=Path, default=cli.DEFAULT_STATE)
    rel.add_argument("--output", type=Path)
    rel.add_argument("--json", action="store_true")
    cli.add_stack(rel)


def run(args) -> int:
    from agentbreeder import evals
    from agentbreeder.build import BuildError
    from agentbreeder.release import ReleaseError, release, rollback

    store = None
    where = _where(args)
    try:
        operator = load_operator_config()
        store = Store(args.state_dir)
        service = DeploymentService(store, Docker(), operator)
        try:
            deployer = resolve_deployer(operator)
        except DeployerError as error:
            service.record_denial("release", os_deployer(), error.code, str(error))
            raise
        if args.rollback:
            outcome = rollback(
                Path(args.agent),
                service,
                deployer,
                args.environment,
                output=args.output,
                where=where,
            )
        else:
            outcome = release(
                Path(args.agent),
                operator,
                service,
                deployer,
                args.environment,
                threshold=args.threshold,
                retries=args.retries,
                output=args.output,
                where=where,
                log=(lambda message: None)
                if args.json
                else (lambda message: print(message, file=sys.stderr)),
            )
    except (
        BuildError,
        ReleaseError,
        evals.EvalError,
        agentfile.AgentFileError,
        registry.RegistryError,
        OperatorConfigError,
        DeployerError,
        GateDenied,
        DeploymentError,
        secret_files.SecretError,
    ) as error:
        print(cli._message(error, args=args, near=Path(args.agent)), file=sys.stderr)
        return 3 if getattr(error, "code", "") == "APPROVAL_REQUIRED" else 2
    finally:
        if store:
            store.close()
    if outcome.code == 5 and outcome.details.get("eval"):  # the eval gate failed (#827)
        from agentbreeder import events

        project = args.stack if args.stack is not None else Path(args.agent)
        events.record(lambda: events.gate_failed(events.project_dir(project), outcome.details))
    advice = _advice(args, where, outcome.details)
    if advice:
        outcome.details["advice"] = advice
    if args.json:
        print(json.dumps(outcome.as_json(), indent=2))
    else:
        print(outcome.message)
        production = outcome.details.get("production")
        if production and production.get("endpoint"):
            print(f"Production: {production['endpoint']} ({production['id']})")
        for case in outcome.details.get("failed_cases") or []:
            checks = ", ".join(case["failed_checks"]) or "none"
            reason = f"; judge: {case['reason']}" if case["reason"] else ""
            print(f"FAIL {case['id']}: failed {checks}{reason}")
        if outcome.details.get("eval_report"):
            print(f"Eval report: {outcome.details['eval_report']}")
        if outcome.details.get("candidate_log"):
            print(
                f"Candidate log: {outcome.details['candidate_log']} "
                f"(show it: `{advice['show_log']}`)"
            )
        elif outcome.details.get("candidate_log_skipped"):
            print(f"Note: {outcome.details['candidate_log_skipped']}", file=sys.stderr)
        if "remove" in advice:
            rollback = f", or roll back: `{advice['rollback']}`" if "rollback" in advice else ""
            print(hints.next_note(f"remove that production: `{advice['remove']}`{rollback}"))
        if outcome.details.get("failed_cases"):
            print(
                hints.next_note(
                    "edit the prompt or eval set, publish it as a new version, reference that "
                    f"version in agent.yaml, then run `{advice['build']}` and release again"
                )
            )
    return outcome.code


def _where(args) -> hints.Where:
    """The --stack (and its environment) or non-default --state-dir this release ran with."""
    if args.stack is not None:
        return hints.Where(stack=args.stack, environment=args.environment)
    custom = args.state_dir if args.state_dir != cli.DEFAULT_STATE else None
    return hints.Where(environment=args.environment, state_dir=custom)


def _advice(args, where: hints.Where, details: dict) -> dict:
    """Commands that work as printed, in the same place this release ran."""
    advice: dict = {}
    if details.get("candidate_log"):
        advice["show_log"] = where.command("logs", "--failed", details["agent"])
    unreleased = details.get("unreleased_production")
    if unreleased:
        advice["remove"] = where.command("remove", unreleased["id"], "--local")
        if unreleased.get("rollback"):
            advice["rollback"] = where.command("release", args.agent, "--rollback")
    if details.get("failed_cases"):
        advice["build"] = where.command("build", args.agent)
    return advice
