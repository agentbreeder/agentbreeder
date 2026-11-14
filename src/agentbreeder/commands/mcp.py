"""`agentbreeder mcp grant|revoke AGENT SERVER TOOL...`: edit an agent's MCP tool grant in
stack.yaml (on `main`, not in a published preview).

Exit 0 on success (also when nothing changed), 2 on a refusal. With `--json` the summary goes to
stdout, and a refusal prints `{"schema_version": 1, "error": {"code", "message"}}` there too; the
`Next:` lines are text mode only."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentbreeder import registry

NAMES = ("mcp",)


def register(commands) -> None:
    mcp = commands.add_parser("mcp", help="Grant or revoke an agent's MCP tools in stack.yaml")
    verbs = mcp.add_subparsers(dest="mcp_command", required=True)
    for verb, text in (
        ("grant", "Grant TOOLs of SERVER to AGENT in stack.yaml (shown as a diff)"),
        ("revoke", "Revoke TOOLs of SERVER from AGENT in stack.yaml (shown as a diff)"),
    ):
        parser = verbs.add_parser(
            verb,
            help=text,
            description=f"{text}. Only environment.agents.AGENT.mcp changes; run `services "
            "up` to apply it to the relay. Deploy uses only tools the agent requests in "
            "agent.yaml and the stack grants.",
        )
        parser.add_argument("agent", metavar="AGENT", help="an environment.agents name")
        parser.add_argument("server", metavar="SERVER", help="an mcp: entry name")
        parser.add_argument("tools", metavar="TOOL", nargs="+", help="tool name(s)")
        parser.add_argument("--project", type=Path, default=Path("."), metavar="DIR")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Show the diff; write nothing")
        mode.add_argument(
            "--expect-base",
            type=_digest,
            metavar="SHA256",
            help="Refuse unless stack.yaml is still this digest (from --dry-run)",
        )
        parser.add_argument("--json", action="store_true")


def run(args) -> int:
    from agentbreeder import hints, mcpgrant

    try:
        result = mcpgrant.change(
            args.project,
            args.mcp_command,
            args.agent,
            args.server,
            args.tools,
            expect_base=args.expect_base,
            dry_run=args.dry_run,
        )
    except mcpgrant.GrantError as error:
        if args.json:
            body = {"code": error.code, "message": error.message}
            print(json.dumps({"schema_version": 1, "error": body}, indent=2))
        print(str(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    verb = result["verb"]
    agent, server = registry._shown(result["agent"]), result["server"]
    if not result["changed"]:
        state = "already granted" if verb == "grant" else "not granted"
        print(f"Nothing to change: {', '.join(args.tools)} {state} ({agent}, mcp.{server})")
        return 0
    print(result["diff"], end="")
    moved = ", ".join(result["added"] if verb == "grant" else result["removed"])
    now = ", ".join(f"{t['name']} ({t['effect'] or 'undeclared'})" for t in result["tools"])
    if result["dry_run"]:
        print(f"Would {verb} {moved} for {agent} on mcp.{server} (dry run: nothing written)")
        print(f"To write exactly this: --expect-base {result['base_sha256']}")
        return 0
    done = "Granted" if verb == "grant" else "Revoked"
    print(f"{done} {moved} for {agent} on mcp.{server}; it now has: {now or 'no tools'}")
    for item in result["in_use"]:
        if item["deployment"] is None:
            print(
                f"In use: an unreadable grant file in {item['environment']} for mcp.{server}; "
                "`services up` refuses until it is cleared (reconcile, or remove its deployment)"
            )
            print(
                hints.next_line("reconcile", stack=args.project, environment=item["environment"])
            )
            continue
        print(
            f"In use: deployment {item['deployment']} ({item['environment']}) holds "
            f"{', '.join(item['tools'])}; `services up` refuses this until it is removed"
        )
        print(hints.next_line("remove", item["deployment"], "--local", stack=args.project))
    environments = {item["environment"] for item in result["in_use"]}
    environment = environments.pop() if len(environments) == 1 else "ENV"
    print(hints.next_line("services", "up", args.project, environment=environment))
    return 0


def _digest(text: str) -> str:
    if not registry.DIGEST.fullmatch(text):
        raise argparse.ArgumentTypeError("must be sha256:<64 hex digits>")
    return text
