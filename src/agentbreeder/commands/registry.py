"""`agentbreeder registry new|publish|import|fork|edit|list|verify|show|diff|used-by`: author
and inspect registry versions."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path

from agentbreeder import registry
from agentbreeder.safe_text import excerpt

NAMES = ("registry",)


def register(commands) -> None:
    reg = commands.add_parser("registry", help="Author and inspect registry versions")
    reg_commands = reg.add_subparsers(dest="registry_command", required=True)
    reg_new = reg_commands.add_parser("new", help="Scaffold a version directory to edit")
    reg_new.add_argument("kind", type=_scaffold_kind, choices=sorted(registry.SCAFFOLD))
    reg_new.add_argument("name")
    reg_new.add_argument("--dir", type=Path, default=Path("."))
    reg_publish = reg_commands.add_parser("publish", help="Publish an immutable version")
    reg_publish.add_argument("source", type=Path)
    reg_publish.add_argument("--to", type=Path, default=Path("registry"), help="Registry root")
    reg_import = reg_commands.add_parser(
        "import", help="Copy a version directory from anywhere into the project registry"
    )
    reg_import.add_argument("source", metavar="PATH", type=Path, help="A version directory")
    reg_import.add_argument("--as", dest="name", metavar="NAME", help="Import under this name")
    reg_import.add_argument("--version", metavar="X.Y.Z", help="Import as this version")
    reg_import.add_argument("--project", type=Path, default=Path("."))
    reg_fork = reg_commands.add_parser(
        "fork", help="Copy a built-in or shared version into the project registry to change"
    )
    reg_fork.add_argument("ref", help="kind/name@X.Y.Z")
    reg_fork.add_argument("--as", dest="name", metavar="NAME", help="Fork under this name")
    reg_fork.add_argument("--version", metavar="X.Y.Z", help="Fork as this version")
    reg_fork.add_argument("--project", type=Path, default=Path("."))
    reg_edit = reg_commands.add_parser(
        "edit", help="Change files of a project version and publish it as a new version"
    )
    reg_edit.add_argument("ref", help="kind/name@X.Y.Z in the project registry")
    reg_edit.add_argument(
        "--set",
        dest="sets",
        nargs=2,
        action="append",
        default=[],
        metavar=("PATH", "FILE"),
        help="Replace or add PATH in the version with FILE's bytes (FILE - reads stdin once)",
    )
    reg_edit.add_argument(
        "--remove",
        dest="removes",
        action="append",
        default=[],
        metavar="PATH",
        help="Remove the file PATH from the version",
    )
    version = reg_edit.add_mutually_exclusive_group()
    version.add_argument(
        "--bump", choices=registry.BUMPS, default="patch", help="Version part to raise (patch)"
    )
    version.add_argument("--version", metavar="X.Y.Z", help="Publish as exactly this version")
    reg_edit.add_argument(
        "--context", type=_context, default=3, metavar="N", help="Diff context lines (default 3)"
    )
    reg_edit.add_argument("--project", type=Path, default=Path("."))
    for command in (reg_publish, reg_import, reg_fork, reg_edit):
        mode = command.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run", action="store_true", help="Check and hash a staged copy; write nothing"
        )
        mode.add_argument(
            "--expect-digest",
            type=_digest,
            metavar="SHA256",
            help="Refuse unless the staged copy has this digest (from --dry-run)",
        )
    reg_list = reg_commands.add_parser("list", help="List versions in the search roots")
    reg_list.add_argument("prefix", nargs="?", default="")
    reg_verify = reg_commands.add_parser("verify", help="Re-check every version's integrity")
    reg_show = reg_commands.add_parser("show", help="Show one version's manifest and files")
    reg_show.add_argument("ref", help="kind/name@X.Y.Z")
    reg_diff = reg_commands.add_parser("diff", help="Show what changed between two versions")
    reg_diff.add_argument("first", metavar="FROM", help="kind/name@X.Y.Z")
    reg_diff.add_argument("second", metavar="TO", help="kind/name@X.Y.Z")
    reg_diff.add_argument(
        "--context", type=_context, default=3, metavar="N", help="Lines of context (default 3)"
    )
    reg_used = reg_commands.add_parser(
        "used-by", help="Show which projects declare, lock and deploy a version"
    )
    reg_used.add_argument("ref", help="kind/name@X.Y.Z")
    reg_used.add_argument(
        "--project",
        dest="projects",
        type=Path,
        action="extend",
        nargs="+",
        metavar="DIR",
        help="Projects to look in (default: .)",
    )
    reg_used.add_argument(
        "--state-dir",
        dest="state_dirs",
        type=Path,
        action="append",
        metavar="DIR",
        help="Local deploy state to read (default: the local state); a project's stack state "
        "is always read",
    )
    placing = (reg_publish, reg_import, reg_fork, reg_edit)
    for command in (*placing, reg_list, reg_verify, reg_show, reg_diff, reg_used):
        command.add_argument("--json", action="store_true")
    for command in (reg_list, reg_verify, reg_show, reg_diff):
        command.add_argument("--project", type=Path, default=Path("."))


def run(args) -> int:
    try:
        code = _run(args)
        sys.stdout.flush()  # small output is only written here: a closed pipe must fail inside
        return code
    except BrokenPipeError:
        # `registry show … | head` or `| true`: the reader left; stop quietly. Point stdout at
        # /dev/null so the interpreter's final flush does not fail again.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
        return 0


def _run(args) -> int:
    try:
        if args.registry_command == "new":
            path = registry.scaffold(args.kind, args.name, args.dir)
            print(f"Created {path}. Edit it, then: agentbreeder registry publish {path}")
            return 0
        if args.registry_command == "publish":
            result = registry.place(
                args.source,
                args.to,
                expect_digest=args.expect_digest,
                dry_run=args.dry_run,
                project_dir=registry.publish_project(args.to),  # root_kind only (#898)
            )
            return _placed(args, result, "publish", notice=False)
        if args.registry_command == "import":
            result = registry.import_version(
                args.source,
                args.project,
                name=args.name,
                version=args.version,
                expect_digest=args.expect_digest,
                dry_run=args.dry_run,
            )
            return _placed(args, result, "import", notice=True)
        if args.registry_command == "fork":
            result = registry.fork_version(
                registry.Ref.parse(args.ref),
                args.project,
                name=args.name,
                version=args.version,
                expect_digest=args.expect_digest,
                dry_run=args.dry_run,
            )
            return _placed(args, result, "fork", notice=True)
        if args.registry_command == "edit":
            return _edit(args)
        if args.registry_command == "used-by":
            return _used_by(args)
        search = registry.roots(args.project)
        if args.registry_command == "show":
            view = registry.show(registry.Ref.parse(args.ref), search, args.project)
            print(json.dumps(view, indent=2) if args.json else _show_text(view))
            return 0
        if args.registry_command == "diff":
            first, second = registry.Ref.parse(args.first), registry.Ref.parse(args.second)
            result = registry.diff(first, second, search, args.context, args.project)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(registry.unified(result), end="")
            return 0
        if args.registry_command == "list":
            rows = registry.listing(search, args.prefix)
            if args.json:
                print(json.dumps(rows, indent=2))
            else:
                for row in rows:
                    refused = "  (symlinked directory: refused)" if row["symlinked"] else ""
                    print(f"{row['ref']}  {row['root']}{refused}")
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
        # RegistryError already cleans its message; clean again in case a subclass or a
        # wrapped error did not (defence in depth, S-27).
        print(registry.clean(str(error))[0], file=sys.stderr)
        return 2


def _placed(args, result: dict, verb: str, notice: bool) -> int:
    """`publish`/`import` output. Text for a write keeps publish's `REF SHA256 PATH` line."""
    if notice:
        print(_notice(result["kind"]), file=sys.stderr)
    if args.json:
        # publish keeps its one-line JSON (scripts read the last line); import, fork and edit
        # match `show`.
        one_line = args.registry_command == "publish"
        print(json.dumps(result, indent=None if one_line else 2))
        return 0
    line = f"{result['ref']} {result['sha256']} {result['path']}"
    if result["source_ref"] != result["ref"]:
        line += f" (from {result['source_ref']})"
    if result["dry_run"]:
        line = f"Would {verb} {line} (dry run: nothing written)"
        line += f"\nTo write exactly this: --expect-digest {result['sha256']}"
    print(line)
    return 0


