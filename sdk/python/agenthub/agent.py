"""Core Agent class for the Agent Garden Full Code Python SDK.

Provides a builder-pattern API for defining agents programmatically.
The resulting agent config can be serialized to valid agent.yaml,
loaded from YAML, and deployed via the standard pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .deploy import DeployConfig, PromptConfig
from .memory import MemoryConfig
from .model import ModelConfig
from .tool import Tool, ToolConfig

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Configuration for an Agent Garden agent."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    team: str = "default"
    owner: str = ""
    framework: str = "custom"
    model: ModelConfig | None = None
    tools: list[ToolConfig] = field(default_factory=list)
    prompts: PromptConfig | None = None
    memory: MemoryConfig | None = None
    guardrails: list[str] = field(default_factory=list)
    deploy: DeployConfig | None = None
    tags: list[str] = field(default_factory=list)
    knowledge_bases: list[str] = field(default_factory=list)
    subagents: list[dict[str, str]] = field(default_factory=list)
    mcp_servers: list[dict[str, str]] = field(default_factory=list)


class Agent:
    """Full Code SDK entry point for defining agents programmatically.

    Supports a fluent builder pattern::

        agent = (
            Agent("my-agent", version="1.0.0", team="eng")
            .with_model(primary="claude-sonnet-4")
            .with_prompt(system="You are helpful.")
            .with_deploy(cloud="aws")
        )
    """

    def __init__(self, name: str, **kwargs: Any) -> None:
        self.config = AgentConfig(name=name, **kwargs)
        self._tools: list[Tool] = []
        self._middleware: list[Callable[..., Any]] = []
        self._hooks: dict[str, list[Callable[..., Any]]] = {}
        self._state: dict[str, Any] = {}

    # -----------------------------------------------------------------
    # Builder pattern methods (all return self for chaining)
    # -----------------------------------------------------------------

    def with_model(
        self,
        primary: str,
        fallback: str | None = None,
        **kwargs: Any,
    ) -> Agent:
        """Configure the LLM model."""
        self.config.model = ModelConfig(primary=primary, fallback=fallback, **kwargs)
        return self

    def with_tool(self, tool: Tool) -> Agent:
        """Add a tool to this agent."""
        self._tools.append(tool)
        self.config.tools.append(tool.to_config())
        return self

    def with_prompt(self, system: str, **kwargs: Any) -> Agent:
        """Set the system prompt (inline text or registry ref)."""
        self.config.prompts = PromptConfig(system=system, **kwargs)
        return self

    def with_memory(self, backend: str = "in_memory", **kwargs: Any) -> Agent:
        """Configure conversation memory."""
        self.config.memory = MemoryConfig(backend=backend, **kwargs)
        return self

    def with_guardrail(self, name: str) -> Agent:
        """Add a guardrail by name."""
        self.config.guardrails.append(name)
        return self

    def with_subagent(
        self,
        ref: str,
        name: str | None = None,
        description: str | None = None,
    ) -> Agent:
        """Add a subagent reference for A2A communication."""
        entry: dict[str, str] = {"ref": ref}
        if name:
            entry["name"] = name
        if description:
            entry["description"] = description
        self.config.subagents.append(entry)
        return self

    def with_mcp_server(self, ref: str, transport: str = "stdio") -> Agent:
        """Attach an MCP server as a sidecar."""
        self.config.mcp_servers.append({"ref": ref, "transport": transport})
        return self

    def with_deploy(self, cloud: str = "local", **kwargs: Any) -> Agent:
        """Configure deployment target."""
        self.config.deploy = DeployConfig(cloud=cloud, **kwargs)
        return self

    def tag(self, *tags: str) -> Agent:
        """Add discovery tags."""
        self.config.tags.extend(tags)
        return self

    # -----------------------------------------------------------------
    # Advanced features
    # -----------------------------------------------------------------

    def use(self, middleware: Callable[..., Any]) -> Agent:
        """Add middleware (pre/post processing on every turn)."""
        self._middleware.append(middleware)
        return self

    def on(self, event: str, handler: Callable[..., Any]) -> Agent:
        """Register event hook (tool_call, turn_start, turn_end, error)."""
        self._hooks.setdefault(event, []).append(handler)
        return self

    @property
    def state(self) -> dict[str, Any]:
        """Typed state persisted across turns."""
        return self._state

    def route(self, message: str, context: dict[str, Any]) -> str | None:
        """Override for custom routing logic. Return tool/agent name or None."""
        return None

    def select_tools(self, message: str) -> list[Tool]:
        """Override for dynamic tool selection based on input."""
        return list(self._tools)

    # -----------------------------------------------------------------
    # Serialization
    # -----------------------------------------------------------------

    def to_yaml(self) -> str:
        """Serialize to valid agent.yaml content."""
        from .yaml_utils import agent_to_yaml

        return agent_to_yaml(self)

    @classmethod
    def from_yaml(cls, yaml_content: str) -> Agent:
        """Load agent from YAML string."""
        from .yaml_utils import yaml_to_agent

        return yaml_to_agent(yaml_content)

    @classmethod
    def from_file(cls, path: str) -> Agent:
        """Load agent from YAML file path."""
        content = Path(path).read_text(encoding="utf-8")
        return cls.from_yaml(content)

    def save(self, path: str) -> None:
        """Save agent.yaml to disk."""
        Path(path).write_text(self.to_yaml(), encoding="utf-8")
        logger.info("Saved agent config to %s", path)

    # -----------------------------------------------------------------
    # Deployment
    # -----------------------------------------------------------------

    def deploy(self, target: str = "local") -> dict[str, Any]:
        """Deploy this agent (wraps garden deploy).

        Returns deploy info dict. Currently a placeholder that saves the
        YAML and returns metadata; the real implementation delegates to
        the engine.DeployEngine pipeline.
        """
        logger.info("Deploying agent '%s' to target '%s'", self.config.name, target)
        return {
            "agent": self.config.name,
            "version": self.config.version,
            "target": target,
            "status": "pending",
        }

    def validate(self) -> list[str]:
        """Validate the agent config. Returns list of error messages (empty = valid)."""
        from .validation import validate_agent

        return validate_agent(self)

    def __repr__(self) -> str:
        return (
            f"Agent(name={self.config.name!r}, version={self.config.version!r}, "
            f"framework={self.config.framework!r})"
        )
