"""Tests for the agentbreeder provider CLI command."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


@pytest.fixture
def providers_file(tmp_path):
    """Return a temp file path for the providers registry and patch it."""
    pf = tmp_path / "providers.json"
    with patch("cli.commands.provider.PROVIDERS_FILE", pf):
        yield pf


class TestProviderList:
    def test_list_empty(self, providers_file):
        # With Track F's catalog landed, `provider list` always shows the built-in
        # OpenAI-compatible catalog table even when no providers are configured.
        result = runner.invoke(app, ["provider", "list"])
        assert result.exit_code == 0
        assert "OpenAI-Compatible Catalog" in result.output

    def test_list_empty_json(self, providers_file):
        result = runner.invoke(app, ["provider", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Track F: top-level shape is {"configured": [...], "catalog": [...]}.
        assert data["configured"] == []
        catalog_names = {entry["name"] for entry in data["catalog"]}
        assert "nvidia" in catalog_names
        assert "groq" in catalog_names

    def test_list_with_providers(self, providers_file):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "active",
                "model_count": 7,
                "masked_key": "••••abcd",
                "base_url": "https://api.openai.com/v1",
            }
        }
        providers_file.write_text(json.dumps(providers))

        result = runner.invoke(app, ["provider", "list"])
        assert result.exit_code == 0
        assert "OpenAI" in result.output
        assert "active" in result.output

    def test_list_with_providers_json(self, providers_file):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "active",
                "model_count": 7,
                "masked_key": "••••abcd",
                "base_url": "https://api.openai.com/v1",
            }
        }
        providers_file.write_text(json.dumps(providers))

        result = runner.invoke(app, ["provider", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        # Track F: top-level shape is {"configured": [...], "catalog": [...]}.
        assert len(data["configured"]) == 1
        assert data["configured"][0]["name"] == "OpenAI"


class TestProviderAdd:
    def test_add_ollama_non_interactive(self, providers_file, tmp_path):
        env_file = tmp_path / ".env"
        with patch("cli.commands.provider._find_env_file", return_value=env_file):
            result = runner.invoke(
                app,
                ["provider", "add", "ollama", "--base-url", "http://localhost:11434", "--json"],
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["provider"]["provider_type"] == "ollama"
        assert data["provider"]["status"] == "active"
        assert data["models"]

    def test_add_openai_with_key(self, providers_file, tmp_path):
        env_file = tmp_path / ".env"
        with patch("cli.commands.provider._find_env_file", return_value=env_file):
            result = runner.invoke(
                app,
                ["provider", "add", "openai", "--api-key", "sk-proj-testkey1234", "--json"],
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["provider"]["provider_type"] == "openai"
        assert data["provider"]["status"] == "active"
        assert data["provider"]["model_count"] == 7
        # Key should be saved to .env
        assert env_file.exists()
        assert "OPENAI_API_KEY=sk-proj-testkey1234" in env_file.read_text()

    def test_add_unknown_type(self, providers_file):
        result = runner.invoke(app, ["provider", "add", "unknown-provider"])
        assert result.exit_code == 1
        assert "Unknown provider type" in result.output

    def test_add_anthropic_with_key_json(self, providers_file, tmp_path):
        env_file = tmp_path / ".env"
        with patch("cli.commands.provider._find_env_file", return_value=env_file):
            result = runner.invoke(
                app,
                ["provider", "add", "anthropic", "--api-key", "sk-ant-test", "--json"],
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["provider"]["provider_type"] == "anthropic"
        assert "ANTHROPIC_API_KEY=sk-ant-test" in env_file.read_text()


class TestProviderTest:
    def test_test_not_configured(self, providers_file):
        result = runner.invoke(app, ["provider", "test", "openai"])
        assert result.exit_code == 1
        assert "not configured" in result.output

    def test_test_success(self, providers_file):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "active",
                "model_count": 7,
                "base_url": "https://api.openai.com/v1",
            }
        }
        providers_file.write_text(json.dumps(providers))

        result = runner.invoke(app, ["provider", "test", "openai"])
        assert result.exit_code == 0
        assert "healthy" in result.output

    def test_test_json(self, providers_file):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "active",
                "model_count": 7,
                "base_url": "https://api.openai.com/v1",
            }
        }
        providers_file.write_text(json.dumps(providers))

        result = runner.invoke(app, ["provider", "test", "openai", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] is True
        assert "latency_ms" in data


class TestProviderModels:
    def test_models_not_configured(self, providers_file):
        result = runner.invoke(app, ["provider", "models", "openai"])
        assert result.exit_code == 1
        assert "not configured" in result.output

    def test_models_list(self, providers_file):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "active",
                "model_count": 7,
                "base_url": "https://api.openai.com/v1",
            }
        }
        providers_file.write_text(json.dumps(providers))

        result = runner.invoke(app, ["provider", "models", "openai"])
        assert result.exit_code == 0
        assert "gpt-4o" in result.output

    def test_models_json(self, providers_file):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "active",
                "model_count": 7,
                "base_url": "https://api.openai.com/v1",
            }
        }
        providers_file.write_text(json.dumps(providers))

        result = runner.invoke(app, ["provider", "models", "openai", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "gpt-4o" in data


class TestProviderRemove:
    def test_remove_not_found(self, providers_file):
        result = runner.invoke(app, ["provider", "remove", "openai"])
        assert result.exit_code == 1
        assert "not configured" in result.output

    def test_remove_confirmed(self, providers_file, tmp_path):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "active",
                "model_count": 7,
                "base_url": "https://api.openai.com/v1",
            }
        }
        providers_file.write_text(json.dumps(providers))

        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-test\nOTHER=value\n")

        with patch("cli.commands.provider._find_env_file", return_value=env_file):
            result = runner.invoke(app, ["provider", "remove", "openai", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["removed"] == "openai"

        # Provider should be removed
        remaining = json.loads(providers_file.read_text())
        assert "openai" not in remaining

        # Key should be removed from .env
        assert "OPENAI_API_KEY" not in env_file.read_text()
        assert "OTHER=value" in env_file.read_text()


class TestProviderDisableEnable:
    def test_disable(self, providers_file):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "active",
                "model_count": 7,
            }
        }
        providers_file.write_text(json.dumps(providers))

        result = runner.invoke(app, ["provider", "disable", "openai"])
        assert result.exit_code == 0
        assert "disabled" in result.output

        updated = json.loads(providers_file.read_text())
        assert updated["openai"]["status"] == "disabled"

    def test_enable(self, providers_file):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "disabled",
                "model_count": 7,
            }
        }
        providers_file.write_text(json.dumps(providers))

        result = runner.invoke(app, ["provider", "enable", "openai"])
        assert result.exit_code == 0
        assert "re-enabled" in result.output

        updated = json.loads(providers_file.read_text())
        assert updated["openai"]["status"] == "active"

    def test_disable_not_found(self, providers_file):
        result = runner.invoke(app, ["provider", "disable", "openai"])
        assert result.exit_code == 1

    def test_disable_json(self, providers_file):
        providers = {
            "openai": {
                "name": "OpenAI",
                "provider_type": "openai",
                "status": "active",
                "model_count": 7,
            }
        }
        providers_file.write_text(json.dumps(providers))

        result = runner.invoke(app, ["provider", "disable", "openai", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "disabled"


class TestHelpers:
    def test_mask_key_short(self):
        from cli.commands.provider import _mask_key

        assert _mask_key("short") == "••••"

    def test_mask_key_long(self):
        from cli.commands.provider import _mask_key

        assert _mask_key("sk-proj-abcdef1234") == "••••1234"

    def test_load_providers_missing_file(self, providers_file):
        from cli.commands.provider import _load_providers

        assert _load_providers() == {}

    def test_env_key_write_and_update(self, tmp_path):
        from cli.commands.provider import _write_env_key

        env_file = tmp_path / ".env"
        with patch("cli.commands.provider._find_env_file", return_value=env_file):
            _write_env_key("MY_KEY", "value1")
            assert "MY_KEY=value1" in env_file.read_text()

            # Update existing key
            _write_env_key("MY_KEY", "value2")
            content = env_file.read_text()
            assert "MY_KEY=value2" in content
            assert content.count("MY_KEY=") == 1

    def test_remove_env_key(self, tmp_path):
        from cli.commands.provider import _remove_env_key

        env_file = tmp_path / ".env"
        env_file.write_text("KEY_A=1\nKEY_B=2\nKEY_C=3\n")

        with patch("cli.commands.provider._find_env_file", return_value=env_file):
            _remove_env_key("KEY_B")
            content = env_file.read_text()
            assert "KEY_A=1" in content
            assert "KEY_B" not in content
            assert "KEY_C=3" in content
