"""`agentbreeder registry new|publish|list|verify|show|diff|used-by`: author and inspect
registry versions."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agentbreeder import registry

NAMES = ("registry",)


def register(commands) -> None:
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
    for command in (reg_publish, reg_list, reg_verify, reg_show, reg_diff, reg_used):
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
            ref, digest, path = registry.publish(args.source, args.to)
            result = {"ref": str(ref), "sha256": digest, "path": str(path)}
            print(json.dumps(result) if args.json else f"{ref} {digest} {path}")
            return 0
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
    for entry in view["files"]:
        if entry["type"] == "file" and entry["text"] is not None:
            lines += ["", f"--- {entry['path']} ---", entry["text"].rstrip("\n")]
    return "\n".join(lines)
