"""AgentBreeder server wrapper for Claude SDK agents.

This file is copied into the agent container at build time.
It wraps any Claude SDK agent as a FastAPI server with /invoke, /stream, and /health endpoints.

Supports three routing providers via AGENT_ROUTING_PROVIDER:
  - "anthropic" (default): uses ANTHROPIC_API_KEY
  - "vertex_ai": uses GOOGLE_APPLICATION_CREDENTIALS + AGENT_ROUTING_PROJECT_ID + AGENT_ROUTING_REGION
  - "bedrock": uses AWS credentials + AGENT_ROUTING_REGION

Adaptive thinking (Opus 4.6 / Sonnet 4.6):
  Set AGENT_THINKING_ENABLED=true and AGENT_THINKING_EFFORT=high|medium|low.
  Uses type:adaptive + output_config (NOT budget_tokens, which is deprecated).

Prompt caching:
  Set AGENT_PROMPT_CACHING=true to auto-inject cache_control on long system prompts.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentbreeder.agent")


class InvokeRequest(BaseModel):
    input: str
    session_id: str | None = None
    user_id: str | None = None
    config: dict[str, Any] | None = None


class InvokeResponse(BaseModel):
    output: str
    session_id: str | None = None
    metadata: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str
    agent_name: str
    version: str


def _load_agent() -> Any:
    """Dynamically load the agent from agent.py."""
    sys.path.insert(0, "/app")
    module = importlib.import_module("agent")
    for attr_name in ("agent", "client", "app", "run"):
        if hasattr(module, attr_name):
            logger.info("Loaded agent from agent.py (attr=%s)", attr_name)
            return getattr(module, attr_name)
    msg = (
        "agent.py must export one of: 'agent', 'client', 'app', or 'run'. "
        "This should be an anthropic.AsyncAnthropic instance or async callable."
    )
    raise AttributeError(msg)


def _build_client() -> Any:
    """Construct the correct AsyncAnthropic* client based on AGENT_ROUTING_PROVIDER env var.

    Supported providers:
    - "anthropic" (default): AsyncAnthropic — uses ANTHROPIC_API_KEY
    - "vertex_ai": AsyncAnthropicVertex — uses GOOGLE_APPLICATION_CREDENTIALS + project/region
    - "bedrock": AsyncAnthropicBedrock — uses AWS credentials + region
    """
    import anthropic

    provider = os.getenv("AGENT_ROUTING_PROVIDER", "anthropic")

    if provider == "vertex_ai":
        project_id = os.getenv("AGENT_ROUTING_PROJECT_ID")
        region = os.getenv("AGENT_ROUTING_REGION", "us-east5")
        if not project_id:
            raise ValueError(
                "AGENT_ROUTING_PROVIDER=vertex_ai requires AGENT_ROUTING_PROJECT_ID to be set"
            )
        logger.info("Using Vertex AI provider (project=%s, region=%s)", project_id, region)
        return anthropic.AsyncAnthropicVertex(project_id=project_id, region=region)

    if provider == "bedrock":
        region = os.getenv("AGENT_ROUTING_REGION", "us-east-1")
        logger.info("Using AWS Bedrock provider (region=%s)", region)
        return anthropic.AsyncAnthropicBedrock(aws_region=region)

    # Default: Anthropic direct
    logger.info("Using Anthropic direct provider")
    return anthropic.AsyncAnthropic()


def _get_cache_threshold(model: str) -> int:
    """Return the minimum system prompt character length for prompt caching eligibility.

    Anthropic caching requires system prompts to meet a minimum token count.
    We use character count as a cheap proxy (1 token ≈ 4 chars).
    - Sonnet 4.6: 2048 tokens → ~8192 chars
    - Opus 4.6 / Haiku 4.5 and others: 4096 tokens → ~16384 chars
    """
    if "sonnet" in model.lower():
        return 8192   # 2048 tokens * ~4 chars/token
    return 16384      # 4096 tokens * ~4 chars/token


def _build_system_param(system_prompt: str, model: str) -> str | list[dict[str, Any]]:
    """Build the system parameter for messages.create().

    If prompt caching is enabled and the system prompt is long enough,
    returns a content block list with cache_control injected.
    Otherwise returns the raw string (or empty string if no system prompt).
    """
    if not system_prompt:
        return ""

    if _prompt_caching_enabled and len(system_prompt) >= _get_cache_threshold(model):
        return [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    return system_prompt


async def _call_client(
    client: Any,
    model: str,
    system_prompt: str,
    messages: list[dict[str, Any]],
) -> str:
    """Call client.messages.create() with thinking and caching applied.

    - Reads max_tokens from AGENT_MAX_TOKENS env var (default 4096).
    - Applies adaptive thinking when AGENT_THINKING_ENABLED=true.
    - Auto-applies prompt caching when AGENT_PROMPT_CACHING=true and system prompt
      meets the model-specific length threshold.
    """
    max_tokens = int(os.getenv("AGENT_MAX_TOKENS", "4096"))
    temperature_str = os.getenv("AGENT_TEMPERATURE")

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }

    # System prompt (may become a content block list if caching is applied)
    system = _build_system_param(system_prompt, model)
    if system:
        kwargs["system"] = system

    # Temperature (only set if not using thinking — Anthropic API rejects both)
    if temperature_str and not _thinking_config:
        kwargs["temperature"] = float(temperature_str)

    # Adaptive thinking (Opus 4.6 / Sonnet 4.6)
    # NOTE: budget_tokens is deprecated on these models — use type:adaptive + output_config
    if _thinking_config:
        thinking = {"type": _thinking_config["type"]}
        kwargs["thinking"] = thinking
        effort = _thinking_config.get("_effort", "high")
        kwargs["output_config"] = {"effort": effort}
        # interleaved-thinking beta is required for streaming; safe no-op for non-streaming
        kwargs["betas"] = ["interleaved-thinking-2025-05-14"]

    response = await client.messages.create(**kwargs)
    return _extract_text(response)


def _extract_text(response: Any) -> str:
    """Extract text content from an Anthropic message response."""
    parts: list[str] = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)


# Module-level globals — initialized at startup
_agent: Any = None
_client: Any = None
_thinking_config: dict[str, Any] | None = None
_prompt_caching_enabled: bool = False
_tools: list[dict[str, Any]] = []


async def startup() -> None:
    global _agent, _client, _thinking_config, _prompt_caching_enabled, _tools  # noqa: PLW0603
    logger.info("Loading agent...")
    try:
        _agent = _load_agent()
        _client = _build_client()
        logger.info("Agent loaded successfully")
    except Exception as exc:
        logger.warning("Agent module not loaded yet — will retry on first request: %s", exc)

    # Adaptive thinking (Opus 4.6 / Sonnet 4.6)
    if os.getenv("AGENT_THINKING_ENABLED") == "true":
        effort = os.getenv("AGENT_THINKING_EFFORT", "high")
        _thinking_config = {"type": "adaptive", "_effort": effort}
        logger.info("Adaptive thinking enabled (effort=%s)", effort)

    _prompt_caching_enabled = os.getenv("AGENT_PROMPT_CACHING") == "true"
    if _prompt_caching_enabled:
        logger.info("Prompt caching enabled")

    # Tool bridge — load tools from AGENT_TOOLS_JSON env var
    import json as _json
    raw_tools = os.getenv("AGENT_TOOLS_JSON", "[]")
    try:
        tool_refs = _json.loads(raw_tools)
        if tool_refs:
            from tool_bridge import to_claude_tools  # noqa: PLC0415
            _tools = to_claude_tools(tool_refs)
            logger.info("Loaded %d tools via tool bridge", len(_tools))
    except Exception as e:
        logger.warning("Failed to load tools: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        status="healthy" if _agent is not None else "loading",
        agent_name=os.getenv("AGENT_NAME", "unknown"),
        version=os.getenv("AGENT_VERSION", "0.1.0"),
    )


@app.post("/invoke", response_model=InvokeResponse)
async def invoke(request: InvokeRequest) -> InvokeResponse:
    if _agent is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    try:
        result = await _run_agent(request.input)
        return InvokeResponse(output=result)
    except Exception as e:
        logger.exception("Agent invocation failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/stream")
async def stream(request: InvokeRequest) -> StreamingResponse:
    """SSE streaming endpoint."""
    if _agent is None or _client is None:
        raise HTTPException(status_code=503, detail="Agent not loaded yet")

    import anthropic
    import json

    async def event_generator():
        model = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
        system_prompt = os.getenv("AGENT_SYSTEM_PROMPT", "")
        messages = [{"role": "user", "content": request.input}]
        max_tokens = int(os.getenv("AGENT_MAX_TOKENS", "4096"))

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        system = _build_system_param(system_prompt, model)
        if system:
            kwargs["system"] = system

        if _thinking_config:
            kwargs["thinking"] = {"type": _thinking_config["type"]}
            kwargs["output_config"] = {"effort": _thinking_config.get("_effort", "high")}
            kwargs["betas"] = ["interleaved-thinking-2025-05-14"]

        if _tools:
            kwargs["tools"] = _tools

        try:
            async with _client.messages.stream(**kwargs) as stream_ctx:
                async for text in stream_ctx.text_stream:
                    yield f"data: {json.dumps({'text': text})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.exception("Streaming invocation failed")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _run_agent(input_data: str) -> str:
    """Run the agent, dispatching based on the type of object loaded from agent.py."""
    import anthropic

    model = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
    system_prompt = os.getenv("AGENT_SYSTEM_PROMPT", "")
    messages: list[dict[str, Any]] = [{"role": "user", "content": input_data}]

    # AsyncAnthropic / AsyncAnthropicVertex / AsyncAnthropicBedrock client
    # Always dispatch through _client (routing-aware), not _agent directly.
    if isinstance(_agent, anthropic.AsyncAnthropic):
        return await _call_client(_client, model, system_prompt, messages)

    # anthropic.Anthropic (sync) client — run in thread to avoid blocking.
    # Applies the same thinking/caching/tools kwargs as the async _call_client path.
    if isinstance(_agent, anthropic.Anthropic):
        max_tokens = int(os.getenv("AGENT_MAX_TOKENS", "4096"))
        temperature_str = os.getenv("AGENT_TEMPERATURE")
        sync_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        system = _build_system_param(system_prompt, model)
        if system:
            sync_kwargs["system"] = system
        if temperature_str and not _thinking_config:
            sync_kwargs["temperature"] = float(temperature_str)
        if _thinking_config:
            sync_kwargs["thinking"] = {"type": _thinking_config["type"]}
            sync_kwargs["output_config"] = {"effort": _thinking_config.get("_effort", "high")}
            sync_kwargs["betas"] = ["interleaved-thinking-2025-05-14"]
        if _tools:
            sync_kwargs["tools"] = _tools
        response = await asyncio.to_thread(_agent.messages.create, **sync_kwargs)
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
