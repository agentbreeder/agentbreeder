"""LiteLLM Gateway Connector — discovers models from a LiteLLM proxy instance.

Connects to a running LiteLLM proxy, reads available models,
and registers them in the Agent Garden Model registry.

Configuration:
    LITELLM_BASE_URL: Base URL of the LiteLLM proxy (default: http://localhost:4000)
    LITELLM_API_KEY: Optional API key for authenticated proxies
"""

from __future__ import annotations

import logging
import os

import httpx

from connectors.base import BaseConnector

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:4000"


class LiteLLMConnector(BaseConnector):
    """Discovers models from a LiteLLM proxy and registers them."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("LITELLM_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self._api_key = api_key or os.environ.get("LITELLM_API_KEY")
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "litellm"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def is_available(self) -> bool:
        """Check if the LiteLLM proxy is reachable."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/health", headers=self._headers()
                )
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def scan(self) -> list[dict]:
        """Fetch available models from the LiteLLM proxy."""
        models: list[dict] = []

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # LiteLLM exposes OpenAI-compatible /v1/models endpoint
                resp = await client.get(
                    f"{self._base_url}/v1/models", headers=self._headers()
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch models from LiteLLM at %s: %s", self._base_url, e)
            return []
        except (ValueError, KeyError) as e:
            logger.warning("Invalid response from LiteLLM: %s", e)
            return []

        for model_entry in data.get("data", []):
            model_id = model_entry.get("id", "")
            if not model_id:
                continue

            # Determine provider from model ID (e.g., "openai/gpt-4o" -> "openai")
            provider = _extract_provider(model_id)

            models.append({
                "name": model_id,
                "provider": provider,
                "description": f"Model {model_id} via LiteLLM gateway",
                "source": "litellm",
                "config": {
                    "litellm_base_url": self._base_url,
                    "model_info": {
                        k: v
                        for k, v in model_entry.items()
                        if k in ("id", "object", "created", "owned_by")
                    },
                },
            })

        logger.info("LiteLLM connector discovered %d models", len(models))
        return models

    async def get_model_info(self, model_id: str) -> dict | None:
        """Get detailed info for a specific model from LiteLLM."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/model/info",
                    params={"model": model_id},
                    headers=self._headers(),
                )
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError as e:
            logger.warning("Failed to get model info for %s: %s", model_id, e)
        return None


def _extract_provider(model_id: str) -> str:
    """Extract provider from model ID. E.g., 'openai/gpt-4o' -> 'openai'."""
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    # Infer provider from common model name prefixes
    model_lower = model_id.lower()
    if model_lower.startswith(("gpt-", "o1-", "o3-")):
        return "openai"
    if model_lower.startswith("claude"):
        return "anthropic"
    if model_lower.startswith("gemini"):
        return "google"
    if model_lower.startswith(("llama", "mixtral", "mistral")):
        return "meta" if "llama" in model_lower else "mistral"
    return "unknown"
