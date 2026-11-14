"""`agentbreeder traces` (#907): recent agent traces per deployment from the stack's Phoenix,
read-only, against a fake Phoenix that answers like phoenix 21.0.0's REST API."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from agentbreeder import cli, phoenix_traces
from agentbreeder.commands import traces as command
from agentbreeder.phoenix_traces import Phoenix, Shown, TracesError

KEY = "phoenix-key-for-traces-tests"
D1 = "a" * 32
D2 = "b" * 32
D3 = "c" * 32
T0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def span(trace, span_id, parent=None, name="s", kind="CHAIN", at=0, ms=10, status="UNSET",
         message="", **attributes):  # fmt: skip
    start = T0 + timedelta(seconds=at)
    return {
        "id": f"Span:{trace}{span_id}",
        "name": name,
        "context": {"trace_id": trace, "span_id": span_id},
        "span_kind": kind,
        "parent_id": parent,
        "start_time": iso(start),
        "end_time": iso(start + timedelta(milliseconds=ms)),
        "status_code": status,
        "status_message": message,
        "attributes": attributes,
        "events": [],
    }


def turn(trace, at=0, deployment=None, error=False):
    """A runtime-shaped trace: agent_turn > (LLM, TOOL)."""
    root_attributes = {"session.id": "s-1", "agent.turn_id": "t-1", "input.value": "secret msg"}
    if deployment:
        root_attributes[phoenix_traces.ATTRIBUTE] = deployment
    return [
        span(trace, "1" * 16, None, "agent_turn", "AGENT", at, 50, **root_attributes),
        span(
            trace,
            "2" * 16,
            "1" * 16,
            "litellm_request",
            "LLM",
            at,
            20,
            **{
                "llm.model_name": "gemini-2.5-flash",
                "llm.token_count.prompt": 100,
                "llm.token_count.completion": 30,
            },
        ),  # fmt: skip
        span(
            trace,
            "3" * 16,
            "1" * 16,
            "execute_tool lookup",
            "TOOL",
            at,
            5,
            "ERROR" if error else "UNSET",
            "not found" if error else "",
            **{"tool.name": "lookup", "input.value": "{}"},
        ),  # fmt: skip
    ]


class FakePhoenix:
    """Phoenix's /v1/projects, /traces and /spans with cursors; records the requests."""

    def __init__(self):
        self.projects: dict[str, list[dict]] = {}  # project -> spans
        self.requests: list[tuple[str, dict]] = []
        self.status = 200
        self.raw: bytes | None = None
        self.redirect: str | None = None
        self.short = False
        self.page_cap = 1000
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parts = urlsplit(self.path)
                query = parse_qs(parts.query)
                fake.requests.append((parts.path, query))
                if self.headers.get("Authorization") != f"Bearer {KEY}":
                    return self.reply(401, b"Invalid token")
                if fake.redirect:
                    self.send_response(307)
                    self.send_header("Location", fake.redirect)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                if fake.short:  # not HTTP: http.client raises BadStatusLine
                    self.wfile.write(b"garbage\r\n\r\n")
                    return None
                if fake.raw is not None:
                    return self.reply(200, fake.raw)
                if fake.status != 200:
                    return self.reply(fake.status, b'{"detail":"x"}')
                return self.reply(200, json.dumps(fake.answer(parts.path, query)).encode())

            def reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def page(self, rows, query):
        limit = min(int(query.get("limit", ["100"])[0]), self.page_cap)
        start = int(query.get("cursor", ["0"])[0])
        chunk = rows[start : start + limit]
        more = start + limit < len(rows)
        return {"data": chunk, "next_cursor": str(start + limit) if more else None}

    def answer(self, path, query):
        if path == "/v1/projects":
            return self.page([{"name": name} for name in self.projects], query)
        _, _, _, project, kind = path.split("/")
        project = unquote(project)
        if project not in self.projects:
            raise AssertionError("unknown project is a 404, not handled here")
        spans = self.projects[project]
        low = query.get("start_time", [None])[0]
        high = query.get("end_time", [None])[0]

        def inside(row):
            moment = datetime.fromisoformat(row["start_time"])
            return (low is None or moment >= datetime.fromisoformat(low)) and (
                high is None or moment < datetime.fromisoformat(high)
            )

        if kind == "traces":
            roots = [s for s in spans if s["parent_id"] is None and inside(s)]
            roots.sort(key=lambda s: s["start_time"], reverse=True)
            rows = [
                {"trace_id": s["context"]["trace_id"], "start_time": s["start_time"]}
                for s in roots
            ]
            return self.page(rows, query)
        rows = [s for s in spans if inside(s)]
        if "trace_id" in query:
            rows = [s for s in rows if s["context"]["trace_id"] in query["trace_id"]]
        if query.get("parent_id") == ["null"]:
            rows = [s for s in rows if s["parent_id"] is None]
        for item in query.get("attribute", []):
            key, value = item.split(":", 1)
            rows = [s for s in rows if s["attributes"].get(key) == json.loads(value)]
        rows.sort(key=lambda s: s["start_time"], reverse=True)
        return self.page(rows, query)


class NotFound(FakePhoenix):
    pass


@pytest.fixture
def phoenix():
    fake = FakePhoenix()
    yield fake
    fake.close()


def client(fake) -> Phoenix:
    return Phoenix(fake.url, KEY)


