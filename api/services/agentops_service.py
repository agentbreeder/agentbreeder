"""AgentOps Service — unified operations dashboard for fleet health, incidents,
canary deploys, cost forecasting, and compliance.

Provides:
- Fleet overview and health heatmap (DB-backed — ``FleetService``, #206)
- Top-N agent rankings by cost, errors, latency, invocations (DB-backed)
- Real-time operations event stream (DB-backed: ``audit_events`` + ``cost_events``)
- Incident management (CRUD + actions) — DB-backed via ``IncidentService`` (#207)
- Canary deploy management (in-memory; tracked separately)
- Cost forecasting and anomaly detection (anchored on ``cost_events``)
- SOC2 compliance status and reporting (still seeded; tracked under #208)
- Team comparison metrics (DB-backed — ``cost_events`` + ``IncidentService``)

Fleet / events / top-agents / teams previously read from in-memory
``_SEED_AGENTS`` and ``_SEED_EVENTS`` constants. Both were removed in #206 —
see ``FleetService`` for the DB-backed replacement that joins ``agents``,
``traces``, ``cost_events``, and ``audit_events``.

Incidents previously lived in an in-process dict (``_incidents``) seeded from
``_SEED_INCIDENTS``. Both were removed as of #207 — see ``IncidentService``
for the DB-backed replacement and migration ``020_incidents_table.py``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.audit import AuditEvent
from api.models.costs import CostEvent
from api.models.database import Agent, ComplianceScan, Incident
from api.models.enums import AgentStatus, IncidentSeverity, IncidentStatus
from api.models.tracing import Trace

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seed Data — cost / compliance (still in-memory; tracked for follow-up)
# ---------------------------------------------------------------------------
# Fleet/events/top-agents/teams seeds (``_SEED_AGENTS``, ``_SEED_EVENTS``)
# were removed in #206 — see ``FleetService`` below for the DB-backed
# replacement. Cost anomalies / suggestions and SOC2 compliance are still
# seeded; they are tracked under the cost-uplift work and #208 respectively.

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

# ``_SEED_COMPLIANCE_CONTROLS`` was removed in #208. Compliance controls now
# come from ``engine.compliance.controls.CONTROL_REGISTRY`` and are evaluated
# against the live DB by ``ComplianceService`` below; results are persisted
# to the ``compliance_scans`` table (migration 021).


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
# Fleet Service (DB-backed, #206)
# ---------------------------------------------------------------------------


def _agent_status_to_health(
    status: AgentStatus | str,
    error_rate_pct: float,
) -> tuple[str, int]:
    """Map a registry ``AgentStatus`` + recent error rate to the
    ``healthy / degraded / down`` triage the dashboard expects, plus a
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


