"""Memory configuration for AgentBreeder agents.

Supports in-memory, PostgreSQL, and Redis backends with configurable
buffer strategies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MemoryConfig:
    """Serializable memory configuration."""

    backend: str = "in_memory"
    memory_type: str = "buffer_window"
    max_messages: int = 100
    namespace_pattern: str = "{agent_id}:{session_id}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML output."""
        d: dict[str, Any] = {"backend": self.backend, "memory_type": self.memory_type}
        if self.max_messages != 100:
            d["max_messages"] = self.max_messages
        if self.namespace_pattern != "{agent_id}:{session_id}":
            d["namespace_pattern"] = self.namespace_pattern
        return d


class Memory:
    """Configure memory backends programmatically."""

    def __init__(self, config: MemoryConfig | None = None) -> None:
        self.config = config or MemoryConfig()

    @staticmethod
    def buffer_window(max_messages: int = 100, **kwargs: Any) -> Memory:
        """Create a buffer-window memory (sliding window of recent messages)."""
        return Memory(
            MemoryConfig(
                memory_type="buffer_window",
                max_messages=max_messages,
                **kwargs,
            )
        )

    @staticmethod
    def buffer(**kwargs: Any) -> Memory:
        """Create an unbounded buffer memory."""
        return Memory(MemoryConfig(memory_type="buffer", **kwargs))

    @staticmethod
    def postgresql(**kwargs: Any) -> Memory:
        """Create a PostgreSQL-backed memory."""
        return Memory(MemoryConfig(backend="postgresql", **kwargs))

    def to_config(self) -> MemoryConfig:
        """Return the underlying MemoryConfig."""
        return self.config

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML output."""
        return self.config.to_dict()
