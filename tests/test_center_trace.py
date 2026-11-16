"""Try it's trace panel (#907, console half): a turn's span tree through
`traces --trace ID --stack DIR --environment ENV --json`, uncached, with only the allow-listed
fields of its answer passed on (docs/traces.md; no message content)."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)

from agentbreeder import phoenix_traces
from agentbreeder.center import server, tryit

TRACE = "4053" + "a" * 28
SPANS = [
    {
        "span_id": "a623a1e273310546",
        "parent_id": None,
        "name": "agent_turn",
        "kind": "AGENT",
        "status": "unset",
        "start_time": "2026-11-14T03:10:33.358423+00:00",
        "latency_ms": 41,
        "model": None,
        "tokens": None,
        "tool": None,
        "error": None,
    },
    {
        "span_id": "b1eaa89e532f0dc9",
        "parent_id": "a623a1e273310546",
        "name": "lookup_order",
        "kind": "TOOL",
        "status": "error",
        "start_time": "2026-11-14T03:10:33.360000+00:00",
        "latency_ms": 11,
        "model": None,
        "tokens": None,
        "tool": "lookup_order",
        "error": "order not found",
    },
    {
        "span_id": "c1eaa89e532f0dc9",
        "parent_id": "a623a1e273310546",
        "name": "litellm.completion",
        "kind": "LLM",
        "status": "ok",
        "start_time": "2026-11-14T03:10:33.370000+00:00",
        "latency_ms": 20,
        "model": "gemini-2.5-flash",
        "tokens": {"prompt": 321, "completion": 70},
        "tool": None,
        "error": None,
    },
]
ANSWER = {
    "schema_version": 1,
    "phoenix": "http://127.0.0.1:57340",
    "traces": [
        {
            "trace_id": TRACE,
            "project": "support",
            "deployment_id": "18bc2abed83946b6a52c045e973d1844",
            "attribution": "exact",
            "start_time": "2026-11-14T03:10:33.358423+00:00",
            "end_time": "2026-11-14T03:10:33.399734+00:00",
            "latency_ms": 41,
            "status": "error",
            "session_id": "s-1",
            "turn_id": "turn-1",
            "models": ["gemini-2.5-flash"],
            "tokens": {"prompt": 321, "completion": 70, "total": 391},
            "tool_calls": [
                {
                    "name": "lookup_order",
                    "status": "error",
                    "latency_ms": 11,
                    "span_id": "b1eaa89e532f0dc9",
                }
            ],
            "spans": SPANS,
            "spans_truncated": False,
        }
    ],
    "overlapping": [],
    "truncated": False,
    "notes": [],
}

# `traces` only: the argv is recorded; `traces.json` and `traces-exit` set the answer.
FAKE_TRACES = textwrap.dedent(
    """
    import json, os, sys
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[0] == "traces":
        open(marker + "/traces-argv", "a").write(json.dumps(argv) + "\\n")
        if os.path.exists(marker + "/traces-stderr"):
            sys.stderr.write(open(marker + "/traces-stderr").read())
            sys.exit(1)
        print(open(marker + "/traces.json").read())
        code = marker + "/traces-exit"
        sys.exit(int(open(code).read()) if os.path.exists(code) else 0)
    elif argv[0] == "doctor":
        print("[]")
    else:
        print("ok")
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_cli.py"
    path.write_text(FAKE_TRACES)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    (tmp_path / "traces.json").write_text(json.dumps(ANSWER))
    return [sys.executable, str(path)]


def read(center, project, cookie, trace=TRACE):  # noqa: F811
    return api(center, "GET", f"/api/projects/{project['id']}/traces/{trace}", cookie=cookie)


def answer(tmp_path, value, code=0):
    (tmp_path / "traces.json").write_text(json.dumps(value))
    (tmp_path / "traces-exit").write_text(str(code))


def calls(tmp_path) -> list[list[str]]:
    path = tmp_path / "traces-argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_a_trace_is_read_with_a_fixed_argv(center, project, cookie, tmp_path):  # noqa: F811
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["error"] is None
    assert calls(tmp_path) == [
        ["traces", "--trace", TRACE, "--stack", project["path"], "--environment", "dev", "--json"]
    ]
    trace = data["data"]
    assert trace["trace_id"] == TRACE
    assert trace["attribution"] == "exact" and trace["status"] == "error"
    assert trace["models"] == ["gemini-2.5-flash"]
    assert trace["tokens"] == {"prompt": 321, "completion": 70, "total": 391}
    assert trace["tool_calls"] == [{"name": "lookup_order", "status": "error", "latency_ms": 11}]
    assert [s["name"] for s in trace["spans"]] == [
        "agent_turn",
        "lookup_order",
        "litellm.completion",
    ]
    assert trace["spans"][1]["error"] == "order not found"
    assert trace["spans"][2]["tokens"] == {"prompt": 321, "completion": 70}
    assert trace["truncated"] is False and trace["notes"] == []


