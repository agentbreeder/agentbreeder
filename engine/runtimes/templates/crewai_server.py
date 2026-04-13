"""AgentBreeder server wrapper for CrewAI agents.

This file is copied into the agent container at build time.
It wraps any CrewAI crew as a FastAPI server with /invoke and /health endpoints.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

import json

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")


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
    """Dynamically load the CrewAI crew from crew.py or agent.py."""
    sys.path.insert(0, "/app")

    # Try crew.py first, then agent.py
    for module_name in ("crew", "agent"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue

        # Look for crew attr first, then agent, then app
        for attr_name in ("crew", "agent", "app"):
            if hasattr(module, attr_name):
                return getattr(module, attr_name)

    msg = (
        "Could not find a crew object in crew.py or agent.py. "
        "Export a Crew instance as 'crew', 'agent', or 'app'."
    )
    raise AttributeError(msg)


# Load crew at startup
_crew = None


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """FastAPI lifespan context manager for startup and shutdown."""
    global _crew  # noqa: PLW0603
    logger.info("Loading CrewAI crew...")
    _crew = _load_agent()
    logger.info("CrewAI crew loaded successfully")

    # Apply model config from env vars if the crew has agents
    agent_model = os.getenv("AGENT_MODEL")
    agent_temperature_str = os.getenv("AGENT_TEMPERATURE")
    agent_temperature = float(agent_temperature_str) if agent_temperature_str else None

    if agent_model and hasattr(_crew, "agents"):
        try:
            from crewai import LLM

            llm_kwargs: dict[str, Any] = {"model": agent_model}
            if agent_temperature is not None:
                llm_kwargs["temperature"] = agent_temperature
            override_llm = LLM(**llm_kwargs)
            for agent in _crew.agents:
                agent.llm = override_llm
            logger.info(
                "Applied model override to %d agent(s): model=%s temperature=%s",
                len(_crew.agents),
                agent_model,
                agent_temperature,
            )
        except Exception:
            logger.warning("Could not apply AGENT_MODEL override — proceeding with crew defaults")

    yield


app = FastAPI(
    title="AgentBreeder Agent",
    description="Deployed by AgentBreeder",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy" if _crew is not None else "loading",
        agent_name=os.getenv("AGENT_NAME", "unknown"),
        version=os.getenv("AGENT_VERSION", "0.1.0"),
    )


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    if _crew is None:
        raise HTTPException(status_code=503, detail="Crew not loaded yet")

    try:
        result = await _run_crew(request.input)
        return InvokeResponse(output=result)
    except Exception as e:
        logger.exception("Crew invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _run_crew(input_data: dict[str, Any]) -> str:
    """Run the CrewAI crew, wrapping the synchronous kickoff in a thread."""
    if hasattr(_crew, "kickoff"):
        return await asyncio.to_thread(_crew.kickoff, inputs=input_data)
    else:
        msg = "Crew object does not have a kickoff method"
        raise TypeError(msg)


@app.post("/stream")
async def stream(request: InvokeRequest) -> StreamingResponse:
    """Stream CrewAI execution as Server-Sent Events."""
    if _crew is None:
        raise HTTPException(status_code=503, detail="Crew not loaded yet")
    return StreamingResponse(
        _stream_crew(request.input),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_crew(input_data: dict[str, Any]) -> Any:
    """Async generator that yields SSE-formatted strings for each CrewAI step."""
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _step_callback(step_output: Any) -> None:
        payload: dict[str, Any] = {}
        if hasattr(step_output, "task") and hasattr(step_output.task, "description"):
            payload["task"] = step_output.task.description
        if hasattr(step_output, "result"):
            payload["result"] = str(step_output.result)
        loop.call_soon_threadsafe(queue.put_nowait, ("step", payload))

    _DONE = object()

    async def _run() -> None:
        try:
            if hasattr(_crew, "akickoff"):
                result = await _crew.akickoff(inputs=input_data, step_callback=_step_callback)
            elif hasattr(_crew, "kickoff"):
                result = await asyncio.to_thread(_crew.kickoff, inputs=input_data)
            else:
                raise TypeError("Crew object does not have akickoff or kickoff method")
            final_raw = getattr(result, "raw", str(result))
            queue.put_nowait(("result", {"output": final_raw}))
        except Exception as exc:  # noqa: BLE001
            queue.put_nowait(("error", {"detail": str(exc)}))
        finally:
            queue.put_nowait(_DONE)

    task = asyncio.create_task(_run())
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            event_type, payload = item
            yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
