"""Ollama provider — connects to a local Ollama instance.

Ollama exposes an OpenAI-compatible API at /v1/chat/completions,
so this provider reuses the same HTTP patterns as OpenAI but with
Ollama-specific model listing and health detection.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from engine.providers.base import (
    ModelNotFoundError,
    ProviderBase,
    ProviderError,
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

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"

# Models known to support function calling in Ollama
_TOOL_CAPABLE_MODELS = frozenset(
    {
        "llama3.1",
        "llama3.2",
        "llama3.3",
        "qwen2.5",
        "qwen3",
        "mistral",
        "mixtral",
        "command-r",
        "command-r-plus",
        "firefunction-v2",
    }
)


class OllamaProvider(ProviderBase):
    """Ollama provider using the OpenAI-compatible /v1 API."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        self._base_url = (
            config.base_url or os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
        ).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(config.timeout),
        )

    @property
    def name(self) -> str:
        return "ollama"

    async def generate(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
        stream: bool = False,
    ) -> GenerateResult:
        resolved_model = self._resolve_model(model)

        if stream:
            return await self._collect_stream(
                messages, resolved_model, temperature, max_tokens, tools
            )

        payload = self._build_payload(
            messages, resolved_model, temperature, max_tokens, tools, stream=False
        )

        response = await self._request("POST", "/v1/chat/completions", payload)
        return self._parse_response(response)

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        resolved_model = self._resolve_model(model)
        payload = self._build_payload(
            messages, resolved_model, temperature, max_tokens, tools, stream=True
        )

        async with self._client.stream("POST", "/v1/chat/completions", json=payload) as resp:
            self._check_status(resp.status_code)
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
                    logger.warning("Failed to parse Ollama stream chunk: %s", data)

    async def list_models(self) -> list[ModelInfo]:
        """List models available in the local Ollama instance.

        Uses the Ollama-native /api/tags endpoint (not the OpenAI-compat one).
        """
        try:
            resp = await self._client.get("/api/tags")
        except httpx.ConnectError as e:
            msg = f"Cannot connect to Ollama at {self._base_url}: {e}"
            raise ProviderError(msg) from e

        if resp.status_code != 200:
            msg = f"Ollama /api/tags returned {resp.status_code}"
            raise ProviderError(msg)

        data = resp.json()
        models: list[ModelInfo] = []
        for m in data.get("models", []):
            model_name = m.get("name", "")
            base_name = model_name.split(":")[0]
            models.append(
                ModelInfo(
                    id=model_name,
                    name=model_name,
                    provider="ollama",
                    supports_tools=base_name in _TOOL_CAPABLE_MODELS,
                    supports_streaming=True,
                    is_local=True,
                )
            )
        return sorted(models, key=lambda m: m.id)

    async def health_check(self) -> bool:
        """Check if Ollama is running by hitting the root endpoint."""
        try:
            resp = await self._client.get("/")
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    async def close(self) -> None:
        await self._client.aclose()

    # ── Auto-detection ───────────────────────────────────────────────────

    @staticmethod
    async def detect(base_url: str | None = None, timeout: float = 5.0) -> bool:
        """Check if an Ollama instance is running at the given URL.

        This is a static convenience method for auto-detection during
        `agentbreeder dev` or provider setup.
        """
        url = (base_url or DEFAULT_OLLAMA_BASE_URL).rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                resp = await client.get(f"{url}/")
                return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

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
        try:
            if method == "GET":
                resp = await self._client.get(path)
            else:
                resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as e:
            msg = f"Ollama request timed out: {e}"
            raise ProviderError(msg) from e
        except httpx.ConnectError as e:
            msg = (
                f"Cannot connect to Ollama at {self._base_url}. "
                "Is Ollama running? Start it with: ollama serve"
            )
            raise ProviderError(msg) from e

        self._check_status(resp.status_code)
        return resp.json()

    def _check_status(self, status_code: int) -> None:
        if status_code == 200:
            return
        if status_code == 404:
            raise ModelNotFoundError("Model not found in Ollama. Run: ollama pull <model>")
        if status_code >= 400:
            msg = f"Ollama API error ({status_code})"
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
            provider="ollama",
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
            provider="ollama",
        )
