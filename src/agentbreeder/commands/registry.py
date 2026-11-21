"""`agentbreeder registry new|publish|import|fork|edit|list|runtimes|verify|show|diff|used-by|
trust`: author and inspect registry versions, and keep the user's trusted-source list."""

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
    reg_new.add_argument(
        "--scripts",
        action="store_true",
        help="skill only: add one declared example script (a script skill is code)",
    )
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
    reg_runtimes = reg_commands.add_parser(
        "runtimes",
        help="List runtime versions with what each supports (framework, capabilities, limits)",
    )
    reg_conformance = reg_commands.add_parser(
        "conformance", help="Show the newest conformance report of a runtime version"
    )
    reg_conformance.add_argument("ref", help="runtime/name@X.Y.Z")
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
    reg_trust = reg_commands.add_parser(
        "trust", help="Trust a remote registry source URL prefix (your sources.yaml)"
    )
    reg_trust.add_argument("url", nargs="?", metavar="URL", help="scheme://host/path-prefix")
    trust_mode = reg_trust.add_mutually_exclusive_group()
    trust_mode.add_argument("--list", action="store_true", help="List trusted prefixes")
    trust_mode.add_argument("--remove", action="store_true", help="Remove URL from the list")
    reg_trust.add_argument(
        "--whole-host", action="store_true", help="Allow a bare host (trusts every repository)"
    )
    reg_trust.add_argument(
        "--private", action="store_true", help="Allow a host on a private or loopback address"
    )
    reg_trust.add_argument("--yes", action="store_true", help="Do not ask for confirmation")
    reg_trust.add_argument("--json", action="store_true")
    placing = (reg_publish, reg_import, reg_fork, reg_edit)
    listing = (reg_list, reg_runtimes, reg_conformance, reg_verify, reg_show, reg_diff, reg_used)
    for command in (*placing, *listing):
        command.add_argument("--json", action="store_true")
    for command in (reg_list, reg_runtimes, reg_verify, reg_show, reg_diff):
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
        if args.registry_command == "conformance":  # reads no registry root
            return _conformance(args)
        if args.registry_command == "new":
            path = registry.scaffold(args.kind, args.name, args.dir, scripts=args.scripts)
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
        if args.registry_command == "trust":
            return _trust(args)
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
                    code = "  (code)" if row["runs_code"] else ""
                    print(f"{row['ref']}  {row['root']}{code}{refused}")
            return 0
        if args.registry_command == "runtimes":
            from agentbreeder import runtime_profile

            view = _with_conformance(runtime_profile.listing(search, args.project))
            print(json.dumps(view, indent=2) if args.json else _runtimes_text(view))
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
        print(_notice(result["kind"], result["runs_code"]), file=sys.stderr)
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
        for line in _edit_next(result["source_ref"], result["ref"], args.project):
            print(line)
    return code


# The files whose ref fields can name a version of each kind (registry._ref_positions):
# `agent set-ref` edits only agent.yaml; nothing edits a stack.yaml ref yet (#990).
_REF_FILES = {
    "runtime": ("agent.yaml",),
    "prompt": ("agent.yaml",),
    "eval": ("agent.yaml",),
    "knowledge": ("agent.yaml",),
    "tool": ("agent.yaml",),
    "mcp": ("agent.yaml", "stack.yaml"),
    "service": ("stack.yaml",),
}


def _edit_next(old: str, new: str, project: Path) -> list[str]:
    """The Next: lines after an edit, from where the project declares OLD (#990). The
    `agent set-ref` command only when its own dry run passes (it edits only agent.yaml, and
    refuses OLD declared twice, NEW's name used elsewhere or an invalid file); else the field to
    change by hand in agent.yaml or stack.yaml. When nothing declares OLD, which file can
    reference NEW; when a file could not be read, no guess."""
    from agentbreeder import hints, setref

    ref = registry.Ref.parse(old)
    row = registry.usage(ref, project)
    declared = row["declared"]
    lines = []
    in_agent = [entry for entry in declared if entry["file"] == "agent.yaml"]
    refused = ""
    if in_agent:
        try:
            setref.set_ref(project, old, new, dry_run=True)
        except registry.RegistryError as error:
            refused = f" (agent set-ref refuses: {error.code})"
        except RecursionError:  # a pathological file must not crash after the publish
            refused = " (agent set-ref refuses: AGENT_INVALID)"
        else:
            extra = [] if project == Path(".") else ["--project", project]
            lines.append(hints.next_line("agent", "set-ref", old, new, *extra))
            in_agent = []
    for entry in in_agent:
        lines.append(
            hints.next_note(f"in agent.yaml, change {entry['at']} from {old} to {new}{refused}")
        )
    for entry in declared:
        if entry["file"] == "stack.yaml":
            lines.append(
                hints.next_note(f"in stack.yaml, change {entry['at']} from {old} to {new}")
            )
    if not lines and not row["errors"]:
        files = " or ".join(_REF_FILES.get(ref.kind, ()))
        if files:
            lines.append(hints.next_note(f"reference {new} from a ref field in {files}"))
    return lines


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


def _notice(kind: str, runs_code: bool) -> str:
    """`runs_code` is the place result's, decided from the staged files (a skill is code only
    when it has scripts, skills spec §5.2)."""
    if runs_code:
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


_CONFORMANCE = {"pass": "passed", "fail": "failed", "incomplete": "incomplete"}


