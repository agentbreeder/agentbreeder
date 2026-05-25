"""Azure Key Vault secrets backend.

Requires: pip install azure-keyvault-secrets azure-identity
Optional env vars:
    AZURE_KEYVAULT_URL — URL of the Azure Key Vault (e.g. https://myvault.vault.azure.net/)
"""

from __future__ import annotations

import logging
import os
from datetime import UTC
from typing import Any

from engine.secrets.base import SecretEntry, SecretsBackend

logger = logging.getLogger(__name__)

_AZURE_IMPORT_ERROR = (
    "Azure Key Vault backend requires azure-keyvault-secrets and azure-identity. "
    "Install it with: pip install azure-keyvault-secrets azure-identity"
)


def sanitize_secret_name(name: str) -> str:
    """Convert a logical name to an Azure Key Vault-legal secret name.

    Key Vault secret names allow only alphanumerics and dashes (max 127 chars).
    Underscores and slashes become dashes; any other character is dropped. The
    transform is idempotent — applying it to an already-legal name is a no-op —
    so the mirror's write name and the deployer's reference name always agree.
    """
    dashed = name.replace("_", "-").replace("/", "-")
    chars = [c for c in dashed if c.isalnum() or c == "-"]
    return "".join(chars)[:127]


def _client(vault_url: str) -> Any:
    """Create an Azure Key Vault SecretClient."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        return SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
    except ImportError as exc:
        raise ImportError(_AZURE_IMPORT_ERROR) from exc


class AzureKeyVaultBackend(SecretsBackend):
    """Secrets stored in Azure Key Vault.

    Azure Key Vault secret names can only contain alphanumeric characters and hyphens.
    Logical names with underscores or slashes (e.g., "agentbreeder/my-agent/MY_SECRET")
    are converted to dashes.
    """

    def __init__(
        self,
        vault_url: str | None = None,
        prefix: str = "agentbreeder-",
    ) -> None:
        self._vault_url = vault_url or os.environ.get("AZURE_KEYVAULT_URL", "")
        if not self._vault_url:
            raise ValueError(
                "Azure Key Vault URL is required. Pass vault_url= or set AZURE_KEYVAULT_URL."
            )
        self._prefix = prefix

    @property
    def backend_name(self) -> str:
        return "azure"

    def _secret_id(self, name: str) -> str:
        """Convert logical name to Azure-compatible secret name (alphanumeric and dashes)."""
        return sanitize_secret_name(f"{self._prefix}{name}" if self._prefix else name)

    async def get(self, name: str) -> str | None:
        client = _client(self._vault_url)
        try:
            secret = client.get_secret(self._secret_id(name))
            value: str | None = secret.value
            return value
        except Exception as exc:
            # Handle ResourceNotFoundError
            if "ResourceNotFoundError" in type(exc).__name__ or "not found" in str(exc).lower():
                return None
            logger.error("Failed to get secret '%s' from Azure Key Vault: %s", name, exc)
            raise

    async def set(self, name: str, value: str, *, tags: dict[str, str] | None = None) -> None:
        client = _client(self._vault_url)
        secret_id = self._secret_id(name)
        azure_tags = {k.lower(): v.lower() for k, v in (tags or {}).items()}

        try:
            client.set_secret(secret_id, value, tags=azure_tags)
            logger.info("Created or updated secret '%s' in Azure Key Vault", secret_id)
        except Exception as exc:
            logger.error("Failed to set secret '%s' in Azure Key Vault: %s", secret_id, exc)
            raise

    async def delete(self, name: str) -> None:
        client = _client(self._vault_url)
        secret_id = self._secret_id(name)
        try:
            client.begin_delete_secret(secret_id)
            logger.info("Scheduled deletion of secret '%s' from Azure Key Vault", secret_id)
        except Exception as exc:
            if "ResourceNotFoundError" in type(exc).__name__ or "not found" in str(exc).lower():
                raise KeyError(f"Secret '{name}' not found in Azure Key Vault") from exc
            raise

    async def list(self) -> list[SecretEntry]:
        client = _client(self._vault_url)
        entries: list[SecretEntry] = []
        try:
            for secret_properties in client.list_properties_of_secrets():
                raw_name = secret_properties.name
                if self._prefix and not raw_name.startswith(self._prefix):
                    continue
                logical = raw_name.removeprefix(self._prefix) if self._prefix else raw_name
                created = secret_properties.created_on
                updated = secret_properties.updated_on
                entries.append(
                    SecretEntry(
                        name=logical,
                        masked_value="••••(azure)",
                        backend="azure",
                        created_at=created.astimezone(UTC) if created else None,
                        updated_at=updated.astimezone(UTC) if updated else None,
                        tags=dict(secret_properties.tags or {}),
                    )
                )
        except Exception as exc:
            logger.error("Failed to list secrets from Azure Key Vault: %s", exc)
            raise
        return entries
