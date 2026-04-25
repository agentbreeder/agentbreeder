"""Prompt-specific API routes (testing, rendering)."""

from __future__ import annotations

import logging
import random
import re
import time
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.models.database import User
from api.models.schemas import (
    ApiResponse,
    PromptTestRequest,
    PromptTestResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/prompts", tags=["prompts"])


# ---------------------------------------------------------------------------
# ACL enforcement helper
# NOTE: Full CRUD endpoints (GET /{id}, PUT /{id}, DELETE /{id}) are not yet
# implemented in this file. Once added, call _enforce_acl() at the top of each
# handler, following the same pattern used in api/routes/agents.py.
# ---------------------------------------------------------------------------


async def _enforce_acl(
    db: AsyncSession,
    user_email: str,
    resource_id: uuid.UUID,
    action: str,
) -> None:
    """Check ACL for a prompt. Raises 403 if explicitly denied.

    Passes silently if: no ACL rows exist, DB unavailable, or permission granted.
    """
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from api.models.database import ResourcePermission  # noqa: PLC0415
        from api.services.rbac_service import check_permission  # noqa: PLC0415

        allowed, reason = await check_permission(
            db,
            user_email=user_email,
            resource_type="prompt",
            resource_id=resource_id,
            action=action,
        )
        result = await db.execute(
            select(ResourcePermission)
            .where(
                ResourcePermission.resource_type == "prompt",
                ResourcePermission.resource_id == resource_id,
            )
            .limit(1)
        )
        has_acl = result.scalar_one_or_none() is not None
        if has_acl and not allowed:
            from fastapi import HTTPException  # noqa: PLC0415

            raise HTTPException(status_code=403, detail=f"Access denied: {reason}")
    except HTTPException:
        raise
    except Exception:
        pass  # Fail open if DB unavailable

# ---------------------------------------------------------------------------
# Simulated LLM responses for prompt testing
# ---------------------------------------------------------------------------

_SIMULATED_RESPONSES: dict[str, list[str]] = {
    "default": [
        (
            "Based on the provided context, I can help with that. "
            "Let me break this down into key points:\n\n"
            "1. The request has been analyzed according to the "
            "given instructions.\n"
            "2. I've considered the relevant variables and context "
            "provided.\n"
            "3. Here's my recommendation based on the prompt "
            "guidelines.\n\n"
            "Please let me know if you'd like me to elaborate on "
            "any of these points."
        ),
        (
            "Thank you for the clear instructions. Here's my "
            "response:\n\n"
            "I've carefully reviewed the prompt and variables "
            "provided. The key considerations are:\n\n"
            "- The context has been fully incorporated into my "
            "analysis.\n"
            "- All template variables have been resolved with the "
            "provided values.\n"
            "- My response follows the tone and format specified "
            "in the system prompt.\n\n"
            "Would you like me to adjust anything?"
        ),
        (
            "I understand the task. Let me address this "
            "systematically:\n\n"
            "First, I've parsed the prompt template and applied "
            "all variable substitutions. The rendered prompt gives "
            "clear direction for the response format and content "
            "expectations.\n\n"
            "Here's my analysis:\n"
            "- The prompt is well-structured with clear "
            "instructions.\n"
            "- Variable placeholders enhance reusability.\n"
            "- The tone guidance helps maintain consistency across "
            "interactions.\n\n"
            "This prompt template should work well for production "
            "use."
        ),
    ],
    "support": [
        (
            "Hello! I'd be happy to help you with that. Let me "
            "look into your issue right away.\n\n"
            "Based on the information provided, here's what I can "
            "do:\n\n"
            "1. I've reviewed your account details.\n"
            "2. The issue appears to be related to your recent "
            "order.\n"
            "3. I can process a resolution for you immediately.\n\n"
            "Is there anything else I can help you with today?"
        ),
    ],
    "analysis": [
        (
            "## Analysis Summary\n\n"
            "After reviewing the data provided, here are my "
            "findings:\n\n"
            "**Key Metrics:**\n"
            "- Primary indicator shows positive trends\n"
            "- Secondary metrics are within expected ranges\n"
            "- No anomalies detected in the current dataset\n\n"
            "**Recommendations:**\n"
            "1. Continue monitoring the primary KPIs\n"
            "2. Consider adjusting thresholds based on recent "
            "patterns\n"
            "3. Schedule a follow-up review in the next cycle\n\n"
            "*Note: This analysis is based on the parameters "
            "defined in the prompt template.*"
        ),
    ],
}


def _pick_simulated_response(prompt_text: str) -> str:
    """Choose a simulated response based on prompt content keywords."""
    lower = prompt_text.lower()
    if any(kw in lower for kw in ["support", "customer", "help", "ticket"]):
        return random.choice(_SIMULATED_RESPONSES["support"])
    if any(kw in lower for kw in ["analy", "data", "report", "metric"]):
        return random.choice(_SIMULATED_RESPONSES["analysis"])
    return random.choice(_SIMULATED_RESPONSES["default"])


def _render_prompt(prompt_text: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} placeholders with provided values."""

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return variables.get(var_name, match.group(0))

    return re.sub(r"\{\{(\w+)\}\}", replacer, prompt_text)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# POST /api/v1/prompts/test
# ---------------------------------------------------------------------------


@router.post("/test", response_model=ApiResponse[PromptTestResponse])
async def test_prompt(
    body: PromptTestRequest,
    _user: User = Depends(get_current_user),
) -> ApiResponse[PromptTestResponse]:
    """Test a prompt by rendering variables and sending to an LLM.

    NOTE: This is a simulated test. In production, this would route through
    the configured provider backend (LiteLLM, direct API, etc.) to actually
    call the selected model. The simulation provides realistic response
    structure, token counts, and latency for UI development and testing.
    """
    start = time.monotonic()

    # Render the prompt with variable substitutions
    rendered = _render_prompt(body.prompt_text, body.variables)

    # Determine model name for the response
    model_name = body.model_name or "simulated-model"

    # Simulate LLM call latency (200-800ms)
    simulated_latency = random.randint(200, 800)

    # Pick a contextual simulated response
    response_text = _pick_simulated_response(rendered)

    # Calculate token estimates
    input_tokens = _estimate_tokens(rendered)
    output_tokens = _estimate_tokens(response_text)

    elapsed_ms = int((time.monotonic() - start) * 1000) + simulated_latency

    result = PromptTestResponse(
        response_text=response_text,
        rendered_prompt=rendered,
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        latency_ms=elapsed_ms,
        temperature=body.temperature,
    )

    return ApiResponse(data=result)
