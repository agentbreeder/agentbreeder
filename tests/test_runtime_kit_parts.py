"""The runtime kit's smaller parts (#1083 F2b): memory history, redaction with forget, config,
library logs, telemetry (redaction and the TOOL relabel)."""

from __future__ import annotations

import logging

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agentbreeder.runtime_kit import config as cfg
from agentbreeder.runtime_kit import logs, redaction, telemetry
from agentbreeder.runtime_kit import memory as kit_memory


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def execute(self, query, params=()):
        self.queries.append((query, params))
        return FakeCursor(self.rows)


def _memory(rows):
    memory = kit_memory.Memory.__new__(kit_memory.Memory)
    memory.db = FakeDb(rows)
    memory.events_enabled = True
    return memory


def test_history_is_oldest_first_keyed_by_customer_and_session():
    memory = _memory([("third", "3"), ("second", "2"), ("first", "1")])  # newest first
    assert memory.history("c-1", "s-1") == [("first", "1"), ("second", "2"), ("third", "3")]
    query, params = memory.db.queries[0]
    assert "customer=%s AND session=%s" in query and "error IS NULL" in query
    assert params == ("c-1", "s-1", kit_memory.HISTORY_TURNS)


def test_history_is_capped_in_bytes_dropping_the_oldest(monkeypatch):
    monkeypatch.setattr(kit_memory, "HISTORY_BYTES", 10)
    memory = _memory([("new", "abc"), ("old", "defg"), ("oldest", "x")])
    assert memory.history("c", "s") == [("new", "abc")]


def test_redaction_remembers_and_forgets():
    token = "f" * 32
    redaction.remember(token)
    redaction.remember("short")  # under 16 characters: never a pattern
    assert redaction.redact(f"token {token} here") == "token [redacted] here"
    assert "short" not in redaction.values()
    redaction.forget(token)
    assert token not in redaction.values()
    assert redaction.redact("key sk-1234567890abcdef") == "key sk-[redacted]"
    assert redaction.redact("masked sk-...WXYZ") == "masked sk-[redacted]"
    assert redaction.upstream("hash " + "a" * 64) == "hash [hash]"
    assert redaction.one_line("a\nb\tc") == "a b c"


def test_redaction_replaces_the_longest_secret_first():
    redaction.remember("abcdefghijklmnop")
    redaction.remember("abcdefghijklmnopqrstuvwxyz")
    try:
        assert redaction.redact("abcdefghijklmnopqrstuvwxyz") == "[redacted]"
    finally:
        redaction.forget("abcdefghijklmnop")
        redaction.forget("abcdefghijklmnopqrstuvwxyz")


def test_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "APP_DIR", tmp_path)
    cfg.config.cache_clear()
    with pytest.raises(cfg.ConfigError):
        cfg.config()
    (tmp_path / "agent.config.json").write_text('{"name": "a"}')
    cfg.config.cache_clear()
    with pytest.raises(cfg.ConfigError, match="missing name or model"):
        cfg.config()
    (tmp_path / "agent.config.json").write_text('{"name": "a", "model": {"alias": "m"}}')
    cfg.config.cache_clear()
    assert cfg.config()["name"] == "a"
    cfg.config.cache_clear()
    assert cfg.artifact("x/y") == (tmp_path / "x/y").resolve()
    with pytest.raises(cfg.ConfigError):
        cfg.artifact("../outside")
    secret = tmp_path / "key"
    secret.write_text("value\n")
    monkeypatch.setenv("KIT_TEST_FILE", str(secret))
    assert cfg.secret("KIT_TEST") == "value"
    monkeypatch.setenv("KIT_TEST_FILE", str(tmp_path / "missing"))
    with pytest.raises(cfg.ConfigError):
        cfg.secret("KIT_TEST")
    monkeypatch.delenv("KIT_TEST_FILE")
    monkeypatch.setenv("KIT_TEST", "plain")
    with pytest.raises(cfg.ConfigError):
        cfg.secret("KIT_TEST")
    monkeypatch.setenv("AGENTBREEDER_DEV_PLAIN_SECRETS", "1")
    assert cfg.secret("KIT_TEST") == "plain"
    monkeypatch.setenv("AGENTBREEDER_SERVICE_TRACES_URL", "http://p/v1/traces/")
    assert cfg.service("traces_url") == "http://p/v1/traces"
    monkeypatch.setenv("AGENTBREEDER_GATEWAY_URL", "http://gw:4000/")
    assert cfg.gateway_url() == "http://gw:4000"
    monkeypatch.setenv("AGENTBREEDER_DEPLOYMENT_ID", "dep-1")
    assert cfg.deployment_id() == "dep-1"
    monkeypatch.setenv("AGENTBREEDER_DEPLOYMENT_ID", "bad id")
    assert cfg.deployment_id() is None