def test_the_answer_is_never_cached(center, project, cookie, tmp_path):  # noqa: F811
    answer(
        tmp_path, {"schema_version": 1, "error": {"code": "TRACES_NOT_FOUND", "message": "x"}}, 2
    )
    _, first, _ = read(center, project, cookie)
    assert first["error"]["code"] == "TRACES_NOT_FOUND" and first["data"] is None
    answer(tmp_path, ANSWER)
    _, second, _ = read(center, project, cookie)
    assert second["error"] is None and second["data"]["trace_id"] == TRACE
    assert len(calls(tmp_path)) == 2


def test_only_allow_listed_fields_reach_the_browser(center, project, cookie, tmp_path):  # noqa: F811
    hostile = json.loads(json.dumps(ANSWER))
    found = hostile["traces"][0]
    found["input.value"] = "customer message"
    found["spans"][0]["attributes"] = {"input.value": "customer message"}
    found["spans"][0]["output"] = "agent reply"
    found["tool_calls"][0]["arguments"] = {"order": "1001"}
    hostile["extra"] = "x"
    answer(tmp_path, hostile)
    _, data, _ = read(center, project, cookie)
    text = json.dumps(data)
    assert "customer message" not in text and "agent reply" not in text and "1001" not in text
    # The Phoenix origin, the session and the turn ids stay on the server.
    assert "57340" not in text and "s-1" not in text and "turn-1" not in text
    assert set(data["data"]) == {
        "trace_id", "deployment_id", "attribution", "latency_ms", "status", "models", "tokens",
        "tool_calls", "spans", "truncated", "notes", "incomplete", "fallbacks", "served_by",
        "cached_prompt_tokens", "unknown_tools", "guardrail",
    }  # fmt: skip
    assert set(data["data"]["spans"][0]) == {
        "span_id", "parent_id", "name", "kind", "status", "latency_ms", "model", "tokens", "tool",
        "error", "tokens_counted",
    }  # fmt: skip


def test_the_root_spans_facts_are_passed_on(center, project, cookie, tmp_path):  # noqa: F811
    """runtime/adk@1.3.0 and 1.4.0 (#1062): `traces --json`'s root-span facts, as docs/traces.md
    has them; older runtimes send null or empty."""
    _, data, _ = read(center, project, cookie)
    assert {k: data["data"][k] for k in tryit.route_facts({}, None)} == {
        "fallbacks": None,
        "served_by": [],
        "cached_prompt_tokens": None,
        "unknown_tools": [],
        "guardrail": None,
    }
    given = json.loads(json.dumps(ANSWER))
    given["traces"][0] |= {
        "fallbacks": 1,
        "served_by": ["chat-backup"],
        "cached_prompt_tokens": 300,
        "unknown_tools": ["delete_everything"],
        "guardrail": {"action": "blocked", "name": "presidio-pii", "entity": "US_SSN"},
    }
    answer(tmp_path, given)
    trace = read(center, project, cookie)[1]["data"]
    assert trace["fallbacks"] == 1 and trace["served_by"] == ["chat-backup"]
    assert trace["cached_prompt_tokens"] == 300 and trace["unknown_tools"] == ["delete_everything"]
    assert trace["guardrail"] == {"action": "blocked", "name": "presidio-pii", "entity": "US_SSN"}


@pytest.mark.parametrize(
    ("guardrail", "shown"),
    [
        (None, None),
        ({"action": "allowed", "name": "g", "entity": "X"}, None),
        ("blocked", None),
        (
            {"action": "blocked", "name": "bad name!", "entity": "an ssn: 123-45-6789"},
            {"action": "blocked", "name": None, "entity": None},
        ),
        (
            {"action": "blocked", "matched": "123-45-6789", "name": "g"},
            {"action": "blocked", "name": "g", "entity": None},
        ),
    ],
)
def test_a_guardrail_is_only_its_name_and_entity_type(guardrail, shown):
    assert tryit.route_facts({"guardrail": guardrail}, None)["guardrail"] == shown


def test_cached_tokens_above_the_prompt_tokens_are_dropped():
    assert tryit.route_facts({"cached_prompt_tokens": 400}, 321)["cached_prompt_tokens"] is None
    assert tryit.route_facts({"cached_prompt_tokens": 321}, 321)["cached_prompt_tokens"] == 321
    assert tryit.route_facts({"cached_prompt_tokens": -1}, None)["cached_prompt_tokens"] is None


