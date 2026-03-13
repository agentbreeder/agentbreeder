"""Google AI (Gemini) provider — direct REST API calls via httpx.

Supports chat completions, streaming, and function calling using the
Google AI REST API (generativelanguage.googleapis.com). No google-generativeai
SDK dependency — uses httpx directly for a lighter footprint.
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

GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-pro"

# Finish reason mapping: Gemini -> OpenAI-compatible
_FINISH_REASON_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "OTHER": "stop",
}


class GoogleProvider(ProviderBase):
    """Google AI (Gemini) REST API provider using httpx."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        api_key = config.api_key or os.environ.get("GOOGLE_AI_API_KEY")
        if not api_key:
            msg = (
                "Google AI API key not found. Set GOOGLE_AI_API_KEY environment variable "
                "or pass api_key in ProviderConfig."
            )
            raise AuthenticationError(msg)
        self._api_key = api_key
        self._base_url = (config.base_url or GOOGLE_API_BASE).rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(config.timeout),
        )

    @property
    def name(self) -> str:
        return "google"

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

        payload = self._build_payload(messages, temperature, max_tokens, tools)
        path = f"/models/{resolved_model}:generateContent"
        response = await self._request("POST", path, payload)
        return self._parse_response(response, resolved_model)

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        resolved_model = self._resolve_model(model) or DEFAULT_MODEL
        payload = self._build_payload(messages, temperature, max_tokens, tools)
        path = f"/models/{resolved_model}:streamGenerateContent"

        async with self._client.stream(
            "POST",
            path,
            json=payload,
            params={"key": self._api_key, "alt": "sse"},
        ) as resp:
            self._check_status(resp.status_code, "")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event_data = json.loads(data)
                    chunk = self._parse_stream_event(event_data, resolved_model)
                    if chunk is not None:
                        yield chunk
                except json.JSONDecodeError:
                    logger.warning("Failed to parse Gemini stream event: %s", data)

    async def list_models(self) -> list[ModelInfo]:
        response = await self._request("GET", "/models")
        models: list[ModelInfo] = []
        for m in response.get("models", []):
            # name is like "models/gemini-2.5-pro"
            raw_name = m.get("name", "")
            model_id = raw_name.split("/", 1)[-1] if "/" in raw_name else raw_name
            display = m.get("displayName", model_id)
            models.append(
                ModelInfo(
                    id=model_id,
                    name=display,
                    provider="google",
                    context_window=m.get("inputTokenLimit"),
                    max_output_tokens=m.get("outputTokenLimit"),
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
        temperature: float | None,
        max_tokens: int | None,
        tools: list[ToolDefinition] | None,
    ) -> dict[str, Any]:
        system_instruction: dict[str, Any] | None = None
        contents: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                system_instruction = {"parts": [{"text": content}]}
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            else:
                contents.append({"role": "user", "parts": [{"text": content}]})

        payload: dict[str, Any] = {"contents": contents}

        if system_instruction:
            payload["systemInstruction"] = system_instruction

        generation_config: dict[str, Any] = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = max_tokens
        if generation_config:
            payload["generationConfig"] = generation_config

        if tools:
            payload["tools"] = [{"functionDeclarations": [self._convert_tool(t) for t in tools]}]

        return payload

    @staticmethod
    def _convert_tool(tool: ToolDefinition) -> dict[str, Any]:
        """Convert from OpenAI ToolDefinition format to Gemini functionDeclaration."""
        return {
            "name": tool.function.name,
            "description": tool.function.description,
            "parameters": tool.function.parameters or {"type": "object", "properties": {}},
        }

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        """Make an HTTP request and handle errors."""
        params = {"key": self._api_key}
        try:
            if method == "GET":
                resp = await self._client.get(path, params=params)
            else:
                resp = await self._client.post(path, json=payload, params=params)
        except httpx.TimeoutException as e:
            msg = f"Google AI request timed out: {e}"
            raise ProviderError(msg) from e
        except httpx.ConnectError as e:
            msg = f"Failed to connect to Google AI API at {self._base_url}: {e}"
            raise ProviderError(msg) from e

        body = resp.text
        self._check_status(resp.status_code, body)
        return resp.json()

    def _check_status(self, status_code: int, body: str) -> None:
        if status_code == 200:
            return
        if status_code == 401 or status_code == 403:
            raise AuthenticationError("Invalid Google AI API key")
        if status_code == 404:
            raise ModelNotFoundError(f"Google AI model not found: {body}")
        if status_code == 429:
            raise RateLimitError(f"Google AI rate limit exceeded: {body}")
        if status_code >= 400:
            msg = f"Google AI API error ({status_code}): {body}"
            raise ProviderError(msg)

    def _parse_response(self, data: dict[str, Any], model_id: str) -> GenerateResult:
        candidates = data.get("candidates", [])
        usage_data = data.get("usageMetadata", {})

        text_content: str | None = None
        tool_calls: list[ToolCall] = []
        finish_reason = "stop"

        if candidates:
            candidate = candidates[0]
            raw_finish = candidate.get("finishReason", "STOP")
            finish_reason = _FINISH_REASON_MAP.get(raw_finish, "stop")

            content_obj = candidate.get("content", {})
            for part in content_obj.get("parts", []):
                if "text" in part:
                    text_content = part["text"]
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(
                        ToolCall(
                            id=f"call_{fc.get('name', '')}",
                            function_name=fc.get("name", ""),
                            function_arguments=json.dumps(fc.get("args", {})),
                        )
                    )

        prompt_tokens = usage_data.get("promptTokenCount", 0)
        completion_tokens = usage_data.get("candidatesTokenCount", 0)

        return GenerateResult(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=UsageInfo(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            model=model_id,
            provider="google",
        )

    @staticmethod
    def _parse_stream_event(data: dict[str, Any], model_id: str) -> StreamChunk | None:
        """Parse a single SSE event from Gemini streaming."""
        candidates = data.get("candidates", [])
        if not candidates:
            return None

        candidate = candidates[0]
        content_obj = candidate.get("content", {})
        finish_reason_raw = candidate.get("finishReason")

        text_content: str | None = None
        for part in content_obj.get("parts", []):
            if "text" in part:
                text_content = part["text"]
                break

        finish_reason = None
        if finish_reason_raw:
            finish_reason = _FINISH_REASON_MAP.get(finish_reason_raw, finish_reason_raw.lower())

        return StreamChunk(
            content=text_content,
            finish_reason=finish_reason,
            model=model_id,
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
            provider="google",
        )

    @staticmethod
    def _model_supports_tools(model_id: str) -> bool:
        """Heuristic: Gemini models support function calling."""
        return model_id.lower().startswith("gemini")
