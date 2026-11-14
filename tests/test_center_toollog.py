"""A tool's execution log in the console (#828, M12 console half, part 2): `tool log REF ...
--json` as an uncached CLI child, the query checked before it, and only the allow-listed fields
of its answer passed on (docs/commands/tool.md). Arguments only with `content=1`."""

from __future__ import annotations

import json
import re
import sys
import textwrap

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)
from test_tool_log import (  # the CLI's own fixtures and helpers, for the contract tests
    AGENT_PASSWORD,
    SECRETS,
    _fetched,
    _result,
    _row,
    _stack,
    _written,
    stacks,  # noqa: F401 (fixture)
)

from agentbreeder import cli, registry
from agentbreeder import toollog as cli_toollog
from agentbreeder.center import server, toollog

CALL = {
    "at": "2026-11-13T16:02:11.123Z",
    "agent": "northwind-support",
    "tool": "tool/orders@1.0.0",
    "function": "lookup_orders",
    "status": "ok",
    "latency_ms": 41,
    "turn_id": "9f1c" * 8,
    "trace_id": "4bf9" * 8,
    "db_role": "support_agent",
}
ANSWER = {
    "schema_version": 1,
    "ref": "tool/orders@1.0.0",
    "environment": "dev",
    "database": "support",
    "credential": "console",
    "schema": "public",
    "window": {"since": "2026-11-12T17:00:00Z", "until": "2026-11-13T17:00:00Z"},
    "limit": 100,
    "truncated": False,
    "content": False,
    "db_role_verified": True,
    "phoenix_url": "http://127.0.0.1:6006",
    "phantom_rows": 0,
    "notes": ["db_role is not verified: no owner trigger in force now"],
    "calls": [CALL],
}

