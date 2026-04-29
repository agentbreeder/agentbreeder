"""AgentBreeder server wrapper for Claude SDK agents.

This file is copied into the agent container at build time.
It wraps any Claude SDK agent as a FastAPI server with /invoke and /health endpoints.

The agent.py in the container must export one of:
- agent: an anthropic.AsyncAnthropic or anthropic.Anthropic client, or
         an async callable `async def agent(input: str) -> str`, or
         an object with `async def run(input: str) -> str`
- app:   same types as above
- client: same types as above
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
from pydantic import BaseModel

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
    description="Deployed by AgentBreeder",
    version=os.getenv("AGENT_VERSION", "0.1.0"),
)


class InvokeRequest(BaseModel):
    input: str
    config: dict[str, Any] | None = None


class InvokeResponse(BaseModel):
    output: Any
    metadata: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _load_agent() -> Any:
    """Dynamically load the agent from agent.py."""
    sys.path.insert(0, "/app")
    try:
        module = importlib.import_module("agent")
    except ImportError as e:
        logger.error("Failed to import agent module: %s", e)
        raise

    # Look for common Claude SDK exports
    for attr_name in ("agent", "app", "client"):
        if hasattr(module, attr_name):
            return getattr(module, attr_name)

    msg = (
        "agent.py must export one of: 'agent', 'app', or 'client'. "
        "This should be an anthropic.AsyncAnthropic client, an anthropic.Anthropic client, "
        "an async callable, or an object with an async run() method."
    )
    raise AttributeError(msg)


# Module-level globals — set at startup, reused for all requests
_agent = None
_client = None  # AsyncAnthropic client used for /stream; may equal _agent or be separate
_tools: list[Any] = []
_prompt_caching_enabled: bool = False
_thinking_config: dict[str, Any] | None = None


def _get_cache_threshold(model: str) -> int:
    """Return the prompt-caching character threshold for the given model."""
    if "sonnet" in model.lower():
        return 8192
    return 16384


async def _call_client(
    client: Any,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
) -> str:
    """Call the Anthropic messages API with thinking/caching configuration applied."""
    max_tokens = int(os.getenv("AGENT_MAX_TOKENS", "4096"))

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }

    # Apply thinking config (disables temperature)
    if _thinking_config:
        thinking_type = _thinking_config.get("type", "adaptive")
        effort = _thinking_config.get("_effort", "high")
        kwargs["thinking"] = {"type": thinking_type}
        kwargs["output_config"] = {"effort": effort}
        kwargs["betas"] = ["interleaved-thinking-2025-05-14"]

    # Apply system prompt with optional prompt caching
    if system_prompt:
        if _prompt_caching_enabled and len(system_prompt) >= _get_cache_threshold(model):
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            kwargs["system"] = system_prompt

    if _tools:
        kwargs["tools"] = _tools

    response = await client.messages.create(**kwargs)
    return _extract_text(response)


@app.on_event("startup")
async def startup() -> None:
    global _agent, _client, _tools  # noqa: PLW0603

    # --- Tool wiring (done first, independently of agent loading) ---
    tools_json = os.getenv("AGENT_TOOLS_JSON", "[]")
    try:
        raw_tools = json.loads(tools_json)
        if raw_tools:
            # Use sys.modules directly to pick up test-injected stubs
            _tb = sys.modules.get("engine.tool_bridge")
            if _tb is None:
                import engine.tool_bridge as _tb  # noqa: PLC0415

            _tools = _tb.to_claude_tools(raw_tools) or []
            logger.info("Loaded %d Claude tool(s)", len(_tools))
    except Exception:
        logger.exception("Failed to load Claude tools — proceeding with no tools")
        _tools = []

    # --- Load agent (can fail gracefully if tools-only startup) ---
    logger.info("Loading agent...")
    try:
        _agent = _load_agent()
        logger.info("Agent loaded successfully")
    except (AttributeError, ImportError, ModuleNotFoundError):
        logger.warning("Could not load agent module — server will return 503 on /invoke")

    # Set _client for streaming when _agent is an AsyncAnthropic instance
    try:
        import anthropic as _anthropic

        if isinstance(_agent, _anthropic.AsyncAnthropic):
            _client = _agent
    except ImportError:
        pass


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
        result = await _run_agent(request.input)
        return InvokeResponse(output=result)
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


_AGENTIC_LOOP_MAX_ITER: int = 10  # guard against infinite tool-use loops


async def _run_agentic_loop(
    client: Any,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
) -> str:
    """Drive a full agentic tool-use loop for an AsyncAnthropic client.

    Each iteration:
    1. Calls ``client.messages.create(...)``
    2. If any ``tool_use`` blocks are present, executes them via
       ``tool_bridge.execute()`` and appends the ``tool_result`` back to
       *messages* before looping.
    3. Stops when the response contains no ``tool_use`` blocks (i.e. only text
       or an ``end_turn`` stop reason) or after *_AGENTIC_LOOP_MAX_ITER*
       iterations, whichever comes first.

    Args:
        client:        An ``anthropic.AsyncAnthropic`` instance.
        model:         Model identifier string.
        system_prompt: System prompt (may be empty).
        messages:      Mutable message list; the first entry is the user
                       message.  Tool results are appended in-place.

    Returns:
        The final text response from the model.
    """
    # Use sys.modules to pick up test-injected stubs for tool_bridge.
    _tb = sys.modules.get("engine.tool_bridge")
    if _tb is None:
        import engine.tool_bridge as _tb  # noqa: PLC0415

    for _iteration in range(_AGENTIC_LOOP_MAX_ITER):
        max_tokens = int(os.getenv("AGENT_MAX_TOKENS", "4096"))
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if _thinking_config:
            thinking_type = _thinking_config.get("type", "adaptive")
            effort = _thinking_config.get("_effort", "high")
            kwargs["thinking"] = {"type": thinking_type}
            kwargs["output_config"] = {"effort": effort}
            kwargs["betas"] = ["interleaved-thinking-2025-05-14"]
        if system_prompt:
            if _prompt_caching_enabled and len(system_prompt) >= _get_cache_threshold(model):
                kwargs["system"] = [
                    {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
                ]
            else:
                kwargs["system"] = system_prompt
        if _tools:
            kwargs["tools"] = _tools

        response = await client.messages.create(**kwargs)

        # Collect tool_use blocks from this response.
        tool_use_blocks = [b for b in response.content if getattr(b, "type", None) == "tool_use"]

        if not tool_use_blocks:
            # No tool calls — return the final text response.
            return _extract_text(response)

        if _iteration == _AGENTIC_LOOP_MAX_ITER - 1:
            # Safety guard: hit max iterations; return whatever text we have.
            logger.warning(
                "Agentic loop reached max iterations (%d); returning partial response.",
                _AGENTIC_LOOP_MAX_ITER,
            )
            return _extract_text(response)

        # Append the assistant turn (including tool_use blocks) to messages.
        messages.append({"role": "assistant", "content": response.content})

        # Execute each tool and collect tool_result blocks.
        tool_results: list[dict[str, Any]] = []
        for block in tool_use_blocks:
            tool_name: str = block.name
            tool_input: dict[str, Any] = block.id and block.input or {}
            try:
                tool_output = _tb.execute(tool_name, tool_input)
                content = str(tool_output)
                is_error = False
            except Exception as exc:  # noqa: BLE001
                logger.error("Tool %r execution error: %s", tool_name, exc)
                content = f"Error: {exc}"
                is_error = True

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    "is_error": is_error,
                }
            )

        # Append all tool results as a single user message.
        messages.append({"role": "user", "content": tool_results})

    # Should never reach here because the loop body always returns or loops.
    return ""  # pragma: no cover


async def _run_agent(input_data: str) -> str:
    """Run the agent, dispatching based on the type of object loaded from agent.py."""
    import anthropic

    model = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
    system_prompt = os.getenv("AGENT_SYSTEM_PROMPT", "")
    messages: list[dict[str, Any]] = [{"role": "user", "content": input_data}]

    # anthropic.AsyncAnthropic client — full agentic tool-use loop
    if isinstance(_agent, anthropic.AsyncAnthropic):
        return await _run_agentic_loop(_agent, model, system_prompt, messages)

    # anthropic.Anthropic (sync) client — run in thread to avoid blocking
    if isinstance(_agent, anthropic.Anthropic):
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 1024,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if _tools:
            kwargs["tools"] = _tools
        response = await asyncio.to_thread(_agent.messages.create, **kwargs)
        return _extract_text(response)

    # Async callable: async def agent(input: str) -> str
    if callable(_agent) and asyncio.iscoroutinefunction(_agent):
        return await _agent(input_data)

    # Object with async run() method
    if hasattr(_agent, "run") and asyncio.iscoroutinefunction(_agent.run):
        return await _agent.run(input_data)

    msg = (
        "Loaded agent object is not a supported type. "
        "Expected: anthropic.AsyncAnthropic, anthropic.Anthropic, "
        "async callable, or object with async run() method."
    )
    raise TypeError(msg)


@app.post("/stream", dependencies=[Depends(_verify_auth)])
async def stream(request: InvokeRequest) -> StreamingResponse:
    """Stream the agent response as Server-Sent Events."""
    if _client is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")
    return StreamingResponse(
        _stream_sse(request.input),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_sse(input_text: str) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE data lines."""
    model = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
    system_prompt = os.getenv("AGENT_SYSTEM_PROMPT", "")
    max_tokens = int(os.getenv("AGENT_MAX_TOKENS", "1024"))
    messages = [{"role": "user", "content": input_text}]
    kwargs: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system_prompt:
        kwargs["system"] = system_prompt
    if _tools:
        kwargs["tools"] = _tools

    try:
        async with _client.messages.stream(**kwargs) as stream_ctx:
            async for text in stream_ctx.text_stream:
                yield f"data: {json.dumps({'text': text})}\n\n"
    except Exception as exc:  # noqa: BLE001
        yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    yield "data: [DONE]\n\n"


def _extract_text(response: Any) -> str:
    """Extract the first text content block from an Anthropic messages response."""
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return ""
