"""A2A server — FastAPI sub-app implementing the A2A protocol.

Provides:
- /.well-known/agent.json — Agent Card discovery
- /a2a — JSON-RPC endpoint for tasks/send, tasks/get
- SSE streaming support for long-running tasks
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from engine.a2a.protocol import (
    A2A_METHOD_SEND,
    AgentCardInfo,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    TaskResult,
)

logger = logging.getLogger(__name__)

# In-memory task store for this agent instance
_tasks: dict[str, TaskResult] = {}

# Agent card — set during mount
_agent_card: AgentCardInfo | None = None
_invoke_handler: Any = None


def create_a2a_app(
    agent_card: AgentCardInfo,
    invoke_handler: Any = None,
) -> FastAPI:
    """Create a FastAPI sub-app that serves the A2A protocol.

    Args:
        agent_card: The Agent Card to serve at /.well-known/agent.json.
        invoke_handler: Optional async callable(message, context) -> dict
            that processes incoming tasks. If None, returns a stub response.
    """
    global _agent_card, _invoke_handler
    _agent_card = agent_card
    _invoke_handler = invoke_handler

    a2a_app = FastAPI(title=f"A2A: {agent_card.name}")

    @a2a_app.get("/.well-known/agent.json")
    async def get_agent_card() -> dict[str, Any]:
        """Serve the Agent Card for discovery."""
        return _agent_card.model_dump() if _agent_card else {}

    @a2a_app.post("/a2a")
    async def handle_jsonrpc(request: Request) -> JSONResponse:
        """Handle A2A JSON-RPC requests."""
        try:
            body = await request.json()
            rpc = JsonRpcRequest(**body)
        except Exception as e:
            error_resp = JsonRpcResponse(
                id="unknown",
                error=JsonRpcError(code=-32700, message=f"Parse error: {e}"),
            )
            return JSONResponse(content=error_resp.model_dump(), status_code=400)

        if rpc.method == A2A_METHOD_SEND:
            return await _handle_task_send(rpc)
        elif rpc.method == "tasks/get":
            return await _handle_task_get(rpc)
        else:
            error_resp = JsonRpcResponse(
                id=rpc.id,
                error=JsonRpcError(code=-32601, message=f"Method not found: {rpc.method}"),
            )
            return JSONResponse(content=error_resp.model_dump())

    return a2a_app


async def _handle_task_send(rpc: JsonRpcRequest) -> JSONResponse:
    """Process a tasks/send request."""
    message = rpc.params.get("message", "")
    context = rpc.params.get("context", {})
    task_id = rpc.params.get("task_id") or str(uuid.uuid4())

    start = time.monotonic()

    if _invoke_handler:
        try:
            result = await _invoke_handler(message, context)
            output = result.get("output", "")
            tokens = result.get("tokens", 0)
        except Exception as e:
            logger.error("Invoke handler failed: %s", e)
            output = ""
            tokens = 0
            error_resp = JsonRpcResponse(
                id=rpc.id,
                error=JsonRpcError(code=-32000, message=str(e)),
            )
            return JSONResponse(content=error_resp.model_dump(), status_code=500)
    else:
        output = f"Stub response for: {message[:100]}"
        tokens = 0

    latency_ms = int((time.monotonic() - start) * 1000)
    task_result = TaskResult(
        task_id=task_id,
        status="completed",
        output=output,
        tokens=tokens,
        latency_ms=latency_ms,
    )
    _tasks[task_id] = task_result

    resp = JsonRpcResponse(id=rpc.id, result=task_result.model_dump())
    return JSONResponse(content=resp.model_dump())


async def _handle_task_get(rpc: JsonRpcRequest) -> JSONResponse:
    """Retrieve a previously submitted task."""
    task_id = rpc.params.get("task_id", "")
    task = _tasks.get(task_id)
    if not task:
        error_resp = JsonRpcResponse(
            id=rpc.id,
            error=JsonRpcError(code=-32001, message=f"Task not found: {task_id}"),
        )
        return JSONResponse(content=error_resp.model_dump(), status_code=404)

    resp = JsonRpcResponse(id=rpc.id, result=task.model_dump())
    return JSONResponse(content=resp.model_dump())
