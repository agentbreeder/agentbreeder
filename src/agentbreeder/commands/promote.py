"""`agentbreeder promote`: deploy the released image to Kubernetes without rebuilding it."""

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

NAMES = ("promote",)


def register(commands) -> None:
    pro = commands.add_parser(
        "promote", help="Deploy the released image to Kubernetes, without rebuilding it"
    )
    pro.add_argument("agent")
    pro.add_argument("--environment")
    pro.add_argument("--to", choices=["kubernetes"], required=True)
    pro.add_argument(
        "--skip-cluster-evals",
        action="store_true",
        help="Skip the evals on the cluster (recorded in the audit log; the release gate stands)",
    )
    pro.add_argument("--state-dir", type=Path, default=cli.DEFAULT_STATE)
    pro.add_argument("--output", type=Path)
    pro.add_argument("--json", action="store_true")
    cli.add_stack(pro)


def run(args) -> int:
    from agentbreeder import evals
    from agentbreeder.build import BuildError
    from agentbreeder.promote import promote
    from agentbreeder.release import ReleaseError

    store = None
    try:
        operator = load_operator_config()
        store = Store(args.state_dir)
        service = DeploymentService(store, Docker(), operator)
        try:
            deployer = resolve_deployer(operator)
        except DeployerError as error:
            service.record_denial("promote", os_deployer(), error.code, str(error))
            raise
        outcome = promote(
            Path(args.agent),
            operator,
            service,
            deployer,
            args.environment,
            skip_cluster_evals=args.skip_cluster_evals,
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
        return 2
    finally:
        if store:
            store.close()
    print(json.dumps(outcome.as_json(), indent=2) if args.json else outcome.message)
    return outcome.code
