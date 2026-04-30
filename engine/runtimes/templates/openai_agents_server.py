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
from datetime import UTC, datetime
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


logging.basicConfig(level=logging.INFO)

# Model string prefixes that route through LiteLLM instead of the OpenAI native SDK.
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
logger = logging.getLogger("agentbreeder.agent")

app = FastAPI(
    title="AgentBreeder Agent",
    description="Deployed by AgentBreeder — OpenAI Agents SDK runtime",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
)


class InvokeRequest(BaseModel):
    input: str
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
    output: str
    agent: str | None = None  # which agent produced final output
    handoffs: list[str] = []  # agents visited during handoffs
    metadata: dict[str, Any] | None = None
    # Structured tool-call timeline (#215). Always present, may be empty when
    # the OpenAI Agents SDK does not surface tool-call telemetry for this run.
    history: list[ToolCall] = Field(default_factory=list)


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
_a2a_tools_registered: bool = False  # tracks whether tool_bridge A2A tools were injected


@app.on_event("startup")
async def startup() -> None:
    global _agent, _tracer, _a2a_tools_registered  # noqa: PLW0603
    logger.info("Loading OpenAI Agents SDK agent...")
    _agent = _load_agent()
    logger.info("Agent loaded successfully")

    try:
        from _tracing import init_tracing

        _tracer = init_tracing()
    except ImportError:
        pass

    # --- tool_bridge A2A sub-agent integration ---
    # Register any A2A sub-agent tools from tool_bridge as FunctionTool instances
    # that the OpenAI Agents SDK can call.  This keeps the existing SDK handoff
    # logic untouched while giving agents access to A2A sub-agents.
    tools_json = os.getenv("AGENT_TOOLS_JSON", "[]")
    try:
        raw_tools = json.loads(tools_json)
        if raw_tools and _agent is not None:
            _tb = sys.modules.get("engine.tool_bridge")
            if _tb is None:
                import engine.tool_bridge as _tb  # noqa: PLC0415

            from agents import FunctionTool

            for tool_spec in raw_tools:
                # Each entry in raw_tools is a dict with at least a "name" key.
                tool_name: str = tool_spec.get("name") or ""
                if not tool_name:
                    continue
                tool_description: str = (
                    tool_spec.get("description") or f"A2A sub-agent: {tool_name}"
                )

                # Capture loop variables in the closure.
                def _make_fn(name: str, tb: Any, description: str) -> Any:
                    def _tool_fn(input: str) -> str:  # noqa: A002
                        try:
                            result = tb.execute(name, {"input": input})
                            return str(result)
                        except Exception as exc:  # noqa: BLE001
                            return f"Error calling {name!r}: {exc}"

                    _tool_fn.__name__ = name
                    _tool_fn.__doc__ = description
                    return _tool_fn

                fn = _make_fn(tool_name, _tb, tool_description)
                try:
                    sdk_tool = FunctionTool(fn)
                    if hasattr(_agent, "tools") and isinstance(_agent.tools, list):
                        _agent.tools.append(sdk_tool)
                        logger.info("Registered A2A tool %r on agent", tool_name)
                    else:
                        logger.warning(
                            "Agent has no mutable .tools list; could not register A2A tool %r",
                            tool_name,
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to create FunctionTool for A2A tool %r", tool_name)

            _a2a_tools_registered = True
            logger.info("A2A tool_bridge integration complete")
    except Exception:  # noqa: BLE001
        logger.exception("Failed to register A2A tools from tool_bridge — proceeding without them")

    import os as _os

    agent_model = _os.getenv("AGENT_MODEL", "")

    if agent_model.startswith("ollama/"):
        # Configure OpenAI Agents SDK to use Ollama's OpenAI-compatible endpoint
        from agents import set_default_openai_client
        from openai import AsyncOpenAI

        ollama_base_url = _os.getenv("OLLAMA_BASE_URL", "http://agentbreeder-ollama:11434")
        model_name = agent_model.split("/", 1)[1]  # strip "ollama/" prefix
        ollama_client = AsyncOpenAI(
            base_url=f"{ollama_base_url}/v1",
            api_key="ollama",  # Ollama doesn't require a real key
        )
        set_default_openai_client(ollama_client)
        logger.info(
            "Configured OpenAI Agents SDK to use Ollama: model=%s base_url=%s/v1",
            model_name,
            ollama_base_url,
        )
    else:
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


@app.post("/invoke", response_model=InvokeResponse, dependencies=[Depends(_verify_auth)])
async def invoke(request: InvokeRequest) -> InvokeResponse:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    try:
        return await _run_agent(request.input, request.config or {})
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _run_agent(input_text: str, config: dict[str, Any]) -> InvokeResponse:
    """Run the OpenAI Agents SDK agent and extract handoff chain + tool history."""
    from agents import HandoffOutputItem, Runner

    result = await Runner.run(_agent, input_text)

    # Extract handoff chain, last agent, and structured tool-call history (#215).
    handoffs: list[str] = []
    last_agent: str | None = None
    history = _extract_tool_history(getattr(result, "new_items", []) or [])
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
        history=history,
    )


