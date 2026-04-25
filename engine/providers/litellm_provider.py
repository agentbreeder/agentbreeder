"""LiteLLM proxy provider — routes generate() calls through the LiteLLM gateway.

The LiteLLM proxy exposes an OpenAI-compatible API, so this provider subclasses
OpenAIProvider and overrides only the name property and the model listing to
tag models with the "litellm" provider rather than "openai".

Configuration (from environment or ProviderConfig):
  - base_url  → LITELLM_BASE_URL  (default: http://localhost:4000)
  - api_key   → LITELLM_VIRTUAL_KEY (the per-agent scoped key minted at deploy time)
"""

from __future__ import annotations

import logging

from engine.providers.base import AuthenticationError
from engine.providers.models import ModelInfo, ProviderConfig
from engine.providers.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

_LITELLM_DEFAULT_BASE_URL = "http://localhost:4000"


class LiteLLMProvider(OpenAIProvider):
    """LiteLLM proxy provider.

    Proxies all requests through the LiteLLM gateway at ``LITELLM_BASE_URL``
    using a scoped virtual key (``LITELLM_VIRTUAL_KEY``) for authentication.
    The proxy speaks the OpenAI Chat Completions API, so all ``generate()``
    and ``generate_stream()`` logic is inherited unchanged from OpenAIProvider.
    """

    def __init__(self, config: ProviderConfig) -> None:
        # Ensure a base_url is set before delegating to OpenAIProvider.__init__,
        # which reads config.base_url to set self._base_url.
        if not config.base_url:
            config.base_url = _LITELLM_DEFAULT_BASE_URL

        # OpenAIProvider.__init__ raises AuthenticationError if api_key is absent.
        # Provide a clear LiteLLM-specific message when the virtual key is missing.
        if not config.api_key:
            msg = (
                "LiteLLM virtual key not found. "
                "Set the LITELLM_VIRTUAL_KEY environment variable or pass "
                "api_key in ProviderConfig. "
                "The key is automatically injected at deploy time when "
                "model.gateway: litellm is set in agent.yaml."
            )
            raise AuthenticationError(msg)

        super().__init__(config)

    @property
    def name(self) -> str:
        return "litellm"

    async def list_models(self) -> list[ModelInfo]:
        """Fetch available models from the LiteLLM proxy /models endpoint."""
        response = await self._request("GET", "/models")
        models: list[ModelInfo] = []
        for m in response.get("data", []):
            model_id = m.get("id", "")
            models.append(
                ModelInfo(
                    id=model_id,
                    name=model_id,
                    provider="litellm",
                    supports_tools=self._model_supports_tools(model_id),
                    supports_streaming=True,
                )
            )
        return sorted(models, key=lambda m: m.id)
