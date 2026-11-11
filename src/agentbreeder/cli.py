"""Local development lifecycle commands with the deploy-time identity gate.

Each command's parser and handler live in `agentbreeder.commands.<name>`; this module keeps the
entry point, the ordered list of command modules and the helpers they share.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from agentbreeder import __version__, agentfile
from agentbreeder.deployer import DeployerError
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.manifest import ManifestError
from agentbreeder.operator_config import OperatorConfigError, load_operator_config
from agentbreeder.runtime import Docker
from agentbreeder.store import DeploymentError, Store

STATE_ENV = "AGENTBREEDER_STATE_DIR"


def _default_state() -> Path:
    value = os.environ.get(STATE_ENV)
    return Path(value).expanduser() if value else Path.home() / ".agentbreeder/local"


DEFAULT_STATE = _default_state()
EXIT_PENDING = 3

# Registration order is the order `agentbreeder --help` lists the commands in.
COMMAND_MODULES = (
    "validate",
    "doctor",
    "example",
    "quickstart",
    "deploy",
    "status",
    "remove",
    "forward",
    "list",
    "approve",
    "disable",
    "reconcile",
    "audit",
    "build",
    "eval",
    "release",
    "registry",
    "secret",
    "services",
    "promote",
    "cluster",
    "up",
    "login",
)


def _modules() -> list[ModuleType]:
    return [importlib.import_module(f"agentbreeder.commands.{name}") for name in COMMAND_MODULES]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentbreeder",
        description=(
            "Local development: validate, gate and deploy trusted HTTP containers. "
            "The identity gate runs at deploy time; status and reconcile re-verify "
            "principals. Opt-in runtime enforcement covers MCP Streamable HTTP tool calls "
            "through the egress sidecar only."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for module in _modules():
        module.register(commands)
    return parser


def stateful_parser(commands, name, help_text, positional=None, local=False, cluster=False):
    """A command that works on local deployment state (--state-dir, --json)."""
    command = commands.add_parser(name, help=help_text)
    if positional:
        command.add_argument(positional)
    command.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    command.add_argument("--json", action="store_true")
    if cluster:
        where = command.add_mutually_exclusive_group(required=True)
        where.add_argument("--local", action="store_true", help="Target: local Docker")
        where.add_argument("--cluster", action="store_true", help="Target: Kubernetes")
    elif local:
        command.add_argument("--local", action="store_true", required=True)
    return command


def add_stack(command, environment: bool = False) -> None:
    command.add_argument(
        "--stack",
        type=Path,
        metavar="DIR",
        help="Use the operator config and state that `services up` wrote for DIR",
    )
    if environment:
        command.add_argument("--environment", help="With --stack: the stack environment")


def _message(error: Exception) -> str:
    code = getattr(error, "code", None)
    text = str(error)
    return text if not code or text.startswith("[") else f"[{code}] {text}"


def _file_kind(path: Path) -> str:
    try:
        kind, data = agentfile.read(path)
        return "stack" if kind == "unknown" and data.get("kind") == "stack" else kind
    except agentfile.AgentFileError:
        return "unknown"  # let the manifest loader report the precise problem


def _use_stack(args) -> int | None:
    """`--stack DIR`: point the operator config and state dir at what `services up` wrote."""
    from agentbreeder.operator_config import CONFIG_ENV
    from agentbreeder.services import engine
    from agentbreeder.services.schema import ServiceError

    if args.command == "cluster":
        return None  # its --stack names the project; it needs no operator config
    if getattr(args, "stack", None) is None:
        if args.command in ("status", "remove", "reconcile", "forward", "list") and (
            args.environment
        ):
            _parser().error("--environment needs --stack for this command")
        return None
    if getattr(args, "state_dir", DEFAULT_STATE) != DEFAULT_STATE:
        _parser().error("use --stack or --state-dir, not both")
    try:
        resolved = engine.context(args.stack, getattr(args, "environment", None))
    except (ServiceError, OSError) as error:
        print(_message(error), file=sys.stderr)
        return 2
    os.environ[CONFIG_ENV] = str(resolved.operator_config)
    if hasattr(args, "state_dir"):
        args.state_dir = resolved.state_dir
    if hasattr(args, "environment"):
        args.environment = resolved.environment
    print(
        f"Using stack {resolved.stack_id} ({resolved.environment}): demo identities "
        "(generated by services up)",
        file=sys.stderr,
    )
    return None


def run_stateful(
    args,
    action: Callable[..., int],
    *,
    existing: bool = False,
    before: Callable[[], int | None] | None = None,
    service: bool = True,
) -> int:
    """Open the local state and run `action(service, operator)` (or `action(store)` when
    `service` is false), mapping deployment errors to exit 2. `before` runs first, inside the
    same error handling; an int it returns ends the command. `existing` refuses a missing state."""
    store = None
    try:
        if before is not None and (early := before()) is not None:
            return early
        if existing and not (args.state_dir / "deployments.sqlite3").exists():
            # Never create an empty state for a read/act-on-existing command: a mistyped
            # --state-dir would otherwise report success ("OK: 0 rows") on the wrong place.
            raise DeploymentError(f"No AgentBreeder state at {args.state_dir}; check --state-dir")
        store = Store(args.state_dir)
        if not service:
            return action(store)
        operator = load_operator_config()
        return action(DeploymentService(store, Docker(), operator), operator)
    except (
        ManifestError,
        DeploymentError,
        GateDenied,
        OperatorConfigError,
        DeployerError,
    ) as error:
        print(_message(error), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print(
            "Cannot access local state; check permissions and available disk space",
            file=sys.stderr,
        )
        return 2
    finally:
        if store:
            store.close()


def _print_deployment(result: dict, args, command: str) -> int:
    gate = result.get("gate") or {}
    for warning in gate.get("warnings", []):
        print(f"WARNING: {warning}", file=sys.stderr)
    if args.json:
        print(json.dumps(result, indent=2))
    elif result["state"] == "pending_approval":
        print(
            f"pending_approval  {result['approval_id']}\n"
            f"A second authorized person must run: agentbreeder approve {result['approval_id']}"
            f"\nThen repeat this deploy before {result['expires']}."
        )
    else:
        detail = result.get("endpoint") or result.get("error") or "No endpoint"
        lines = [f"{result['id']}  {result['state']}", detail]
        if result.get("compliance"):
            lines.append(f"Identity: {result['compliance']}")
        if command == "status":
            lines.append("Last recorded observation; not a live health check.")
        if gate:
            lines.append(
                f"Gate attempt {gate['attempt_id']} ({gate['enforcement']}); unavailable checks: "
                + (", ".join(gate["checks_unavailable"]) or "none")
            )
        print("\n".join(lines))
    if result["state"] == "pending_approval":
        return EXIT_PENDING
    return 1 if result["state"] == "failed" else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    module = next(m for m in _modules() if args.command in m.NAMES)
    if args.command != "services":
        stacked = _use_stack(args)
        if stacked is not None:
            return stacked
    return module.run(args)
