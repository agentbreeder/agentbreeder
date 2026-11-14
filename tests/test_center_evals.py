"""The eval report viewer's reads (#932, console half): `evals list|show --json` with fixed argv,
redacted with the stack's state, never cached, the answer checked against its shape."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)

from agentbreeder import cli, eval_reports
from agentbreeder.center import evals, server

RUN = "20261113-213200-1a2b3c"
REPORT = {
    "schema_version": 1,
    "agent": "support",
    "file": f"support.eval-{RUN}.json",
    "run_id": RUN,
    "eval_set": "eval/support@1.0.0",
    "judge": "judge-model",
    "endpoint": "http://127.0.0.1:8080",
    "cases": 2,
    "passed": 1,
    "pass_rate": 0.5,
    "flaky": [],
    "failed": ["c2"],
    "mean_correctness": 0.5,
    "p50_latency_ms": 120,
    "content": False,
    "results": [
        {
            "id": "c2",
            "category": "billing",
            "passed": False,
            "attempts": 1,
            "checks": {"agent_responded": True, "judge_correct": False},
            "failed_checks": ["judge_correct"],
            "correctness": 0.1,
            "reason": "wrong answer",
            "latency_ms": 120,
            "trace_id": "ab" * 16,
            "tools": ["lookup_order"],
        }
    ],
    "notes": [],
}
LISTING = {"schema_version": 1, "reports": [], "total": 0, "truncated": False}

# `evals` records its argv and prints marker/evals.json (or exits 2 with marker/evals-stderr);
# `services env` names a state dir unless marker/no-state exists.
FAKE = textwrap.dedent(
    """
    import json, os, sys
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[0] == "evals":
        open(marker + "/evals-argv", "a").write(json.dumps(argv) + "\\n")
        if os.path.exists(marker + "/evals-stderr"):
            sys.stderr.write(open(marker + "/evals-stderr").read())
            sys.exit(2)
        print(open(marker + "/evals.json").read())
    elif argv[:2] == ["services", "env"]:
        if not os.path.exists(marker + "/no-state"):
            print("export AGENTBREEDER_STATE_DIR=/tmp/fake-state")
    elif argv[0] == "doctor":
        print("[]")
    else:
        print("ok")
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_cli.py"
    path.write_text(FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    (tmp_path / "evals.json").write_text(json.dumps(REPORT))
    return [sys.executable, str(path)]


def argvs(tmp_path) -> list[list[str]]:
    path = tmp_path / "evals-argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def get(center, project, cookie, tail):  # noqa: F811
    return api(center, "GET", f"/api/projects/{project['id']}/{tail}", cookie=cookie)


# -- pure ---------------------------------------------------------------------------------------


def test_the_rules_match_the_cli():
    assert server.EVAL_RUN.endswith(eval_reports.RUN.pattern + ")")
    assert evals.RUN.pattern == eval_reports.RUN.pattern
    assert evals.AGENT.pattern == eval_reports.AGENT.pattern
    assert evals.RUN.flags == eval_reports.RUN.flags
    assert evals.AGENT.flags == eval_reports.AGENT.flags
    for name in ("support", "a", "a-b"):
        assert evals.list_argv({"path": "/p"}, "/s", {"agent": [name]})[-2] == name


@pytest.mark.parametrize(
    "argv",
    [
        evals.list_argv({"path": "/p"}, "/s", {}),
        evals.list_argv({"path": "/p"}, "/s", {"agent": ["support"]}),
        evals.show_argv({"path": "/p"}, "/s", RUN, {}),
        evals.show_argv({"path": "/p"}, "/s", RUN, {"agent": ["support"], "content": ["1"]}),
    ],
)
def test_every_argv_parses_with_the_real_cli(argv):
    args = cli._parser().parse_args(argv)
    assert args.json is True and str(args.project) == "/p" and str(args.state_dir) == "/s"
    assert args.output is None and args.stack is None


def test_show_argv_is_fixed_but_for_the_checked_values():
    assert evals.show_argv({"path": "/p"}, "/s", RUN, {"content": ["yes"], "x": ["--output"]}) == [
        "evals", "show", RUN, "--project", "/p", "--state-dir", "/s", "--json",
    ]  # fmt: skip
    assert evals.show_argv({"path": "/p"}, "/s", RUN, {"agent": ["s"], "content": ["1"]}) == [
        "evals", "show", RUN, "--project", "/p", "--state-dir", "/s", "--agent", "s",
        "--content", "--json",
    ]  # fmt: skip


@pytest.mark.parametrize("name", ["Support", "-x", "x-", "a_b", "--output", "a" * 54, "a b"])
def test_a_bad_agent_is_refused(name):
    with pytest.raises(evals.Invalid) as raised:
        evals.list_argv({"path": "/p"}, "/s", {"agent": [name]})
    assert raised.value.code == "AGENT_INVALID"


def test_a_bad_run_is_refused():
    with pytest.raises(evals.Invalid) as raised:
        evals.show_argv({"path": "/p"}, "/s", "--content", {})
    assert raised.value.code == "RUN_INVALID"


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {**REPORT, "schema_version": 2},
        {**REPORT, "run_id": "20261112-090000-abcdef"},
        {**REPORT, "results": {"c2": {}}},
        {**REPORT, "results": ["c2"]},
        {**REPORT, "notes": "none"},
        {**REPORT, "agent": "Support/x"},
        {**REPORT, "agent": None},
        {**REPORT, "file": 7},
    ],
)
def test_a_show_answer_off_contract_is_refused(data):
    assert not evals.show_answer(data, RUN)


