"""AgentOps — Unified Operations API routes (powers the Fleet view in Studio)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.middleware.rbac import require_role
from api.models.database import User
from api.models.schemas import ApiMeta, ApiResponse
from api.services.agentops_service import (
    ComplianceService,
    FleetService,
    IncidentService,
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
    """Return all registered agents with health and last deploy info."""
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
