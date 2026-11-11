"""`agentbreeder up`: start the local command center on 127.0.0.1 (single user, preview)."""

from __future__ import annotations

from pathlib import Path

NAMES = ("up",)


def register(commands) -> None:
    center = commands.add_parser(
        "up", help="Start the local command center on 127.0.0.1 (single user, preview)"
    )
    center.add_argument("dir", nargs="?", type=Path, help="A project with agent.yaml, stack.yaml")
    center.add_argument("--environment", default="dev")
    center.add_argument("--port", type=int, default=None)
    center.add_argument("--no-browser", action="store_true")
    center.add_argument(
        "--yes", action="store_true", help="With DIR: confirm the grant and start its services"
    )


def run(args) -> int:
    from agentbreeder.center.server import main as center_main

    return center_main(args)
