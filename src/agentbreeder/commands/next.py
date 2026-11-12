"""`agentbreeder next [DIR]`: where this project is on the guided path, and what to run next.

Reads only (see `guide_checks`). The text form prints the checklist and exits 0 when every
required step is done, 1 when steps remain; `--json` prints the `docs/next.md` contract and exits
0 whenever it was computed. Exit 2: the checklist could not be computed (`[CODE] message`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

NAMES = ("next",)
ENVIRONMENT = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
MARKS = {"done": "✓", "next": "→", "blocked": "✗", "todo": "·"}


def register(commands) -> None:
    command = commands.add_parser(
        "next", help="Show where this project is on the guided path and the command to run next"
    )
    command.add_argument(
        "project",
        nargs="?",
        type=Path,
        default=Path("."),
        metavar="DIR",
        help="Project directory with agent.yaml and stack.yaml (default: .)",
    )
    command.add_argument("--environment", default="dev", help="Environment (default: dev)")
    command.add_argument(
        "--output",
        type=Path,
        help="Where `build --output` wrote its manifests (default DIR/deploy)",
    )
    command.add_argument("--json", action="store_true", help="Print the checklist as JSON")


def run(args) -> int:
    from agentbreeder import guide_checks

    if not ENVIRONMENT.fullmatch(args.environment):
        print(
            "[ENVIRONMENT_INVALID] --environment must be [a-z0-9-], at most 32 characters",
            file=sys.stderr,
        )
        return 2
    try:
        result = guide_checks.compute(args.project, args.environment, args.output)
    except guide_checks.GuideError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - one coded line, never a traceback with paths
        # The type only (never the message, which may hold paths or state content).
        print(
            f"[NEXT_FAILED] next could not compute the checklist ({type(error).__name__})",
            file=sys.stderr,
        )
        return 2
    if args.json:
        print(result.model_dump_json(indent=2))
        return 0
    print(text(result))
    return 0 if result.complete else 1


def text(result) -> str:
    lines = [f"{result.project} ({result.environment})"]
    for step in result.steps:
        mark = MARKS[step.state]
        detail = f": {step.reason.message}" if step.reason else ""
        optional = "" if step.required else " (optional)"
        lines.append(f"{mark} {step.title}{optional}{detail}")
        if step.state in ("next", "blocked") and step.fix:
            if step.fix.note:
                lines.append(f"    {step.fix.note}")
            if step.fix.url:
                lines.append(f"    {step.fix.url}")
    for condition in result.conditions:
        if condition.code != "STEP_BLOCKED":
            lines.append(f"! {condition.message}")
    first = next((s for s in result.steps if s.state in ("next", "blocked")), None)
    if first and first.fix and first.fix.command:
        lines.append(f"Next: {first.fix.command}")
    elif result.complete:
        attempt = next((s for s in result.steps if s.id == "try"), None)
        if attempt and attempt.fix and attempt.fix.command:
            lines.append(f"Done. Try it: {attempt.fix.command}")
        else:
            lines.append("Done.")
    return "\n".join(lines)
