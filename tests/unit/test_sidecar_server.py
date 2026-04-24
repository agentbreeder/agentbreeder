"""Tests for engine/sidecar/server.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


def _get_client():
    from engine.sidecar.server import app

    return TestClient(app)


class TestSidecarHealth:
    def test_health_returns_ok(self) -> None:
        client = _get_client()
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["sidecar"] == "agentbreeder"
        assert "otel_endpoint" in data
        assert "cost_tracking" in data

    def test_health_includes_guardrails_list(self) -> None:
        with patch.dict("os.environ", {"AB_GUARDRAILS": "pii_detection,content_filter"}):
            from importlib import reload

            import engine.sidecar.server as mod

            reload(mod)
            client = TestClient(mod.app)
            response = client.get("/health")
            assert response.status_code == 200

    def test_health_empty_guardrails(self) -> None:
        with patch.dict("os.environ", {"AB_GUARDRAILS": ""}):
            from importlib import reload

            import engine.sidecar.server as mod

            reload(mod)
            client = TestClient(mod.app)
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["guardrails"] == []


class TestSidecarTrace:
    def test_trace_without_tracer(self) -> None:
        """When OTel is unavailable, trace endpoint still returns 200."""
        import engine.sidecar.server as mod

        original_tracer = mod._tracer
        mod._tracer = None
        try:
            client = TestClient(mod.app)
            response = client.post(
                "/trace",
                json={"operation": "agent.tool_call", "attributes": {"tool": "search"}},
            )
            assert response.status_code == 200
            assert response.json() == {"status": "recorded"}
        finally:
            mod._tracer = original_tracer

    def test_trace_with_tracer(self) -> None:
        """When a tracer is available, span is started and attributes set."""
        import engine.sidecar.server as mod

        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_span

        original_tracer = mod._tracer
        mod._tracer = mock_tracer
        try:
            client = TestClient(mod.app)
            response = client.post(
                "/trace",
                json={"operation": "agent.llm_call", "attributes": {"tokens": 42}},
            )
            assert response.status_code == 200
            mock_tracer.start_as_current_span.assert_called_once_with("agent.llm_call")
        finally:
            mod._tracer = original_tracer

    def test_trace_missing_operation_defaults(self) -> None:
        import engine.sidecar.server as mod

        original_tracer = mod._tracer
        mod._tracer = None
        try:
            client = TestClient(mod.app)
            response = client.post("/trace", json={})
            assert response.status_code == 200
        finally:
            mod._tracer = original_tracer


class TestSidecarCost:
    def test_cost_recorded(self) -> None:
        import engine.sidecar.server as mod

        original = mod._cost_tracking
        mod._cost_tracking = True
        try:
            client = TestClient(mod.app)
            response = client.post(
                "/cost",
                json={
                    "agent": "my-agent",
                    "model": "claude-sonnet-4",
                    "input_tokens": 1024,
                    "output_tokens": 256,
                    "cost_usd": 0.0038,
                },
            )
            assert response.status_code == 200
            assert response.json() == {"status": "recorded"}
        finally:
            mod._cost_tracking = original

    def test_cost_skipped_when_tracking_disabled(self) -> None:
        import engine.sidecar.server as mod

        original = mod._cost_tracking
        mod._cost_tracking = False
        try:
            client = TestClient(mod.app)
            response = client.post("/cost", json={"agent": "x", "cost_usd": 1.0})
            assert response.status_code == 200
            assert response.json() == {"status": "skipped"}
        finally:
            mod._cost_tracking = original

    def test_cost_partial_payload(self) -> None:
        import engine.sidecar.server as mod

        original = mod._cost_tracking
        mod._cost_tracking = True
        try:
            client = TestClient(mod.app)
            response = client.post("/cost", json={})
            assert response.status_code == 200
            assert response.json()["status"] == "recorded"
        finally:
            mod._cost_tracking = original
