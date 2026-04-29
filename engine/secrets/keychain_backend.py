"""OS keychain secrets backend (Track K).

Stores secrets in the operating-system credential store via the ``keyring``
Python library:

* macOS         — Keychain
* Linux         — Secret Service (libsecret) or KWallet
* Windows       — Credential Manager

This is the recommended default for single-user local installs because the
secret value never lives unencrypted on disk.

A *workspace* concept is layered on top: each workspace gets its own
namespace inside the keychain, so multiple workspaces on the same machine
do not see each other's secrets.

Requires::

    pip install keyring

The ``keyring`` library has no native async API, so we marshal each call
through ``asyncio.to_thread`` to keep the rest of the engine non-blocking.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from engine.secrets.base import SecretEntry, SecretsBackend, _mask

if TYPE_CHECKING:
    pass  # avoid heavy imports at module load

logger = logging.getLogger(__name__)

_KEYRING_IMPORT_ERROR = (
    "OS keychain backend requires the 'keyring' package. Install it with: pip install keyring"
)

# A separate key under the same service that stores the JSON list of known
# secret names for the workspace. ``keyring`` cannot enumerate entries, so we
# maintain our own index. The index value is never returned from ``get`` /
# ``list`` calls — it has its own dedicated lookup helpers.
_INDEX_KEY = "__agentbreeder_index__"


def _import_keyring() -> Any:
    """Import the keyring library lazily and surface a friendly error.

    Returns:
        The ``keyring`` module.

    Raises:
        ImportError: If ``keyring`` is not installed.
    """
    try:
        import keyring

        return keyring
    except ImportError as exc:  # pragma: no cover - exercised via mocks
        raise ImportError(_KEYRING_IMPORT_ERROR) from exc


class KeychainBackend(SecretsBackend):
    """OS-level keychain backed secrets store.

    Args:
        workspace: Logical workspace name. Used as the keyring *username* and
            also baked into the service name so secrets from different
            workspaces never collide.
        service: Optional override for the keyring service name. Defaults to
            ``agentbreeder``.
    """

    def __init__(self, workspace: str = "default", service: str = "agentbreeder") -> None:
        if not workspace:
            raise ValueError("workspace must be a non-empty string")
        self._workspace = workspace
        self._service = f"{service}:{workspace}"

    @property
    def backend_name(self) -> str:
        return "keychain"

    @property
    def workspace(self) -> str:
        return self._workspace

    # ── index helpers ────────────────────────────────────────────────────

    async def _read_index(self) -> dict[str, dict[str, Any]]:
        """Read the per-workspace index of known secret names.

        Stored as JSON under a sentinel keyring entry. Returns an empty dict
        if no index exists yet.
        """
        keyring = _import_keyring()
        raw = await asyncio.to_thread(keyring.get_password, self._service, _INDEX_KEY)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return cast("dict[str, dict[str, Any]]", data)
        except json.JSONDecodeError:
            logger.warning(
                "Corrupt keychain index for workspace '%s' — resetting", self._workspace
            )
        return {}

    async def _write_index(self, index: dict[str, dict[str, Any]]) -> None:
        keyring = _import_keyring()
        await asyncio.to_thread(keyring.set_password, self._service, _INDEX_KEY, json.dumps(index))

    # ── SecretsBackend interface ─────────────────────────────────────────

    async def get(self, name: str) -> str | None:
        keyring = _import_keyring()
        if name == _INDEX_KEY:
            return None
        value = await asyncio.to_thread(keyring.get_password, self._service, name)
        return cast("str | None", value)

    async def set(self, name: str, value: str, *, tags: dict[str, str] | None = None) -> None:
        if name == _INDEX_KEY:
            raise ValueError(f"'{_INDEX_KEY}' is a reserved keychain index name")
        keyring = _import_keyring()
        await asyncio.to_thread(keyring.set_password, self._service, name, value)
        index = await self._read_index()
        now = datetime.now(tz=UTC).isoformat()
        existing = index.get(name, {})
        index[name] = {
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "tags": tags or existing.get("tags", {}),
        }
        await self._write_index(index)
        logger.info("Stored secret '%s' in OS keychain (workspace=%s)", name, self._workspace)

    async def delete(self, name: str) -> None:
        if name == _INDEX_KEY:
            raise KeyError(f"Secret '{name}' not found in keychain backend")
        keyring = _import_keyring()
        existing = await asyncio.to_thread(keyring.get_password, self._service, name)
        if existing is None:
            raise KeyError(f"Secret '{name}' not found in keychain backend")
        try:
            await asyncio.to_thread(keyring.delete_password, self._service, name)
        except Exception as exc:  # pragma: no cover - keyring backend specific
            # Some keyring backends raise on missing entries even though we
            # already checked existence. Log and continue — the index update
            # is still authoritative.
            logger.warning("keyring delete raised for '%s': %s", name, exc)
        index = await self._read_index()
        index.pop(name, None)
        await self._write_index(index)
        logger.info("Deleted secret '%s' from OS keychain", name)

    async def list(self) -> list[SecretEntry]:
        keyring = _import_keyring()
        index = await self._read_index()
        entries: list[SecretEntry] = []
        for name, meta in sorted(index.items()):
            value = await asyncio.to_thread(keyring.get_password, self._service, name)
            if value is None:
                # Drift: index claims a secret that the keychain no longer has.
                continue
            created = _parse_iso(meta.get("created_at"))
            updated = _parse_iso(meta.get("updated_at"))
            entries.append(
                SecretEntry(
                    name=name,
                    masked_value=_mask(value),
                    backend="keychain",
                    created_at=created,
                    updated_at=updated,
                    tags=cast("dict[str, str]", meta.get("tags") or {}),
                )
            )
        return entries


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
