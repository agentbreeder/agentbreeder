"""Unit tests for the AgentOps service."""

from __future__ import annotations

import pytest

from api.services.agentops_service import AgentOpsStore, get_agentops_store


@pytest.fixture
def store() -> AgentOpsStore:
    """Return a fresh AgentOpsStore for each test."""
    return AgentOpsStore()


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
    def test_create_incident(self, store: AgentOpsStore) -> None:
        incident = store.create_incident(
            agent_name="test-agent",
            title="Test incident",
            severity="high",
            description="Something went wrong",
        )
        assert incident["agent_name"] == "test-agent"
        assert incident["title"] == "Test incident"
        assert incident["severity"] == "high"
        assert incident["status"] == "open"
        assert incident["id"].startswith("inc-")
        assert len(incident["timeline"]) == 1

    def test_get_incident_found(self, store: AgentOpsStore) -> None:
        inc = store.create_incident(
            agent_name="a1",
            title="t1",
            severity="low",
            description="d1",
        )
        found = store.get_incident(inc["id"])
        assert found is not None
        assert found["id"] == inc["id"]

    def test_get_incident_not_found(self, store: AgentOpsStore) -> None:
        result = store.get_incident("nonexistent-id")
        assert result is None

    def test_update_incident_status(self, store: AgentOpsStore) -> None:
        inc = store.create_incident(
            agent_name="a1",
            title="t1",
            severity="medium",
            description="d1",
        )
        updated = store.update_incident(inc["id"], status="investigating")
        assert updated is not None
        assert updated["status"] == "investigating"
        # Timeline should have 2 entries now (create + status change)
        assert len(updated["timeline"]) == 2

    def test_update_incident_not_found(self, store: AgentOpsStore) -> None:
        result = store.update_incident("nonexistent-id", status="resolved")
        assert result is None

    def test_update_incident_message_only(self, store: AgentOpsStore) -> None:
        inc = store.create_incident(
            agent_name="a1",
            title="t1",
            severity="low",
            description="d1",
        )
        updated = store.update_incident(inc["id"], message="Added a note")
        assert updated is not None
        assert updated["status"] == "open"  # unchanged
        assert updated["timeline"][-1]["message"] == "Added a note"

    def test_list_incidents_filter_by_status(self, store: AgentOpsStore) -> None:
        store.create_incident(agent_name="a1", title="t1", severity="high", description="d1")
        store.create_incident(agent_name="a2", title="t2", severity="low", description="d2")

        open_incidents = store.list_incidents(status="open")
        # Should include newly created ones (all created as "open")
        for inc in open_incidents:
            assert inc["status"] == "open"

    def test_list_incidents_filter_by_severity(self, store: AgentOpsStore) -> None:
        store.create_incident(agent_name="a1", title="t1", severity="critical", description="d1")
        store.create_incident(agent_name="a2", title="t2", severity="low", description="d2")

        critical = store.list_incidents(severity="critical")
        for inc in critical:
            assert inc["severity"] == "critical"

    def test_execute_action_restart(self, store: AgentOpsStore) -> None:
        inc = store.create_incident(
            agent_name="test-agent",
            title="t1",
            severity="high",
            description="d1",
        )
        result = store.execute_action(inc["id"], "restart")
        assert result["success"] is True
        assert result["action"] == "restart"
        assert "restart" in result["message"].lower()

    def test_execute_action_invalid_incident(self, store: AgentOpsStore) -> None:
        result = store.execute_action("nonexistent-id", "restart")
        assert result["success"] is False
        assert "error" in result


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
