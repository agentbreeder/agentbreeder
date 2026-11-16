"""The Traces tab of an agent (#907, console half): one deployment's recent traces through
`traces ID --stack DIR --environment ENV --last N --json`, uncached, with only an allow-listed
summary per trace passed on (docs/traces.md; no spans, no message content)."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)

from agentbreeder import phoenix_traces
from agentbreeder.center import server, traces

DEPLOYMENT = "18bc2abed83946b6a52c045e973d1844"
OTHER = "28bc2abed83946b6a52c045e973d1844"
TRACE = "4053" + "a" * 28


def _trace(trace_id=TRACE, deployment=DEPLOYMENT, **extra):
    return {
        "trace_id": trace_id,
        "project": "support",
        "deployment_id": deployment,
        "attribution": "window",
        "start_time": "2026-11-14T03:10:33.358423+00:00",
        "end_time": "2026-11-14T03:10:33.399734+00:00",
        "latency_ms": 41,
        "status": "error",
        "session_id": "s-1",
        "turn_id": "turn-1",
        "models": ["gemini-2.5-flash"],
        "tokens": {"prompt": 321, "completion": 70, "total": 391},
        "tool_calls": [
            {"name": "lookup_order", "status": "error", "latency_ms": 11, "span_id": "b" * 16}
        ],
        "spans": [
            {"span_id": "a" * 16, "parent_id": None, "name": "agent_turn", "kind": "AGENT"},
            {"span_id": "b" * 16, "parent_id": "a" * 16, "name": "lookup_order", "kind": "TOOL"},
        ],
        "spans_truncated": False,
        **extra,
    }


ANSWER = {
    "schema_version": 1,
    "phoenix": "http://127.0.0.1:57340",
    "traces": [_trace()],
    "overlapping": [OTHER],
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


def read(center, project, cookie, deployment=DEPLOYMENT, query=""):  # noqa: F811
    path = f"/api/projects/{project['id']}/deployments/{deployment}/traces{query}"
    return api(center, "GET", path, cookie=cookie)


def answer(tmp_path, value, code=0):
    (tmp_path / "traces.json").write_text(json.dumps(value))
    (tmp_path / "traces-exit").write_text(str(code))


def calls(tmp_path) -> list[list[str]]:
    path = tmp_path / "traces-argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_traces_are_read_with_a_fixed_argv(center, project, cookie, tmp_path):  # noqa: F811
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["error"] is None
    assert calls(tmp_path) == [
        ["traces", DEPLOYMENT, "--stack", project["path"], "--environment", "dev",
         "--last", "20", "--json"]
    ]  # fmt: skip
    listed = data["data"]
    assert listed["overlapping"] == [OTHER] and listed["truncated"] is False
    assert listed["dropped"] == 0 and listed["notes"] == []
    (trace,) = listed["traces"]
    assert trace == {
        "trace_id": TRACE,
        "start_time": "2026-11-14T03:10:33.358423+00:00",
        "latency_ms": 41,
        "status": "error",
        "attribution": "window",
        "models": ["gemini-2.5-flash"],
        "tokens": {"prompt": 321, "completion": 70, "total": 391},
        "tool_calls": [{"name": "lookup_order", "status": "error", "latency_ms": 11}],
        "span_count": 2,
        "spans_truncated": False,
        "incomplete": False,
        # Root-span facts (#1062): this trace is from a runtime before adk 1.3.0.
        "fallbacks": None,
        "served_by": [],
        "cached_prompt_tokens": None,
        "unknown_tools": [],
        "guardrail": None,
    }


def test_a_listed_trace_carries_its_root_spans_facts(center, project, cookie, tmp_path):  # noqa: F811
    given = json.loads(json.dumps(ANSWER))
    given["traces"][0] |= {
        "fallbacks": 2,
        "served_by": ["chat-backup", "bad alias"],
        "cached_prompt_tokens": 300,
        "unknown_tools": ["delete_everything"],
        "guardrail": {"action": "blocked", "name": "presidio-pii", "entity": "US_SSN"},
    }
    answer(tmp_path, given)
    (trace,) = read(center, project, cookie)[1]["data"]["traces"]
    assert trace["fallbacks"] == 2 and trace["served_by"] == ["chat-backup"]
    assert trace["cached_prompt_tokens"] == 300 and trace["unknown_tools"] == ["delete_everything"]
    assert trace["guardrail"] == {"action": "blocked", "name": "presidio-pii", "entity": "US_SSN"}


@pytest.mark.parametrize("last", ["1", "10", "50"])
def test_last_is_passed_on(center, project, cookie, tmp_path, last):  # noqa: F811
    status, _, _ = read(center, project, cookie, query=f"?last={last}")
    assert status == 200 and calls(tmp_path)[0][-3:] == ["--last", last, "--json"]


@pytest.mark.parametrize("last", ["0", "51", "200", "-1", "1e1", "abc", "07", "%205"])
def test_last_outside_the_rule_never_reaches_the_cli(center, project, cookie, tmp_path, last):  # noqa: F811
    status, data, _ = read(center, project, cookie, query=f"?last={last}")
    assert status == 400 and data["error"]["code"] == "LAST_INVALID"
    assert calls(tmp_path) == []


@pytest.mark.parametrize(
    "deployment", ["A" * 32, "a" * 31, "a" * 33, "g" * 32, "-" + "a" * 31, "..%2f" + "a" * 27]
)
def test_ids_outside_the_rule_never_reach_the_cli(center, project, cookie, tmp_path, deployment):  # noqa: F811
    status, _, _ = read(center, project, cookie, deployment)
    assert status == 404
    assert calls(tmp_path) == []


def test_the_answer_is_never_cached(center, project, cookie, tmp_path):  # noqa: F811
    failure = {"code": "TRACES_UNREACHABLE", "message": "x"}
    answer(tmp_path, {"schema_version": 1, "error": failure}, 2)
    _, first, _ = read(center, project, cookie)
    assert first["error"]["code"] == "TRACES_UNREACHABLE" and first["data"] is None
    answer(tmp_path, ANSWER)
    _, second, _ = read(center, project, cookie)
    assert second["error"] is None and second["data"]["traces"][0]["trace_id"] == TRACE
    assert len(calls(tmp_path)) == 2


def test_only_allow_listed_fields_reach_the_browser(center, project, cookie, tmp_path):  # noqa: F811
    hostile = json.loads(json.dumps(ANSWER))
    found = hostile["traces"][0]
    found["input.value"] = "customer message"
    found["spans"][0]["attributes"] = {"input.value": "customer message"}
    found["spans"][1]["error"] = "agent reply"
    found["tool_calls"][0]["arguments"] = {"order": "1001"}
    hostile["extra"] = "x"
    hostile["overlapping"] += ["not-an-id", 7]
    answer(tmp_path, hostile)
    _, data, _ = read(center, project, cookie)
    text = json.dumps(data)
    assert "customer message" not in text and "agent reply" not in text and "1001" not in text
    # The Phoenix origin, the project, and the session and turn ids stay on the server.
    assert "57340" not in text and "s-1" not in text and "turn-1" not in text
    assert "support" not in text and "not-an-id" not in text
    assert set(data["data"]) == {"traces", "overlapping", "truncated", "dropped", "notes"}
    assert "spans" not in data["data"]["traces"][0]


def test_another_deployments_trace_is_dropped(center, project, cookie, tmp_path):  # noqa: F811
    value = dict(ANSWER, traces=[
        _trace(), _trace("b" * 32, OTHER), _trace("c" * 32, None), _trace(TRACE),
        _trace("NOTHEX" + "a" * 26), "x",
    ])  # fmt: skip
    answer(tmp_path, value)
    _, data, _ = read(center, project, cookie)
    assert [t["trace_id"] for t in data["data"]["traces"]] == [TRACE]
    assert data["data"]["dropped"] == 5


@pytest.mark.parametrize(
    "value,code",
    [
        ({"schema_version": 1, "error": {"code": "TRACES_NO_PHOENIX", "message": "no"}},
         "TRACES_NO_PHOENIX"),
        ({"schema_version": 1, "error": {"code": "NOT_FOUND", "message": "no"}}, "NOT_FOUND"),
        ({"schema_version": 1, "error": {"code": "bad code", "message": "no"}}, "TRACES_FAILED"),
        ({"schema_version": 1, "traces": "x"}, "TRACES_INVALID"),
        ({"schema_version": 2, "traces": []}, "TRACES_INVALID"),
        ({"traces": []}, "TRACES_INVALID"),
        ([1, 2], "TRACES_INVALID"),
    ],
)  # fmt: skip
def test_failures_are_coded(center, project, cookie, tmp_path, value, code):  # noqa: F811
    answer(tmp_path, value, 2 if isinstance(value, dict) and "error" in value else 0)
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["data"] is None and data["error"]["code"] == code


def test_no_traces_is_an_empty_list(center, project, cookie, tmp_path):  # noqa: F811
    note = f"{DEPLOYMENT}: no Phoenix project support (no traces yet, or `traces: none`)"
    answer(tmp_path, dict(ANSWER, traces=[], overlapping=[], notes=[note, 3]))
    _, data, _ = read(center, project, cookie)
    assert data["error"] is None and data["data"]["traces"] == []
    assert data["data"]["notes"] == [note]


def test_a_cli_failure_without_json_is_reported(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "traces-stderr").write_text("[TRACES_UNREACHABLE] Phoenix is not running\n")
    _, data, _ = read(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == "TRACES_UNREACHABLE"


def test_list_argv_checks_its_input_itself():
    where = {"path": "/p", "environment": "dev"}
    with pytest.raises(Exception, match="32 lowercase hex"):
        traces.list_argv(where, "A" * 32, 20)
    with pytest.raises(Exception, match="Last must be"):
        traces.list_argv(where, DEPLOYMENT, traces.MAX_LAST + 1)


def test_bad_values_are_dropped_and_limits_hold():
    found = _trace(
        start_time="yesterday", latency_ms=True, status="maybe", attribution="guess",
        models=["m"] * 80, tokens="x", tool_calls=[{"name": 3}] * 80 + ["x"],
        spans=[{}] * 600, spans_truncated="yes",
    )  # fmt: skip
    notes = ["n" * 900] * 20 + [7]
    listed, problem = traces.list_answer(
        {"schema_version": 1, "traces": [found], "notes": notes}, DEPLOYMENT
    )
    assert problem is None
    (trace,) = listed["traces"]
    assert trace["start_time"] is None and trace["latency_ms"] is None
    assert trace["status"] is None and trace["attribution"] is None
    assert len(trace["models"]) == traces.MAX_ITEMS
    assert trace["tokens"] == {"prompt": None, "completion": None, "total": None}
    assert len(trace["tool_calls"]) == traces.MAX_ITEMS and trace["tool_calls"][0]["name"] == "?"
    assert trace["span_count"] == traces.MAX_SPANS and trace["spans_truncated"] is True
    assert len(listed["notes"]) == traces.MAX_NOTES and len(listed["notes"][0]) == traces.MAX_ERROR
    assert listed["overlapping"] == [] and listed["truncated"] is False


def test_the_list_is_capped():
    many = [_trace(f"{i:032x}") for i in range(traces.MAX_LAST * 4 + 5)]
    listed, _ = traces.list_answer({"schema_version": 1, "traces": many}, DEPLOYMENT)
    assert len(listed["traces"]) == traces.MAX_LAST and listed["truncated"] is True


def test_the_cli_shape_passes_through():
    """`phoenix_traces.Report` and `summarize`, behind `traces --json`, give an answer
    `list_answer` accepts whole."""
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
    summary = phoenix_traces.summarize(TRACE, "support", rows, phoenix_traces.Shown(lambda t: t))
    summary["deployment_id"], summary["attribution"] = DEPLOYMENT, "exact"
    report = phoenix_traces.Report("http://127.0.0.1:6006", traces=[summary])
    listed, problem = traces.list_answer(json.loads(json.dumps(report.as_json())), DEPLOYMENT)
    assert problem is None and listed["dropped"] == 0
    (trace,) = listed["traces"]
    assert trace["start_time"] == "2026-11-14T03:10:33+00:00"
    assert trace["models"] == ["chat"] and trace["latency_ms"] == 1000
    assert trace["tokens"] == {"prompt": 5, "completion": 2, "total": 7}
    assert trace["span_count"] == 2 and trace["attribution"] == "exact"
    assert "customer message" not in json.dumps(listed)


def test_limits_match_the_cli():
    assert traces.DEFAULT_LAST == phoenix_traces.DEFAULT_LAST
    assert traces.MAX_LAST <= phoenix_traces.MAX_LAST
    assert traces.MAX_SPANS == phoenix_traces.MAX_SPANS
    route = next(r for r in server.ROUTES if r.handler is server._deployment_traces)
    assert route.method == "GET"
    found, match, _ = server.find_route("GET", route.sample)
    assert found is route and match["deployment"] == "0" * 32


def test_list_reads_share_the_trace_bound(center, project, cookie, tmp_path):  # noqa: F811
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


def test_the_bound_is_released_when_a_read_fails(center, project, cookie, monkeypatch):  # noqa: F811
    def broken(*args, **kwargs):
        raise RuntimeError("runner broke")

    monkeypatch.setattr(center.runner, "read", broken)
    status, _, _ = read(center, project, cookie)
    assert status == 500
    for _ in range(server.MAX_TRACES_IN_FLIGHT):
        assert center.traces.acquire(blocking=False)
    for _ in range(server.MAX_TRACES_IN_FLIGHT):
        center.traces.release()


def test_a_trace_still_arriving_passes_through():
    """#1010: a listed trace with no root span yet is marked, so the tab can say so."""
    rows = [
        {
            "context": {"span_id": "b" * 16}, "parent_id": "a" * 16, "name": "call_llm",
            "span_kind": "CHAIN", "status_code": "OK", "start_time": "2026-11-14T03:10:33+00:00",
            "end_time": "2026-11-14T03:10:34+00:00", "attributes": {},
        },
    ]  # fmt: skip
    summary = phoenix_traces.summarize(TRACE, "support", rows, phoenix_traces.Shown(lambda t: t))
    summary["deployment_id"] = DEPLOYMENT
    listed, problem = traces.list_answer({"schema_version": 1, "traces": [summary]}, DEPLOYMENT)
    assert problem is None
    (trace,) = listed["traces"]
    assert trace["incomplete"] is True and trace["attribution"] is None
    summary["incomplete"] = "true"
    listed, _ = traces.list_answer({"schema_version": 1, "traces": [summary]}, DEPLOYMENT)
    assert listed["traces"][0]["incomplete"] is False
