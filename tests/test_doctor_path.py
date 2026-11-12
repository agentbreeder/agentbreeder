"""`doctor`'s advisory PATH check (#805, #819): the `agentbreeder` on PATH is this version."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentbreeder import __version__, cli, doctor, launcher
from agentbreeder.commands import doctor as doctor_command


def script(path: Path, interpreter: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{interpreter}\n")
    path.chmod(0o755)
    return str(path)


def reporting(version: str):
    def run(argv, **kwargs):
        assert argv[1:] == ["--version"] and kwargs["capture_output"]
        return subprocess.CompletedProcess(argv, 0, version + "\n", "")

    return run


@pytest.fixture
def bare(monkeypatch):
    monkeypatch.setattr(launcher, "prefix", lambda *a, **k: "python -m agentbreeder")


def test_this_install_on_path_passes(tmp_path, bare):
    found = script(tmp_path / "bin" / "agentbreeder", sys.executable)
    [check] = doctor.check_path(which=lambda n, path=None: found, run=reporting("0.0.0"))
    assert check.passed and not check.required and "this install" in check.message


def test_same_version_elsewhere_passes(tmp_path, bare):
    found = script(tmp_path / "other" / "agentbreeder", "/usr/bin/python3")
    [check] = doctor.check_path(which=lambda n, path=None: found, run=reporting(__version__))
    assert check.passed


def test_stale_version_warns_but_never_fails(tmp_path, bare):
    found = script(tmp_path / "old" / "agentbreeder", "/usr/bin/python3")
    [check] = doctor.check_path(which=lambda n, path=None: found, run=reporting("2.7.3"))
    assert not check.passed and not check.required
    assert "version 2.7.3" in check.message and "python -m agentbreeder" in check.message


def test_garbage_version_output_is_not_echoed(tmp_path, bare):
    found = script(tmp_path / "x" / "agentbreeder", "/usr/bin/python3")
    [check] = doctor.check_path(
        which=lambda n, path=None: found, run=reporting("\x1b]52;c;eA==\x07")
    )
    assert "version unknown" in check.message and "\x1b" not in check.message


def test_npx_and_uvx_need_none_on_path(monkeypatch):
    monkeypatch.setattr(launcher, "prefix", lambda *a, **k: "npx agentbreeder@3.0.0-alpha.3")
    [check] = doctor.check_path(which=lambda n, path=None: None)
    assert check.passed and check.info and "npx agentbreeder@3.0.0-alpha.3" in check.message


def test_none_on_path_otherwise_warns(bare):
    [check] = doctor.check_path(which=lambda n, path=None: None)
    assert not check.passed and not check.required


def test_doctor_cli_shows_the_check_and_keeps_its_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(
        doctor_command, "check_docker", lambda: [doctor.Check("Docker", True, "ok")]
    )
    monkeypatch.setattr(doctor_command, "check_compose", lambda: [])
    warn = doctor.Check("agentbreeder on PATH", False, "stale", False)
    monkeypatch.setattr(doctor_command, "check_path", lambda: [warn])
    assert cli.main(["doctor"]) == 0
    assert "WARN agentbreeder on PATH: stale" in capsys.readouterr().out
    assert cli.main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[1]["name"] == "agentbreeder on PATH"


def test_doctor_end_to_end_with_npx_env(tmp_path):
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = {
        **os.environ,
        "PYTHONPATH": src,
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "AGENTBREEDER_LAUNCHER": "npx",
        "AGENTBREEDER_LAUNCHER_VERSION": "3.0.0-alpha.3",
    }
    result = subprocess.run(
        [sys.executable, "-m", "agentbreeder", "doctor", "--json"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    checks = {c["name"]: c for c in json.loads(result.stdout)}
    path_check = checks["agentbreeder on PATH"]
    assert path_check["passed"] and "npx agentbreeder@3.0.0-alpha.3" in path_check["message"]


def test_uvx_ephemeral_bin_does_not_hide_a_stale_install(tmp_path, monkeypatch):
    """uvx puts its temporary environment's bin first on PATH; the user's shell would find the
    stale one behind it."""
    cache = tmp_path / "uv-cache"
    ephemeral = cache / "archive-v0" / "abc" / "bin"
    script(ephemeral / "agentbreeder", sys.executable)
    stale = script(tmp_path / "old" / "agentbreeder", "/usr/bin/python3")
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setenv("PATH", f"{ephemeral}{os.pathsep}{tmp_path / 'old'}")
    monkeypatch.setattr(launcher, "prefix", lambda *a, **k: "npx agentbreeder@3.0.0-alpha.3")
    [check] = doctor.check_path(run=reporting("2.7.3"))
    assert not check.passed and stale in check.message and "2.7.3" in check.message
    assert doctor.user_path() == str(tmp_path / "old")


def test_under_npx_a_script_running_this_interpreter_is_skipped(tmp_path, monkeypatch):
    """Whatever cache uv used: under npx/uvx, the first `agentbreeder` that runs this very
    interpreter is the temporary environment, not an install the user's shell has."""
    ephemeral = tmp_path / "somewhere" / "bin"
    script(ephemeral / "agentbreeder", sys.executable)
    stale = script(tmp_path / "old" / "agentbreeder", "/usr/bin/python3")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "unused-cache"))
    monkeypatch.setenv("PATH", f"{ephemeral}{os.pathsep}{tmp_path / 'old'}")
    monkeypatch.setattr(launcher, "prefix", lambda *a, **k: "npx agentbreeder@3.0.0-alpha.3")
    [check] = doctor.check_path(run=reporting("2.7.3"))
    assert not check.passed and stale in check.message
    # Without npx/uvx the same script is a real install (a venv on PATH) and passes.
    monkeypatch.setattr(launcher, "prefix", lambda *a, **k: "agentbreeder")
    [check] = doctor.check_path(run=reporting("2.7.3"))
    assert check.passed and "this install" in check.message
