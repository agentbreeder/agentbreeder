"""Unit tests for the AgentOps service.

Both ``IncidentService`` (#207) and ``FleetService`` (#206) are DB-backed;
tests use an in-memory SQLite engine to exercise the queries without a
live PostgreSQL. The remaining ``AgentOpsStore`` only holds canary /
cost-anomaly / compliance state in memory and is tested directly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.audit import AuditEvent
from api.models.costs import CostEvent
from api.models.database import Agent, Base
from api.models.enums import AgentStatus
from api.models.tracing import Trace
from api.services.agentops_service import (
    AgentOpsStore,
    FleetService,
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


async def _seed_trace(
    db: AsyncSession,
    *,
    agent: Agent,
    duration_ms: int = 100,
    status: str = "success",
    minutes_ago: int = 5,
) -> None:
    """Insert a recent trace row."""
    db.add(
        Trace(
            id=uuid.uuid4(),
            trace_id=str(uuid.uuid4()),
            agent_id=agent.id,
            agent_name=agent.name,
            status=status,
            duration_ms=duration_ms,
            total_tokens=100,
            input_tokens=50,
            output_tokens=50,
            cost_usd=0.01,
            model_name=agent.model_primary,
            created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        )
    )
    await db.flush()


async def _seed_cost_event(
    db: AsyncSession,
    *,
    agent: Agent,
    cost: float = 1.5,
    minutes_ago: int = 5,
) -> CostEvent:
    """Insert a recent cost event."""
    ce = CostEvent(
        id=uuid.uuid4(),
        agent_id=agent.id,
        agent_name=agent.name,
        team=agent.team,
        model_name=agent.model_primary,
        provider="anthropic",
        input_tokens=100,
        output_tokens=200,
        total_tokens=300,
        cost_usd=cost,
        request_type="chat",
        created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
    )
    db.add(ce)
    await db.flush()
    return ce


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
    async def test_agent_with_no_traces_shows_zero_metrics(self, db_session: AsyncSession) -> None:
        await _seed_agent(db_session, name="ghost-bot")
        result = await FleetService.get_fleet_overview(db_session)
        assert len(result["agents"]) == 1
        a = result["agents"][0]
        assert a["name"] == "ghost-bot"
        assert a["invocations_24h"] == 0
        assert a["error_rate_pct"] == 0.0
        assert a["avg_latency_ms"] == 0
        assert a["cost_24h_usd"] == 0.0
        # running + 0% error rate → healthy
        assert a["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_failed_agent_is_down(self, db_session: AsyncSession) -> None:
        await _seed_agent(db_session, name="dead-bot", status=AgentStatus.failed)
        result = await FleetService.get_fleet_overview(db_session)
        assert result["agents"][0]["status"] == "down"
        assert result["agents"][0]["health_score"] == 0
        assert result["summary"]["down"] == 1

    @pytest.mark.asyncio
    async def test_high_error_rate_is_down(self, db_session: AsyncSession) -> None:
        a = await _seed_agent(db_session, name="bad-bot")
        # 4 successful, 16 errors = 80% error → down
        for _ in range(4):
            await _seed_trace(db_session, agent=a, status="success")
        for _ in range(16):
            await _seed_trace(db_session, agent=a, status="error")
        result = await FleetService.get_fleet_overview(db_session)
        agent = result["agents"][0]
        assert agent["error_rate_pct"] == 80.0
        assert agent["status"] == "down"

    @pytest.mark.asyncio
    async def test_aggregates_match_seeded_data(self, db_session: AsyncSession) -> None:
        a = await _seed_agent(db_session, name="busy-bot")
        await _seed_trace(db_session, agent=a, duration_ms=200, status="success")
        await _seed_trace(db_session, agent=a, duration_ms=400, status="success")
        await _seed_cost_event(db_session, agent=a, cost=2.5)
        await _seed_cost_event(db_session, agent=a, cost=3.5)
        result = await FleetService.get_fleet_overview(db_session)
        agent = result["agents"][0]
        assert agent["invocations_24h"] == 2
        assert agent["error_rate_pct"] == 0.0
        assert agent["avg_latency_ms"] == 300
        assert agent["cost_24h_usd"] == 6.0
        assert agent["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_traces_outside_24h_window_are_ignored(self, db_session: AsyncSession) -> None:
        a = await _seed_agent(db_session, name="old-bot")
        # > 24h ago — should not be counted
        db_session.add(
            Trace(
                id=uuid.uuid4(),
                trace_id=str(uuid.uuid4()),
                agent_id=a.id,
                agent_name=a.name,
                status="error",
                duration_ms=999,
                total_tokens=0,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0.0,
                created_at=datetime.now(UTC) - timedelta(hours=48),
            )
        )
        await db_session.flush()
        result = await FleetService.get_fleet_overview(db_session)
        assert result["agents"][0]["invocations_24h"] == 0


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


class TestTopAgents:
    @pytest.mark.asyncio
    async def test_empty_returns_empty(self, db_session: AsyncSession) -> None:
        assert await FleetService.get_top_agents(db_session) == []

    @pytest.mark.asyncio
    async def test_ranked_by_cost(self, db_session: AsyncSession) -> None:
        cheap = await _seed_agent(db_session, name="cheap-bot")
        pricey = await _seed_agent(db_session, name="pricey-bot")
        await _seed_cost_event(db_session, agent=cheap, cost=1.0)
        await _seed_cost_event(db_session, agent=pricey, cost=50.0)
        ranked = await FleetService.get_top_agents(db_session, metric="cost", limit=5)
        assert [a["name"] for a in ranked][:2] == ["pricey-bot", "cheap-bot"]
        assert ranked[0]["cost_24h_usd"] == 50.0

    @pytest.mark.asyncio
    async def test_ranked_by_invocations(self, db_session: AsyncSession) -> None:
        chatty = await _seed_agent(db_session, name="chatty-bot")
        quiet = await _seed_agent(db_session, name="quiet-bot")
        for _ in range(5):
            await _seed_trace(db_session, agent=chatty)
        await _seed_trace(db_session, agent=quiet)
        ranked = await FleetService.get_top_agents(db_session, metric="invocations", limit=5)
        assert ranked[0]["name"] == "chatty-bot"
        assert ranked[0]["invocations_24h"] == 5

    @pytest.mark.asyncio
    async def test_respects_limit(self, db_session: AsyncSession) -> None:
        for i in range(5):
            agent = await _seed_agent(db_session, name=f"bot-{i}")
            await _seed_cost_event(db_session, agent=agent, cost=float(i))
        ranked = await FleetService.get_top_agents(db_session, metric="cost", limit=2)
        assert len(ranked) == 2


class TestEvents:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty(self, db_session: AsyncSession) -> None:
        assert await FleetService.get_events(db_session) == []

    @pytest.mark.asyncio
    async def test_audit_event_classified_as_deploy(self, db_session: AsyncSession) -> None:
        db_session.add(
            AuditEvent(
                actor="alice@example.com",
                action="deploy.created",
                resource_type="agent",
                resource_name="my-bot",
                details={"message": "Deployed v1.0.0"},
            )
        )
        await db_session.flush()
        events = await FleetService.get_events(db_session)
        assert len(events) == 1
        assert events[0]["type"] == "deploy"
        assert events[0]["severity"] == "info"
        assert events[0]["agent_name"] == "my-bot"
        assert "Deployed v1.0.0" in events[0]["message"]

    @pytest.mark.asyncio
    async def test_cost_spike_emitted_for_expensive_event(self, db_session: AsyncSession) -> None:
        a = await _seed_agent(db_session, name="spendy-bot")
        await _seed_cost_event(db_session, agent=a, cost=15.0)
        events = await FleetService.get_events(db_session)
        spike = [e for e in events if e["type"] == "cost_spike"]
        assert len(spike) == 1
        assert spike[0]["severity"] == "critical"
        assert spike[0]["agent_name"] == "spendy-bot"

    @pytest.mark.asyncio
    async def test_cost_below_threshold_not_emitted(self, db_session: AsyncSession) -> None:
        a = await _seed_agent(db_session, name="cheap-bot")
        await _seed_cost_event(db_session, agent=a, cost=0.50)  # under $1
        events = await FleetService.get_events(db_session)
        assert all(e["type"] != "cost_spike" for e in events)

    @pytest.mark.asyncio
    async def test_events_newest_first(self, db_session: AsyncSession) -> None:
        old = AuditEvent(
            actor="x",
            action="deploy.created",
            resource_type="agent",
            resource_name="a",
            details={},
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )
        new = AuditEvent(
            actor="x",
            action="incident.opened",
            resource_type="agent",
            resource_name="b",
            details={},
            created_at=datetime.now(UTC),
        )
        db_session.add_all([old, new])
        await db_session.flush()
        events = await FleetService.get_events(db_session)
        assert events[0]["timestamp"] > events[1]["timestamp"]

    @pytest.mark.asyncio
    async def test_limit_caps_returned(self, db_session: AsyncSession) -> None:
        for i in range(10):
            db_session.add(
                AuditEvent(
                    actor="x",
                    action="deploy.created",
                    resource_type="agent",
                    resource_name=f"a-{i}",
                    details={},
                )
            )
        await db_session.flush()
        assert len(await FleetService.get_events(db_session, limit=3)) == 3


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
    def test_empty_forecast_when_no_recent_spend(self, store: AgentOpsStore) -> None:
        # base_daily_cost defaults to 0 — forecast collapses to zeros.
        result = store.get_cost_forecast(days=30)
        assert "current_month_spend" in result
        assert "projected_month_spend" in result
        assert result["current_month_spend"] == 0.0
        assert result["projected_month_spend"] == 0.0
        assert result["confidence"] == "low"
        assert result["trend"] == "flat"

    def test_forecast_with_real_anchor(self, store: AgentOpsStore) -> None:
        result = store.get_cost_forecast(days=30, base_daily_cost=10.0)
        assert result["projected_month_spend"] > 0
        assert result["confidence"] == "medium"
        assert result["trend"] == "increasing"

    def test_forecast_has_correct_number_of_points(self, store: AgentOpsStore) -> None:
        result = store.get_cost_forecast(days=14, base_daily_cost=5.0)
        assert len(result["forecast_points"]) == 14

    def test_forecast_points_have_required_keys(self, store: AgentOpsStore) -> None:
        result = store.get_cost_forecast(days=5, base_daily_cost=10.0)
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


# ---------------------------------------------------------------------------
# Team Comparison
# ---------------------------------------------------------------------------


class TestTeamComparison:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty(self, db_session: AsyncSession) -> None:
        assert await FleetService.get_team_comparison(db_session) == []

    @pytest.mark.asyncio
    async def test_aggregates_across_agents_and_costs(self, db_session: AsyncSession) -> None:
        a1 = await _seed_agent(db_session, name="bot-a", team="alpha")
        a2 = await _seed_agent(db_session, name="bot-b", team="alpha")
        await _seed_agent(db_session, name="bot-c", team="beta")
        await _seed_cost_event(db_session, agent=a1, cost=2.0)
        await _seed_cost_event(db_session, agent=a2, cost=3.0)
        teams = await FleetService.get_team_comparison(db_session)
        by_team = {t["team"]: t for t in teams}
        assert by_team["alpha"]["agent_count"] == 2
        assert by_team["alpha"]["total_cost_24h"] == 5.0
        assert by_team["beta"]["agent_count"] == 1
        assert by_team["beta"]["total_cost_24h"] == 0.0

    @pytest.mark.asyncio
    async def test_open_incidents_passed_through(self, db_session: AsyncSession) -> None:
        await _seed_agent(db_session, name="bot-a", team="alpha")
        teams = await FleetService.get_team_comparison(
            db_session, open_incidents_by_agent={"bot-a": 3}
        )
        assert teams[0]["incidents_open"] == 3

    @pytest.mark.asyncio
    async def test_sorted_by_team_name(self, db_session: AsyncSession) -> None:
        for team in ["zulu", "alpha", "mike"]:
            await _seed_agent(db_session, name=f"bot-{team}", team=team)
        teams = await FleetService.get_team_comparison(db_session)
        assert [t["team"] for t in teams] == ["alpha", "mike", "zulu"]


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
