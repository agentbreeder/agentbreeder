"""Local development lifecycle commands with the deploy-time identity gate."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from agentbreeder import __version__, agentfile, registry, secret_files
from agentbreeder.deployer import DeployerError, device_login, os_deployer, resolve_deployer
from agentbreeder.deployment import DeploymentService
from agentbreeder.doctor import check_docker
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
    stateful("remove", "Remove a deployment", "id", local=True)
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
    stateful("reconcile", "Re-verify principals and retry blocked cleanup", local=True)
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
        "set", help="Store a secret value read from stdin (never from arguments)"
    )
    secret_set.add_argument("name")
    secret_set.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    login = commands.add_parser("login", help="Sign in with the operator's OIDC device flow")
    login.add_argument("--issuer")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
        checks = check_docker()
        if args.json:
            print(json.dumps([asdict(check) for check in checks], indent=2))
        else:
            for check in checks:
                print(f"{'PASS' if check.passed else 'FAIL'} {check.name}: {check.message}")
        return 0 if all(check.passed for check in checks) else 1
    if args.command == "registry":
        return _registry(args)
    if args.command == "build":
        return _build(args)
    if args.command == "eval":
        return _eval(args)
    if args.command == "release":
        return _release(args)
    if args.command == "secret":
        data = sys.stdin.buffer.read(secret_files.MAX_BYTES + 2)
        if data.endswith(b"\n"):
            data = data[:-1]  # `echo value |` and here-strings add one newline
        try:
            path = secret_files.set_secret(args.state_dir, args.name, data)
        except secret_files.SecretError as error:
            print(f"[{error.code}] {error}", file=sys.stderr)
            return 2
        print(f"Stored secret {args.name} at {path} (0600). Reference it as secret://{args.name}")
        return 0
    if args.command == "login":
        try:
            deployer = device_login(load_operator_config(), issuer=args.issuer)
        except (DeployerError, OperatorConfigError) as error:
            print(_message(error), file=sys.stderr)
            return 2
        print(f"Signed in as {deployer.label}")
        return 0
    return _stateful(args)


def _file_kind(path: Path) -> str:
    try:
        return agentfile.read(path)[0]
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
            print("OK: every registry version is intact")
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
    """1 when blocked cleanup still fails; 4 when any deployment is noncompliant or cannot be
    verified (so scripts and CI can gate on drift); otherwise 0."""
    compliance = [r["compliance"] or "" for r in results]
    if any(c.endswith(":cleanup_failed") for c in compliance):
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
