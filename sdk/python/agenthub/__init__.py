"""AgentBreeder Python SDK — Full Code tier.
Define, validate, and serialize agents programmatically.
Usage::
    from agenthub import Agent, Tool, Model, Memory
    agent = (
        Agent("my-agent", version="1.0.0", team="eng")
        .with_model(primary="claude-sonnet-4")
        .with_prompt(system="You are helpful.")
        .with_deploy(cloud="aws")
    )
"""

from .agent import Agent, AgentConfig
from .deploy import DeployConfig, PromptConfig
from .memory import Memory, MemoryConfig
from .model import Model, ModelConfig
from .tool import Tool, ToolConfig

__version__ = "0.1.0"
__all__ = [
    # Agent
    "Agent",
    "AgentConfig",
    "DeployConfig",
    "Memory",
    "MemoryConfig",
    "Model",
    "ModelConfig",
    "PromptConfig",
    "Tool",
    "ToolConfig",
]
