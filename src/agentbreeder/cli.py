"""Local development lifecycle commands with the deploy-time identity gate."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from agentbreeder import __version__, agentfile, env_import, registry, secret_files
from agentbreeder.deployer import DeployerError, device_login, os_deployer, resolve_deployer
from agentbreeder.deployment import DeploymentService
from agentbreeder.doctor import check_compose, check_docker
from agentbreeder.gate import GateDenied
from agentbreeder.manifest import ManifestError, load_manifest
from agentbreeder.operator_config import OperatorConfigError, load_operator_config
from agentbreeder.runtime import Docker
from agentbreeder.store import DeploymentError, Store

STATE_ENV = "AGENTBREEDER_STATE_DIR"


def _default_state() -> Path:
    value = os.environ.get(STATE_ENV)
    return Path(value).expanduser() if value else Path.home() / ".agentbreeder/local"


DEFAULT_STATE = _default_state()
EXIT_PENDING = 3
_STATEFUL = {
    "deploy",
    "status",
    "remove",
    "approve",
    "reject",
    "approve-call",
    "reject-call",
    "disable",
    "enable",
    "reconcile",
}


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
    validate = commands.add_parser(
        "validate", help="Validate a deploy manifest or an agent file (kind: agent)"
    )
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--json", action="store_true", help="Print the validated manifest")
    doctor = commands.add_parser("doctor", help="Check local Docker prerequisites without changes")
    doctor.add_argument("--json", action="store_true")
    example = commands.add_parser(
        "example",
        help="Copy a shipped example project (`example list` shows them)",
        description="Copy a shipped example to DIR (default ./NAME). `example list` lists them.",
    )
    example.add_argument("name", help="Example name, or `list`")
    example.add_argument("dir", nargs="?", type=Path, help="Destination (default ./NAME)")
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

    def stateful(name, help_text, positional=None, local=False):
        command = commands.add_parser(name, help=help_text)
        if positional:
            command.add_argument(positional)
        command.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
        command.add_argument("--json", action="store_true")
        if local:
            command.add_argument("--local", action="store_true", required=True)
        return command

    deploy = stateful("deploy", "Gate and deploy a manifest", "manifest", local=True)
    deploy.add_argument("--environment", help="Operator-defined environment name")
    status = stateful("status", "Show a deployment and re-verify its principal", "id")
    status.add_argument("--no-verify", action="store_true", help="Skip principal re-resolution")
    remove = stateful("remove", "Remove a deployment", "id", local=True)
    for name in ("approve", "reject"):
        stateful(name, f"{name.title()} a pending approval as a second person", "approval_id")
    for name in ("approve-call", "reject-call"):
        verb = name.split("-")[0]
        stateful(name, f"{verb.title()} one held MCP tool call as a second person", "held_id")
    for name, help_text, local in (
        ("disable", "Persist a principal blocker and stop its workloads", True),
        ("enable", "Clear a principal blocker (starts nothing)", False),
    ):
        command = stateful(name, help_text, "target", local=local)
        command.add_argument("--environment")
        command.add_argument("--provider", help="Provider connection (with --ref)")
        command.add_argument("--ref", help="Principal reference (with --provider)")
        if name == "disable":
            command.add_argument("--reason", default="administrative disable")
            command.add_argument(
                "--idp", action="store_true", help="Also disable in the IdP (opt-in write)"
            )
    reconcile = stateful("reconcile", "Re-verify principals and retry blocked cleanup", local=True)
    audit = commands.add_parser("audit", help="Inspect and verify the local audit chain")
    audit_commands = audit.add_subparsers(dest="audit_command", required=True)
    for name in ("verify", "list"):
        command = audit_commands.add_parser(name)
        command.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
        command.add_argument("--json", action="store_true")
        if name == "list":
            command.add_argument("--limit", type=int, default=20)
            command.add_argument("--attempt")
    build = commands.add_parser(
        "build", help="Build an agent file into an image and a deploy manifest (never deploys)"
    )
    build.add_argument("agent")
    build.add_argument("--environment", help="Operator environment to build the manifest for")
    lock_mode = build.add_mutually_exclusive_group()
    lock_mode.add_argument("--locked", action="store_true", help="Refuse any agent.lock change")
    lock_mode.add_argument(
        "--update",
        nargs="*",
        metavar="REF",
        help="Accept new hashes for path refs (all or listed)",
    )
    build.add_argument("--output", type=Path, help="Where to write manifests (default ./deploy)")
    build.add_argument("--json", action="store_true")
    ev = commands.add_parser("eval", help="Run an agent's eval set against a running endpoint")
    ev.add_argument("agent")
    ev.add_argument("--endpoint", required=True)
    ev.add_argument("--environment")
    ev.add_argument("--only", help="Comma-separated case IDs")
    ev.add_argument("--retries", type=int, default=None)
    ev.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    ev.add_argument("--json", action="store_true")
    rel = commands.add_parser(
        "release",
        help="Build, deploy a candidate, run the eval gate, promote the exact image if it passes",
    )
    rel.add_argument("agent")
    rel.add_argument("--environment")
    rel.add_argument("--threshold", type=float, default=None)
    rel.add_argument("--retries", type=int, default=None)
    rel.add_argument("--rollback", action="store_true", help="Redeploy the previous production")
    rel.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    rel.add_argument("--output", type=Path)
    rel.add_argument("--json", action="store_true")
    reg = commands.add_parser("registry", help="Author and inspect registry versions")
    reg_commands = reg.add_subparsers(dest="registry_command", required=True)
    reg_new = reg_commands.add_parser("new", help="Scaffold a version directory to edit")
    reg_new.add_argument("kind", choices=sorted(registry.SCAFFOLD))
    reg_new.add_argument("name")
    reg_new.add_argument("--dir", type=Path, default=Path("."))
    reg_publish = reg_commands.add_parser("publish", help="Publish an immutable version")
    reg_publish.add_argument("source", type=Path)
    reg_publish.add_argument("--to", type=Path, default=Path("registry"), help="Registry root")
    reg_list = reg_commands.add_parser("list", help="List versions in the search roots")
    reg_list.add_argument("prefix", nargs="?", default="")
    reg_verify = reg_commands.add_parser("verify", help="Re-check every version's integrity")
    for command in (reg_publish, reg_list, reg_verify):
        command.add_argument("--json", action="store_true")
    for command in (reg_list, reg_verify):
        command.add_argument("--project", type=Path, default=Path("."))
    secret = commands.add_parser("secret", help="Manage local secret files (secret:// refs)")
    secret_commands = secret.add_subparsers(dest="secret_command", required=True)
    secret_set = secret_commands.add_parser(
        "set",
        help="Store a secret value from stdin, an environment variable or a generated token "
        "(never from arguments)",
    )
    secret_set.add_argument("name")
    secret_set.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    source = secret_set.add_mutually_exclusive_group()
    source.add_argument(
        "--from-env", metavar="VAR", help="Read the value from environment variable VAR"
    )
    source.add_argument("--generate", action="store_true", help="Store a random hex token")
    secret_set.add_argument(
        "--env-file", type=Path, help="With --from-env: also look in this KEY=value file"
    )
    secret_set.add_argument(
        "--force", action="store_true", help="With --generate: replace an existing secret"
    )
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
    for command in (deploy, status, remove, reconcile, build, ev, rel):
        command.add_argument(
            "--stack",
            type=Path,
            metavar="DIR",
            help="Use the operator config and state that `services up` wrote for DIR",
        )
    for command in (status, remove, reconcile):
        command.add_argument("--environment", help="With --stack: the stack environment")
    login = commands.add_parser("login", help="Sign in with the operator's OIDC device flow")
    login.add_argument("--issuer")
    return parser


def _secret_set(args) -> int:
    if args.env_file and not args.from_env:
        _parser().error("--env-file needs --from-env")
    if args.force and not args.generate:
        _parser().error("--force needs --generate")
    try:
        if args.from_env:
            value = env_import.lookup(args.from_env, args.env_file)
            if value is None:
                print(
                    f"[SECRET_SOURCE_MISSING] {args.from_env} is not set in the environment"
                    + (f" or {args.env_file}" if args.env_file else ""),
                    file=sys.stderr,
                )
                return 2
            data, source = value.encode(), f"from {args.from_env}"
        elif args.generate:
            if secret_files.exists(args.state_dir, args.name) and not args.force:
                print(
                    f"[SECRET_EXISTS] Secret {args.name} already exists; "
                    "pass --force to replace it",
                    file=sys.stderr,
                )
                return 2
            data, source = secret_files.generate_token(), "generated"
        else:
            data = sys.stdin.buffer.read(secret_files.MAX_BYTES + 2)
            if data.endswith(b"\n"):
                data = data[:-1]  # `echo value |` and here-strings add one newline
            source = "from stdin"
        path = secret_files.set_secret(args.state_dir, args.name, data)
    except (secret_files.SecretError, env_import.EnvImportError) as error:
        print(f"[{error.code}] {error}", file=sys.stderr)
        return 2
    print(
        f"Stored secret {args.name} ({source}) at {path} (0600). "
        f"Reference it as secret://{args.name}"
    )
    return 0


def _use_stack(args) -> int | None:
    """`--stack DIR`: point the operator config and state dir at what `services up` wrote."""
    from agentbreeder.operator_config import CONFIG_ENV
    from agentbreeder.services import engine
    from agentbreeder.services.schema import ServiceError

    if getattr(args, "stack", None) is None:
        if args.command in ("status", "remove", "reconcile") and args.environment:
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


def _services(args) -> int:
    from agentbreeder.services import plan as stack_plan
    from agentbreeder.services.engine import Engine
    from agentbreeder.services.schema import ServiceError

    command = args.services_command
    if command == "logs":
        if len(args.target) > 2:
            _parser().error("logs takes [DIR] NAME")
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
                print(
                    f"Next: agentbreeder release agent.yaml --stack {directory} "
                    f"--environment {args.environment}"
                )
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
        print(_message(error), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print("Cannot access the stack state; check permissions and disk space", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.close()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "services":
        return _services(args)
    stacked = _use_stack(args)
    if stacked is not None:
        return stacked
    if args.command == "validate":
        kind = _file_kind(args.manifest)
        if kind in ("agent", "legacy"):
            try:
                agent = agentfile.load_agent_file(args.manifest)
            except agentfile.AgentFileError as error:
                print(f"Validation failed: {error}", file=sys.stderr)
                return 2
            print(
                agent.model_dump_json(indent=2)
                if args.json
                else f"Valid agent file: {agent.name} ({len(agent.refs())} registry refs). "
                "Build it with `agentbreeder build`."
            )
            return 0
        if kind == "stack":
            from agentbreeder.services.schema import ServiceError, load_stack

            try:
                stack = load_stack(args.manifest)
            except ServiceError as error:
                print(f"Validation failed: {error}", file=sys.stderr)
                return 2
            print(
                stack.model_dump_json(indent=2)
                if args.json
                else f"Stack file schema is valid: {len(stack.services)} services. "
                "`agentbreeder services up` checks references and starts them."
            )
            return 0
        try:
            manifest = load_manifest(args.manifest)
        except ManifestError as error:
            print(f"Validation failed: {error}", file=sys.stderr)
            return 2
        print(
            manifest.model_dump_json(indent=2)
            if args.json
            else f"Valid manifest: {manifest.name} (schema {manifest.schema_version}). "
            "Nothing deployed."
        )
        return 0
    if args.command == "doctor":
        checks = check_docker() + check_compose()
        if args.json:
            print(json.dumps([asdict(check) for check in checks], indent=2))
        else:
            for check in checks:
                mark = "PASS" if check.passed else "FAIL" if check.required else "WARN"
                print(f"{mark} {check.name}: {check.message}")
        return 0 if all(check.passed or not check.required for check in checks) else 1
    if args.command == "example":
        return _example(args)
    if args.command == "quickstart":
        from agentbreeder import quickstart

        return quickstart.main(dry_run=args.dry_run)
    if args.command == "registry":
        return _registry(args)
    if args.command == "build":
        return _build(args)
    if args.command == "eval":
        return _eval(args)
    if args.command == "release":
        return _release(args)
    if args.command == "secret":
        return _secret_set(args)
    if args.command == "login":
        try:
            deployer = device_login(load_operator_config(), issuer=args.issuer)
        except (DeployerError, OperatorConfigError) as error:
            print(_message(error), file=sys.stderr)
            return 2
        print(f"Signed in as {deployer.label}")
        return 0
    return _stateful(args)


def _example(args) -> int:
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


def _file_kind(path: Path) -> str:
    try:
        kind, data = agentfile.read(path)
        return "stack" if kind == "unknown" and data.get("kind") == "stack" else kind
    except agentfile.AgentFileError:
        return "unknown"  # let the manifest loader report the precise problem


def _build(args) -> int:
    from agentbreeder.build import BuildError, build

    mode = "locked" if args.locked else "update" if args.update is not None else "plain"
    try:
        result = build(
            Path(args.agent),
            load_operator_config(),
            args.environment,
            mode=mode,
            update=set(args.update or []),
            output=args.output,
        )
    except (
        BuildError,
        agentfile.AgentFileError,
        registry.RegistryError,
        OperatorConfigError,
        DeploymentError,
    ) as error:
        print(_message(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result.as_json(), indent=2))
        return 0
    print(f"Built {result.name}: {result.image}")
    print(f"Manifest: {result.manifest_path}")
    if result.candidate_manifest_path:
        print(f"Candidate manifest: {result.candidate_manifest_path}")
    print(f"Lock: {result.lock_path} ({'updated' if result.lock_changed else 'unchanged'})")
    environment = f" --environment {args.environment}" if args.environment else ""
    print(f"Next: agentbreeder deploy {result.manifest_path} --local{environment}")
    return 0


def _eval(args) -> int:
    from agentbreeder import evals
    from agentbreeder.release import ReleaseError, _eval_set

    try:
        operator = load_operator_config()
        agent = agentfile.load_agent_file(Path(args.agent))
        environment = args.environment or operator.model.default_environment
        eval_set = _eval_set(agent, Path(args.agent).resolve().parent)
        report = evals.run(
            eval_set,
            args.endpoint,
            operator,
            environment,
            args.state_dir,
            retries=agent.evals.retries if args.retries is None else args.retries,
            only=set(args.only.split(",")) if args.only else None,
        )
    except (
        evals.EvalError,
        ReleaseError,
        agentfile.AgentFileError,
        registry.RegistryError,
        OperatorConfigError,
        secret_files.SecretError,
    ) as error:
        print(_message(error), file=sys.stderr)
        return 2
    summary = report.summary()
    if args.json:
        print(json.dumps({**summary, "results": report.results}, indent=2))
    else:
        for result in report.results:
            mark = (
                "PASS"
                if result["passed"] and result["attempts"] == 1
                else ("FLKY" if result["passed"] else "FAIL")
            )
            print(f"{mark} {result['case_id']} ({result['correctness']:.2f})")
        print(
            f"{summary['passed']}/{summary['cases']} passed, mean correctness "
            f"{summary['mean_correctness']}, p50 {summary['p50_latency_ms']} ms"
        )
    return 0 if report.pass_rate >= agent.evals.threshold else 5


def _release(args) -> int:
    from agentbreeder import evals
    from agentbreeder.build import BuildError
    from agentbreeder.release import ReleaseError, release, rollback

    store = None
    try:
        operator = load_operator_config()
        store = Store(args.state_dir)
        service = DeploymentService(store, Docker(), operator)
        try:
            deployer = resolve_deployer(operator)
        except DeployerError as error:
            service.record_denial("release", os_deployer(), error.code, str(error))
            raise
        if args.rollback:
            outcome = rollback(
                Path(args.agent), service, deployer, args.environment, output=args.output
            )
        else:
            outcome = release(
                Path(args.agent),
                operator,
                service,
                deployer,
                args.environment,
                threshold=args.threshold,
                retries=args.retries,
                output=args.output,
                log=(lambda message: None)
                if args.json
                else (lambda message: print(message, file=sys.stderr)),
            )
    except (
        BuildError,
        ReleaseError,
        evals.EvalError,
        agentfile.AgentFileError,
        registry.RegistryError,
        OperatorConfigError,
        DeployerError,
        GateDenied,
        DeploymentError,
        secret_files.SecretError,
    ) as error:
        print(_message(error), file=sys.stderr)
        return 3 if getattr(error, "code", "") == "APPROVAL_REQUIRED" else 2
    finally:
        if store:
            store.close()
    if args.json:
        print(json.dumps(outcome.as_json(), indent=2))
    else:
        print(outcome.message)
        production = outcome.details.get("production")
        if production and production.get("endpoint"):
            print(f"Production: {production['endpoint']} ({production['id']})")
        if outcome.details.get("eval_report"):
            print(f"Eval report: {outcome.details['eval_report']}")
    return outcome.code


def _registry(args) -> int:
    try:
        if args.registry_command == "new":
            path = registry.scaffold(args.kind, args.name, args.dir)
            print(f"Created {path}. Edit it, then: agentbreeder registry publish {path}")
            return 0
        if args.registry_command == "publish":
            ref, digest, path = registry.publish(args.source, args.to)
            result = {"ref": str(ref), "sha256": digest, "path": str(path)}
            print(json.dumps(result) if args.json else f"{ref} {digest} {path}")
            return 0
        search = registry.roots(args.project)
        if args.registry_command == "list":
            rows = registry.listing(search, args.prefix)
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for row in rows:
                    print(f"{row['ref']}  {row['root']}")
            return 0
        problems = registry.verify(search)
        if args.json:
            print(json.dumps(problems, indent=2))
        for problem in problems:
            print(f"FAIL {problem['ref']}: {problem['error']}", file=sys.stderr)
        if not problems and not args.json:
            print("OK: every registry version passed its safety and manifest checks")
            builtin = registry.BUILTIN_ROOT
            if builtin in search and builtin.is_dir() and registry.trusted_root(builtin):
                print("Built-in content matches the shipped manifest (corruption check).")
        return 2 if problems else 0
    except registry.RegistryError as error:
        print(str(error), file=sys.stderr)
        return 2


def _message(error: Exception) -> str:
    code = getattr(error, "code", None)
    text = str(error)
    return text if not code or text.startswith("[") else f"[{code}] {text}"


# Commands that only make sense against existing state. deploy creates it; disable may set a
# blocker before any deployment exists.
_NEEDS_EXISTING_STATE = frozenset(
    {
        "status",
        "remove",
        "approve",
        "reject",
        "approve-call",
        "reject-call",
        "enable",
        "reconcile",
        "audit",
    }
)


def _stateful(args) -> int:
    store = None
    try:
        if args.command == "deploy" and _file_kind(Path(args.manifest)) == "agent":
            raise DeploymentError(
                f"[WRONG_FILE_KIND] {args.manifest} is an agent file; run "
                f"`agentbreeder build {args.manifest}` and deploy the manifest it writes"
            )
        manifest = load_manifest(Path(args.manifest)) if args.command == "deploy" else None
        if (
            args.command in _NEEDS_EXISTING_STATE
            and not (args.state_dir / "deployments.sqlite3").exists()
        ):
            # Never create an empty state for a read/act-on-existing command: a mistyped
            # --state-dir would otherwise report success ("OK: 0 rows") on the wrong place.
            raise DeploymentError(f"No AgentBreeder state at {args.state_dir}; check --state-dir")
        store = Store(args.state_dir)
        if args.command == "audit":
            return _audit(store, args)
        operator = load_operator_config()
        service = DeploymentService(store, Docker(), operator)
        if args.command == "deploy":
            try:
                deployer = resolve_deployer(operator)
            except DeployerError as error:
                service.record_denial("deploy", os_deployer(), error.code, str(error))
                raise
            result = service.deploy(
                manifest, local=args.local, environment=args.environment, deployer=deployer
            )
            return _print_deployment(result, args, "deploy")
        if args.command in ("approve", "reject"):
            result = service.decide(
                args.approval_id, resolve_deployer(operator), approve=args.command == "approve"
            )
            print(
                json.dumps(result, indent=2)
                if args.json
                else f"{result['id']}  {result['state']} by {result['checker']}"
            )
            return 0
        if args.command in ("approve-call", "reject-call"):
            result = service.decide_call(
                args.held_id, resolve_deployer(operator), approve=args.command == "approve-call"
            )
            print(
                json.dumps(result, indent=2)
                if args.json
                else f"{result['id']}  {result['server']}/{result['tool']}  {result['state']} by "
                f"{result['checker']}"
            )
            return 0
        if args.command in ("disable", "enable"):
            options = {"environment": args.environment, "provider": args.provider, "ref": args.ref}
            actor = resolve_deployer(operator)
            if args.command == "disable":
                result = service.disable(
                    args.target, actor, reason=args.reason, idp=args.idp, **options
                )
            else:
                result = service.enable(args.target, actor, **options)
            print(json.dumps(result, indent=2) if args.json else _summary(result))
            return 1 if result["state"] == "failed" else 0
        if args.command == "reconcile":
            results = service.reconcile(resolve_deployer(operator))
            if args.json:
                print(json.dumps(results, indent=2))
            else:
                for item in results:
                    print(f"{item['id']}  {item['name']}  {item['compliance']}")
            return _reconcile_exit_code(results)
        if args.command == "remove":
            return _print_deployment(
                service.remove(args.id, local=args.local, actor=resolve_deployer(operator)),
                args,
                "remove",
            )
        return _print_deployment(
            service.status(args.id, verify=not args.no_verify), args, "status"
        )
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


def _reconcile_exit_code(results: list[dict]) -> int:
    """1 when cleanup still fails (a blocked deployment, or gateway keys that could not be
    revoked); 4 when any deployment is noncompliant or cannot be verified (so scripts and CI can
    gate on drift); otherwise 0."""
    compliance = [r["compliance"] or "" for r in results]
    if any(
        c.endswith(":cleanup_failed")
        or c.startswith("gateway_unreachable:")
        or c == "gateway_credential_error"
        for c in compliance
    ):
        return 1
    if any(c.startswith(("noncompliant:", "unverifiable:")) for c in compliance):
        return 4
    return 0


def _summary(result: dict) -> str:
    lines = [result["state"]]
    for key in ("stopped", "failed"):
        if result.get(key):
            lines.append(f"{key}: {json.dumps(result[key])}")
    return "\n".join(lines)


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


def _audit(store: Store, args) -> int:
    if args.audit_command == "verify":
        report = store.verify_audit()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(
                f"{'OK' if report['ok'] else 'INVALID'}: {report['rows']} rows, head "
                f"{report['head'][:16]}…"
                + (
                    f"; first invalid seq {report['first_invalid_seq']} ({report['problem']})"
                    if not report["ok"]
                    else ""
                )
            )
            if report["incomplete_attempts"]:
                print(f"Incomplete attempts: {', '.join(report['incomplete_attempts'])}")
            print(f"Limitation: {report['limitations']}")
        return 0 if report["ok"] else 1
    rows = store.audit_rows(limit=max(1, min(args.limit, 1000)), attempt_id=args.attempt)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            reason = (row["reasons"] or [{}])[0].get("code", "")
            print(
                f"{row['seq']:>5} {row['at']} {row['attempt_id'][:8]} {row['action']} "
                f"{row['decision']} {reason} actor={row['actor']}"
                f"{'' if row['actor_verified'] else ' (unverified)'}"
            )
    return 0
