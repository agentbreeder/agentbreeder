"""AgentOps — Unified Operations Dashboard API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import get_current_user
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from api.services.agentops_service import get_agentops_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agentops", tags=["agentops"])


# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------


@router.get("/fleet")
async def get_fleet_overview(_user: User = Depends(get_current_user)) -> ApiResponse[dict]:
    """Return all agents with health, cost, and last deploy info."""
    store = get_agentops_store()
    overview = store.get_fleet_overview()
    return ApiResponse(
        data=overview,
        meta=ApiMeta(total=overview["summary"]["total"]),
    )


@router.get("/fleet/heatmap")
async def get_fleet_heatmap(_user: User = Depends(get_current_user)) -> ApiResponse[dict]:
    """Return health heatmap grid data for visualization."""
    store = get_agentops_store()
    heatmap = store.get_fleet_heatmap()
    return ApiResponse(data=heatmap, meta=ApiMeta(total=heatmap["total"]))


@router.get("/top-agents")
async def get_top_agents(
    metric: str = Query("cost", description="One of: cost | errors | latency | invocations"),
    limit: int = Query(5, ge=1, le=20),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list]:
    """Top N agents by cost, errors, latency, or invocations."""
    store = get_agentops_store()
    agents = store.get_top_agents(metric=metric, limit=limit)
    return ApiResponse(data=agents, meta=ApiMeta(total=len(agents)))


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.get("/events")
async def get_events(
    limit: int = Query(50, ge=1, le=500),
    since: str | None = Query(None, description="ISO timestamp — return events after this time"),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list]:
    """Recent operations events (deploys, alerts, restarts, cost spikes)."""
    store = get_agentops_store()
    events = store.get_events(limit=limit, since=since)
    return ApiResponse(data=events, meta=ApiMeta(total=len(events)))


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------


@router.get("/teams")
async def get_team_comparison(_user: User = Depends(get_current_user)) -> ApiResponse[list]:
    """Team-level metrics comparison."""
    store = get_agentops_store()
    teams = store.get_team_comparison()
    return ApiResponse(data=teams, meta=ApiMeta(total=len(teams)))


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


@router.get("/incidents")
async def list_incidents(
    status: str | None = Query(None, description="open|investigating|mitigated|resolved"),
    severity: str | None = Query(None, description="critical|high|medium|low"),
    _user: User = Depends(get_current_user),
) -> ApiResponse[list]:
    """List all incidents."""
    store = get_agentops_store()
    incidents = store.list_incidents(status=status, severity=severity)
    return ApiResponse(data=incidents, meta=ApiMeta(total=len(incidents)))


@router.post("/incidents", status_code=201)
async def create_incident(
    body: dict[str, Any], _user: User = Depends(require_role("deployer"))
) -> ApiResponse[dict]:
    """Create a new incident."""
    store = get_agentops_store()

    agent_name = body.get("agent_name")
    title = body.get("title")
    severity = body.get("severity", "medium")
    description = body.get("description", "")

    if not agent_name or not title:
        raise HTTPException(status_code=400, detail="agent_name and title are required")

    incident = store.create_incident(
        agent_name=agent_name,
        title=title,
        severity=severity,
        description=description,
    )
    return ApiResponse(data=incident)


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: str, _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Get a single incident by ID."""
    store = get_agentops_store()
    incident = store.get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return ApiResponse(data=incident)


@router.put("/incidents/{incident_id}")
async def update_incident(
    incident_id: str, body: dict[str, Any], _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Update an incident (status transition or add timeline message)."""
    store = get_agentops_store()
    incident = store.update_incident(
        incident_id,
        status=body.get("status"),
        message=body.get("message"),
    )
    if not incident:
        raise HTTPException(status_code=404, detail=f"Incident '{incident_id}' not found")
    return ApiResponse(data=incident)


@router.post("/incidents/{incident_id}/actions")
async def execute_action(
    incident_id: str, body: dict[str, Any], _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """Execute a remediation action: restart | rollback | scale | disable."""
    store = get_agentops_store()
    action = body.get("action")
    if not action:
        raise HTTPException(status_code=400, detail="action is required")

    result = store.execute_action(incident_id, action)
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
    days: int = Query(30, ge=1, le=90), _user: User = Depends(get_current_user)
) -> ApiResponse[dict]:
    """30-day spend projection."""
    store = get_agentops_store()
    return ApiResponse(data=store.get_cost_forecast(days=days))


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
async def get_compliance_status(_user: User = Depends(get_current_user)) -> ApiResponse[dict]:
    """SOC2 compliance checks overview."""
    store = get_agentops_store()
    return ApiResponse(data=store.get_compliance_status())


@router.get("/compliance/report")
async def generate_compliance_report(
    report_format: str = Query("json", description="Report format: json | csv | pdf"),
    _user: User = Depends(get_current_user),
) -> ApiResponse[dict]:
    """Generate a SOC2 evidence export."""
    store = get_agentops_store()
    return ApiResponse(data=store.generate_compliance_report(report_format=report_format))