def _extract_tool_history(new_items: list[Any]) -> list[ToolCall]:
    """Pair tool-call items with their tool-call output items into ``ToolCall`` records.

    The OpenAI Agents SDK emits a sequence of items per run.  Tool calls and
    their outputs appear as ``ToolCallItem`` and ``ToolCallOutputItem``
    (or items with ``raw_item`` carrying matching ``call_id``s).  We pair them
    up by ``call_id`` and produce one ``ToolCall`` per executed tool.

    Tool-call telemetry is best-effort: the SDK does not always expose
    timestamps or per-call durations, so ``started_at`` defaults to the time
    the response was assembled and ``duration_ms`` defaults to 0 when missing.
    """
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    now_iso = datetime.now(UTC).isoformat()

    for item in new_items:
        item_type = type(item).__name__
        raw = getattr(item, "raw_item", None)

        # Match by class name to keep the test stub flexible.
        if item_type in ("ToolCallItem",) or (raw is not None and _looks_like_tool_call(raw)):
            call_id = _extract_call_id(item, raw) or f"_anon_{len(order)}"
            name = _extract_tool_name(item, raw) or "unknown"
            args = _extract_tool_args(item, raw) or {}
            entry = calls.setdefault(
                call_id,
                {"name": name, "args": args, "result": "", "started_at": now_iso},
            )
            entry["name"] = name
            entry["args"] = args
            if call_id not in order:
                order.append(call_id)
        elif item_type in ("ToolCallOutputItem",) or (
            raw is not None and _looks_like_tool_output(raw)
        ):
            call_id = _extract_call_id(item, raw) or (
                order[-1] if order else f"_anon_{len(order)}"
            )
            output = _extract_tool_output(item, raw) or ""
            entry = calls.setdefault(
                call_id,
                {"name": "unknown", "args": {}, "result": "", "started_at": now_iso},
            )
            entry["result"] = output
            if call_id not in order:
                order.append(call_id)

    return [
        ToolCall(
            name=calls[cid].get("name", "unknown"),
            args=calls[cid].get("args", {}) or {},
            result=str(calls[cid].get("result", "") or ""),
            duration_ms=int(calls[cid].get("duration_ms", 0) or 0),
            started_at=str(calls[cid].get("started_at", now_iso)),
        )
        for cid in order
    ]


def _looks_like_tool_call(raw: Any) -> bool:
    rtype = getattr(raw, "type", None) or (raw.get("type") if isinstance(raw, dict) else None)
    return rtype in ("function_call", "tool_call", "function_tool_call")


def _looks_like_tool_output(raw: Any) -> bool:
    rtype = getattr(raw, "type", None) or (raw.get("type") if isinstance(raw, dict) else None)
    return rtype in ("function_call_output", "tool_call_output", "function_tool_call_output")


def _extract_call_id(item: Any, raw: Any) -> str | None:
    for src in (item, raw):
        if src is None:
            continue
        if isinstance(src, dict):
            cid = src.get("call_id") or src.get("id")
        else:
            cid = getattr(src, "call_id", None) or getattr(src, "id", None)
        if cid:
            return str(cid)
    return None


def _extract_tool_name(item: Any, raw: Any) -> str | None:
    for src in (item, raw):
        if src is None:
            continue
        if isinstance(src, dict):
            name = src.get("name") or src.get("tool_name")
        else:
            name = getattr(src, "name", None) or getattr(src, "tool_name", None)
        if name:
            return str(name)
    return None


def _extract_tool_args(item: Any, raw: Any) -> dict[str, Any]:
    for src in (item, raw):
        if src is None:
            continue
        if isinstance(src, dict):
            args = src.get("arguments") or src.get("args") or src.get("input")
        else:
            args = (
                getattr(src, "arguments", None)
                or getattr(src, "args", None)
                or getattr(src, "input", None)
            )
        if args is None:
            continue
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, TypeError):
                return {"raw": args}
        if isinstance(args, dict):
            return args
    return {}


def _extract_tool_output(item: Any, raw: Any) -> str:
    for src in (item, raw):
        if src is None:
            continue
        if isinstance(src, dict):
            out = src.get("output") or src.get("result") or src.get("content")
        else:
            out = (
                getattr(src, "output", None)
                or getattr(src, "result", None)
                or getattr(src, "content", None)
            )
        if out is None:
            continue
        if isinstance(out, (dict, list)):
            try:
                return json.dumps(out)
            except (TypeError, ValueError):
                return str(out)
        return str(out)
    return ""


@app.post("/stream", dependencies=[Depends(_verify_auth)])
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
