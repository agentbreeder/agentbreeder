"""Abstract base class for LLM providers.

Every provider (OpenAI, Ollama, etc.) implements this interface.
Provider-specific logic MUST stay inside engine/providers/ -- never leak it elsewhere.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from engine.providers.models import (
    GenerateResult,
    ModelInfo,
    ProviderConfig,
    StreamChunk,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Base exception for provider errors."""


class AuthenticationError(ProviderError):
    """Raised when API key is invalid or missing."""


class RateLimitError(ProviderError):
    """Raised when the provider rate-limits the request."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ModelNotFoundError(ProviderError):
    """Raised when the requested model does not exist."""


class ProviderBase(ABC):
    """Abstract base class for LLM providers.

    Each provider (OpenAI, Ollama) implements this to handle:
    - Generating completions (chat, function calling)
    - Streaming responses
    - Listing available models
    - Health checking the provider endpoint
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider name (e.g., 'openai', 'ollama')."""
        ...

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
        stream: bool = False,
    ) -> GenerateResult:
        """Generate a completion from the model.

        Args:
            messages: Chat messages in OpenAI format.
            model: Model ID to use. Falls back to config.default_model.
            temperature: Sampling temperature (0.0-2.0).
            max_tokens: Maximum tokens to generate.
            tools: Tool/function definitions for function calling.
            stream: If True, use generate_stream() internally and collect the result.

        Returns:
            GenerateResult with the model's response.

        Raises:
            AuthenticationError: If the API key is invalid.
            RateLimitError: If rate-limited by the provider.
            ModelNotFoundError: If the model doesn't exist.
            ProviderError: For other provider errors.
        """
        ...

    @abstractmethod
    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion from the model.

        Yields StreamChunk objects as they arrive.
        """
        ...
        # Make this a valid async generator for type checking
        if False:  # pragma: no cover
            yield StreamChunk()  # type: ignore[misc]

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """List models available from this provider."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider endpoint is reachable and authenticated.

        Returns True if healthy, False otherwise.
        """
        ...

    def _resolve_model(self, model: str | None) -> str:
        """Resolve the model ID, falling back to default_model from config."""
        resolved = model or self.config.default_model
        if not resolved:
            msg = "No model specified and no default_model in provider config"
            raise ProviderError(msg)
        return resolved

    async def close(self) -> None:  # noqa: B027
        """Clean up resources (e.g., close HTTP clients). Override if needed."""