# `tool log` only: the argv is recorded; `log.json` and `log-exit` set the answer.
FAKE = textwrap.dedent(
    """
    import json, os, sys
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[:2] == ["tool", "log"]:
        open(marker + "/log-argv", "a").write(json.dumps(argv) + "\\n")
        if os.path.exists(marker + "/log-stderr"):
            sys.stderr.write(open(marker + "/log-stderr").read())
            sys.exit(1)
        print(open(marker + "/log.json").read())
        code = marker + "/log-exit"
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
    path.write_text(FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    (tmp_path / "log.json").write_text(json.dumps(ANSWER))
    return [sys.executable, str(path)]


def read(center, project, cookie, query="ref=tool%2Forders%401.0.0"):  # noqa: F811
    return api(center, "GET", f"/api/projects/{project['id']}/tools/log?{query}", cookie=cookie)


def answer(tmp_path, value, code=0):
    (tmp_path / "log.json").write_text(json.dumps(value))
    (tmp_path / "log-exit").write_text(str(code))


def calls(tmp_path) -> list[list[str]]:
    path = tmp_path / "log-argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# -- the route ---------------------------------------------------------------------------------


def test_a_log_is_read_with_a_fixed_argv(center, project, cookie, tmp_path):  # noqa: F811
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["error"] is None
    assert calls(tmp_path) == [
        [
            "tool", "log", "tool/orders@1.0.0", "--project", project["path"],
            "--environment", "dev", "--since", "24h", "--limit", "100", "--json",
        ]
    ]  # fmt: skip
    log = data["data"]
    assert log["calls"] == [CALL] and log["credential"] == "console"
    assert log["phoenix_url"] == "http://127.0.0.1:6006" and log["content"] is False
    assert log["notes"] == ANSWER["notes"]


def test_every_option_reaches_the_cli(center, project, cookie, tmp_path):  # noqa: F811
    answer(tmp_path, {**ANSWER, "ref": "tool/orders", "content": True})
    query = "ref=tool%2Forders&since=2h&agent=northwind-support&limit=5&content=1"
    status, data, _ = read(center, project, cookie, query)
    assert status == 200 and data["error"] is None, data
    assert calls(tmp_path)[-1] == [
        "tool", "log", "tool/orders", "--project", project["path"], "--environment", "dev",
        "--since", "2h", "--limit", "5", "--agent", "northwind-support", "--content", "--json",
    ]  # fmt: skip


def test_the_answer_is_never_cached(center, project, cookie, tmp_path):  # noqa: F811
    _, first, _ = read(center, project, cookie)
    assert first["error"] is None
    refused = {"code": "SERVICES_DOWN", "message": "start it with `agentbreeder services up`"}
    answer(tmp_path, {"schema_version": 1, "error": refused}, 2)
    _, second, _ = read(center, project, cookie)
    assert second["data"] is None and second["stale"] is False
    assert second["error"] == refused
    assert len(calls(tmp_path)) == 2


@pytest.mark.parametrize(
    "query,code",
    [
        ("", "REF_INVALID"),
        ("ref=prompt%2Fx", "REF_INVALID"),
        ("ref=mcp%2Fx%401.0.0", "REF_INVALID"),
        ("ref=tool%2FOrders", "REF_INVALID"),
        ("ref=tool%2Forders%40v1", "REF_INVALID"),
        ("ref=tool%2Forders&since=1d", "SINCE_INVALID"),
        ("ref=tool%2Forders&since=--content", "SINCE_INVALID"),
        ("ref=tool%2Forders&limit=0", "LIMIT_INVALID"),
        ("ref=tool%2Forders&limit=1001", "LIMIT_INVALID"),
        ("ref=tool%2Forders&limit=-1", "LIMIT_INVALID"),
        ("ref=tool%2Forders&agent=--project", "NAME_INVALID"),
        ("ref=tool%2Forders&agent=Bad", "NAME_INVALID"),
        ("ref=tool%2Forders&content=yes", "CONTENT_INVALID"),
    ],
)
def test_bad_queries_never_reach_the_cli(center, project, cookie, tmp_path, query, code):  # noqa: F811
    status, data, _ = read(center, project, cookie, query)
    assert status == 400 and data["error"]["code"] == code
    assert calls(tmp_path) == []


def test_arguments_are_dropped_unless_asked_for(center, project, cookie, tmp_path):  # noqa: F811
    hostile = {**CALL, "args": {"q": "customer text"}, "error": "customer error"}
    answer(tmp_path, {**ANSWER, "calls": [hostile], "extra": "x"})
    _, data, _ = read(center, project, cookie)
    text = json.dumps(data)
    assert "customer" not in text and "extra" not in text
    assert set(data["data"]["calls"][0]) == set(CALL)


@pytest.mark.parametrize(
    "value,code",
    [
        ({"schema_version": 1, "error": {"code": "CONSOLE_ROLE_MISSING", "message": "no"}},
         "CONSOLE_ROLE_MISSING"),
        ({"schema_version": 1, "error": {"code": "bad code", "message": "no"}}, "TOOL_LOG_FAILED"),
        ({**ANSWER, "schema_version": 2}, "TOOL_LOG_INVALID"),
        ({**ANSWER, "ref": "tool/other@1.0.0"}, "TOOL_LOG_INVALID"),
        ({**ANSWER, "content": True}, "TOOL_LOG_INVALID"),
        ({**ANSWER, "credential": "owner"}, "TOOL_LOG_INVALID"),
        ({**ANSWER, "calls": "x"}, "TOOL_LOG_INVALID"),
        ({**ANSWER, "calls": [1]}, "TOOL_LOG_INVALID"),
        ({**ANSWER, "calls": [CALL] * 101}, "TOOL_LOG_INVALID"),
        ({**ANSWER, "window": None}, "TOOL_LOG_INVALID"),
        ([1, 2], "TOOL_LOG_INVALID"),
    ],
)  # fmt: skip
def test_failures_are_coded(center, project, cookie, tmp_path, value, code):  # noqa: F811
    answer(tmp_path, value, 2 if isinstance(value, dict) and "error" in value else 0)
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["data"] is None and data["error"]["code"] == code


def test_a_cli_failure_without_json_is_reported(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "log-stderr").write_text("[DEPENDENCY_MISSING] tool log needs psycopg\n")
    _, data, _ = read(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == "DEPENDENCY_MISSING"


def test_log_reads_are_bounded(center, project, cookie, tmp_path):  # noqa: F811
    for _ in range(server.MAX_TOOL_LOGS_IN_FLIGHT):
        assert center.tool_logs.acquire(blocking=False)
    try:
        status, data, _ = read(center, project, cookie)
        assert status == 400 and data["error"]["code"] == "TOOL_LOG_BUSY"
        assert calls(tmp_path) == []
    finally:
        for _ in range(server.MAX_TOOL_LOGS_IN_FLIGHT):
            center.tool_logs.release()
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["error"] is None


# -- the answer check --------------------------------------------------------------------------

REQUEST = {"ref": "tool/orders@1.0.0", "since": "24h", "agent": None, "limit": 100}


def test_hostile_values_become_null_and_limits_hold():
    hostile = {
        "at": "yesterday",
        "agent": "Bad Agent",
        "tool": "tool/orders",
        "function": "x;drop",
        "status": "maybe",
        "latency_ms": True,
        "turn_id": "XYZ",
        "trace_id": "a" * 65,
        "db_role": "Role",
        "args": {"q": "x" * (toollog.MAX_ARGS + 1)},
        "error": "e\x1b[31m‮" + "y" * 3000,
    }
    value = {
        **ANSWER,
        "content": True,
        "calls": [hostile, {**CALL, "latency_ms": toollog.MAX_LATENCY + 1, "args": [1]}],
        "notes": ["n\x00ote"] * 20 + [7],
        "phoenix_url": "http://evil.example:6006",
        "schema": "Public; drop",
        "phantom_rows": -1,
        "environment": 5,
    }
    log, problem = toollog.log_answer(value, {**REQUEST, "content": True})
    assert problem is None
    first, second = log["calls"]
    assert {key: first[key] for key in CALL} == dict.fromkeys(CALL)
    assert first["args"] is None
    assert len(first["error"]) == toollog.ERROR_LIMIT and "\x1b" not in first["error"]
    assert second["latency_ms"] is None and second["args"] is None and second["error"] is None
    assert log["notes"] == ["note"] * toollog.MAX_NOTES
    assert log["phoenix_url"] is None and log["schema"] is None and log["phantom_rows"] == 0
    assert log["environment"] is None


def test_arguments_over_the_cap_or_not_an_object_are_null():
    big = {"q": "x" * toollog.MAX_ARGS}
    calls = [{**CALL, "args": big}, {**CALL, "args": ["a"]}, {**CALL, "args": {"q": "ok"}}]
    log, problem = toollog.log_answer(
        {**ANSWER, "content": True, "calls": calls}, {**REQUEST, "content": True}
    )
    assert problem is None
    assert [call["args"] for call in log["calls"]] == [None, None, {"q": "ok"}]


def test_log_argv_never_takes_a_path_from_the_request():
    where = {"path": "/p", "environment": "dev"}
    request = toollog.log_request({"ref": ["tool/orders"]})
    assert request == {
        "ref": "tool/orders", "since": "24h", "agent": None, "limit": 100, "content": False,
    }  # fmt: skip
    assert toollog.log_argv(where, request) == [
        "tool", "log", "tool/orders", "--project", "/p", "--environment", "dev",
        "--since", "24h", "--limit", "100", "--json",
    ]  # fmt: skip


def test_rules_match_the_cli():
    """Local copies (the server does not load `agentbreeder.toollog`, which loads psycopg)."""
    for name in ("AGENT", "DB_ROLE", "FUNCTION", "HEX", "SCHEMA", "TOOL_REF"):
        assert getattr(toollog, name).pattern == getattr(cli_toollog, name).pattern, name
    assert toollog.TOOL_NAME.pattern == cli_toollog.TOOL_NAME.pattern.replace(
        f"(?P<name>{registry.NAME.pattern})", registry.NAME.pattern
    )
    since = f"{cli_toollog._RELATIVE.pattern}|{cli_toollog._ABSOLUTE.pattern}"
    assert toollog.SINCE.pattern == re.sub(r"\(\?P<\w+>([^()]*)\)", r"\1", since)
    assert toollog.SINCE.pattern == server.LOG_SINCE.pattern
    assert toollog.MAX_LIMIT == cli_toollog.MAX_LIMIT
    assert toollog.DEFAULT_LIMIT == cli_toollog.DEFAULT_LIMIT
    assert toollog.DEFAULT_SINCE == cli_toollog.DEFAULT_SINCE
    assert toollog.ERROR_LIMIT == cli_toollog.ERROR_LIMIT
    route = next(r for r in server.ROUTES if r.handler is server._tool_log)
    assert route.method == "GET"


# -- the real CLI's answers --------------------------------------------------------------------


def _cli_json(capsys, *argv) -> tuple[int, object]:
    code = cli.main(["tool", "log", *argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_the_real_cli_answer_passes(tmp_path, stacks, capsys, monkeypatch):  # noqa: F811
    stack_dir = _stack(tmp_path / "p")
    _written(stack_dir, secrets=SECRETS)
    rows = [_row(args=json.dumps({"q": AGENT_PASSWORD, "id": 7}), error="boom"), _row(ok=None)]
    _fetched(monkeypatch, _result(rows), [])
    for content in (False, True):
        argv = ["tool/orders@1.0.0", "--project", str(stack_dir), "--since", "2h", "--limit", "5"]
        code, body = _cli_json(capsys, *argv, *(["--content"] if content else []))
        assert code == 0
        request = toollog.log_request(
            {"ref": ["tool/orders@1.0.0"], "since": ["2h"], "limit": ["5"]}
            | ({"content": ["1"]} if content else {})
        )
        log, problem = toollog.log_answer(body, request)
        assert problem is None, body
        assert [call["status"] for call in log["calls"]] == ["ok", None]
        assert log["calls"][0]["at"] is not None and log["window"]["since"] is not None
        assert log["phoenix_url"] == "http://127.0.0.1:6006"
        if content:
            assert log["calls"][0]["args"] == {"q": "[redacted]", "id": 7}
            assert log["calls"][0]["error"] == "boom"
        else:
            assert "args" not in log["calls"][0]


def test_a_real_cli_refusal_passes(tmp_path, stacks, capsys):  # noqa: F811
    stack_dir = _stack(tmp_path / "p")
    code, body = _cli_json(capsys, "tool/orders", "--project", str(stack_dir))
    assert code == 2
    request = toollog.log_request({"ref": ["tool/orders"]})
    log, problem = toollog.log_answer(body, request)
    assert log is None and problem["code"] == "SERVICES_DOWN"
    assert "services up" in problem["message"]