def show() -> Shown:
    from agentbreeder import redaction

    return Shown(redaction.Redactor([KEY, "hidden-value"]))


def deployment(identifier, name="support", created=T0 - timedelta(hours=1), removed=None):
    return {
        "id": identifier,
        "name": name,
        "state": "removed" if removed else "ready",
        "created": iso(created),
        "updated": iso(removed or created),
        "removed": iso(removed) if removed else None,
        "target": "local",
    }


# -- the client --------------------------------------------------------------------------------


def test_client_sends_the_bearer_and_reads_pages(phoenix):
    phoenix.projects = {"support": [], "other": []}
    phoenix.page_cap = 1
    assert client(phoenix).projects() == {"support", "other"}
    assert len(phoenix.requests) == 2  # cursor followed


@pytest.mark.parametrize(
    "status,code", [(401, "TRACES_AUTH"), (403, "TRACES_AUTH"), (500, "TRACES_PHOENIX_ERROR")]
)
def test_client_errors_are_coded(phoenix, status, code):
    phoenix.status = status
    with pytest.raises(TracesError) as error:
        client(phoenix).projects()
    assert error.value.code == code
    assert KEY not in str(error.value)


def test_wrong_key_is_auth(phoenix):
    with pytest.raises(TracesError) as error:
        Phoenix(phoenix.url, "wrong").projects()
    assert error.value.code == "TRACES_AUTH"


def test_404_is_none(phoenix):
    phoenix.status = 404
    assert client(phoenix).get("/v1/projects/x/traces") is None
    assert client(phoenix).trace_ids("x", T0, None, 5) == []


def test_redirect_is_not_followed(phoenix):
    other = FakePhoenix()
    try:
        phoenix.redirect = other.url + "/v1/projects"
        with pytest.raises(TracesError) as error:
            client(phoenix).projects()
        assert error.value.code == "TRACES_PHOENIX_ERROR"
        assert other.requests == []  # the bearer never went elsewhere
    finally:
        other.close()


@pytest.mark.parametrize("raw", [b"not json", b"[1]", b'"x"', b'{"data": 3}'])
def test_malformed_answers(phoenix, raw):
    phoenix.raw = raw
    with pytest.raises(TracesError) as error:
        client(phoenix).projects()
    assert error.value.code == "TRACES_PHOENIX_ERROR"


def test_oversized_answer(phoenix, monkeypatch):
    monkeypatch.setattr(phoenix_traces, "BODY_LIMIT", 10)
    phoenix.projects = {"support-agent-with-a-long-name": []}
    with pytest.raises(TracesError, match="too large"):
        client(phoenix).projects()


def test_unreachable():
    with pytest.raises(TracesError) as error:
        Phoenix("http://127.0.0.1:9", KEY).projects()
    assert error.value.code == "TRACES_UNREACHABLE"


def test_page_limit_marks_truncation(phoenix, monkeypatch):
    monkeypatch.setattr(phoenix_traces, "MAX_PAGES", 2)
    phoenix.page_cap = 1
    phoenix.projects = {"support": turn("1" * 32)}
    spans, partial = client(phoenix).spans("support", ["1" * 32])
    assert partial == {"1" * 32} and len(spans["1" * 32]) == 2


def test_limit_reached_with_more_is_truncation(phoenix, monkeypatch):
    monkeypatch.setattr(phoenix_traces, "MAX_SPANS", 2)
    phoenix.projects = {"support": turn("1" * 32)}
    spans, partial = client(phoenix).spans("support", ["1" * 32])
    assert partial == {"1" * 32} and len(spans["1" * 32]) == 2


def test_spans_are_batched_by_trace_id(phoenix, monkeypatch):
    monkeypatch.setattr(phoenix_traces, "BATCH", 2)
    ids = [f"{n:032x}" for n in range(5)]
    phoenix.projects = {"support": [row for t in ids for row in turn(t)]}
    spans, partial = client(phoenix).spans("support", ids)
    assert not partial and all(len(spans[t]) == 3 for t in ids)
    assert sum(1 for path, _ in phoenix.requests if path.endswith("/spans")) == 3


def test_project_names_are_quoted(phoenix):
    phoenix.status = 404
    client(phoenix).trace_ids("a b/c", T0, T0, 1)
    assert phoenix.requests[-1][0] == "/v1/projects/a%20b%2Fc/traces"


# -- parsing -----------------------------------------------------------------------------------


def test_summarize_builds_the_trace_shape():
    trace = phoenix_traces.summarize("1" * 32, "support", turn("1" * 32, error=True), show())
    assert trace["status"] == "error"
    assert trace["tokens"] == {"prompt": 100, "completion": 30, "total": 130}
    assert trace["models"] == ["gemini-2.5-flash"]
    assert trace["session_id"] == "s-1" and trace["turn_id"] == "t-1"
    assert trace["latency_ms"] == 50
    assert trace["tool_calls"] == [
        {"name": "lookup", "status": "error", "latency_ms": 5, "span_id": "3" * 16}
    ]
    assert [s["name"] for s in trace["spans"]] == [
        "agent_turn", "litellm_request", "execute_tool lookup",
    ]  # fmt: skip
    assert trace["spans"][2]["error"] == "not found"
    assert trace["deployment_id"] is None and trace["attribution"] is None
    text = json.dumps(trace)
    assert "secret msg" not in text and "input" not in text  # content never read out


