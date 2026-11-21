"""OpenTelemetry tracing with OpenInference attributes, exported over OTLP/HTTP.

Export is batched in a background thread; if the collector is down, spans are dropped and the
agent keeps serving. A turn's spans are exported when the turn ends (1.5.0), so a turn a gateway
guardrail blocked can be exported without its content (`TurnSpans`, #1106). Traces contain
customer messages: point AGENTBREEDER_SERVICE_TRACES_URL only at a trusted collector (the
Northwind stack uses Phoenix on loopback, via the tools proxy).
"""

from __future__ import annotations

import json
import os
import threading
from collections import OrderedDict
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Status, StatusCode
from redaction import redact, upstream

tracer = trace.get_tracer("agentbreeder-adk-runtime")


def pin_adk_schema() -> None:
    """Keep ADK on its legacy span schema. google-adk 2.14 defaults to its semconv-aligned one (no
    `call_llm` span, tool content in other attributes); this runtime's span tree and redaction
    were written and checked for the legacy one, which ADK keeps until at least 2027-03-31. Set,
    not defaulted: an operator's environment cannot switch it. ADK reads it at call time."""
    os.environ["ADK_TELEMETRY_SCHEMA_VERSION_OPT_IN"] = "1"


pin_adk_schema()


# Text that is never exported in a span: a loaded skill's body and reference files (1.5.0).
# ADK records a tool's response and each model request on its own spans, so the text is
# replaced on the way out, wherever it appears: as it is, or escaped inside JSON (once or twice).
_WITHHELD: dict[str, str] = {}  # text as it may appear -> the label that replaces it
_FORMS: tuple[str, ...] = ()  # the same texts, longest first (one text may contain another)
WITHHELD_MIN = 16  # shorter text is not withheld: replacing it would eat ordinary words


def withhold(text: str, label: str) -> None:
    """Never export `text` in a span; `[label withheld]` stands in for it."""
    text = text.strip()
    if len(text) < WITHHELD_MIN:
        return
    once = json.dumps(text)[1:-1]
    plain = json.dumps(text, ensure_ascii=False)[1:-1]
    global _FORMS
    for form in (text, once, plain, json.dumps(once)[1:-1], json.dumps(plain)[1:-1]):
        _WITHHELD[form] = f"[{label} withheld]"
    _FORMS = tuple(sorted(_WITHHELD, key=len, reverse=True))


def withheld(text: str) -> str:
    """`text` without any withheld text: the exact text or one of its JSON-escaped forms.
    A partial quote or a paraphrase is not recognised."""
    for form in _FORMS:
        if form in text:
            text = text.replace(form, _WITHHELD[form])
    return text


def _clean(attributes, scrub=redact):
    """String values (and strings in sequences) redacted and without withheld text; other
    values unchanged."""

    def clean(text: str) -> str:
        return scrub(withheld(text) if _WITHHELD else text)

    result = {}
    for key, value in (attributes or {}).items():
        if isinstance(value, str):
            value = clean(value)
        elif isinstance(value, (list, tuple)):
            value = type(value)(clean(v) if isinstance(v, str) else v for v in value)
        result[key] = value
    return result


class RedactingExporter(SpanExporter):
    """Redacts every span on its way out: status, string attributes and event attributes
    (`exception.message`, `exception.stacktrace`). ADK's own spans record exceptions with the
    SDK defaults, so redacting only the runtime's spans would not be enough."""

    def __init__(self, inner: SpanExporter):
        self.inner = inner

    def export(self, spans):
        return self.inner.export([self._redacted(span) for span in spans])

    @staticmethod
    def _redacted(span: ReadableSpan) -> ReadableSpan:
        status = span.status
        # Status and events hold exception text, a failed model call's gateway text included:
        # also without token hashes, like the model-error log line (`upstream`).
        if status.description:
            status = Status(status.status_code, upstream(withheld(status.description)))
        events = [
            Event(event.name, _clean(event.attributes, upstream), event.timestamp)
            for event in span.events
        ]
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=_clean(span.attributes),
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


# A turn a gateway guardrail blocked (#1106): its spans are exported without content. The
# gateway does not say which text matched (the message, a tool result or the model's answer),
# so nothing a customer, a tool or a model wrote is kept: only these attributes, and only the
# first word of a span's name (ADK puts a tool's name, which a model can invent, after it).
BARE_KEYS = frozenset(
    {"openinference.span.kind", "session.id", "user.id", "agent.turn_id", "agent.framework"}
    | {"agent.name", "llm.model_name", "retrieval.mode", "gen_ai.operation.name"}
    | {"gen_ai.system", "gen_ai.request.model"}
    # The runtime's own labels, each a value it validated or made itself. Listed one by one:
    # `agentbreeder.unknown_tool(s)` hold names a model made up, and a new attribute must not
    # pass by default.
    | {"agentbreeder.deployment_id", "agentbreeder.error_code", "agentbreeder.fallbacks"}
    | {"agentbreeder.cached_prompt_tokens", "agentbreeder.subagent"}
    | {"agentbreeder.guardrail.action", "agentbreeder.guardrail.name"}
    | {"agentbreeder.guardrail.entity", "agentbreeder.skill.name", "agentbreeder.skill.version"}
    | {"agentbreeder.skill.sha256", "agentbreeder.skill.resource"}
)
BARE_PREFIXES = ("llm.token_count.", "gen_ai.usage.")  # numbers only (see `bare`)
MAX_HELD = 2000  # spans held for one turn; the rest of that turn's spans go out bare
# The exporter's queue: room for four turns' held spans released at the same moment.
SPAN_QUEUE = 4 * MAX_HELD + 2048
MAX_BLOCKED = 4096  # blocked traces remembered, for a span that ends after its turn


