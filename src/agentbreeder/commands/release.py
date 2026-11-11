"""`agentbreeder release`: build, deploy a candidate, run the eval gate and promote the exact
image if it passes (or `--rollback` to the previous production)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentbreeder import agentfile, cli, registry, secret_files
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
                Path(args.agent), service, deployer, args.environment, output=args.output
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
        print(cli._message(error), file=sys.stderr)
        return 3 if getattr(error, "code", "") == "APPROVAL_REQUIRED" else 2
    finally:
        if store:
            store.close()
    if args.json:
        print(json.dumps(outcome.as_json(), indent=2))
    else:
        print(outcome.message)
        production = outcome.details.get("production")
        if production and production.get("endpoint"):
            print(f"Production: {production['endpoint']} ({production['id']})")
        if outcome.details.get("eval_report"):
            print(f"Eval report: {outcome.details['eval_report']}")
    return outcome.code