def test_summarize_redacts_and_flattens_untrusted_text():
    rows = [
        span("1" * 32, "1" * 16, None, "turn hidden-value\x1b[31m‮", "AGENT", status="ERROR",
             message=f"boom {KEY}\nnext", **{"session.id": "x" * 500}),
    ]  # fmt: skip
    trace = phoenix_traces.summarize("1" * 32, "p\x07", rows, show())
    root = trace["spans"][0]
    assert "hidden-value" not in root["name"] and "\x1b" not in root["name"]
    assert "‮" not in root["name"]
    assert KEY not in root["error"] and "\n" not in root["error"]
    assert len(trace["session_id"]) == phoenix_traces.TEXT
    assert trace["project"] == "p"


def test_summarize_drops_malformed_rows():
    good = turn("1" * 32)
    bad = [
        {"context": "x"},
        {"context": {"span_id": "short"}},
        {"context": {"span_id": "4" * 16}, "attributes": "x", "span_kind": "<b>",
         "status_code": "WEIRD", "start_time": "not a time", "end_time": 3, "name": 7,
         "parent_id": "nothex", "llm.token_count.prompt": True},
    ]  # fmt: skip
    trace = phoenix_traces.summarize("1" * 32, "support", [*bad, *good], show())
    odd = next(s for s in trace["spans"] if s["span_id"] == "4" * 16)
    assert odd["kind"] == "UNKNOWN" and odd["status"] == "unset" and odd["name"] == "?"
    assert odd["parent_id"] is None and odd["latency_ms"] is None
    assert phoenix_traces.summarize("1" * 32, "support", bad[:2], show()) is None


@pytest.mark.parametrize(
    "value,expected",
    [(5, 5), (5.7, 5), (True, None), ("5", None), (-1, None), (10**13, None),
     (float("inf"), None), (float("nan"), None)],
)  # fmt: skip
def test_counts(value, expected):
    assert phoenix_traces._count(value) == expected


def test_token_attributes_are_type_checked():
    rows = [span("1" * 32, "1" * 16, None, "llm", "LLM",
                 **{"llm.token_count.prompt": "9", "llm.token_count.completion": 4,
                    "gen_ai.request.model": "m"})]  # fmt: skip
    trace = phoenix_traces.summarize("1" * 32, "p", rows, show())
    assert trace["tokens"] == {"prompt": 0, "completion": 4, "total": 4}
    assert trace["models"] == ["m"]


def test_tool_error_without_error_status_is_an_error():
    rows = [span("1" * 32, "1" * 16, None, "t", "TOOL", **{"tool.error": "nope"})]
    trace = phoenix_traces.summarize("1" * 32, "p", rows, show())
    assert trace["status"] == "error" and trace["spans"][0]["error"] == "nope"


def test_orphans_are_roots_and_spans_are_capped(monkeypatch):
    rows = [span("1" * 32, "5" * 16, "9" * 16, "orphan", at=1), *turn("1" * 32)]
    trace = phoenix_traces.summarize("1" * 32, "p", rows, show())
    assert trace["spans"][0]["name"] == "agent_turn"
    monkeypatch.setattr(phoenix_traces, "MAX_SPANS", 2)
    trace = phoenix_traces.summarize("1" * 32, "p", rows, show())
    assert trace["spans_truncated"] and len(trace["spans"]) == 2


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-11-13", datetime(2026, 11, 13, tzinfo=UTC)),
        ("2026-11-13T09:00", datetime(2026, 11, 13, 9, tzinfo=UTC)),
        ("2026-11-13T09:00+01:00", datetime(2026, 11, 13, 8, tzinfo=UTC)),
        (None, None),
    ],
)
def test_since(value, expected):
    assert phoenix_traces.since(value) == expected


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01", "x" * 100])
def test_since_invalid(value):
    with pytest.raises(TracesError) as error:
        phoenix_traces.since(value)
    assert error.value.code == "TRACES_SINCE"


# -- attribution -------------------------------------------------------------------------------


def run_for(fake, chosen, rows, last=20, after=None):
    report = phoenix_traces.Report(fake.url)
    phoenix = client(fake)
    known = phoenix.projects()
    for row in chosen:
        phoenix_traces.for_deployment(phoenix, row, rows, known, show(), last, after, report)
    return report


def test_exact_attribution_ignores_other_deployments(phoenix):
    phoenix.projects = {
        "support": [*turn("1" * 32, 1, D1), *turn("2" * 32, 2, D2), *turn("3" * 32, 3)]
    }
    rows = [deployment(D1), deployment(D2)]
    report = run_for(phoenix, [rows[0]], rows)
    assert [(t["trace_id"], t["attribution"]) for t in report.traces] == [("1" * 32, "exact")]
    assert report.overlapping == []
    assert all(t["deployment_id"] == D1 for t in report.traces)


def test_window_attribution_lists_untagged_overlapping_deployments(phoenix):
    phoenix.projects = {
        "support": [*turn("1" * 32, 1, D1), *turn("2" * 32, 2), *turn("3" * 32, 3)]
    }
    rows = [deployment(D1), deployment(D2), deployment(D3, name="support-candidate")]
    report = run_for(phoenix, [rows[1]], rows)
    # D1's traces carry its ID, so they are left out, and D1 is not ambiguous; D3 is.
    assert [t["trace_id"] for t in report.traces] == ["3" * 32, "2" * 32]
    assert {t["attribution"] for t in report.traces} == {"window"}
    assert report.overlapping == [D3]


