"""docs/commands/: one page per CLI command, and a generated index in docs/commands.md (#893)."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

from agentbreeder import cli

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "commands_index", ROOT / "scripts" / "commands_index.py"
)
commands_index = importlib.util.module_from_spec(_spec)
sys.modules["commands_index"] = commands_index
_spec.loader.exec_module(commands_index)

PAGES = ROOT / "docs" / "commands"
_LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


def _commands() -> set[str]:
    for action in cli._parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("no subcommands")


def test_index_is_current():
    assert commands_index.main(["--check"]) == 0


def test_one_page_per_command():
    assert {p.stem for p in PAGES.glob("*.md")} == _commands()


def test_summary_is_the_help_line():
    parser = cli._parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    helps = {c.dest: c.help for c in sub._choices_actions}
    for page in PAGES.glob("*.md"):
        summary = page.read_text(encoding="utf-8").split("\n")[2]
        assert summary == helps[page.stem].rstrip(".") + ".", page.name


def test_relative_links_resolve():
    for page in [ROOT / "docs" / "commands.md", *PAGES.glob("*.md")]:
        for target in _LINK.findall(page.read_text(encoding="utf-8")):
            if "://" in target:
                continue
            assert (page.parent / target).exists(), f"{page.name}: {target}"


def test_render_requires_one_pair_of_markers():
    for page in ["no markers", f"{commands_index.END}\n{commands_index.START}\n"]:
        try:
            commands_index.render(page, [])
        except ValueError:
            continue
        raise AssertionError(page)


def test_write_reports_a_bad_page(tmp_path, capsys):
    (tmp_path / "docs" / "commands").mkdir(parents=True)
    (tmp_path / "docs" / "commands.md").write_text(
        f"x\n{commands_index.START}\n{commands_index.END}\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "commands" / "logs.md").write_text(
        "# logs\n\nSummary.\n", encoding="utf-8"
    )
    assert commands_index.main(["--write", "--root", str(tmp_path)]) == 1
    assert "first line must be" in capsys.readouterr().err
    (tmp_path / "docs" / "commands" / "logs.md").write_text(
        "# `agentbreeder logs`\n\nShow logs.\n\nBody.\n", encoding="utf-8"
    )
    assert commands_index.main(["--write", "--root", str(tmp_path)]) == 0
    assert "- [`logs`](commands/logs.md): Show logs.\n" in (
        tmp_path / "docs" / "commands.md"
    ).read_text(encoding="utf-8")
    assert commands_index.main(["--check", "--root", str(tmp_path)]) == 0


def test_check_reports_missing_summary_markers_and_a_stale_index(tmp_path, capsys):
    pages = tmp_path / "docs" / "commands"
    pages.mkdir(parents=True)
    page = tmp_path / "docs" / "commands.md"
    page.write_text("no markers\n", encoding="utf-8")
    (pages / "logs.md").write_text("# `agentbreeder logs`\nShow logs.\n", encoding="utf-8")
    assert commands_index.main(["--check", "--root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "followed by a blank line and a summary" in err and "must hold one" in err

    (pages / "logs.md").write_text("# `agentbreeder logs`\n\nShow logs.\n", encoding="utf-8")
    page.write_text(f"{commands_index.START}\n{commands_index.END}\n", encoding="utf-8")
    assert commands_index.main(["--check", "--root", str(tmp_path)]) == 1
    assert "out of date" in capsys.readouterr().err
