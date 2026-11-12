"""Local development lifecycle commands with the deploy-time identity gate.

Each command's parser and handler live in `agentbreeder.commands.<name>`; this module keeps the
entry point, the ordered list of command modules and the helpers they share.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shlex
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
    "migrate",
    "logs",
    "next",
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


# Errors that `--stack DIR` fixes when the project has a stack.yaml (guided G-F1, G-F2).
_STACK_CODES = ("CONTEXT_ENVIRONMENT_UNKNOWN", "MODEL_ACCESS_UNCONFIGURED")


def _stack_beside(near: Path, *, manifest: bool = False) -> Path | None:
    """The directory holding a stack.yaml beside `near` (an agent file), or, for a manifest
    (in deploy/), beside it or one level above. A symlinked stack.yaml is not suggested."""
    parent = near.absolute().parent
    for directory in (parent, parent.parent) if manifest else (parent,):
        candidate = directory / "stack.yaml"
        if candidate.is_file() and not candidate.is_symlink():
            return directory
    return None


# A `build` command in a message, in any launcher form (`agentbreeder`, `npx agentbreeder@V`, ...).
_NAMES_BUILD = re.compile(r"agentbreeder(@\S*)? build\b")


def _stack_used(args) -> bool:
    return getattr(args, "stack", None) is not None


def _message(error: Exception, *, args=None, near: Path | None = None) -> str:
    """`[CODE] message`, plus the fix when the CLI knows it. `near` is the agent file or
    manifest the command was given; `args` the parsed command line."""
    from agentbreeder import hints

    code = getattr(error, "code", None)
    text = str(error)
    text = text if not code or text.startswith("[") else f"[{code}] {text}"
    if code in _STACK_CODES and near is not None and not _stack_used(args):
        stack = _stack_beside(Path(near), manifest=getattr(args, "command", None) == "deploy")
        if stack is not None:
            text += (
                f"\nstack.yaml is in {stack}: add `--stack {shlex.quote(str(stack))}` to use "
                "the operator config `services up` wrote for it"
            )
    if code == "LOCK_STALE" and _NAMES_BUILD.search(text) and near and not _stack_used(args):
        stack = _stack_beside(Path(near))  # #854: the `build` it names needs --stack too
        if stack is not None:
            flag = f"--stack {shlex.quote(str(stack))}"
            text += f"\nstack.yaml is in {stack}: run that build with `{flag}`"
    if code == "REF_UNPINNED" and getattr(args, "command", None) == "release":
        build = hints.command(
            "build",
            near or "agent.yaml",
            stack=getattr(args, "stack", None),
            environment=getattr(args, "environment", None),
        )
        text += (
            "\nrelease needs published versions: scaffold with "
            f"`{hints.command('registry', 'new', 'KIND', 'NAME')}`, copy your file in, run "
            f"`{hints.command('registry', 'publish', 'DIR')}`, reference KIND/NAME@X.Y.Z in "
            f"agent.yaml, then `{build}`"
        )
    return text


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
        if args.command in (
            "status",
            "remove",
            "reconcile",
            "forward",
            "list",
            "migrate",
            "logs",
        ) and (args.environment):
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
    args.demo_stack = True  # the stack's generated config was selected (G-F9)
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
    near: Path | None = None,
    readonly: bool = False,
) -> int:
    """Open the local state and run `action(service, operator)` (or `action(store)` when
    `service` is false), mapping deployment errors to exit 2. `before` runs first, inside the
    same error handling; an int it returns ends the command. `existing` refuses a missing state.
    `near` (the file the command was given) lets an error name a fix such as `--stack`.
    `readonly` opens existing state with `Store.open_readonly` (needs `service=False`)."""
    store = None
    try:
        if before is not None and (early := before()) is not None:
            return early
        if existing and not (args.state_dir / "deployments.sqlite3").exists():
            # Never create an empty state for a read/act-on-existing command: a mistyped
            # --state-dir would otherwise report success ("OK: 0 rows") on the wrong place.
            raise DeploymentError(f"No AgentBreeder state at {args.state_dir}; check --state-dir")
        store = Store.open_readonly(args.state_dir) if readonly else Store(args.state_dir)
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
        print(_message(error, args=args, near=near), file=sys.stderr)
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


DEMO_EXPLANATION = (
    "Note: these warnings are expected with the demo identities `services up` generated "
    "(a static file, no policy decision point, your OS user as deployer); a real identity "
    "provider and policy remove them. See docs/identity-gate.md."
)


def _print_deployment(result: dict, args, command: str) -> int:
    gate = result.get("gate") or {}
    for warning in gate.get("warnings", []):
        print(f"WARNING: {warning}", file=sys.stderr)
    if gate.get("warnings") and getattr(args, "demo_stack", False):
        print(DEMO_EXPLANATION, file=sys.stderr)
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
