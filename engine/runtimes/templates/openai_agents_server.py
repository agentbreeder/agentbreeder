"""Agent Garden server wrapper for OpenAI Agents SDK agents.

This file is copied into the agent container at build time.
It wraps any OpenAI Agents SDK agent as a FastAPI server with /invoke and /health endpoints.
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
logger = logging.getLogger("garden.agent")

app = FastAPI(
    title="Agent Garden Agent",
    description="Deployed by Agent Garden — OpenAI Agents SDK runtime",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
)


class InvokeRequest(BaseModel):
    input: str
    config: dict[str, Any] | None = None


class InvokeResponse(BaseModel):
    output: str
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


@app.on_event("startup")
async def startup() -> None:
    global _agent  # noqa: PLW0603
    logger.info("Loading OpenAI Agents SDK agent...")
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
        result = await _run_agent(request.input, request.config or {})
        return InvokeResponse(output=result)
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _run_agent(input_text: str, config: dict[str, Any]) -> str:
    """Run the OpenAI Agents SDK agent."""
    from agents import Runner

    result = await Runner.run(_agent, input_text)
    return result.final_output
