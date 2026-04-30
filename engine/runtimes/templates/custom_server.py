"""AgentBreeder thin wrapper server for custom (BYO) framework agents.

This file is copied into the agent container at build time when the user has not
provided their own server.py. It wraps any Python agent as a FastAPI server with
/invoke and /health endpoints.

Supported agent.py exports (checked in order):
  - agent    — most common name
  - app      — also common
  - run      — callable function
  - handler  — Lambda-style handler

The agent object (or function) may be:
  - async callable / has ainvoke / has arun
  - sync callable / has invoke / has run

InvokeRequest.input accepts both dict and str because the underlying framework
is unknown at build time.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")


def _verify_auth(authorization: str | None = Header(default=None)) -> None:
    """Bearer-token auth for protected endpoints.

    Disabled (no-op) when AGENT_AUTH_TOKEN env var is unset/empty so local dev
    works without ceremony. /health is intentionally NOT protected so Cloud Run
    and k8s liveness probes can hit it without credentials.
    """
    expected = os.getenv("AGENT_AUTH_TOKEN", "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    presented = authorization.removeprefix("Bearer ").strip()
    if presented != expected:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


app = FastAPI(
    title="AgentBreeder Agent",
    description="Deployed by AgentBreeder (custom runtime)",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
)


class InvokeRequest(BaseModel):
    input: dict[str, Any] | str
    config: dict[str, Any] | None = None


class ToolCall(BaseModel):
    """Structured record of a single tool invocation (#215).

    Custom (BYO) agents have no standard mechanism to surface tool-call
    telemetry, so the field is always present but typically empty.  Authors
    can opt in by attaching a ``tool_calls`` attribute or ``__tool_history__``
    key to their result.
    """

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    duration_ms: int = 0
    started_at: str = ""


class InvokeResponse(BaseModel):
    output: Any
    metadata: dict[str, Any] | None = None
    history: list[ToolCall] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _load_agent() -> Any:
    """Dynamically load the agent from agent.py or main.py.

    Tries agent.py first, then main.py. Within each module looks for
    attributes named: agent, app, run, handler.
    """
    sys.path.insert(0, "/app")

    module = None
    for module_name in ("agent", "main"):
        try:
            module = importlib.import_module(module_name)
            logger.info("Loaded agent from %s.py", module_name)
            break
        except ImportError:
            continue

    if module is None:
        msg = (
            "Could not import agent.py or main.py. "
            "Ensure one of these files exists in your agent directory."
        )
        raise ImportError(msg)

    for attr_name in ("agent", "app", "run", "handler"):
        if hasattr(module, attr_name):
            obj = getattr(module, attr_name)
            logger.info("Using '%s' attribute as the agent entry point", attr_name)
            return obj

    msg = (
        "agent.py / main.py must export one of: 'agent', 'app', 'run', or 'handler'. "
        "This can be a class instance, a compiled graph, or a callable function."
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


@app.post("/invoke", response_model=InvokeResponse, dependencies=[Depends(_verify_auth)])
async def invoke(request: InvokeRequest) -> InvokeResponse:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    try:
        config = request.config or {}
        result = await _run_agent(request.input, config)
        history = _extract_tool_history(result)
        return InvokeResponse(output=result, history=history)
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _extract_tool_history(result: Any) -> list[ToolCall]:
    """Pull a ``tool_history`` / ``tool_calls`` field off a custom agent result, if present.

    Custom (BYO) agents are framework-agnostic, so we cannot capture tool
    telemetry from the framework itself.  We do the only honest thing: if the
    user's agent attaches ``tool_history`` / ``tool_calls`` to its dict
    response, surface it; otherwise return an empty history.
    """
    if not isinstance(result, dict):
        return []
    raw = result.get("tool_history") or result.get("tool_calls") or []
    if not isinstance(raw, list):
        return []
    out: list[ToolCall] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(ToolCall(**entry))
        except Exception:  # noqa: BLE001
            # Tolerate partial / unexpected shapes from BYO agents.
            out.append(
                ToolCall(
                    name=str(entry.get("name", "") or ""),
                    args=entry.get("args", {}) or {},
                    result=str(entry.get("result", "") or ""),
                )
            )
    return out


async def _run_agent(input_data: dict[str, Any] | str, config: dict[str, Any]) -> Any:
    """Run the agent, handling async/sync and multiple calling conventions."""
    result: Any

    # Async methods take priority
    if hasattr(_agent, "ainvoke"):
        result = await _agent.ainvoke(input_data, config=config)
    elif hasattr(_agent, "arun"):
        result = await _agent.arun(input_data)
    elif asyncio.iscoroutinefunction(_agent):
        result = await _agent(input_data)
    # Sync methods — run in a thread to avoid blocking the event loop
    elif hasattr(_agent, "invoke"):
        result = await asyncio.to_thread(_agent.invoke, input_data, config=config)
    elif hasattr(_agent, "run"):
        result = await asyncio.to_thread(_agent.run, input_data)
    elif callable(_agent):
        result = await asyncio.to_thread(_agent, input_data)
    else:
        msg = (
            "Agent does not expose a callable interface. "
            "Expected one of: ainvoke, arun, invoke, run, or __call__."
        )
        raise TypeError(msg)

    # Normalize result to a JSON-serialisable type
    if not isinstance(result, (dict, list, str, int, float, bool, type(None))):
        result = str(result)

    return result
