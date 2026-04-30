"""AgentOps Service — unified operations dashboard for fleet health, incidents,
canary deploys, cost forecasting, and compliance.

Provides:
- Fleet overview and health heatmap
- Top-N agent rankings by cost, errors, latency, invocations
- Real-time operations event stream
- Incident management (CRUD + actions) — DB-backed via ``IncidentService``
- Canary deploy management
- Cost forecasting and anomaly detection
- SOC2 compliance status and reporting
- Team comparison metrics

Incidents previously lived in an in-process dict (``_incidents``) seeded from
``_SEED_INCIDENTS``. Both are removed as of #207 — see ``IncidentService``
below for the DB-backed replacement and migration ``020_incidents_table.py``
for the schema.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import Agent, Incident
from api.models.enums import IncidentSeverity, IncidentStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed Data — fleet / events / cost / compliance
# ---------------------------------------------------------------------------
# These remain as in-memory demo seed data because they back read-only views
# (fleet snapshot, recent events, cost forecasts, SOC2 demo). They will move
# to real backends in their own follow-up issues. Incidents are the only
# AgentOps surface that supported user writes, so they are the only one that
# needed PostgreSQL persistence in #207.

_SEED_AGENTS = [
    {
        "id": "agent-001",
        "name": "customer-support-agent",
        "team": "customer-success",
        "status": "healthy",
        "health_score": 97,
        "invocations_24h": 4820,
        "error_rate_pct": 0.4,
        "avg_latency_ms": 320,
        "cost_24h_usd": 28.50,
        "last_deploy": "2026-03-12T10:00:00+00:00",
        "model": "claude-sonnet-4.6",
        "framework": "langgraph",
    },
    {
        "id": "agent-002",
        "name": "code-review-agent",
        "team": "engineering",
        "status": "healthy",
        "health_score": 91,
        "invocations_24h": 2100,
        "error_rate_pct": 1.2,
        "avg_latency_ms": 870,
        "cost_24h_usd": 61.20,
        "last_deploy": "2026-03-11T14:30:00+00:00",
        "model": "claude-opus-4.6",
        "framework": "openai_agents",
    },
    {
        "id": "agent-003",
        "name": "data-pipeline-agent",
        "team": "data",
        "status": "degraded",
        "health_score": 63,
        "invocations_24h": 980,
        "error_rate_pct": 7.8,
        "avg_latency_ms": 1450,
        "cost_24h_usd": 14.30,
        "last_deploy": "2026-03-10T09:00:00+00:00",
        "model": "gpt-4o",
        "framework": "crewai",
    },
    {
        "id": "agent-004",
        "name": "sales-outreach-agent",
        "team": "sales",
        "status": "healthy",
        "health_score": 88,
        "invocations_24h": 3400,
        "error_rate_pct": 2.1,
        "avg_latency_ms": 510,
        "cost_24h_usd": 19.80,
        "last_deploy": "2026-03-12T08:00:00+00:00",
        "model": "gpt-4o",
        "framework": "langgraph",
    },
    {
        "id": "agent-005",
        "name": "doc-generation-agent",
        "team": "engineering",
        "status": "healthy",
        "health_score": 95,
        "invocations_24h": 1560,
        "error_rate_pct": 0.8,
        "avg_latency_ms": 620,
        "cost_24h_usd": 42.70,
        "last_deploy": "2026-03-11T16:00:00+00:00",
        "model": "claude-sonnet-4.6",
        "framework": "claude_sdk",
    },
    {
        "id": "agent-006",
        "name": "fraud-detection-agent",
        "team": "security",
        "status": "healthy",
        "health_score": 99,
        "invocations_24h": 8750,
        "error_rate_pct": 0.1,
        "avg_latency_ms": 180,
        "cost_24h_usd": 9.60,
        "last_deploy": "2026-03-09T12:00:00+00:00",
        "model": "gpt-4.1",
        "framework": "custom",
    },
    {
        "id": "agent-007",
        "name": "hr-onboarding-agent",
        "team": "hr",
        "status": "down",
        "health_score": 12,
        "invocations_24h": 0,
        "error_rate_pct": 100.0,
        "avg_latency_ms": 0,
        "cost_24h_usd": 0.0,
        "last_deploy": "2026-03-08T11:00:00+00:00",
        "model": "claude-haiku-4.5",
        "framework": "langgraph",
    },
    {
        "id": "agent-008",
        "name": "market-research-agent",
        "team": "sales",
        "status": "healthy",
        "health_score": 82,
        "invocations_24h": 720,
        "error_rate_pct": 3.5,
        "avg_latency_ms": 940,
        "cost_24h_usd": 37.90,
        "last_deploy": "2026-03-12T06:00:00+00:00",
        "model": "claude-opus-4.6",
        "framework": "crewai",
    },
]

_SEED_EVENTS = [
    {
        "id": "evt-001",
        "timestamp": "2026-03-13T08:15:00+00:00",
        "type": "deploy",
        "agent_name": "customer-support-agent",
        "message": "Deployed v2.3.1 to production (ECS Fargate, us-east-1)",
        "severity": "info",
    },
    {
        "id": "evt-002",
        "timestamp": "2026-03-13T07:42:00+00:00",
        "type": "alert",
        "agent_name": "data-pipeline-agent",
        "message": "Error rate exceeded 5% threshold (currently 7.8%)",
        "severity": "warning",
    },
    {
        "id": "evt-003",
        "timestamp": "2026-03-13T07:00:00+00:00",
        "type": "cost_spike",
        "agent_name": "code-review-agent",
        "message": "Cost spike detected: $61.20 vs $22.00 expected (24h)",
        "severity": "warning",
    },
    {
        "id": "evt-004",
        "timestamp": "2026-03-13T06:30:00+00:00",
        "type": "restart",
        "agent_name": "hr-onboarding-agent",
        "message": "Agent went down — auto-restart failed (container OOM)",
        "severity": "critical",
    },
    {
        "id": "evt-005",
        "timestamp": "2026-03-13T05:00:00+00:00",
        "type": "deploy",
        "agent_name": "market-research-agent",
        "message": "Deployed v1.0.2 — canary at 25% traffic",
        "severity": "info",
    },
    {
        "id": "evt-006",
        "timestamp": "2026-03-13T04:20:00+00:00",
        "type": "alert",
        "agent_name": "data-pipeline-agent",
        "message": "P95 latency exceeded 2000ms SLA (measured: 2340ms)",
        "severity": "warning",
    },
    {
        "id": "evt-007",
        "timestamp": "2026-03-12T22:10:00+00:00",
        "type": "deploy",
        "agent_name": "code-review-agent",
        "message": "Deployed v3.1.0 to production",
        "severity": "info",
    },
    {
        "id": "evt-008",
        "timestamp": "2026-03-12T18:00:00+00:00",
        "type": "alert",
        "agent_name": "sales-outreach-agent",
        "message": "Budget threshold at 85% for team 'sales'",
        "severity": "warning",
    },
]

_SEED_COST_ANOMALIES = [
    {
        "id": "anom-001",
        "agent_name": "code-review-agent",
        "detected_at": "2026-03-13T07:00:00+00:00",
        "expected_cost": 22.00,
        "actual_cost": 61.20,
        "spike_pct": 178.2,
        "status": "open",
    },
    {
        "id": "anom-002",
        "agent_name": "doc-generation-agent",
        "detected_at": "2026-03-12T14:00:00+00:00",
        "expected_cost": 30.00,
        "actual_cost": 42.70,
        "spike_pct": 42.3,
        "status": "acknowledged",
    },
]

_SEED_COST_SUGGESTIONS = [
    {
        "agent_name": "code-review-agent",
        "current_model": "claude-opus-4.6",
        "suggested_model": "claude-sonnet-4.6",
        "estimated_savings_pct": 80.0,
        "reason": "Code review tasks do not require Opus-level reasoning; "
        "Sonnet achieves comparable quality at 80% lower cost.",
    },
    {
        "agent_name": "hr-onboarding-agent",
        "current_model": "claude-haiku-4.5",
        "suggested_model": "claude-haiku-4.5",
        "estimated_savings_pct": 0.0,
        "reason": "Model is already optimal for the task profile. "
        "Consider reducing max_tokens from 4096 to 2048.",
    },
    {
        "agent_name": "market-research-agent",
        "current_model": "claude-opus-4.6",
        "suggested_model": "claude-sonnet-4.6",
        "estimated_savings_pct": 78.5,
        "reason": "Research summarization tasks show no quality degradation "
        "when switching from Opus to Sonnet in eval runs.",
    },
]

_LAST_CHECKED = "2026-03-13T00:00:00+00:00"

_SEED_COMPLIANCE_CONTROLS = [
    {
        "id": "cc-001",
        "name": "Access Control — MFA enforced",
        "category": "Access Control",
        "status": "pass",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-002",
        "name": "Access Control — RBAC configured",
        "category": "Access Control",
        "status": "pass",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-003",
        "name": "Access Control — Least-privilege deployed",
        "category": "Access Control",
        "status": "partial",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-004",
        "name": "Audit — All deploys logged",
        "category": "Audit",
        "status": "pass",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-005",
        "name": "Audit — Log retention >= 90 days",
        "category": "Audit",
        "status": "pass",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-006",
        "name": "Audit — Immutable audit trail",
        "category": "Audit",
        "status": "pass",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-007",
        "name": "Availability — 99.5% SLA target met",
        "category": "Availability",
        "status": "partial",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-008",
        "name": "Availability — Health checks enabled",
        "category": "Availability",
        "status": "pass",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-009",
        "name": "Data Security — Secrets in Secrets Manager",
        "category": "Data Security",
        "status": "pass",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-010",
        "name": "Data Security — PII guardrails active",
        "category": "Data Security",
        "status": "fail",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-011",
        "name": "Change Management — PRs require review",
        "category": "Change Management",
        "status": "pass",
        "last_checked": _LAST_CHECKED,
    },
    {
        "id": "cc-012",
        "name": "Change Management — CI/CD enforced",
        "category": "Change Management",
        "status": "pass",
        "last_checked": _LAST_CHECKED,
    },
]


# ---------------------------------------------------------------------------
# Incident Service (DB-backed, #207)
# ---------------------------------------------------------------------------


def _serialize_incident(inc: Incident) -> dict[str, Any]:
    """Convert an ``Incident`` ORM row to the dict shape the dashboard expects.

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

    @staticmethod
    async def execute_action(
        db: AsyncSession,
        incident_id: str,
        action: str,
        actor: str = "operator",
    ) -> dict[str, Any]:
        """Execute a remediation action on an incident.

        Actions: restart | rollback | scale | disable.

        Note: the action itself is not yet wired to real deploy machinery
        (rollback / restart / scale-down). This method records the operator's
        intent in the incident timeline; the actual execution lands in a
        follow-up PR. See #207 for the deferred work.
        """
        try:
            inc_uuid = uuid.UUID(incident_id)
        except (ValueError, TypeError):
            return {
                "success": False,
                "error": f"Incident '{incident_id}' not found",
            }
        inc = await db.get(Incident, inc_uuid)
        if inc is None:
            return {
                "success": False,
                "error": f"Incident '{incident_id}' not found",
            }

        now_iso = datetime.now(UTC).isoformat()
        agent_name = (inc.incident_metadata or {}).get("agent_name", "")
        action_messages: dict[str, str] = {
            "restart": f"Restarting agent '{agent_name}' — rolling restart initiated",
            "rollback": f"Rolling back agent '{agent_name}' to previous version",
            "scale": f"Scaling up agent '{agent_name}' — adding 2 additional instances",
            "disable": f"Disabling agent '{agent_name}' — all traffic drained",
        }
        msg = action_messages.get(action, f"Executing action '{action}'")
        timeline = list(inc.timeline or [])
        timeline.append(
            {
                "timestamp": now_iso,
                "actor": actor,
                "message": msg,
            }
        )
        inc.timeline = timeline
        await db.flush()

        logger.info(
            "Action executed",
            extra={"incident_id": incident_id, "action": action},
        )
        return {
            "success": True,
            "action": action,
            "incident_id": incident_id,
            "message": msg,
            "timestamp": now_iso,
        }

    @staticmethod
    async def open_count_by_agent_name(db: AsyncSession) -> dict[str, int]:
        """Return ``{agent_name: open_or_investigating_incident_count}`` for
        team comparison metrics. Reads ``incident_metadata.agent_name`` since
        that is how the seeded fleet identifies its agents.
        """
        stmt = select(Incident).where(
            Incident.status.in_([IncidentStatus.open, IncidentStatus.investigating])
        )
        result = await db.execute(stmt)
        counts: dict[str, int] = {}
        for inc in result.scalars().all():
            name = (inc.incident_metadata or {}).get("agent_name", "")
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# AgentOps Store (read-only fleet / events / cost / compliance)
# ---------------------------------------------------------------------------


