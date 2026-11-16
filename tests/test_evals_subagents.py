"""Eval rules for sub-agents and skills, case tags and `evals.tag_threshold` (#1058 §9,
#1059 §10), on fixture replies (the runtime that sends these fields is a later slice)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_release import (  # noqa: F401 - fixtures
    FakeJudge,
    FakeRuntime,
    do_release,
    operator,
    prepare_lock,
    project,
)

from agentbreeder import evals, evidence
from agentbreeder.agentfile import load_agent_file
from agentbreeder.release import EXIT_EVAL, EXIT_OK, ReleaseError

IMAGE = "sha256:" + "4" * 64


class Agent:
    """A fake /invoke that answers each turn with the next payload."""

    def __init__(self, *payloads):
        replies = list(payloads)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                body = replies.pop(0) if len(replies) > 1 else replies[0]
                data = json.dumps({"reply": "done", "tools": [], **body}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def agent():
    made = []

    def start(*payloads):
        made.append(Agent(*payloads))
        return made[-1].url

    yield start
    for item in made:
        item.server.shutdown()


SUBAGENTS = [
    {"name": "billing", "calls": 1, "model_calls": 2, "tools": ["refund_request"]},
    {"name": "returns", "calls": 1, "model_calls": 1, "tools": []},
]


def case(expect: dict, turns=("hi",), **extra) -> dict:
    return {"id": "c", "turns": list(turns), "reference": "r", "expect": expect, **extra}


def run(url: str, expect: dict, **extra) -> dict:
    return evals.run_case(url, "run", case(expect, **extra), FakeJudge())


def test_subagent_rules_pass_on_a_reply_that_reports_them(agent):
    url = agent({"tools": ["ask_billing", "refund_request"], "subagents": SUBAGENTS})
    expect = {
        "subagents_include": ["billing", "returns"],
        "subagents_exclude": ["shipping"],
        "subagent_tools_include": {"billing": ["refund_request"]},
        "tools_include": ["refund_request"],  # child tools stay in `tools`, unqualified (D5)
    }
    result = run(url, expect)
    assert result["passed"], result["checks"]
    assert result["checks"]["consulted:billing"] and result["checks"]["did_not_consult:shipping"]
    assert result["checks"]["called:billing.refund_request"]


@pytest.mark.parametrize(
    "expect,failed",
    [
        ({"subagents_include": ["shipping"]}, "consulted:shipping"),
        ({"subagents_exclude": ["billing"]}, "did_not_consult:billing"),
        (
            {"subagent_tools_include": {"returns": ["refund_request"]}},
            "called:returns.refund_request",
        ),
        ({"subagent_tools_include": {"shipping": ["x"]}}, "called:shipping.x"),
    ],
)
def test_subagent_rules_fail(agent, expect, failed):
    result = run(agent({"subagents": SUBAGENTS}), expect)
    assert not result["passed"] and result["checks"][failed] is False


@pytest.mark.parametrize(
    "expect",
    [
        {"subagents_include": ["billing"]},
        {"subagents_exclude": ["billing"]},  # would pass vacuously on an older runtime
        {"subagent_tools_include": {"billing": ["x"]}},
    ],
)
def test_a_reply_without_subagents_fails_the_case_never_passes(agent, expect):
    result = run(agent({}), expect)  # runtime/adk 1.3.0 sends no `subagents`
    assert not result["passed"]
    assert result["checks"]["reply_reports:subagents"] is False


def test_every_turn_must_report_the_field(agent):
    url = agent({"subagents": SUBAGENTS}, {})
    result = run(url, {"subagents_exclude": ["shipping"]}, turns=("one", "two"))
    assert result["checks"]["reply_reports:subagents"] is False


def test_subagents_from_every_turn_count(agent):
    url = agent({"subagents": [SUBAGENTS[0]]}, {"subagents": [SUBAGENTS[1]]})
    result = run(url, {"subagents_include": ["billing", "returns"]}, turns=("one", "two"))
    assert result["passed"], result["checks"]


def test_skill_rules(agent):
    url = agent({"skills": [{"name": "refund-policy", "version": "1.0.0", "resources": []}]})
    ok = run(url, {"skills_loaded": ["refund-policy"], "skills_not_loaded": ["tone"]})
    assert ok["passed"], ok["checks"]
    bad = run(url, {"skills_loaded": ["tone"], "skills_not_loaded": ["refund-policy"]})
    assert not bad["checks"]["loaded_skill:tone"]
    assert not bad["checks"]["did_not_load_skill:refund-policy"]
    old = run(agent({}), {"skills_not_loaded": ["tone"]})
    assert not old["passed"] and old["checks"]["reply_reports:skills"] is False


def test_rules_without_the_new_keys_add_no_checks(agent):
    result = run(agent({}), {"tools_exclude": ["x"]})
    assert result["passed"] and not any("reply_reports" in k for k in result["checks"])
    assert "tags" not in result


def test_a_failed_turn_fails_the_rule_too(agent):
    result = evals.run_case(
        "http://127.0.0.1:9", "run", case({"subagents_exclude": ["x"]}), FakeJudge()
    )
    assert not result["passed"] and result["checks"]["reply_reports:subagents"] is False


def test_tags_are_carried_on_the_result(agent):
    result = run(agent({}), {}, tags=["billing", "smoke"])
    assert result["tags"] == ["billing", "smoke"]


# -- loading -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"expect": {"tool_include": ["x"]}},  # a typo is refused, not silently passed
        {"expect": []},
        {"expect": {"subagents_include": "billing"}},
        {"expect": {"subagents_include": ["Billing"]}},
        {"expect": {"skills_loaded": ["Bad_Name"]}},
        {"expect": {"subagent_tools_include": ["billing"]}},
        {"expect": {"subagent_tools_include": {"billing": []}}},
        {"expect": {"subagent_tools_include": {"bill-ing": ["x"]}}},
        {"tags": "billing"},
        {"tags": ["Billing"]},
        {"tags": ["a", "a"]},
        {"tags": ["a", "b", "c", "d", "e", "f"]},
        {"tags": ["x" * 33]},
    ],
)
def test_bad_rules_and_tags_are_refused_at_load(tmp_path, bad):
    good = {"id": "a", "turns": ["hi"], "reference": "r"}
    (tmp_path / "manifest.yaml").write_text("judge: {alias: j}\n")
    (tmp_path / "golden.jsonl").write_text(
        json.dumps(good) + "\n" + json.dumps({**good, **bad}) + "\n"
    )
    with pytest.raises(evals.EvalError) as caught:
        evals.load_eval_set(tmp_path)
    assert caught.value.code == "EVAL_INVALID" and "golden.jsonl:2" in str(caught.value)


def test_good_rules_and_tags_load(tmp_path):
    case_ = {
        "id": "a",
        "turns": ["hi"],
        "reference": "r",
        "tags": ["billing", "smoke-1"],
        "expect": {
            "subagents_include": ["billing"],
            "subagent_tools_include": {"billing": ["refund_request"]},
            "skills_loaded": ["refund-policy"],
        },
    }
    (tmp_path / "manifest.yaml").write_text("judge: {alias: j}\n")
    (tmp_path / "golden.jsonl").write_text(json.dumps(case_) + "\n")
    assert evals.load_eval_set(tmp_path).cases == [case_]


def test_shipped_eval_sets_still_load():
    from pathlib import Path

    root = Path(evals.__file__).parent / "examples"
    sets = [p.parent for p in root.glob("*/registry/eval/*/*/golden.jsonl")]
    assert sets
    for directory in sets:
        evals.load_eval_set(directory)


# -- the report --------------------------------------------------------------------------------


def report(*rows) -> evals.EvalReport:
    results = [
        {
            "case_id": f"c{i}",
            "passed": passed,
            "attempts": 1,
            "correctness": 1.0,
            "latency_ms": 5,
            "checks": {},
            **({"tags": tags} if tags else {}),
        }
        for i, (passed, tags) in enumerate(rows)
    ]
    return evals.EvalReport("run1", "http://x", "eval/golden@1.0.0", "j", results)


def test_per_tag_report_and_threshold():
    made = report((True, ["billing"]), (False, ["billing", "returns"]), (True, None))
    assert made.summary()["tags"] == {
        "billing": {"cases": 2, "passed": 1, "pass_rate": 0.5},
        "returns": {"cases": 1, "passed": 0, "pass_rate": 0.0},
    }
    assert made.tags_below(None) == []
    assert made.tags_below(0.5) == ["returns"]
    assert made.tags_below(0.6) == ["billing", "returns"]


def test_an_untagged_report_is_unchanged():
    assert "tags" not in report((True, None), (False, None)).summary()


# -- release, evidence --------------------------------------------------------------------------


class TaggedEvaluator:
    """Every case passes except the ones tagged `returns`."""

    def __init__(self):
        self.endpoints = []

    def __call__(self, eval_set, endpoint, _operator, environment, state, *, retries):
        self.endpoints.append(endpoint)
        results = [
            {"case_id": c["id"], "passed": c["id"] != "b", "attempts": 1, "correctness": 1.0,
             "latency_ms": 10, "checks": {},
             "tags": ["returns"] if c["id"] == "b" else ["billing"]}
            for c in eval_set.cases
        ]  # fmt: skip
        return evals.EvalReport("run1", endpoint, "eval/golden@1.0.0", "support-judge", results)


def _set_evals(directory, line: str) -> None:
    path = directory / "agent.yaml"
    text = path.read_text()
    start = text.index("evals:")
    path.write_text(text[:start] + line + "\n")


def test_tag_threshold_fails_a_release_the_overall_rate_passes(project, operator, state):  # noqa: F811
    _set_evals(project, "evals: {ref: eval/golden@1.0.0, threshold: 0.5, tag_threshold: 0.9}")
    prepare_lock(project, operator)
    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE, TaggedEvaluator())
    assert outcome.code == EXIT_EVAL
    assert "tag(s) returns below 90%" in outcome.message
    assert outcome.details["eval"]["tags"]["returns"]["passed"] == 0
    row = [r for r in state.audit_rows(limit=50) if r["decision"] == "eval_failed"][0]
    codes = {item["code"]: item["message"] for item in row["reasons"]}
    assert codes["RELEASE_TAG_THRESHOLD"] == "0.9"


def test_without_tag_threshold_the_same_run_passes(project, operator, state):  # noqa: F811
    _set_evals(project, "evals: {ref: eval/golden@1.0.0, threshold: 0.5}")
    prepare_lock(project, operator)
    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE, TaggedEvaluator())
    assert outcome.code == EXIT_OK, outcome.message
    row = [r for r in state.audit_rows(limit=50) if r["decision"] == "promoted"][0]
    assert "RELEASE_TAG_THRESHOLD" not in {item["code"] for item in row["reasons"]}


def test_a_release_without_the_tag_threshold_is_evaluated_again(project, operator, state):  # noqa: F811
    _set_evals(project, "evals: {ref: eval/golden@1.0.0, threshold: 0.5}")
    prepare_lock(project, operator)
    assert do_release(project, operator, state, FakeRuntime(), IMAGE)[0].code == EXIT_OK
    _set_evals(project, "evals: {ref: eval/golden@1.0.0, threshold: 0.5, tag_threshold: 0.5}")
    agent = load_agent_file(project / "agent.yaml")
    with pytest.raises(ReleaseError, match="RELEASE_THRESHOLD_LOWERED"):
        evidence.released(state, agent, project, state.active("helper"), IMAGE)


def test_tag_threshold_evidence_values():
    assert not evidence._tag_threshold_lowered({}, None)
    assert evidence._tag_threshold_lowered({}, 0.5)
    assert evidence._tag_threshold_lowered({"RELEASE_TAG_THRESHOLD": "x"}, 0.5)
    assert evidence._tag_threshold_lowered({"RELEASE_TAG_THRESHOLD": "0.4"}, 0.5)
    assert not evidence._tag_threshold_lowered({"RELEASE_TAG_THRESHOLD": "0.5"}, 0.5)


def test_odd_reply_values_fail_the_rule_and_never_crash():
    """Review (security): a list or dict where a name or tool belongs is ignored, not hashed."""
    checks: dict[str, bool] = {}
    responses = [{"subagents": [{"name": ["billing"], "tools": [{"x": 1}, "t"]}], "skills": [1]}]
    expect = {
        "subagents_include": ["billing"],
        "subagent_tools_include": {"billing": ["t"]},
        "skills_loaded": ["tone"],
    }
    evals._composition_checks(expect, responses, checks)
    assert checks["consulted:billing"] is False and checks["called:billing.t"] is False
    assert checks["loaded_skill:tone"] is False


def test_unknown_rule_message_does_not_echo_the_key(tmp_path):
    (tmp_path / "manifest.yaml").write_text("judge: {alias: j}\n")
    case_ = {"id": "a", "turns": ["hi"], "reference": "r", "expect": {"secret-ish-key": []}}
    (tmp_path / "golden.jsonl").write_text(json.dumps(case_) + "\n")
    with pytest.raises(evals.EvalError) as caught:
        evals.load_eval_set(tmp_path)
    assert "secret-ish-key" not in str(caught.value) and "tools_include" in str(caught.value)


def test_tag_gate():
    assert report((True, None)).tag_gate(None) is None
    assert "no case is tagged" in report((True, None)).tag_gate(0.5)
    assert report((True, ["a"])).tag_gate(1.0) is None
    assert report((False, ["a"])).tag_gate(0.5) == "tag(s) a below 50%"


def test_tag_threshold_over_an_untagged_set_fails_release(project, operator, state):  # noqa: F811
    _set_evals(project, "evals: {ref: eval/golden@1.0.0, threshold: 0.5, tag_threshold: 0.5}")
    prepare_lock(project, operator)
    from test_release import Evaluator

    outcome, _ = do_release(project, operator, state, FakeRuntime(), IMAGE, Evaluator())
    assert outcome.code == EXIT_EVAL and "no case is tagged" in outcome.message
