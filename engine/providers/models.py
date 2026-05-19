"""Pydantic models for the provider abstraction layer.

These models define the data contracts for LLM provider interactions:
generate requests/responses, model info, provider config, and tool definitions.
"""

from __future__ import annotations

import enum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class ProviderType(enum.StrEnum):
    """Supported LLM provider types."""

    openai = "openai"
    ollama = "ollama"
    anthropic = "anthropic"
    google = "google"
    openrouter = "openrouter"
    litellm = "litellm"


class ToolFunction(BaseModel):
    """An OpenAI-compatible function/tool definition for function calling."""

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    """A tool definition passed to the model."""

    type: str = "function"
    function: ToolFunction


class ToolCall(BaseModel):
    """A tool call returned by the model."""

    id: str
    type: str = "function"
    function_name: str
    function_arguments: str  # JSON string


class Message(BaseModel):
    """A chat message in OpenAI format."""

    role: str  # system, user, assistant, tool
    content: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None


class UsageInfo(BaseModel):
    """Token usage information for a generation."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class GenerateResult(BaseModel):
    """Result of a generate() call.

    ``fallback_from`` is populated by :class:`FallbackChain` when the
    request was served by a fallback provider rather than the primary.
    It carries the *name* of the originally-attempted (failed) primary
    so cost attribution and tracing can record both the attempted and
    the succeeded provider. ``None`` (the default) means the primary
    served the request directly — no fallback fired.
    """

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    usage: UsageInfo = Field(default_factory=UsageInfo)
    model: str = ""
    provider: str = ""
    fallback_from: str | None = None


class StreamChunk(BaseModel):
    """A single chunk from a streaming response."""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str | None = None
    model: str = ""


class ModelInfo(BaseModel):
    """Information about an available model."""

    id: str
    name: str = ""
    provider: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool = False
    supports_streaming: bool = True
    is_local: bool = False


class ProviderConfig(BaseModel):
    """Configuration for a provider instance."""

    provider_type: ProviderType
    api_key: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    timeout: float = 60.0
    max_retries: int = 2

    @field_validator("default_model")
    @classmethod
    def _default_model_non_empty(cls, v: str | None) -> str | None:
        """``default_model`` may be omitted, but if set it must be a non-empty string."""
        if v is None:
            return v
        if not isinstance(v, str) or not v.strip():
            msg = "default_model must be a non-empty string"
            raise ValueError(msg)
        return v

    @field_validator("api_key")
    @classmethod
    def _api_key_not_whitespace(cls, v: str | None) -> str | None:
        """``api_key`` may be omitted (loaded from env later), but cannot be whitespace-only."""
        if v is None:
            return v
        if not isinstance(v, str) or not v.strip():
            msg = "api_key cannot be empty or whitespace-only"
            raise ValueError(msg)
        return v

    @field_validator("base_url")
    @classmethod
    def _base_url_is_valid(cls, v: str | None) -> str | None:
        """``base_url`` must be a parseable HTTP(S) URL when set.

        We use :func:`urllib.parse.urlparse` instead of ``HttpUrl`` so the
        field stays a plain ``str | None`` for backward compatibility — many
        provider implementations index into ``config.base_url`` as a string.
        """
        if v is None:
            return v
        if not isinstance(v, str) or not v.strip():
            msg = "base_url cannot be empty or whitespace-only"
            raise ValueError(msg)
        parsed = urlparse(v)
        if parsed.scheme not in {"http", "https"}:
            msg = f"base_url must use http:// or https:// scheme, got: {v!r}"
            raise ValueError(msg)
        if not parsed.netloc:
            msg = f"base_url must include a host, got: {v!r}"
            raise ValueError(msg)
        return v


class FallbackConfig(BaseModel):
    """Configuration for a fallback chain."""

    primary: ProviderConfig
    fallbacks: list[ProviderConfig] = Field(default_factory=list)


class ProviderHealth(BaseModel):
    """Structured result of a provider health check.

    Replaces the legacy bare ``bool`` return so callers can distinguish:

    * **reachability failure** (no TCP / DNS / TLS) — usually transient
    * **authentication failure** (401/403) — wrong/missing key
    * **server error** (5xx) — provider-side problem
    * **healthy** — ready to serve traffic

    Backward compatibility: instances are truthy when ``healthy`` is True
    and falsy otherwise, so existing ``if not await provider.health_check()``
    call sites continue to work unchanged.
    """

    healthy: bool
    reason: str | None = None
    error_code: int | None = None

    def __bool__(self) -> bool:  # pragma: no cover — trivial
        return self.healthy