def test_answers_on_contract_pass():
    assert evals.show_answer(REPORT, RUN)
    assert evals.list_answer(LISTING)
    assert not evals.list_answer({**LISTING, "reports": ["x"]})
    assert not evals.list_answer({**LISTING, "schema_version": 0})


# -- through the server -------------------------------------------------------------------------


def test_a_report_is_read_with_fixed_argv(center, project, cookie, tmp_path):  # noqa: F811
    status, data, _ = get(center, project, cookie, f"evals/{RUN}?agent=support&content=1")
    assert status == 200 and data["error"] is None
    assert data["data"] == REPORT
    assert argvs(tmp_path) == [
        [
            "evals", "show", RUN, "--project", project["path"], "--state-dir", "/tmp/fake-state",
            "--agent", "support", "--content", "--json",
        ]
    ]  # fmt: skip


def test_the_list_is_a_project_view(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "evals.json").write_text(json.dumps(LISTING))
    status, data, _ = get(center, project, cookie, "evals")
    assert status == 200 and data["data"] == LISTING
    assert argvs(tmp_path) == [
        [
            "evals", "list", "--project", project["path"], "--state-dir", "/tmp/fake-state",
            "--json",
        ]
    ]  # fmt: skip


def test_reads_are_never_cached(center, project, cookie, tmp_path):  # noqa: F811
    get(center, project, cookie, f"evals/{RUN}")
    (tmp_path / "evals-stderr").write_text("[EVAL_REPORT_NOT_FOUND] no report for that run\n")
    status, data, _ = get(center, project, cookie, f"evals/{RUN}")
    assert status == 200 and data["data"] is None and data["stale"] is False
    assert data["error"]["code"] == "EVAL_REPORT_NOT_FOUND"
    assert len(argvs(tmp_path)) == 2


def test_an_answer_off_contract_is_not_passed_on(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "evals.json").write_text(json.dumps({**REPORT, "run_id": "x"}))
    _, data, _ = get(center, project, cookie, f"evals/{RUN}")
    assert data["data"] is None and data["error"]["code"] == "EVALS_INVALID"
    (tmp_path / "evals.json").write_text(json.dumps({"reports": "x"}))
    _, data, _ = get(center, project, cookie, "evals")
    assert data["data"] is None and data["error"]["code"] == "EVALS_INVALID"


def test_without_a_running_stack_nothing_is_read(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "no-state").write_text("")
    _, data, _ = get(center, project, cookie, f"evals/{RUN}")
    assert data["error"]["code"] == "STACK_NOT_UP"
    assert argvs(tmp_path) == []


@pytest.mark.parametrize(
    "tail,code",
    [
        (f"evals/{RUN}?agent=Support", "AGENT_INVALID"),
        (f"evals/{RUN}?agent=--output", "AGENT_INVALID"),
        ("evals?agent=a_b", "AGENT_INVALID"),
    ],
)
def test_bad_values_never_reach_the_cli(center, project, cookie, tmp_path, tail, code):  # noqa: F811
    status, data, _ = get(center, project, cookie, tail)
    assert status == 400 and data["error"]["code"] == code
    assert argvs(tmp_path) == []


@pytest.mark.parametrize("run", ["20261113-213200-1A2B3C", "x", "..%2F..%2Faudit", RUN + "0"])
def test_a_bad_run_does_not_match_the_route(center, project, cookie, tmp_path, run):  # noqa: F811
    status, _, _ = get(center, project, cookie, f"evals/{run}")
    assert status == 404
    assert argvs(tmp_path) == []


def test_reports_need_a_session(center, project, tmp_path):  # noqa: F811
    status, _, _ = api(center, "GET", f"/api/projects/{project['id']}/evals/{RUN}")
    assert status == 401
    assert argvs(tmp_path) == []
