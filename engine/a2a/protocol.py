"""A2A JSON-RPC message types per Google A2A specification.

Defines the core protocol messages for agent-to-agent communication.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class JsonRpcRequest(BaseModel):
    """JSON-RPC 2.0 request."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcError(BaseModel):
    """JSON-RPC 2.0 error."""

    code: int
    message: str
    data: Any = None


class JsonRpcResponse(BaseModel):
    """JSON-RPC 2.0 response."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    result: Any = None
    error: JsonRpcError | None = None


# --- A2A-specific message types ---


class TaskSendParams(BaseModel):
    """Parameters for tasks/send method."""

    message: str
    context: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None


class TaskResult(BaseModel):
    """Result of a task execution."""

    task_id: str
    status: str = "completed"  # "completed" | "failed" | "in_progress"
    output: str = ""
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    tokens: int = 0
    latency_ms: int = 0


class AgentCardInfo(BaseModel):
    """Agent Card as returned by /.well-known/agent.json."""

    name: str
    description: str = ""
    url: str
    version: str = "1.0.0"
    capabilities: list[str] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    authentication: dict[str, Any] = Field(default_factory=lambda: {"schemes": ["none"]})
    default_input_modes: list[str] = Field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text"])


# JSON-RPC method names
A2A_METHOD_SEND = "tasks/send"
A2A_METHOD_GET = "tasks/get"
A2A_METHOD_CANCEL = "tasks/cancel"
A2A_METHOD_SUBSCRIBE = "tasks/sendSubscribe"
