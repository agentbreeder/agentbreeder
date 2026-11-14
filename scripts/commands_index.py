"""Write or check the command index in docs/commands.md (#893).

    python scripts/commands_index.py --write
        rewrite the lines between the index markers from docs/commands/*.md
    python scripts/commands_index.py --check
        fail if those lines are not current

Each docs/commands/<command>.md starts with "# `agentbreeder <command>`", a blank line and a
one-line summary; the index has one sorted line per file. Regenerate the index rather than merge
it by hand.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = "docs/commands.md"
DIRECTORY = "docs/commands"
START = "<!-- commands-index:start -->"
END = "<!-- commands-index:end -->"
_TITLE = re.compile(r"^# `agentbreeder ([a-z][a-z0-9-]*)`$")


def entries(root: Path) -> tuple[list[str], list[str]]:
    """The index lines, and problems with the command pages."""
    lines, errors = [], []
    for path in sorted((root / DIRECTORY).glob("*.md"), key=lambda p: p.stem):
        head = path.read_text(encoding="utf-8").split("\n", 3)
        title = _TITLE.match(head[0]) if head else None
        name = f"{DIRECTORY}/{path.name}"
        if not title or title.group(1) != path.stem:
            errors.append(f"{name}: first line must be '# `agentbreeder {path.stem}`'")
            continue
        if len(head) < 3 or head[1] != "" or not head[2].strip():
            errors.append(f"{name}: the title must be followed by a blank line and a summary line")
            continue
        lines.append(f"- [`{path.stem}`](commands/{path.name}): {head[2].strip()}")
    return lines, errors


def render(page: str, lines: list[str]) -> str:
    if page.count(START) != 1 or page.count(END) != 1 or page.index(START) > page.index(END):
        raise ValueError(f"{PAGE} must hold one {START} line before one {END} line")
    before, rest = page.split(START, 1)
    _, after = rest.split(END, 1)
    return before + START + "\n" + "".join(line + "\n" for line in lines) + END + after


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    lines, errors = entries(args.root)
    path = args.root / PAGE
    page = path.read_text(encoding="utf-8")
    try:
        new = render(page, lines)
    except ValueError as error:
        errors.append(str(error))
    if errors:
        for error in errors:
            print(f"commands_index: {error}", file=sys.stderr)
        return 1
    if args.write:
        if new != page:
            path.write_text(new, encoding="utf-8")
        print(f"commands_index: {len(lines)} command(s) in {PAGE}")
        return 0
    if new != page:
        print(
            f"commands_index: {PAGE} is out of date; run python scripts/commands_index.py --write",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
