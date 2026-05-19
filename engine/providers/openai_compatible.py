"""Generic OpenAI-compatible provider.

Most "new" LLM providers (Nvidia NIM, Groq, Together, Fireworks, DeepInfra,
Cerebras, Hyperbolic, Moonshot/Kimi, OpenRouter, …) speak the **OpenAI Chat
Completions** wire format. The only real differences are:

- ``base_url`` (e.g. ``https://api.groq.com/openai/v1``)
- ``api_key_env`` (e.g. ``GROQ_API_KEY``)
- ``default_headers`` (OpenRouter wants ``HTTP-Referer`` + ``X-Title``)

This class is parameterised by those three things and reuses the same wire
logic as :class:`engine.providers.openai_provider.OpenAIProvider` — so adding a
new provider is a YAML edit to ``catalog.yaml``, not a new Python class.

The hand-written ``OpenAIProvider`` stays in place because it's the canonical
default for ``provider_type=openai`` and serves as the implementation
reference. This class is *additive*.
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


class OpenAICompatibleProvider(ProviderBase):
    """Provider for any OpenAI-compatible chat completions endpoint.

    Construct via :class:`engine.providers.models.ProviderConfig` plus three
    catalog-derived fields surfaced as kwargs:

    Args:
        config: Standard provider config — must set ``base_url``.
        provider_name: Catalog name (e.g. ``"nvidia"``). Stamped on results.
        api_key_env: Env var name to read the API key from when
            ``config.api_key`` is unset (e.g. ``"NVIDIA_API_KEY"``).
        default_headers: Extra headers to send on every request (e.g.
            OpenRouter's ``HTTP-Referer`` + ``X-Title``).
    """

    def __init__(
        self,
        config: ProviderConfig,
        *,
        provider_name: str,
        api_key_env: str,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(config)
        if not config.base_url:
            msg = (
                f"OpenAICompatibleProvider requires base_url in ProviderConfig "
                f"(provider='{provider_name}')"
            )
            raise ProviderError(msg)

        api_key = config.api_key or os.environ.get(api_key_env)
        if not api_key:
            msg = (
                f"API key not found for provider '{provider_name}'. "
                f"Set {api_key_env} environment variable or pass api_key in "
                f"ProviderConfig."
            )
            raise AuthenticationError(msg)

        self._provider_name = provider_name
        self._api_key = api_key
        self._base_url = config.base_url.rstrip("/")
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if default_headers:
            headers.update(default_headers)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(config.timeout),
        )

    @property
    def name(self) -> str:
        return self._provider_name

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
        resolved_model = self._resolve_model(model)
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
                    yield self._parse_stream_chunk(json.loads(data))
                except json.JSONDecodeError:
                    logger.warning("Failed to parse stream chunk from %s: %s", self.name, data)

    async def list_models(self) -> list[ModelInfo]:
        response = await self._request("GET", "/models")
        models: list[ModelInfo] = []
        for m in response.get("data", []):
            model_id = m.get("id", "")
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    id=model_id,
                    name=model_id,
                    provider=self._provider_name,
                    context_window=m.get("context_length") or m.get("context_window"),
                    supports_streaming=True,
                    supports_tools=True,  # most OpenAI-compatible providers do
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
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [t.model_dump() for t in tools]
        if stream:
            payload["stream"] = True
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        try:
            if method == "GET":
                resp = await self._client.get(path)
            else:
                resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            msg = f"{self._provider_name} request timed out: {exc}"
            raise ProviderError(msg) from exc
        except httpx.ConnectError as exc:
            msg = f"Failed to connect to {self._provider_name} API at {self._base_url}: {exc}"
            raise ProviderError(msg) from exc
        body = resp.text
        self._check_status(resp.status_code, body)
        return resp.json()

    def _check_status(self, status_code: int, body: str) -> None:
        if status_code == 200:
            return
        if status_code == 401:
            msg = f"Invalid API key for provider '{self._provider_name}'"
            raise AuthenticationError(msg)
        if status_code == 404:
            raise ModelNotFoundError(f"Model not found ({self._provider_name}): {body}")
        if status_code == 429:
            raise RateLimitError(f"{self._provider_name} rate limit exceeded: {body}")
        if status_code >= 400:
            msg = f"{self._provider_name} API error ({status_code}): {body}"
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
            provider=self._provider_name,
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
            provider=self._provider_name,
        )


# ─── Catalog-driven factory ────────────────────────────────────────────────


def from_catalog(
    name: str,
    *,
    default_model: str | None = None,
    timeout: float = 60.0,
) -> OpenAICompatibleProvider:
    """Construct an :class:`OpenAICompatibleProvider` from a catalog entry.

    Reads the api key from the env var declared on the catalog entry. Raises
    :class:`KeyError` if ``name`` is not in the catalog (built-in or user-local).

    For ``type: gateway`` entries (Track H / #164) the ``base_url`` may be
    overridden by the gateway-specific env var (e.g. ``LITELLM_BASE_URL``,
    ``OPENROUTER_BASE_URL``) so the same catalog entry serves local-dev
    docker-compose, self-hosted, and SaaS deployments.
    """
    # Local import to avoid a circular dep — catalog imports nothing from here.
    from engine.providers.catalog import get_entry

    entry = get_entry(name)
    if entry is None:
        msg = f"Provider '{name}' is not in the catalog"
        raise KeyError(msg)

    # ProviderType is a closed enum — the generic provider always uses OpenAI's
    # chat-completions wire shape, so we tag the config as ``openai`` even when
    # the upstream is e.g. Groq. The actual provider name surfaces via
    # ``provider_name`` on the instance and on every result.
    from engine.providers.models import ProviderType

    base_url = str(entry.base_url)
    if entry.type == "gateway":
        # Gateways can be relocated (local proxy → SaaS endpoint) without
        # editing the catalog. Convention: <NAME>_BASE_URL overrides the
        # built-in default.
        override = os.environ.get(f"{name.upper()}_BASE_URL")
        if override:
            base_url = override

    config = ProviderConfig(
        provider_type=ProviderType.openai,
        base_url=base_url,
        default_model=default_model,
        timeout=timeout,
    )
    return OpenAICompatibleProvider(
        config,
        provider_name=name,
        api_key_env=entry.api_key_env,
        default_headers=dict(entry.default_headers),
    )
