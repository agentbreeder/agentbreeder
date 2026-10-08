"""Unit tests for the AgentOps service.

``IncidentService`` (#207), ``FleetService`` (#206) and ``ComplianceService``
(#208) are DB-backed; tests use an in-memory SQLite engine to exercise the
queries without a live PostgreSQL.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Agent, Base
from api.models.enums import AgentStatus
from api.services.agentops_service import (
    FleetService,
    IncidentService,
)


@pytest.fixture
async def db_session():
    """Provide a fresh in-memory SQLite session per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionFactory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Fleet Overview / Top Agents / Events / Team Comparison (DB-backed, #206)
# ---------------------------------------------------------------------------


async def _seed_agent(
    db: AsyncSession,
    *,
    name: str,
    team: str = "engineering",
    status: AgentStatus = AgentStatus.running,
    framework: str = "langgraph",
    model: str = "claude-sonnet-4.6",
) -> Agent:
    """Insert a registry row used by every fleet test."""
    agent = Agent(
        id=uuid.uuid4(),
        name=name,
        version="1.0.0",
        description="",
        team=team,
        owner="alice@example.com",
        framework=framework,
        model_primary=model,
        status=status,
    )
    db.add(agent)
    await db.flush()
    return agent


class TestFleetOverview:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_summary(self, db_session: AsyncSession) -> None:
        result = await FleetService.get_fleet_overview(db_session)
        assert result["agents"] == []
        assert result["summary"] == {
            "total": 0,
            "healthy": 0,
            "degraded": 0,
            "down": 0,
            "avg_health_score": 0.0,
        }

    @pytest.mark.asyncio
    async def test_running_agent_is_healthy(self, db_session: AsyncSession) -> None:
        await _seed_agent(db_session, name="ghost-bot")
        result = await FleetService.get_fleet_overview(db_session)
        assert len(result["agents"]) == 1
        a = result["agents"][0]
        assert a["name"] == "ghost-bot"
        # running agent with no telemetry → healthy
        assert a["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_failed_agent_is_down(self, db_session: AsyncSession) -> None:
        await _seed_agent(db_session, name="dead-bot", status=AgentStatus.failed)
        result = await FleetService.get_fleet_overview(db_session)
        assert result["agents"][0]["status"] == "down"
        assert result["agents"][0]["health_score"] == 0
        assert result["summary"]["down"] == 1


class TestFleetHeatmap:
    @pytest.mark.asyncio
    async def test_empty_returns_empty_grid(self, db_session: AsyncSession) -> None:
        result = await FleetService.get_fleet_heatmap(db_session)
        assert result == {"grid": [], "total": 0}

    @pytest.mark.asyncio
    async def test_one_cell_per_agent(self, db_session: AsyncSession) -> None:
        await _seed_agent(db_session, name="bot-a", team="alpha")
        await _seed_agent(db_session, name="bot-b", team="beta")
        result = await FleetService.get_fleet_heatmap(db_session)
        assert result["total"] == 2
        cells = {c["name"]: c for c in result["grid"]}
        assert set(cells) == {"bot-a", "bot-b"}
        for cell in result["grid"]:
            assert {"agent_id", "name", "team", "health_score", "status"} <= set(cell)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


class TestIncidents:
    """DB-backed IncidentService — replaces the old in-memory ``_incidents`` dict.

    Each test gets a fresh sqlite-backed session via the ``db_session`` fixture.
    Persistence-across-restart behaviour is verified explicitly in
    ``TestIncidentPersistence`` below.
    """

    @pytest.mark.asyncio
    async def test_create_incident(self, db_session: AsyncSession) -> None:
        incident = await IncidentService.create_incident(
            db_session,
            agent_name="test-agent",
            title="Test incident",
            severity="high",
            description="Something went wrong",
        )
        assert incident["agent_name"] == "test-agent"
        assert incident["title"] == "Test incident"
        assert incident["severity"] == "high"
        assert incident["status"] == "open"
        assert incident["id"]
        assert len(incident["timeline"]) == 1

    @pytest.mark.asyncio
    async def test_create_incident_invalid_severity_defaults_to_medium(
        self, db_session: AsyncSession
    ) -> None:
        incident = await IncidentService.create_incident(
            db_session,
            agent_name="x",
            title="x",
            severity="not-a-real-severity",
            description="",
        )
        assert incident["severity"] == "medium"

    @pytest.mark.asyncio
    async def test_get_incident_found(self, db_session: AsyncSession) -> None:
        inc = await IncidentService.create_incident(
            db_session,
            agent_name="a1",
            title="t1",
            severity="low",
            description="d1",
        )
        found = await IncidentService.get_incident(db_session, inc["id"])
        assert found is not None
        assert found["id"] == inc["id"]

    @pytest.mark.asyncio
    async def test_get_incident_not_found(self, db_session: AsyncSession) -> None:
        result = await IncidentService.get_incident(
            db_session, "00000000-0000-0000-0000-000000000000"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_get_incident_invalid_uuid(self, db_session: AsyncSession) -> None:
        # The old API used "inc-001" string IDs — those should now cleanly
        # 404 instead of crashing the route.
        assert await IncidentService.get_incident(db_session, "inc-001") is None
        assert await IncidentService.get_incident(db_session, "") is None

    @pytest.mark.asyncio
    async def test_update_incident_status(self, db_session: AsyncSession) -> None:
        inc = await IncidentService.create_incident(
            db_session,
            agent_name="a1",
            title="t1",
            severity="medium",
            description="d1",
        )
        updated = await IncidentService.update_incident(
            db_session, inc["id"], status="investigating"
        )
        assert updated is not None
        assert updated["status"] == "investigating"
        assert len(updated["timeline"]) == 2

    @pytest.mark.asyncio
    async def test_update_incident_resolved_sets_resolved_at(
        self, db_session: AsyncSession
    ) -> None:
        inc = await IncidentService.create_incident(
            db_session, agent_name="a1", title="t", severity="low", description=""
        )
        updated = await IncidentService.update_incident(db_session, inc["id"], status="resolved")
        assert updated is not None
        assert updated["status"] == "resolved"
        assert updated["resolved_at"] is not None

    @pytest.mark.asyncio
    async def test_update_incident_not_found(self, db_session: AsyncSession) -> None:
        result = await IncidentService.update_incident(
            db_session,
            "00000000-0000-0000-0000-000000000000",
            status="resolved",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_update_incident_message_only(self, db_session: AsyncSession) -> None:
        inc = await IncidentService.create_incident(
            db_session,
            agent_name="a1",
            title="t1",
            severity="low",
            description="d1",
        )
        updated = await IncidentService.update_incident(
            db_session, inc["id"], message="Added a note"
        )
        assert updated is not None
        assert updated["status"] == "open"  # unchanged
        assert updated["timeline"][-1]["message"] == "Added a note"

    @pytest.mark.asyncio
    async def test_update_incident_invalid_status(self, db_session: AsyncSession) -> None:
        inc = await IncidentService.create_incident(
            db_session, agent_name="a1", title="t", severity="low", description=""
        )
        result = await IncidentService.update_incident(
            db_session, inc["id"], status="not-a-real-status"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_list_incidents_filter_by_status(self, db_session: AsyncSession) -> None:
        await IncidentService.create_incident(
            db_session, agent_name="a1", title="t1", severity="high", description="d1"
        )
        await IncidentService.create_incident(
            db_session, agent_name="a2", title="t2", severity="low", description="d2"
        )
        open_incidents = await IncidentService.list_incidents(db_session, status="open")
        assert len(open_incidents) == 2
        for inc in open_incidents:
            assert inc["status"] == "open"

    @pytest.mark.asyncio
    async def test_list_incidents_filter_by_severity(self, db_session: AsyncSession) -> None:
        await IncidentService.create_incident(
            db_session, agent_name="a1", title="t1", severity="critical", description="d1"
        )
        await IncidentService.create_incident(
            db_session, agent_name="a2", title="t2", severity="low", description="d2"
        )
        critical = await IncidentService.list_incidents(db_session, severity="critical")
        assert len(critical) == 1
        assert critical[0]["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_list_incidents_sorted_newest_first(self, db_session: AsyncSession) -> None:
        first = await IncidentService.create_incident(
            db_session, agent_name="a1", title="t1", severity="low", description=""
        )
        second = await IncidentService.create_incident(
            db_session, agent_name="a2", title="t2", severity="low", description=""
        )
        results = await IncidentService.list_incidents(db_session)
        assert [r["id"] for r in results] == [second["id"], first["id"]]

    @pytest.mark.asyncio
    async def test_list_incidents_empty(self, db_session: AsyncSession) -> None:
        # No SEED_INCIDENTS — fresh DB starts empty.
        assert await IncidentService.list_incidents(db_session) == []


class TestIncidentPersistence:
    """Verify the bug from #207: incidents must survive across sessions."""

    @pytest.mark.asyncio
    async def test_incident_survives_session_recycle(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        # Session A — write
        async with SessionFactory() as session_a:
            inc = await IncidentService.create_incident(
                session_a,
                agent_name="restart-test",
                title="Survives restart",
                severity="high",
                description="If this round-trips, persistence is wired.",
            )
            await session_a.commit()
            inc_id = inc["id"]

        # Session B — read (simulates a fresh request after restart)
        async with SessionFactory() as session_b:
            fetched = await IncidentService.get_incident(session_b, inc_id)
            assert fetched is not None
            assert fetched["title"] == "Survives restart"
            assert fetched["agent_name"] == "restart-test"

        await engine.dispose()


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------


class TestCompliance:
    """DB-backed compliance scanner — replaces the old in-memory seed list.

    Each test runs a real scan against the in-memory SQLite session provided
    by the ``db_session`` fixture. The control registry is exercised through
    ``ComplianceService.run_and_persist`` so the wire shape rendered by the
    API is the same shape verified here.
    """

    @pytest.mark.asyncio
    async def test_run_and_persist_writes_one_row(self, db_session: AsyncSession) -> None:
        from api.services.agentops_service import ComplianceService

        row = await ComplianceService.run_and_persist(db_session)
        assert row.id is not None
        assert row.overall_status in ("compliant", "partial", "non_compliant")
        assert isinstance(row.results, list)
        assert len(row.results) == 6  # six controls ship in #208

    @pytest.mark.asyncio
    async def test_status_payload_shape(self, db_session: AsyncSession) -> None:
        from api.services.agentops_service import ComplianceService

        row = await ComplianceService.run_and_persist(db_session)
        payload = ComplianceService.status_payload(row)
        assert "overall_status" in payload
        assert "controls" in payload
        assert "scan_id" in payload
        assert payload["controls_total"] == len(payload["controls"])
        for ctrl in payload["controls"]:
            assert {"id", "name", "category", "status", "last_checked"}.issubset(ctrl.keys())
            assert ctrl["status"] in ("pass", "fail", "partial", "skipped")

    @pytest.mark.asyncio
    async def test_report_payload_cites_real_evidence(self, db_session: AsyncSession) -> None:
        from api.services.agentops_service import ComplianceService

        row = await ComplianceService.run_and_persist(db_session)
        report = ComplianceService.report_payload(row, report_format="json")
        assert "report_id" in report
        assert "scan_id" in report
        assert report["format"] == "json"
        assert len(report["evidence"]) == 6
        # Every control must cite its own evidence dict (not the old
        # placeholder string "Automated compliance check for X").
        for ev in report["evidence"]:
            assert "evidence" in ev
            assert isinstance(ev["evidence"], dict)
            assert ev["details"]
            assert "Automated compliance check for" not in ev["details"]

    @pytest.mark.asyncio
    async def test_get_or_run_latest_caches_recent_scan(self, db_session: AsyncSession) -> None:
        from api.services.agentops_service import ComplianceService

        first = await ComplianceService.get_or_run_latest(db_session, max_age_seconds=3600)
        second = await ComplianceService.get_or_run_latest(db_session, max_age_seconds=3600)
        assert first.id == second.id  # cached, no new scan

    @pytest.mark.asyncio
    async def test_get_or_run_latest_runs_when_stale(self, db_session: AsyncSession) -> None:
        from api.services.agentops_service import ComplianceService

        first = await ComplianceService.get_or_run_latest(db_session, max_age_seconds=3600)
        # max_age=0 forces a fresh scan even though the previous row is < 1s old
        second = await ComplianceService.get_or_run_latest(db_session, max_age_seconds=0)
        assert first.id != second.id

    @pytest.mark.asyncio
    async def test_overall_status_rolls_up_correctly(self, db_session: AsyncSession) -> None:
        from api.services.agentops_service import ComplianceService

        row = await ComplianceService.run_and_persist(db_session)
        results = list(row.results)
        has_fail = any(r["status"] == "fail" for r in results)
        has_partial = any(r["status"] == "partial" for r in results)
        if has_fail:
            assert row.overall_status == "non_compliant"
        elif has_partial:
            assert row.overall_status == "partial"
        else:
            assert row.overall_status == "compliant"

    @pytest.mark.asyncio
    async def test_summary_counts_match_results(self, db_session: AsyncSession) -> None:
        from api.services.agentops_service import ComplianceService

        row = await ComplianceService.run_and_persist(db_session)
        summary = dict(row.summary)
        results = list(row.results)
        assert summary["controls_total"] == len(results)
        assert summary["controls_passed"] == sum(1 for r in results if r["status"] == "pass")
        assert summary["controls_failed"] == sum(1 for r in results if r["status"] == "fail")
        assert summary["controls_partial"] == sum(1 for r in results if r["status"] == "partial")
        assert summary["controls_skipped"] == sum(1 for r in results if r["status"] == "skipped")

    @pytest.mark.asyncio
    async def test_scan_persists_across_session_recycle(self) -> None:
        """A scan written in session A must be readable from session B."""
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from api.models.database import Base, ComplianceScan
        from api.services.agentops_service import ComplianceService

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with SessionFactory() as session_a:
            written = await ComplianceService.run_and_persist(session_a)
            written_id = written.id

        async with SessionFactory() as session_b:
            from sqlalchemy import select

            res = await session_b.execute(
                select(ComplianceScan).where(ComplianceScan.id == written_id)
            )
            row = res.scalar_one()
            assert row.overall_status == written.overall_status
            assert len(row.results) == 6

        await engine.dispose()
