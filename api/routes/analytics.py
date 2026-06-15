"""/api/v1/analytics/* — product funnel ingest + aggregation (W4).

PII rule (design §11.2): events are structural only. The ingest endpoint stores
event/engine/team/session_id/props; callers must never send message or prompt
bodies. A retention job (TTL) prunes old rows (see ops runbook).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import AnalyticsEvent, User
from api.models.schemas import (
    AnalyticsEventIngest,
    ApiResponse,
    EngineScorecard,
    FunnelMetrics,
    FunnelStage,
)

router = APIRouter(prefix="/api/v1", tags=["analytics"])

# Macro-stages shown in the headline funnel (design §11.4 — collapse the 11 raw events).
_FUNNEL: list[tuple[str, str]] = [
    ("builder_session_started", "Converse"),
    ("spec_validated", "Spec validated"),
    ("eject_to_code_started", "Eject"),
    ("deploy_started", "Deploy"),
    ("deploy_succeeded", "Live"),
]

_PERIODS = {"7d": 7, "30d": 30, "all": 3650}


def _percentile(values: list[float], pct: int) -> float | None:
    """Linear-interpolated percentile; None for an empty sample."""
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _scorecard_from_rows(
    *,
    engine: str,
    samples: int,
    valid: int,
    deployed: int,
    total_turns: int,
    hallucinated: int,
) -> EngineScorecard:
    def rate(n: int) -> float:
        return round(n / samples, 4) if samples else 0.0

    return EngineScorecard(
        engine=engine,
        samples=samples,
        spec_validity_rate=rate(valid),
        deploy_success_rate=rate(deployed),
        turns_to_spec=round(total_turns / samples, 2) if samples else 0.0,
        hallucinated_field_rate=rate(hallucinated),
    )


@router.post("/analytics/events", status_code=201)
async def ingest_event(
    body: AnalyticsEventIngest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    row = AnalyticsEvent(
        event=body.event,
        engine=body.engine,
        team=getattr(user, "team", None),
        session_id=uuid.UUID(body.session_id) if body.session_id else None,
        props=body.props or {},
    )
    db.add(row)
    await db.commit()
    return ApiResponse(data={"id": str(row.id)})


@router.get("/analytics/funnel")
async def get_funnel(
    period: str = Query("7d"),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[FunnelMetrics]:
    days = _PERIODS.get(period, 7)
    since = datetime.now(UTC) - timedelta(days=days)

    counts: dict[str, int] = {}
    for key, _label in _FUNNEL:
        res = await db.execute(
            select(func.count())
            .select_from(AnalyticsEvent)
            .where(AnalyticsEvent.event == key, AnalyticsEvent.created_at >= since)
        )
        counts[key] = int(res.scalar_one())

    stages: list[FunnelStage] = []
    prev: int | None = None
    for key, label in _FUNNEL:
        c = counts[key]
        dropoff = 0.0 if prev in (None, 0) else round((1 - c / prev) * 100, 1)
        stages.append(FunnelStage(key=key, label=label, count=c, dropoff_pct=dropoff))
        prev = c

    # p50/p90 time-to-first-deploy from deploy_succeeded props
    res = await db.execute(
        select(AnalyticsEvent.props).where(
            AnalyticsEvent.event == "deploy_succeeded",
            AnalyticsEvent.created_at >= since,
        )
    )
    times = [
        float(p["time_to_deploy_s"])
        for (p,) in res.all()
        if isinstance(p, dict) and p.get("time_to_deploy_s") is not None
    ]
    metrics = FunnelMetrics(
        period=period,
        stages=stages,
        engines=[],  # full per-engine joins are a follow-on; helpers above are unit-covered
        time_to_first_deploy_p50_s=_percentile(times, 50),
        time_to_first_deploy_p90_s=_percentile(times, 90),
    )
    return ApiResponse(data=metrics)