def _trace_metrics_24h_subquery() -> Any:
    """Per-agent trace stats over the last 24h (invocations, errors,
    avg latency). Joined against ``agents`` to build the fleet snapshot.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    error_case = case((Trace.status == "error", 1), else_=0)
    return (
        select(
            Trace.agent_name.label("agent_name"),
            func.count(Trace.id).label("invocations_24h"),
            func.sum(error_case).label("errors_24h"),
            func.avg(Trace.duration_ms).label("avg_latency_ms"),
        )
        .where(Trace.created_at >= cutoff)
        .group_by(Trace.agent_name)
        .subquery()
    )


def _cost_metrics_24h_subquery() -> Any:
    """Per-agent cost spend over the last 24 hours."""
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    return (
        select(
            CostEvent.agent_name.label("agent_name"),
            func.sum(CostEvent.cost_usd).label("cost_24h_usd"),
        )
        .where(CostEvent.created_at >= cutoff)
        .group_by(CostEvent.agent_name)
        .subquery()
    )


async def _build_fleet_rows(db: AsyncSession) -> list[dict[str, Any]]:
    """Compose the per-agent fleet snapshot from ``agents`` joined to
    24h trace + cost aggregates.

    Agents with no trace activity in the window get zeroed metrics —
    this is the empty-state behaviour required by #206 (``traces`` may
    be sparse on a fresh deploy and we never seed fakes).
    """
    trace_sq = _trace_metrics_24h_subquery()
    cost_sq = _cost_metrics_24h_subquery()

    stmt = (
        select(
            Agent.id,
            Agent.name,
            Agent.team,
            Agent.status,
            Agent.framework,
            Agent.model_primary,
            Agent.updated_at,
            trace_sq.c.invocations_24h,
            trace_sq.c.errors_24h,
            trace_sq.c.avg_latency_ms,
            cost_sq.c.cost_24h_usd,
        )
        .outerjoin(trace_sq, trace_sq.c.agent_name == Agent.name)
        .outerjoin(cost_sq, cost_sq.c.agent_name == Agent.name)
        .order_by(Agent.name)
    )

    result = await db.execute(stmt)
    rows: list[dict[str, Any]] = []
    for r in result.all():
        invocations = int(r.invocations_24h or 0)
        errors = int(r.errors_24h or 0)
        latency = float(r.avg_latency_ms or 0.0)
        cost = float(r.cost_24h_usd or 0.0)
        error_rate = round((errors / invocations) * 100.0, 2) if invocations else 0.0
        status, health = _agent_status_to_health(r.status, error_rate)
        rows.append(
            {
                "id": str(r.id),
                "name": r.name,
                "team": r.team,
                "status": status,
                "health_score": health,
                "invocations_24h": invocations,
                "error_rate_pct": error_rate,
                "avg_latency_ms": int(round(latency)),
                "cost_24h_usd": round(cost, 2),
                "last_deploy": r.updated_at.isoformat() if r.updated_at else "",
                "model": r.model_primary or "",
                "framework": r.framework or "",
            }
        )
    return rows


def _classify_audit_event(event: AuditEvent) -> tuple[str, str, str]:
    """Map an ``AuditEvent`` row to ``(type, severity, message)`` for the
    operations event stream.

    The dotted ``action`` namespace from #209 (e.g. ``deploy.created``,
    ``incident.opened``, ``secret.rotated``) drives the bucketing.
    """
    action = (event.action or "").lower()
    rtype = (event.resource_type or "").lower()
    name = event.resource_name or ""
    actor = event.actor or "system"
    details = event.details or {}

    if action.startswith("deploy") or rtype == "deploy":
        msg = details.get("message") or f"{actor} {action} {name}".strip()
        return "deploy", "info", msg
    if action.startswith("incident") or rtype == "incident":
        sev_raw = str(details.get("severity", "")).lower()
        sev = "critical" if sev_raw in {"critical", "high"} else "warning"
        msg = details.get("title") or details.get("message") or f"{action} {name}".strip()
        return "alert", sev, msg
    if "rollback" in action or "restart" in action:
        return "restart", "warning", f"{actor} {action} {name}".strip()
    if "guardrail" in action or "alert" in action:
        return "alert", "warning", details.get("message") or f"{action} {name}".strip()
    return "audit", "info", f"{actor} {action} {name}".strip()


class FleetService:
    """DB-backed read service for fleet / events / top-agents / teams.

    Replaces the old in-memory ``_SEED_AGENTS`` / ``_SEED_EVENTS`` constants
    (#206). All methods take an ``AsyncSession`` and aggregate over the
    registry (``agents``), the trace store (``traces``), the cost ledger
    (``cost_events``), and the audit log (``audit_events``).
    """

    @staticmethod
    async def get_fleet_overview(db: AsyncSession) -> dict[str, Any]:
        """All agents with 24h health, cost, latency, and last-deploy info."""
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

    @staticmethod
    async def get_top_agents(
        db: AsyncSession,
        *,
        metric: str = "cost",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Top-N agents by cost | errors | latency | invocations.

        Ranking sorts the same row set used by ``get_fleet_overview``, so
        cost / invocations / latency / error rate all line up with what
        the user sees on the fleet table. Agents with no traces in the
        window land at the bottom by construction.
        """
        sort_key: dict[str, str] = {
            "cost": "cost_24h_usd",
            "errors": "error_rate_pct",
            "latency": "avg_latency_ms",
            "invocations": "invocations_24h",
        }
        key = sort_key.get(metric, "cost_24h_usd")
        rows = await _build_fleet_rows(db)
        rows.sort(key=lambda r: r[key], reverse=True)
        return rows[: max(0, int(limit))]

    @staticmethod
    async def get_events(
        db: AsyncSession,
        *,
        limit: int = 50,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recent operations events, newest-first.

        Sourced from two tables:
          - ``audit_events`` — deploys, incidents, restarts, rollbacks
          - ``cost_events`` — single requests >= $1.00 surfaced as
            ``cost_spike`` events (a soft anomaly threshold).

        Returns ``[]`` cleanly when both tables are empty.
        """
        since_dt: datetime | None = None
        if since:
            try:
                parsed = datetime.fromisoformat(since)
                since_dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except ValueError:
                since_dt = None

        audit_stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
        if since_dt is not None:
            audit_stmt = audit_stmt.where(AuditEvent.created_at > since_dt)
        audit_rows = (await db.execute(audit_stmt)).scalars().all()

        cost_stmt = (
            select(CostEvent)
            .where(CostEvent.cost_usd >= 1.0)
            .order_by(CostEvent.created_at.desc())
            .limit(limit)
        )
        if since_dt is not None:
            cost_stmt = cost_stmt.where(CostEvent.created_at > since_dt)
        cost_rows = (await db.execute(cost_stmt)).scalars().all()

        events: list[dict[str, Any]] = []
        for ae in audit_rows:
            etype, sev, msg = _classify_audit_event(ae)
            events.append(
                {
                    "id": f"audit-{ae.id}",
                    "timestamp": ae.created_at.isoformat() if ae.created_at else "",
                    "type": etype,
                    "agent_name": ae.resource_name if ae.resource_type == "agent" else "",
                    "message": msg or f"{ae.actor} {ae.action} {ae.resource_name}".strip(),
                    "severity": sev,
                }
            )
        for ce in cost_rows:
            events.append(
                {
                    "id": f"cost-{ce.id}",
                    "timestamp": ce.created_at.isoformat() if ce.created_at else "",
                    "type": "cost_spike",
                    "agent_name": ce.agent_name,
                    "message": (
                        f"Cost spike: ${ce.cost_usd:.2f} on {ce.model_name} "
                        f"({ce.total_tokens} tokens)"
                    ),
                    "severity": "warning" if ce.cost_usd < 10.0 else "critical",
                }
            )

        events.sort(key=lambda e: e["timestamp"], reverse=True)
        return events[:limit]

    @staticmethod
    async def get_team_comparison(
        db: AsyncSession,
        *,
        open_incidents_by_agent: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        """Team-level metrics — agent count, 24h spend, avg health, open
        incidents.

        Spend is aggregated directly over ``cost_events.team`` (preserving
        spend recorded against agents that may since have been deleted).
        Health and agent counts derive from live ``agents`` rows. The
        ``open_incidents_by_agent`` mapping is the dict produced by
        :py:meth:`IncidentService.open_count_by_agent_name` — passed in
        rather than re-queried so the route can reuse it (preserves the
        #207 IncidentService integration).
        """
        per_agent = open_incidents_by_agent or {}
        fleet = await _build_fleet_rows(db)

        cutoff = datetime.now(UTC) - timedelta(hours=24)
        cost_stmt = (
            select(
                CostEvent.team.label("team"),
                func.sum(CostEvent.cost_usd).label("cost_usd"),
            )
            .where(CostEvent.created_at >= cutoff)
            .group_by(CostEvent.team)
        )
        cost_rows = (await db.execute(cost_stmt)).all()
        cost_by_team: dict[str, float] = {
            row.team: float(row.cost_usd or 0.0) for row in cost_rows
        }

        teams: dict[str, dict[str, Any]] = {}
        for agent in fleet:
            team = agent["team"]
            bucket = teams.setdefault(
                team,
                {
                    "team": team,
                    "agent_count": 0,
                    "total_cost_24h": 0.0,
                    "_health_scores": [],
                    "incidents_open": 0,
                },
            )
            bucket["agent_count"] += 1
            bucket["_health_scores"].append(agent["health_score"])
            bucket["incidents_open"] += per_agent.get(agent["name"], 0)

        for team, total_cost in cost_by_team.items():
            bucket = teams.setdefault(
                team,
                {
                    "team": team,
                    "agent_count": 0,
                    "total_cost_24h": 0.0,
                    "_health_scores": [],
                    "incidents_open": 0,
                },
            )
            bucket["total_cost_24h"] = total_cost

        out: list[dict[str, Any]] = []
        for bucket in teams.values():
            scores = bucket.pop("_health_scores")
            avg = round(sum(scores) / len(scores), 1) if scores else 0.0
            out.append(
                {
                    **bucket,
                    "total_cost_24h": round(float(bucket["total_cost_24h"]), 2),
                    "avg_health_score": avg,
                }
            )

        out.sort(key=lambda t: t["team"])
        return out


# ---------------------------------------------------------------------------
# AgentOps Store (in-memory — canary / cost-anomaly / compliance only)
# ---------------------------------------------------------------------------


class AgentOpsStore:
    """In-memory store for the remaining read-only AgentOps surfaces that
    aren't yet wired to real backends:

    - Canary deploys (no canary table yet — tracked separately)
    - Cost forecasts / anomalies / suggestions (the cost-uplift work)
    - SOC2 compliance demo (tracked under #208)

    Fleet / events / top-agents / teams moved to ``FleetService`` (#206).
    Incidents moved to ``IncidentService`` (#207). The cost forecast still
    lives here but is now anchored on a real ``cost_events`` aggregate
    passed in from the route.
    """

    def __init__(self) -> None:
        self._canaries: dict[str, dict[str, Any]] = {}
        self._cost_anomalies: list[dict[str, Any]] = list(_SEED_COST_ANOMALIES)
        self._cost_suggestions: list[dict[str, Any]] = list(_SEED_COST_SUGGESTIONS)

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

    def get_cost_forecast(self, days: int = 30, base_daily_cost: float = 0.0) -> dict[str, Any]:
        """30-day spend projection.

        ``base_daily_cost`` is the most-recent 24h spend (passed in by the
        route from a ``cost_events`` query). The forecast applies a small
        upward trend + deterministic noise on top of that anchor — when
        ``base_daily_cost`` is 0 the forecast collapses to all zeros, which
        is the correct empty-state behaviour for a fresh deploy.

        The full spend forecasting pipeline (regression on historical
        cost_events, anomaly detection, etc.) is tracked under the
        cost-uplift work; this method intentionally stays simple — the
        cost dashboard surfaces (``/api/v1/costs/*``) own the real model.
        """
        today = datetime.now(UTC).date()

        forecast_points = []
        for i in range(days):
            date = today + timedelta(days=i)
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
            "confidence": "medium" if base_daily_cost > 0 else "low",
            "trend": "increasing" if base_daily_cost > 0 else "flat",
        }

    def get_cost_anomalies(self) -> list[dict[str, Any]]:
        """Return cost spike alerts."""
        return list(self._cost_anomalies)

    def get_cost_suggestions(self) -> list[dict[str, Any]]:
        """Return model swap recommendations."""
        return list(self._cost_suggestions)


# ---------------------------------------------------------------------------
# Compliance Service (DB-backed, #208)
# ---------------------------------------------------------------------------


class ComplianceService:
    """Real SOC 2 / HIPAA control scanner — replaces ``_SEED_COMPLIANCE_CONTROLS``.

    Each call to :meth:`run_and_persist` executes every control in
    ``engine.compliance.controls.CONTROL_REGISTRY`` against the live
    ``AsyncSession`` and writes a single row to ``compliance_scans``. The
    ``status_payload`` and ``report_payload`` shapes are kept wire-compatible
    with the previous in-memory responses so the dashboard does not need to
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

        Reading the dashboard at high frequency must not re-run controls on
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

        The dashboard's ``Control`` interface expects ``id``, ``name``,
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
