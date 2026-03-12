"""Pydantic models for the provider abstraction layer.

These models define the data contracts for LLM provider interactions:
generate requests/responses, model info, provider config, and tool definitions.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field


class ProviderType(enum.StrEnum):
    """Supported LLM provider types."""

    openai = "openai"
    ollama = "ollama"


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
    """Result of a generate() call."""

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    usage: UsageInfo = Field(default_factory=UsageInfo)
    model: str = ""
    provider: str = ""


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


class FallbackConfig(BaseModel):
    """Configuration for a fallback chain."""

    primary: ProviderConfig
    fallbacks: list[ProviderConfig] = Field(default_factory=list)
