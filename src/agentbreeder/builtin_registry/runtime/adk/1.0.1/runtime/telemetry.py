"""OpenTelemetry tracing with OpenInference attributes, exported over OTLP/HTTP.

Export is batched in a background thread; if the collector is down, spans are dropped and the
agent keeps serving. Traces contain customer messages: point AGENTBREEDER_SERVICE_TRACES_URL only
at a trusted collector (the Northwind stack uses Phoenix on loopback, via the tools proxy).
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer("agentbreeder-adk-runtime")


def setup(project: str, endpoint: str, enabled: bool = True) -> None:
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": f"{project}-agent", "openinference.project.name": project}
        )
    )
    if enabled and endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, timeout=5))
        )
    trace.set_tracer_provider(provider)


def as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


@contextmanager
def span(name: str, kind: str, input=None, **attributes):
    """Start an OpenInference span (kind: AGENT, LLM, TOOL, RETRIEVER, EMBEDDING, CHAIN)."""
    with tracer.start_as_current_span(name, record_exception=False) as current:
        current.set_attribute("openinference.span.kind", kind)
        if input is not None:
            current.set_attribute("input.value", as_text(input))
            current.set_attribute(
                "input.mime_type", "text/plain" if isinstance(input, str) else "application/json"
            )
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        try:
            yield current
        except Exception as error:
            current.set_status(Status(StatusCode.ERROR, str(error)[:300]))
            current.record_exception(error)
            raise


def set_output(current, value) -> None:
    current.set_attribute("output.value", as_text(value))
    current.set_attribute(
        "output.mime_type", "text/plain" if isinstance(value, str) else "application/json"
    )


def set_documents(current, documents: list[dict]) -> None:
    for i, doc in enumerate(documents):
        prefix = f"retrieval.documents.{i}.document"
        current.set_attribute(f"{prefix}.id", doc.get("id", ""))
        current.set_attribute(f"{prefix}.content", doc.get("text", ""))
        if doc.get("rrf") is not None:
            current.set_attribute(f"{prefix}.score", float(doc["rrf"]))
        current.set_attribute(
            f"{prefix}.metadata",
            json.dumps({"title": doc.get("title"), "similarity": doc.get("similarity")}),
        )


def ids(current) -> tuple[str, str]:
    context = current.get_span_context()
    return format(context.trace_id, "032x"), format(context.span_id, "016x")