def test_window_respects_lifetime_and_since(phoenix):
    phoenix.projects = {
        "support": [*turn("1" * 32, 1), *turn("2" * 32, 100), *turn("3" * 32, 200)]
    }
    removed = deployment(D1, removed=T0 + timedelta(seconds=150))
    report = run_for(phoenix, [removed], [removed])
    assert [t["trace_id"] for t in report.traces] == ["2" * 32, "1" * 32]
    report = run_for(phoenix, [removed], [removed], after=T0 + timedelta(seconds=50))
    assert [t["trace_id"] for t in report.traces] == ["2" * 32]
    report = run_for(phoenix, [removed], [removed], after=T0 + timedelta(seconds=300))
    assert report.traces == []
    report = run_for(phoenix, [removed], [removed], last=1)
    assert [t["trace_id"] for t in report.traces] == ["2" * 32] and not report.truncated


def test_removed_without_event_uses_updated():
    row = deployment(D1, removed=T0)
    row["removed"] = None
    assert phoenix_traces._lifetime(row)[1] == T0


def test_candidate_shares_the_agent_project(phoenix):
    phoenix.projects = {"support": turn("1" * 32, 1, D2)}
    row = deployment(D2, name="support-candidate")
    report = run_for(phoenix, [row], [row])
    assert report.traces[0]["project"] == "support"


def test_no_project_is_a_note(phoenix):
    phoenix.projects = {"other": []}
    row = deployment(D1)
    report = run_for(phoenix, [row], [row])
    assert report.traces == [] and "no Phoenix project support" in report.notes[0]
    assert phoenix_traces._project_for("x-candidate", {"y"}) is None


def test_one_trace_window_and_ambiguity(phoenix):
    phoenix.projects = {"support": [*turn("1" * 32, 1), *turn("2" * 32, 2, D1)]}
    rows = [deployment(D1), deployment(D2)]
    report = phoenix_traces.Report(phoenix.url)
    phoenix_traces.one_trace(client(phoenix), "1" * 32, ["support"], rows, show(), report)
    trace = report.traces[0]
    assert (trace["deployment_id"], trace["attribution"]) == (D2, "window")
    report = phoenix_traces.Report(phoenix.url)
    phoenix_traces.one_trace(client(phoenix), "2" * 32, ["support"], rows, show(), report)
    assert (report.traces[0]["deployment_id"], report.traces[0]["attribution"]) == (D1, "exact")
    rows.append(deployment(D3))
    report = phoenix_traces.Report(phoenix.url)
    phoenix_traces.one_trace(client(phoenix), "1" * 32, ["support"], rows, show(), report)
    assert report.traces[0]["deployment_id"] is None and report.overlapping == [D2, D3]


def test_one_trace_not_found(phoenix):
    phoenix.projects = {"a": [], "b": turn("1" * 32)}
    report = phoenix_traces.Report(phoenix.url)
    phoenix_traces.one_trace(client(phoenix), "1" * 32, ["a", "b"], [], show(), report)
    assert report.traces[0]["project"] == "b"
    with pytest.raises(TracesError) as error:
        phoenix_traces.one_trace(client(phoenix), "9" * 32, ["a", "b"], [], show(), report)
    assert error.value.code == "TRACES_NOT_FOUND"


# -- locate --------------------------------------------------------------------------------------


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """A stack state as `services up` leaves it, and a stack.yaml with a phoenix service."""
    from agentbreeder.services import engine
    from agentbreeder.services.state import StackState

    project = tmp_path / "project"
    project.mkdir()
    root = tmp_path / "stacks"
    monkeypatch.setenv("AGENTBREEDER_STACKS_DIR", str(root))
    services = {"phoenix": SimpleNamespace(ref="service/phoenix@1.0.0")}
    monkeypatch.setattr(engine, "load", lambda path: SimpleNamespace(services=services))
    state = StackState("demo", project, root)
    state.record_port("phoenix", "http", 6006)
    state.write_secret("phoenix", "ingest-key", f"{KEY}\n".encode(), "bootstrap")
    (state.directory / "envs" / "dev" / "state").mkdir(parents=True)
    state.close()
    return SimpleNamespace(
        project=project,
        state_dir=state.directory / "envs" / "dev" / "state",
        directory=state.directory,
        services=services,
    )


def test_locate_reads_port_and_key(stack):
    found = phoenix_traces.locate(stack.project, stack.state_dir)
    assert found.url == "http://127.0.0.1:6006" and found.key == KEY
    assert KEY not in repr(found)


def test_locate_needs_a_stack(stack, tmp_path):
    for project, state_dir in ((None, stack.state_dir), (stack.project, tmp_path)):
        with pytest.raises(TracesError) as error:
            phoenix_traces.locate(project, state_dir)
        assert error.value.code == "TRACES_NO_PHOENIX"


@pytest.mark.parametrize("names", [[], ["p1", "p2"]])
def test_locate_needs_exactly_one_phoenix(stack, names):
    stack.services.clear()
    stack.services.update({n: SimpleNamespace(ref="service/phoenix@1.0.0") for n in names})
    stack.services["db"] = SimpleNamespace(ref="service/postgres-pgvector@1.2.0")
    with pytest.raises(TracesError, match="service/phoenix"):
        phoenix_traces.locate(stack.project, stack.state_dir)


