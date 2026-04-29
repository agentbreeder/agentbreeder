"""Tests for the OS keychain backend, workspace loader, factory wiring, and CLI."""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _run(coro):
    return asyncio.run(coro)


# ─── fake keyring fixture ─────────────────────────────────────────────────────


class FakeKeyring:
    """In-memory stand-in for the ``keyring`` module."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.store[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise RuntimeError("PasswordDeleteError")
        del self.store[(service, username)]


@pytest.fixture
def fake_keyring(monkeypatch) -> FakeKeyring:
    fake = FakeKeyring()
    module = ModuleType("keyring")
    module.get_password = fake.get_password  # type: ignore[attr-defined]
    module.set_password = fake.set_password  # type: ignore[attr-defined]
    module.delete_password = fake.delete_password  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", module)
    return fake


# ─── KeychainBackend ─────────────────────────────────────────────────────────


class TestKeychainBackend:
    def test_set_get_roundtrip(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        b = KeychainBackend(workspace="dev")
        _run(b.set("OPENAI_API_KEY", "sk-abc1234"))
        assert _run(b.get("OPENAI_API_KEY")) == "sk-abc1234"

    def test_workspace_isolation(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        b1 = KeychainBackend(workspace="dev")
        b2 = KeychainBackend(workspace="prod")
        _run(b1.set("KEY", "dev-value"))
        _run(b2.set("KEY", "prod-value"))
        assert _run(b1.get("KEY")) == "dev-value"
        assert _run(b2.get("KEY")) == "prod-value"

    def test_delete_then_missing(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        b = KeychainBackend(workspace="dev")
        _run(b.set("KEY", "v"))
        _run(b.delete("KEY"))
        assert _run(b.get("KEY")) is None

    def test_delete_unknown_raises_keyerror(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        b = KeychainBackend(workspace="dev")
        with pytest.raises(KeyError):
            _run(b.delete("NO_SUCH"))

    def test_list_returns_metadata(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        b = KeychainBackend(workspace="dev")
        _run(b.set("A", "v1", tags={"team": "eng"}))
        _run(b.set("B", "v22222"))
        entries = _run(b.list())
        names = sorted(e.name for e in entries)
        assert names == ["A", "B"]
        assert all(e.backend == "keychain" for e in entries)
        assert any(e.tags == {"team": "eng"} for e in entries)

    def test_index_key_is_reserved(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        b = KeychainBackend(workspace="dev")
        with pytest.raises(ValueError):
            _run(b.set("__agentbreeder_index__", "x"))

    def test_get_index_key_returns_none(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        b = KeychainBackend(workspace="dev")
        assert _run(b.get("__agentbreeder_index__")) is None

    def test_set_updates_existing_metadata(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        b = KeychainBackend(workspace="dev")
        _run(b.set("K", "v"))
        first = [e for e in _run(b.list()) if e.name == "K"][0]
        _run(b.set("K", "v2"))
        second = [e for e in _run(b.list()) if e.name == "K"][0]
        assert first.created_at is not None
        assert second.created_at == first.created_at  # preserved
        assert second.updated_at is not None

    def test_corrupt_index_resets(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        b = KeychainBackend(workspace="dev")
        _run(b.set("K", "v"))
        # Corrupt the index — sentinel sits under workspace-scoped service.
        fake_keyring.store[("agentbreeder:dev", "__agentbreeder_index__")] = "{not json"
        # list() should not raise; it logs a warning and returns empty.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            entries = _run(b.list())
        assert entries == []

    def test_workspace_must_be_non_empty(self, fake_keyring):
        from engine.secrets.keychain_backend import KeychainBackend

        with pytest.raises(ValueError):
            KeychainBackend(workspace="")

    def test_missing_keyring_raises_importerror(self, monkeypatch):
        from engine.secrets.keychain_backend import KeychainBackend

        b = KeychainBackend(workspace="dev")

        def _bad_import(*args: Any, **kwargs: Any):
            raise ImportError("no keyring")

        # Force the lazy import to fail by removing the cached module and
        # blocking re-import.
        monkeypatch.delitem(sys.modules, "keyring", raising=False)
        monkeypatch.setattr("builtins.__import__", _bad_import)
        with pytest.raises(ImportError):
            _run(b.get("ANY"))


# ─── Workspace config loader ────────────────────────────────────────────────


class TestWorkspaceLoader:
    def test_default_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(tmp_path / "nope.yaml"))
        monkeypatch.delenv("AGENTBREEDER_INSTALL_MODE", raising=False)
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        from engine.secrets.workspace import load_workspace_secrets_config

        ws = load_workspace_secrets_config()
        assert ws.source == "default"
        assert ws.backend == "keychain"
        assert ws.workspace == "default"

    def test_install_mode_cloud_default_aws(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(tmp_path / "x.yaml"))
        monkeypatch.setenv("AGENTBREEDER_INSTALL_MODE", "cloud")
        from engine.secrets.workspace import detect_default_backend, load_workspace_secrets_config

        assert detect_default_backend() == "aws"
        assert load_workspace_secrets_config().backend == "aws"

    def test_install_mode_team_with_vault(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(tmp_path / "x.yaml"))
        monkeypatch.setenv("AGENTBREEDER_INSTALL_MODE", "team")
        monkeypatch.setenv("VAULT_ADDR", "https://vault.local")
        from engine.secrets.workspace import detect_default_backend

        assert detect_default_backend() == "vault"

    def test_install_mode_team_no_vault_falls_to_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(tmp_path / "x.yaml"))
        monkeypatch.setenv("AGENTBREEDER_INSTALL_MODE", "team")
        monkeypatch.delenv("VAULT_ADDR", raising=False)
        from engine.secrets.workspace import detect_default_backend

        assert detect_default_backend() == "env"

    def test_parses_valid_yaml(self, tmp_path, monkeypatch):
        f = tmp_path / "workspace.yaml"
        f.write_text(
            "workspace: prod\nsecrets:\n  backend: aws\n  options:\n    region: us-east-1\n"
        )
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(f))
        from engine.secrets.workspace import load_workspace_secrets_config

        ws = load_workspace_secrets_config()
        assert ws.source == "config"
        assert ws.backend == "aws"
        assert ws.workspace == "prod"
        assert ws.options == {"region": "us-east-1"}

    def test_workspace_override_arg(self, tmp_path, monkeypatch):
        f = tmp_path / "workspace.yaml"
        f.write_text("workspace: prod\nsecrets:\n  backend: keychain\n")
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(f))
        from engine.secrets.workspace import load_workspace_secrets_config

        ws = load_workspace_secrets_config(workspace="explicit")
        assert ws.workspace == "explicit"

    def test_invalid_yaml_falls_back_to_default(self, tmp_path, monkeypatch):
        f = tmp_path / "workspace.yaml"
        f.write_text("not a mapping just a string")
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(f))
        monkeypatch.delenv("AGENTBREEDER_INSTALL_MODE", raising=False)
        from engine.secrets.workspace import load_workspace_secrets_config

        ws = load_workspace_secrets_config()
        assert ws.source == "default"

    def test_path_override_arg(self, tmp_path):
        f = tmp_path / "explicit.yaml"
        f.write_text("workspace: ovr\nsecrets:\n  backend: env\n")
        from engine.secrets.workspace import load_workspace_secrets_config

        ws = load_workspace_secrets_config(path=f)
        assert ws.backend == "env"
        assert ws.workspace == "ovr"


class TestEnvFallbackWarning:
    def test_emitted_only_once(self):
        from engine.secrets.workspace import (
            env_fallback_warning_once,
            reset_env_fallback_warning,
        )

        reset_env_fallback_warning()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            env_fallback_warning_once()
            env_fallback_warning_once()
        assert sum(1 for x in w if issubclass(x.category, DeprecationWarning)) == 1
        reset_env_fallback_warning()


# ─── factory.get_workspace_backend ───────────────────────────────────────────


class TestFactoryWorkspace:
    def test_keychain_default_when_no_config(self, fake_keyring, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(tmp_path / "missing.yaml"))
        monkeypatch.delenv("AGENTBREEDER_INSTALL_MODE", raising=False)
        from engine.secrets.factory import get_workspace_backend

        backend, ws = get_workspace_backend()
        assert backend.backend_name == "keychain"
        assert ws.workspace == "default"
        assert ws.source == "default"

    def test_workspace_yaml_overrides_default(self, tmp_path, monkeypatch):
        f = tmp_path / "workspace.yaml"
        f.write_text("workspace: dev\nsecrets:\n  backend: env\n")
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(f))
        from engine.secrets.factory import get_workspace_backend

        backend, ws = get_workspace_backend()
        assert backend.backend_name == "env"
        assert ws.workspace == "dev"

    def test_workspace_arg_passed_to_keychain(self, fake_keyring, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(tmp_path / "missing.yaml"))
        monkeypatch.delenv("AGENTBREEDER_INSTALL_MODE", raising=False)
        from engine.secrets.factory import get_workspace_backend

        backend, ws = get_workspace_backend(workspace="prod")
        assert backend.backend_name == "keychain"
        assert ws.workspace == "prod"
        # Round-trip a value to confirm the workspace name plumbed through.
        _run(backend.set("X", "v"))
        assert _run(backend.get("X")) == "v"

    def test_get_backend_with_explicit_keychain(self, fake_keyring):
        from engine.secrets.factory import get_backend

        backend = get_backend("keychain", workspace="alpha")
        assert backend.backend_name == "keychain"

    def test_get_backend_none_uses_workspace(self, fake_keyring, tmp_path, monkeypatch):
        """``get_backend(None)`` must consult the workspace config."""
        f = tmp_path / "workspace.yaml"
        f.write_text("workspace: w1\nsecrets:\n  backend: keychain\n")
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(f))
        from engine.secrets.factory import get_backend

        backend = get_backend()
        assert backend.backend_name == "keychain"

    def test_get_backend_none_emits_env_fallback_warning(self, tmp_path, monkeypatch):
        """When the install-mode default is env, a deprecation warning fires."""
        from engine.secrets.workspace import reset_env_fallback_warning

        reset_env_fallback_warning()
        monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(tmp_path / "missing.yaml"))
        monkeypatch.setenv("AGENTBREEDER_INSTALL_MODE", "team")
        monkeypatch.delenv("VAULT_ADDR", raising=False)

        from engine.secrets.factory import get_backend

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            backend = get_backend()
        assert backend.backend_name == "env"
        assert any(issubclass(x.category, DeprecationWarning) for x in w)
        reset_env_fallback_warning()

    def test_unknown_backend_raises_value_error(self):
        from engine.secrets.factory import get_backend

        with pytest.raises(ValueError):
            get_backend("not-a-backend")


# ─── CLI integration: new sync + workspace-aware behaviour ───────────────────


@pytest.fixture
def runner():
    from typer.testing import CliRunner

    return CliRunner()


@pytest.fixture
def cli_app():
    from cli.commands.secret import secret_app

    return secret_app


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch, fake_keyring):
    """Force the CLI to use an empty workspace.yaml so the keychain backend wins."""
    monkeypatch.setenv("AGENTBREEDER_WORKSPACE_FILE", str(tmp_path / "ws.yaml"))
    monkeypatch.delenv("AGENTBREEDER_INSTALL_MODE", raising=False)
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    return tmp_path


class TestCLIWorkspaceSet:
    def test_set_then_list_via_keychain(self, runner, cli_app, isolated_workspace):
        result = runner.invoke(cli_app, ["set", "MY_KEY", "--value", "abcdefghij", "--json"])
        assert result.exit_code == 0, result.stdout
        out = json.loads(result.stdout)
        assert out["backend"] == "keychain"
        assert out["operation"] == "created"

        result = runner.invoke(cli_app, ["list", "--json"])
        assert result.exit_code == 0
        listed = json.loads(result.stdout)
        names = [e["name"] for e in listed["entries"]]
        assert "MY_KEY" in names

    def test_set_again_marks_updated(self, runner, cli_app, isolated_workspace):
        runner.invoke(cli_app, ["set", "K", "--value", "v1234567890", "--json"])
        result = runner.invoke(cli_app, ["set", "K", "--value", "v0987654321", "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["operation"] == "updated"

    def test_rotate_existing(self, runner, cli_app, isolated_workspace):
        runner.invoke(cli_app, ["set", "ROT", "--value", "old1234567", "--json"])
        result = runner.invoke(cli_app, ["rotate", "ROT", "--value", "new0987654", "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["rotated"] is True

    def test_rotate_missing(self, runner, cli_app, isolated_workspace):
        result = runner.invoke(cli_app, ["rotate", "NO_SUCH", "--value", "x", "--json"])
        assert result.exit_code == 1


class TestCLISync:
    def test_sync_invalid_target(self, runner, cli_app, isolated_workspace):
        result = runner.invoke(cli_app, ["sync", "--target", "nope", "--json", "--dry-run"])
        assert result.exit_code == 2

    def test_sync_dry_run_uses_workspace_secrets(
        self, runner, cli_app, isolated_workspace, monkeypatch
    ):
        # Seed a value into the workspace keychain backend.
        runner.invoke(cli_app, ["set", "OPENAI", "--value", "sk-abc1234", "--json"])

        # Stub the target backend so we never hit the cloud.
        from engine.secrets.base import SecretsBackend

        class FakeAWS(SecretsBackend):
            backend_name = "aws"  # type: ignore[assignment]
            calls: list[tuple[str, str]] = []

            async def get(self, name: str) -> str | None:
                return None

            async def set(self, name, value, *, tags=None):
                FakeAWS.calls.append((name, value))

            async def delete(self, name: str) -> None: ...

            async def list(self):
                return []

        import cli.commands.secret as cmd

        monkeypatch.setattr(cmd, "_make_backend", lambda b, **kw: FakeAWS())
        result = runner.invoke(cli_app, ["sync", "--target", "aws", "--dry-run", "--json"])
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["dry_run"] is True
        assert out["mirrored"] >= 1


# ─── __init__.py re-exports ──────────────────────────────────────────────────


def test_init_reexports():
    pkg = importlib.import_module("engine.secrets")
    for name in (
        "KeychainBackend",
        "WorkspaceSecretsConfig",
        "load_workspace_secrets_config",
        "get_workspace_backend",
        "detect_default_backend",
        "find_secret_refs",
    ):
        assert hasattr(pkg, name), f"missing re-export: {name}"


def test_secret_entry_to_dict_handles_naive_datetime(fake_keyring):
    """Sanity coverage that the SecretEntry dataclass survives missing tz info."""
    from engine.secrets.base import SecretEntry

    entry = SecretEntry(
        name="X",
        masked_value="••••",
        backend="keychain",
        created_at=datetime.now(),  # naive
        updated_at=datetime.now(tz=UTC),
    )
    out = entry.to_dict()
    assert out["name"] == "X"
    assert out["backend"] == "keychain"


# Reference to satisfy lint when Path import is unused on platforms that
# cannot import keyring.
_ = Path
