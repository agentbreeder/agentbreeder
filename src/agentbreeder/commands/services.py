"""`agentbreeder services up|plan|status|logs|usage|env|down`: local backing services from
stack.yaml (local-only preview)."""

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
    from agentbreeder.commands import logs as logs_command

    sv_logs.add_argument(
        "--tail", type=logs_command._tail, default=200, metavar="N",
        help="The last N lines (default 200, at most 10000)",
    )  # fmt: skip
    sv_logs.add_argument(
        "--since", metavar="D", help="Only lines since D (10m, 2h, 2026-11-11T09:00:00Z)"
    )
    sv_logs.add_argument(
        "--follow",
        action="store_true",
        help=(
            "Keep printing new lines until the service stops, Ctrl-C, SIGTERM, SIGHUP or the "
            "end of a non-terminal stdin"
        ),
    )
    sv_logs.add_argument("--json", action="store_true", help="Print the result as JSON")
    logs_command.add_level(sv_logs)
    sv_logs.add_argument(
        "--raw",
        action="store_true",
        help="Print control characters as they are (unsafe; --json always escapes)",
    )
    from agentbreeder.commands import usage as usage_command

    usage_command.register(service_commands)
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
    if command == "usage":  # read-only: no Engine (which opens the state read-write)
        from agentbreeder.commands import usage as usage_command

        return usage_command.run(args)
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
            return _logs(engine, name, args)
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
        if command == "logs" and getattr(error, "code", None):
            from agentbreeder.commands import logs as logs_command

            return logs_command._fail(args, error)  # `--json` gets the error object too
        print(cli._message(error), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        if command == "logs":
            from agentbreeder.commands import logs as logs_command

            return logs_command._fail(
                args,
                logs_command.LogsError(
                    "STATE_UNREADABLE", "cannot access the stack state; check permissions"
                ),
            )
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


def _logs(engine, name: str, args) -> int:
    """`services logs [DIR] NAME [--tail N] [--since D] [--follow] [--level L] [--json] [--raw]`,
    in the same shape as `logs ID` (guided §10): `{"schema_version", "source": "service",
    "target": "local", "name", "lines", "levels", "format", "level", "truncated"}`, or with
    --follow one object per line (with its `level`) plus the end record. Lines are redacted
    (failing closed) and controls shown as visible escapes unless --raw. A built-in service's
    lines are classified by `loglevels`; `--level` on any other service is LEVELS_UNAVAILABLE."""
    from agentbreeder.commands import logs as logs_command
    from agentbreeder.runtime import SINCE
    from agentbreeder.services.schema import ServiceError

    label = {"source": "service", "target": "local", "name": name}
    level = getattr(args, "level", None)
    unclassified = "this service (only the built-in postgres-pgvector, litellm and phoenix are)"
    try:
        if args.since is not None and not SINCE.fullmatch(args.since):
            raise logs_command.LogsError(
                "SINCE_INVALID", "--since must look like 10m, 2h or 2026-11-11T09:00:00Z"
            )
        if args.follow:
            redactor = engine.log_redactor(name)  # checks the name first (SERVICE_UNKNOWN)
            fmt = engine.log_format(name)
            logs_command.check_level(args, fmt, unclassified)
            return logs_command._follow(
                args, None, _ServiceReader(engine, name), None, redactor, args.tail,
                "DOCKER_LOGS_FAILED", label=label, fmt=fmt,
            )  # fmt: skip
        if level is not None and name in engine.stack.services:
            logs_command.check_level(args, engine.log_format(name), unclassified)
        text, truncated, _ = engine.read_logs(name, args.tail, args.since)
        fmt = engine.log_format(name)
        logs_command.check_level(args, fmt, unclassified)  # an MCP relay: never classified
    except (ServiceError, logs_command.LogsError) as error:
        return logs_command._fail(args, error)
    lines, levels = logs_command.leveled(logs_command._lines(text), fmt, level)
    if args.json:
        body = {"schema_version": logs_command.SCHEMA_VERSION, **label}
        extra = {"levels": levels, "format": fmt, "level": level}
        print(json.dumps({**body, "lines": lines, **extra, "truncated": truncated}, indent=2))
        return 0
    for line in lines:
        sys.stdout.write(logs_command._shown(line, args.raw) + "\n")
    if truncated:
        print("Note: older lines were dropped (4 MiB cap); use --tail or --since", file=sys.stderr)
    return 0


class _ServiceReader:
    """The follow loop's reader interface (`follow(info, tail, since)`) for a stack service."""

    def __init__(self, engine, name: str):
        self.engine, self.name = engine, name

    def follow(self, info, tail, since):
        from agentbreeder.services.schema import ServiceError
        from agentbreeder.store import DeploymentError

        try:
            return self.engine.docker.follow_logs(self.engine.state, self.name, tail, since)
        except ServiceError as error:
            raise DeploymentError(str(error).removeprefix(f"[{error.code}] ")) from error
