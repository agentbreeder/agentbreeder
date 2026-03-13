"""HashiCorp Vault backend (KV v2 secrets engine).

Requires: pip install hvac
Required env vars (or pass to constructor):
    VAULT_ADDR  — Vault server address (e.g. https://vault.company.com)
    VAULT_TOKEN — Vault token with read/write policy on the mount path
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from engine.secrets.base import SecretEntry, SecretsBackend

logger = logging.getLogger(__name__)

_HVAC_IMPORT_ERROR = "HashiCorp Vault backend requires hvac. Install it with: pip install hvac"


def _client(addr: str, token: str):  # type: ignore[return]
    try:
        import hvac  # type: ignore[import-untyped]

        client = hvac.Client(url=addr, token=token)
        if not client.is_authenticated():
            raise PermissionError(
                f"Vault authentication failed. Check VAULT_TOKEN and VAULT_ADDR ({addr})."
            )
        return client
    except ImportError as exc:
        raise ImportError(_HVAC_IMPORT_ERROR) from exc


class VaultBackend(SecretsBackend):
    """Secrets stored in HashiCorp Vault KV v2 secrets engine.

    Each Agent Garden secret is stored at:
        {mount}/{prefix}{name}  (with data key "value")
    """

    def __init__(
        self,
        addr: str | None = None,
        token: str | None = None,
        mount: str = "secret",
        prefix: str = "garden/",
    ) -> None:
        self._addr = addr or os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
        self._token = token or os.environ.get("VAULT_TOKEN", "")
        self._mount = mount
        self._prefix = prefix

    @property
    def backend_name(self) -> str:
        return "vault"

    def _path(self, name: str) -> str:
        return f"{self._prefix}{name}" if self._prefix else name

    async def get(self, name: str) -> str | None:
        client = _client(self._addr, self._token)
        try:
            resp = client.secrets.kv.v2.read_secret_version(
                path=self._path(name),
                mount_point=self._mount,
                raise_on_deleted_version=False,
            )
            return resp["data"]["data"].get("value")
        except Exception as exc:
            if "InvalidPath" in type(exc).__name__ or "404" in str(exc):
                return None
            logger.error("Failed to get secret '%s' from Vault: %s", name, exc)
            raise

    async def set(self, name: str, value: str, *, tags: dict[str, str] | None = None) -> None:
        client = _client(self._addr, self._token)
        client.secrets.kv.v2.create_or_update_secret(
            path=self._path(name),
            secret={"value": value},
            mount_point=self._mount,
        )
        logger.info("Set secret '%s' in Vault (mount: %s)", name, self._mount)

    async def delete(self, name: str) -> None:
        client = _client(self._addr, self._token)
        try:
            client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=self._path(name),
                mount_point=self._mount,
            )
            logger.info("Deleted secret '%s' from Vault", name)
        except Exception as exc:
            if "InvalidPath" in type(exc).__name__ or "404" in str(exc):
                raise KeyError(f"Secret '{name}' not found in Vault") from exc
            raise

    async def list(self) -> list[SecretEntry]:
        client = _client(self._addr, self._token)
        entries: list[SecretEntry] = []
        try:
            resp = client.secrets.kv.v2.list_secrets(
                path=self._prefix or "",
                mount_point=self._mount,
            )
            keys: list[str] = resp["data"]["keys"]
        except Exception as exc:
            if "InvalidPath" in type(exc).__name__ or "404" in str(exc):
                return []  # no secrets yet
            raise

        for key in keys:
            if key.endswith("/"):
                continue  # skip sub-directories
            logical = key.removeprefix(self._prefix) if self._prefix else key
            entries.append(
                SecretEntry(
                    name=logical,
                    masked_value="••••(vault)",
                    backend="vault",
                    created_at=datetime.now(tz=UTC),
                    updated_at=None,
                )
            )
        return entries