def bare(span: ReadableSpan) -> ReadableSpan:
    """`span` without content: its ids, times, kind, counts and the runtime's own labels."""
    attributes = {
        key: value
        for key, value in (span.attributes or {}).items()
        if (key in BARE_KEYS and isinstance(value, (str, bool, int, float)))
        or (key.startswith(BARE_PREFIXES) and isinstance(value, (bool, int, float)))
    }
    events = [
        Event(
            event.name,
            {k: v for k, v in (event.attributes or {}).items() if k == "exception.type"},
            event.timestamp,
        )
        for event in span.events
    ]
    return ReadableSpan(
        name=(span.name or "").split(" ", 1)[0],
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=events,
        links=span.links,
        kind=span.kind,
        status=Status(span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class TurnSpans(SpanProcessor):
    """Keeps a running turn's finished spans until the turn ends, then passes them on: as they
    are, or bare when a guardrail blocked the turn. Without this a span that ended before the
    block (the knowledge search, an earlier model call) could already be on its way out."""

    def __init__(self, inner: SpanProcessor):
        self.inner = inner
        self.lock = threading.Lock()
        self.held: dict[int, list[ReadableSpan]] = {}
        self.blocked: OrderedDict[int, None] = OrderedDict()

    def on_start(self, span, parent_context=None):
        self.inner.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        trace_id = span.context.trace_id
        with self.lock:
            spans = self.held.get(trace_id)
            if spans is not None and len(spans) < MAX_HELD:
                spans.append(span)
                return
            # Past the cap nobody knows yet whether the turn will be blocked: bare, to be safe.
            strip = spans is not None or trace_id in self.blocked
        self.inner.on_end(bare(span) if strip else span)

    def hold(self, trace_id: int) -> None:
        with self.lock:
            self.held.setdefault(trace_id, [])

    def release(self, trace_id: int, blocked: bool) -> None:
        with self.lock:
            spans = self.held.pop(trace_id, [])
            if blocked:
                self.blocked[trace_id] = None
                while len(self.blocked) > MAX_BLOCKED:
                    self.blocked.popitem(last=False)
        for span in spans:
            self.inner.on_end(bare(span) if blocked else span)

    def shutdown(self):
        with self.lock:
            held, self.held = self.held, {}
            self.blocked.update(dict.fromkeys(held))  # also for a span that ends after this
        for spans in held.values():  # turns cut off by shutdown: their outcome is unknown
            for span in spans:
                self.inner.on_end(bare(span))
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self.inner.force_flush(timeout_millis)


_TURNS: TurnSpans | None = None


def hold(trace_id: str | None) -> None:
    """Keep the spans of this trace (a turn's `trace_id`, as hex) until `release`."""
    if _TURNS is not None and trace_id and int(trace_id, 16):
        _TURNS.hold(int(trace_id, 16))


def release(trace_id: str | None, blocked: bool = False) -> None:
    """Export the turn's spans: bare when a gateway guardrail blocked the turn."""
    if _TURNS is not None and trace_id and int(trace_id, 16):
        _TURNS.release(int(trace_id, 16), blocked)


def setup(project: str, endpoint: str, enabled: bool = True) -> None:
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": f"{project}-agent", "openinference.project.name": project}
        )
    )
    if enabled and endpoint:
        global _TURNS
        _TURNS = TurnSpans(
            # Held spans go out together when their turn ends, so the queue is sized for
            # several full turns at once rather than the default 2048 (#1106 review).
            BatchSpanProcessor(
                RedactingExporter(OTLPSpanExporter(endpoint=endpoint, timeout=5)),
                max_queue_size=SPAN_QUEUE,
            )
        )
        provider.add_span_processor(_TURNS)
    trace.set_tracer_provider(provider)


def as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


@contextmanager
def span(name: str, kind: str, input=None, **attributes):
    """Start an OpenInference span (kind: AGENT, LLM, TOOL, RETRIEVER, EMBEDDING, CHAIN)."""
    # The SDK would otherwise set the status from the raw exception text on the way out.
    with tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as current:
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
            # Redacted like stored text; the type and message only (no traceback frames).
            message = upstream(str(error))[:300]
            current.set_status(Status(StatusCode.ERROR, message))
            current.add_event(
                "exception",
                {"exception.type": type(error).__name__, "exception.message": message},
            )
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