@pytest.mark.parametrize(
    "value,code",
    [
        ({"schema_version": 1, "error": {"code": "TRACES_AUTH", "message": "no"}}, "TRACES_AUTH"),
        ({"schema_version": 1, "error": {"code": "bad code", "message": "no"}}, "TRACES_FAILED"),
        ({"schema_version": 1, "traces": []}, "TRACES_NOT_FOUND"),
        ({"schema_version": 1, "traces": [{"trace_id": "b" * 32}]}, "TRACES_NOT_FOUND"),
        ({"schema_version": 1, "traces": "x"}, "TRACE_INVALID"),
        ([1, 2], "TRACE_INVALID"),
    ],
)  # fmt: skip
def test_failures_are_coded(center, project, cookie, tmp_path, value, code):  # noqa: F811
    answer(tmp_path, value, 2 if isinstance(value, dict) and "error" in value else 0)
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["data"] is None and data["error"]["code"] == code


def test_a_cli_failure_without_json_is_reported(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "traces-stderr").write_text("[TRACES_UNREACHABLE] Phoenix is not running\n")
    _, data, _ = read(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == "TRACES_UNREACHABLE"


@pytest.mark.parametrize("trace", ["A" * 32, "a" * 31, "a" * 33, "g" * 32, "-" + "a" * 31])
def test_trace_ids_outside_the_rule_never_reach_the_cli(center, project, cookie, tmp_path, trace):  # noqa: F811
    status, _, _ = read(center, project, cookie, trace)
    assert status == 404
    assert calls(tmp_path) == []


def test_trace_argv_checks_the_id_itself():
    with pytest.raises(Exception, match="32 lowercase hex"):
        tryit.trace_argv({"path": "/p", "environment": "dev"}, "A" * 32)


def test_bad_values_are_dropped_and_limits_hold():
    spans = [
        {"span_id": "nothex", "name": "dropped"},
        {"span_id": "d" * 16, "parent_id": "zz", "name": "x" * 500, "kind": "agent", "status": "?",
         "latency_ms": True, "tokens": {"prompt": -1}, "error": "e" * 900},
        {"span_id": "d" * 16, "name": "a repeated id: the first wins"},
        "not a span",
    ] + [{"span_id": f"{i:016x}", "name": "n"} for i in range(600)]  # fmt: skip
    found = {"trace_id": TRACE, "spans": spans, "tokens": "x", "tool_calls": [{"name": 3}] * 80,
             "models": ["m"] * 80, "attribution": "guess", "status": "maybe"}  # fmt: skip
    trace, problem = tryit.trace_answer({"traces": [found], "notes": ["n"] * 20 + [7]}, TRACE)
    assert problem is None
    first = trace["spans"][0]
    assert first["span_id"] == "d" * 16 and first["parent_id"] is None
    assert len(first["name"]) == tryit.MAX_TEXT and len(first["error"]) == tryit.MAX_ERROR
    assert first["kind"] == "UNKNOWN" and first["status"] == "unset"
    assert first["latency_ms"] is None and first["tokens"] is None
    assert len(trace["spans"]) <= tryit.MAX_SPANS and trace["truncated"] is True
    assert trace["tokens"] == {"prompt": None, "completion": None, "total": None}
    assert len(trace["tool_calls"]) == tryit.MAX_ITEMS and trace["tool_calls"][0]["name"] == "?"
    assert len(trace["models"]) == tryit.MAX_ITEMS
    assert trace["attribution"] is None and trace["status"] is None
    assert len(trace["notes"]) == tryit.MAX_NOTES


def test_the_cli_shape_passes_through():
    """`phoenix_traces.summarize`, the function behind `traces --json`, gives an answer
    `trace_answer` accepts whole."""
    rows = [
        {
            "context": {"span_id": "a" * 16},
            "parent_id": None,
            "name": "agent_turn",
            "span_kind": "AGENT",
            "status_code": "OK",
            "start_time": "2026-11-14T03:10:33+00:00",
            "end_time": "2026-11-14T03:10:34+00:00",
            "attributes": {"input.value": "customer message"},
        },
        {
            "context": {"span_id": "b" * 16},
            "parent_id": "a" * 16,
            "name": "call",
            "span_kind": "LLM",
            "status_code": "OK",
            "start_time": "2026-11-14T03:10:33.500000+00:00",
            "end_time": "2026-11-14T03:10:33.900000+00:00",
            "attributes": {
                "llm.model_name": "chat",
                "llm.token_count.prompt": 5,
                "llm.token_count.completion": 2,
            },
        },
    ]
    shown = phoenix_traces.Shown(lambda text: text)
    summary = phoenix_traces.summarize(TRACE, "support", rows, shown)
    trace, problem = tryit.trace_answer({"traces": [summary], "notes": []}, TRACE)
    assert problem is None
    assert [s["kind"] for s in trace["spans"]] == ["AGENT", "LLM"]
    assert trace["spans"][1]["parent_id"] == "a" * 16
    assert trace["spans"][1]["tokens"] == {"prompt": 5, "completion": 2}
    assert trace["tokens"] == {"prompt": 5, "completion": 2, "total": 7}
    assert trace["models"] == ["chat"] and trace["latency_ms"] == 1000
    assert "customer message" not in json.dumps(trace)


def test_limits_match_the_cli():
    assert tryit.MAX_SPANS == phoenix_traces.MAX_SPANS
    assert tryit.MAX_TEXT == phoenix_traces.TEXT
    assert tryit.MAX_ERROR == phoenix_traces.ERROR_TEXT
    assert tryit.HEX16.pattern == phoenix_traces.HEX16.pattern
    route = next(r for r in server.ROUTES if r.handler is server._turn_trace)
    assert route.method == "GET"


def test_trace_reads_are_bounded(center, project, cookie, tmp_path):  # noqa: F811
    for _ in range(server.MAX_TRACES_IN_FLIGHT):
        assert center.traces.acquire(blocking=False)
    try:
        status, data, _ = read(center, project, cookie)
        assert status == 400 and data["error"]["code"] == "TRACE_BUSY"
        assert calls(tmp_path) == []
    finally:
        for _ in range(server.MAX_TRACES_IN_FLIGHT):
            center.traces.release()
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["error"] is None


def test_counted_tokens_and_a_trace_still_arriving_pass_through():
    """#1009: the trace total counts each model call once, and a span whose counts are also on a
    span under it says so; #1010: a trace with no root span yet is marked, not attributed."""
    llm = {"llm.token_count.prompt": 5, "llm.token_count.completion": 2}

    def row(span_id, parent, name, kind, **attributes):
        return {
            "context": {"span_id": span_id}, "parent_id": parent, "name": name,
            "span_kind": kind, "status_code": "OK", "start_time": "2026-11-14T03:10:33+00:00",
            "end_time": "2026-11-14T03:10:34+00:00", "attributes": attributes,
        }  # fmt: skip

    rows = [
        row("a" * 16, None, "agent_turn", "AGENT"),
        row("b" * 16, "a" * 16, "call_llm", "CHAIN", **llm),
        row("c" * 16, "b" * 16, "generate_content", "LLM", **llm),
    ]
    shown = phoenix_traces.Shown(lambda text: text)
    summary = phoenix_traces.summarize(TRACE, "support", rows, shown)
    trace, _ = tryit.trace_answer({"traces": [summary]}, TRACE)
    assert trace["tokens"] == {"prompt": 5, "completion": 2, "total": 7}
    assert [s["tokens_counted"] for s in trace["spans"]] == [None, False, True]
    assert trace["incomplete"] is False
    summary = phoenix_traces.summarize(TRACE, "support", rows[1:], shown)
    trace, _ = tryit.trace_answer({"traces": [summary]}, TRACE)
    assert trace["incomplete"] is True and trace["attribution"] is None
    for bad in ("yes", 1, None):
        found = dict(summary, incomplete=bad)
        found["spans"] = [dict(summary["spans"][0], tokens_counted=bad)]
        trace, _ = tryit.trace_answer({"traces": [found]}, TRACE)
        assert trace["incomplete"] is False and trace["spans"][0]["tokens_counted"] is None
    found = dict(summary, spans=[dict(summary["spans"][0], tokens=None, tokens_counted=True)])
    trace, _ = tryit.trace_answer({"traces": [found]}, TRACE)
    assert trace["spans"][0]["tokens_counted"] is None  # no counts, nothing counted


def test_the_route_patterns_are_traces_own():
    """`tryit`'s alias, unknown tool and entity rules are `phoenix_traces`' (the console does not
    import it). UNKNOWN_TOOL and ENTITY arrive there with runtime/adk@1.4.0 (#1056, #1074)."""
    assert tryit.ALIAS.pattern == phoenix_traces.ALIAS.pattern
    for name in ("UNKNOWN_TOOL", "ENTITY"):
        if hasattr(phoenix_traces, name):
            assert getattr(tryit, name).pattern == getattr(phoenix_traces, name).pattern
