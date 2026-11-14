"""`agentbreeder agent set-ref OLD NEW`: move the project's agent to another registry version."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentbreeder import registry, setref

NAMES = ("agent",)


def register(commands) -> None:
    agent = commands.add_parser("agent", help="Change the project's agent.yaml")
    agent_commands = agent.add_subparsers(dest="agent_command", required=True)
    set_ref = agent_commands.add_parser(
        "set-ref",
        help="Replace one registry ref in agent.yaml (exact, shown as a diff first)",
        description="Replace OLD with NEW in DIR/agent.yaml: OLD must be a ref field's value "
        "and occur once in the file; NEW must be the same kind and resolve. Then build and "
        "release.",
    )
    set_ref.add_argument("old", metavar="OLD", help="kind/name@X.Y.Z the agent uses now")
    set_ref.add_argument("new", metavar="NEW", help="kind/name@X.Y.Z to use instead")
    set_ref.add_argument("--project", type=Path, default=Path("."), metavar="DIR")
    mode = set_ref.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Show the diff; write nothing")
    mode.add_argument(
        "--expect-base",
        type=_digest,
        metavar="SHA256",
        help="Refuse unless agent.yaml is still this digest (from --dry-run)",
    )
    set_ref.add_argument("--json", action="store_true")


def run(args) -> int:
    from agentbreeder import hints

    try:
        result = setref.set_ref(
            args.project,
            args.old,
            args.new,
            expect_base=args.expect_base,
            dry_run=args.dry_run,
        )
    except registry.RegistryError as error:
        print(registry.clean(str(error))[0], file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(result["diff"], end="")
    if result["dry_run"]:
        print(f"Would set {result['at']} to {result['new']} (dry run: nothing written)")
        print(f"To write exactly this: --expect-base {result['base_sha256']}")
        return 0
    print(f"Set {result['at']} to {result['new']} in agent.yaml (was {result['old']})")
    # build, then release; `next` prints them with this project's stack and environment.
    print(hints.next_line("next", args.project))
    return 0


def _digest(text: str) -> str:
    if not registry.DIGEST.fullmatch(text):
        raise argparse.ArgumentTypeError("must be sha256:<64 hex digits>")
    return text
