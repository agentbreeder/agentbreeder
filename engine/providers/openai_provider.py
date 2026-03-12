"""OpenAI provider — direct API calls via httpx.

Supports chat completions, streaming, and function calling using the
OpenAI REST API. No openai SDK dependency — uses httpx directly for
a lighter footprint and consistent error handling.
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

OPENAI_API_BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider(ProviderBase):
    """OpenAI API provider using httpx."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        api_key = config.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            msg = (
                "OpenAI API key not found. Set OPENAI_API_KEY environment variable "
                "or pass api_key in ProviderConfig."
            )
            raise AuthenticationError(msg)
        self._api_key = api_key
        self._base_url = (config.base_url or OPENAI_API_BASE).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(config.timeout),
        )

    @property
    def name(self) -> str:
        return "openai"

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

        payload = self._build_payload(
            messages, resolved_model, temperature, max_tokens, tools, stream=False
        )

        response = await self._request("POST", "/chat/completions", payload)
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
        payload = self._build_payload(
            messages, resolved_model, temperature, max_tokens, tools, stream=True
        )

        async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
            self._check_status(resp.status_code, "")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk_data = json.loads(data)
                    yield self._parse_stream_chunk(chunk_data)
                except json.JSONDecodeError:
                    logger.warning("Failed to parse stream chunk: %s", data)

    async def list_models(self) -> list[ModelInfo]:
        response = await self._request("GET", "/models")
        models: list[ModelInfo] = []
        for m in response.get("data", []):
            model_id = m.get("id", "")
            models.append(
                ModelInfo(
                    id=model_id,
                    name=model_id,
                    provider="openai",
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
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [t.model_dump() for t in tools]
        if stream:
            payload["stream"] = True
        return payload

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        """Make an HTTP request and handle errors."""
        try:
            if method == "GET":
                resp = await self._client.get(path)
            else:
                resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as e:
            msg = f"OpenAI request timed out: {e}"
            raise ProviderError(msg) from e
        except httpx.ConnectError as e:
            msg = f"Failed to connect to OpenAI API at {self._base_url}: {e}"
            raise ProviderError(msg) from e

        body = resp.text
        self._check_status(resp.status_code, body)
        return resp.json()

    def _check_status(self, status_code: int, body: str) -> None:
        if status_code == 200:
            return
        if status_code == 401:
            raise AuthenticationError("Invalid OpenAI API key")
        if status_code == 404:
            raise ModelNotFoundError(f"Model not found: {body}")
        if status_code == 429:
            raise RateLimitError(f"OpenAI rate limit exceeded: {body}")
        if status_code >= 400:
            msg = f"OpenAI API error ({status_code}): {body}"
            raise ProviderError(msg)

    def _parse_response(self, data: dict[str, Any]) -> GenerateResult:
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage_data = data.get("usage", {})

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    function_name=func.get("name", ""),
                    function_arguments=func.get("arguments", "{}"),
                )
            )

        return GenerateResult(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            usage=UsageInfo(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            ),
            model=data.get("model", ""),
            provider="openai",
        )

    def _parse_stream_chunk(self, data: dict[str, Any]) -> StreamChunk:
        choice = data.get("choices", [{}])[0]
        delta = choice.get("delta", {})

        tool_calls: list[ToolCall] | None = None
        if "tool_calls" in delta:
            tool_calls = []
            for tc in delta["tool_calls"]:
                func = tc.get("function", {})
                tool_calls.append(
                    ToolCall(
                        id=tc.get("id", ""),
                        function_name=func.get("name", ""),
                        function_arguments=func.get("arguments", ""),
                    )
                )

        return StreamChunk(
            content=delta.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason"),
            model=data.get("model", ""),
        )

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
            provider="openai",
        )

    @staticmethod
    def _model_supports_tools(model_id: str) -> bool:
        """Heuristic: GPT-4 and GPT-3.5-turbo models support function calling."""
        tool_prefixes = ("gpt-4", "gpt-3.5-turbo", "o3", "o4")
        return any(model_id.startswith(p) for p in tool_prefixes)
