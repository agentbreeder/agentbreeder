"""Gateway API routes — model gateway management and proxying.

Exposes gateway status, model catalog across providers, and request log
for the Agent Garden model gateway (LiteLLM + direct providers).
"""

from __future__ import annotations

import logging
import random
import time

from fastapi import APIRouter, Query

from api.models.schemas import ApiMeta, ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/gateway", tags=["gateway"])


# ---------------------------------------------------------------------------
# Simulated data helpers
# ---------------------------------------------------------------------------

_GATEWAY_TIERS = [
    {
        "tier": "litellm",
        "label": "LiteLLM Gateway",
        "description": "Self-hosted LiteLLM proxy — routes to all configured providers",
        "status": "connected",
        "latency_ms": 12,
        "model_count": 3,
        "base_url": "http://litellm:4000",
    },
    {
        "tier": "openrouter",
        "label": "OpenRouter",
        "description": "OpenRouter multi-provider gateway (300+ models)",
        "status": "disconnected",
        "latency_ms": None,
        "model_count": 0,
        "base_url": "https://openrouter.ai/api/v1",
    },
    {
        "tier": "direct",
        "label": "Direct API",
        "description": "Direct calls to Anthropic, OpenAI, and Google AI APIs",
        "status": "partial",
        "latency_ms": 45,
        "model_count": 12,
        "base_url": None,
    },
]

_GATEWAY_MODELS = [
    # LiteLLM tier
    {
        "id": "gpt-4o",
        "name": "GPT-4o",
        "provider": "openai",
        "gateway_tier": "litellm",
        "context_window": 128000,
        "input_price_per_million": 2.50,
        "output_price_per_million": 10.00,
        "status": "active",
    },
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6",
        "provider": "anthropic",
        "gateway_tier": "litellm",
        "context_window": 200000,
        "input_price_per_million": 3.00,
        "output_price_per_million": 15.00,
        "status": "active",
    },
    {
        "id": "gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "provider": "google",
        "gateway_tier": "litellm",
        "context_window": 1000000,
        "input_price_per_million": 1.25,
        "output_price_per_million": 5.00,
        "status": "active",
    },
    # Direct tier
    {
        "id": "claude-opus-4",
        "name": "Claude Opus 4",
        "provider": "anthropic",
        "gateway_tier": "direct",
        "context_window": 200000,
        "input_price_per_million": 15.00,
        "output_price_per_million": 75.00,
        "status": "active",
    },
    {
        "id": "claude-haiku-3-5",
        "name": "Claude Haiku 3.5",
        "provider": "anthropic",
        "gateway_tier": "direct",
        "context_window": 200000,
        "input_price_per_million": 0.80,
        "output_price_per_million": 4.00,
        "status": "active",
    },
    {
        "id": "gpt-4o-mini",
        "name": "GPT-4o Mini",
        "provider": "openai",
        "gateway_tier": "direct",
        "context_window": 128000,
        "input_price_per_million": 0.15,
        "output_price_per_million": 0.60,
        "status": "active",
    },
    {
        "id": "o3-mini",
        "name": "o3 Mini",
        "provider": "openai",
        "gateway_tier": "direct",
        "context_window": 128000,
        "input_price_per_million": 1.10,
        "output_price_per_million": 4.40,
        "status": "active",
    },
    {
        "id": "gemini-2.0-flash",
        "name": "Gemini 2.0 Flash",
        "provider": "google",
        "gateway_tier": "direct",
        "context_window": 1000000,
        "input_price_per_million": 0.10,
        "output_price_per_million": 0.40,
        "status": "active",
    },
    {
        "id": "gemini-1.5-pro",
        "name": "Gemini 1.5 Pro",
        "provider": "google",
        "gateway_tier": "direct",
        "context_window": 2000000,
        "input_price_per_million": 1.25,
        "output_price_per_million": 5.00,
        "status": "active",
    },
    {
        "id": "llama-3.3-70b",
        "name": "Llama 3.3 70B",
        "provider": "meta",
        "gateway_tier": "direct",
        "context_window": 128000,
        "input_price_per_million": 0.23,
        "output_price_per_million": 0.40,
        "status": "active",
    },
    {
        "id": "mistral-large-2",
        "name": "Mistral Large 2",
        "provider": "mistral",
        "gateway_tier": "direct",
        "context_window": 128000,
        "input_price_per_million": 2.00,
        "output_price_per_million": 6.00,
        "status": "active",
    },
    {
        "id": "mistral-small-3",
        "name": "Mistral Small 3",
        "provider": "mistral",
        "gateway_tier": "direct",
        "context_window": 32000,
        "input_price_per_million": 0.10,
        "output_price_per_million": 0.30,
        "status": "active",
    },
]

