"""Model configuration for AgentBreeder agents.

Provides dataclass-based config and convenience factory methods for
popular model providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ModelConfig:
    """Configuration for the LLM model used by an agent."""

    primary: str
    fallback: str | None = None
    gateway: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML output."""
        d: dict[str, Any] = {"primary": self.primary}
        if self.fallback is not None:
            d["fallback"] = self.fallback
        if self.gateway is not None:
            d["gateway"] = self.gateway
        if self.temperature != 0.7:
            d["temperature"] = self.temperature
        if self.max_tokens != 4096:
            d["max_tokens"] = self.max_tokens
        return d


class Model:
    """Model configuration helpers with factory methods for popular models."""

    @staticmethod
    def claude_sonnet(**kwargs: Any) -> ModelConfig:
        """Create a config for Claude Sonnet 4."""
        return ModelConfig(primary="claude-sonnet-4", **kwargs)

    @staticmethod
    def claude_opus(**kwargs: Any) -> ModelConfig:
        """Create a config for Claude Opus 4."""
        return ModelConfig(primary="claude-opus-4", **kwargs)

    @staticmethod
    def gpt4o(**kwargs: Any) -> ModelConfig:
        """Create a config for GPT-4o."""
        return ModelConfig(primary="gpt-4o", **kwargs)

    @staticmethod
    def ollama(model_name: str, **kwargs: Any) -> ModelConfig:
        """Create a config for a local Ollama model."""
        return ModelConfig(primary=f"ollama/{model_name}", gateway="ollama", **kwargs)
