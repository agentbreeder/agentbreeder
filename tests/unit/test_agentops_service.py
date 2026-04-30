"""Unit tests for the AgentOps service.

Incidents are now DB-backed (#207) — tests for ``IncidentService`` use an
in-memory SQLite engine so the same test file exercises both the read-only
``AgentOpsStore`` and the persistence layer.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Base
from api.services.agentops_service import (
    AgentOpsStore,
    IncidentService,
    get_agentops_store,
)


@pytest.fixture
def store() -> AgentOpsStore:
    """Return a fresh AgentOpsStore for each test."""
    return AgentOpsStore()


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
# Fleet Overview
# ---------------------------------------------------------------------------


class TestFleetOverview:
    def test_get_fleet_overview_returns_agents(self, store: AgentOpsStore) -> None:
        result = store.get_fleet_overview()
        assert "agents" in result
        assert "summary" in result
        assert len(result["agents"]) > 0

        agent = result["agents"][0]
        required_keys = {
            "id",
            "name",
            "team",
            "status",
            "health_score",
            "invocations_24h",
            "error_rate_pct",
            "avg_latency_ms",
            "cost_24h_usd",
            "last_deploy",
            "model",
            "framework",
        }
        assert required_keys.issubset(agent.keys())

    def test_fleet_overview_summary_counts(self, store: AgentOpsStore) -> None:
        result = store.get_fleet_overview()
        summary = result["summary"]
        agents = result["agents"]

        assert summary["total"] == len(agents)
        assert summary["healthy"] + summary["degraded"] + summary["down"] == summary["total"]
        assert 0.0 <= summary["avg_health_score"] <= 100.0

    def test_get_fleet_heatmap_returns_grid(self, store: AgentOpsStore) -> None:
        result = store.get_fleet_heatmap()
        assert "grid" in result
        assert "total" in result
        assert result["total"] == len(result["grid"])

        cell = result["grid"][0]
        assert "agent_id" in cell
        assert "name" in cell
        assert "health_score" in cell
        assert "status" in cell


# ---------------------------------------------------------------------------
# Top Agents
# ---------------------------------------------------------------------------


class TestTopAgents:
    def test_get_top_agents_by_cost(self, store: AgentOpsStore) -> None:
        agents = store.get_top_agents(metric="cost", limit=5)
        assert len(agents) <= 5
        costs = [a["cost_24h_usd"] for a in agents]
        assert costs == sorted(costs, reverse=True)

    def test_get_top_agents_by_errors(self, store: AgentOpsStore) -> None:
        agents = store.get_top_agents(metric="errors", limit=5)
        assert len(agents) <= 5
        rates = [a["error_rate_pct"] for a in agents]
        assert rates == sorted(rates, reverse=True)

    def test_get_top_agents_by_latency(self, store: AgentOpsStore) -> None:
        agents = store.get_top_agents(metric="latency", limit=5)
        assert len(agents) <= 5
        latencies = [a["avg_latency_ms"] for a in agents]
        assert latencies == sorted(latencies, reverse=True)

    def test_get_top_agents_by_invocations(self, store: AgentOpsStore) -> None:
        agents = store.get_top_agents(metric="invocations", limit=5)
        assert len(agents) <= 5
        invocations = [a["invocations_24h"] for a in agents]
        assert invocations == sorted(invocations, reverse=True)

    def test_top_agents_respects_limit(self, store: AgentOpsStore) -> None:
        agents = store.get_top_agents(metric="cost", limit=3)
        assert len(agents) <= 3


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class TestEvents:
    def test_get_events_returns_list(self, store: AgentOpsStore) -> None:
        events = store.get_events()
        assert isinstance(events, list)
        assert len(events) > 0

        evt = events[0]
        required_keys = {"id", "timestamp", "type", "agent_name", "message", "severity"}
        assert required_keys.issubset(evt.keys())

    def test_get_events_with_limit(self, store: AgentOpsStore) -> None:
        all_events = store.get_events(limit=1000)
        limited = store.get_events(limit=2)
        assert len(limited) <= 2
        assert len(all_events) >= len(limited)

    def test_get_events_sorted_newest_first(self, store: AgentOpsStore) -> None:
        events = store.get_events(limit=100)
        timestamps = [e["timestamp"] for e in events]
        assert timestamps == sorted(timestamps, reverse=True)


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

    @pytest.mark.asyncio
    async def test_execute_action_restart(self, db_session: AsyncSession) -> None:
        inc = await IncidentService.create_incident(
            db_session,
            agent_name="test-agent",
            title="t1",
            severity="high",
            description="d1",
        )
        result = await IncidentService.execute_action(db_session, inc["id"], "restart")
        assert result["success"] is True
        assert result["action"] == "restart"
        assert "restart" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_execute_action_appends_timeline(self, db_session: AsyncSession) -> None:
        inc = await IncidentService.create_incident(
            db_session, agent_name="a1", title="t", severity="low", description=""
        )
        await IncidentService.execute_action(db_session, inc["id"], "rollback")
        fetched = await IncidentService.get_incident(db_session, inc["id"])
        assert fetched is not None
        assert len(fetched["timeline"]) == 2
        assert "rolling back" in fetched["timeline"][-1]["message"].lower()

    @pytest.mark.asyncio
    async def test_execute_action_invalid_incident(self, db_session: AsyncSession) -> None:
        result = await IncidentService.execute_action(
            db_session, "00000000-0000-0000-0000-000000000000", "restart"
        )
        assert result["success"] is False
        assert "error" in result


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


class TestOpenCountByAgentName:
    @pytest.mark.asyncio
    async def test_counts_open_and_investigating(self, db_session: AsyncSession) -> None:
        a1 = await IncidentService.create_incident(
            db_session, agent_name="bot-1", title="t", severity="high", description=""
        )
        await IncidentService.create_incident(
            db_session, agent_name="bot-1", title="t", severity="low", description=""
        )
        await IncidentService.create_incident(
            db_session, agent_name="bot-2", title="t", severity="low", description=""
        )
        # Move one to investigating and one to resolved.
        await IncidentService.update_incident(db_session, a1["id"], status="investigating")
        # Resolved one should NOT count.
        resolved = await IncidentService.create_incident(
            db_session, agent_name="bot-1", title="t", severity="low", description=""
        )
        await IncidentService.update_incident(db_session, resolved["id"], status="resolved")

        counts = await IncidentService.open_count_by_agent_name(db_session)
        assert counts == {"bot-1": 2, "bot-2": 1}

    @pytest.mark.asyncio
    async def test_empty_when_no_incidents(self, db_session: AsyncSession) -> None:
        assert await IncidentService.open_count_by_agent_name(db_session) == {}


# ---------------------------------------------------------------------------
# Canary Deploys
# ---------------------------------------------------------------------------


class TestCanaryDeploys:
    def test_start_canary(self, store: AgentOpsStore) -> None:
        canary = store.start_canary(
            agent_name="my-agent",
            version="v2.0.0",
            traffic_percent=10,
        )
        assert canary["agent_name"] == "my-agent"
        assert canary["version"] == "v2.0.0"
        assert canary["traffic_percent"] == 10
        assert canary["status"] == "running"
        assert canary["id"].startswith("canary-")

    def test_get_canary_found(self, store: AgentOpsStore) -> None:
        canary = store.start_canary(agent_name="a1", version="v1.0", traffic_percent=25)
        found = store.get_canary(canary["id"])
        assert found is not None
        assert found["id"] == canary["id"]

    def test_get_canary_not_found(self, store: AgentOpsStore) -> None:
        result = store.get_canary("nonexistent")
        assert result is None

    def test_update_canary_traffic(self, store: AgentOpsStore) -> None:
        canary = store.start_canary(agent_name="a1", version="v1.0", traffic_percent=10)
        updated = store.update_canary(canary["id"], traffic_percent=50)
        assert updated is not None
        assert updated["traffic_percent"] == 50
        assert updated["status"] == "running"

    def test_update_canary_traffic_100_completes(self, store: AgentOpsStore) -> None:
        canary = store.start_canary(agent_name="a1", version="v1.0", traffic_percent=10)
        updated = store.update_canary(canary["id"], traffic_percent=100)
        assert updated is not None
        assert updated["status"] == "completed"

    def test_update_canary_abort(self, store: AgentOpsStore) -> None:
        canary = store.start_canary(agent_name="a1", version="v1.0", traffic_percent=25)
        updated = store.update_canary(canary["id"], abort=True)
        assert updated is not None
        assert updated["status"] == "aborted"

    def test_update_canary_not_found(self, store: AgentOpsStore) -> None:
        result = store.update_canary("nonexistent", traffic_percent=50)
        assert result is None


# ---------------------------------------------------------------------------
# Cost Forecasting
# ---------------------------------------------------------------------------


class TestCostForecast:
    def test_get_cost_forecast(self, store: AgentOpsStore) -> None:
        result = store.get_cost_forecast(days=30)
        assert "current_month_spend" in result
        assert "projected_month_spend" in result
        assert "forecast_points" in result
        assert "confidence" in result
        assert "trend" in result
        assert result["projected_month_spend"] > 0

    def test_forecast_has_correct_number_of_points(self, store: AgentOpsStore) -> None:
        result = store.get_cost_forecast(days=14)
        assert len(result["forecast_points"]) == 14

    def test_forecast_points_have_required_keys(self, store: AgentOpsStore) -> None:
        result = store.get_cost_forecast(days=5)
        for point in result["forecast_points"]:
            assert "date" in point
            assert "projected_cost" in point
            assert point["projected_cost"] > 0

    def test_get_cost_anomalies(self, store: AgentOpsStore) -> None:
        anomalies = store.get_cost_anomalies()
        assert isinstance(anomalies, list)
        assert len(anomalies) > 0

        a = anomalies[0]
        assert "agent_name" in a
        assert "expected_cost" in a
        assert "actual_cost" in a
        assert "spike_pct" in a

    def test_get_cost_suggestions(self, store: AgentOpsStore) -> None:
        suggestions = store.get_cost_suggestions()
        assert isinstance(suggestions, list)
        assert len(suggestions) > 0

        s = suggestions[0]
        assert "agent_name" in s
        assert "current_model" in s
        assert "suggested_model" in s
        assert "estimated_savings_pct" in s


# ---------------------------------------------------------------------------
# Compliance
# ---------------------------------------------------------------------------


class TestCompliance:
    def test_get_compliance_status(self, store: AgentOpsStore) -> None:
        result = store.get_compliance_status()
        assert "overall_status" in result
        assert "controls" in result
        assert len(result["controls"]) > 0
        assert result["controls_total"] == len(result["controls"])

    def test_compliance_controls_have_required_keys(self, store: AgentOpsStore) -> None:
        result = store.get_compliance_status()
        for ctrl in result["controls"]:
            assert "id" in ctrl
            assert "name" in ctrl
            assert "category" in ctrl
            assert "status" in ctrl
            assert ctrl["status"] in ("pass", "fail", "partial")

    def test_compliance_overall_non_compliant_when_failures(self, store: AgentOpsStore) -> None:
        result = store.get_compliance_status()
        if result["controls_failed"] > 0:
            assert result["overall_status"] == "non_compliant"

    def test_generate_compliance_report(self, store: AgentOpsStore) -> None:
        report = store.generate_compliance_report()
        assert "report_id" in report
        assert "generated_at" in report
        assert "controls_passed" in report
        assert "controls_failed" in report
        assert "evidence" in report
        assert len(report["evidence"]) > 0

    def test_compliance_report_format(self, store: AgentOpsStore) -> None:
        report = store.generate_compliance_report(report_format="csv")
        assert report["format"] == "csv"


# ---------------------------------------------------------------------------
# Team Comparison
# ---------------------------------------------------------------------------


class TestTeamComparison:
    def test_get_team_comparison(self, store: AgentOpsStore) -> None:
        teams = store.get_team_comparison()
        assert isinstance(teams, list)
        assert len(teams) > 0

        t = teams[0]
        assert "team" in t
        assert "agent_count" in t
        assert "total_cost_24h" in t
        assert "avg_health_score" in t
        assert "incidents_open" in t

    def test_team_comparison_agent_counts_match_fleet(self, store: AgentOpsStore) -> None:
        fleet = store.get_fleet_overview()
        teams = store.get_team_comparison()

        total_from_teams = sum(t["agent_count"] for t in teams)
        assert total_from_teams == fleet["summary"]["total"]

    def test_team_comparison_sorted_by_team_name(self, store: AgentOpsStore) -> None:
        teams = store.get_team_comparison()
        names = [t["team"] for t in teams]
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_singleton(self) -> None:
        store1 = get_agentops_store()
        store2 = get_agentops_store()
        assert store1 is store2

    def test_singleton_is_agentops_store(self) -> None:
        store = get_agentops_store()
        assert isinstance(store, AgentOpsStore)
