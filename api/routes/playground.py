"""Playground API routes — interactive agent chat/testing."""

from __future__ import annotations

import logging
import random
import time
import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.models.schemas import ApiResponse

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


class SaveEvalCaseRequest(BaseModel):
    agent_id: str
    conversation_history: list[ConversationMessage]
    assistant_message: str
    model_used: str
    tags: list[str] = Field(default_factory=list)


class SaveEvalCaseResponse(BaseModel):
    eval_case_id: str
    saved: bool


# ---------------------------------------------------------------------------
# Simulated agent responses for playground testing
# ---------------------------------------------------------------------------

_SIMULATED_AGENT_RESPONSES: list[str] = [
    (
        "I've analyzed your request and here's what I found:\n\n"
        "Based on the context of our conversation, I can help with that. "
        "Let me break this down step by step:\n\n"
        "1. I've reviewed the relevant information available to me.\n"
        "2. The key factors to consider are the scope and timeline.\n"
        "3. My recommendation is to proceed with the approach outlined above.\n\n"
        "Would you like me to go deeper into any of these points?"
    ),
    (
        "Great question! Let me think through this carefully.\n\n"
        "After considering the available data and context:\n\n"
        "- The primary concern has been addressed in the analysis above.\n"
        "- I've cross-referenced this with the knowledge base entries.\n"
        "- The suggested next steps should resolve the issue effectively.\n\n"
        "Let me know if you need any clarification or want to explore "
        "alternative approaches."
    ),
    (
        "I understand what you're looking for. Here's my response:\n\n"
        "## Summary\n\n"
        "Based on my analysis, the situation can be addressed as follows:\n\n"
        "**Key Findings:**\n"
        "- The data supports moving forward with option A.\n"
        "- Risk factors have been evaluated and are within acceptable bounds.\n"
        "- Implementation can begin immediately with the outlined steps.\n\n"
        "**Next Steps:**\n"
        "1. Confirm the parameters with the relevant stakeholders.\n"
        "2. Execute the plan in the recommended order.\n"
        "3. Monitor results and adjust as needed.\n\n"
        "Shall I elaborate on any of these points?"
    ),
]

_SIMULATED_TOOL_CALLS: list[PlaygroundToolCall] = [
    PlaygroundToolCall(
        tool_name="search_knowledge_base",
        tool_input={"query": "relevant documentation", "top_k": 5},
        tool_output={
            "results": [
                {"title": "Product FAQ", "score": 0.92, "snippet": "...relevant content..."},
                {"title": "User Guide", "score": 0.87, "snippet": "...related content..."},
            ],
            "total": 2,
        },
        duration_ms=145,
    ),
    PlaygroundToolCall(
        tool_name="lookup_order",
        tool_input={"order_id": "ORD-12345"},
        tool_output={
            "status": "shipped",
            "tracking": "1Z999AA10123456784",
            "estimated_delivery": "2026-03-15",
        },
        duration_ms=89,
    ),
]


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


@router.post("/chat", response_model=ApiResponse[PlaygroundChatResponse])
async def playground_chat(
    body: PlaygroundChatRequest,
) -> ApiResponse[PlaygroundChatResponse]:
    """Send a message to an agent and get a response.

    NOTE: This is currently a simulated endpoint for UI development.
    In production, this routes through the engine's provider infrastructure
    to call the actual model configured for the agent. The simulation
    provides realistic response structure, tool calls, token counts,
    latency, and cost estimates.
    """
    start = time.monotonic()

    # Log system prompt override if provided
    if body.system_prompt_override:
        logger.info(
            "Playground chat using system prompt override (%d chars) for agent %s",
            len(body.system_prompt_override),
            body.agent_id,
        )

    # Determine model
    model_used = body.model_override or "claude-sonnet-4"

    # Simulate tool calls (30% chance of including tool calls)
    tool_calls: list[PlaygroundToolCall] = []
    if random.random() < 0.3:
        tool_calls = random.sample(
            _SIMULATED_TOOL_CALLS, k=random.randint(1, len(_SIMULATED_TOOL_CALLS))
        )

    # Pick a simulated response
    response_text = random.choice(_SIMULATED_AGENT_RESPONSES)

    # Calculate tokens
    input_text = body.message + " ".join(m.content for m in body.conversation_history)
    input_tokens = _estimate_tokens(input_text)
    output_tokens = _estimate_tokens(response_text)
    total_tokens = input_tokens + output_tokens

    # Cost estimate
    cost = _estimate_cost(input_tokens, output_tokens, model_used)

    # Simulated latency (300-1200ms)
    simulated_latency = random.randint(300, 1200)
    elapsed_ms = int((time.monotonic() - start) * 1000) + simulated_latency

    result = PlaygroundChatResponse(
        response=response_text,
        tool_calls=tool_calls,
        token_count=total_tokens,
        cost_estimate=cost,
        latency_ms=elapsed_ms,
        model_used=model_used,
        conversation_id=str(uuid.uuid4()),
    )

    return ApiResponse(data=result)


# ---------------------------------------------------------------------------
# POST /api/v1/playground/eval-case
# ---------------------------------------------------------------------------


@router.post("/eval-case", response_model=ApiResponse[SaveEvalCaseResponse])
async def save_eval_case(
    body: SaveEvalCaseRequest,
) -> ApiResponse[SaveEvalCaseResponse]:
    """Save an assistant message as an eval test case.

    NOTE: Simulated — in production this persists to the eval store.
    """
    logger.info(
        "Saving eval case for agent %s with %d history messages",
        body.agent_id,
        len(body.conversation_history),
    )

    result = SaveEvalCaseResponse(
        eval_case_id=str(uuid.uuid4()),
        saved=True,
    )

    return ApiResponse(data=result)