def _edit(args) -> int:
    """`registry edit`: the diff first (text mode), then the placed line and a Next: step."""
    from agentbreeder import hints

    ref = registry.Ref.parse(args.ref)
    if sum(1 for _, source in args.sets if source == "-") > 1:
        raise registry.RegistryError("EDIT_INVALID", "only one --set may read stdin (-)")
    sets = [(registry.edit_path(path), _input(source)) for path, source in args.sets]
    result = registry.edit_version(
        ref,
        args.project,
        sets=sets,
        removes=args.removes,
        bump=args.bump,
        version=args.version,
        expect_digest=args.expect_digest,
        dry_run=args.dry_run,
        context=args.context,
    )
    if not args.json:
        print(registry.unified(result["diff"]), end="")
    code = _placed(args, result, "publish", notice=True)  # "Would publish …" in a dry run
    if not args.json and not result["dry_run"]:
        print(hints.next_line("agent", "set-ref", result["source_ref"], result["ref"]))
    return code


def _input(source: str) -> bytes:
    """--set FILE's bytes, or stdin for `-`: a regular file only (never a FIFO or device that
    could hang the read), capped at the version size limit."""
    limit = registry.MAX_BYTES
    if source == "-":
        data = sys.stdin.buffer.read(limit + 1)
    else:
        try:
            descriptor = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as reader:
                if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
                    raise registry.RegistryError(
                        "EDIT_INVALID", f"{registry.clean(source, name=True)[0]} is not a file"
                    )
                data = reader.read(limit + 1)
        except OSError as error:
            raise registry.RegistryError(
                "EDIT_INVALID",
                f"cannot read {registry.clean(source, name=True)[0]}: {error.strerror or error}",
            ) from None
    if len(data) > limit:
        raise registry.RegistryError("ENTRY_UNSAFE", "registry version is too large")
    return data


