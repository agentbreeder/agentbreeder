"""Changelog fragments (#893): one file per entry in changelog.d/, folded in at release.

    python scripts/changelog.py lint
        check every fragment's name and content
    python scripts/changelog.py check --labels-json '["release"]' --author LOGIN
        for a pull request (CI): HEAD must be GitHub's test-merge commit; reads the PR's changes as
        HEAD^1..HEAD. Fails when CHANGELOG.md is edited without the `release` label, or when a
        non-trivial PR adds or edits no fragment. Also runs `lint`.
    python scripts/changelog.py fold [--dry-run]
        at release: insert every fragment into CHANGELOG.md's [Unreleased] under its type's
        heading and delete the fragments (--dry-run prints the result and changes nothing)

A fragment is `changelog.d/<issue>.<type>.md` or `changelog.d/<issue>.<slug>.<type>.md`, holding
one Markdown list item exactly as it will appear in CHANGELOG.md.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAGMENTS = "changelog.d"
CHANGELOG = "CHANGELOG.md"
# Headings in the order [Unreleased] lists them today; `fold` creates a missing one in this order.
TYPES = {"added": "Added", "changed": "Changed", "internal": "Internal", "fixed": "Fixed"}
NOT_FRAGMENTS = {"README.md", ".gitkeep"}
MARKING = "not in a published preview"
RELEASE_LABEL = "release"
EXEMPT_AUTHORS = {"dependabot[bot]"}
# Paths a PR may change without a fragment: specs, plans, verification records, agent guides.
TRIVIAL = (
    "docs/superpowers/*",
    f"{FRAGMENTS}/*",
    "CLAUDE.md",
    "AGENTS.md",
    ".agents/*",
    ".gitignore",
)
_NAME = re.compile(r"^([1-9][0-9]*)(?:\.([a-z0-9][a-z0-9-]*))?\.(" + "|".join(TYPES) + r")\.md$")
_UNRELEASED = re.compile(r"^## \[Unreleased\][ \t]*$", re.MULTILINE)
_NEXT_RELEASE = re.compile(r"^## ", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}(\s|$)")  # unindented: a heading that would end the list
_ITEM = re.compile(r"^([-*+]|\d+[.)])\s")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Fragment:
    path: Path
    issue: int
    type: str
    text: str

    @property
    def sort_key(self) -> tuple[int, str]:
        return (-self.issue, self.path.name)  # newest issue first, as [Unreleased] lists today


def fragment_files(root: Path) -> list[Path]:
    directory = root / FRAGMENTS
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.name not in NOT_FRAGMENTS)


def lint_one(path: Path) -> tuple[Fragment | None, list[str]]:
    """The fragment, or None and why not."""
    name = f"{FRAGMENTS}/{path.name}"
    match = _NAME.match(path.name)
    if path.is_symlink() or not path.is_file():
        return None, [f"{name}: not a regular file"]
    if not match:
        return None, [
            f"{name}: name must be <issue>.<type>.md or <issue>.<slug>.<type>.md, "
            f"type one of {', '.join(TYPES)}, slug [a-z0-9-]"
        ]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, [f"{name}: unreadable ({error.__class__.__name__})"]
    text = text.replace("\r\n", "\n").strip("\n")
    errors = []
    if not text.startswith("- "):
        errors.append(f"{name}: must be one Markdown list item starting with '- '")
    lines, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            lines.append(line)
    if any(_HEADING.match(line) for line in lines):
        errors.append(f"{name}: must not contain headings")
    if any(_ITEM.match(line) for line in lines[1:]) or any(
        previous == "" and line[:1] not in ("", " ", "\t")
        for previous, line in zip(lines, lines[1:], strict=False)
    ):
        errors.append(
            f"{name}: one entry per fragment (a second item or an unindented paragraph was found)"
        )
    if MARKING not in " ".join(text.split()).lower():
        errors.append(f"{name}: must say it is on `main`, not in a published preview")
    if errors:
        return None, errors
    return Fragment(path, int(match.group(1)), match.group(3), text), []


def lint(root: Path) -> tuple[list[Fragment], list[str]]:
    fragments, errors = [], []
    for path in fragment_files(root):
        fragment, problems = lint_one(path)
        errors += problems
        if fragment:
            fragments.append(fragment)
    return fragments, errors


def check_changes(changes: list[tuple[str, str]], labels: list[str], author: str) -> list[str]:
    """Problems with a PR's changes: (status letter, path) pairs from git diff --name-status."""
    paths = {path for _, path in changes}
    release = RELEASE_LABEL in labels
    errors = []
    if CHANGELOG in paths and not release:
        errors.append(
            f"{CHANGELOG} is edited directly. Add a fragment in {FRAGMENTS}/ instead "
            f"(see {FRAGMENTS}/README.md); only a release PR (label '{RELEASE_LABEL}') edits it."
        )
    if release or author in EXEMPT_AUTHORS:
        return errors
    nontrivial = sorted(p for p in paths if not any(fnmatch.fnmatch(p, t) for t in TRIVIAL))
    fragment = any(
        status in ("A", "M")
        and path.startswith(f"{FRAGMENTS}/")
        and _NAME.match(path.split("/", 1)[1])
        for status, path in changes
    )
    if nontrivial and not fragment:
        errors.append(
            f"no changelog fragment: this PR changes {nontrivial[0]}"
            + (f" and {len(nontrivial) - 1} more" if len(nontrivial) > 1 else "")
            + f". Add {FRAGMENTS}/<issue>.<type>.md (see {FRAGMENTS}/README.md)."
        )
    return errors


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def pr_changes(root: Path) -> list[tuple[str, str]]:
    parents = _git(root, "rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(parents) != 3:
        raise SystemExit(
            "check: HEAD is not a merge commit; run it on GitHub's pull_request test merge "
            "(actions/checkout with fetch-depth: 2)"
        )
    out = _git(root, "diff", "--name-status", "--no-renames", "-z", "HEAD^1", "HEAD")
    fields = out.split("\0")
    return [(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]


def fold_text(changelog: str, fragments: list[Fragment]) -> str:
    """CHANGELOG.md with the fragments added at the top of their [Unreleased] headings."""
    start = _UNRELEASED.search(changelog)
    if not start:
        raise ValueError(f"{CHANGELOG} has no '## [Unreleased]' heading")
    body_start = start.end()
    after = _NEXT_RELEASE.search(changelog, body_start)
    end = after.start() if after else len(changelog)
    section = changelog[body_start:end]
    # A trailing `---` rule belongs between releases, not inside [Unreleased].
    rule = re.search(r"\n---[ \t]*\n*\Z", section)
    if rule:
        end -= len(section) - rule.start() - 1
        section = section[: rule.start() + 1]
    # Each type's entries go under its own heading, so the order between types does not matter.
    for type_ in TYPES:
        entries = sorted((f for f in fragments if f.type == type_), key=lambda f: f.sort_key)
        if entries:
            section = _insert(section, TYPES[type_], "".join(f.text + "\n\n" for f in entries))
    return changelog[:body_start] + section + changelog[end:]


def _insert(section: str, heading: str, entries: str) -> str:
    match = re.search(rf"^### {re.escape(heading)}[ \t]*\n", section, re.MULTILINE)
    if match:
        at = match.end()
        while section.startswith("\n", at):
            at += 1
        return section[:at].rstrip("\n") + "\n\n" + entries + section[at:]
    # A new heading goes before the first existing heading that comes later in TYPES' order.
    order = list(TYPES.values())
    later = order[order.index(heading) + 1 :]
    positions = [
        m.start()
        for m in re.finditer(r"^### (.+?)[ \t]*$", section, re.MULTILINE)
        if m.group(1) in later
    ]
    block = f"### {heading}\n\n{entries}"
    if positions:
        at = min(positions)
        return section[:at] + block + section[at:]
    trailer = section[len(section.rstrip("\n")) :] or "\n"
    return section.rstrip("\n") + "\n\n" + block.rstrip("\n") + trailer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("lint")
    check = commands.add_parser("check")
    check.add_argument("--labels-json", default="[]")
    check.add_argument("--author", default="")
    fold = commands.add_parser("fold")
    fold.add_argument("--dry-run", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.root

    fragments, errors = lint(root)
    if args.command == "check":
        labels = json.loads(args.labels_json)
        if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            parser.error("--labels-json must be a JSON list of strings")
        errors += check_changes(pr_changes(root), labels, args.author)
    if errors:
        for error in errors:
            print(f"changelog: {error}", file=sys.stderr)
        return 1
    if args.command == "fold":
        path = root / CHANGELOG
        try:
            text = fold_text(path.read_text(encoding="utf-8"), fragments)
        except ValueError as error:
            print(f"changelog: {error}", file=sys.stderr)
            return 1
        if args.dry_run:
            sys.stdout.write(text)
            return 0
        path.write_text(text, encoding="utf-8")
        for fragment in fragments:
            fragment.path.unlink()
        print(f"changelog: folded {len(fragments)} fragment(s) into {CHANGELOG}")
        return 0
    print(f"changelog: {len(fragments)} fragment(s) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
