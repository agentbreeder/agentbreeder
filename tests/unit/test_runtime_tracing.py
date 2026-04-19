"""Tests for engine/runtimes/templates/_tracing.py — OTel init and noop fallbacks."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch


class TestInitTracingNoEndpoint:
    def test_returns_noop_tracer_when_no_endpoint(self, monkeypatch) -> None:
        from engine.runtimes.templates._tracing import _NoopTracer, init_tracing

        monkeypatch.delenv("OPENTELEMETRY_ENDPOINT", raising=False)
        tracer = init_tracing()
        assert isinstance(tracer, _NoopTracer)

    def test_noop_tracer_start_as_current_span(self, monkeypatch) -> None:
        from engine.runtimes.templates._tracing import init_tracing

        monkeypatch.delenv("OPENTELEMETRY_ENDPOINT", raising=False)
        tracer = init_tracing()
        span = tracer.start_as_current_span("test.op")
        assert span is not None

    def test_noop_span_context_manager(self, monkeypatch) -> None:
        from engine.runtimes.templates._tracing import init_tracing

        monkeypatch.delenv("OPENTELEMETRY_ENDPOINT", raising=False)
        tracer = init_tracing()
        with tracer.start_as_current_span("test.op") as span:
            span.set_attribute("key", "value")
            span.record_exception(RuntimeError("boom"))
            span.set_status(MagicMock())

    def test_noop_tracer_start_span(self, monkeypatch) -> None:
        from engine.runtimes.templates._tracing import _NoopSpan, init_tracing

        monkeypatch.delenv("OPENTELEMETRY_ENDPOINT", raising=False)
        tracer = init_tracing()
        span = tracer.start_span("test.span")
        assert isinstance(span, _NoopSpan)


class TestInitTracingImportError:
    def test_returns_noop_when_otel_not_installed(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENTELEMETRY_ENDPOINT", "http://localhost:4317")
        # Hide opentelemetry from the import system
        with patch.dict(
            sys.modules,
            {
                "opentelemetry": None,
                "opentelemetry.trace": None,
                "opentelemetry.sdk": None,
                "opentelemetry.sdk.trace": None,
                "opentelemetry.sdk.resources": None,
                "opentelemetry.sdk.trace.export": None,
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": None,
            },
        ):
            import importlib

            import engine.runtimes.templates._tracing as tracing_mod

            importlib.reload(tracing_mod)
            tracer = tracing_mod.init_tracing()
            assert isinstance(tracer, tracing_mod._NoopTracer)

    def test_returns_noop_on_general_exception(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENTELEMETRY_ENDPOINT", "http://localhost:4317")
        mock_trace = MagicMock()
        mock_provider_cls = MagicMock(side_effect=RuntimeError("provider init failed"))
        mock_trace.set_tracer_provider = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "opentelemetry": MagicMock(trace=mock_trace),
                "opentelemetry.trace": mock_trace,
                "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
                "opentelemetry.sdk.resources": MagicMock(Resource=MagicMock()),
                "opentelemetry.sdk.trace.export": MagicMock(),
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": MagicMock(),
            },
        ):
            import importlib

            import engine.runtimes.templates._tracing as tracing_mod

            importlib.reload(tracing_mod)
            tracer = tracing_mod.init_tracing()
            assert isinstance(tracer, tracing_mod._NoopTracer)


class TestConstants:
    def test_attribute_name_constants(self) -> None:
        from engine.runtimes.templates._tracing import (
            ATTR_AGENT_FRAMEWORK,
            ATTR_AGENT_NAME,
            ATTR_AGENT_VERSION,
            ATTR_LLM_MODEL,
            ATTR_LLM_TOKENS_IN,
            ATTR_LLM_TOKENS_OUT,
        )

        assert ATTR_AGENT_NAME == "agent.name"
        assert ATTR_AGENT_VERSION == "agent.version"
        assert ATTR_AGENT_FRAMEWORK == "agent.framework"
        assert ATTR_LLM_MODEL == "llm.model"
        assert ATTR_LLM_TOKENS_IN == "llm.token_count.input"
        assert ATTR_LLM_TOKENS_OUT == "llm.token_count.output"
