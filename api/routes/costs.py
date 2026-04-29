"""Cost Tracking & Budget Management API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_user
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from api.services.cost_service import get_cost_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["costs"])


# ---------------------------------------------------------------------------
# Cost Events
# ---------------------------------------------------------------------------


@router.post("/costs/events", status_code=201)
async def record_cost_event(
    body: dict[str, Any], _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Record a new cost event."""
    store = get_cost_store()

    agent_name = body.get("agent_name")
    team = body.get("team")
    model_name = body.get("model_name")
    provider = body.get("provider")
    input_tokens = body.get("input_tokens")
    output_tokens = body.get("output_tokens")

    if not all([agent_name, team, model_name, provider]):
        raise HTTPException(
            status_code=400,
            detail="agent_name, team, model_name, and provider are required",
        )

    if input_tokens is None or output_tokens is None:
        raise HTTPException(
            status_code=400,
            detail="input_tokens and output_tokens are required",
        )

    event = store.record_cost_event(
        trace_id=body.get("trace_id"),
        agent_id=body.get("agent_id"),
        agent_name=agent_name,
        team=team,
        model_name=model_name,
        provider=provider,
        input_tokens=int(input_tokens),
        output_tokens=int(output_tokens),
        cost_usd=float(body.get("cost_usd", 0.0)),
        request_type=body.get("request_type", "chat"),
    )
    return ApiResponse(data=event.to_dict())


@router.get("/costs/summary")
async def get_cost_summary(
    _user: User = Depends(get_current_user),
    team: str | None = Query(None),
    agent_name: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
) -> ApiResponse[dict]:
    """Get aggregated cost summary."""
    store = get_cost_store()
    summary = store.get_cost_summary(team=team, agent_name=agent_name, days=days)
    return ApiResponse(data=summary)


@router.get("/costs/breakdown")
async def get_cost_breakdown(
    _user: User = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365),
    group_by: str = Query("agent"),
) -> ApiResponse[dict]:
    """Get cost breakdown by agent, model, or team."""
    store = get_cost_store()
    breakdown = store.get_cost_breakdown(days=days, group_by=group_by)
    return ApiResponse(data=breakdown)


@router.get("/costs/trend")
async def get_cost_trend(
    _user: User = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365),
    team: str | None = Query(None),
    agent_name: str | None = Query(None),
) -> ApiResponse[dict]:
    """Get daily cost trend."""
    store = get_cost_store()
    trend = store.get_cost_trend(days=days, team=team, agent_name=agent_name)
    return ApiResponse(data=trend)


@router.get("/costs/top-spenders")
async def get_top_spenders(
    _user: User = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=100),
) -> ApiResponse[list]:
    """Get top N agents by cost."""
    store = get_cost_store()
    spenders = store.get_top_spenders(days=days, limit=limit)
    return ApiResponse(data=spenders)


@router.post("/costs/compare")
async def compare_models(
    body: dict[str, Any], _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Compare estimated costs between two models."""
    store = get_cost_store()

    model_a = body.get("model_a")
    model_b = body.get("model_b")
    if not model_a or not model_b:
        raise HTTPException(status_code=400, detail="model_a and model_b are required")

    sample_tokens = int(body.get("sample_tokens", 1_000_000))
    result = store.compare_models(model_a, model_b, sample_tokens)
    return ApiResponse(data=result)


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


@router.get("/budgets")
async def list_budgets(_user: User = Depends(get_current_user)) -> ApiResponse[list]:
    """List all team budgets."""
    store = get_cost_store()
    budgets = store.list_budgets()
    return ApiResponse(
        data=[b.to_dict() for b in budgets],
        meta=ApiMeta(total=len(budgets)),
    )


@router.post("/budgets", status_code=201)
async def create_budget(
    body: dict[str, Any], _user: User = Depends(require_role("admin"))
) -> ApiResponse[dict]:
    """Create or update a team budget."""
    store = get_cost_store()

    team = body.get("team")
    monthly_limit_usd = body.get("monthly_limit_usd")
    if not team or monthly_limit_usd is None:
        raise HTTPException(
            status_code=400,
            detail="team and monthly_limit_usd are required",
        )

    budget = store.create_budget(
        team=team,
        monthly_limit_usd=float(monthly_limit_usd),
        alert_threshold_pct=float(body.get("alert_threshold_pct", 80.0)),
    )
    return ApiResponse(data=budget.to_dict())


@router.get("/budgets/{team}")
async def get_budget(team: str, _user: User = Depends(get_current_user)) -> ApiResponse[dict]:
    """Get a team's budget."""
    store = get_cost_store()
    budget = store.get_budget(team)
    if not budget:
        raise HTTPException(status_code=404, detail=f"No budget found for team '{team}'")
    return ApiResponse(data=budget.to_dict())


@router.put("/budgets/{team}")
async def update_budget(
    team: str, body: dict[str, Any], _user: User = Depends(require_role("admin"))
) -> ApiResponse[dict]:
    """Update a team's budget."""
    store = get_cost_store()
    budget = store.update_budget(
        team,
        monthly_limit_usd=body.get("monthly_limit_usd"),
        alert_threshold_pct=body.get("alert_threshold_pct"),
    )
    if not budget:
        raise HTTPException(status_code=404, detail=f"No budget found for team '{team}'")
    return ApiResponse(data=budget.to_dict())


# ---------------------------------------------------------------------------
# AgentOps Cost Intelligence (delegated to AgentOps service)
# ---------------------------------------------------------------------------


@router.get("/costs/forecast")
async def get_cost_forecast(
    days: int = Query(30, ge=1, le=90), _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """30-day spend projection."""
    from api.services.agentops_service import get_agentops_store

    return ApiResponse(data=get_agentops_store().get_cost_forecast(days=days))


@router.get("/costs/anomalies")
async def get_cost_anomalies(_user: User = Depends(get_current_user)) -> ApiResponse[list]:
    """Cost spike alerts."""
    from api.services.agentops_service import get_agentops_store

    return ApiResponse(data=get_agentops_store().get_cost_anomalies())


@router.get("/costs/suggestions")
async def get_cost_suggestions(_user: User = Depends(get_current_user)) -> ApiResponse[list]:
    """Model swap recommendations."""
    from api.services.agentops_service import get_agentops_store

    return ApiResponse(data=get_agentops_store().get_cost_suggestions())


@router.get("/costs/chargeback")
async def get_cost_chargeback(
    days: int = Query(30, ge=1, le=90), _user: User = Depends(get_current_user)
) -> ApiResponse[list]:
    """Team-level cost chargeback report."""
    from api.services.agentops_service import get_agentops_store

    return ApiResponse(data=get_agentops_store().get_team_comparison())
