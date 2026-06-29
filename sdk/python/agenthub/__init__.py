"""AgentBreeder Python SDK — Full Code tier.

Define, validate, serialize, and deploy agents and orchestrations programmatically.

Usage::

    from agenthub import Agent, Tool, Model, Memory
    from agenthub import Orchestration, Pipeline, FanOut, Supervisor
    from agenthub import KeywordRouter, IntentRouter, RoundRobinRouter, ClassifierRouter

    agent = (
        Agent("my-agent", version="1.0.0", team="eng")
        .with_model(primary="claude-sonnet-4")
        .with_prompt(system="You are helpful.")
        .with_deploy(cloud="aws")
    )

    pipeline = (
        Orchestration("support", strategy="router", team="eng")
        .add_agent("triage", ref="agents/triage")
        .add_agent("billing", ref="agents/billing")
        .with_route("triage", condition="billing", target="billing")
    )
"""

from .agent import Agent, AgentConfig
from .deploy import DeployConfig, PromptConfig
from .memory import Memory, MemoryConfig
from .model import Model, ModelConfig
from .orchestration import (
    AgentEntry,
    ClassifierRouter,
    FanOut,
    IntentRouter,
    KeywordRouter,
    Orchestration,
    OrchestrationConfig,
    OrchestrationDeployConfig,
    Pipeline,
    RoundRobinRouter,
    Router,
    RouteRule,
    SharedStateConfig,
    Supervisor,
    SupervisorConfig,
)
from .rag import (
    CypherResponse,
    DeleteResponse,
    GraphEdge,
    GraphNode,
    IngestResult,
    ListIndexesResponse,
    NeighborhoodResponse,
    RagChunk,
    RagIndex,
    RagIndexError,
    RagIndexInfo,
    RagMcpClient,
    RagMcpError,
    RagMcpToolError,
    RagMcpTransportError,
    SearchResponse,
    StatsResponse,
    UpsertResponse,
    close_default_client,
    cypher,
    delete,
    list_indexes,
    neighborhood,
    query,
    search,
    stats,
    upsert,
)
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
    # RAG — HTTP index client
    "RagIndex",
    "RagIndexError",
    "IngestResult",
    # RAG — MCP tools (#279)
    "RagMcpClient",
    "RagMcpError",
    "RagMcpTransportError",
    "RagMcpToolError",
    "RagChunk",
    "SearchResponse",
    "NeighborhoodResponse",
    "GraphNode",
    "GraphEdge",
    "CypherResponse",
    "UpsertResponse",
    "DeleteResponse",
    "RagIndexInfo",
    "ListIndexesResponse",
    "StatsResponse",
    "search",
    "query",
    "neighborhood",
    "cypher",
    "upsert",
    "delete",
    "list_indexes",
    "stats",
    "close_default_client",
    # Orchestration — builders
    "Orchestration",
    "Pipeline",
    "FanOut",
    "Supervisor",
    # Orchestration — routers
    "Router",
    "KeywordRouter",
    "IntentRouter",
    "RoundRobinRouter",
    "ClassifierRouter",
    # Orchestration — data types
    "OrchestrationConfig",
    "OrchestrationDeployConfig",
    "AgentEntry",
    "RouteRule",
    "SharedStateConfig",
    "SupervisorConfig",
]
