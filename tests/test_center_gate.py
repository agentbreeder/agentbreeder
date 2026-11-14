"""The gate-failed view's server half (#826, M10 console half, PR 3): the saved candidate tail,
`logs --failed NAME --stack DIR --environment ENV --json` with fixed argv, never cached, exit 1
(no tail) as `{saved: false}`, the answer checked and the file's path never passed on."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)

from agentbreeder import cli
from agentbreeder.center import evals, jobs, server

TAIL = {
    "schema_version": 1,
    "agent": "support",
    "path": "/state/releases/support.candidate.log",
    "lines": ["2026-11-13T10:00:00Z boot", "2026-11-13T10:00:01Z \\x1b[31mred"],
}

# `logs` records its argv, then prints marker/tail.json, exits 1 (marker/no-tail) or exits 2
# with marker/logs-stderr.
FAKE = textwrap.dedent(
    """
    import json, os, sys
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[0] == "logs":
        open(marker + "/logs-argv", "a").write(json.dumps(argv) + "\\n")
        if os.path.exists(marker + "/no-tail"):
            sys.stderr.write("No failed-candidate log for support in /state: none failed yet\\n")
            sys.exit(1)
        if os.path.exists(marker + "/logs-stderr"):
            sys.stderr.write(open(marker + "/logs-stderr").read())
            sys.exit(2)
        print(open(marker + "/tail.json").read())
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
    (tmp_path / "tail.json").write_text(json.dumps(TAIL))
    return [sys.executable, str(path)]


def argvs(tmp_path) -> list[list[str]]:
    path = tmp_path / "logs-argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def get(center, project, cookie, name):  # noqa: F811
    return api(center, "GET", f"/api/projects/{project['id']}/logs/failed/{name}", cookie=cookie)


def test_the_argv_parses_with_the_real_cli():
    argv = ["logs", "--failed", "support", "--stack", "/p", "--environment", "dev", "--json"]
    args = cli._parser().parse_args(argv)
    assert args.failed == "support" and str(args.stack) == "/p" and args.environment == "dev"
    assert args.json is True and args.raw is False and args.id is None


def test_the_route_takes_the_cli_agent_rule():
    assert server.AGENT.endswith("{0,52})")  # the route is looser; the server checks evals.AGENT
    assert evals.AGENT.fullmatch("support") and not evals.AGENT.fullmatch("support-")


def test_a_saved_tail_is_read_with_fixed_argv(center, project, cookie, tmp_path):  # noqa: F811
    status, data, _ = get(center, project, cookie, "support")
    assert status == 200 and data["error"] is None
    assert data["data"] == {"agent": "support", "saved": True, "lines": TAIL["lines"]}
    assert "path" not in data["data"]  # the state directory is not shown
    assert argvs(tmp_path) == [
        [
            "logs", "--failed", "support", "--stack", project["path"],
            "--environment", project["environment"], "--json",
        ]
    ]  # fmt: skip


def test_no_saved_tail_is_an_answer_not_an_error(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "no-tail").write_text("")
    status, data, _ = get(center, project, cookie, "support")
    assert status == 200 and data["error"] is None and data["stale"] is False
    assert data["data"] == {"agent": "support", "saved": False, "lines": []}


def test_reads_are_never_cached(center, project, cookie, tmp_path):  # noqa: F811
    get(center, project, cookie, "support")
    (tmp_path / "logs-stderr").write_text("[STATE_UNSAFE] the tail must be a 0600 file you own\n")
    _, data, _ = get(center, project, cookie, "support")
    assert data["data"] is None and data["stale"] is False
    assert data["error"]["code"] == "STATE_UNSAFE"
    assert len(argvs(tmp_path)) == 2


@pytest.mark.parametrize(
    "answer",
    [
        {**TAIL, "agent": "other"},
        {**TAIL, "lines": "x"},
        {**TAIL, "lines": ["ok", 3]},
        ["not", "an", "object"],
    ],
)
def test_an_answer_off_contract_is_not_passed_on(center, project, cookie, tmp_path, answer):  # noqa: F811
    (tmp_path / "tail.json").write_text(json.dumps(answer))
    _, data, _ = get(center, project, cookie, "support")
    assert data["data"] is None and data["error"]["code"] == "LOGS_INVALID"


@pytest.mark.parametrize("name", ["support-", "-x", "a" * 54])
def test_a_name_outside_the_rule_never_reaches_the_cli(center, project, cookie, tmp_path, name):  # noqa: F811
    status, data, _ = get(center, project, cookie, name)
    assert status in (400, 404)
    if status == 400:
        assert data["error"]["code"] == "AGENT_INVALID"
    assert argvs(tmp_path) == []


@pytest.mark.parametrize("name", ["Support", "a_b", "..%2Fx", "--json"])
def test_a_bad_name_does_not_match_the_route(center, project, cookie, tmp_path, name):  # noqa: F811
    status, _, _ = get(center, project, cookie, name)
    assert status == 404
    assert argvs(tmp_path) == []


def test_the_tail_needs_a_session(center, project, tmp_path):  # noqa: F811
    status, _, _ = api(center, "GET", f"/api/projects/{project['id']}/logs/failed/support")
    assert status == 401
    assert argvs(tmp_path) == []


def test_empty_codes_answer_nothing_and_skip_the_cache(tmp_path):
    script = tmp_path / "exit.py"
    script.write_text("import sys\nprint('{\"a\": 1}')\nsys.exit(int(sys.argv[1]))\n")
    runner = jobs.Runner(None, tmp_path, prefix=[sys.executable, str(script)])
    assert runner.read(["0"]) == {"data": {"a": 1}, "error": None, "stale": False}
    assert runner.read(["1"], empty_codes=(1,)) == {"data": None, "error": None, "stale": False}
    assert runner.cache == {"0": {"data": {"a": 1}, "error": None, "stale": False}}
    # Without empty_codes, exit 1 is an error.
    assert runner.read(["1"])["error"]["code"] == "COMMAND_FAILED"


def test_a_crash_that_exits_1_is_not_nothing_there(tmp_path):
    script = tmp_path / "crash.py"
    script.write_text("raise RuntimeError('boom')\n")  # Python exits 1 with a traceback
    runner = jobs.Runner(None, tmp_path, prefix=[sys.executable, str(script)])
    answer = runner.read(["x"], empty_codes=(1,))
    assert answer["data"] is None and answer["error"]["code"] == "COMMAND_FAILED"
    assert "boom" not in answer["error"]["message"]  # free stderr never reaches the browser
