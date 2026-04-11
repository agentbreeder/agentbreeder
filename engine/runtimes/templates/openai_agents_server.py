"""AgentBreeder server wrapper for OpenAI Agents SDK agents.

This file is copied into the agent container at build time.
It wraps any OpenAI Agents SDK agent as a FastAPI server with /invoke, /stream, and /health
endpoints.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")

app = FastAPI(
    title="AgentBreeder Agent",
    description="Deployed by AgentBreeder — OpenAI Agents SDK runtime",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
)


class InvokeRequest(BaseModel):
    input: str
    config: dict[str, Any] | None = None


class InvokeResponse(BaseModel):
    output: str
    agent: str | None = None  # which agent produced final output
    handoffs: list[str] = []  # agents visited during handoffs
    metadata: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _load_agent() -> Any:
    """Dynamically load the agent from agent.py or main.py."""
    sys.path.insert(0, "/app")

    # Try agent.py first, then main.py
    for module_name in ("agent", "main"):
        try:
            module = importlib.import_module(module_name)
            break
        except ImportError:
            continue
    else:
        msg = "Could not import 'agent' or 'main' module. Ensure agent.py or main.py exists."
        raise ImportError(msg)

    # Look for common OpenAI Agents SDK exports
    for attr_name in ("agent", "app", "triage_agent", "runner"):
        if hasattr(module, attr_name):
            return getattr(module, attr_name)

    msg = (
        "agent.py (or main.py) must export one of: 'agent', 'app', 'triage_agent', or 'runner'. "
        "This should be an openai-agents Agent instance."
    )
    raise AttributeError(msg)


# Load agent at startup
_agent = None
_tracer = None


@app.on_event("startup")
async def startup() -> None:
    global _agent, _tracer  # noqa: PLW0603
    logger.info("Loading OpenAI Agents SDK agent...")
    _agent = _load_agent()
    logger.info("Agent loaded successfully")

    try:
        from _tracing import init_tracing

        _tracer = init_tracing()
    except ImportError:
        pass

    # Set the default OpenAI API key so nested runner contexts don't lose it
    import os as _os

    from agents import set_default_openai_key

    api_key = _os.getenv("OPENAI_API_KEY")
    if api_key:
        set_default_openai_key(api_key)
        logger.info("Default OpenAI API key configured")


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
        return await _run_agent(request.input, request.config or {})
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _run_agent(input_text: str, config: dict[str, Any]) -> InvokeResponse:
    """Run the OpenAI Agents SDK agent and extract handoff chain."""
    from agents import HandoffOutputItem, Runner

    result = await Runner.run(_agent, input_text)

    # Extract handoff chain and last agent from result items
    handoffs: list[str] = []
    last_agent: str | None = None
    for item in result.new_items:
        if isinstance(item, HandoffOutputItem):
            if hasattr(item, "target_agent") and item.target_agent:
                handoffs.append(
                    item.target_agent.name
                    if hasattr(item.target_agent, "name")
                    else str(item.target_agent)
                )
            else:
                handoffs.append(str(item))
        if hasattr(item, "agent") and item.agent:
            last_agent = item.agent.name if hasattr(item.agent, "name") else str(item.agent)

    return InvokeResponse(
        output=result.final_output,
        agent=last_agent,
        handoffs=handoffs,
    )


@app.post("/stream")
async def stream(request: InvokeRequest) -> StreamingResponse:
    """Stream agent output using SSE. Emits events on agent updates and handoffs."""

    from collections.abc import AsyncIterator

    async def event_stream() -> AsyncIterator[str]:
        from agents import Runner

        result = Runner.run_streamed(_agent, request.input)
        async for event in result.stream_events():
            event_type = type(event).__name__
            data: dict[str, Any] = {"type": event_type}
            if hasattr(event, "agent") and event.agent:
                data["agent"] = (
                    event.agent.name if hasattr(event.agent, "name") else str(event.agent)
                )
            if hasattr(event, "delta"):
                data["delta"] = event.delta
            yield f"data: {json.dumps(data)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
