"""Golden help output for every command (#813): the per-command module split must not change it.

Regenerate after an intended CLI change with AGENTBREEDER_UPDATE_GOLDEN=1 on Python 3.12, then on
3.13 (which wraps usage lines differently; its differing files go to py313/), and review the diff.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentbreeder import cli

GOLDEN = Path(__file__).parent / "fixtures" / "cli_help"
VERSIONED = GOLDEN / f"py{sys.version_info.major}{sys.version_info.minor}"
UPDATE = os.environ.get("AGENTBREEDER_UPDATE_GOLDEN") == "1"


def _paths(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()):
    yield prefix
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                yield from _paths(child, (*prefix, name))


PATHS = list(_paths(cli._parser()))


def _name(path: tuple[str, ...]) -> str:
    return ("_".join(path) or "agentbreeder") + ".txt"


def _fixture(path: tuple[str, ...]) -> Path:
    """The help text for this Python version: an override in py3XX/ if one exists."""
    override = VERSIONED / _name(path)
    return override if override.exists() else GOLDEN / _name(path)


def _help(path: tuple[str, ...], capsys) -> str:
    with pytest.raises(SystemExit) as raised:
        cli.main([*path, "--help"])
    assert raised.value.code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    return captured.out


@pytest.fixture(autouse=True)
def _fixed_width(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.setenv("NO_COLOR", "1")


@pytest.mark.parametrize("path", PATHS, ids=lambda path: " ".join(path) or "(top)")
def test_help_matches_golden(path, capsys):
    text = _help(path, capsys)
    if UPDATE:
        base = GOLDEN / _name(path)
        if sys.version_info[:2] == (3, 12):
            base.parent.mkdir(parents=True, exist_ok=True)
            base.write_text(text, encoding="utf-8")
        elif text != base.read_text(encoding="utf-8"):
            VERSIONED.mkdir(parents=True, exist_ok=True)
            (VERSIONED / _name(path)).write_text(text, encoding="utf-8")
    assert text == _fixture(path).read_text(encoding="utf-8")


def test_every_golden_file_is_a_command():
    names = {_name(path) for path in PATHS}
    assert {p.name for p in GOLDEN.glob("*.txt")} == names
    assert {p.name for p in GOLDEN.glob("py*/*.txt")} <= names
    assert len(PATHS) > 40  # every command and nested command, not just the top level


def test_command_modules_declare_exactly_the_commands_they_register():
    """`main` dispatches on each module's NAMES; a parser missing from NAMES would crash."""
    declared = [name for module in cli._modules() for name in module.NAMES]
    registered = [path[0] for path in PATHS if len(path) == 1]
    assert declared == registered


def test_help_end_to_end_matches_golden():
    environment = {**os.environ, "COLUMNS": "80", "NO_COLOR": "1"}
    for path in [(), ("services", "up"), ("registry", "publish"), ("deploy",)]:
        result = subprocess.run(
            [sys.executable, "-m", "agentbreeder", *path, "--help"],
            capture_output=True,
            text=True,
            env=environment,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == _fixture(path).read_text(encoding="utf-8")
