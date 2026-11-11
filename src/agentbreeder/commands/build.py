"""`agentbreeder build`: build an agent file into an image and a deploy manifest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentbreeder import agentfile, cli, registry
from agentbreeder.operator_config import OperatorConfigError, load_operator_config
from agentbreeder.store import DeploymentError

NAMES = ("build",)


def register(commands) -> None:
    build = commands.add_parser(
        "build", help="Build an agent file into an image and a deploy manifest (never deploys)"
    )
    build.add_argument("agent")
    build.add_argument("--environment", help="Operator environment to build the manifest for")
    lock_mode = build.add_mutually_exclusive_group()
    lock_mode.add_argument("--locked", action="store_true", help="Refuse any agent.lock change")
    lock_mode.add_argument(
        "--update",
        nargs="*",
        metavar="REF",
        help="Accept new hashes for path refs (all or listed)",
    )
    build.add_argument("--output", type=Path, help="Where to write manifests (default ./deploy)")
    build.add_argument("--json", action="store_true")
    cli.add_stack(build)


def run(args) -> int:
    from agentbreeder.build import BuildError, build

    mode = "locked" if args.locked else "update" if args.update is not None else "plain"
    try:
        result = build(
            Path(args.agent),
            load_operator_config(),
            args.environment,
            mode=mode,
            update=set(args.update or []),
            output=args.output,
        )
    except (
        BuildError,
        agentfile.AgentFileError,
        registry.RegistryError,
        OperatorConfigError,
        DeploymentError,
    ) as error:
        print(cli._message(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.as_json(), indent=2))
        return 0
    print(f"Built {result.name}: {result.image}")
    print(f"Manifest: {result.manifest_path}")
    if result.candidate_manifest_path:
        print(f"Candidate manifest: {result.candidate_manifest_path}")
    print(f"Lock: {result.lock_path} ({'updated' if result.lock_changed else 'unchanged'})")
    environment = f" --environment {args.environment}" if args.environment else ""
    print(f"Next: agentbreeder deploy {result.manifest_path} --local{environment}")
    return 0