class AgentOpsStore:
    """In-memory store for the read-only AgentOps surfaces (fleet snapshot,
    recent events, cost forecasts, SOC2 demo, team comparison).

    Incident persistence has moved to ``IncidentService`` + the ``incidents``
    table (#207). This class no longer holds any user-mutable state.
    """

    def __init__(self) -> None:
        self._agents: list[dict[str, Any]] = list(_SEED_AGENTS)
        self._events: list[dict[str, Any]] = list(_SEED_EVENTS)
        self._canaries: dict[str, dict[str, Any]] = {}
        self._cost_anomalies: list[dict[str, Any]] = list(_SEED_COST_ANOMALIES)
        self._cost_suggestions: list[dict[str, Any]] = list(_SEED_COST_SUGGESTIONS)
        self._compliance_controls: list[dict[str, Any]] = list(_SEED_COMPLIANCE_CONTROLS)

    # -----------------------------------------------------------------------
    # Fleet Overview
    # -----------------------------------------------------------------------

    def get_fleet_overview(self) -> dict[str, Any]:
        """Return all agents with health, cost, and last deploy info."""
        total = len(self._agents)
        healthy = sum(1 for a in self._agents if a["status"] == "healthy")
        degraded = sum(1 for a in self._agents if a["status"] == "degraded")
        down = sum(1 for a in self._agents if a["status"] == "down")
        avg_health = (
            round(sum(a["health_score"] for a in self._agents) / total, 1) if total else 0.0
        )
        return {
            "agents": list(self._agents),
            "summary": {
                "total": total,
                "healthy": healthy,
                "degraded": degraded,
                "down": down,
                "avg_health_score": avg_health,
            },
        }

    def get_fleet_heatmap(self) -> dict[str, Any]:
        """Return health heatmap grid data for visualization."""
        grid = [
            {
                "agent_id": a["id"],
                "name": a["name"],
                "team": a["team"],
                "health_score": a["health_score"],
                "status": a["status"],
            }
            for a in self._agents
        ]
        return {"grid": grid, "total": len(grid)}

    def get_top_agents(self, metric: str = "cost", limit: int = 5) -> list[dict[str, Any]]:
        """Top N agents by cost | errors | latency | invocations."""
        sort_key: dict[str, str] = {
            "cost": "cost_24h_usd",
            "errors": "error_rate_pct",
            "latency": "avg_latency_ms",
            "invocations": "invocations_24h",
        }
        key = sort_key.get(metric, "cost_24h_usd")
        agents = sorted(self._agents, key=lambda a: a[key], reverse=True)
        return agents[:limit]

    # -----------------------------------------------------------------------
    # Events
    # -----------------------------------------------------------------------

    def get_events(self, limit: int = 50, since: str | None = None) -> list[dict[str, Any]]:
        """Return recent operations events, newest-first."""
        events = self._events
        if since:
            events = [e for e in events if e["timestamp"] > since]
        # Sort newest first
        events = sorted(events, key=lambda e: e["timestamp"], reverse=True)
        return events[:limit]

    # -----------------------------------------------------------------------
    # Canary Deploys
    # -----------------------------------------------------------------------

    def start_canary(
        self,
        *,
        agent_name: str,
        version: str,
        traffic_percent: int,
    ) -> dict[str, Any]:
        """Start a new canary deployment."""
        canary_id = f"canary-{str(uuid.uuid4())[:8]}"
        now = datetime.now(UTC).isoformat()
        canary: dict[str, Any] = {
            "id": canary_id,
            "agent_name": agent_name,
            "version": version,
            "traffic_percent": traffic_percent,
            "status": "running",
            "started_at": now,
            "updated_at": now,
        }
        self._canaries[canary_id] = canary
        logger.info(
            "Canary started",
            extra={
                "canary_id": canary_id,
                "agent": agent_name,
                "version": version,
                "traffic": traffic_percent,
            },
        )
        return canary

    def update_canary(
        self,
        canary_id: str,
        *,
        traffic_percent: int | None = None,
        abort: bool = False,
    ) -> dict[str, Any] | None:
        """Update canary traffic split or abort the canary."""
        canary = self._canaries.get(canary_id)
        if not canary:
            return None

        now = datetime.now(UTC).isoformat()
        canary["updated_at"] = now

        if abort:
            canary["status"] = "aborted"
        elif traffic_percent is not None:
            canary["traffic_percent"] = traffic_percent
            if traffic_percent >= 100:
                canary["status"] = "completed"

        return canary

    def get_canary(self, canary_id: str) -> dict[str, Any] | None:
        """Get a canary deployment by ID."""
        return self._canaries.get(canary_id)

    # -----------------------------------------------------------------------
    # Cost Forecasting
    # -----------------------------------------------------------------------

    def get_cost_forecast(self, days: int = 30) -> dict[str, Any]:
        """30-day spend projection using simulated trend data."""
        base_daily_cost = sum(a["cost_24h_usd"] for a in self._agents)
        today = datetime.now(UTC).date()

        forecast_points = []
        for i in range(days):
            date = today + timedelta(days=i)
            # Slight upward trend + noise simulation
            trend_factor = 1.0 + (i * 0.005)
            noise = (((i * 7) % 11) - 5) * 0.01  # deterministic "noise"
            projected = round(base_daily_cost * trend_factor * (1 + noise), 2)
            forecast_points.append({"date": date.isoformat(), "projected_cost": projected})

        current_month_spend = base_daily_cost * 13  # 13 days into month
        projected_month_spend = base_daily_cost * (1.03 * 30)

        return {
            "current_month_spend": round(current_month_spend, 2),
            "projected_month_spend": round(projected_month_spend, 2),
            "forecast_points": forecast_points,
            "confidence": "medium",
            "trend": "increasing",
        }

    def get_cost_anomalies(self) -> list[dict[str, Any]]:
        """Return cost spike alerts."""
        return list(self._cost_anomalies)

    def get_cost_suggestions(self) -> list[dict[str, Any]]:
        """Return model swap recommendations."""
        return list(self._cost_suggestions)

    # -----------------------------------------------------------------------
    # Compliance
    # -----------------------------------------------------------------------

    def get_compliance_status(self) -> dict[str, Any]:
        """Return SOC2 compliance checks overview."""
        controls = list(self._compliance_controls)
        passed = sum(1 for c in controls if c["status"] == "pass")
        failed = sum(1 for c in controls if c["status"] == "fail")
        partial = sum(1 for c in controls if c["status"] == "partial")
        total = len(controls)

        if failed > 0:
            overall = "non_compliant"
        elif partial > 0:
            overall = "partial"
        else:
            overall = "compliant"

        return {
            "overall_status": overall,
            "controls_total": total,
            "controls_passed": passed,
            "controls_failed": failed,
            "controls_partial": partial,
            "last_checked": datetime.now(UTC).isoformat(),
            "controls": controls,
        }

    def generate_compliance_report(self, report_format: str = "json") -> dict[str, Any]:
        """Generate a SOC2 evidence export."""
        controls = list(self._compliance_controls)
        passed = sum(1 for c in controls if c["status"] == "pass")
        failed = sum(1 for c in controls if c["status"] == "fail")

        evidence = [
            {
                "control_id": c["id"],
                "control_name": c["name"],
                "category": c["category"],
                "status": c["status"],
                "last_checked": c["last_checked"],
                "evidence_type": "automated_check",
                "details": f"Automated compliance check for {c['name']}",
            }
            for c in controls
        ]

        return {
            "report_id": f"rpt-{str(uuid.uuid4())[:8]}",
            "generated_at": datetime.now(UTC).isoformat(),
            "format": report_format,
            "controls_passed": passed,
            "controls_failed": failed,
            "controls_total": len(controls),
            "evidence": evidence,
        }

    # -----------------------------------------------------------------------
    # Team Comparison
    # -----------------------------------------------------------------------

    def get_team_comparison(
        self, open_incidents_by_agent: dict[str, int] | None = None
    ) -> list[dict[str, Any]]:
        """Return team-level metrics comparison.

        ``open_incidents_by_agent`` is an optional mapping from
        ``agent_name`` to the count of open + investigating incidents,
        usually produced by ``IncidentService.open_count_by_agent_name``.
        Defaults to an empty mapping (incidents_open will be 0 for all teams)
        so this method remains callable without a DB session — useful for
        unit tests that exercise the in-memory fleet seed only.
        """
        per_agent = open_incidents_by_agent or {}
        teams: dict[str, dict[str, Any]] = {}

        for agent in self._agents:
            team = agent["team"]
            if team not in teams:
                teams[team] = {
                    "team": team,
                    "agent_count": 0,
                    "total_cost_24h": 0.0,
                    "health_scores": [],
                    "incidents_open": 0,
                }
            teams[team]["agent_count"] += 1
            teams[team]["total_cost_24h"] += agent["cost_24h_usd"]
            teams[team]["health_scores"].append(agent["health_score"])
            teams[team]["incidents_open"] += per_agent.get(agent["name"], 0)

        result = []
        for team_data in teams.values():
            scores = team_data.pop("health_scores")
            avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
            result.append(
                {
                    **team_data,
                    "total_cost_24h": round(team_data["total_cost_24h"], 2),
                    "avg_health_score": avg_score,
                }
            )

        result.sort(key=lambda t: t["team"])
        return result


# ---------------------------------------------------------------------------
# Global Singleton
# ---------------------------------------------------------------------------

_store: AgentOpsStore | None = None


def get_agentops_store() -> AgentOpsStore:
    """Get the global AgentOps store singleton."""
    global _store
    if _store is None:
        _store = AgentOpsStore()
    return _store
