"""Tests for Azure Key Vault secrets backend using mocks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


def run(coro):
    return asyncio.run(coro)


def _make_azure_exception(name: str) -> type:
    return type(name, (Exception,), {})


def _make_azure_client(secrets: dict[str, str] | None = None) -> MagicMock:
    secrets = secrets or {}
    NotFound = _make_azure_exception("ResourceNotFoundError")
    client = MagicMock()

    class FakeSecret:
        def __init__(self, name, value, tags=None):
            self.name = name
            self.value = value
            self.properties = MagicMock()
            self.properties.name = name
            self.properties.created_on = datetime(2026, 1, 1, tzinfo=UTC)
            self.properties.updated_on = datetime(2026, 3, 1, tzinfo=UTC)
            self.properties.tags = tags or {}

    def get_secret(name):
        if name not in secrets:
            raise NotFound("Secret not found")
        return FakeSecret(name, secrets[name])

    def set_secret(name, value, tags=None):
        secrets[name] = value
        return FakeSecret(name, value, tags)

    def begin_delete_secret(name):
        if name not in secrets:
            raise NotFound("Secret not found")
        del secrets[name]
        poller = MagicMock()
        return poller

    def list_properties_of_secrets():
        props = []
        for k in secrets:
            secret = FakeSecret(k, secrets[k])
            props.append(secret.properties)
        return props

    client.get_secret.side_effect = get_secret
    client.set_secret.side_effect = set_secret
    client.begin_delete_secret.side_effect = begin_delete_secret
    client.list_properties_of_secrets.side_effect = list_properties_of_secrets
    return client


class TestAzureBackend:
    @pytest.fixture
    def backend_with_secrets(self):
        from engine.secrets.azure_backend import AzureKeyVaultBackend

        store = {"agentbreeder-OPENAI-API-KEY": "sk-abc"}
        mock_client = _make_azure_client(store)
        with patch("engine.secrets.azure_backend._client", return_value=mock_client):
            backend = AzureKeyVaultBackend(
                vault_url="https://myvault.vault.azure.net/", prefix="agentbreeder-"
            )
            yield backend, store

    def test_backend_name(self):
        from engine.secrets.azure_backend import AzureKeyVaultBackend

        b = AzureKeyVaultBackend(vault_url="https://myvault.vault.azure.net/")
        assert b.backend_name == "azure"

    def test_secret_id_sanitization(self):
        from engine.secrets.azure_backend import AzureKeyVaultBackend

        b = AzureKeyVaultBackend(vault_url="https://v.vault.azure.net/", prefix="ab-")
        assert b._secret_id("my_secret/key") == "ab-my-secret-key"

    def test_get_existing(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        result = run(backend.get("OPENAI_API_KEY"))
        assert result == "sk-abc"

    def test_get_missing_returns_none(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        result = run(backend.get("MISSING"))
        assert result is None

    def test_set_new_secret(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.set("NEW_KEY", "new-value"))
        assert store["agentbreeder-NEW-KEY"] == "new-value"

    def test_delete_existing(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.delete("OPENAI_API_KEY"))
        assert "agentbreeder-OPENAI-API-KEY" not in store

    def test_delete_missing_raises(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        with pytest.raises(KeyError, match="not found"):
            run(backend.delete("NO-SUCH"))

    def test_list_returns_entries(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        entries = run(backend.list())
        assert len(entries) == 1
        assert entries[0].name == "OPENAI-API-KEY"
        assert entries[0].backend == "azure"

    def test_import_error_propagates(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            # Block only the azure SDK packages — not our own
            # engine.secrets.azure_backend module (whose path contains "azure").
            if name.startswith("azure"):
                raise ImportError("No azure-keyvault-secrets")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from engine.secrets.azure_backend import AzureKeyVaultBackend

        b = AzureKeyVaultBackend(vault_url="https://v.vault.azure.net/")
        with pytest.raises(ImportError, match="azure-keyvault-secrets"):
            run(b.get("KEY"))
