"""Anthropic provider — direct API calls via httpx.

Supports chat completions, streaming, and tool use using the
Anthropic Messages REST API. No anthropic SDK dependency — uses httpx directly
for a lighter footprint and consistent error handling.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from engine.providers.base import (
    AuthenticationError,
    ModelNotFoundError,
    ProviderBase,
    ProviderError,
    RateLimitError,
)
from engine.providers.models import (
    GenerateResult,
    ModelInfo,
    ProviderConfig,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    UsageInfo,
)

logger = logging.getLogger(__name__)

ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"

# Models known to support tool use
_TOOL_CAPABLE_PREFIXES = (
    "claude-3",
    "claude-sonnet",
    "claude-haiku",
    "claude-opus",
    "claude-4",
)

# Finish reason mapping: Anthropic -> OpenAI-compatible
_FINISH_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}


class AnthropicProvider(ProviderBase):
    """Anthropic Messages API provider using httpx."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        api_key = config.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            msg = (
                "Anthropic API key not found. Set ANTHROPIC_API_KEY environment variable "
                "or pass api_key in ProviderConfig."
            )
            raise AuthenticationError(msg)
        self._api_key = api_key
        self._base_url = (config.base_url or ANTHROPIC_API_BASE).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(config.timeout),
        )

    @property
    def name(self) -> str:
        return "anthropic"

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
        stream: bool = False,
    ) -> GenerateResult:
        resolved_model = self._resolve_model(model) or DEFAULT_MODEL

        if stream:
            return await self._collect_stream(
                messages, resolved_model, temperature, max_tokens, tools
            )

        payload = self._build_payload(messages, resolved_model, temperature, max_tokens, tools)
        response = await self._request("POST", "/messages", payload)
        return self._parse_response(response)

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        resolved_model = self._resolve_model(model) or DEFAULT_MODEL
        payload = self._build_payload(messages, resolved_model, temperature, max_tokens, tools)
        payload["stream"] = True

        async with self._client.stream("POST", "/messages", json=payload) as resp:
            self._check_status(resp.status_code, "")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event_data = json.loads(data)
                    chunk = self._parse_stream_event(event_data)
                    if chunk is not None:
                        yield chunk
                except json.JSONDecodeError:
                    logger.warning("Failed to parse Anthropic stream event: %s", data)

    async def list_models(self) -> list[ModelInfo]:
        response = await self._request("GET", "/models")
        models: list[ModelInfo] = []
        for m in response.get("data", []):
            model_id = m.get("id", "")
            models.append(
                ModelInfo(
                    id=model_id,
                    name=m.get("display_name", model_id),
                    provider="anthropic",
                    supports_tools=self._model_supports_tools(model_id),
                    supports_streaming=True,
                )
            )
        return sorted(models, key=lambda m: m.id)

    async def health_check(self) -> bool:
        try:
            await self._request("GET", "/models")
            return True
        except ProviderError:
            return False

    async def close(self) -> None:
        await self._client.aclose()

    # ── Internal helpers ─────────────────────────────────────────────────

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        tools: list[ToolDefinition] | None,
    ) -> dict[str, Any]:
        # Split system message out; Anthropic puts it in a top-level field
        system_content: str | None = None
        non_system: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_content = msg.get("content", "")
            else:
                role = msg.get("role", "user")
                # Anthropic uses "assistant" same as OpenAI; "user" → "user"
                non_system.append({"role": role, "content": msg.get("content", "")})

        payload: dict[str, Any] = {
            "model": model,
            "messages": non_system,
            # Anthropic requires max_tokens — default to 1024
            "max_tokens": max_tokens or 1024,
        }
        if system_content:
            payload["system"] = system_content
        if temperature is not None:
            payload["temperature"] = temperature
        if tools:
            payload["tools"] = [self._convert_tool(t) for t in tools]
        return payload

    @staticmethod
    def _convert_tool(tool: ToolDefinition) -> dict[str, Any]:
        """Convert from OpenAI ToolDefinition format to Anthropic format."""
        return {
            "name": tool.function.name,
            "description": tool.function.description,
            "input_schema": tool.function.parameters or {"type": "object", "properties": {}},
        }

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        """Make an HTTP request and handle errors."""
        try:
            if method == "GET":
                resp = await self._client.get(path)
            else:
                resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as e:
            msg = f"Anthropic request timed out: {e}"
            raise ProviderError(msg) from e
        except httpx.ConnectError as e:
            msg = f"Failed to connect to Anthropic API at {self._base_url}: {e}"
            raise ProviderError(msg) from e

        body = resp.text
        self._check_status(resp.status_code, body)
        return resp.json()

    def _check_status(self, status_code: int, body: str) -> None:
        if status_code == 200:
            return
        if status_code == 401:
            raise AuthenticationError("Invalid Anthropic API key")
        if status_code == 404:
            raise ModelNotFoundError(f"Anthropic model not found: {body}")
        if status_code == 429:
            raise RateLimitError(f"Anthropic rate limit exceeded: {body}")
        if status_code >= 400:
            msg = f"Anthropic API error ({status_code}): {body}"
            raise ProviderError(msg)

    def _parse_response(self, data: dict[str, Any]) -> GenerateResult:
        content_blocks = data.get("content", [])
        usage_data = data.get("usage", {})

        text_content: str | None = None
        tool_calls: list[ToolCall] = []

        for block in content_blocks:
            block_type = block.get("type")
            if block_type == "text":
                text_content = block.get("text")
            elif block_type == "tool_use":
                # Anthropic tool use block: input is already a dict
                arguments = block.get("input", {})
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        function_name=block.get("name", ""),
                        function_arguments=json.dumps(arguments),
                    )
                )

        raw_finish = data.get("stop_reason", "end_turn")
        finish_reason = _FINISH_REASON_MAP.get(raw_finish, raw_finish)

        return GenerateResult(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=UsageInfo(
                prompt_tokens=usage_data.get("input_tokens", 0),
                completion_tokens=usage_data.get("output_tokens", 0),
                total_tokens=usage_data.get("input_tokens", 0)
                + usage_data.get("output_tokens", 0),
            ),
            model=data.get("model", ""),
            provider="anthropic",
        )

    @staticmethod
    def _parse_stream_event(data: dict[str, Any]) -> StreamChunk | None:
        """Parse a single SSE event from Anthropic streaming."""
        event_type = data.get("type")

        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                return StreamChunk(content=delta.get("text"), model="")
        elif event_type == "message_delta":
            delta = data.get("delta", {})
            raw_stop = delta.get("stop_reason")
            if raw_stop:
                finish_reason = _FINISH_REASON_MAP.get(raw_stop, raw_stop)
                return StreamChunk(finish_reason=finish_reason, model="")
        elif event_type == "message_start":
            # Extract model from message_start
            message = data.get("message", {})
            model_id = message.get("model", "")
            if model_id:
                return StreamChunk(model=model_id)

        return None

    async def _collect_stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float | None,
        max_tokens: int | None,
        tools: list[ToolDefinition] | None,
    ) -> GenerateResult:
        """Collect a stream into a single GenerateResult."""
        content_parts: list[str] = []
        all_tool_calls: list[ToolCall] = []
        finish_reason = "stop"
        result_model = model

        async for chunk in self.generate_stream(messages, model, temperature, max_tokens, tools):
            if chunk.content:
                content_parts.append(chunk.content)
            if chunk.tool_calls:
                all_tool_calls.extend(chunk.tool_calls)
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.model:
                result_model = chunk.model

        return GenerateResult(
            content="".join(content_parts) if content_parts else None,
            tool_calls=all_tool_calls,
            finish_reason=finish_reason,
            model=result_model,
            provider="anthropic",
        )

    @staticmethod
    def _model_supports_tools(model_id: str) -> bool:
        """Heuristic: Claude 3+ and named variants support tool use."""
        return any(model_id.startswith(p) for p in _TOOL_CAPABLE_PREFIXES)