def test_library_logs_are_one_redacted_line(capsys):
    logs.quiet(("kit_test_framework",))
    logger = logging.getLogger("kit_test_framework.sub")
    try:
        raise ValueError("boom")
    except ValueError:
        logger.warning("failed with key sk-1234567890abcdef\nsecond line", exc_info=True)
    logger.info("not shown")
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "sk-[redacted]" in err and "Traceback" not in err
    assert "not shown" not in err
    assert logging.getLogger("httpx").level == logging.WARNING


def _spans():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(telemetry.RedactingExporter(exporter)))
    return provider, exporter


def test_framework_tool_spans_are_relabelled_and_the_kits_kept():
    provider, exporter = _spans()
    framework = provider.get_tracer("openinference.instrumentation.langchain")
    with framework.start_as_current_span("get_order") as span:
        span.set_attribute("openinference.span.kind", "TOOL")
    kit_tracer = provider.get_tracer(telemetry.SCOPE)
    with kit_tracer.start_as_current_span("get_order") as span:
        span.set_attribute("openinference.span.kind", "TOOL")
    with framework.start_as_current_span("ChatOpenAI") as span:
        span.set_attribute("openinference.span.kind", "LLM")
    kinds = [s.attributes["openinference.span.kind"] for s in exporter.get_finished_spans()]
    assert kinds == ["CHAIN", "TOOL", "LLM"]


def test_spans_are_redacted_on_the_way_out():
    from opentelemetry.trace import Status, StatusCode

    provider, exporter = _spans()
    secret = "z" * 24
    redaction.remember(secret)
    try:
        with provider.get_tracer("x").start_as_current_span("s") as span:
            span.set_attribute("input.value", f"key {secret}")
            span.set_attribute("list", [secret, 1])
            span.add_event("exception", {"exception.message": f"failed {secret} " + "b" * 64})
            span.set_status(Status(StatusCode.ERROR, f"error {secret}"))
    finally:
        redaction.forget(secret)
    (span,) = exporter.get_finished_spans()
    assert secret not in str(dict(span.attributes)) and "[hash]" in str(span.events[0].attributes)
    assert secret not in span.status.description


def test_the_kits_span_records_an_exception_by_type_only():
    from opentelemetry import trace

    # The process's tracer provider can be set only once: add an exporter to whichever is set.
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        telemetry.setup("kit-test", "", enabled=False)
    provider = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(telemetry.RedactingExporter(exporter)))
    with pytest.raises(ValueError):
        with telemetry.span("agent_turn", "AGENT", input={"a": 1}, **{"x": None, "y": "z"}):
            raise ValueError("provider said SECRET")
    provider.force_flush()
    (span,) = exporter.get_finished_spans()
    assert (
        "SECRET" not in str(span.events[0].attributes) and span.status.description == "ValueError"
    )
    assert span.attributes["input.mime_type"] == "application/json" and "x" not in span.attributes
    with telemetry.span("r", "RETRIEVER") as current:
        telemetry.set_documents(current, [{"id": "a", "text": "t", "rrf": 0.5, "title": "T"}])
        telemetry.set_output(current, ["x"])
        trace_id, span_id = telemetry.ids(current)
    assert len(trace_id) == 32 and len(span_id) == 16
    exporter_inner = telemetry.RedactingExporter(exporter)
    assert exporter_inner.force_flush() is not None
    exporter_inner.shutdown()


def test_setup_without_an_endpoint_exports_nothing():
    provider = telemetry.setup("kit-test", "", enabled=True)
    assert provider is not None
    provider = telemetry.setup("kit-test", "http://collector:4318/v1/traces", enabled=False)
    assert provider is not None
