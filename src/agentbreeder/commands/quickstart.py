"""`agentbreeder quickstart`: build, gate, deploy, invoke and remove the shipped sample."""

from __future__ import annotations

NAMES = ("quickstart",)


def register(commands) -> None:
    quick = commands.add_parser(
        "quickstart",
        help="Build, gate, deploy, invoke and remove the sample (needs Docker)",
        description=(
            "Build the shipped http-agent sample, pass it through a demo identity gate (static "
            "adapter), deploy it locally, invoke it, then remove it. History is kept in a "
            "temporary directory that is printed first."
        ),
    )
    quick.add_argument(
        "--dry-run", action="store_true", help="Run doctor and validate only; no Docker changes"
    )


def run(args) -> int:
    from agentbreeder import quickstart

    return quickstart.main(dry_run=args.dry_run)
