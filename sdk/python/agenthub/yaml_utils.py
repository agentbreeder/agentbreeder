"""YAML serialization and deserialization for AgentBreeder agents.

Converts between Agent objects and valid agent.yaml content that matches
the canonical spec documented in CLAUDE.md.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .agent import Agent

from .deploy import DeployConfig, PromptConfig
from .memory import MemoryConfig
from .model import ModelConfig
from .tool import Tool


def agent_to_yaml(agent: Agent) -> str:
    """Serialize an Agent to valid agent.yaml content.

    Output field ordering matches the canonical agent.yaml spec:
    identity -> model -> framework -> tools ->
    prompts -> guardrails -> deploy
    """
    d: dict[str, Any] = {}

    # Identity block
    d["name"] = agent.config.name
    d["version"] = agent.config.version
    if agent.config.description:
        d["description"] = agent.config.description
    d["team"] = agent.config.team
    if agent.config.owner:
        d["owner"] = agent.config.owner
    if agent.config.tags:
        d["tags"] = agent.config.tags

    # Model
    if agent.config.model is not None:
        d["model"] = agent.config.model.to_dict()

    # Framework
    d["framework"] = agent.config.framework

    # Tools
    if agent._tools:
        d["tools"] = [t.to_dict() for t in agent._tools]

    # Prompts
    if agent.config.prompts is not None:
        d["prompts"] = agent.config.prompts.to_dict()

    # Guardrails
    if agent.config.guardrails:
        d["guardrails"] = agent.config.guardrails

    # Memory
    if agent.config.memory is not None:
        d["memory"] = agent.config.memory.to_dict()

    # Deploy
    if agent.config.deploy is not None:
        d["deploy"] = agent.config.deploy.to_dict()

    return yaml.dump(d, default_flow_style=False, sort_keys=False, allow_unicode=True)


def yaml_to_agent(yaml_str: str) -> Agent:
    """Parse agent.yaml content into an Agent instance.

    Supports the full canonical agent.yaml spec.
    """
    # Import here to avoid circular imports
    from .agent import Agent, AgentConfig

    data = yaml.safe_load(yaml_str)
    if not isinstance(data, dict):
        raise ValueError("Invalid YAML: expected a mapping at the top level")

    # Parse model config
    model_config = None
    if "model" in data:
        m = data["model"]
        if isinstance(m, dict):
            model_config = ModelConfig(
                primary=m["primary"],
                fallback=m.get("fallback"),
                gateway=m.get("gateway"),
                temperature=m.get("temperature", 0.7),
                max_tokens=m.get("max_tokens", 4096),
            )

    # Parse prompt config
    prompt_config = None
    if "prompts" in data:
        p = data["prompts"]
        if isinstance(p, dict) and "system" in p:
            prompt_config = PromptConfig(system=p["system"])

    # Parse memory config
    memory_config = None
    if "memory" in data:
        mem = data["memory"]
        if isinstance(mem, dict):
            memory_config = MemoryConfig(
                backend=mem.get("backend", "in_memory"),
                memory_type=mem.get("memory_type", "buffer_window"),
                max_messages=mem.get("max_messages", 100),
                namespace_pattern=mem.get("namespace_pattern", "{agent_id}:{session_id}"),
            )

    # Parse deploy config
    deploy_config = None
    if "deploy" in data:
        dep = data["deploy"]
        if isinstance(dep, dict):
            deploy_config = DeployConfig(
                cloud=dep.get("cloud", "local"),
                runtime=dep.get("runtime"),
                region=dep.get("region"),
                scaling=dep.get("scaling"),
                resources=dep.get("resources"),
                env_vars=dep.get("env_vars", {}),
                secrets=dep.get("secrets", []),
            )

    # Build the agent config
    config = AgentConfig(
        name=data.get("name", ""),
        version=data.get("version", "1.0.0"),
        description=data.get("description", ""),
        team=data.get("team", "default"),
        owner=data.get("owner", ""),
        framework=data.get("framework", "custom"),
        model=model_config,
        prompts=prompt_config,
        memory=memory_config,
        guardrails=data.get("guardrails", []),
        deploy=deploy_config,
        tags=data.get("tags", []),
    )

    agent = Agent.__new__(Agent)
    agent.config = config
    agent._middleware = []
    agent._hooks = {}
    agent._state = {}

    # Parse tools
    agent._tools = []
    if "tools" in data:
        for tool_data in data["tools"]:
            if isinstance(tool_data, dict):
                if "ref" in tool_data:
                    agent._tools.append(Tool.from_ref(tool_data["ref"]))
                else:
                    agent._tools.append(
                        Tool(
                            name=tool_data.get("name", ""),
                            description=tool_data.get("description", ""),
                        )
                    )

    return agent
