"""Tests for the secrets management engine and CLI."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC

import pytest

from engine.secrets.base import SecretEntry, _mask
from engine.secrets.env_backend import EnvBackend, _parse_env_file, _write_env_file
from engine.secrets.factory import find_secret_refs, get_backend, resolve_secret_refs

# ── helpers ──────────────────────────────────────────────────────────────────


def run(coro):
    return asyncio.run(coro)


# ── _mask ────────────────────────────────────────────────────────────────────


class TestMask:
    def test_short_value_fully_masked(self):
        assert _mask("abc") == "••••"

    def test_long_value_shows_last_four(self):
        assert _mask("sk-abcd1234xyz9999") == "••••9999"

    def test_exactly_eight_chars_masked(self):
        assert _mask("12345678") == "••••"

    def test_nine_chars_shows_last_four(self):
        assert _mask("123456789") == "••••6789"


# ── SecretEntry ───────────────────────────────────────────────────────────────


class TestSecretEntry:
    def test_to_dict_has_required_fields(self):
        e = SecretEntry(name="MY_KEY", masked_value="••••abcd", backend="env")
        d = e.to_dict()
        assert d["name"] == "MY_KEY"
        assert d["masked_value"] == "••••abcd"
        assert d["backend"] == "env"
        assert d["created_at"] is None

    def test_to_dict_with_datetime(self):
        from datetime import datetime

        ts = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
        e = SecretEntry(name="KEY", masked_value="••••", backend="aws", created_at=ts)
        d = e.to_dict()
        assert "2026-03-13" in d["created_at"]


# ── _parse_env_file / _write_env_file ────────────────────────────────────────


class TestEnvFileParsing:
    def test_parse_basic(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("OPENAI_API_KEY=sk-abc\nANTHROPIC_API_KEY=ant-xyz\n")
        result = _parse_env_file(env)
        assert result["OPENAI_API_KEY"] == "sk-abc"
        assert result["ANTHROPIC_API_KEY"] == "ant-xyz"

    def test_parse_quoted_values(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("KEY_A=\"hello world\"\nKEY_B='single quotes'\n")
        result = _parse_env_file(env)
        assert result["KEY_A"] == "hello world"
        assert result["KEY_B"] == "single quotes"

    def test_parse_ignores_comments(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# comment\nKEY=val\n# another comment\n")
        result = _parse_env_file(env)
        assert "KEY" in result
        assert len(result) == 1

    def test_parse_empty_file(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("")
        assert _parse_env_file(env) == {}

    def test_parse_missing_file(self, tmp_path):
        assert _parse_env_file(tmp_path / "nonexistent.env") == {}

    def test_write_creates_file(self, tmp_path):
        env = tmp_path / ".env"
        _write_env_file(env, {"KEY": "value"})
        assert env.exists()
        assert "KEY=value" in env.read_text()

    def test_write_updates_existing_key(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("KEY=old\nOTHER=keep\n")
        _write_env_file(env, {"KEY": "new", "OTHER": "keep"})
        text = env.read_text()
        assert "KEY=new" in text
        assert "KEY=old" not in text
        assert "OTHER=keep" in text

    def test_write_deletes_removed_key(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("KEEP=yes\nDELETE=me\n")
        _write_env_file(env, {"KEEP": "yes"})
        assert "DELETE" not in env.read_text()


# ── EnvBackend ────────────────────────────────────────────────────────────────


class TestEnvBackend:
    def test_set_and_get(self, tmp_path):
        backend = EnvBackend(env_file=tmp_path / ".env")
        run(backend.set("MY_KEY", "my-value"))
        assert run(backend.get("MY_KEY")) == "my-value"

    def test_get_missing_returns_none(self, tmp_path):
        backend = EnvBackend(env_file=tmp_path / ".env")
        assert run(backend.get("DOES_NOT_EXIST")) is None

    def test_delete_removes_key(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("REMOVE_ME=yes\nKEEP=this\n")
        backend = EnvBackend(env_file=env)
        run(backend.delete("REMOVE_ME"))
        assert run(backend.get("REMOVE_ME")) is None
        assert run(backend.get("KEEP")) == "this"

    def test_delete_missing_key_raises(self, tmp_path):
        backend = EnvBackend(env_file=tmp_path / ".env")
        with pytest.raises(KeyError):
            run(backend.delete("NOT_THERE"))

    def test_list_returns_entries(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("OPENAI_API_KEY=sk-abc123xyz9\nANTHROPIC_KEY=ant-foo\n")
        backend = EnvBackend(env_file=env)
        entries = run(backend.list())
        names = [e.name for e in entries]
        assert "OPENAI_API_KEY" in names
        assert "ANTHROPIC_KEY" in names
        # Values should be masked
        for e in entries:
            assert "••••" in e.masked_value

    def test_list_skips_empty_values(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("HAS_VALUE=abc\nEMPTY_KEY=\n")
        backend = EnvBackend(env_file=env)
        entries = run(backend.list())
        names = [e.name for e in entries]
        assert "HAS_VALUE" in names
        assert "EMPTY_KEY" not in names

    def test_backend_name(self, tmp_path):
        backend = EnvBackend(env_file=tmp_path / ".env")
        assert backend.backend_name == "env"

    def test_rotate_updates_value(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("KEY=old-value\n")
        backend = EnvBackend(env_file=env)
        run(backend.rotate("KEY", "new-value"))
        assert run(backend.get("KEY")) == "new-value"

    def test_rotate_missing_key_raises(self, tmp_path):
        backend = EnvBackend(env_file=tmp_path / ".env")
        with pytest.raises(KeyError, match="not found"):
            run(backend.rotate("MISSING", "new"))

    def test_list_raw(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("API_KEY=secret-value\nOTHER=thing\n")
        backend = EnvBackend(env_file=env)
        raw = backend.list_raw()
        assert raw["API_KEY"] == "secret-value"
        assert raw["OTHER"] == "thing"


# ── factory.get_backend ───────────────────────────────────────────────────────


class TestGetBackend:
    def test_env_backend(self, tmp_path):
        b = get_backend("env", env_file=tmp_path / ".env")
        assert b.backend_name == "env"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown secrets backend"):
            get_backend("nonexistent")

    def test_aws_import_error_propagates(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("No module named 'boto3'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        b = get_backend("aws", region="us-east-1")
        # Import error is raised lazily (on first use), not at construction
        with pytest.raises(ImportError, match="boto3"):
            run(b.get("SOME_KEY"))

    def test_gcp_import_error_propagates(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "google" in name:
                raise ImportError("No module named 'google.cloud'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        b = get_backend("gcp", project_id="my-project")
        with pytest.raises(ImportError, match="google-cloud-secret-manager"):
            run(b.get("SOME_KEY"))


# ── factory.resolve_secret_refs ──────────────────────────────────────────────


class TestResolveSecretRefs:
    def _backend_with(self, tmp_path, data: dict) -> EnvBackend:
        env = tmp_path / ".env"
        env.write_text("\n".join(f"{k}={v}" for k, v in data.items()) + "\n")
        return EnvBackend(env_file=env)

    def test_resolves_top_level_ref(self, tmp_path):
        b = self._backend_with(tmp_path, {"MY_KEY": "resolved-value"})
        result = run(resolve_secret_refs("secret://MY_KEY", b))
        assert result == "resolved-value"

    def test_resolves_nested_ref(self, tmp_path):
        b = self._backend_with(tmp_path, {"API_KEY": "sk-abc"})
        config = {"model": {"api_key": "secret://API_KEY"}}
        result = run(resolve_secret_refs(config, b))
        assert result["model"]["api_key"] == "sk-abc"

    def test_resolves_list_ref(self, tmp_path):
        b = self._backend_with(tmp_path, {"TOKEN": "tok-xyz"})
        config = ["secret://TOKEN", "plain-string"]
        result = run(resolve_secret_refs(config, b))
        assert result[0] == "tok-xyz"
        assert result[1] == "plain-string"

    def test_plain_string_passthrough(self, tmp_path):
        b = self._backend_with(tmp_path, {})
        result = run(resolve_secret_refs("not-a-secret", b))
        assert result == "not-a-secret"

    def test_missing_secret_raises(self, tmp_path):
        b = self._backend_with(tmp_path, {})
        with pytest.raises(ValueError, match="not found in 'env' backend"):
            run(resolve_secret_refs("secret://MISSING_KEY", b))

    def test_non_string_passthrough(self, tmp_path):
        b = self._backend_with(tmp_path, {})
        assert run(resolve_secret_refs(42, b)) == 42
        assert run(resolve_secret_refs(None, b)) is None


# ── factory.find_secret_refs ─────────────────────────────────────────────────


class TestFindSecretRefs:
    def test_finds_ref_in_string(self):
        assert find_secret_refs("secret://MY_KEY") == ["MY_KEY"]

    def test_finds_ref_in_dict(self):
        config = {"api_key": "secret://KEY_A", "other": "plain"}
        assert set(find_secret_refs(config)) == {"KEY_A"}

    def test_finds_multiple_refs(self):
        config = {"a": "secret://KEY_A", "b": "secret://KEY_B"}
        refs = find_secret_refs(config)
        assert set(refs) == {"KEY_A", "KEY_B"}

    def test_finds_refs_in_list(self):
        refs = find_secret_refs(["secret://K1", "plain", "secret://K2"])
        assert set(refs) == {"K1", "K2"}

    def test_plain_string_no_refs(self):
        assert find_secret_refs("no-secret-here") == []

    def test_nested_structure(self):
        config = {
            "model": {"api_key": "secret://OPENAI_KEY"},
            "tools": [{"token": "secret://ZENDESK_TOKEN"}],
        }
        refs = find_secret_refs(config)
        assert set(refs) == {"OPENAI_KEY", "ZENDESK_TOKEN"}


# ── CLI integration (agentbreeder secret commands) ──────────────────────────────────


class TestSecretCLI:
    """Integration tests for the agentbreeder secret CLI using Typer's test client."""

    @pytest.fixture
    def runner(self):
        from typer.testing import CliRunner

        return CliRunner()

    @pytest.fixture
    def cli_app(self):
        from cli.commands.secret import secret_app

        return secret_app

    @pytest.fixture
    def env_file(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("EXISTING_KEY=existing-value\n")
        return f

    def test_list_shows_table(self, runner, cli_app, env_file):
        # With a custom env_file we'd need to pass it — test via JSON instead
        result = runner.invoke(cli_app, ["list", "--backend", "env", "--json"])
        assert result.exit_code == 0

    def test_set_and_get_json(self, runner, cli_app, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        # Monkeypatch _find_env_file to use our tmp file
        import engine.secrets.env_backend as eb

        monkeypatch.setattr(eb, "_find_env_file", lambda: env_file)

        result = runner.invoke(
            cli_app,
            ["set", "TEST_KEY", "--value", "test-val", "--backend", "env", "--json"],
        )
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["name"] == "TEST_KEY"
        assert out["status"] == "ok"

        result = runner.invoke(
            cli_app, ["get", "TEST_KEY", "--reveal", "--backend", "env", "--json"]
        )
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["value"] == "test-val"

    def test_delete_json(self, runner, cli_app, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("DEL_KEY=val\n")
        import engine.secrets.env_backend as eb

        monkeypatch.setattr(eb, "_find_env_file", lambda: env_file)

        result = runner.invoke(
            cli_app, ["delete", "DEL_KEY", "--force", "--backend", "env", "--json"]
        )
        assert result.exit_code == 0
        out = json.loads(result.stdout)
        assert out["deleted"] is True

    def test_delete_missing_key_exits_1(self, runner, cli_app, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        import engine.secrets.env_backend as eb

        monkeypatch.setattr(eb, "_find_env_file", lambda: env_file)

        result = runner.invoke(
            cli_app, ["delete", "NO_SUCH_KEY", "--force", "--backend", "env", "--json"]
        )
        assert result.exit_code == 1

    def test_migrate_dry_run(self, runner, cli_app, tmp_path, monkeypatch):
        src_env = tmp_path / ".env"
        src_env.write_text("OPENAI_API_KEY=sk-abc123\nANTHROPIC_KEY=ant-xyz\n")
        import engine.secrets.env_backend as eb

        monkeypatch.setattr(eb, "_find_env_file", lambda: src_env)

        # Migrate env → env (just for testing dry-run logic without cloud deps)
        result = runner.invoke(
            cli_app,
            ["migrate", "--from", "env", "--to", "env", "--dry-run", "--json"],
        )
        # Same backend → rejected
        assert result.exit_code == 2

    def test_migrate_same_backend_rejected(self, runner, cli_app):
        result = runner.invoke(cli_app, ["migrate", "--from", "env", "--to", "env", "--json"])
        assert result.exit_code == 2

    def test_get_missing_exits_1(self, runner, cli_app, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        import engine.secrets.env_backend as eb

        monkeypatch.setattr(eb, "_find_env_file", lambda: env_file)
        result = runner.invoke(cli_app, ["get", "NO_SUCH_KEY", "--backend", "env", "--json"])
        assert result.exit_code == 1