def _notice(kind: str) -> str:
    if registry.runs_code(kind):
        return (
            f"Note: a {kind} version is code that runs inside agents with their privileges. "
            "Review it before an agent uses it; it takes effect only after `release` passes."
        )
    return f"Note: a {kind} version steers the agent. Review it before an agent uses it."


def _digest(text: str) -> str:
    if not registry.DIGEST.fullmatch(text):
        raise argparse.ArgumentTypeError("must be sha256:<64 hex digits>")
    return text


def _used_by(args) -> int:
    """Declared and locked come from registry.usage (pure, console-callable); deployed from
    registry_deployed, which reads local deploy state read-only."""
    from agentbreeder import cli, registry_deployed

    ref = registry.Ref.parse(args.ref)
    extra = args.state_dirs or [cli.DEFAULT_STATE]
    projects: dict[Path, Path] = {}
    for project in args.projects or [Path(".")]:
        projects.setdefault(project.resolve(), project)  # each project once
    rows = []
    for project in projects.values():
        row = registry.usage(ref, project)
        directories = registry_deployed.state_dirs(project, row["stack"], extra)
        rows.append(registry_deployed.deployed(ref, row, project, directories))
    result = {"ref": str(ref), "projects": rows}
    print(json.dumps(result, indent=2) if args.json else _used_by_text(result))
    return 2 if any(row["errors"] for row in rows) else 0


def _deployment_line(entry: dict) -> str:
    where = ", ".join(part for part in (entry["target"], entry["environment"]) if part)
    return f"{entry['deployment']}  {entry['name']}  {entry['state']} ({where})  {entry['image']}"


def _used_by_text(result: dict) -> str:
    lines = [result["ref"]]
    for row in result["projects"]:
        names = [f"{kind} {row[kind]}" for kind in ("agent", "stack") if row[kind]]
        lines += ["", row["project"] + (f"  ({', '.join(names)})" if names else "")]
        for entry in row["declared"]:
            lines.append(f"  declared      {entry['file']} at {entry['at']}")
        for entry in row["locked"]:
            lines.append(f"  locked        {entry['file']} {entry['sha256']} ({entry['source']})")
        for entry in row["deployed"]:
            lines.append(f"  deployed      {_deployment_line(entry)}")
        for entry in row["recorded_not_running"]:
            lines.append(f"  not running   {_deployment_line(entry)}")
        for entry in row["deployed_undetermined"]:
            lines.append(f"  undetermined  {_deployment_line(entry)}: {entry['reason']}")
        if not any(row[k] for k in _BUCKETS):
            lines.append("  not used")
        for entry in row["errors"]:
            lines.append(f"  error         {entry['file'] or row['project']}: {entry['error']}")
    lines += [
        "",
        "Deployed comes from recorded local state (the last observation, not a live check) and "
        "the project's build record; only agent images are checked.",
    ]
    return "\n".join(lines)


