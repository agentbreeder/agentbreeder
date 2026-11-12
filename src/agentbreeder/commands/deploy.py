"""`agentbreeder deploy`: gate and deploy a manifest."""

from __future__ import annotations

from pathlib import Path

from agentbreeder import cli
from agentbreeder.deployer import DeployerError, os_deployer, resolve_deployer
from agentbreeder.manifest import load_manifest
from agentbreeder.store import DeploymentError

NAMES = ("deploy",)


def register(commands) -> None:
    deploy = cli.stateful_parser(
        commands, "deploy", "Gate and deploy a manifest", "manifest", cluster=True
    )
    deploy.add_argument("--environment", help="Operator-defined environment name")
    cli.add_stack(deploy)


def run(args) -> int:
    manifest = None

    def before() -> None:
        nonlocal manifest
        if cli._file_kind(Path(args.manifest)) == "agent":
            raise DeploymentError(
                f"[WRONG_FILE_KIND] {args.manifest} is an agent file; run "
                f"`agentbreeder build {args.manifest}` and deploy the manifest it writes"
            )
        manifest = load_manifest(Path(args.manifest))

    def action(service, operator) -> int:
        try:
            deployer = resolve_deployer(operator)
        except DeployerError as error:
            service.record_denial("deploy", os_deployer(), error.code, str(error))
            raise
        result = service.deploy(
            manifest, local=args.local, environment=args.environment, deployer=deployer
        )
        return cli._print_deployment(result, args, "deploy")

    return cli.run_stateful(args, action, before=before, near=Path(args.manifest))
