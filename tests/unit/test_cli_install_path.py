"""Tests for the install-path / PATH discovery helpers (issue #463)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from cli.main import (
    _path_entries,
    _scripts_dir,
    _scripts_dir_on_path,
    _shell_rc_hint,
    app,
)

runner = CliRunner()


class TestScriptsDir:
    def test_scripts_dir_returns_existing_path(self) -> None:
        path = _scripts_dir()
        # sysconfig.get_path('scripts') always returns a non-empty path.
        assert isinstance(path, Path)
        assert str(path)


class TestPathDetection:
    def test_returns_true_when_scripts_dir_on_path(self) -> None:
        scripts = _scripts_dir().resolve()
        with patch.dict(os.environ, {"PATH": f"/usr/bin{os.pathsep}{scripts}"}):
            assert _scripts_dir_on_path() is True

    def test_returns_false_when_path_empty(self) -> None:
        with patch.dict(os.environ, {"PATH": ""}):
            assert _scripts_dir_on_path() is False

    def test_returns_false_when_only_unrelated_entries(self) -> None:
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            assert _scripts_dir_on_path() is False

    def test_path_entries_skips_blanks(self) -> None:
        with patch.dict(os.environ, {"PATH": f"/usr/bin{os.pathsep}{os.pathsep}/bin"}):
            entries = _path_entries()
        assert Path("/usr/bin").resolve() in entries
        assert Path("/bin").resolve() in entries


class TestShellRcHint:
    def test_zsh_emits_zshrc_export(self) -> None:
        with patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
            label, line = _shell_rc_hint()
        assert "zsh" in label
        assert line.startswith('export PATH="')
        assert ':$PATH"' in line

    def test_fish_emits_fish_add_path(self) -> None:
        with patch.dict(os.environ, {"SHELL": "/usr/local/bin/fish"}):
            label, line = _shell_rc_hint()
        assert "fish" in label
        assert line.startswith("fish_add_path ")

    def test_bash_default_for_unknown_posix_shell(self) -> None:
        with patch.dict(os.environ, {"SHELL": "/bin/ksh"}, clear=False):
            with patch("cli.main._is_windows", return_value=False):
                label, line = _shell_rc_hint()
        assert "bash" in label
        assert line.startswith('export PATH="')

    def test_windows_emits_powershell(self) -> None:
        # Patch the helper (not ``os.name`` itself) — globally mutating
        # ``os.name`` would make ``pathlib.Path()`` try to instantiate
        # ``WindowsPath`` on a non-Windows test host and raise.
        with patch("cli.main._is_windows", return_value=True):
            label, line = _shell_rc_hint()
        assert "PowerShell" in label
        assert line.startswith("$env:PATH = ")


class TestPrintInstallPathHint:
    def test_silent_when_scripts_dir_on_path(self) -> None:
        """Happy path (pipx / venv users) must stay quiet."""
        with patch("cli.main._scripts_dir_on_path", return_value=True):
            from cli.main import _print_install_path_hint

            # Should not raise and not print anything we can capture via stdout
            # — the function returns early without instantiating Console.
            _print_install_path_hint()

    def test_welcome_shows_hint_when_scripts_dir_off_path(self) -> None:
        """`agentbreeder welcome` surfaces the hint when PATH is misconfigured."""
        with patch("cli.main._scripts_dir_on_path", return_value=False):
            with patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
                result = runner.invoke(app, ["welcome"])
        assert result.exit_code == 0
        assert "Install path" in result.output
        assert "pipx install agentbreeder" in result.output
        assert "python3 -m pip install agentbreeder" in result.output

    def test_welcome_hides_hint_on_happy_path(self) -> None:
        with patch("cli.main._scripts_dir_on_path", return_value=True):
            result = runner.invoke(app, ["welcome"])
        assert result.exit_code == 0
        assert "Install path" not in result.output
