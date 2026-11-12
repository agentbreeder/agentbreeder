"""`agentbreeder services up|plan|status|logs|env|down`: local backing services from stack.yaml
(local-only preview)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from agentbreeder import cli, hints, registry, secret_files
from agentbreeder.store import DeploymentError

NAMES = ("services",)


def register(commands) -> None:
    services = commands.add_parser(
        "services", help="Local backing services from stack.yaml (local-only preview)"
    )
    service_commands = services.add_subparsers(dest="services_command", required=True)
    up = service_commands.add_parser("up", help="Start the stack and write its demo config")
    up.add_argument("dir", nargs="?", type=Path, default=Path("."))
    up.add_argument("--environment", required=True)
    up_lock = up.add_mutually_exclusive_group()
    up_lock.add_argument("--locked", action="store_true", help="Refuse any stack.lock change")
    up_lock.add_argument(
        "--update",
        action="store_true",
        help="Accept new hashes for edited project files (file params); never for a "
        "published service version",
    )
    up.add_argument("--env-file", type=Path, help="KEY=value file for imported variables")
    up.add_argument(
        "--yes",
        action="store_true",
        help="Confirm a new or changed grant (and new tool pins of built-in MCP servers)",
    )
    up.add_argument(
        "--repin",
        action="store_true",
        help="Review MCP tool definitions that changed upstream and pin them again",
    )
    up.add_argument(
        "--allow-project-mcp",
        action="append",
        default=[],
        metavar="NAME",
        help="Allow a non-built-in mcp: entry that sends a credential upstream (repeatable); "
        "with --yes it also accepts that server's new tool pins",
    )
    up.add_argument("--json", action="store_true")
    up.add_argument(
        "--expect-grant", metavar="SHA256", help="Refuse unless the grant has this digest"
    )
    sv_plan = service_commands.add_parser(
        "plan", help="Show the grant `up` would ask to confirm (starts nothing)"
    )
    sv_plan.add_argument("dir", nargs="?", type=Path, default=Path("."))
    sv_plan.add_argument("--environment", required=True)
    sv_plan.add_argument("--json", action="store_true")
    sv_status = service_commands.add_parser("status", help="Show the stack's services")
    sv_status.add_argument("dir", nargs="?", type=Path, default=Path("."))
    sv_status.add_argument("--json", action="store_true")
    sv_logs = service_commands.add_parser("logs", help="Show a service's logs (redacted)")
    sv_logs.add_argument("target", nargs="+", metavar="[DIR] NAME")
    sv_logs.add_argument("--tail", type=int, default=200)
    sv_env = service_commands.add_parser("env", help="Print exports for the stack's config")
    sv_env.add_argument("dir", nargs="?", type=Path, default=Path("."))
    sv_env.add_argument("--environment")
    down = service_commands.add_parser("down", help="Stop the stack (keeps data and secrets)")
    down.add_argument("dir", nargs="?", type=Path, default=Path("."))
    down.add_argument("--volumes", action="store_true", help="Also delete the stack's volumes")
    down.add_argument(
        "--purge",
        action="store_true",
        help="Delete everything `up` made: volumes, built service images, generated secrets, "
        "operator config and history (the next `up` starts from scratch)",
    )
    down.add_argument("--yes", action="store_true")
    down.add_argument("--json", action="store_true")


def run(args) -> int:
    from agentbreeder.services import plan as stack_plan
    from agentbreeder.services.engine import Engine
    from agentbreeder.services.schema import ServiceError

    command = args.services_command
    if command == "logs":
        if len(args.target) > 2:
            cli._parser().error("logs takes [DIR] NAME")
        directory = Path(args.target[0]) if len(args.target) == 2 else Path(".")
        name = args.target[-1]
    else:
        directory = args.dir
    if command == "down" and (args.volumes or args.purge) and not args.yes:
        flag = "--purge" if args.purge else "--volumes"
        print(f"[CONFIRM_REQUIRED] down {flag} deletes data; add --yes", file=sys.stderr)
        return 2
    engine = None
    log = (
        (lambda message: None)
        if getattr(args, "json", False)
        else (lambda message: print(message, file=sys.stderr))
    )
    try:
        engine = Engine(directory, log=log)
        if command == "up":

            def confirm(grant: dict) -> bool:
                print(stack_plan.grant_text(grant), file=sys.stderr)
                if args.yes:
                    return True
                if not sys.stdin.isatty():
                    return False
                return input("Confirm this grant? [y/N] ").strip().lower() in ("y", "yes")

            def confirm_pins(report: dict) -> bool:
                print(stack_plan.pins_text(report), file=sys.stderr)
                if args.yes and report["auto"]:
                    return True
                if not sys.stdin.isatty():
                    return False
                return input("Pin these? [y/N] ").strip().lower() in ("y", "yes")

            result = engine.up(
                args.environment,
                mode="locked" if args.locked else "update" if args.update else "plain",
                env_file=args.env_file,
                confirm=confirm,
                confirm_pins=confirm_pins,
                repin=args.repin,
                allow_project_mcp=frozenset(args.allow_project_mcp),
                expect_grant=args.expect_grant,
            )
            for warning in result["warnings"]:
                print(f"WARNING: {warning}", file=sys.stderr)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                for service, action in result["services"].items():
                    print(f"{service:<16} {action}")
                for service in result.get("removed") or []:
                    print(f"{service:<16} removed")
                for line in stack_plan.mcp_status_lines(result.get("mcp") or {}):
                    print(line)
                print(f"Operator config: {result['operator_config']}")
                hint = _next_after_up(directory, args.environment)
                if hint:
                    print(hint)
            return 0
        if command == "plan":
            preview = engine.preview(args.environment)
            print(json.dumps(preview, indent=2) if args.json else preview["text"])
            return 0
        if command == "status":
            result = engine.status()
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"Stack {result['stack']}")
                for service, info in result["services"].items():
                    ports = ", ".join(f"{k} 127.0.0.1:{v}" for k, v in info["host_ports"].items())
                    health = f" ({info['health']})" if info["health"] else ""
                    print(f"{service:<16} {info['state']}{health}  {ports}")
                for line in stack_plan.mcp_status_lines(result.get("mcp") or {}):
                    print(line)
            return 0
        if command == "logs":
            print(engine.logs(name, args.tail))
            return 0
        if command == "env":
            print("\n".join(engine.env_lines(args.environment)))
            return 0
        result = engine.down(volumes=args.volumes, purge=args.purge)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            kept = "" if result["state_removed"] else " (data and secrets kept)"
            print(f"Stopped stack {result['stack']}{kept}")
            for volume in result["volumes_removed"]:
                print(f"Removed volume {volume}")
            for image in result["images_removed"]:
                print(f"Removed image {image}")
            if result["state_removed"]:
                print(f"Removed state {result['state_removed']}")
        return 0
    except (
        ServiceError,
        registry.RegistryError,
        secret_files.SecretError,
        DeploymentError,
    ) as error:
        print(cli._message(error), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("Cannot access the stack state; check permissions and disk space", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.close()


def _next_after_up(directory: Path, environment: str) -> str | None:
    """Absolute paths, so the line works from any directory: `build` until the project has an
    agent.lock, then `release`."""
    project = directory.resolve()
    agent = project / "agent.yaml"
    if not agent.is_file():
        return None
    step = "release" if (project / "agent.lock").is_file() else "build"
    return hints.next_line(step, agent, stack=project, environment=environment)