def test_locate_without_port_or_key(stack):
    from agentbreeder.services.state import StackState

    stack.services["phoenix2"] = stack.services.pop("phoenix")
    with pytest.raises(TracesError, match="no host port"):
        phoenix_traces.locate(stack.project, stack.state_dir)
    state = StackState.open_directory(stack.directory)
    state.close()
    stack.services["phoenix"] = stack.services.pop("phoenix2")
    (stack.directory / "service-secrets" / "phoenix" / "ingest-key").unlink()
    with pytest.raises(TracesError, match="no ingest key"):
        phoenix_traces.locate(stack.project, stack.state_dir)


@pytest.mark.parametrize("value", [b"\xff\xfe", b"two words", b"\n"])
def test_locate_malformed_key(stack, value):
    path = stack.directory / "service-secrets" / "phoenix" / "ingest-key"
    path.write_bytes(value)
    with pytest.raises(TracesError) as error:
        phoenix_traces.locate(stack.project, stack.state_dir)
    assert error.value.code == "TRACES_NO_PHOENIX"


def test_locate_refuses_an_unsafe_key_file(stack):
    path = stack.directory / "service-secrets" / "phoenix" / "ingest-key"
    path.chmod(0o644)
    with pytest.raises(TracesError) as error:
        phoenix_traces.locate(stack.project, stack.state_dir)
    assert error.value.code == "STATE_UNSAFE"


def test_locate_never_brought_up(stack, monkeypatch):
    from agentbreeder.services.state import StackState

    monkeypatch.setattr(StackState, "open_directory", classmethod(lambda cls, path: None))
    with pytest.raises(TracesError, match="never brought up"):
        phoenix_traces.locate(stack.project, stack.state_dir)


# -- the command ---------------------------------------------------------------------------------


@pytest.fixture
def wired(phoenix, monkeypatch, tmp_path):
    """The command with Phoenix located at the fake and a deployment state."""
    from agentbreeder.store import Store

    state_dir = tmp_path / "state"
    store = Store(state_dir)
    store.close()
    rows = [deployment(D1), deployment(D2, removed=T0 + timedelta(hours=1))]
    monkeypatch.setattr(phoenix_traces, "locate", lambda project, state: Phoenix(phoenix.url, KEY))
    monkeypatch.setattr(command, "_state", lambda path: (rows, [b"hidden-value"]))
    return SimpleNamespace(state_dir=state_dir, rows=rows)


def run(args, capsys):
    code = cli.main(["traces", *args])
    out, err = capsys.readouterr()
    return code, out, err


def test_list_json(phoenix, wired, capsys):
    phoenix.projects = {"support": [*turn("1" * 32, 1, D1), *turn("2" * 32, 2, D2)]}
    code, out, err = run(["--state-dir", str(wired.state_dir), "--json"], capsys)
    assert code == 0, err
    data = json.loads(out)
    assert data["schema_version"] == 1 and data["phoenix"] == phoenix.url
    assert [t["trace_id"] for t in data["traces"]] == ["1" * 32]  # D2 is removed
    assert KEY not in out + err


def test_by_id_text(phoenix, wired, capsys):
    phoenix.projects = {"support": [*turn("2" * 32, 2, D2), *turn("3" * 32, 3)]}
    code, out, err = run([D2, "--state-dir", str(wired.state_dir)], capsys)
    assert code == 0, err
    assert f"Deployment {D2} (exact), project support:" in out
    assert "tokens 100/30" in out and "tools: lookup" in out


def test_window_text_warns(phoenix, wired, capsys):
    phoenix.projects = {"support": turn("3" * 32, 3, error=True)}
    code, out, err = run([D1, "--state-dir", str(wired.state_dir)], capsys)
    assert code == 0
    assert "(window)" in out and "tools: lookup(error)" in out
    assert "may belong to any of these deployments" in err and D2 in err


def test_trace_tree(phoenix, wired, capsys):
    phoenix.projects = {"support": turn("1" * 32, 1, D1, error=True)}
    code, out, err = run(["--trace", "1" * 32, "--state-dir", str(wired.state_dir)], capsys)
    assert code == 0, err
    lines = out.splitlines()
    assert lines[2] == f"  project support, deployment {D1} (exact), session s-1, turn t-1"
    assert lines[3].startswith("  agent_turn [AGENT] 50 ms")
    assert lines[4] == "    litellm_request [LLM, gemini-2.5-flash, 100/30 tokens] 20 ms"
    assert lines[5] == "    execute_tool lookup [TOOL] 5 ms ERROR: not found"


def test_trace_with_project(phoenix, wired, capsys, monkeypatch):
    phoenix.projects = {"elsewhere": turn("1" * 32, 1)}
    monkeypatch.setattr(phoenix_traces, "MAX_SPANS", 1)
    code, out, _ = run(
        ["--trace", "1" * 32, "--project", "elsewhere", "--state-dir", str(wired.state_dir)],
        capsys,
    )
    assert code == 0 and "only the first 1 spans" in out


def test_empty(phoenix, wired, capsys):
    phoenix.projects = {}
    code, out, err = run(["--state-dir", str(wired.state_dir)], capsys)
    assert code == 0 and "No traces found." in out and "no Phoenix project" in err


