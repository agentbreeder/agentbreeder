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

from engine.tool_bridge import to_crewai_tools
from engine.config_parser import ToolRef

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")


class InvokeRequest(BaseModel):
    input: dict[str, Any]
    config: dict[str, Any] | None = None


class InvokeResponse(BaseModel):
    output: Any
    mode: str = "crew"
    metadata: dict[str, Any] | None = None
    output_schema_errors: list[str] | None = None


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _detect_mode(agent_module: Any) -> tuple[str, Any]:
    """Return (mode, object) for the agent module.

    Prefers `flow` over `crew` when both are present.
    Raises RuntimeError if neither attribute is found.
    """
    if hasattr(agent_module, "flow"):
        return "flow", agent_module.flow
    if hasattr(agent_module, "crew"):
        return "crew", agent_module.crew
    raise RuntimeError(
        "Agent module exposes neither 'flow' nor 'crew' attribute. "
        "Define one of: flow = MyFlow(), crew = Crew(...)"
    )


async def _dispatch(obj: Any, mode: str, input_data: dict[str, Any]) -> str:
    """Dispatch an invocation to either a Flow or a Crew."""
    if mode == "flow":
        result = await obj.kickoff_async(inputs=input_data)
        return str(result)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: obj.kickoff(inputs=input_data))
    return str(result)


def _load_agent() -> Any:
    """Dynamically load the CrewAI crew or flow from crew.py or agent.py."""
    sys.path.insert(0, "/app")

    # Try crew.py first, then agent.py
    for module_name in ("crew", "agent"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue

        # Try flow/crew detection first
        try:
            _mode_val, obj = _detect_mode(module)
            # Also set module-level _mode and _agent_obj
            globals()["_mode"] = _mode_val
            globals()["_agent_obj"] = obj
            return obj
        except RuntimeError:
            pass

        # Fallback: look for crew attr first, then agent, then app
        for attr_name in ("crew", "agent", "app"):
            if hasattr(module, attr_name):
                globals()["_mode"] = "crew"
                globals()["_agent_obj"] = getattr(module, attr_name)
                return getattr(module, attr_name)

    msg = (
        "Could not find a crew object in crew.py or agent.py. "
        "Export a Crew instance as 'crew', 'agent', or 'app'."
    )
    raise AttributeError(msg)


# Load crew at startup
_crew = None
_crewai_tools: list = []
_mode: str = "crew"
_agent_obj: Any = None
_output_schema: dict[str, Any] | None = None


def _validate_output(
    output: str, schema: dict[str, Any] | None
) -> list[str] | None:
    """Validate output against a JSON Schema dict. Returns None if valid or no schema."""
    if schema is None:
        return None
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        return [f"Output is not valid JSON: {exc}"]
    try:
        import jsonschema
        validator = jsonschema.Draft7Validator(schema)
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if not errors:
            return None
        return [f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors]
    except Exception as exc:
        return [f"Schema validation error: {exc}"]


async def startup() -> None:
    """Wire tool bridge into the loaded crew.

    This is called by the lifespan after _load_agent(), and can also be
    called directly in tests (with _crew pre-set) to exercise the wiring.
    """
    global _crewai_tools, _output_schema  # noqa: PLW0603
    # Load output schema if provided
    output_schema_env = os.getenv("AGENT_OUTPUT_SCHEMA")
    if output_schema_env:
        try:
            _output_schema = json.loads(output_schema_env)
        except json.JSONDecodeError:
            logger.warning("AGENT_OUTPUT_SCHEMA is not valid JSON — output validation disabled")
    tools_json = os.getenv("AGENT_TOOLS_JSON", "[]")
    try:
        raw_tools = json.loads(tools_json)
        tool_refs = [ToolRef(**t) for t in raw_tools]
        _crewai_tools = to_crewai_tools(tool_refs)
        if _crewai_tools:
            logger.info("Loaded %d CrewAI tool(s)", len(_crewai_tools))
            if hasattr(_crew, "agents"):
                for agent in _crew.agents:
                    if hasattr(agent, "tools") and isinstance(agent.tools, list):
                        agent.tools = list(agent.tools) + _crewai_tools
                        logger.debug(
                            "Injected %d tool(s) into CrewAI agent %r",
                            len(_crewai_tools),
                            getattr(agent, "role", "<unknown>"),
                        )
    except Exception:
        logger.exception("Failed to load CrewAI tools -- proceeding with no tools")
        _crewai_tools = []


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

    # --- Tool bridge ---
    await startup()

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

    obj = _agent_obj if _agent_obj is not None else _crew
    try:
        result = await _dispatch(obj, _mode, request.input)
        schema_errors = _validate_output(str(result), _output_schema)
        return InvokeResponse(output=result, mode=_mode, output_schema_errors=schema_errors)
    except Exception as e:
        logger.exception("Crew invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/stream")
async def stream(request: InvokeRequest) -> StreamingResponse:
    """Stream CrewAI execution as Server-Sent Events."""
    if _agent_obj is None and _crew is None:
        raise HTTPException(status_code=503, detail="Crew not loaded yet")
    return StreamingResponse(
        _stream_crew(request.input),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_crew(input_data: dict[str, Any]) -> Any:
    """Async generator that yields SSE-formatted strings for each CrewAI step."""
    # Flow mode: no step callbacks, just stream the final result
    if _mode == "flow" and _agent_obj is not None:
        try:
            result = await _agent_obj.kickoff_async(inputs=input_data)
            yield f"event: result\ndata: {json.dumps({'output': str(result)})}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"
        return

    # Crew mode: use akickoff() with step_callback for streaming
    crew = _crew or _agent_obj
    if crew is None:
        return

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
            if hasattr(crew, "akickoff"):
                result = await crew.akickoff(inputs=input_data, step_callback=_step_callback)
            elif hasattr(crew, "kickoff"):
                result = await asyncio.to_thread(crew.kickoff, inputs=input_data)
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
