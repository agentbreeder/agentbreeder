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
    ProviderHealth,
    StreamChunk,
    ToolCall,
    ToolDefinition,
    UsageInfo,
)

logger = logging.getLogger(__name__)

ANTHROPIC_API_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"

# Prompt caching: Anthropic caches a request prefix (order: tools → system →
# messages) when a cache_control breakpoint is attached, subject to a minimum
# cacheable prefix of ~1024 tokens. We estimate ~4 chars/token and auto-cache the
# large, static parts of a request so repeated calls that re-send them (e.g. the
# conversational agent builder's big tool schema) read from cache instead of
# reprocessing. Caching is GA on anthropic-version 2023-06-01 — no beta header.
_CACHE_MIN_CHARS = 4096
_CACHE_CONTROL = {"type": "ephemeral"}

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

        # Per-index accumulator for tool_use content blocks.
        # Keyed by block index; each entry holds the block id, name, and the
        # running concatenation of input_json_delta.partial_json fragments.
        _tool_blocks: dict[int, dict[str, str]] = {}

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
                except json.JSONDecodeError:
                    logger.warning("Failed to parse Anthropic stream event: %s", data)
                    continue

                event_type = event_data.get("type")

                # ── Tool-use block accumulation ───────────────────────────
                if event_type == "content_block_start":
                    block = event_data.get("content_block", {})
                    if block.get("type") == "tool_use":
                        idx = event_data.get("index", 0)
                        _tool_blocks[idx] = {
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "json_buf": "",
                        }
                    continue

                if event_type == "content_block_delta":
                    idx = event_data.get("index", 0)
                    delta = event_data.get("delta", {})
                    delta_type = delta.get("type")
                    if delta_type == "input_json_delta" and idx in _tool_blocks:
                        _tool_blocks[idx]["json_buf"] += delta.get("partial_json", "")
                        continue
                    # text_delta (and any unknown delta type) fall through to
                    # the stateless parser below

                if event_type == "content_block_stop":
                    idx = event_data.get("index", 0)
                    if idx in _tool_blocks:
                        entry = _tool_blocks.pop(idx)
                        yield StreamChunk(
                            tool_calls=[
                                ToolCall(
                                    id=entry["id"],
                                    function_name=entry["name"],
                                    function_arguments=entry["json_buf"],
                                )
                            ],
                            model="",
                        )
                    continue

                # ── Stateless events (text deltas, message_start, finish) ─
                chunk = self._parse_stream_event(event_data)
                if chunk is not None:
                    yield chunk

        # Safety: flush any tool blocks that never received content_block_stop
        for entry in _tool_blocks.values():
            yield StreamChunk(
                tool_calls=[
                    ToolCall(
                        id=entry["id"],
                        function_name=entry["name"],
                        function_arguments=entry["json_buf"],
                    )
                ],
                model="",
            )

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

    async def health_check(self) -> ProviderHealth:
        try:
            await self._request("GET", "/models")
        except AuthenticationError as e:
            return ProviderHealth(
                healthy=False, reason=str(e) or "authentication failed", error_code=401
            )
        except RateLimitError as e:
            return ProviderHealth(healthy=False, reason=str(e) or "rate limited", error_code=429)
        except ProviderError as e:
            msg = str(e)
            if "timed out" in msg.lower() or "failed to connect" in msg.lower():
                return ProviderHealth(healthy=False, reason=msg, error_code=None)
            return ProviderHealth(healthy=False, reason=msg, error_code=500)
        return ProviderHealth(healthy=True)

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
        if temperature is not None:
            payload["temperature"] = temperature

        if tools:
            tool_payload = [self._convert_tool(t) for t in tools]
            # A breakpoint on the LAST tool caches the entire tools prefix.
            if len(json.dumps(tool_payload)) >= _CACHE_MIN_CHARS:
                tool_payload[-1]["cache_control"] = dict(_CACHE_CONTROL)
            payload["tools"] = tool_payload

        if system_content:
            # cache_control can only attach to a structured block, so promote a
            # large system prompt to a one-block list (caches the tools+system
            # prefix). Small prompts stay plain strings — unchanged behaviour.
            if len(system_content) >= _CACHE_MIN_CHARS:
                payload["system"] = [
                    {
                        "type": "text",
                        "text": system_content,
                        "cache_control": dict(_CACHE_CONTROL),
                    }
                ]
            else:
                payload["system"] = system_content

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
        if status_code == 401 or status_code == 403:
            raise AuthenticationError("Invalid Anthropic API key")
        if status_code == 404:
            # Raw body is not included to avoid leaking upstream internals.
            logger.debug("Anthropic 404 response body: %s", body)
            raise ModelNotFoundError("Anthropic model not found (HTTP 404)")
        if status_code == 429:
            # Raw body is not included to avoid leaking upstream internals.
            logger.debug("Anthropic 429 response body: %s", body)
            raise RateLimitError("Anthropic rate limit exceeded (HTTP 429)")
        if status_code >= 400:
            # Raw body is not included to avoid leaking upstream internals.
            logger.debug("Anthropic error response body (status=%d): %s", status_code, body)
            msg = f"Anthropic API error (HTTP {status_code})"
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

        # When prompt caching is active, Anthropic reports cached input tokens
        # separately from the uncached `input_tokens`. Fold them all into
        # prompt_tokens so cost attribution isn't undercounted.
        prompt_tokens = (
            usage_data.get("input_tokens", 0)
            + usage_data.get("cache_creation_input_tokens", 0)
            + usage_data.get("cache_read_input_tokens", 0)
        )
        completion_tokens = usage_data.get("output_tokens", 0)

        return GenerateResult(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=UsageInfo(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
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