def test_truncated_warns(phoenix, wired, capsys, monkeypatch):
    monkeypatch.setattr(phoenix_traces, "MAX_PAGES", 1)
    phoenix.page_cap = 1
    phoenix.projects = {"support": turn("1" * 32, 1, D1)}
    code, _, err = run([D1, "--state-dir", str(wired.state_dir)], capsys)
    assert code == 0 and "the list is partial" in err


@pytest.mark.parametrize(
    "args,code",
    [
        ([D1, "--trace", "1" * 32], "USAGE"),
        (["nothex"], "ID_INVALID"),
        (["--trace", "X" * 32], "TRACE_ID_INVALID"),
        (["--project", "p"], "USAGE"),
        (["--trace", "1" * 32, "--project", "a/b"], "PROJECT_INVALID"),
        (["--since", "soon"], "TRACES_SINCE"),
        (["d" * 32], "NOT_FOUND"),
    ],
)
def test_usage_errors(phoenix, wired, capsys, args, code):
    exit_code, out, err = run([*args, "--state-dir", str(wired.state_dir), "--json"], capsys)
    assert exit_code == 2
    assert err.startswith(f"[{code}] ")
    assert json.loads(out)["error"]["code"] == code


def test_phoenix_down_is_coded(wired, capsys, monkeypatch):
    monkeypatch.setattr(
        phoenix_traces, "locate", lambda project, state: Phoenix("http://127.0.0.1:9", KEY)
    )
    code, _, err = run(["--state-dir", str(wired.state_dir)], capsys)
    assert code == 2 and err.startswith("[TRACES_UNREACHABLE]")


def test_state_errors_are_coded(wired, capsys, monkeypatch):
    def broken(path):
        raise OSError("x")

    monkeypatch.setattr(command, "_state", broken)
    code, _, err = run(["--state-dir", str(wired.state_dir)], capsys)
    assert code == 2 and err.startswith("[STATE_UNREADABLE]")


def test_last_bounds(capsys):
    with pytest.raises(SystemExit):
        cli.main(["traces", "--last", "0"])
    with pytest.raises(SystemExit):
        cli.main(["traces", "--last", "201"])
    with pytest.raises(SystemExit):
        cli.main(["traces", "--environment", "dev"])  # needs --stack


def test_without_stack_is_coded(tmp_path, capsys):
    code, _, err = run(["--state-dir", str(tmp_path)], capsys)
    assert code == 2 and err.startswith("[TRACES_NO_PHOENIX]")


# -- state ---------------------------------------------------------------------------------------


def test_state_reads_lifetimes_and_redaction_values(tmp_path):
    from agentbreeder.store import Store

    assert command._state(tmp_path / "none") == ([], [])
    store = Store(tmp_path)
    store.db.execute(
        "INSERT INTO deployments (id, name, digest, manifest, context, container, state, created,"
        " updated) VALUES (?, 'support', 'd', '{\"target\": \"kubernetes\"}', '{}', 'c',"
        " 'removed', '2026-11-13T00:00:00+00:00', '2026-11-13T02:00:00+00:00')",
        (D1,),
    )
    store.db.execute(
        "INSERT INTO events (deployment_id, state, timestamp) VALUES "
        "(?, 'removed', '2026-11-13T01:00:00+00:00'), (?, 'removed', '2026-11-13T03:00:00+00:00')",
        (D1, D1),
    )
    store.db.commit()
    store.close()
    rows, values = command._state(tmp_path)
    assert rows == [
        {
            "id": D1, "name": "support", "state": "removed",
            "created": "2026-11-13T00:00:00+00:00", "updated": "2026-11-13T02:00:00+00:00",
            "target": "kubernetes", "removed": "2026-11-13T01:00:00+00:00",
        }
    ]  # fmt: skip
    assert isinstance(values, list)


def test_state_fails_closed_on_redaction_problems(tmp_path, monkeypatch):
    from agentbreeder import redaction

    found = redaction.Collected()
    found.problems.append("cannot read x")
    monkeypatch.setattr(redaction, "collect", lambda *a, **k: found)
    with pytest.raises(TracesError) as error:
        command._state(tmp_path)
    assert error.value.code == "REDACTION_INCOMPLETE"


@pytest.mark.parametrize(
    "error,code",
    [("StateNeedsMigration", "STATE_NEEDS_MIGRATION"), ("StateBusy", "STATE_BUSY"),
     ("DeploymentError", "STATE_UNREADABLE")],
)  # fmt: skip
def test_state_open_errors(tmp_path, monkeypatch, error, code):
    from agentbreeder import store

    (tmp_path / "deployments.sqlite3").write_bytes(b"")

    def refuse(path):
        raise getattr(store, error)("refused")

    monkeypatch.setattr(store.Store, "open_readonly", refuse)
    with pytest.raises(TracesError) as raised:
        command._state(tmp_path)
    assert raised.value.code == code


def test_module_paths_exist():
    assert Path(phoenix_traces.__file__).is_file()


# -- review round 1 ------------------------------------------------------------------------------


def test_no_proxy_is_used(monkeypatch):
    from urllib.request import ProxyHandler

    handlers = [h for h in phoenix_traces._OPENER.handlers if isinstance(h, ProxyHandler)]
    assert all(h.proxies == {} for h in handlers)  # build_opener drops an empty one