def _conformance(args) -> int:
    """`registry conformance REF`: the newest report for an image built on a runtime version
    (#1085 S3, console spec §6.3), read from `~/.agentbreeder/conformance`. `--json` prints
    `{schema_version, ref, report}`, `report` null when there is none."""
    from agentbreeder.conformance import report

    ref = registry.Ref.parse(args.ref)
    if ref.kind != "runtime":
        raise registry.RegistryError("REF_INVALID", f"{args.ref!r} is not a runtime/ ref")
    found = report.newest_report(str(ref))
    if args.json:
        print(json.dumps({"schema_version": 1, "ref": str(ref), "report": found}, indent=2))
    elif found is None:
        print(f"No conformance report for {ref}.")
    else:
        print(report.text(found))
    return 0


def _with_conformance(view: dict) -> dict:
    """Add `conformance` ({passed, result, digest, date}) to each usable runtime version that
    has a report: the newest one for an image built on it (#759, console spec §8). Reports are
    read from the default state directory, `~/.agentbreeder/conformance`."""
    from agentbreeder.conformance import report

    for entry in view["runtimes"]:
        newest = report.newest(entry["ref"]) if entry["usable"] else None
        if newest is not None:
            entry["conformance"] = newest
    return view


def _runtimes_text(view: dict) -> str:
    """One block per runtime version. Every value from a manifest is cleaned: a project or
    path registry is not trusted text."""

    def shown(value) -> str:
        return registry.clean(str(value), name=True)[0]

    lines = []
    for entry in view["runtimes"]:
        source = "" if entry["root_kind"] == "builtin" else f"  ({entry['root_kind']} registry)"
        if not entry["usable"]:
            lines.append(
                f"{entry['ref']}  cannot be used: {shown(entry['reason'])} "
                f"({entry['code']}){source}"
            )
            continue
        kit = f", kit {shown(entry['kit'])}" if "kit" in entry else ""
        status = "run end to end" if entry["verified"] else "not run end to end"
        if "conformance" in entry:
            status = f"conformance {_CONFORMANCE[entry['conformance']['result']]}; {status}"
        lines.append(
            f"{entry['ref']}  {shown(entry['display_name'])}  protocol {entry['protocol']} "
            f"({entry['profile']}{kit})  {status}{source}"
        )
        capabilities = ", ".join(entry["capabilities"]) or "none"
        limits = ", ".join(f"{part} {count}" for part, count in entry["limits"].items())
        lines.append(f"  capabilities: {capabilities}; limits: {limits}")
        if entry["reserved_tool_names"]:
            lines.append(f"  reserved tool names: {', '.join(entry['reserved_tool_names'])}")
        if "notes" in entry:
            lines.append(f"  note: {shown(entry['notes'])}")
    return "\n".join(lines) if lines else "No runtime versions in the search roots."


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
        notes.append("control, bidi or invisible characters removed")
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
    if "skill" in view:
        lines += _skill_text(view)
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


def _skill_text(view: dict) -> list[str]:
    """A skill's front matter and script tools for a terminal (values cleaned by registry.show;
    the body and references are printed with the files below)."""
    if view["skill"] is None:
        return [f"Skill: not valid: {excerpt(view['skill_error'], 300)}"]
    skill = view["skill"]
    code = "code: scripts" if skill["runs_code"] else "instruction pack, no code"
    lines = [
        f"Skill: {excerpt(skill['name'], 64)} ({code})",
        f"  Always in context: {excerpt(skill['description'], 1024)}",
    ]
    for key in ("license", "compatibility"):
        if skill[key]:
            lines.append(f"  {key.capitalize()}: {excerpt(skill[key], 300)}")
    for script in skill["scripts"]:
        lines.append(
            f"  Script {excerpt(script['path'], 100)} -> tool {excerpt(script['tool'], 64)}: "
            f"{excerpt(script['description'], 300)}"
        )
    return lines


def _trust(args) -> int:
    """`registry trust` (#829 spec §8.3): edits only the user's own sources.yaml."""
    from agentbreeder.sources import trust
    from agentbreeder.sources.url import SourceUrlError

    path = trust.sources_file()
    items = trust.file_items(path)
    if args.list:
        if args.url:
            print("registry trust --list takes no URL", file=sys.stderr)
            return 2
        rows = items + [
            {"url": entry.prefix, "private": False, "whole_host": False, "from": trust.ENV}
            for entry in trust.entries()
            if entry.origin == "env"
        ]
        if args.json:
            print(json.dumps(rows, indent=2))
        for row in [] if args.json else rows:
            flags = [k for k in ("private", "whole_host") if row.get(k)]
            origin = f"  ({row['from']})" if "from" in row else ""
            print(row["url"] + (f"  [{', '.join(flags)}]" if flags else "") + origin)
        if not rows and not args.json:
            print("No trusted registry sources.")
        return 0
    if not args.url:
        print("registry trust needs a URL (or --list)", file=sys.stderr)
        return 2
    try:
        value, _ = trust.prefix(args.url, whole_host=args.whole_host or args.remove)
    except SourceUrlError as error:
        print(f"[SOURCE_INVALID] {registry.clean(str(error))[0]}", file=sys.stderr)
        return 2
    if args.remove:
        kept = [item for item in items if item["url"] != value]
        if len(kept) == len(items):
            print(f"{value} is not in {path}", file=sys.stderr)
            return 2
        trust.write(path, kept)
        print(f"Removed {value} from {path}")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print(
                "registry trust changes what your machine may fetch; re-run with --yes",
                file=sys.stderr,
            )
            return 2
        warning = (
            f"Trust {value}{' on a private address' if args.private else ''}? Builds may then "
            "fetch registry versions from it; tool code runs inside your agents and eval sets "
            "decide release gates. [y/N] "
        )
        try:
            answer = input(warning)
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Not trusted.")
            return 1
    entry = {"url": value, "private": args.private, "whole_host": args.whole_host}
    items = [item for item in items if item["url"] != value] + [entry]
    trust.write(path, items)
    print(f"Trusted {value} in {path}")
    return 0
