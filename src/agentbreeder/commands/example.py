"""`agentbreeder example`: copy a shipped example project."""

from __future__ import annotations

import sys
from pathlib import Path

NAMES = ("example",)


def register(commands) -> None:
    example = commands.add_parser(
        "example",
        help="Copy a shipped example project (`example list` shows them)",
        description="Copy a shipped example to DIR (default ./NAME). `example list` lists them.",
    )
    example.add_argument("name", help="Example name, or `list`")
    example.add_argument("dir", nargs="?", type=Path, help="Destination (default ./NAME)")


def run(args) -> int:
    from agentbreeder import example

    try:
        if args.name == "list" and args.dir is None:
            for name in example.names():
                print(f"{name:12} {example.DESCRIPTIONS.get(name, '')}".rstrip())
            return 0
        destination = example.copy(args.name, args.dir or Path(args.name))
    except example.ExampleError as error:
        print(str(error), file=sys.stderr)
        return 2
    print(f"Copied {args.name} to {destination}")
    print(f"Next: read {destination / 'README.md'}")
    return 0
