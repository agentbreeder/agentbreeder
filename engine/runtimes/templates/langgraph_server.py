"""AgentBreeder server wrapper for LangGraph agents.

This file is copied into the agent container at build time.
It wraps any LangGraph agent as a FastAPI server with /invoke, /resume, and /health endpoints.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import uuid
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
    thread_id: str | None = None


class InvokeResponse(BaseModel):
    output: Any
    metadata: dict[str, Any] | None = None
    thread_id: str | None = None


class ResumeRequest(BaseModel):
    thread_id: str
    human_input: Any  # The human response


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _get_checkpointer() -> Any:
    """Select and return the appropriate checkpointer based on environment.

    Uses AsyncPostgresSaver when DATABASE_URL is set, otherwise falls back
    to in-memory MemorySaver. Imports are kept inside this function to avoid
    import errors in containers that may not have all dependencies installed.
    """
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            from langgraph.checkpoint.postgres.aio import (
                AsyncPostgresSaver,
            )

            checkpointer = AsyncPostgresSaver.from_conn_string(database_url)
            logger.info("Using AsyncPostgresSaver checkpointer (DATABASE_URL is set)")
            return checkpointer
        except ImportError:
            logger.warning(
                "DATABASE_URL is set but langgraph-checkpoint-postgres is not installed. "
                "Falling back to MemorySaver."
            )

    from langgraph.checkpoint.memory import MemorySaver

    logger.info("Using MemorySaver checkpointer (no DATABASE_URL)")
    return MemorySaver()


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

            # Warn if an uncompiled StateGraph is exported — AgentBreeder will compile it
            # but the user may lose custom compilation options.
            try:
                from langgraph.graph import StateGraph

                if isinstance(obj, StateGraph):
                    logger.warning(
                        "agent.py exports an uncompiled StateGraph. "
                        "AgentBreeder will compile it, but you may lose custom"
                        " compilation options. "
                        "Consider exporting a compiled graph: 'graph = workflow.compile()'."
                    )
            except ImportError:
                pass  # langgraph not available yet at import time — skip check

            return obj

    msg = (
        "agent.py must export one of: 'graph', 'app', 'workflow', or 'agent'. "
        "This should be a compiled LangGraph graph or a runnable."
    )
    raise AttributeError(msg)


# Module-level references populated during startup
_agent = None
_checkpointer = None
_tracer = None


@app.on_event("startup")
async def startup() -> None:
    global _agent, _checkpointer, _tracer  # noqa: PLW0603
    logger.info("Loading agent...")

    try:
        from _tracing import init_tracing

        _tracer = init_tracing()
    except ImportError:
        pass

    raw_agent = _load_agent()
    checkpointer = _get_checkpointer()

    # Run checkpointer setup if supported (AsyncPostgresSaver needs this to
    # create LangGraph's checkpoint tables; MemorySaver does not have setup()).
    if hasattr(checkpointer, "setup"):
        logger.info("Running checkpointer setup...")
        await checkpointer.setup()

    # Compile the graph with the checkpointer only when it is still a StateGraph.
    # If agent.py already exported a compiled graph, compile() won't be present.
    try:
        from langgraph.graph import StateGraph

        if isinstance(raw_agent, StateGraph):
            logger.info("Compiling StateGraph with checkpointer...")
            _agent = raw_agent.compile(checkpointer=checkpointer)
        else:
            _agent = raw_agent
    except ImportError:
        # langgraph not importable here — fall back to the object as-is
        _agent = raw_agent

    _checkpointer = checkpointer
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

    # Resolve or generate a thread_id for this invocation.
    thread_id = request.thread_id or str(uuid.uuid4())

    try:
        # Merge caller-supplied config with the thread_id configurable.
        base_config: dict[str, Any] = request.config or {}
        configurable = base_config.get("configurable", {})
        configurable["thread_id"] = thread_id
        config: dict[str, Any] = {**base_config, "configurable": configurable}

        input_data = request.input
        result = await _run_agent(input_data, config)

        # Check whether the graph paused at a HITL interrupt breakpoint.
        if hasattr(_agent, "aget_state"):
            state = await _agent.aget_state(config)
            if state.next:  # Pending nodes mean the graph is interrupted.
                return InvokeResponse(
                    output={
                        "status": "interrupted",
                        "thread_id": thread_id,
                        "awaiting": list(state.next),
                    },
                    thread_id=thread_id,
                    metadata={"interrupted": True},
                )

        return InvokeResponse(output=result, thread_id=thread_id)
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/resume", response_model=InvokeResponse)
async def resume(request: ResumeRequest) -> InvokeResponse:
    """Resume a paused HITL graph with human input."""
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    config = {"configurable": {"thread_id": request.thread_id}}
    try:
        from langgraph.types import Command

        result = await _agent.ainvoke(Command(resume=request.human_input), config=config)
        return InvokeResponse(output=result, thread_id=request.thread_id)
    except Exception as e:
        logger.exception("Agent resume failed")
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
