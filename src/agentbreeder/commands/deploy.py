"""`agentbreeder deploy`: gate and deploy a manifest, or a released `agent/<name>@X.Y.Z`
package (M13 #763; `deploy_ref.py`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentbreeder import cli, deploy_ref, hints, registry
from agentbreeder.deployer import DeployerError, os_deployer, resolve_deployer
from agentbreeder.manifest import load_manifest
from agentbreeder.store import DeploymentError

NAMES = ("deploy",)


def register(commands) -> None:
    deploy = cli.stateful_parser(
        commands,
        "deploy",
        "Gate and deploy a manifest, or a released agent/NAME@X.Y.Z package",
        "manifest",
        cluster=True,
    )
    deploy.add_argument("--environment", help="Operator-defined environment name")
    deploy.add_argument(
        "--project",
        type=Path,
        metavar="DIR",
        help="With agent/NAME@X.Y.Z: the project whose ./registry holds it (default: .)",
    )
    cli.add_stack(deploy)


def _where(args) -> hints.Where:
    if args.stack is not None:
        return hints.Where(stack=args.stack, environment=args.environment)
    custom = args.state_dir if args.state_dir != cli.DEFAULT_STATE else None
    return hints.Where(environment=args.environment, state_dir=custom)


def run(args) -> int:
    manifest = None
    ref = None

    def before() -> None:
        nonlocal manifest, ref
        ref = deploy_ref.parse(args.manifest)  # parses only; the store is not open yet
        if ref is not None:
            return
        if args.project is not None:
            raise DeploymentError("[PROJECT_UNUSED] --project is for agent/NAME@X.Y.Z refs only")
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
        if ref is None:
            result = service.deploy(
                manifest, local=args.local, environment=args.environment, deployer=deployer
            )
            return cli._print_deployment(result, args, "deploy")
        try:
            prepared = deploy_ref.prepare(
                ref,
                args.project or Path.cwd(),
                service,
                operator,
                args.environment,
                cluster=args.cluster,
                where=_where(args),
            )
        except deploy_ref.PackageDeployError as error:
            if error.code != "AUDIT_CORRUPT":  # never append to a chain that fails to verify
                # The ref and code only: some messages name a registry root (a path).
                service.record_denial("deploy", deployer, error.code, f"{ref}: {error.code}")
            print(cli._message(error, args=args, near=near), file=sys.stderr)
            if args.json:
                print(json.dumps({"code": error.code, "error": str(error)}, indent=2))
            return error.exit
        for note in prepared.notes:
            print(f"Note: {note}", file=sys.stderr)
        result = service.deploy(
            prepared.manifest,
            local=True,
            environment=prepared.environment,
            deployer=deployer,
            package=(prepared.ref, prepared.sha256),
        )
        return cli._print_deployment(result, args, "deploy")

    near = Path(args.manifest)
    if registry.REF.fullmatch(args.manifest):  # a ref: hints look beside the project
        near = (args.project or Path(".")) / "agent.yaml"
    return cli.run_stateful(args, action, before=before, near=near)
