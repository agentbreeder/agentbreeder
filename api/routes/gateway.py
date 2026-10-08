"""Gateway API routes — model gateway management and proxying.

Exposes gateway status, model catalog across providers, and request log
for the AgentBreeder model gateway (LiteLLM + direct providers).

Required env vars (consumed by the LiteLLM-backed endpoints):

- ``LITELLM_BASE_URL`` — base URL of the LiteLLM proxy
  (default ``http://localhost:4000``).
- ``LITELLM_MASTER_KEY`` — master key used to authenticate against
  ``/health``, ``/v1/models``, ``/v1/provider/info``, ``/spend/logs`` etc.
  Required; gateway endpoints return empty/503 when unset.

If LiteLLM is not running, ``/logs`` returns ``503`` with an empty
``data: []`` and a clear error message; it never falls back to fake data.
"""

from __future__ import annotations

import logging
import os
import time

import httpx
from fastapi import APIRouter, Depends, Query, Response

from api.auth import get_current_user
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
_LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "")


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


@router.get("/status", response_model=ApiResponse[list[dict]])
async def gateway_status(_user: User = Depends(get_current_user)) -> ApiResponse[list[dict]]:
    """Return live status of the LiteLLM gateway tier."""
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

    tiers = [live_litellm_tier]

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
    """List the models the LiteLLM proxy reports."""
    raw = await _fetch_litellm("/models", {"data": []})
    models = [
        {
            "id": raw_model["id"],
            "name": raw_model["id"],
            "provider": raw_model.get("owned_by", "unknown"),
            "gateway_tier": "litellm",
            "context_window": None,
            "input_price_per_million": None,
            "output_price_per_million": None,
            "status": "active",
        }
        for raw_model in raw.get("data", [])
        if raw_model.get("id")
    ]

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
    """List providers seen in LiteLLM /health, with their health status."""
    _fallback_health = {
        "status": "unknown",
        "healthy_endpoints": [],
        "unhealthy_endpoints": [],
    }
    health = await _fetch_litellm("/health", _fallback_health)

    # Derive providers from the endpoints LiteLLM reports in /health.
    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    statuses: dict[str, str] = {}
    for key, status in (("unhealthy_endpoints", "unhealthy"), ("healthy_endpoints", "healthy")):
        for ep in health.get(key, []):
            pname = ep.get("model", "").split("/")[0].lower() if ep.get("model") else ""
            if pname:
                statuses[pname] = status
    providers = [
        {"id": name, "name": name, "status": status, "last_checked": now_str}
        for name, status in sorted(statuses.items())
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
