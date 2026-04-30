"""AgentOps — Unified Operations Dashboard API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.middleware.rbac import require_role
from api.models.costs import CostEvent
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from api.services.agentops_service import (
    ComplianceService,
    FleetService,
    IncidentService,
    get_agentops_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agentops", tags=["agentops"])


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------


@router.get("/fleet")
async def get_fleet_overview(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Return all agents with health, cost, and last deploy info.

    Joins the registry (``agents``) with 24h aggregates over ``traces``
    and ``cost_events``. Agents with no recent traffic show zeroed
    metrics — never seed fakes.
    """
    overview = await FleetService.get_fleet_overview(db)
    return ApiResponse(
        data=overview,
        meta=ApiMeta(total=overview["summary"]["total"]),
    )


@router.get("/fleet/heatmap")
async def get_fleet_heatmap(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Return health heatmap grid data for visualization."""
    heatmap = await FleetService.get_fleet_heatmap(db)
    return ApiResponse(data=heatmap, meta=ApiMeta(total=heatmap["total"]))


@router.get("/top-agents")
async def get_top_agents(
    metric: str = Query("cost", description="One of: cost | errors | latency | invocations"),
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list]:
    """Top N agents by cost, errors, latency, or invocations.

    Ranking is computed over the same ``agents`` × ``traces`` × ``cost_events``
    join used by ``/fleet``. Cost ranks by ``cost_events.cost_usd`` summed
    over the last 24h; invocations / errors / latency rank from ``traces``.
    """
    agents = await FleetService.get_top_agents(db, metric=metric, limit=limit)
    return ApiResponse(data=agents, meta=ApiMeta(total=len(agents)))


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.get("/events")
async def get_events(
    limit: int = Query(50, ge=1, le=500),
    since: str | None = Query(None, description="ISO timestamp — return events after this time"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list]:
    """Recent operations events (deploys, alerts, restarts, cost spikes).

    Derived from ``audit_events`` (deploys, incidents, restarts) and
    ``cost_events`` (single requests over $1.00 surfaced as cost
    spikes). Returns ``[]`` cleanly on a fresh deploy.
    """
    events = await FleetService.get_events(db, limit=limit, since=since)
    return ApiResponse(data=events, meta=ApiMeta(total=len(events)))


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


@router.get("/teams")
async def get_team_comparison(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list]:
    """Team-level metrics comparison.

    Reuses the #207 ``IncidentService`` to count open incidents per
    agent, then aggregates 24h spend from ``cost_events`` directly so
    the team total stays consistent with what the gateway/cost views
    show.
    """
    open_by_agent = await IncidentService.open_count_by_agent_name(db)
    teams = await FleetService.get_team_comparison(db, open_incidents_by_agent=open_by_agent)
    return ApiResponse(data=teams, meta=ApiMeta(total=len(teams)))


# ---------------------------------------------------------------------------
# Incidents (DB-backed, #207)
# ---------------------------------------------------------------------------


@router.get("/incidents")
async def list_incidents(
    status: str | None = Query(None, description="open|investigating|mitigated|resolved"),
    severity: str | None = Query(None, description="critical|high|medium|low"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list]:
    """List all incidents."""
    incidents = await IncidentService.list_incidents(db, status=status, severity=severity)
    return ApiResponse(data=incidents, meta=ApiMeta(total=len(incidents)))


@router.post("/incidents", status_code=201)
async def create_incident(
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("deployer")),
) -> ApiResponse[dict]:
    """Create a new incident."""
    agent_name = body.get("agent_name")
    title = body.get("title")
    severity = body.get("severity", "medium")
    description = body.get("description", "")

    if not agent_name or not title:
        raise HTTPException(status_code=400, detail="agent_name and title are required")

    incident = await IncidentService.create_incident(
        db,
        agent_name=agent_name,
        title=title,
        severity=severity,
        description=description,
        created_by=getattr(user, "email", None),
    )
    return ApiResponse(data=incident)


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Get a single incident by ID."""
    incident = await IncidentService.get_incident(db, incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return ApiResponse(data=incident)


@router.put("/incidents/{incident_id}")
async def update_incident(
    incident_id: str,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Update an incident (status transition or add timeline message)."""
    incident = await IncidentService.update_incident(
        db,
        incident_id,
        status=body.get("status"),
        message=body.get("message"),
        actor=getattr(user, "email", "operator") or "operator",
    )
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return ApiResponse(data=incident)


@router.post("/incidents/{incident_id}/actions")
async def execute_action(
    incident_id: str,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Execute a remediation action: restart | rollback | scale | disable.

    Note: the action is recorded in the incident timeline. Wiring the action
    to real deploy / rollback / scale machinery is deferred to a follow-up
    PR (#207 — needs the deployer abstraction).
    """
    action = body.get("action")
    if not action:
        raise HTTPException(status_code=400, detail="action is required")

    result = await IncidentService.execute_action(
        db,
        incident_id,
        action,
        actor=getattr(user, "email", "operator") or "operator",
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Action failed"))
    return ApiResponse(data=result)


# ---------------------------------------------------------------------------
# Canary Deploys
# ---------------------------------------------------------------------------


@router.post("/canary", status_code=201)
async def start_canary(
    body: dict[str, Any], _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Start a new canary deployment."""
    store = get_agentops_store()

    agent_name = body.get("agent_name")
    version = body.get("version")
    traffic_percent = body.get("traffic_percent", 10)

    if not agent_name or not version:
        raise HTTPException(status_code=400, detail="agent_name and version are required")

    canary = store.start_canary(
        agent_name=agent_name,
        version=version,
        traffic_percent=int(traffic_percent),
    )
    return ApiResponse(data=canary)


@router.put("/canary/{canary_id}")
async def update_canary(
    canary_id: str, body: dict[str, Any], _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Update canary traffic split or abort."""
    store = get_agentops_store()
    canary = store.update_canary(
        canary_id,
        traffic_percent=body.get("traffic_percent"),
        abort=bool(body.get("abort", False)),
    )
    if not canary:
        raise HTTPException(status_code=404, detail=f"Canary '{canary_id}' not found")
    return ApiResponse(data=canary)


@router.get("/canary/{canary_id}")
async def get_canary(canary_id: str, _user: User = Depends(get_current_user)) -> ApiResponse[dict]:
    """Get a canary deployment by ID."""
    store = get_agentops_store()
    canary = store.get_canary(canary_id)
    if not canary:
        raise HTTPException(status_code=404, detail=f"Canary '{canary_id}' not found")
    return ApiResponse(data=canary)


# ---------------------------------------------------------------------------
# Cost Intelligence
# ---------------------------------------------------------------------------


@router.get("/costs/forecast")
async def get_cost_forecast(
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """30-day spend projection anchored on the last 24h of real spend."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    cutoff = datetime.now(UTC) - timedelta(hours=24)
    stmt = select(func.coalesce(func.sum(CostEvent.cost_usd), 0.0)).where(
        CostEvent.created_at >= cutoff
    )
    base = float((await db.execute(stmt)).scalar() or 0.0)

    store = get_agentops_store()
    return ApiResponse(data=store.get_cost_forecast(days=days, base_daily_cost=base))


@router.get("/costs/anomalies")
async def get_cost_anomalies(_user: User = Depends(get_current_user)) -> ApiResponse[list]:
    """Cost spike alerts."""
    store = get_agentops_store()
    anomalies = store.get_cost_anomalies()
    return ApiResponse(data=anomalies, meta=ApiMeta(total=len(anomalies)))


@router.get("/costs/suggestions")
async def get_cost_suggestions(_user: User = Depends(get_current_user)) -> ApiResponse[list]:
    """Model swap recommendations."""
    store = get_agentops_store()
    suggestions = store.get_cost_suggestions()
    return ApiResponse(data=suggestions, meta=ApiMeta(total=len(suggestions)))


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------


@router.get("/compliance/status")
async def get_compliance_status(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """SOC 2 / HIPAA compliance checks overview.

    Runs the controls registered in ``engine.compliance.controls`` against
    the live database (re-using the most recent scan if it's < 60s old) and
    persists each scan to the ``compliance_scans`` table (#208).
    """
    row = await ComplianceService.get_or_run_latest(db)
    return ApiResponse(data=ComplianceService.status_payload(row))


@router.post("/compliance/scan")
async def trigger_compliance_scan(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Force a fresh scan, ignoring the 60s read-through cache (#208)."""
    row = await ComplianceService.run_and_persist(db)
    return ApiResponse(data=ComplianceService.status_payload(row))


@router.get("/compliance/report")
async def generate_compliance_report(
    report_format: str = Query("json", description="Report format: json | csv | pdf"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Generate a SOC 2 / HIPAA evidence export from the latest scan.

    The report cites the *real* per-control evidence (row counts, oldest
    audit-event timestamps, secrets-backend names, etc.) recorded by the
    most recent scan in the ``compliance_scans`` table (#208).
    """
    row = await ComplianceService.get_or_run_latest(db)
    return ApiResponse(data=ComplianceService.report_payload(row, report_format=report_format))
