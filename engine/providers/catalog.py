"""Provider catalog — load and merge OpenAI-compatible provider presets.

The catalog has two layers:

1. **Built-in presets** — shipped in ``engine/providers/catalog.yaml``. Curated
   list of public OpenAI-compatible providers (Nvidia, Groq, OpenRouter, …).
2. **User-local overrides** — ``~/.agentbreeder/providers.local.yaml``. Lets a
   developer register private/internal providers (self-hosted vLLM, etc.)
   without modifying the package.

User-local entries take precedence over built-in ones with the same name. This
mirrors the way ``kubeconfig`` merges contexts.

TODO(track-a): workspace-scoped catalogs (``<workspace>/providers.yaml``) will
land with Track A. The merge order will then be: built-in < user-local <
workspace.
"""

from __future__ import annotations

import logging
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, HttpUrl, ValidationError

logger = logging.getLogger(__name__)


# ─── Paths ─────────────────────────────────────────────────────────────────

CATALOG_PATH = Path(__file__).parent / "catalog.yaml"
USER_LOCAL_PATH = Path.home() / ".agentbreeder" / "providers.local.yaml"


# ─── Schema ────────────────────────────────────────────────────────────────


CatalogProviderType = Literal["openai_compatible", "gateway"]


class CatalogEntry(BaseModel):
    """A single provider entry in the catalog.

    All built-in entries must declare ``type``, ``base_url``, and ``api_key_env``.
    Anything else (``docs``, ``default_headers``, ``notable_models``) is optional
    metadata used by the dashboard, CLI ``provider list``, and discovery.
    """

    type: CatalogProviderType = "openai_compatible"
    base_url: HttpUrl
    api_key_env: str = Field(min_length=1)
    default_headers: dict[str, str] = Field(default_factory=dict)
    docs: HttpUrl | None = None
    discovery: str | None = None
    notable_models: list[str] = Field(default_factory=list)
    # Source — set by loader, not read from YAML
    source: Literal["builtin", "user-local", "workspace"] = "builtin"


class Catalog(BaseModel):
    """Top-level catalog document."""

    version: int = 1
    providers: dict[str, CatalogEntry] = Field(default_factory=dict)


class CatalogError(Exception):
    """Raised when the catalog YAML is malformed."""


# ─── Loader ────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict[str, object]:
    """Read a YAML file and return its top-level dict, or {} if absent."""
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        msg = f"Failed to parse catalog YAML at {path}: {exc}"
        raise CatalogError(msg) from exc
    if not isinstance(data, dict):
        msg = f"Catalog at {path} must be a YAML mapping, got {type(data).__name__}"
        raise CatalogError(msg)
    return data


def _parse_catalog(data: dict[str, object], source: str) -> Catalog:
    """Parse a raw catalog dict and stamp each entry with its source."""
    try:
        catalog = Catalog.model_validate(data)
    except ValidationError as exc:
        msg = f"Invalid catalog ({source}): {exc}"
        raise CatalogError(msg) from exc
    # Stamp source on every entry
    for entry in catalog.providers.values():
        entry.source = source  # type: ignore[assignment]
    return catalog


@cache
def load_catalog() -> Catalog:
    """Load + merge the built-in catalog with the user-local overrides.

    The result is cached for the process lifetime. Tests should call
    :func:`reset_cache` between assertions if they mutate paths.
    """
    builtin = _parse_catalog(_load_yaml(CATALOG_PATH), source="builtin")

    if USER_LOCAL_PATH.exists():
        try:
            user_local = _parse_catalog(_load_yaml(USER_LOCAL_PATH), source="user-local")
        except CatalogError:
            logger.exception(
                "Skipping user-local catalog at %s due to validation errors",
                USER_LOCAL_PATH,
            )
            user_local = Catalog()
    else:
        user_local = Catalog()

    merged = Catalog(version=builtin.version, providers=dict(builtin.providers))
    for name, entry in user_local.providers.items():
        merged.providers[name] = entry  # user-local wins on name collision
    return merged


def reset_cache() -> None:
    """Clear the catalog cache. Used by tests."""
    load_catalog.cache_clear()


# ─── User-local mutations ──────────────────────────────────────────────────


def write_user_local(catalog: Catalog) -> Path:
    """Persist a catalog to the user-local override file.

    The file is created with ``0600`` permissions (best-effort — Windows ignores).
    """
    USER_LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": catalog.version,
        "providers": {
            name: {
                "type": entry.type,
                "base_url": str(entry.base_url),
                "api_key_env": entry.api_key_env,
                **({"default_headers": entry.default_headers} if entry.default_headers else {}),
                **({"docs": str(entry.docs)} if entry.docs else {}),
                **({"discovery": entry.discovery} if entry.discovery else {}),
                **({"notable_models": entry.notable_models} if entry.notable_models else {}),
            }
            for name, entry in catalog.providers.items()
            if entry.source == "user-local"
        },
    }
    with USER_LOCAL_PATH.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)
    try:
        USER_LOCAL_PATH.chmod(0o600)
    except OSError:
        # Windows or unusual filesystems — ignore.
        logger.debug("Could not chmod %s to 0600", USER_LOCAL_PATH)
    reset_cache()
    return USER_LOCAL_PATH


def load_user_local() -> Catalog:
    """Load just the user-local overrides (does not merge with built-in)."""
    return _parse_catalog(_load_yaml(USER_LOCAL_PATH), source="user-local")


# ─── Public lookup helpers ─────────────────────────────────────────────────


def get_entry(name: str) -> CatalogEntry | None:
    """Return the catalog entry for a provider name, or None if unknown."""
    return load_catalog().providers.get(name)


def list_entries() -> dict[str, CatalogEntry]:
    """Return all catalog entries (built-in + user-local merged)."""
    return dict(load_catalog().providers)


def parse_model_ref(ref: str) -> tuple[str, str] | None:
    """Parse a ``<provider>/<model>`` reference against the catalog.

    Returns ``(provider_name, model_id)`` if ``provider_name`` is in the
    catalog, else ``None``. The model_id may itself contain ``/`` (e.g.
    ``meta/llama-3.1-405b``) — only the first segment is matched as provider.

    Examples:
        >>> parse_model_ref("nvidia/meta-llama-3.1-405b-instruct")
        ("nvidia", "meta-llama-3.1-405b-instruct")
        >>> parse_model_ref("gpt-4o")  # no slash, not catalog
        None
    """
    if "/" not in ref:
        return None
    provider, _, model = ref.partition("/")
    if not provider or not model:
        return None
    if get_entry(provider) is None:
        return None
    return provider, model
