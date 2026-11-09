"""The built-in litellm service's launcher (files/start.py): secret files into LiteLLM's env."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

import agentbreeder

LAUNCHER = (
    Path(agentbreeder.__file__).parent / "builtin_registry/service/litellm/1.0.0/files/start.py"
)


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("litellm_start", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SECRETS", str(tmp_path))
    calls = []
    monkeypatch.setattr(module.os, "execvpe", lambda *args: calls.append(args))
    monkeypatch.setenv("OPENAI_API_KEY", "stale-from-image")
    return module, tmp_path, calls


def test_executable_and_exec_form():
    assert os.access(LAUNCHER, os.X_OK)
    assert LAUNCHER.read_text().startswith("#!/app/.venv/bin/python3\n")


def test_secrets_become_litellm_env(launcher, capsys):
    module, secrets, calls = launcher
    (secrets / "master-key").write_text("sk-abc")
    (secrets / "database-url").write_text("postgresql://u:p@h/db\n")
    (secrets / "gemini-api-key").write_text("g-key")
    (secrets / "unrelated").write_text("ignored")
    module.main()
    program, argv, environment = calls[0]
    assert program == "litellm"
    assert argv == ["litellm", "--config", "/run/params/config", "--port", "4000"]
    assert environment["LITELLM_MASTER_KEY"] == "sk-abc"
    assert environment["DATABASE_URL"] == "postgresql://u:p@h/db"
    assert environment["GEMINI_API_KEY"] == "g-key"
    assert "OPENAI_API_KEY" not in environment  # not imported: never inherited
    assert "ignored" not in environment.values()
    assert "sk-abc" not in capsys.readouterr().err


def test_no_provider_key_warns(launcher, capsys):
    module, secrets, calls = launcher
    (secrets / "master-key").write_text("sk-abc")
    (secrets / "database-url").write_text("postgresql://x")
    module.main()
    assert calls and "no provider key" in capsys.readouterr().err


@pytest.mark.parametrize(
    "files, message",
    [
        ({"database-url": "x"}, "master-key is missing"),
        ({"master-key": "", "database-url": "x"}, "master-key is empty or malformed"),
        ({"master-key": "a\nb", "database-url": "x"}, "master-key is empty or malformed"),
        ({"master-key": "a\0b", "database-url": "x"}, "master-key is empty or malformed"),
        ({"master-key": "x" * 20000, "database-url": "x"}, "master-key is empty or malformed"),
    ],
)
def test_malformed_secrets_stop_without_echoing(launcher, files, message):
    module, secrets, calls = launcher
    for name, value in files.items():
        (secrets / name).write_text(value)
    with pytest.raises(SystemExit) as stop:
        module.main()
    assert message in str(stop.value) and not calls
    assert "a\nb" not in str(stop.value)


def test_symlinked_secret_is_refused(launcher):
    module, secrets, calls = launcher
    (secrets / "real").write_text("sk-abc")
    (secrets / "master-key").symlink_to(secrets / "real")
    (secrets / "database-url").write_text("x")
    with pytest.raises(SystemExit, match="master-key can't be read"):
        module.main()
    assert not calls
