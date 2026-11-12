"""`agentbreeder registry new|publish|list|verify|show`: author and inspect registry versions."""

from __future__ import annotations

import json
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
    for command in (reg_publish, reg_list, reg_verify, reg_show):
        command.add_argument("--json", action="store_true")
    for command in (reg_list, reg_verify, reg_show):
        command.add_argument("--project", type=Path, default=Path("."))


def run(args) -> int:
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
        if args.registry_command == "show":
            view = registry.show(registry.Ref.parse(args.ref), search, args.project)
            print(json.dumps(view, indent=2) if args.json else _show_text(view))
            return 0
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
