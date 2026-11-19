"""OpenTelemetry tracing with OpenInference attributes, exported over OTLP/HTTP (spec §12).

As `runtime/adk@1.3.0`'s `telemetry.py`, plus one rule for kit runtimes (spec §7.3): the kit
writes one TOOL span per tool call itself, so a TOOL span from any other instrumentation scope
(a framework's instrumentor) is exported as CHAIN, keeping its place in the tree without
counting the call twice. Every span is redacted on its way out.

Export is batched in a background thread; if the collector is down, spans are dropped and the
agent keeps serving. Traces contain customer messages: point AGENTBREEDER_SERVICE_TRACES_URL only
at a trusted collector.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Status, StatusCode

from .redaction import redact, upstream

SCOPE = "agentbreeder-runtime-kit"
KIND = "openinference.span.kind"
tracer = trace.get_tracer(SCOPE)


def _clean(attributes, scrub=redact):
    """String values (and strings in sequences) redacted; other values unchanged."""
    result = {}
    for key, value in (attributes or {}).items():
        if isinstance(value, str):
            value = scrub(value)
        elif isinstance(value, (list, tuple)):
            value = type(value)(scrub(v) if isinstance(v, str) else v for v in value)
        result[key] = value
    return result


def relabel(span: ReadableSpan, attributes: dict) -> dict:
    """A TOOL span the kit did not write is a framework's view of the kit's call: CHAIN."""
    scope = span.instrumentation_scope.name if span.instrumentation_scope else ""
    if attributes.get(KIND) == "TOOL" and scope != SCOPE:
        return {**attributes, KIND: "CHAIN"}
    return attributes


class RedactingExporter(SpanExporter):
    """Redacts every span on its way out: status, string attributes and event attributes
    (`exception.message`, `exception.stacktrace`); relabels framework TOOL spans (`relabel`)."""

    def __init__(self, inner: SpanExporter):
        self.inner = inner

    def export(self, spans):
        return self.inner.export([self.redacted(span) for span in spans])

    @staticmethod
    def redacted(span: ReadableSpan) -> ReadableSpan:
        status = span.status
        # Status and events hold exception text, a failed model call's gateway text included:
        # also without token hashes (`upstream`).
        if status.description:
            status = Status(status.status_code, upstream(status.description))
        events = [
            Event(event.name, _clean(event.attributes, upstream), event.timestamp)
            for event in span.events
        ]
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=relabel(span, _clean(span.attributes)),
            events=events,
            links=span.links,
            kind=span.kind,
            status=status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    def shutdown(self):
        return self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self.inner.force_flush(timeout_millis)


def setup(project: str, endpoint: str, enabled: bool = True, exporter=None) -> TracerProvider:
    """The process's tracer provider; spans go to `endpoint` (OTLP/HTTP) when enabled. Tests
    pass their own `exporter`."""
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": f"{project}-agent", "openinference.project.name": project}
        )
    )
    if exporter is None and enabled and endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=endpoint, timeout=5)
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(RedactingExporter(exporter)))
    trace.set_tracer_provider(provider)
    return provider


def as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


@contextmanager
def span(name: str, kind: str, input=None, **attributes):
    """Start an OpenInference span (kind: AGENT, LLM, TOOL, RETRIEVER, EMBEDDING, CHAIN)."""
    # The SDK would otherwise set the status from the raw exception text on the way out.
    with tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as current:
        current.set_attribute(KIND, kind)
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
            # Redacted like stored text; the type only, never the message: a model error's
            # message is the gateway's text (conformance `model_errors.text_spans`).
            current.set_status(Status(StatusCode.ERROR, type(error).__name__))
            current.add_event("exception", {"exception.type": type(error).__name__})
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
