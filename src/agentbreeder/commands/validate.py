"""`agentbreeder validate`: check a deploy manifest, an agent file or a stack file."""

from __future__ import annotations

import sys
from pathlib import Path

from agentbreeder import agentfile, cli
from agentbreeder.manifest import ManifestError, load_manifest

NAMES = ("validate",)


def register(commands) -> None:
    validate = commands.add_parser(
        "validate", help="Validate a deploy manifest or an agent file (kind: agent)"
    )
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--json", action="store_true", help="Print the validated manifest")


def run(args) -> int:
    kind = cli._file_kind(args.manifest)
    if kind in ("agent", "legacy"):
        try:
            agent = agentfile.load_agent_file(args.manifest)
        except agentfile.AgentFileError as error:
            print(f"Validation failed: {error}", file=sys.stderr)
            return 2
        print(
            agent.model_dump_json(indent=2)
            if args.json
            else f"Valid agent file: {agent.name} ({len(agent.refs())} registry refs). "
            "Build it with `agentbreeder build`."
        )
        return 0
    if kind == "stack":
        from agentbreeder.services.schema import ServiceError, load_stack

        try:
            stack = load_stack(args.manifest)
        except ServiceError as error:
            print(f"Validation failed: {error}", file=sys.stderr)
            return 2
        print(
            stack.model_dump_json(indent=2)
            if args.json
            else f"Stack file schema is valid: {len(stack.services)} services. "
            "`agentbreeder services up` checks references and starts them."
        )
        return 0
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as error:
        print(f"Validation failed: {error}", file=sys.stderr)
        return 2
    print(
        manifest.model_dump_json(indent=2)
        if args.json
        else f"Valid manifest: {manifest.name} (schema {manifest.schema_version}). "
        "Nothing deployed."
    )
    return 0
