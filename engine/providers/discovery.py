"""Per-provider model discovery — Track G (#163).

Each :class:`ProviderDiscovery` adapter knows how to fetch the live model
list for one provider family. The output is a list of
:class:`DiscoveredModel` records that the lifecycle service then merges into
the registry, deriving status (active/beta/deprecated/retired) from the
diff between the previous sync and the current one.

Three adapters ship with this module:

* :class:`OpenAICompatibleDiscovery` — handles every OpenAI-compatible endpoint
  (OpenAI, Nvidia, Groq, Together, Fireworks, OpenRouter, …). Calls
  ``GET <base_url>/models`` and parses ``{data: [{id, ...}]}``.
* :class:`AnthropicDiscovery` — Anthropic does not expose a public ``/models``
  endpoint, so we ship a curated, hard-coded list and stamp it with
  ``source="anthropic-curated"``. Updated as Anthropic releases new models.
* :class:`GoogleDiscovery` — calls
  ``GET https://generativelanguage.googleapis.com/v1beta/models?key=<API_KEY>``
  and converts ``{models: [{name, displayName, ...}]}`` into discovered
  models.

The factory :func:`get_discovery` returns the right adapter for a provider
type / catalog entry. All HTTP I/O is async and uses ``httpx.AsyncClient``;
all tests mock external HTTP.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from engine.providers.catalog import CatalogEntry, get_entry

logger = logging.getLogger(__name__)


# ─── Public data shape ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class DiscoveredModel:
    """A single model record returned by a provider discovery adapter.

    These are *raw* — the lifecycle service is what derives status, deprecation
    pointers, and writes them to the registry.
    """

    id: str
    """Provider-native model id (e.g. ``"gpt-4o"``, ``"claude-sonnet-4-5"``)."""

    name: str
    """Display name. Falls back to ``id`` if the upstream omits a friendly name."""

    provider: str
    """Provider name (e.g. ``"openai"``, ``"anthropic"``, ``"nvidia"``)."""

    context_window: int | None = None
    max_output_tokens: int | None = None
    capabilities: tuple[str, ...] = ()
    """Free-form capability tags (e.g. ``"streaming"``, ``"tools"``, ``"vision"``)."""

    raw: dict[str, Any] = field(default_factory=dict)
    """Original upstream payload, kept for debugging + future extraction."""


# ─── Discovery interface ───────────────────────────────────────────────────


@runtime_checkable
class ProviderDiscovery(Protocol):
    """Per-provider ``/models`` fetcher contract.

    Discovery adapters must be safe to run repeatedly (idempotent) and must
    raise :class:`DiscoveryError` on transport / auth failures so the
    lifecycle service can record a per-provider error in the sync report
    instead of aborting the whole sync.
    """

    provider_name: str

    async def list_models(self) -> list[DiscoveredModel]:
        """Return all models currently exposed by this provider.

        Implementations MUST close any HTTP clients they open.
        """
        ...


class DiscoveryError(Exception):
    """Raised by adapters when the upstream call fails."""


# ─── OpenAI-compatible adapter ─────────────────────────────────────────────


class OpenAICompatibleDiscovery:
    """Discovery adapter for any provider that speaks OpenAI's ``GET /models``.

    Handles plain OpenAI plus every entry in the catalog (Nvidia, Groq,
    Together, OpenRouter, …). The OpenAI list endpoint shape is::

        { "data": [ {"id": "gpt-4o", "object": "model", ...}, ... ] }

    OpenRouter additionally returns ``context_length`` per model and a
    free-form capability hint, which we surface when present.
    """

    def __init__(
        self,
        provider_name: str,
        base_url: str,
        api_key: str | None,
        *,
        default_headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not base_url:
            msg = f"OpenAICompatibleDiscovery requires base_url (provider={provider_name!r})"
            raise DiscoveryError(msg)
        self.provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._default_headers = dict(default_headers or {})
        self._timeout = timeout

    async def list_models(self) -> list[DiscoveredModel]:
        headers = {"Content-Type": "application/json", **self._default_headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(self._timeout),
        ) as client:
            try:
                resp = await client.get("/models")
            except httpx.HTTPError as exc:
                msg = f"{self.provider_name} discovery transport error: {exc}"
                raise DiscoveryError(msg) from exc
        if resp.status_code == 401:
            msg = f"{self.provider_name} discovery: invalid api-key"
            raise DiscoveryError(msg)
        if resp.status_code >= 400:
            msg = f"{self.provider_name} discovery: HTTP {resp.status_code} — {resp.text[:200]}"
            raise DiscoveryError(msg)

        try:
            payload = resp.json()
        except ValueError as exc:
            msg = f"{self.provider_name} discovery: non-JSON response"
            raise DiscoveryError(msg) from exc

        out: list[DiscoveredModel] = []
        for entry in payload.get("data", []):
            model_id = entry.get("id")
            if not model_id:
                continue
            ctx = entry.get("context_length") or entry.get("context_window")
            capabilities: list[str] = ["streaming"]
            # Most OpenAI-compatible providers support tools — we surface the
            # capability so the registry can show a badge. Conservative.
            capabilities.append("tools")
            if entry.get("supports_vision"):
                capabilities.append("vision")
            out.append(
                DiscoveredModel(
                    id=str(model_id),
                    name=str(entry.get("display_name") or entry.get("name") or model_id),
                    provider=self.provider_name,
                    context_window=int(ctx) if isinstance(ctx, int | float) else None,
                    max_output_tokens=entry.get("max_completion_tokens"),
                    capabilities=tuple(capabilities),
                    raw=entry,
                )
            )
        out.sort(key=lambda m: m.id)
        return out


# ─── Anthropic adapter ─────────────────────────────────────────────────────


# Anthropic does not expose a public ``/models`` endpoint, so we keep a
# curated list. Update when Anthropic ships new models.
ANTHROPIC_CURATED_MODELS: tuple[dict[str, Any], ...] = (
    {
        "id": "claude-opus-4-5",
        "name": "Claude Opus 4.5",
        "context_window": 200_000,
        "max_output_tokens": 16_000,
        "capabilities": ("streaming", "tools", "vision", "thinking", "prompt_caching"),
    },
    {
        "id": "claude-sonnet-4-7",
        "name": "Claude Sonnet 4.7",
        "context_window": 1_000_000,
        "max_output_tokens": 64_000,
        "capabilities": ("streaming", "tools", "vision", "thinking", "prompt_caching"),
    },
    {
        "id": "claude-sonnet-4-5",
        "name": "Claude Sonnet 4.5",
        "context_window": 200_000,
        "max_output_tokens": 8_192,
        "capabilities": ("streaming", "tools", "vision", "prompt_caching"),
    },
    {
        "id": "claude-haiku-4-5",
        "name": "Claude Haiku 4.5",
        "context_window": 200_000,
        "max_output_tokens": 8_192,
        "capabilities": ("streaming", "tools", "vision"),
    },
    {
        "id": "claude-3-5-sonnet-20241022",
        "name": "Claude 3.5 Sonnet (legacy)",
        "context_window": 200_000,
        "max_output_tokens": 8_192,
        "capabilities": ("streaming", "tools", "vision"),
    },
)


class AnthropicDiscovery:
    """Curated Anthropic discovery — no upstream ``/models`` endpoint exists.

    The list is hard-coded and stamped with ``source="anthropic-curated"`` by
    the lifecycle service. Update :data:`ANTHROPIC_CURATED_MODELS` when new
    models ship.
    """

    provider_name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        models: tuple[dict[str, Any], ...] = ANTHROPIC_CURATED_MODELS,
    ) -> None:
        # api_key is accepted for symmetry — we don't *need* it because we
        # don't hit Anthropic at all, but the CLI / API pass one through.
        self._api_key = api_key
        self._models = models

    async def list_models(self) -> list[DiscoveredModel]:
        out = [
            DiscoveredModel(
                id=str(m["id"]),
                name=str(m.get("name") or m["id"]),
                provider=self.provider_name,
                context_window=m.get("context_window"),
                max_output_tokens=m.get("max_output_tokens"),
                capabilities=tuple(m.get("capabilities") or ()),
                raw=dict(m),
            )
            for m in self._models
        ]
        out.sort(key=lambda x: x.id)
        return out


# ─── Google adapter ────────────────────────────────────────────────────────


GOOGLE_DISCOVERY_BASE = "https://generativelanguage.googleapis.com/v1beta"


class GoogleDiscovery:
    """Google Generative Language ``/v1beta/models`` adapter.

    Auth is passed as a ``key=<API_KEY>`` query param (not a bearer header).
    Returns models in shape ``{models: [{name, displayName, inputTokenLimit,
    outputTokenLimit, supportedGenerationMethods, ...}, ...]}``.
    """

    provider_name = "google"

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = GOOGLE_DISCOVERY_BASE,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            msg = "Google discovery requires GOOGLE_API_KEY"
            raise DiscoveryError(msg)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def list_models(self) -> list[DiscoveredModel]:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=httpx.Timeout(self._timeout)
        ) as client:
            try:
                resp = await client.get("/models", params={"key": self._api_key})
            except httpx.HTTPError as exc:
                msg = f"google discovery transport error: {exc}"
                raise DiscoveryError(msg) from exc
        if resp.status_code in (401, 403):
            msg = "google discovery: invalid api-key"
            raise DiscoveryError(msg)
        if resp.status_code >= 400:
            msg = f"google discovery: HTTP {resp.status_code} — {resp.text[:200]}"
            raise DiscoveryError(msg)
        try:
            payload = resp.json()
        except ValueError as exc:
            msg = "google discovery: non-JSON response"
            raise DiscoveryError(msg) from exc

        out: list[DiscoveredModel] = []
        for entry in payload.get("models", []):
            raw_name = entry.get("name") or ""
            # name is "models/<id>"
            model_id = raw_name.split("/", 1)[-1] if "/" in raw_name else raw_name
            if not model_id:
                continue
            methods = set(entry.get("supportedGenerationMethods") or [])
            capabilities: list[str] = []
            if "streamGenerateContent" in methods:
                capabilities.append("streaming")
            if "generateContent" in methods:
                capabilities.append("generate")
            out.append(
                DiscoveredModel(
                    id=model_id,
                    name=str(entry.get("displayName") or model_id),
                    provider=self.provider_name,
                    context_window=entry.get("inputTokenLimit"),
                    max_output_tokens=entry.get("outputTokenLimit"),
                    capabilities=tuple(capabilities),
                    raw=entry,
                )
            )
        out.sort(key=lambda x: x.id)
        return out


# ─── Factory ───────────────────────────────────────────────────────────────


def get_discovery(
    provider_name: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    catalog_entry: CatalogEntry | None = None,
) -> ProviderDiscovery:
    """Build a discovery adapter for a provider.

    Resolution rules:

    * ``"anthropic"`` → :class:`AnthropicDiscovery` (hard-coded list).
    * ``"google"``    → :class:`GoogleDiscovery` (``GOOGLE_API_KEY`` env var
      if ``api_key`` is not passed).
    * ``"openai"``    → :class:`OpenAICompatibleDiscovery` against
      ``https://api.openai.com/v1`` (``OPENAI_API_KEY`` env var by default).
    * Anything else   → resolve from the catalog (built-in + user-local).
      Falls back to the explicit ``base_url`` if ``catalog_entry`` is also
      None.

    Raises :class:`DiscoveryError` when the provider isn't known.
    """
    name = provider_name.lower()
    if name == "anthropic":
        return AnthropicDiscovery(api_key=api_key)
    if name == "google":
        key = api_key or os.environ.get("GOOGLE_API_KEY")
        return GoogleDiscovery(api_key=key)
    if name == "openai":
        key = api_key or os.environ.get("OPENAI_API_KEY")
        return OpenAICompatibleDiscovery(
            provider_name="openai",
            base_url=base_url or "https://api.openai.com/v1",
            api_key=key,
        )

    # Catalog-driven providers (nvidia, groq, openrouter, …).
    entry = catalog_entry or get_entry(name)
    if entry is None and not base_url:
        msg = f"Discovery: unknown provider '{provider_name}' (not in catalog)"
        raise DiscoveryError(msg)
    if entry is not None:
        key = api_key or os.environ.get(entry.api_key_env)
        return OpenAICompatibleDiscovery(
            provider_name=name,
            base_url=str(entry.base_url),
            api_key=key,
            default_headers=dict(entry.default_headers),
        )
    # Fall-back: explicit base_url only (used by tests + bring-your-own).
    return OpenAICompatibleDiscovery(
        provider_name=name,
        base_url=base_url or "",
        api_key=api_key,
    )


__all__ = [
    "ANTHROPIC_CURATED_MODELS",
    "AnthropicDiscovery",
    "DiscoveredModel",
    "DiscoveryError",
    "GoogleDiscovery",
    "OpenAICompatibleDiscovery",
    "ProviderDiscovery",
    "get_discovery",
]
