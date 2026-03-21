"""AgentBreeder server wrapper for LangGraph agents.

This file is copied into the agent container at build time.
It wraps any LangGraph agent as a FastAPI server with /invoke and /health endpoints.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")

app = FastAPI(
    title="AgentBreeder Agent",
    description="Deployed by AgentBreeder",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
)


class InvokeRequest(BaseModel):
    input: dict[str, Any]
    config: dict[str, Any] | None = None


class InvokeResponse(BaseModel):
    output: Any
    metadata: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _load_agent() -> Any:
    """Dynamically load the agent graph from agent.py."""
    sys.path.insert(0, "/app")
    try:
        module = importlib.import_module("agent")
    except ImportError as e:
        logger.error("Failed to import agent module: %s", e)
        raise

    # Look for common LangGraph exports
    for attr_name in ("graph", "app", "workflow", "agent"):
        if hasattr(module, attr_name):
            obj = getattr(module, attr_name)
            # If it's a StateGraph, compile it
            if hasattr(obj, "compile"):
                return obj.compile()
            return obj

    msg = (
        "agent.py must export one of: 'graph', 'app', 'workflow', or 'agent'. "
        "This should be a compiled LangGraph graph or a runnable."
    )
    raise AttributeError(msg)


# Load agent at startup
_agent = None


@app.on_event("startup")
async def startup() -> None:
    global _agent  # noqa: PLW0603
    logger.info("Loading agent...")
    _agent = _load_agent()
    logger.info("Agent loaded successfully")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy" if _agent is not None else "loading",
        agent_name=os.getenv("AGENT_NAME", "unknown"),
        version=os.getenv("AGENT_VERSION", "0.1.0"),
    )


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    try:
        config = request.config or {}
        result = await _run_agent(request.input, config)
        return InvokeResponse(output=result)
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _run_agent(input_data: dict[str, Any], config: dict[str, Any]) -> Any:
    """Run the agent, handling both sync and async graphs."""
    if hasattr(_agent, "ainvoke"):
        return await _agent.ainvoke(input_data, config=config)
    elif hasattr(_agent, "invoke"):
        return _agent.invoke(input_data, config=config)
    else:
        msg = "Agent does not have invoke or ainvoke method"
        raise TypeError(msg)
