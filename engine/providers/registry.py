"""Provider registry and fallback chain logic.

Maps provider types to implementations and handles fallback chains
when the primary provider fails.
"""

from __future__ import annotations

import logging
import os

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

        for provider in self._providers:
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
        msg = f"All providers in fallback chain failed. Last error: {last_error}"
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
