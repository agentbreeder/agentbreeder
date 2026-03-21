"""Tests for cloud secrets backends (AWS, GCP, Vault) using mocks.

These tests mock all cloud SDK calls so they run without cloud credentials
and without the optional dependencies installed.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


def run(coro):
    return asyncio.run(coro)


# ── helpers: build mock cloud clients ────────────────────────────────────────


def _make_boto3_exception(name: str) -> type:
    """Create a fake boto3 exception class."""
    exc_cls = type(name, (Exception,), {})
    return exc_cls


def _make_boto3_client(secrets: dict[str, str] | None = None) -> MagicMock:
    """Build a mock boto3 secretsmanager client."""
    secrets = secrets or {}

    NotFound = _make_boto3_exception("ResourceNotFoundException")

    client = MagicMock()
    client.exceptions.ResourceNotFoundException = NotFound

    def get_secret_value(SecretId):
        # Strip prefix for lookup in our in-memory store
        name = SecretId.split("/")[-1]
        if name not in secrets:
            raise NotFound(f"Secret {SecretId} not found")
        return {"SecretString": json.dumps({"value": secrets[name]})}

    def put_secret_value(SecretId, SecretString):
        name = SecretId.split("/")[-1]
        parsed = json.loads(SecretString)
        secrets[name] = parsed["value"]

    def create_secret(**kwargs):
        name = kwargs["Name"].split("/")[-1]
        parsed = json.loads(kwargs["SecretString"])
        secrets[name] = parsed["value"]

    def delete_secret(SecretId, **kwargs):
        name = SecretId.split("/")[-1]
        if name not in secrets:
            raise NotFound(f"Secret {SecretId} not found")
        del secrets[name]

    def get_paginator(op):
        pager = MagicMock()
        page_data = [
            {
                "SecretList": [
                    {
                        "Name": f"agentbreeder/{k}",
                        "CreatedDate": datetime(2026, 1, 1, tzinfo=UTC),
                        "LastChangedDate": datetime(2026, 3, 1, tzinfo=UTC),
                        "Tags": [],
                    }
                    for k in secrets
                ]
            }
        ]
        pager.paginate.return_value = page_data
        return pager

    client.get_secret_value.side_effect = get_secret_value
    client.put_secret_value.side_effect = put_secret_value
    client.create_secret.side_effect = create_secret
    client.delete_secret.side_effect = delete_secret
    client.get_paginator.side_effect = get_paginator
    return client


# ── AWSSecretsManagerBackend ──────────────────────────────────────────────────


class TestAWSBackend:
    """Tests for AWSSecretsManagerBackend with a mocked boto3 client."""

    @pytest.fixture
    def backend_with_secrets(self):
        """Returns (backend, in-memory secrets dict)."""
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        store = {"OPENAI_API_KEY": "sk-abc123"}
        mock_client = _make_boto3_client(store)

        with patch("engine.secrets.aws_backend._client", return_value=mock_client):
            backend = AWSSecretsManagerBackend(region="us-east-1", prefix="agentbreeder/")
            yield backend, store

    def test_backend_name(self):
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        b = AWSSecretsManagerBackend()
        assert b.backend_name == "aws"

    def test_full_name_with_prefix(self):
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        b = AWSSecretsManagerBackend(prefix="agentbreeder/")
        assert b._full_name("MY_KEY") == "agentbreeder/MY_KEY"

    def test_full_name_no_prefix(self):
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        b = AWSSecretsManagerBackend(prefix="")
        assert b._full_name("MY_KEY") == "MY_KEY"

    def test_get_existing_secret(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        result = run(backend.get("OPENAI_API_KEY"))
        assert result == "sk-abc123"

    def test_get_missing_returns_none(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        result = run(backend.get("MISSING_KEY"))
        assert result is None

    def test_set_creates_new_secret(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.set("NEW_KEY", "new-value"))
        assert store["NEW_KEY"] == "new-value"

    def test_set_updates_existing_secret(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.set("OPENAI_API_KEY", "sk-updated"))
        assert store["OPENAI_API_KEY"] == "sk-updated"

    def test_delete_existing_secret(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.delete("OPENAI_API_KEY"))
        assert "OPENAI_API_KEY" not in store

    def test_delete_missing_raises(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        with pytest.raises(KeyError, match="not found"):
            run(backend.delete("NO_SUCH_KEY"))

    def test_list_returns_entries(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        entries = run(backend.list())
        assert len(entries) == 1
        assert entries[0].name == "OPENAI_API_KEY"
        assert entries[0].backend == "aws"

    def test_get_raw_string_value(self):
        """If the secret is a plain string (not JSON), it should be returned as-is."""
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        NotFound = _make_boto3_exception("ResourceNotFoundException")
        mock_client = MagicMock()
        mock_client.exceptions.ResourceNotFoundException = NotFound
        mock_client.get_secret_value.return_value = {"SecretString": "plain-string-value"}

        with patch("engine.secrets.aws_backend._client", return_value=mock_client):
            backend = AWSSecretsManagerBackend()
            result = run(backend.get("RAW_KEY"))
        assert result == "plain-string-value"

    def test_get_reraises_unexpected_exception(self):
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        NotFound = _make_boto3_exception("ResourceNotFoundException")
        mock_client = MagicMock()
        mock_client.exceptions.ResourceNotFoundException = NotFound
        mock_client.get_secret_value.side_effect = RuntimeError("network failure")

        with patch("engine.secrets.aws_backend._client", return_value=mock_client):
            backend = AWSSecretsManagerBackend()
            with pytest.raises(RuntimeError, match="network failure"):
                run(backend.get("KEY"))

    def test_set_with_tags(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.set("TAGGED_KEY", "tagged-value", tags={"env": "prod", "team": "eng"}))
        assert store["TAGGED_KEY"] == "tagged-value"

    def test_rotate_calls_set(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.rotate("OPENAI_API_KEY", "sk-rotated"))
        assert store["OPENAI_API_KEY"] == "sk-rotated"

    def test_import_error_propagates(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("No module named 'boto3'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        b = AWSSecretsManagerBackend()
        with pytest.raises(ImportError, match="boto3"):
            run(b.get("KEY"))


# ── GCPSecretManagerBackend ───────────────────────────────────────────────────


def _make_gcp_client(secrets: dict[str, str] | None = None) -> MagicMock:
    """Build a mock GCP Secret Manager client."""
    secrets = secrets or {}

    client = MagicMock()

    def access_secret_version(request):
        path = request["name"]  # projects/proj/secrets/agentbreeder-KEY/versions/latest
        secret_id = path.split("/secrets/")[1].split("/versions")[0]
        name = secret_id.removeprefix("agentbreeder-")
        if name not in secrets:
            raise Exception("NOT_FOUND: Secret not found")
        resp = MagicMock()
        resp.payload.data = secrets[name].encode("utf-8")
        return resp

    def add_secret_version(request):
        parent = request["parent"]  # projects/proj/secrets/agentbreeder-KEY
        secret_id = parent.split("/secrets/")[1]
        name = secret_id.removeprefix("agentbreeder-")
        value = request["payload"]["data"].decode("utf-8")
        secrets[name] = value

    def create_secret(request):
        secret_id = request["secret_id"].removeprefix("agentbreeder-")
        secrets.setdefault(secret_id, "")

    def delete_secret(request):
        name_path = request["name"]  # projects/proj/secrets/agentbreeder-KEY
        secret_id = name_path.split("/secrets/")[1]
        name = secret_id.removeprefix("agentbreeder-")
        if name not in secrets:
            raise Exception("NOT_FOUND: Secret not found")
        del secrets[name]

    # list_secrets returns an iterable of mock objects
    class FakeSecret:
        def __init__(self, key):
            self.name = f"projects/my-project/secrets/agentbreeder-{key}"
            self.create_time = MagicMock(seconds=1741824000)
            self.labels = {}

    client.access_secret_version.side_effect = access_secret_version
    client.add_secret_version.side_effect = add_secret_version
    client.create_secret.side_effect = create_secret
    client.delete_secret.side_effect = delete_secret
    client.list_secrets.return_value = [FakeSecret(k) for k in secrets]

    return client, secrets


class TestGCPBackend:
    @pytest.fixture
    def backend_with_secrets(self):
        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        mock_client, store = _make_gcp_client({"ANTHROPIC_KEY": "ant-xyz"})

        with patch("engine.secrets.gcp_backend._client", return_value=mock_client):
            backend = GCPSecretManagerBackend(project_id="my-project", prefix="agentbreeder-")
            yield backend, store, mock_client

    def test_backend_name(self):
        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        b = GCPSecretManagerBackend(project_id="proj")
        assert b.backend_name == "gcp"

    def test_no_project_raises(self):
        import os

        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        # Temporarily clear GOOGLE_CLOUD_PROJECT
        old = os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
        try:
            with pytest.raises(ValueError, match="project ID required"):
                GCPSecretManagerBackend()
        finally:
            if old is not None:
                os.environ["GOOGLE_CLOUD_PROJECT"] = old

    def test_get_existing(self, backend_with_secrets):
        backend, _, _ = backend_with_secrets
        result = run(backend.get("ANTHROPIC_KEY"))
        assert result == "ant-xyz"

    def test_get_missing_returns_none(self, backend_with_secrets):
        backend, _, _ = backend_with_secrets
        result = run(backend.get("NO_SUCH"))
        assert result is None

    def test_set_new_secret(self, backend_with_secrets):
        backend, store, mock_client = backend_with_secrets
        # Simulate NOT_FOUND so create_secret is called
        original = mock_client.add_secret_version.side_effect

        call_count = [0]

        def add_version_fail_first(request):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("NOT_FOUND")
            original(request)

        mock_client.add_secret_version.side_effect = add_version_fail_first
        run(backend.set("NEW_GCP_KEY", "gcp-value"))
        assert mock_client.create_secret.called

    def test_set_existing_secret(self, backend_with_secrets):
        backend, store, _ = backend_with_secrets
        run(backend.set("ANTHROPIC_KEY", "ant-updated"))
        assert store["ANTHROPIC_KEY"] == "ant-updated"

    def test_delete_existing(self, backend_with_secrets):
        backend, store, _ = backend_with_secrets
        run(backend.delete("ANTHROPIC_KEY"))
        assert "ANTHROPIC_KEY" not in store

    def test_delete_missing_raises(self, backend_with_secrets):
        backend, _, _ = backend_with_secrets
        with pytest.raises(KeyError, match="not found"):
            run(backend.delete("NO_SUCH_KEY"))

    def test_list_returns_entries(self, backend_with_secrets):
        backend, _, _ = backend_with_secrets
        entries = run(backend.list())
        assert len(entries) == 1
        assert entries[0].name == "ANTHROPIC_KEY"
        assert entries[0].backend == "gcp"

    def test_import_error_propagates(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "google" in name:
                raise ImportError("No module named 'google.cloud'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        b = GCPSecretManagerBackend(project_id="proj")
        with pytest.raises(ImportError, match="google-cloud-secret-manager"):
            run(b.get("KEY"))

    def test_secret_id_truncates_long_names(self):
        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        b = GCPSecretManagerBackend(project_id="proj", prefix="")
        long_name = "A" * 300
        assert len(b._secret_id(long_name)) == 255


# ── VaultBackend ──────────────────────────────────────────────────────────────


def _make_hvac_client(secrets: dict[str, str] | None = None) -> MagicMock:
    """Build a mock hvac Vault client."""
    secrets = secrets or {}

    client = MagicMock()
    client.is_authenticated.return_value = True

    class InvalidPathError(Exception):
        __name__ = "InvalidPath"

    def read_secret_version(path, mount_point, raise_on_deleted_version):
        name = path.removeprefix("agentbreeder/")
        if name not in secrets:
            raise InvalidPathError(f"404 path not found: {path}")
        return {"data": {"data": {"value": secrets[name]}}}

    def create_or_update_secret(path, secret, mount_point):
        name = path.removeprefix("agentbreeder/")
        secrets[name] = secret["value"]

    def delete_metadata_and_all_versions(path, mount_point):
        name = path.removeprefix("agentbreeder/")
        if name not in secrets:
            raise InvalidPathError(f"404 path not found: {path}")
        del secrets[name]

    def list_secrets(path, mount_point):
        return {"data": {"keys": [f"agentbreeder/{k}" for k in secrets]}}

    kv = client.secrets.kv.v2
    kv.read_secret_version.side_effect = read_secret_version
    kv.create_or_update_secret.side_effect = create_or_update_secret
    kv.delete_metadata_and_all_versions.side_effect = delete_metadata_and_all_versions
    kv.list_secrets.side_effect = list_secrets

    return client, secrets


class TestVaultBackend:
    @pytest.fixture
    def backend_with_secrets(self):
        from engine.secrets.vault_backend import VaultBackend

        mock_client, store = _make_hvac_client({"VAULT_KEY": "vault-value"})

        with patch("engine.secrets.vault_backend._client", return_value=mock_client):
            backend = VaultBackend(addr="http://vault:8200", token="root", prefix="agentbreeder/")
            yield backend, store

    def test_backend_name(self):
        from engine.secrets.vault_backend import VaultBackend

        b = VaultBackend(addr="http://vault:8200", token="root")
        assert b.backend_name == "vault"

    def test_path_with_prefix(self):
        from engine.secrets.vault_backend import VaultBackend

        b = VaultBackend(addr="http://v:8200", token="t", prefix="agentbreeder/")
        assert b._path("MY_KEY") == "agentbreeder/MY_KEY"

    def test_path_no_prefix(self):
        from engine.secrets.vault_backend import VaultBackend

        b = VaultBackend(addr="http://v:8200", token="t", prefix="")
        assert b._path("MY_KEY") == "MY_KEY"

    def test_get_existing(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        result = run(backend.get("VAULT_KEY"))
        assert result == "vault-value"

    def test_get_missing_returns_none(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        result = run(backend.get("NO_SUCH"))
        assert result is None

    def test_set_creates_secret(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.set("NEW_VAULT_KEY", "new-vault-value"))
        assert store["NEW_VAULT_KEY"] == "new-vault-value"

    def test_set_with_tags_does_not_crash(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.set("TAGGED", "value", tags={"env": "prod"}))
        assert store["TAGGED"] == "value"

    def test_delete_existing(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.delete("VAULT_KEY"))
        assert "VAULT_KEY" not in store

    def test_delete_missing_raises(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        with pytest.raises(KeyError, match="not found in Vault"):
            run(backend.delete("NO_SUCH"))

    def test_list_returns_entries(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        entries = run(backend.list())
        assert len(entries) == 1
        assert entries[0].name == "VAULT_KEY"
        assert entries[0].backend == "vault"

    def test_list_skips_directories(self, backend_with_secrets):
        """Keys ending with '/' are sub-directories and should be skipped."""
        backend, store = backend_with_secrets
        from engine.secrets.vault_backend import VaultBackend

        mock_client, _ = _make_hvac_client({"REAL_KEY": "v", "subdir/": ""})
        # Override list_secrets to include a dir entry
        mock_client.secrets.kv.v2.list_secrets.return_value = {
            "data": {"keys": ["agentbreeder/REAL_KEY", "agentbreeder/subdir/"]}
        }
        with patch("engine.secrets.vault_backend._client", return_value=mock_client):
            b = VaultBackend(addr="http://v:8200", token="t", prefix="agentbreeder/")
            entries = run(b.list())
        names = [e.name for e in entries]
        assert "REAL_KEY" in names
        assert "subdir/" not in names

    def test_list_empty_when_no_secrets(self, backend_with_secrets):
        from engine.secrets.vault_backend import VaultBackend

        class InvalidPathError(Exception):
            __name__ = "InvalidPath"

        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.list_secrets.side_effect = InvalidPathError("404")

        with patch("engine.secrets.vault_backend._client", return_value=mock_client):
            b = VaultBackend(addr="http://v:8200", token="t", prefix="agentbreeder/")
            entries = run(b.list())
        assert entries == []

    def test_rotate_updates_value(self, backend_with_secrets):
        backend, store = backend_with_secrets
        run(backend.rotate("VAULT_KEY", "rotated-value"))
        assert store["VAULT_KEY"] == "rotated-value"

    def test_rotate_missing_raises(self, backend_with_secrets):
        backend, _ = backend_with_secrets
        with pytest.raises(KeyError, match="not found"):
            run(backend.rotate("NO_SUCH", "new"))

    def test_import_error_propagates(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "hvac":
                raise ImportError("No module named 'hvac'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from engine.secrets.vault_backend import VaultBackend

        b = VaultBackend(addr="http://v:8200", token="t")
        with pytest.raises(ImportError, match="hvac"):
            run(b.get("KEY"))

    def test_get_reraises_unexpected_exception(self, backend_with_secrets):
        from engine.secrets.vault_backend import VaultBackend

        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.read_secret_version.side_effect = RuntimeError("vault down")

        with patch("engine.secrets.vault_backend._client", return_value=mock_client):
            b = VaultBackend(addr="http://v:8200", token="t")
            with pytest.raises(RuntimeError, match="vault down"):
                run(b.get("KEY"))


# ── env_backend missing lines ─────────────────────────────────────────────────


class TestEnvBackendExtraCoverage:
    def test_find_env_file_returns_cwd_if_exists(self, tmp_path, monkeypatch):
        """Cover line 23 — return cwd_env if it exists."""
        env = tmp_path / ".env"
        env.write_text("KEY=val\n")
        monkeypatch.chdir(tmp_path)
        from engine.secrets.env_backend import _find_env_file

        result = _find_env_file()
        assert result == env

    def test_parse_env_file_skips_malformed_lines(self, tmp_path):
        """Cover line 38 — continue when line doesn't match key=value."""
        env = tmp_path / ".env"
        env.write_text("VALID=yes\nthis-is-not-valid-env-line\n")
        from engine.secrets.env_backend import _parse_env_file

        result = _parse_env_file(env)
        assert result == {"VALID": "yes"}

    def test_write_env_file_new_key_appended(self, tmp_path):
        """Cover lines 67/71 — existing file, add a key not already present."""
        from engine.secrets.env_backend import _write_env_file

        env = tmp_path / ".env"
        env.write_text("EXISTING=val\n")
        _write_env_file(env, {"EXISTING": "val", "NEW_KEY": "new"})
        text = env.read_text()
        assert "NEW_KEY=new" in text
        assert "EXISTING=val" in text

    def test_write_env_file_preserves_non_kv_lines(self, tmp_path):
        """Cover line 57-58 — non-key=value lines in existing file are preserved."""
        from engine.secrets.env_backend import _write_env_file

        env = tmp_path / ".env"
        env.write_text("# My comment\nKEY=val\n")
        _write_env_file(env, {"KEY": "updated"})
        text = env.read_text()
        assert "# My comment" in text
        assert "KEY=updated" in text