_GATEWAY_PROVIDERS = [
    {
        "id": "anthropic",
        "name": "Anthropic",
        "tier": "direct",
        "status": "healthy",
        "latency_ms": 38,
        "model_count": 3,
        "last_checked": "2026-03-13T10:00:00Z",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "tier": "direct",
        "status": "healthy",
        "latency_ms": 52,
        "model_count": 4,
        "last_checked": "2026-03-13T10:00:00Z",
    },
    {
        "id": "google",
        "name": "Google AI",
        "tier": "direct",
        "status": "healthy",
        "latency_ms": 61,
        "model_count": 3,
        "last_checked": "2026-03-13T10:00:00Z",
    },
    {
        "id": "meta",
        "name": "Meta (via Together)",
        "tier": "direct",
        "status": "healthy",
        "latency_ms": 85,
        "model_count": 1,
        "last_checked": "2026-03-13T10:00:00Z",
    },
    {
        "id": "mistral",
        "name": "Mistral AI",
        "tier": "direct",
        "status": "healthy",
        "latency_ms": 43,
        "model_count": 2,
        "last_checked": "2026-03-13T10:00:00Z",
    },
    {
        "id": "litellm",
        "name": "LiteLLM Proxy",
        "tier": "litellm",
        "status": "healthy",
        "latency_ms": 12,
        "model_count": 3,
        "last_checked": "2026-03-13T10:00:00Z",
    },
]

_AGENTS = [
    "customer-support-agent",
    "code-review-agent",
    "data-analysis-agent",
    "document-qa-agent",
    "email-triage-agent",
]

_MODELS_USED = [m["id"] for m in _GATEWAY_MODELS]


def _generate_log_entries(count: int = 20) -> list[dict]:
    """Generate simulated gateway request log entries."""
    seed_base = int(time.time()) // 60  # changes every minute for some variation
    random.seed(seed_base)
    entries = []
    for i in range(count):
        model_id = random.choice(_MODELS_USED)
        model_info = next((m for m in _GATEWAY_MODELS if m["id"] == model_id), None)
        input_tokens = random.randint(100, 4000)
        output_tokens = random.randint(50, 800)
        latency = random.randint(200, 3000)
        cost = 0.0
        if model_info:
            cost = (
                input_tokens * float(model_info["input_price_per_million"]) / 1_000_000
                + output_tokens * float(model_info["output_price_per_million"]) / 1_000_000
            )
        entries.append(
            {
                "id": f"req_{seed_base}_{i:04d}",
                "timestamp": f"2026-03-13T{9 + i // 4:02d}:{(i * 3) % 60:02d}:00Z",
                "agent": random.choice(_AGENTS),
                "model": model_id,
                "provider": model_info["provider"] if model_info else "unknown",
                "gateway_tier": model_info["gateway_tier"] if model_info else "direct",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency,
                "cost_usd": round(cost, 6),
                "status": "success" if random.random() > 0.05 else "error",
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=ApiResponse[list[dict]])
async def gateway_status() -> ApiResponse[list[dict]]:
    """Return status of each gateway tier (LiteLLM, OpenRouter, Direct API)."""
    return ApiResponse(
        data=_GATEWAY_TIERS,
        meta=ApiMeta(page=1, per_page=len(_GATEWAY_TIERS), total=len(_GATEWAY_TIERS)),
    )


@router.get("/models", response_model=ApiResponse[list[dict]])
async def list_gateway_models(
    tier: str | None = Query(None, description="Filter by gateway tier"),
    provider: str | None = Query(None, description="Filter by provider"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> ApiResponse[list[dict]]:
    """List all models across all connected gateway providers."""
    models = list(_GATEWAY_MODELS)

    if tier:
        models = [m for m in models if m["gateway_tier"] == tier]
    if provider:
        models = [m for m in models if m["provider"].lower() == provider.lower()]

    total = len(models)
    start = (page - 1) * per_page
    end = start + per_page
    page_models = models[start:end]

    return ApiResponse(
        data=page_models,
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/providers", response_model=ApiResponse[list[dict]])
async def list_gateway_providers() -> ApiResponse[list[dict]]:
    """List configured gateway providers with health status."""
    return ApiResponse(
        data=_GATEWAY_PROVIDERS,
        meta=ApiMeta(page=1, per_page=len(_GATEWAY_PROVIDERS), total=len(_GATEWAY_PROVIDERS)),
    )


@router.get("/logs", response_model=ApiResponse[list[dict]])
async def gateway_logs(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    model: str | None = Query(None),
    provider: str | None = Query(None),
    status: str | None = Query(None),
) -> ApiResponse[list[dict]]:
    """Return paginated list of recent gateway requests."""
    all_entries = _generate_log_entries(count=100)

    if model:
        all_entries = [e for e in all_entries if e["model"] == model]
    if provider:
        all_entries = [e for e in all_entries if e["provider"] == provider]
    if status:
        all_entries = [e for e in all_entries if e["status"] == status]

    total = len(all_entries)
    start = (page - 1) * per_page
    end = start + per_page
    page_entries = all_entries[start:end]

    return ApiResponse(
        data=page_entries,
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/costs/comparison", response_model=ApiResponse[list[dict]])
async def cost_comparison() -> ApiResponse[list[dict]]:
    """Return cost comparison table across providers (price per 1M tokens)."""
    comparison = [
        {
            "model": m["id"],
            "name": m["name"],
            "provider": m["provider"],
            "gateway_tier": m["gateway_tier"],
            "input_per_million": m["input_price_per_million"],
            "output_per_million": m["output_price_per_million"],
            "context_window": m["context_window"],
        }
        for m in _GATEWAY_MODELS
    ]

    # Sort by input price ascending
    comparison.sort(key=lambda x: float(x["input_per_million"]))

    return ApiResponse(
        data=comparison,
        meta=ApiMeta(page=1, per_page=len(comparison), total=len(comparison)),
    )
