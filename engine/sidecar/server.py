"""AgentBreeder observability sidecar server.

Issue #73: Auto-inject OTel observability sidecar.

Lightweight FastAPI app that runs inside the sidecar container.  It exposes:
  GET  /health   — liveness probe consumed by ECS / Cloud Run
  POST /trace    — accept a span event from the agent container and forward to OTel
  POST /cost     — accept a cost/token event and emit as OTel metric + structured log

The OTel SDK setup is best-effort; if the exporter fails to initialise (e.g. the
collector endpoint is unreachable at startup) the health endpoint still returns 200
so the container does not crash-loop.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

logger = logging.getLogger(__name__)
app = FastAPI(title="AgentBreeder Sidecar", version="0.1.0")

_otel_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
_cost_tracking = os.getenv("AB_COST_TRACKING", "true").lower() == "true"
_guardrails = [g for g in os.getenv("AB_GUARDRAILS", "").split(",") if g]

# --- OTel initialisation (best-effort) ---
_tracer = None
try:
    from opentelemetry import trace  # type: ignore[import]
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import]
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import]
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore[import]

    _provider = TracerProvider()
    _provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=_otel_endpoint))
    )
    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer("agentbreeder.sidecar")
    logger.info("OTel tracer initialised — exporting to %s", _otel_endpoint)
except Exception as exc:  # noqa: BLE001
    logger.warning("Failed to initialise OTel exporter: %s — tracing disabled", exc)


@app.get("/health")
def health() -> dict:
    """Liveness probe for ECS health checks and Cloud Run startup probes."""
    return {
        "status": "ok",
        "sidecar": "agentbreeder",
        "otel_endpoint": _otel_endpoint,
        "cost_tracking": _cost_tracking,
        "guardrails": _guardrails,
    }


@app.post("/trace")
async def receive_trace(payload: dict) -> dict:
    """Accept a trace event from the agent container and record an OTel span.

    Expected payload shape:
        {
            "operation": "agent.tool_call",
            "attributes": {"tool": "search", "tokens": 42}
        }
    """
    if _tracer is not None:
        from opentelemetry import trace  # type: ignore[import]

        with _tracer.start_as_current_span(
            payload.get("operation", "agent.call")
        ) as span:
            for key, value in payload.get("attributes", {}).items():
                span.set_attribute(key, str(value))
    else:
        logger.debug("Trace event received (OTel disabled): %s", payload.get("operation"))
    return {"status": "recorded"}


@app.post("/cost")
async def record_cost(payload: dict) -> dict:
    """Accept a cost/token event from the agent container.

    Emits a structured log entry and (when OTel is available) a span attribute.
    A future version will emit these as OTel metrics via the Metrics API.

    Expected payload shape:
        {
            "agent": "my-agent",
            "model": "claude-sonnet-4",
            "input_tokens": 1024,
            "output_tokens": 256,
            "cost_usd": 0.0038
        }
    """
    if not _cost_tracking:
        return {"status": "skipped"}
    logger.info(
        "Cost event agent=%s model=%s input_tokens=%s output_tokens=%s cost_usd=%s",
        payload.get("agent", "unknown"),
        payload.get("model", "unknown"),
        payload.get("input_tokens", 0),
        payload.get("output_tokens", 0),
        payload.get("cost_usd", 0.0),
    )
    return {"status": "recorded"}
