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

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel


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
