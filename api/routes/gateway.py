"""Gateway API routes — model gateway management and proxying.

Exposes gateway status, model catalog across providers, and request log
for the AgentBreeder model gateway (LiteLLM + direct providers).

Required env vars (consumed by the LiteLLM-backed endpoints):

- ``LITELLM_BASE_URL`` — base URL of the LiteLLM proxy
  (default ``http://localhost:4000``).
- ``LITELLM_MASTER_KEY`` — master key used to authenticate against
  ``/health``, ``/v1/models``, ``/v1/provider/info``, ``/spend/logs`` etc.
  (default ``sk-agentbreeder-quickstart``).

If LiteLLM is not running, ``/logs`` returns ``503`` with an empty
``data: []`` and a clear error message; it never falls back to fake data.
"""

from __future__ import annotations

import logging
import os
import time

import httpx
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.costs import CostEvent
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from api.services.gateway_logs_service import (
    GatewayLogsUnavailableError,
    fetch_spend_logs,
)

# ---------------------------------------------------------------------------
# LiteLLM connection helpers
# ---------------------------------------------------------------------------

_LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-agentbreeder-quickstart")


def _litellm_headers() -> dict:
    return {"Authorization": f"Bearer {_LITELLM_MASTER_KEY}"}


async def _fetch_litellm(path: str, fallback):
    """GET a LiteLLM endpoint and return its JSON, or fallback on any error."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{_LITELLM_BASE_URL}{path}", headers=_litellm_headers())
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return fallback


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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _infer_tier_from_provider(provider: str, model_name: str = "") -> str:
    """Infer gateway tier from a CostEvent row's provider/model fields.

    Matches the convention used by the live LiteLLM logs path: anything
    that came through LiteLLM is labeled ``litellm``; anything else is a
    direct provider call.
    """
    p = (provider or "").lower()
    if p in {"litellm", "openai_proxy", "litellm_proxy"}:
        return "litellm"
    if "/" in (model_name or ""):
        return "litellm"
    return "direct"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=ApiResponse[list[dict]])
async def gateway_status(_user: User = Depends(get_current_user)) -> ApiResponse[list[dict]]:
    """Return status of each gateway tier (LiteLLM, OpenRouter, Direct API)."""
    _fallback_health = {
        "status": "unknown",
        "healthy_endpoints": [],
        "unhealthy_endpoints": [],
    }
    health = await _fetch_litellm("/health", _fallback_health)

    # Build a live LiteLLM tier entry from /health response
    healthy = health.get("healthy_endpoints", [])
    unhealthy = health.get("unhealthy_endpoints", [])
    litellm_status = health.get("status", "unknown")
    live_litellm_tier = {
        "tier": "litellm",
        "label": "LiteLLM Gateway",
        "description": "Self-hosted LiteLLM proxy — routes to all configured providers",
        "status": litellm_status,
        "latency_ms": None,
        "model_count": len(healthy),
        "base_url": _LITELLM_BASE_URL,
        "healthy_endpoints": healthy,
        "unhealthy_endpoints": unhealthy,
    }

    # Merge live litellm tier with static non-litellm tiers
    non_litellm = [t for t in _GATEWAY_TIERS if t["tier"] != "litellm"]
    tiers = [live_litellm_tier, *non_litellm]

    return ApiResponse(
        data=tiers,
        meta=ApiMeta(page=1, per_page=len(tiers), total=len(tiers)),
    )


@router.get("/models", response_model=ApiResponse[list[dict]])
async def list_gateway_models(
    _user: User = Depends(get_current_user),
    tier: str | None = Query(None, description="Filter by gateway tier"),
    provider: str | None = Query(None, description="Filter by provider"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> ApiResponse[list[dict]]:
    """List all models across all connected gateway providers."""
    raw = await _fetch_litellm("/models", {"data": []})
    litellm_model_ids = {m.get("id") for m in raw.get("data", []) if m.get("id")}

    # Annotate the static model list with live availability from LiteLLM
    models = [
        {
            **m,
            "status": "active" if m["id"] in litellm_model_ids else m.get("status", "active"),
        }
        for m in _GATEWAY_MODELS
    ]

    # Also surface any LiteLLM models not in the static list
    static_ids = {m["id"] for m in _GATEWAY_MODELS}
    for raw_model in raw.get("data", []):
        mid = raw_model.get("id", "")
        if mid and mid not in static_ids:
            models.append(
                {
                    "id": mid,
                    "name": mid,
                    "provider": raw_model.get("owned_by", "unknown"),
                    "gateway_tier": "litellm",
                    "context_window": None,
                    "input_price_per_million": None,
                    "output_price_per_million": None,
                    "status": "active",
                }
            )

    if tier:
        models = [m for m in models if m["gateway_tier"] == tier]
    if provider:
        models = [m for m in models if str(m["provider"]).lower() == provider.lower()]

    total = len(models)
    start = (page - 1) * per_page
    end = start + per_page
    page_models = models[start:end]

    return ApiResponse(
        data=page_models,
        meta=ApiMeta(page=page, per_page=per_page, total=total),
    )


@router.get("/providers", response_model=ApiResponse[list[dict]])
async def list_gateway_providers(
    _user: User = Depends(get_current_user),
) -> ApiResponse[list[dict]]:
    """List configured gateway providers with health status."""
    _fallback_health = {
        "status": "unknown",
        "healthy_endpoints": [],
        "unhealthy_endpoints": [],
    }
    health = await _fetch_litellm("/health", _fallback_health)

    # Build a set of healthy provider names from LiteLLM /health
    healthy_providers: set[str] = set()
    for ep in health.get("healthy_endpoints", []):
        pname = ep.get("model", "").split("/")[0].lower() if ep.get("model") else ""
        if pname:
            healthy_providers.add(pname)

    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    providers = [
        {
            **p,
            "status": "healthy" if p["id"] in healthy_providers else p.get("status", "unknown"),
            "last_checked": now_str,
        }
        for p in _GATEWAY_PROVIDERS
    ]

    return ApiResponse(
        data=providers,
        meta=ApiMeta(page=1, per_page=len(providers), total=len(providers)),
    )


@router.get("/spend", response_model=ApiResponse[dict])
async def gateway_spend(_user: User = Depends(get_current_user)) -> ApiResponse[dict]:
    """Return global spend summary from the LiteLLM proxy."""
    _fallback_spend = {"total_cost": 0, "spend_by_team": {}}
    raw = await _fetch_litellm("/global/spend", _fallback_spend)
    data = {
        "total_cost": raw.get("total_cost", 0),
        "spend_by_team": raw.get("spend_by_team", {}),
    }
    return ApiResponse(data=data, meta=ApiMeta(page=1, per_page=1, total=1))


@router.get("/teams", response_model=ApiResponse[list[dict]])
async def list_litellm_teams(_user: User = Depends(get_current_user)) -> ApiResponse[list[dict]]:
    """Return the list of LiteLLM teams (budget groups)."""
    _fallback_teams: dict = {"teams": []}
    raw = await _fetch_litellm("/team/list", _fallback_teams)
    teams: list[dict] = raw.get("teams", []) if isinstance(raw, dict) else []
    return ApiResponse(
        data=teams,
        meta=ApiMeta(page=1, per_page=len(teams), total=len(teams)),
    )


@router.get("/logs", response_model=ApiResponse[list[dict]])
async def gateway_logs(
    response: Response,
    _user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    model: str | None = Query(None),
    provider: str | None = Query(None),
    status: str | None = Query(None),
) -> ApiResponse[list[dict]]:
    """Return paginated list of recent gateway requests.

    Sources the data from the LiteLLM proxy's ``/spend/logs`` endpoint.
    LiteLLM persists every proxied call to its ``LiteLLM_SpendLogs`` table
    when a database is configured; the REST endpoint exposes recent rows.

    If LiteLLM is not running or unreachable, this endpoint returns
    ``503`` with ``data: []`` and a descriptive ``errors`` entry — it
    NEVER falls back to synthetic data.
    """
    try:
        all_entries = await fetch_spend_logs(limit=max(100, per_page * page))
    except GatewayLogsUnavailableError as exc:
        response.status_code = 503
        return ApiResponse(
            data=[],
            meta=ApiMeta(page=page, per_page=per_page, total=0),
            errors=[str(exc)],
        )

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
async def cost_comparison(
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[dict]]:
    """Return cost comparison aggregated from real ``cost_events`` rows.

    Groups recorded usage by ``(provider, model_name)`` and computes the
    average input/output price per million tokens for each combination.
    The gateway tier is inferred from the provider (or a ``provider/model``
    prefix in the model name).

    If ``cost_events`` has no rows, returns an empty list — never the
    hardcoded fixture.
    """
    # Aggregate over the cost_events table:
    #   sum(input_tokens), sum(output_tokens), sum(cost_usd) per (provider, model)
    stmt = (
        select(
            CostEvent.provider.label("provider"),
            CostEvent.model_name.label("model_name"),
            func.sum(CostEvent.input_tokens).label("input_tokens"),
            func.sum(CostEvent.output_tokens).label("output_tokens"),
            func.sum(CostEvent.cost_usd).label("cost_usd"),
            func.count(CostEvent.id).label("requests"),
        )
        .group_by(CostEvent.provider, CostEvent.model_name)
        .order_by(CostEvent.model_name)
    )

    try:
        result = await db.execute(stmt)
        rows = result.all()
    except Exception as exc:  # noqa: BLE001 — DB transient failures shouldn't 500 the page
        logger.warning("cost_events aggregation failed: %s", exc)
        rows = []

    comparison: list[dict] = []
    for row in rows:
        input_tok = int(row.input_tokens or 0)
        output_tok = int(row.output_tokens or 0)
        total_cost = float(row.cost_usd or 0.0)
        requests = int(row.requests or 0)

        # Cost is recorded as a single number per event. Without a per-call
        # input/output breakdown we cannot recover the exact per-million
        # input vs output price, so we approximate using the empirical
        # token mix on the row group: split total_cost proportional to the
        # token volume, then divide by tokens to get $/1M.
        total_tok = input_tok + output_tok
        if total_tok > 0 and total_cost > 0:
            input_share = total_cost * (input_tok / total_tok) if input_tok else 0.0
            output_share = total_cost * (output_tok / total_tok) if output_tok else 0.0
            input_per_million = round(input_share / input_tok * 1_000_000, 6) if input_tok else 0.0
            output_per_million = (
                round(output_share / output_tok * 1_000_000, 6) if output_tok else 0.0
            )
        else:
            input_per_million = 0.0
            output_per_million = 0.0

        comparison.append(
            {
                "model": row.model_name,
                "name": row.model_name,
                "provider": row.provider,
                "gateway_tier": _infer_tier_from_provider(row.provider, row.model_name),
                "input_per_million": input_per_million,
                "output_per_million": output_per_million,
                "context_window": None,
                "requests": requests,
                "total_cost_usd": round(total_cost, 6),
            }
        )

    # Sort by input price ascending (free / unknown rows last).
    comparison.sort(
        key=lambda x: float(x["input_per_million"]) if x["input_per_million"] else float("inf")
    )

    return ApiResponse(
        data=comparison,
        meta=ApiMeta(page=1, per_page=len(comparison), total=len(comparison)),
    )
