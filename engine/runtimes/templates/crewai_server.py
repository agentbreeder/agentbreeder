"""AgentBreeder server wrapper for CrewAI agents.

This file is copied into the agent container at build time.
It wraps any CrewAI crew as a FastAPI server with /invoke and /health endpoints.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


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


# Capture engine.tool_bridge at import time using sys.modules.get so that test stubs
# injected via patch.dict(sys.modules) are picked up correctly (the `import a.b as x`
# form resolves through the parent-package attribute and bypasses sys.modules overrides).
try:
    import importlib as _importlib

    _engine_tb = _importlib.import_module("engine.tool_bridge")
except ImportError:
    _engine_tb = None  # type: ignore[assignment]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")

# Model string prefixes that route through LiteLLM instead of the native SDK.
_LITELLM_PREFIXES = (
    "ollama/",
    "groq/",
    "bedrock/",
    "openai/",
    "anthropic/",
    "huggingface/",
    "vertex_ai/",
    "azure/",
    "cohere/",
    "mistral/",
    "together_ai/",
    "replicate/",
)

app = FastAPI(
    title="AgentBreeder Agent",
    description="Deployed by AgentBreeder",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
)


class InvokeRequest(BaseModel):
    input: dict[str, Any]
    config: dict[str, Any] | None = None


class ToolCall(BaseModel):
    """Structured record of a single tool invocation (#215).

    Shared shape across all runtime templates so the dashboard playground can
    render a tool-call timeline without regex-scraping message bodies.
    """

    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    duration_ms: int = 0
    started_at: str = ""


class InvokeResponse(BaseModel):
    output: Any
    mode: str | None = None
    metadata: dict[str, Any] | None = None
    output_schema_errors: list[str] | None = None
    # Structured tool-call timeline (#215). CrewAI does not surface per-tool
    # telemetry through ``Crew.kickoff`` natively, so this is best-effort and
    # may be empty for runs that did not use callbacks to capture tool steps.
    history: list[ToolCall] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _load_agent() -> Any:
    """Dynamically load the CrewAI crew module from crew.py or agent.py."""
    sys.path.insert(0, "/app")

    for module_name in ("crew", "agent"):
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue

    msg = (
        "Could not find crew.py or agent.py. Export a Crew instance as 'crew', 'agent', or 'app'."
    )
    raise AttributeError(msg)


def _detect_mode(module: Any) -> tuple[str, Any]:
    """Detect whether the module exposes a Flow or a Crew."""
    if hasattr(module, "flow"):
        return "flow", module.flow
    if hasattr(module, "crew"):
        return "crew", module.crew
    msg = "Module exports neither 'flow' nor 'crew' — cannot determine dispatch mode"
    raise RuntimeError(msg)


async def _dispatch(obj: Any, mode: str, inputs: dict[str, Any]) -> Any:
    """Dispatch inputs to a Flow or Crew object."""
    if mode == "flow":
        return await obj.kickoff_async(inputs=inputs)
    return await asyncio.to_thread(obj.kickoff, inputs=inputs)


def _validate_output(output: str, *, schema: dict[str, Any] | None) -> list[str] | None:
    """Validate a JSON output string against a JSON Schema dict.

    Returns None on success, or a list of error strings on failure.
    """
    if schema is None:
        return None
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError) as exc:
        return [f"Output is not valid JSON: {exc}"]

    errors: list[str] = []
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for field in required:
        if field not in data:
            errors.append(f"Missing required field: '{field}'")

    for field, field_schema in properties.items():
        if field not in data:
            continue
        expected_type = field_schema.get("type")
        if expected_type and not _check_json_type(data[field], expected_type):
            errors.append(
                f"Field '{field}' has wrong type: expected {expected_type}, "
                f"got {type(data[field]).__name__}"
            )

    return errors if errors else None


def _check_json_type(value: Any, json_type: str) -> bool:
    """Check whether a Python value matches a JSON Schema type string."""
    _type_map: dict[str, type | tuple[type, ...]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }
    expected = _type_map.get(json_type)
    if expected is None:
        return True
    if json_type == "integer" and isinstance(value, bool):
        return False
    return isinstance(value, expected)


# Module-level globals — set at startup, reused for all requests
_module: Any = None
_crew: Any = None
_crewai_tools: list[Any] = []


@app.on_event("startup")
async def startup() -> None:
    global _module, _crew, _crewai_tools  # noqa: PLW0603

    # --- Tool wiring ---
    tools_json = os.getenv("AGENT_TOOLS_JSON", "[]")
    try:
        raw_tools = json.loads(tools_json)
        if raw_tools and _engine_tb is not None:
            _crewai_tools = _engine_tb.to_crewai_tools(raw_tools) or []
            logger.info("Loaded %d CrewAI tool(s)", len(_crewai_tools))
    except Exception:
        logger.exception("Failed to load CrewAI tools — proceeding with no tools")
        _crewai_tools = []

    # --- Load agent module if not already set ---
    if _module is None:
        logger.info("Loading CrewAI crew...")
        try:
            _module = _load_agent()
        except (AttributeError, ImportError, ModuleNotFoundError):
            logger.warning("Could not load agent module — proceeding without agent")

    # --- Extract crew object if not already set ---
    if _crew is None and _module is not None:
        try:
            _, _crew = _detect_mode(_module)
        except RuntimeError:
            pass

    # --- Inject tools and model config into crew agents ---
    _agent_model = os.getenv("AGENT_MODEL")
    _agent_temperature = os.getenv("AGENT_TEMPERATURE")
    if _crew is not None:
        for agent in getattr(_crew, "agents", []):
            if _crewai_tools:
                existing = list(getattr(agent, "tools", None) or [])
                agent.tools = existing + list(_crewai_tools)
            if _agent_model and hasattr(agent, "llm") and agent.llm is not None:
                try:
                    agent.llm.model = _agent_model
                    if _agent_temperature is not None:
                        agent.llm.temperature = float(_agent_temperature)
                    # For Ollama models, set base_url so LiteLLM routes correctly.
                    # Other LiteLLM-prefixed models (groq/, bedrock/, etc.) route
                    # via their own API keys — no custom base_url needed.
                    if _agent_model.startswith("ollama/"):
                        _ollama_url = os.getenv(
                            "OLLAMA_BASE_URL", "http://agentbreeder-ollama:11434"
                        )
                        agent.llm.base_url = _ollama_url
                        logger.info("Configured CrewAI agent LLM for Ollama: %s", _ollama_url)
                    elif _agent_model.startswith(_LITELLM_PREFIXES):
                        logger.info("LiteLLM-prefixed model; routing via provider API key")
                except Exception:
                    pass

    if _module is not None or _crew is not None:
        logger.info("CrewAI server ready")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="healthy" if (_module is not None or _crew is not None) else "loading",
        agent_name=os.getenv("AGENT_NAME", "unknown"),
        version=os.getenv("AGENT_VERSION", "0.1.0"),
    )


@app.post("/stream", dependencies=[Depends(_verify_auth)])
async def stream(request: InvokeRequest) -> StreamingResponse:
    """Stream CrewAI execution as Server-Sent Events."""
    if _crew is None and _module is None:
        raise HTTPException(status_code=503, detail="Crew not loaded yet")
    active_crew = _crew
    if active_crew is None and _module is not None:
        try:
            _, active_crew = _detect_mode(_module)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return StreamingResponse(
        _stream_crew_sse(active_crew, request.input),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_crew_sse(crew: Any, inputs: dict[str, Any]) -> AsyncGenerator[str, None]:
    """Async generator that streams CrewAI execution as SSE events."""
    try:
        if hasattr(crew, "akickoff"):
            # Streaming path: use akickoff with a step_callback
            import queue as _queue

            step_q: _queue.Queue[Any] = _queue.Queue()

            def _step_cb(step_output: Any) -> None:
                step_q.put(step_output)

            # Run akickoff in a task so we can drain the step queue concurrently
            async def _run() -> Any:
                return await crew.akickoff(inputs=inputs, step_callback=_step_cb)

            task = asyncio.create_task(_run())

            # Drain steps until the task completes
            while not task.done():
                try:
                    step = step_q.get_nowait()
                    description = getattr(getattr(step, "task", None), "description", "")
                    result = getattr(step, "result", "")
                    payload = json.dumps({"description": description, "result": result})
                    yield f"event: step\ndata: {payload}\n\n"
                except _queue.Empty:
                    await asyncio.sleep(0)

            # Drain any remaining steps
            while not step_q.empty():
                step = step_q.get_nowait()
                description = getattr(getattr(step, "task", None), "description", "")
                result = getattr(step, "result", "")
                payload = json.dumps({"description": description, "result": result})
                yield f"event: step\ndata: {payload}\n\n"

            crew_result = await task
        else:
            # Fallback: sync kickoff in thread pool
            crew_result = await asyncio.to_thread(crew.kickoff, inputs=inputs)

        raw = getattr(crew_result, "raw", str(crew_result))
        yield f"event: result\ndata: {json.dumps({'output': raw})}\n\n"
    except Exception as exc:  # noqa: BLE001
        yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    yield "data: [DONE]\n\n"


@app.post("/invoke", response_model=InvokeResponse, dependencies=[Depends(_verify_auth)])
async def invoke(request: InvokeRequest) -> InvokeResponse:
    if _module is None and _crew is None:
        raise HTTPException(status_code=503, detail="Crew not loaded yet")

    try:
        active_module = _module
        if active_module is None:
            # _crew was set directly (e.g., test harness)
            class _SyntheticModule:
                pass

            active_module = _SyntheticModule()
            active_module.crew = _crew

        mode, obj = _detect_mode(active_module)
        result = await _dispatch(obj, mode, request.input)
        output_schema = (request.config or {}).get("output_schema")
        schema_errors = _validate_output(str(result), schema=output_schema)
        history = _extract_tool_history(result)
        return InvokeResponse(
            output=result,
            mode=mode,
            output_schema_errors=schema_errors,
            history=history,
        )
    except Exception as e:
        logger.exception("Crew invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


def _extract_tool_history(crew_result: Any) -> list[ToolCall]:
    """Best-effort extraction of tool calls from a CrewOutput (#215).

    CrewAI does not surface tool-call telemetry through the standard
    ``CrewOutput`` shape — per-tool spans live on ``tasks_output`` only when a
    callback captures them.  Walk whatever metadata happens to be present so
    crews that do attach step callbacks get a populated history; everything
    else returns an empty list.
    """
    history: list[ToolCall] = []
    tasks_output = getattr(crew_result, "tasks_output", None) or []
    for task in tasks_output:
        # Some CrewAI versions expose a ``tools_used`` / ``tool_calls`` list
        # on the per-task output; tolerate both shapes.
        for attr in ("tool_calls", "tools_used"):
            entries = getattr(task, attr, None)
            if not entries:
                continue
            for entry in entries:
                name = ""
                args: dict[str, Any] = {}
                result_val = ""
                if isinstance(entry, dict):
                    name = str(entry.get("tool", entry.get("name", "")))
                    args = entry.get("input", entry.get("args", {})) or {}
                    if not isinstance(args, dict):
                        args = {"raw": str(args)}
                    result_val = str(entry.get("result", entry.get("output", "")))
                else:
                    name = str(getattr(entry, "tool", getattr(entry, "name", "")))
                    raw_args = getattr(entry, "input", None) or getattr(entry, "args", None)
                    if isinstance(raw_args, dict):
                        args = raw_args
                    elif raw_args is not None:
                        args = {"raw": str(raw_args)}
                    result_val = str(
                        getattr(entry, "result", "") or getattr(entry, "output", "") or ""
                    )
                if not name:
                    continue
                history.append(ToolCall(name=name, args=args, result=result_val))
    return history
