"""Playground API routes — interactive agent chat/testing."""

from __future__ import annotations

import logging
import os
import time
import uuid

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.config import settings
from api.database import get_db
from api.models.database import User
from api.models.schemas import ApiResponse
from registry.agents import AgentRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/playground", tags=["playground"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ConversationMessage(BaseModel):
    role: str  # "user" | "assistant" | "system"
    content: str


class PlaygroundToolCall(BaseModel):
    """Represents a tool invocation during agent execution."""

    tool_name: str
    tool_input: dict = Field(default_factory=dict)
    tool_output: dict = Field(default_factory=dict)
    duration_ms: int = 0


class PlaygroundChatRequest(BaseModel):
    agent_id: str
    message: str
    model_override: str | None = None
    system_prompt_override: str | None = None
    conversation_history: list[ConversationMessage] = Field(default_factory=list)


class PlaygroundChatResponse(BaseModel):
    response: str
    tool_calls: list[PlaygroundToolCall] = Field(default_factory=list)
    token_count: int = 0
    cost_estimate: float = 0.0
    latency_ms: int = 0
    model_used: str = ""
    conversation_id: str = ""


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return max(1, len(text) // 4)


def _estimate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Rough cost estimate based on model pricing."""
    # Approximate pricing per million tokens
    pricing = {
        "claude-sonnet-4": (3.0, 15.0),
        "claude-3-5-sonnet": (3.0, 15.0),
        "gpt-4o": (5.0, 15.0),
        "gpt-4o-mini": (0.15, 0.6),
        "gemini-2.0-flash": (0.075, 0.3),
    }
    input_price, output_price = pricing.get(model, (2.0, 10.0))
    return round(
        (input_tokens * input_price / 1_000_000) + (output_tokens * output_price / 1_000_000),
        6,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/playground/chat
# ---------------------------------------------------------------------------


async def _litellm_headers() -> dict:
    key = os.getenv("LITELLM_MASTER_KEY", "sk-agentbreeder-quickstart")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def _list_available_models() -> list[str]:
    """Return model IDs that LiteLLM reports as available."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{settings.litellm_base_url}/v1/models",
                headers=await _litellm_headers(),
            )
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]
    except Exception:
        return []


async def _try_litellm_call(
    messages: list[dict], candidates: list[str]
) -> tuple[str, int, int, str] | None:
    """Try each candidate model in order. Returns (text, in_tok, out_tok, model) or None."""
    for model in candidates:
        try:
            text, in_tok, out_tok = await _call_litellm(messages, model)
            return text, in_tok, out_tok, model
        except Exception:
            continue
    return None


async def _resolve_model(requested: str | None) -> str:
    """Return a model name (may not be available — callers should handle failures)."""
    if requested:
        return requested
    models = await _list_available_models()
    for m in models:
        if m.startswith("ollama/"):
            return m
    return models[0] if models else "ollama/llama3.2"


async def _call_litellm(
    messages: list[dict], model: str, timeout: float = 180.0
) -> tuple[str, int, int]:
    """Call LiteLLM gateway. Returns (response_text, input_tokens, output_tokens)."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{settings.litellm_base_url}/v1/chat/completions",
            headers=await _litellm_headers(),
            json={"model": model, "messages": messages},
        )
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return choice, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)


def _build_system_prompt(agent_name: str, description: str, config: dict) -> str:
    """Build a system prompt from agent config."""
    parts = [f"You are {agent_name}, an AI agent."]
    if description:
        parts.append(description)

    # Extract system prompt from config_snapshot
    prompts = config.get("prompts", {})
    if isinstance(prompts, dict) and prompts.get("system"):
        parts.append(prompts["system"])

    # List available tools
    tools = config.get("tools", [])
    if tools:
        tool_names = []
        for t in tools:
            if isinstance(t, dict):
                tool_names.append(t.get("name") or t.get("ref", ""))
            elif isinstance(t, str):
                tool_names.append(t)
        if tool_names:
            parts.append(f"Available tools: {', '.join(filter(None, tool_names))}.")

    parts.append("Answer the user's questions helpfully and accurately.")
    return "\n\n".join(parts)


@router.post("/chat", response_model=ApiResponse[PlaygroundChatResponse])
async def playground_chat(
    body: PlaygroundChatRequest,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PlaygroundChatResponse]:
    """Send a message to an agent and get a response via LiteLLM gateway."""
    start = time.monotonic()

    # Load agent config to build system prompt and collect model candidates
    system_prompt: str | None = body.system_prompt_override
    model_primary: str | None = body.model_override
    model_fallback: str | None = None

    try:
        agent = await AgentRegistry.get_by_id(db, uuid.UUID(body.agent_id))
        if agent:
            if not model_primary and agent.model_primary:
                model_primary = agent.model_primary
            if agent.model_fallback:
                model_fallback = agent.model_fallback
            if not system_prompt:
                system_prompt = _build_system_prompt(
                    agent.name,
                    agent.description or "",
                    agent.config_snapshot or {},
                )
    except Exception:
        pass  # agent_id may be a placeholder; proceed without agent context

    # Build ordered candidate list: primary → fallback → any available ollama → any available
    candidates: list[str] = []
    if model_primary:
        candidates.append(model_primary)
    if model_fallback and model_fallback not in candidates:
        candidates.append(model_fallback)
    # Add any available models from LiteLLM as last-resort options
    available = await _list_available_models()
    for model in available:
        if model not in candidates:
            candidates.append(model)
    if not candidates:
        candidates = ["ollama/llama3.2"]

    model_used = candidates[0]

    # Build message list
    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    for m in body.conversation_history:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": body.message})

    response_text: str
    input_tokens: int
    output_tokens: int

    result_tuple = await _try_litellm_call(messages, candidates)
    if result_tuple:
        response_text, input_tokens, output_tokens, model_used = result_tuple
    else:
        tried = ", ".join(candidates[:3]) + ("..." if len(candidates) > 3 else "")
        logger.warning("All model candidates failed for playground chat: %s", tried)
        response_text = (
            f"⚠️ No model available to respond.\n\n"
            f"Tried: `{tried}`\n\n"
            f"To fix this, either:\n"
            f"- Start Ollama locally: `ollama serve && ollama pull llama3.2`\n"
            f"- Add an API key in the quickstart setup (OpenAI, Anthropic, Gemini, or OpenRouter)"
        )
        input_text = body.message + " ".join(m.content for m in body.conversation_history)
        input_tokens = _estimate_tokens(input_text)
        output_tokens = _estimate_tokens(response_text)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    cost = _estimate_cost(input_tokens, output_tokens, model_used)

    result = PlaygroundChatResponse(
        response=response_text,
        tool_calls=[],
        token_count=input_tokens + output_tokens,
        cost_estimate=cost,
        latency_ms=elapsed_ms,
        model_used=model_used,
        conversation_id=str(uuid.uuid4()),
    )

    return ApiResponse(data=result)
