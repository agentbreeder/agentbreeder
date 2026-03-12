"""Provider abstraction layer for LLM integrations.

Provides a unified generate() interface across multiple LLM providers
(OpenAI, Ollama) with fallback chain support.

Usage:
    from engine.providers import create_provider, ProviderConfig, ProviderType

    config = ProviderConfig(provider_type=ProviderType.openai, api_key="sk-...")
    provider = create_provider(config)
    result = await provider.generate(
        messages=[{"role": "user", "content": "Hello"}],
        model="gpt-4o",
    )
"""

from engine.providers.base import (
    AuthenticationError,
    ModelNotFoundError,
    ProviderBase,
    ProviderError,
    RateLimitError,
)
from engine.providers.models import (
    FallbackConfig,
    GenerateResult,
    Message,
    ModelInfo,
    ProviderConfig,
    ProviderType,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    ToolFunction,
    UsageInfo,
)
from engine.providers.ollama_provider import OllamaProvider
from engine.providers.openai_provider import OpenAIProvider
from engine.providers.registry import FallbackChain, create_provider, create_provider_from_env

__all__ = [
    # Provider classes
    "ProviderBase",
    "OpenAIProvider",
    "OllamaProvider",
    # Registry / factory
    "create_provider",
    "create_provider_from_env",
    "FallbackChain",
    # Models
    "ProviderType",
    "ProviderConfig",
    "FallbackConfig",
    "GenerateResult",
    "StreamChunk",
    "ModelInfo",
    "Message",
    "ToolDefinition",
    "ToolFunction",
    "ToolCall",
    "UsageInfo",
    # Errors
    "ProviderError",
    "AuthenticationError",
    "RateLimitError",
    "ModelNotFoundError",
]
