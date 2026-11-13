"""`docs/your-first-agent.md` and the guided path (`guide.py`, `guide_checks.py`) cannot drift.

The page has one numbered section per step id, in `guide.STEP_ORDER` with `guide.TITLES`, then
"Clean up". Every fenced block is `sh`. Each command a step's checks can suggest is shown in a
block of that step's section: same subcommand words (the leading literal words of the
`hints.command` call) and every flag the call passes (literal `--flag` words and the
`stack`/`environment`/`state_dir` keywords). The blocks show no other `agentbreeder` command,
and every `agentbreeder` command on the page, in a block or inline, parses with the CLI parser.
"""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path

import pytest

from agentbreeder import cli, guide, guide_checks

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "your-first-agent.md"
CLEAN_UP = "Clean up"

# The function in guide_checks.py whose suggestions belong to each step. A new function that
# calls hints.command must be added here, so its commands are checked against the page.
STEP_OF_FUNCTION = {
    "_prerequisites": "prerequisites",
    "_project": "project",
    "_invalid": "project",
    "_keys": "keys",
    "_services": "services",
    "_build": "build",
    "_release": "release",
    "_try": "try",
    "_kubernetes": "kubernetes",
}
KEYWORD_FLAGS = {"stack": "--stack", "environment": "--environment", "state_dir": "--state-dir"}
# Commands the page's clean-up section shows; the guide has no clean-up step.
CLEAN_UP_COMMANDS = {
    (("list",), frozenset({"--stack", "--environment"})),
    (("remove",), frozenset({"--local", "--stack", "--environment"})),
    (("remove",), frozenset({"--cluster", "--stack", "--environment"})),
    (("services", "down"), frozenset({"--purge", "--yes"})),
    (("cluster", "down"), frozenset({"--stack"})),
}

Suggestion = tuple[tuple[str, ...], frozenset[str]]


def _is_suggestion(node: ast.AST) -> bool:
    """Any call of a function named `command` or `next_line`, however it is reached."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    return name in {"command", "next_line"}


def _suggestions(source: str) -> dict[str, set[Suggestion]]:
    """Each step's suggested commands: (subcommand words, flags)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "agentbreeder.hints":
            raise AssertionError("import hints, not names from it, so the walk sees the calls")
    found: dict[str, set[Suggestion]] = {step: set() for step in guide.STEP_ORDER}
    for top in tree.body:
        for node in ast.walk(top):
            if not _is_suggestion(node):
                continue
            func = node.func
            assert (
                isinstance(top, ast.FunctionDef)
                and top.name in STEP_OF_FUNCTION
                and isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "hints"
            ), f"line {node.lineno}: map this suggested command to a step in STEP_OF_FUNCTION"
            words: list[str] = []
            for argument in node.args:
                if not (isinstance(argument, ast.Constant) and isinstance(argument.value, str)):
                    break
                words.append(argument.value)
            assert words, f"line {node.lineno}: a suggested command has no literal subcommand"
            flags = {
                a.value
                for a in node.args
                if isinstance(a, ast.Constant)
                and isinstance(a.value, str)
                and a.value.startswith("--")
            }
            assert all(k.arg is not None for k in node.keywords), (
                f"line {node.lineno}: pass stack=/environment= by name, not **kwargs, so the "
                "page is checked for them"
            )
            flags |= {KEYWORD_FLAGS[k.arg] for k in node.keywords if k.arg in KEYWORD_FLAGS}
            found[STEP_OF_FUNCTION[top.name]].add((tuple(words), frozenset(flags)))
    return found


def _guide() -> dict[str, set[Suggestion]]:
    return _suggestions(Path(guide_checks.__file__).read_text(encoding="utf-8"))


def _headings(text: str) -> list[str]:
    return [h.strip() for h in re.findall(r"^## (.+)$", text, flags=re.MULTILINE)]


def _sections(text: str) -> dict[str, str]:
    """`## heading` -> body."""
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts), 2)}


def _fences(text: str) -> list[tuple[str, str]]:
    """(info string, body) of each fenced block."""
    return re.findall(r"^```(\S*)\n(.*?)^```$", text, flags=re.MULTILINE | re.DOTALL)


def _commands(body: str) -> list[list[str]]:
    """Each `agentbreeder …` line in the section's blocks, as argv after `agentbreeder`."""
    commands = []
    for _, block in _fences(body):
        for line in block.splitlines():
            words = shlex.split(line, comments=True)
            if words and words[0] == "agentbreeder":
                commands.append(words[1:])
    return commands


def _inline(text: str) -> list[list[str]]:
    """Each inline code span that is an `agentbreeder …` command, as argv."""
    outside = re.sub(r"^```.*?^```$", "", text, flags=re.MULTILINE | re.DOTALL)
    spans = re.findall(r"`([^`\n]+)`", outside)
    return [shlex.split(s)[1:] for s in spans if s.startswith("agentbreeder ") and "…" not in s]


