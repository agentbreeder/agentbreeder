"""OpenRouter Connector — discovers models from the OpenRouter API.

Connects to OpenRouter (https://openrouter.ai/api/v1), reads available
models, and registers them in the Agent Garden Model registry.

Configuration:
    OPENROUTER_API_KEY: API key for OpenRouter (required for full model list)
    OPENROUTER_BASE_URL: Optional override for the base URL
"""

from __future__ import annotations

import logging
import os

import httpx

from connectors.base import BaseConnector

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterConnector(BaseConnector):
    """Discovers models from OpenRouter and registers them."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "openrouter"

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def is_available(self) -> bool:
        """Check if the OpenRouter API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}/models", headers=self._headers())
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def scan(self) -> list[dict]:
        """Fetch available models from OpenRouter."""
        models: list[dict] = []

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}/models", headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch models from OpenRouter at %s: %s", self._base_url, e)
            return []
        except (ValueError, KeyError) as e:
            logger.warning("Invalid response from OpenRouter: %s", e)
            return []

        for model_entry in data.get("data", []):
            model_id = model_entry.get("id", "")
            if not model_id:
                continue

            provider = _extract_provider(model_id)
            pricing = model_entry.get("pricing", {})

            models.append(
                {
                    "name": model_id,
                    "provider": provider,
                    "description": model_entry.get("name", f"Model {model_id} via OpenRouter"),
                    "source": "openrouter",
                    "config": {
                        "openrouter_base_url": self._base_url,
                        "context_length": model_entry.get("context_length"),
                        "pricing": {
                            "prompt": pricing.get("prompt"),
                            "completion": pricing.get("completion"),
                        },
                    },
                }
            )

        logger.info("OpenRouter connector discovered %d models", len(models))
        return models


def _extract_provider(model_id: str) -> str:
    """Extract provider from model ID. E.g., 'anthropic/claude-3.5-sonnet' -> 'anthropic'."""
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    # Infer from common prefixes
    model_lower = model_id.lower()
    if model_lower.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    if model_lower.startswith("claude"):
        return "anthropic"
    if model_lower.startswith("gemini"):
        return "google"
    if model_lower.startswith("llama"):
        return "meta"
    if model_lower.startswith(("mixtral", "mistral")):
        return "mistral"
    return "unknown"
