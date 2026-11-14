"""`agentbreeder new agent NAME --model gemini [--dir DIR] [--dry-run] [--json]`: write a starter
project (see `newagent`). Exit 2 with `[CODE] message` on stderr when it refuses, and with
`--json` also `{"schema_version": 1, "error": {"code", "message"}}` on stdout."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agentbreeder import hints

NAMES = ("new",)


def register(commands) -> None:
    from agentbreeder import newagent

    new = commands.add_parser("new", help="Write a starter project (`new agent NAME`)")
    kinds = new.add_subparsers(dest="new_command", required=True)
    agent = kinds.add_parser(
        "agent",
        help="Write a starter agent: agent.yaml, stack.yaml, a prompt and two eval cases",
        description=(
            "Write a starter agent project to DIR (default ./NAME): agent.yaml, stack.yaml "
            "(Postgres and LiteLLM), stack/litellm/config.yaml, and editable sources in "
            "src/prompt/ and src/evals/. Publishes prompt/NAME-prompt@0.1.0 and "
            "eval/NAME-evals@0.1.0 into DIR/registry and writes agent.lock. DIR must not exist "
            "or be an empty directory."
        ),
    )
    agent.add_argument("name", help="Agent name: [a-z][a-z0-9-], at most 31 characters")
    agent.add_argument(
        "--model",
        required=True,
        choices=sorted(newagent.PROVIDERS),
        help="Model provider (only providers run end to end are offered)",
    )
    where = agent.add_mutually_exclusive_group()
    where.add_argument("--dir", type=Path, help="Destination (default ./NAME)")
    where.add_argument(
        "--stack-for",
        type=Path,
        metavar="AGENT_YAML",
        help=(
            "Instead of a starter, write stack.yaml and stack/litellm/config.yaml beside an "
            "existing agent.yaml named NAME (onboarding an agent.yaml alone)"
        ),
    )
    agent.add_argument("--dry-run", action="store_true", help="Show what would be written")
    agent.add_argument("--json", action="store_true", help="Print the result as JSON")


def run(args) -> int:
    from agentbreeder import newagent

    if args.stack_for is not None:
        return _stack_for(args)
    try:
        plan = newagent.plan(args.name, args.model, args.dir)
        if args.dry_run:
            newagent.check(plan)
        else:
            plan = newagent.create(plan)
    except newagent.NewAgentError as error:
        return _refused(error, args)
    summary = plan.summary(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    print(_text(summary))
    return 0


def _refused(error, args) -> int:
    from agentbreeder import newagent
    from agentbreeder.safe_text import visible

    # A message can quote text from a project's files (an agent.yaml key): escape controls.
    # One line: a newline quoted from a file is shown as `\\n`, not as a second message.
    print(visible(str(error).replace("\n", "\\n")), file=sys.stderr)
    if args.json:  # a console read gets the code without parsing stderr
        message = str(error).removeprefix(f"[{error.code}] ")
        error_body = {"code": error.code, "message": message}
        print(
            json.dumps({"schema_version": newagent.SUMMARY_VERSION, "error": error_body}, indent=2)
        )
    return 2


def _stack_for(args) -> int:
    from agentbreeder import newagent, stackfor
    from agentbreeder.safe_text import visible

    try:
        plan = stackfor.plan(args.name, args.model, args.stack_for)
        if args.dry_run:
            stackfor.check(plan)
        else:
            plan = stackfor.create(plan)
    except newagent.NewAgentError as error:
        return _refused(error, args)
    summary = plan.summary(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0
    directory = summary["directory"]
    verb = "Would write" if args.dry_run else "Wrote"
    lines = [f"{verb} a stack for the {summary['name']} agent in {directory}:"]
    lines += [f"  {entry['path']}" for entry in summary["files"]]
    lines.append(f"Gateway aliases: {', '.join(summary['aliases'])} ({summary['model']}).")
    lines.append(f"The gate refuses deploys after review_by {summary['review_by']} (stack.yaml).")
    lines += [f"Warning: {warning}" for warning in summary["warnings"]]
    if args.dry_run:
        lines.append("Dry run: nothing was written.")
    else:
        lines.append(
            f"Put {summary['key']}=<your key> in a file only you can read (chmod 600); the next "
            "step asks for it."
        )
        lines.append(hints.next_line("next", directory))
    print(visible("\n".join(lines)))
    return 0


def _text(summary: dict) -> str:
    directory = summary["directory"]
    verb = "Would write" if summary["dry_run"] else "Wrote"
    lines = [f"{verb} the {summary['name']} agent in {directory}:"]
    lines += [f"  {entry['path']}" for entry in summary["files"]]
    refs = " and ".join(entry["ref"] for entry in summary["published"])
    lines.append(
        f"{'Would publish' if summary['dry_run'] else 'Published'} {refs} into registry/ "
        f"and {'write' if summary['dry_run'] else 'wrote'} agent.lock"
    )
    lines.append(f"The gate refuses deploys after review_by {summary['review_by']} (stack.yaml).")
    if summary["dry_run"]:
        lines.append("Dry run: nothing was written.")
        return "\n".join(lines)
    lines.append(
        f"Put {summary['key']}=<your key> in a file only you can read (chmod 600); the next "
        "step asks for it."
    )
    lines.append(hints.next_line("next", directory))
    return "\n".join(lines)