# ── Additional coverage for remaining branches ────────────────────────────────


class TestFactoryAliases:
    """Cover factory.py aliases and branch paths."""

    def test_get_backend_hashicorp_vault_alias(self, monkeypatch):
        """Covers factory.py lines 38-39 — 'hashicorp_vault' alias."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "hvac":
                raise ImportError("No hvac")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from engine.secrets.factory import get_backend

        b = get_backend("hashicorp_vault", addr="http://v:8200", token="t")
        assert b.backend_name == "vault"

    def test_get_backend_vault_alias(self, monkeypatch):
        """Covers factory.py lines 37-39 — 'vault' alias via get_backend."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "hvac":
                raise ImportError("No hvac")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from engine.secrets.factory import get_backend

        b = get_backend("vault", addr="http://v:8200", token="t")
        assert b.backend_name == "vault"

    def test_get_backend_aws_secrets_manager_alias(self, monkeypatch):
        """Covers the 'aws_secrets_manager' alias."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "boto3":
                raise ImportError("No boto3")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        from engine.secrets.factory import get_backend

        b = get_backend("aws_secrets_manager")
        assert b.backend_name == "aws"

    def test_get_backend_gcp_secret_manager_alias(self):
        """Covers the 'gcp_secret_manager' alias."""
        from engine.secrets.factory import get_backend

        b = get_backend("gcp_secret_manager", project_id="my-project")
        assert b.backend_name == "gcp"


class TestAWSHelpers:
    """Cover _to_utc edge cases and _client() success path."""

    def test_to_utc_none(self):
        from engine.secrets.aws_backend import _to_utc

        assert _to_utc(None) is None

    def test_to_utc_naive_datetime(self):
        from engine.secrets.aws_backend import _to_utc

        naive = datetime(2026, 3, 13, 12, 0, 0)
        result = _to_utc(naive)
        assert result.tzinfo is not None

    def test_to_utc_aware_datetime(self):
        from engine.secrets.aws_backend import _to_utc

        aware = datetime(2026, 3, 13, 12, 0, 0, tzinfo=UTC)
        result = _to_utc(aware)
        assert result.tzinfo is not None

    def test_client_success_with_mocked_boto3(self, monkeypatch):
        """Cover line 33 — boto3 import + client creation succeeds."""
        mock_boto3 = types.ModuleType("boto3")
        mock_client_obj = MagicMock()
        mock_boto3.client = MagicMock(return_value=mock_client_obj)
        monkeypatch.setitem(sys.modules, "boto3", mock_boto3)
        from engine.secrets.aws_backend import _client

        result = _client("us-east-1")
        assert result is mock_client_obj

    def test_set_triggers_create_when_not_found(self):
        """Cover lines 88-97 — put_secret raises NotFound → create_secret path."""
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        store = {}
        NotFound = _make_boto3_exception("ResourceNotFoundException")
        mock_client = MagicMock()
        mock_client.exceptions.ResourceNotFoundException = NotFound

        def put_secret(SecretId, SecretString):
            raise NotFound("Secret does not exist")

        def create_secret(**kwargs):
            name = kwargs["Name"].split("/")[-1]
            store[name] = json.loads(kwargs["SecretString"])["value"]

        mock_client.put_secret_value.side_effect = put_secret
        mock_client.create_secret.side_effect = create_secret

        with patch("engine.secrets.aws_backend._client", return_value=mock_client):
            backend = AWSSecretsManagerBackend(prefix="agentbreeder/")
            run(backend.set("NEW_KEY", "created-value"))

        assert store["NEW_KEY"] == "created-value"

    def test_set_with_tags_triggers_create_with_tags(self):
        """Cover line 95 — create_secret path WITH tags."""
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        NotFound = _make_boto3_exception("ResourceNotFoundException")
        mock_client = MagicMock()
        mock_client.exceptions.ResourceNotFoundException = NotFound
        mock_client.put_secret_value.side_effect = NotFound("not found")
        created_kwargs = {}

        def create_secret(**kwargs):
            created_kwargs.update(kwargs)

        mock_client.create_secret.side_effect = create_secret

        with patch("engine.secrets.aws_backend._client", return_value=mock_client):
            backend = AWSSecretsManagerBackend(prefix="agentbreeder/")
            run(backend.set("TAGGED", "val", tags={"env": "prod"}))

        assert "Tags" in created_kwargs
        assert created_kwargs["Tags"] == [{"Key": "env", "Value": "prod"}]


class TestVaultHelpers:
    """Cover vault _client() success/fail and re-raise paths."""

    def test_client_success_with_mocked_hvac(self, monkeypatch):
        """Cover lines 28-33 — hvac import succeeds, client authenticated."""
        mock_hvac = types.ModuleType("hvac")
        mock_client_obj = MagicMock()
        mock_client_obj.is_authenticated.return_value = True
        mock_hvac.Client = MagicMock(return_value=mock_client_obj)
        monkeypatch.setitem(sys.modules, "hvac", mock_hvac)
        from engine.secrets.vault_backend import _client

        result = _client("http://vault:8200", "root-token")
        assert result is mock_client_obj

    def test_client_unauthenticated_raises(self, monkeypatch):
        """Cover line 29-32 — hvac client not authenticated."""
        mock_hvac = types.ModuleType("hvac")
        mock_client_obj = MagicMock()
        mock_client_obj.is_authenticated.return_value = False
        mock_hvac.Client = MagicMock(return_value=mock_client_obj)
        monkeypatch.setitem(sys.modules, "hvac", mock_hvac)
        from engine.secrets.vault_backend import _client

        with pytest.raises(PermissionError, match="authentication failed"):
            _client("http://vault:8200", "bad-token")

    def test_delete_reraises_non_invalid_path(self):
        """Cover line 101 — delete raises non-InvalidPath exception."""
        from engine.secrets.vault_backend import VaultBackend

        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.delete_metadata_and_all_versions.side_effect = RuntimeError(
            "vault is sealed"
        )
        with patch("engine.secrets.vault_backend._client", return_value=mock_client):
            b = VaultBackend(addr="http://v:8200", token="t")
            with pytest.raises(RuntimeError, match="vault is sealed"):
                run(b.delete("KEY"))

    def test_list_reraises_non_invalid_path(self):
        """Cover line 115 — list raises non-InvalidPath exception."""
        from engine.secrets.vault_backend import VaultBackend

        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.secrets.kv.v2.list_secrets.side_effect = RuntimeError("vault sealed")
        with patch("engine.secrets.vault_backend._client", return_value=mock_client):
            b = VaultBackend(addr="http://v:8200", token="t")
            with pytest.raises(RuntimeError, match="vault sealed"):
                run(b.list())


class TestGCPHelpers:
    """Cover GCP edge cases."""

    def test_client_success_with_mocked_sdk(self, monkeypatch):
        """Cover line 28 — google SDK import succeeds."""

        mock_google = types.ModuleType("google")
        mock_cloud = types.ModuleType("google.cloud")
        mock_sm = types.ModuleType("google.cloud.secretmanager")
        mock_client_obj = MagicMock()
        mock_sm.SecretManagerServiceClient = MagicMock(return_value=mock_client_obj)

        monkeypatch.setitem(sys.modules, "google", mock_google)
        monkeypatch.setitem(sys.modules, "google.cloud", mock_cloud)
        monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", mock_sm)
        mock_google.cloud = mock_cloud
        mock_cloud.secretmanager = mock_sm

        from engine.secrets.gcp_backend import _client

        result = _client()
        assert result is mock_client_obj

    def test_get_reraises_unexpected_exception(self):
        """Cover lines 85-86 — get raises non-NOT_FOUND exception."""
        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        mock_client = MagicMock()
        mock_client.access_secret_version.side_effect = RuntimeError("quota exceeded")
        with patch("engine.secrets.gcp_backend._client", return_value=mock_client):
            b = GCPSecretManagerBackend(project_id="proj")
            with pytest.raises(RuntimeError, match="quota exceeded"):
                run(b.get("KEY"))

    def test_set_reraises_non_not_found(self):
        """Cover line 102 — set raises non-NOT_FOUND exception."""
        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        mock_client = MagicMock()
        mock_client.add_secret_version.side_effect = RuntimeError("quota exceeded")
        with patch("engine.secrets.gcp_backend._client", return_value=mock_client):
            b = GCPSecretManagerBackend(project_id="proj")
            with pytest.raises(RuntimeError, match="quota exceeded"):
                run(b.set("KEY", "value"))

    def test_delete_reraises_non_not_found(self):
        """Cover lines 128+ — delete raises non-NOT_FOUND exception."""
        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        mock_client = MagicMock()
        mock_client.delete_secret.side_effect = RuntimeError("network error")
        with patch("engine.secrets.gcp_backend._client", return_value=mock_client):
            b = GCPSecretManagerBackend(project_id="proj")
            with pytest.raises(RuntimeError, match="network error"):
                run(b.delete("KEY"))

    def test_dt_helper_with_naive_datetime(self):
        """Cover _dt with a naive datetime."""
        from engine.secrets.gcp_backend import _dt

        naive = datetime(2026, 3, 13, 12, 0)
        result = _dt(naive)
        assert result is not None
        assert result.tzinfo is not None

    def test_dt_helper_with_none(self):
        """Cover _dt(None) → None."""
        from engine.secrets.gcp_backend import _dt

        assert _dt(None) is None

    def test_dt_helper_with_aware_datetime(self):
        """Cover _dt with aware datetime."""
        from engine.secrets.gcp_backend import _dt

        aware = datetime(2026, 3, 13, tzinfo=UTC)
        result = _dt(aware)
        assert result is not None
