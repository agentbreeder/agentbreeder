"""AgentOps Service — fleet health, incidents, and compliance.

Provides:
- Fleet overview and health heatmap (DB-backed — ``FleetService``, #206)
- Incident management (CRUD + actions) — DB-backed via ``IncidentService`` (#207)
- SOC 2 / HIPAA compliance scans — DB-backed via ``ComplianceService`` (#208)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Agent, ComplianceScan, Incident
from api.models.enums import AgentStatus, IncidentSeverity, IncidentStatus

logger = logging.getLogger(__name__)

# ``_SEED_COMPLIANCE_CONTROLS`` was removed in #208. Compliance controls now
# come from ``engine.compliance.controls.CONTROL_REGISTRY`` and are evaluated
# against the live DB by ``ComplianceService`` below; results are persisted
# to the ``compliance_scans`` table (migration 021).


# ---------------------------------------------------------------------------
# Incident Service (DB-backed, #207)
# ---------------------------------------------------------------------------


def _serialize_incident(inc: Incident) -> dict[str, Any]:
    """Convert an ``Incident`` ORM row to the dict shape Studio expects.

    Keeps wire-compatibility with the previous in-memory representation:
      ``id``, ``agent_name``, ``title``, ``severity``, ``status``,
      ``description``, ``created_at``, ``updated_at``, ``timeline``.

    The ``agent_name`` field is stored in ``incident_metadata['agent_name']``
    so we can keep the existing UI working without joining ``agents`` on
    every read (the FK ``affected_agent_id`` is nullable and not used by
    the demo seed data, which references agents by name only).
    """
    timeline = list(inc.timeline or [])
    created_iso = inc.created_at.isoformat() if inc.created_at else ""
    if timeline:
        last = timeline[-1].get("timestamp")
        last_ts_str = last if isinstance(last, str) else created_iso
    else:
        last_ts_str = created_iso

    metadata = inc.incident_metadata or {}
    return {
        "id": str(inc.id),
        "agent_name": metadata.get("agent_name", ""),
        "title": inc.title,
        "severity": inc.severity.value if hasattr(inc.severity, "value") else str(inc.severity),
        "status": inc.status.value if hasattr(inc.status, "value") else str(inc.status),
        "description": inc.description or "",
        "created_at": created_iso,
        "updated_at": last_ts_str,
        "timeline": [
            {
                "timestamp": entry.get("timestamp", ""),
                "actor": entry.get("actor", "system"),
                "message": entry.get("message", entry.get("note", "")),
            }
            for entry in timeline
        ],
        "resolved_at": inc.resolved_at.isoformat() if inc.resolved_at else None,
    }


class IncidentService:
    """DB-backed incident management.

    Replaces the in-memory ``_incidents`` dict. All operations go through an
    ``AsyncSession`` and persist to the ``incidents`` table.
    """

    @staticmethod
    async def list_incidents(
        db: AsyncSession,
        *,
        status: str | None = None,
        severity: str | None = None,
    ) -> list[dict[str, Any]]:
        """List incidents, optionally filtered by status and/or severity."""
        stmt = select(Incident).order_by(Incident.created_at.desc())
        if status:
            stmt = stmt.where(Incident.status == IncidentStatus(status))
        if severity:
            stmt = stmt.where(Incident.severity == IncidentSeverity(severity))
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return [_serialize_incident(r) for r in rows]

    @staticmethod
    async def create_incident(
        db: AsyncSession,
        *,
        agent_name: str,
        title: str,
        severity: str,
        description: str,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """Create a new incident."""
        try:
            sev_enum = IncidentSeverity(severity)
        except ValueError:
            sev_enum = IncidentSeverity.medium

        # Best-effort look up of the affected agent — so deletes can SET NULL
        # rather than orphan the FK. Failures here (e.g. unknown agent name)
        # are non-fatal: the incident still records ``agent_name`` in metadata.
        affected_agent_id: uuid.UUID | None = None
        if agent_name:
            agent_stmt = select(Agent.id).where(Agent.name == agent_name)
            res = await db.execute(agent_stmt)
            affected_agent_id = res.scalar_one_or_none()

        now_iso = datetime.now(UTC).isoformat()
        incident = Incident(
            title=title,
            severity=sev_enum,
            status=IncidentStatus.open,
            description=description,
            created_by=created_by,
            affected_agent_id=affected_agent_id,
            timeline=[
                {
                    "timestamp": now_iso,
                    "actor": created_by or "system",
                    "message": "Incident created",
                }
            ],
            incident_metadata={"agent_name": agent_name},
        )
        db.add(incident)
        await db.flush()
        await db.refresh(incident)
        logger.info(
            "Incident created",
            extra={"incident_id": str(incident.id), "agent": agent_name},
        )
        return _serialize_incident(incident)

    @staticmethod
    async def get_incident(db: AsyncSession, incident_id: str) -> dict[str, Any] | None:
        """Get a single incident by ID."""
        try:
            inc_uuid = uuid.UUID(incident_id)
        except (ValueError, TypeError):
            return None
        inc = await db.get(Incident, inc_uuid)
        if inc is None:
            return None
        return _serialize_incident(inc)

    @staticmethod
    async def update_incident(
        db: AsyncSession,
        incident_id: str,
        *,
        status: str | None = None,
        message: str | None = None,
        actor: str = "operator",
    ) -> dict[str, Any] | None:
        """Update incident status and append to timeline.

        Valid status transitions:
          open → investigating → mitigated → resolved
        """
        try:
            inc_uuid = uuid.UUID(incident_id)
        except (ValueError, TypeError):
            return None
        inc = await db.get(Incident, inc_uuid)
        if inc is None:
            return None

        now = datetime.now(UTC)
        now_iso = now.isoformat()
        timeline = list(inc.timeline or [])

        if status is not None:
            try:
                new_status = IncidentStatus(status)
            except ValueError:
                return None
            old_status = inc.status.value if hasattr(inc.status, "value") else str(inc.status)
            timeline.append(
                {
                    "timestamp": now_iso,
                    "actor": actor,
                    "message": message or f"Status changed: {old_status} → {new_status.value}",
                }
            )
            inc.status = new_status
            if new_status == IncidentStatus.resolved:
                inc.resolved_at = now
        elif message:
            timeline.append(
                {
                    "timestamp": now_iso,
                    "actor": actor,
                    "message": message,
                }
            )

        inc.timeline = timeline
        await db.flush()
        await db.refresh(inc)
        return _serialize_incident(inc)


# ---------------------------------------------------------------------------
# Fleet Service (DB-backed, #206)
# ---------------------------------------------------------------------------


def _agent_status_to_health(
    status: AgentStatus | str,
    error_rate_pct: float,
) -> tuple[str, int]:
    """Map a registry ``AgentStatus`` + recent error rate to the
    ``healthy / degraded / down`` triage Studio expects, plus a
    ``health_score`` in [0, 100].

    Heuristic:
      - ``failed`` agents are always ``down`` (score 0).
      - ``stopped`` agents are ``down`` (score 10) — no traffic flowing.
      - ``deploying`` agents are ``degraded`` (score 50) until they're
        live and start emitting traces.
      - ``running`` agents are scored from observed error rate over the
        last 24h: ``health = max(0, 100 - error_rate_pct * 4)``.
            * < 5%  → healthy (≥ 80 score)
            * 5–25% → degraded
            * ≥ 25% → down
    """
    raw = status.value if hasattr(status, "value") else str(status)
    if raw == AgentStatus.failed.value:
        return "down", 0
    if raw == AgentStatus.stopped.value:
        return "down", 10
    if raw == AgentStatus.deploying.value:
        return "degraded", 50

    # running — derive from recent error rate
    score = max(0, min(100, int(round(100 - error_rate_pct * 4))))
    if error_rate_pct >= 25.0:
        return "down", score
    if error_rate_pct >= 5.0:
        return "degraded", score
    return "healthy", score


async def _build_fleet_rows(db: AsyncSession) -> list[dict[str, Any]]:
    """Compose the per-agent fleet snapshot from the ``agents`` registry.

    Health is derived from the registry status alone — no per-agent traffic
    telemetry is collected yet, so none is reported.
    """
    stmt = select(
        Agent.id,
        Agent.name,
        Agent.team,
        Agent.status,
        Agent.framework,
        Agent.model_primary,
        Agent.updated_at,
    ).order_by(Agent.name)

    result = await db.execute(stmt)
    rows: list[dict[str, Any]] = []
    for r in result.all():
        status, health = _agent_status_to_health(r.status, 0.0)
        rows.append(
            {
                "id": str(r.id),
                "name": r.name,
                "team": r.team,
                "status": status,
                "health_score": health,
                "last_deploy": r.updated_at.isoformat() if r.updated_at else "",
                "model": r.model_primary or "",
                "framework": r.framework or "",
            }
        )
    return rows


class FleetService:
    """DB-backed read service for the fleet overview and health heatmap (#206)."""

    @staticmethod
    async def get_fleet_overview(db: AsyncSession) -> dict[str, Any]:
        """All registered agents with health and last-deploy info."""
        agents = await _build_fleet_rows(db)
        total = len(agents)
        healthy = sum(1 for a in agents if a["status"] == "healthy")
        degraded = sum(1 for a in agents if a["status"] == "degraded")
        down = sum(1 for a in agents if a["status"] == "down")
        avg_health = round(sum(a["health_score"] for a in agents) / total, 1) if total else 0.0
        return {
            "agents": agents,
            "summary": {
                "total": total,
                "healthy": healthy,
                "degraded": degraded,
                "down": down,
                "avg_health_score": avg_health,
            },
        }

    @staticmethod
    async def get_fleet_heatmap(db: AsyncSession) -> dict[str, Any]:
        """Health heatmap grid — one cell per registered agent."""
        agents = await _build_fleet_rows(db)
        grid = [
            {
                "agent_id": a["id"],
                "name": a["name"],
                "team": a["team"],
                "health_score": a["health_score"],
                "status": a["status"],
            }
            for a in agents
        ]
        return {"grid": grid, "total": len(grid)}


# ---------------------------------------------------------------------------
# Compliance Service (DB-backed, #208)
# ---------------------------------------------------------------------------


class ComplianceService:
    """Real SOC 2 / HIPAA control scanner — replaces ``_SEED_COMPLIANCE_CONTROLS``.

    Each call to :meth:`run_and_persist` executes every control in
    ``engine.compliance.controls.CONTROL_REGISTRY`` against the live
    ``AsyncSession`` and writes a single row to ``compliance_scans``. The
    ``status_payload`` and ``report_payload`` shapes are kept wire-compatible
    with the previous in-memory responses so Studio does not need to
    change its TypeScript types.
    """

    @staticmethod
    async def run_and_persist(db: AsyncSession) -> ComplianceScan:
        """Run a fresh scan and insert the row. Method commits before
        returning so the caller can immediately read the persisted row."""
        from engine.compliance import run_compliance_scan

        summary = await run_compliance_scan(db)
        row = ComplianceScan(
            ran_at=datetime.fromisoformat(summary.ran_at),
            overall_status=summary.overall_status,
            results=[r.to_dict() for r in summary.results],
            summary=summary.summary_dict(),
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def get_or_run_latest(db: AsyncSession, *, max_age_seconds: int = 60) -> ComplianceScan:
        """Return the most recent scan, or run a fresh one if it's stale.

        Reading from Studio at high frequency must not re-run controls on
        every render — we cache the most recent scan for ``max_age_seconds``
        (default 60s).
        """
        latest = await db.execute(
            select(ComplianceScan).order_by(ComplianceScan.ran_at.desc()).limit(1)
        )
        row = latest.scalar_one_or_none()
        if row is not None and row.ran_at is not None:
            ran_at = row.ran_at if row.ran_at.tzinfo else row.ran_at.replace(tzinfo=UTC)
            age = (datetime.now(UTC) - ran_at).total_seconds()
            if age < max_age_seconds:
                return row
        return await ComplianceService.run_and_persist(db)

    @staticmethod
    def status_payload(row: ComplianceScan) -> dict[str, Any]:
        """Wire-compatible payload for ``GET /agentops/compliance/status``.

        Studio's ``Control`` interface expects ``id``, ``name``,
        ``category``, ``status``, and ``last_checked`` — provided directly by
        ``ControlResult.to_dict()``.
        """
        results = list(row.results or [])
        summary = dict(row.summary or {})
        ran_at = row.ran_at.isoformat() if row.ran_at else datetime.now(UTC).isoformat()
        return {
            "overall_status": row.overall_status,
            "controls_total": summary.get("controls_total", len(results)),
            "controls_passed": summary.get(
                "controls_passed", sum(1 for r in results if r.get("status") == "pass")
            ),
            "controls_failed": summary.get(
                "controls_failed", sum(1 for r in results if r.get("status") == "fail")
            ),
            "controls_partial": summary.get(
                "controls_partial", sum(1 for r in results if r.get("status") == "partial")
            ),
            "controls_skipped": summary.get(
                "controls_skipped", sum(1 for r in results if r.get("status") == "skipped")
            ),
            "last_checked": ran_at,
            "scan_id": str(row.id),
            "controls": results,
        }

    @staticmethod
    def report_payload(row: ComplianceScan, report_format: str = "json") -> dict[str, Any]:
        """Wire-compatible payload for ``GET /agentops/compliance/report``.

        The previous shape rendered ``"Automated compliance check for X"`` as
        a placeholder ``details`` string. We now embed the *real* per-control
        evidence dict and details so the downloaded JSON / PDF cites concrete
        evidence (row counts, oldest timestamps, backend names).
        """
        results = list(row.results or [])
        summary = dict(row.summary or {})
        ran_at = row.ran_at.isoformat() if row.ran_at else datetime.now(UTC).isoformat()
        evidence = [
            {
                "control_id": r.get("id"),
                "control_name": r.get("name"),
                "category": r.get("category"),
                "status": r.get("status"),
                "last_checked": r.get("last_checked", ran_at),
                "evidence_type": "automated_check",
                "evidence": r.get("evidence", {}),
                "details": r.get("details", ""),
            }
            for r in results
        ]
        return {
            "report_id": f"rpt-{str(row.id)[:8]}",
            "scan_id": str(row.id),
            "generated_at": ran_at,
            "format": report_format,
            "overall_status": row.overall_status,
            "controls_passed": summary.get(
                "controls_passed", sum(1 for r in results if r.get("status") == "pass")
            ),
            "controls_failed": summary.get(
                "controls_failed", sum(1 for r in results if r.get("status") == "fail")
            ),
            "controls_partial": summary.get(
                "controls_partial", sum(1 for r in results if r.get("status") == "partial")
            ),
            "controls_skipped": summary.get(
                "controls_skipped", sum(1 for r in results if r.get("status") == "skipped")
            ),
            "controls_total": summary.get("controls_total", len(results)),
            "evidence": evidence,
        }