def test_proxy_environment_is_ignored(phoenix, monkeypatch):
    other = FakePhoenix()
    try:
        monkeypatch.setenv("HTTP_PROXY", other.url)
        monkeypatch.setenv("http_proxy", other.url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        phoenix.projects = {"support": []}
        assert client(phoenix).projects() == {"support"}
        assert other.requests == []
    finally:
        other.close()


def test_not_http_is_unreachable(phoenix):
    phoenix.short = True
    with pytest.raises(TracesError) as error:
        client(phoenix).projects()
    assert error.value.code == "TRACES_UNREACHABLE"


def test_unhashable_trace_ids_are_skipped(phoenix):
    phoenix.raw = json.dumps(
        {
            "data": [{"context": {"trace_id": []}}, {"context": {"trace_id": {}}}],
            "next_cursor": None,
        }
    ).encode()
    spans, partial = client(phoenix).spans("support", ["1" * 32])
    assert spans == {"1" * 32: []} and not partial


def test_long_cursor_is_refused(phoenix):
    phoenix.raw = json.dumps({"data": [], "next_cursor": "x" * 600}).encode()
    with pytest.raises(TracesError, match="cursor"):
        client(phoenix).projects()


def test_call_budget(phoenix, monkeypatch):
    monkeypatch.setattr(phoenix_traces, "MAX_CALLS", 2)
    phoenix.page_cap = 1
    phoenix.projects = {"a": [], "b": [], "c": []}
    with pytest.raises(TracesError) as error:
        client(phoenix).projects()
    assert error.value.code == "TRACES_LIMIT"
    assert len(phoenix.requests) == 2


def test_projects_with_surrogates_are_invalid():
    assert not phoenix_traces.valid_project("a\udcff")
    assert phoenix_traces.valid_project("northwind-support")


def test_non_ascii_kind_is_unknown():
    rows = [span("1" * 32, "1" * 16, None, "x", "\u00c4GENT")]
    assert phoenix_traces.summarize("1" * 32, "p", rows, show())["spans"][0]["kind"] == "UNKNOWN"


def test_tool_error_on_any_span_is_an_error_in_text(phoenix, wired, capsys):
    rows = [span("1" * 32, "1" * 16, None, "turn", "AGENT", **{"tool.error": "nope",
             phoenix_traces.ATTRIBUTE: D1})]  # fmt: skip
    phoenix.projects = {"support": rows}
    code, out, _ = run(["--trace", "1" * 32, "--state-dir", str(wired.state_dir)], capsys)
    assert code == 0 and "turn [AGENT] 10 ms ERROR: nope" in out


def test_pages_boundary(phoenix):
    phoenix.projects = {"support": turn("1" * 32)}
    path = "/v1/projects/support/spans"
    rows, more = client(phoenix)._pages(path, [], 3)
    assert len(rows) == 3 and not more
    rows, more = client(phoenix)._pages(path, [], 2)
    assert len(rows) == 2 and more


def test_queries_sent_to_phoenix(phoenix):
    phoenix.projects = {"support": turn("1" * 32, 1)}
    run_for(phoenix, [deployment(D1)], [deployment(D1)], last=5)
    attributed, listing = (
        q
        for path, q in phoenix.requests
        if path.endswith(("/spans", "/traces")) and "trace_id" not in q
    )
    assert attributed["parent_id"] == ["null"] and attributed["sort"] == ["start_time"]
    assert attributed["order"] == ["desc"] and attributed["limit"] == ["5"]
    assert attributed["attribute"] == [f'{phoenix_traces.ATTRIBUTE}:"{D1}"']
    assert listing["sort"] == ["start_time"] and listing["order"] == ["desc"]
    assert listing["limit"] == [str(5 * phoenix_traces.WINDOW_FACTOR)]
    assert "end_time" not in listing  # still running


@pytest.mark.parametrize(
    "state,end",
    [("failed", T0), ("disabled", T0), ("ready", None), ("removing", None),
     ("pending_approval", T0 - timedelta(hours=1))],
)  # fmt: skip
def test_lifetimes_by_state(state, end):
    row = {**deployment(D1), "state": state, "updated": iso(T0)}
    assert phoenix_traces._lifetime(row) == (T0 - timedelta(hours=1), end)


def test_failed_and_pending_deployments_take_no_later_traces(phoenix):
    phoenix.projects = {"support": [*turn("1" * 32, -10), *turn("2" * 32, 10)]}
    failed = {**deployment(D2), "state": "failed", "updated": iso(T0)}
    pending = {**deployment(D3), "state": "pending_approval"}
    live = deployment(D1)
    report = run_for(phoenix, [failed], [live, failed, pending])
    assert [t["trace_id"] for t in report.traces] == ["1" * 32]
    assert report.overlapping == [D1]  # the pending one never ran
    report = run_for(phoenix, [pending], [live, failed, pending])
    assert report.traces == [] and report.overlapping == []
    report = phoenix_traces.Report(phoenix.url)
    phoenix_traces.one_trace(client(phoenix), "2" * 32, ["support"], [live, failed, pending],
                             show(), report)  # fmt: skip
    assert report.traces[0]["deployment_id"] == D1 and report.overlapping == []


def test_listing_skips_failed_and_pending(phoenix, wired, capsys):
    wired.rows[:] = [
        deployment(D1),
        {**deployment(D2), "state": "failed"},
        {**deployment(D3), "state": "pending_approval"},
    ]
    phoenix.projects = {"support": turn("1" * 32, 1)}
    code, out, _ = run(["--state-dir", str(wired.state_dir), "--json"], capsys)
    assert code == 0
    assert {t["deployment_id"] for t in json.loads(out)["traces"]} == {D1}


def test_no_overlap_warning_without_window_traces(phoenix):
    phoenix.projects = {"support": turn("1" * 32, 1, D1)}
    rows = [deployment(D1), deployment(D2)]
    report = run_for(phoenix, [rows[0]], rows, after=T0 + timedelta(seconds=30))
    assert report.traces == [] and report.overlapping == []


def test_window_over_fetches_past_tagged_traces(phoenix):
    tagged = [row for n in range(2) for row in turn(f"{n + 1:032x}", 10 + n, D1)]
    phoenix.projects = {"support": [*tagged, *turn("f" * 32, 1)]}
    rows = [deployment(D1), deployment(D2)]
    report = run_for(phoenix, [rows[1]], rows, last=1)
    assert [t["trace_id"] for t in report.traces] == ["f" * 32]


def test_tags_are_cached(phoenix):
    phoenix.projects = {"support": [*turn("1" * 32, 1), *turn("2" * 32, 2, D3)]}
    rows = [deployment(D1), deployment(D2), deployment(D3)]
    phoenix_client = client(phoenix)
    known = phoenix_client.projects()
    report = phoenix_traces.Report(phoenix.url)
    for row in rows[:2]:
        phoenix_traces.for_deployment(phoenix_client, row, rows, known, show(), 5, None, report)
    attributed = [q for _, q in phoenix.requests if "attribute" in q]
    assert len(attributed) == len({q["attribute"][0] for q in attributed}) == 3
    assert report.overlapping == [D2, D1]


def test_overlaps_ignores_disjoint_and_empty_lifetimes():
    early = (T0 - timedelta(hours=2), T0 - timedelta(hours=1))
    late = (T0, None)
    assert not phoenix_traces._overlaps(early, late)
    assert phoenix_traces._overlaps((T0 - timedelta(hours=2), T0 + timedelta(seconds=1)), late)
    assert not phoenix_traces._overlaps((T0, T0), late)


def test_notes_are_in_json(phoenix, wired, capsys):
    phoenix.projects = {}
    code, out, _ = run(["--state-dir", str(wired.state_dir), "--json"], capsys)
    assert code == 0 and "no Phoenix project support" in json.loads(out)["notes"][0]


def test_no_stack_comes_before_state_errors(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(command, "_state", lambda path: (_ for _ in ()).throw(OSError("x")))
    code, _, err = run(["--state-dir", str(tmp_path)], capsys)
    assert code == 2 and err.startswith("[TRACES_NO_PHOENIX]")


# -- review round 2 ------------------------------------------------------------------------------


def test_repeated_span_ids_keep_the_first():
    rows = [
        span("1" * 32, "1" * 16, None, "first", "AGENT"),
        span("1" * 32, "1" * 16, "1" * 16, "again", "AGENT", at=1),
    ]
    trace = phoenix_traces.summarize("1" * 32, "p", rows, show())
    assert [s["name"] for s in trace["spans"]] == ["first"]


def test_tree_survives_cycles():
    def item(span_id, parent, name):
        return {
            "span_id": span_id,
            "parent_id": parent,
            "name": name,
            "kind": "CHAIN",
            "model": None,
            "tokens": None,
            "latency_ms": 1,
            "status": "unset",
            "error": None,
        }

    trace = {"spans": [item("1" * 16, "2" * 16, "a"), item("2" * 16, "1" * 16, "b"),
                       item("3" * 16, "3" * 16, "self")]}  # fmt: skip
    lines = command._tree(trace)
    assert sorted(line.strip().split()[0] for line in lines) == ["a", "b", "self"]
    assert lines[0] == "  a [CHAIN] 1 ms" and lines[1] == "    b [CHAIN] 1 ms"


def test_tagging_deployment_with_since_never_falls_back(phoenix):
    phoenix.projects = {"support": [*turn("1" * 32, 1, D1), *turn("2" * 32, 100)]}
    rows = [deployment(D1), deployment(D2)]
    report = run_for(phoenix, [rows[0]], rows, after=T0 + timedelta(seconds=50))
    assert report.traces == [] and report.overlapping == []


def test_notes_for_never_ran_and_stopped(phoenix):
    phoenix.projects = {"support": []}
    pending = {**deployment(D1), "state": "pending_approval"}
    failed = {**deployment(D2), "state": "failed", "updated": iso(T0)}
    report = run_for(phoenix, [pending, failed], [pending, failed], after=T0 + timedelta(hours=1))
    assert report.notes == [
        f"{D1}: never ran (awaiting approval)",
        f"{D2}: stopped before --since",
    ]


def test_deadline(phoenix, monkeypatch):
    monkeypatch.setattr(phoenix_traces, "DEADLINE", -1)
    with pytest.raises(TracesError, match="took more than"):
        client(phoenix).projects()


def test_failed_at_creation_is_not_awaiting_approval(phoenix):
    phoenix.projects = {"support": []}
    row = {**deployment(D1), "state": "failed"}
    row["updated"] = row["created"]
    report = run_for(phoenix, [row], [row])
    assert report.notes == [f"{D1}: stopped before --since"]