_BUCKETS = ("declared", "locked", "deployed", "recorded_not_running", "deployed_undetermined")


def _scaffold_kind(text: str) -> str:
    # Before argparse's `choices` check, so `registry new agent` names the reason, not only
    # "invalid choice".
    if text == registry.PACKAGE_KIND:
        raise argparse.ArgumentTypeError(
            f"[{registry.PACKAGE_RELEASE_ONLY}] agent/ versions are written by release"
        )
    return text


def _context(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = -1
    if not 0 <= value <= registry.MAX_CONTEXT:
        raise argparse.ArgumentTypeError(f"must be 0 to {registry.MAX_CONTEXT}")
    return value


def _file_note(entry: dict) -> str:
    notes = []
    if entry["executable"]:
        notes.append("executable")
    if entry["binary"]:
        notes.append("binary, not shown")
    if entry["omitted"]:
        notes.append("not shown: output limit reached")
    elif entry["truncated"]:
        notes.append(f"first {registry.SHOW_FILE_LIMIT // 1024} KiB shown")
    if entry["stripped"]:
        notes.append("control or bidi characters removed")
    return f"  ({', '.join(notes)})" if notes else ""


def _show_text(view: dict) -> str:
    """`registry show` for a terminal; every string was already cleaned by registry.show."""
    lines = [
        f"{view['ref']}  {view['sha256']}",
        f"Root: {view['root']} ({view['root_kind']})",
    ]
    if view["description"]:
        lines.append(f"Description: {registry.clean(view['description'], name=True)[0]}")
    lines.append("Files:")
    for entry in view["files"]:
        if entry["type"] == "dir":
            lines.append(f"  {entry['path']}/")
        else:
            lines.append(f"  {entry['path']}  {entry['size']} B{_file_note(entry)}")
    if "package" in view:
        lines += _package_text(view)
    if "functions" in view:
        lines += _functions_text(view)
    for entry in view["files"]:
        if entry["type"] == "file" and entry["text"] is not None:
            lines += ["", f"--- {entry['path']} ---", entry["text"].rstrip("\n")]
    return "\n".join(lines)


def _package_text(view: dict) -> list[str]:
    """An `agent/` package's record for a terminal (values cleaned by registry.show)."""
    if view["package"] is None:
        return [f"Package: not valid: {view['package_error']}"]
    record = view["package"]

    def one(value) -> str:
        return registry.clean(str(value), name=True)[0]

    evals, deploy = record["evals"], record["deploy"]
    lines = [
        "Package:",
        f"  Runtime: {one(record['agent']['runtime'])}",
        f"  Image: {one(record['image']['id'])} ({one(record['image']['platform'])})",
        f"  Evals: {one(evals['ref'])} passed {evals['passed']}/{evals['cases']} "
        f"(threshold {evals['threshold']}, {one(evals['environment'])}, "
        f"run {one(evals['run_id'])})",
        f"  Released: {one(record['released']['at'])} by AgentBreeder "
        f"{one(record['released']['agentbreeder'])}",
        f"  Models: {', '.join(one(alias) for alias in deploy['aliases'])}",
    ]
    if deploy["mcp_access"]:
        lines.append(f"  MCP: {', '.join(one(entry['ref']) for entry in deploy['mcp_access'])}")
    return lines


def _functions_text(view: dict) -> list[str]:
    """A tool's functions for a terminal. Descriptions come from tool.yaml (untrusted), so every
    value is printed as one capped line."""
    if view["functions_error"]:
        return [f"Functions: not readable: {excerpt(view['functions_error'], 300)}"]
    lines = ["Functions:"]
    for entry in view["functions"]:
        signature = entry["signature"] or f"(signature not shown: {entry['signature_error']})"
        lines.append(
            f"  {excerpt(entry['name'], 64)}  {excerpt(entry['module'], 64)}: "
            f"{excerpt(signature, 1000)}"
        )
        if entry["description"]:
            lines.append(f"    {excerpt(entry['description'], 300)}")
    return lines
