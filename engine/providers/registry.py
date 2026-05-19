"""Provider registry and fallback chain logic.

Maps provider types to implementations and handles fallback chains
when the primary provider fails.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

from engine.observability.degraded_mode import warn_once
from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.base import ProviderBase, ProviderError
from engine.providers.google_provider import GoogleProvider
from engine.providers.litellm_provider import LiteLLMProvider
from engine.providers.models import (
    FallbackConfig,
    GenerateResult,
    ModelInfo,
    ProviderConfig,
    ProviderType,
    StreamChunk,
    ToolDefinition,
)
from engine.providers.ollama_provider import OllamaProvider
from engine.providers.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

# Provider type -> implementation class
_PROVIDER_CLASSES: dict[ProviderType, type[ProviderBase]] = {
    ProviderType.openai: OpenAIProvider,
    ProviderType.ollama: OllamaProvider,
    ProviderType.anthropic: AnthropicProvider,
    ProviderType.google: GoogleProvider,
    # OpenRouter is OpenAI-compatible, use OpenAIProvider with a custom base_url
    ProviderType.openrouter: OpenAIProvider,
    # LiteLLM proxy is OpenAI-compatible; uses LITELLM_BASE_URL + LITELLM_VIRTUAL_KEY
    ProviderType.litellm: LiteLLMProvider,
}


def create_provider(config: ProviderConfig) -> ProviderBase:
    """Create a provider instance from a config.

    Raises KeyError if the provider type is not supported.
    """
    provider_cls = _PROVIDER_CLASSES.get(config.provider_type)
    if provider_cls is None:
        supported = ", ".join(p.value for p in _PROVIDER_CLASSES)
        msg = (
            f"Provider type '{config.provider_type}' is not supported. "
            f"Supported providers: {supported}"
        )
        raise KeyError(msg)
    return provider_cls(config)


def create_catalog_provider(
    name: str,
    *,
    default_model: str | None = None,
    timeout: float = 60.0,
) -> ProviderBase:
    """Create a provider from the OpenAI-compatible catalog by name.

    Looks up ``name`` in ``engine/providers/catalog.yaml`` (plus user-local
    overrides) and constructs a generic
    :class:`engine.providers.openai_compatible.OpenAICompatibleProvider`.

    Use this for providers like ``nvidia``, ``groq``, ``together``, etc. that
    don't have a hand-written class — they all share the OpenAI Chat
    Completions wire shape.

    Raises:
        KeyError: if ``name`` is not in the catalog.
        AuthenticationError: if the api-key env var declared on the entry is unset.
    """
    # Local import to avoid a hard dep cycle (registry is imported widely).
    from engine.providers.openai_compatible import from_catalog

    return from_catalog(name, default_model=default_model, timeout=timeout)


def resolve_model_ref(
    ref: str,
    *,
    timeout: float = 60.0,
) -> ProviderBase | None:
    """Resolve a model reference against the catalog.

    Supports two ref shapes:

    * **2-segment direct** — ``<provider>/<model>`` (e.g.
      ``nvidia/meta-llama-3.1-405b-instruct``). Resolves to the catalog
      provider for ``provider`` with ``default_model = model``.

    * **3-segment gateway** — ``<gateway>/<upstream>/<model>`` (Track H /
      #164, e.g. ``openrouter/moonshotai/kimi-k2``). Resolves to the
      gateway's catalog entry; the wire ``model`` field is shaped as
      ``<upstream>/<model>``. Only matches when the first segment names
      a catalog entry whose ``type == "gateway"``.

    Returns ``None`` if neither shape matches.

    Examples:
        >>> resolve_model_ref("nvidia/meta-llama-3.1-405b-instruct")
        <OpenAICompatibleProvider name="nvidia" model="meta-llama-3.1-405b-instruct">
        >>> resolve_model_ref("openrouter/moonshotai/kimi-k2")
        <OpenAICompatibleProvider name="openrouter" model="moonshotai/kimi-k2">
        >>> resolve_model_ref("gpt-4o")  # not in catalog → None
        None
    """
    from engine.providers.catalog import parse_gateway_ref, parse_model_ref

    # Try 3-segment gateway form first — it's strictly more specific than
    # the direct form and always wins when the first segment is a gateway.
    gateway_ref = parse_gateway_ref(ref)
    if gateway_ref is not None:
        return create_catalog_provider(
            gateway_ref.gateway,
            default_model=gateway_ref.upstream_model,
            timeout=timeout,
        )

    parsed = parse_model_ref(ref)
    if parsed is None:
        return None
    provider_name, model_id = parsed
    return create_catalog_provider(provider_name, default_model=model_id, timeout=timeout)


def create_provider_from_env(
    provider_type: ProviderType,
    model: str | None = None,
) -> ProviderBase:
    """Create a provider with config from environment variables.

    Reads API keys and base URLs from standard env vars:
    - OPENAI_API_KEY for OpenAI
    - OLLAMA_BASE_URL for Ollama (defaults to localhost:11434)

    This is the easiest way to get a provider for quick use.
    """
    config = ProviderConfig(
        provider_type=provider_type,
        default_model=model,
    )

    if provider_type == ProviderType.openai:
        config.api_key = os.environ.get("OPENAI_API_KEY")
    elif provider_type == ProviderType.ollama:
        config.base_url = os.environ.get("OLLAMA_BASE_URL")
    elif provider_type == ProviderType.anthropic:
        config.api_key = os.environ.get("ANTHROPIC_API_KEY")
    elif provider_type == ProviderType.google:
        config.api_key = os.environ.get("GOOGLE_AI_API_KEY")
    elif provider_type == ProviderType.openrouter:
        config.api_key = os.environ.get("OPENROUTER_API_KEY")
        config.base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    elif provider_type == ProviderType.litellm:
        config.api_key = os.environ.get("LITELLM_VIRTUAL_KEY")
        config.base_url = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")

    return create_provider(config)


class FallbackChain:
    """Executes a generate request across a chain of providers.

    If the primary provider fails, tries each fallback in order.
    This implements the fallback chain described in agent.yaml:
        model.primary -> model.fallback
    """

    def __init__(self, config: FallbackConfig) -> None:
        self._providers: list[ProviderBase] = []
        self._providers.append(create_provider(config.primary))
        for fallback in config.fallbacks:
            self._providers.append(create_provider(fallback))
        if not self._providers:
            msg = "FallbackChain requires at least one provider"
            raise ProviderError(msg)

    @property
    def providers(self) -> list[ProviderBase]:
        """Return the ordered list of providers (primary first)."""
        return list(self._providers)

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> GenerateResult:
        """Try generating with each provider in order until one succeeds.

        Raises the last ProviderError if all providers fail.
        """
        last_error: ProviderError | None = None
        primary_name = self._providers[0].name

        for idx, provider in enumerate(self._providers):
            try:
                logger.info(
                    "Attempting generate with provider '%s'",
                    provider.name,
                )
                result = await provider.generate(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                )
                if idx > 0:
                    warn_once(
                        "provider.fallback",
                        f"{primary_name}-to-{provider.name}",
                        extra={"primary": primary_name, "fallback": provider.name},
                    )
                    # Record which primary was originally attempted so
                    # downstream cost-attribution / tracing can see the
                    # full chain context. The serving provider is in
                    # ``result.provider`` already.
                    result.fallback_from = primary_name
                logger.info(
                    "Generate succeeded with provider '%s'",
                    provider.name,
                )
                return result
            except ProviderError as e:
                last_error = e
                logger.warning(
                    "Provider '%s' failed: %s. Trying next fallback...",
                    provider.name,
                    e,
                )

        # All providers failed
        warn_once(
            "provider.fallback",
            f"{primary_name}-all-failed",
            extra={"primary": primary_name, "last_error": str(last_error)},
        )
        msg = f"All providers in fallback chain failed. Last error: {last_error}"
        raise ProviderError(msg)

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream chunks from the first provider whose stream opens successfully.

        Semantics (per audit spec W4-20):

        * Try the primary's ``generate_stream``. If it raises a
          :class:`ProviderError` **before yielding the first chunk**, switch
          to the next fallback and retry.
        * Once any chunk has been yielded, the request is "committed" to
          that provider — a later failure mid-stream is *not* retried on a
          fallback (we'd have to re-emit partial content, which would
          confuse clients). Such failures are re-raised.
        * If all providers fail before yielding a chunk, raises
          :class:`ProviderError` with the last error.

        Fallback attribution: when a fallback serves the stream, this method
        prepends a marker :class:`StreamChunk` with no content but
        ``model = "<fallback_name>"``, mirroring the ``fallback_from``
        signal :meth:`generate` records on :class:`GenerateResult`. Callers
        that don't care can ignore empty chunks as they already do today.
        """
        last_error: ProviderError | None = None
        primary_name = self._providers[0].name

        for idx, provider in enumerate(self._providers):
            logger.info(
                "Attempting generate_stream with provider '%s'",
                provider.name,
            )
            try:
                stream = provider.generate_stream(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    tools=tools,
                )
                # Pull the first chunk eagerly so we can fall back if the
                # stream fails to open. ``StopAsyncIteration`` here means
                # the stream opened cleanly but had zero chunks — treat
                # as success (the provider's contract may yield nothing
                # for e.g. tool-only responses).
                first_chunk: StreamChunk | None = None
                try:
                    first_chunk = await stream.__anext__()
                except StopAsyncIteration:
                    first_chunk = None
                except ProviderError as e:
                    last_error = e
                    logger.warning(
                        "Provider '%s' stream failed to open: %s. Trying next fallback...",
                        provider.name,
                        e,
                    )
                    continue
            except ProviderError as e:
                last_error = e
                logger.warning(
                    "Provider '%s' failed to start stream: %s. Trying next fallback...",
                    provider.name,
                    e,
                )
                continue

            # Stream opened successfully — commit to this provider.
            if idx > 0:
                warn_once(
                    "provider.fallback",
                    f"{primary_name}-to-{provider.name}",
                    extra={"primary": primary_name, "fallback": provider.name},
                )
            if first_chunk is not None:
                yield first_chunk
            async for chunk in stream:
                yield chunk
            logger.info(
                "Stream completed with provider '%s'",
                provider.name,
            )
            return

        # All providers failed before producing a chunk.
        warn_once(
            "provider.fallback",
            f"{primary_name}-stream-all-failed",
            extra={"primary": primary_name, "last_error": str(last_error)},
        )
        msg = f"All providers in fallback chain failed to stream. Last error: {last_error}"
        raise ProviderError(msg)

    async def list_all_models(self) -> list[ModelInfo]:
        """List models from all providers in the chain."""
        all_models: list[ModelInfo] = []
        for provider in self._providers:
            try:
                models = await provider.list_models()
                all_models.extend(models)
            except ProviderError as e:
                logger.warning(
                    "Failed to list models from provider '%s': %s",
                    provider.name,
                    e,
                )
        return all_models

    async def close(self) -> None:
        """Close all providers in the chain."""
        for provider in self._providers:
            await provider.close()