def _shows(argv: list[str], suggestion: Suggestion) -> bool:
    words, flags = suggestion
    if len(argv) < len(words):
        return False
    same = all(
        word == shown or (word.startswith("<") and word.endswith(">"))
        for word, shown in zip(words, argv, strict=False)
    )
    return same and flags <= set(argv)


@pytest.fixture(scope="module")
def text() -> str:
    return DOC.read_text(encoding="utf-8")


def _step_heading(index: int, step: str) -> str:
    return f"{index}. {guide.TITLES[step]}"


def _allowed() -> dict[str, set[Suggestion]]:
    guide_suggestions = _guide()
    allowed = {
        _step_heading(i, s): guide_suggestions[s] for i, s in enumerate(guide.STEP_ORDER, 1)
    }
    allowed[CLEAN_UP] = set(CLEAN_UP_COMMANDS)
    return allowed


def test_the_page_has_every_step_in_order_then_clean_up(text):
    expected = [_step_heading(i, s) for i, s in enumerate(guide.STEP_ORDER, 1)] + [CLEAN_UP]
    assert _headings(text) == expected


def test_every_block_is_sh(text):
    assert {info for info, _ in _fences(text)} == {"sh"}


def test_every_suggested_command_is_shown_in_its_steps_section(text):
    sections = _sections(text)
    for heading, suggestions in _allowed().items():
        assert suggestions, f"{heading}: nothing to show"
        shown = _commands(sections[heading])
        for words, flags in sorted(suggestions, key=str):
            assert any(_shows(argv, (words, flags)) for argv in shown), (
                f"{heading}: the page does not show `agentbreeder {' '.join(words)}` "
                f"with {sorted(flags)}"
            )


def test_the_blocks_show_no_command_the_guide_does_not_suggest(text):
    allowed = _allowed()
    for heading, body in _sections(text).items():
        for argv in _commands(body):
            assert any(_shows(argv, s) for s in allowed[heading]), (
                f"{heading}: `agentbreeder {' '.join(argv)}` is not a command this step suggests"
            )


def test_every_command_on_the_page_parses(text):
    parser = cli._parser()
    commands = _commands(text) + _inline(text)
    assert len(commands) >= len(guide.STEP_ORDER)
    for argv in commands:
        try:
            parser.parse_args(argv)
        except SystemExit:  # pragma: no cover - the message names the command
            pytest.fail(f"`agentbreeder {' '.join(argv)}` does not parse")


def test_the_drift_checks_catch_drift():
    """Each check fails on the drift it exists for."""
    page = (
        "## 1. Prerequisites\n```sh\nagentbreeder doctor\n```\n"
        "## 1. Prerequisites\n```bash\nagentbreeder deploy x --local\ncd somewhere\n```\n"
        "Run `agentbreeder forward` or `agentbreeder status ID`.\n"
    )
    assert _headings(page) == ["1. Prerequisites", "1. Prerequisites"]  # duplicates are seen
    assert {info for info, _ in _fences(page)} == {"sh", "bash"}
    assert _commands(page) == [["doctor"], ["deploy", "x", "--local"]]
    assert _inline(page) == [["forward"], ["status", "ID"]]
    # A shown command must carry the suggestion's flags, and placeholders match any word.
    assert _shows(["cluster", "up", "--yes", "--stack", "."], (("cluster", "up"), {"--yes"}))
    assert not _shows(["cluster", "up", "--stack", "."], (("cluster", "up"), {"--yes"}))
    assert _shows(["registry", "publish", "src"], (("registry", "publish", "<directory>"), set()))
    assert not _shows(["registry"], (("registry", "publish"), frozenset()))
    assert not _shows(["services", "down"], (("services", "up"), frozenset()))
    # The walk finds every call, refuses one outside a mapped function or not through `hints`,
    # and reads keyword flags.
    source = (
        "def _keys(ctx):\n"
        "    return hints.command('services', 'up', '--yes', x, '--env-file', stack=p)\n"
    )
    assert _suggestions(source)["keys"] == {
        (("services", "up", "--yes"), frozenset({"--yes", "--env-file", "--stack"}))
    }
    for bad in (
        "def _new_step(ctx):\n    return hints.command('x')\n",
        "def _keys(ctx):\n    return ctx.where().command('x')\n",
        "X = hints.command('x')\n",
        "from agentbreeder.hints import command\n",
        "def _keys(ctx):\n    return hints.command('x', **where)\n",
    ):
        with pytest.raises(AssertionError):
            _suggestions(bad)
    with pytest.raises(AssertionError):
        _suggestions("def _keys(ctx):\n    return hints.command(name)\n")
