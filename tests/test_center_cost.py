"""LLM cost in the console (#906, console half): `cost --stack DIR ... --json` as an uncached CLI
child, the window checked and worked out by the server, and only the allow-listed, type-checked
fields of its answer passed on (docs/cost.md, docs/commands/cost.md)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import textwrap
from datetime import UTC, date, datetime

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)

from agentbreeder import cli, gateway_keys, llmcost
from agentbreeder.center import cost, server

ID = "0123456789abcdef0123456789abcdef"
MODEL = {
    "model_group": "chat",
    "model": "openai/gpt-5.6",
    "calls": 5,
    "failed": 0,
    "prompt_tokens": 55,
    "completion_tokens": 100,
    "cost_usd": 0.00222,
    "unpriced": 0,
    "local": 0,
}
KEY = {
    "key_alias": f"agentbreeder-{ID}",
    "kind": "deployment",
    "id": ID,
    "name": "hello",
    "target": "local",
    "state": "ready",
    "environment": "dev",
    "calls": 5,
    "failed": 0,
    "prompt_tokens": 55,
    "completion_tokens": 100,
    "cost_usd": 0.00222,
    "unpriced": 0,
    "local": 0,
    "models": [MODEL],
}
TOTAL = {k: MODEL[k] for k in ("calls", "failed", "prompt_tokens", "completion_tokens")}
TOTAL |= {"cost_usd": 0.00222, "unpriced": 0, "local": 0}


def window(days=30):
    return cost.cost_request({"days": [str(days)]})


def answer_for(request, **changes):
    return {
        "schema_version": 1,
        "since": request["since"],
        "until": request["until"],
        "gateway": "litellm",
        "currency": "USD",
        "source": "litellm spend logs",
        "truncated": False,
        "keys": [KEY],
        "models": [MODEL],
        "total": TOTAL,
        **changes,
    }


# `cost` only: the argv is recorded; `cost.json` and `cost-exit` set the answer. `{since}` and
# `{until}` in cost.json are replaced by the argv's days, as the real CLI echoes them.
FAKE = textwrap.dedent(
    """
    import json, os, sys
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[:1] == ["cost"]:
        open(marker + "/cost-argv", "a").write(json.dumps(argv) + "\\n")
        if os.path.exists(marker + "/cost-stderr"):
            sys.stderr.write(open(marker + "/cost-stderr").read())
            sys.exit(1)
        text = open(marker + "/cost.json").read()
        text = text.replace("{since}", argv[argv.index("--since") + 1])
        print(text.replace("{until}", argv[argv.index("--until") + 1]))
        code = marker + "/cost-exit"
        sys.exit(int(open(code).read()) if os.path.exists(code) else 0)
    elif argv[0] == "doctor":
        print("[]")
    else:
        print("ok")
    """
)
TEMPLATE = {"since": "{since}", "until": "{until}"}


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_cli.py"
    path.write_text(FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    (tmp_path / "cost.json").write_text(json.dumps(answer_for(TEMPLATE)))
    return [sys.executable, str(path)]


def read(center, project, cookie, query="days=30"):  # noqa: F811
    return api(center, "GET", f"/api/projects/{project['id']}/llm-cost?{query}", cookie=cookie)


def answer(tmp_path, value, code=0):
    (tmp_path / "cost.json").write_text(json.dumps(value))
    (tmp_path / "cost-exit").write_text(str(code))


def calls(tmp_path) -> list[list[str]]:
    path = tmp_path / "cost-argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# -- the route ---------------------------------------------------------------------------------


def test_cost_is_read_with_a_fixed_argv(center, project, cookie, tmp_path):  # noqa: F811
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["error"] is None, data
    expected = window(30)
    assert calls(tmp_path) == [
        [
            "cost", "--stack", project["path"], "--environment", "dev",
            "--since", expected["since"], "--until", expected["until"], "--json",
        ]
    ]  # fmt: skip
    got = data["data"]
    assert got["currency"] == "USD" and got["source"] == "litellm spend logs"
    assert got["days"] == 30 and got["since"] == expected["since"]
    assert datetime.strptime(got["as_of"], "%Y-%m-%dT%H:%M:%SZ")
    # This answer is from a `cost` before #1056: no cached prompt tokens, so they are unknown.
    unknown = {"cached_prompt_tokens": None}
    model = {**MODEL, **unknown}
    assert got["keys"] == [{**KEY, **unknown, "models": [model], "models_cut": 0}]
    assert got["models"] == [model] and got["total"] == {**TOTAL, **unknown}
    assert got["keys_cut"] == 0 and got["models_cut"] == 0 and got["truncated"] is False


def test_the_default_window_is_thirty_days(center, project, cookie, tmp_path):  # noqa: F811
    status, data, _ = read(center, project, cookie, "")
    assert status == 200 and data["data"]["days"] == 30


def test_the_answer_is_never_cached(center, project, cookie, tmp_path):  # noqa: F811
    _, first, _ = read(center, project, cookie)
    assert first["error"] is None
    refused = {"code": "GATEWAY_UNREACHABLE", "message": "the gateway did not answer"}
    answer(tmp_path, {"schema_version": 1, "error": refused}, 2)
    _, second, _ = read(center, project, cookie)
    assert second["data"] is None and second["stale"] is False
    assert second["error"] == refused
    assert len(calls(tmp_path)) == 2


@pytest.mark.parametrize(
    "query", ["days=1", "days=31", "days=-7", "days=7%0A", "days=--json", "days=7.0"]
)
def test_bad_windows_never_reach_the_cli(center, project, cookie, tmp_path, query):  # noqa: F811
    status, data, _ = read(center, project, cookie, query)
    assert status == 400 and data["error"]["code"] == "COST_WINDOW"
    assert calls(tmp_path) == []


def test_unknown_keys_are_dropped(center, project, cookie, tmp_path):  # noqa: F811
    leaky = {**KEY, "master_key": "sk-marker", "models": [{**MODEL, "request_id": "r-marker"}]}
    answer(tmp_path, answer_for(TEMPLATE, keys=[leaky], url="http://gateway-marker"))
    _, data, _ = read(center, project, cookie)
    text = json.dumps(data)
    assert "marker" not in text
    assert set(data["data"]["keys"][0]) == set(KEY) | {"models_cut", "cached_prompt_tokens"}


def test_cached_prompt_tokens_are_passed_on(center, project, cookie, tmp_path):  # noqa: F811
    """#1056: `cost --json` has the cached part of the prompt tokens per key, model and total."""
    model = {**MODEL, "cached_prompt_tokens": 40}
    key = {**KEY, "cached_prompt_tokens": 40, "models": [model]}
    total = {**TOTAL, "cached_prompt_tokens": 40}
    answer(tmp_path, answer_for(TEMPLATE, keys=[key], models=[model], total=total))
    got = read(center, project, cookie)[1]["data"]
    assert got["keys"][0]["cached_prompt_tokens"] == 40
    assert got["keys"][0]["models"][0]["cached_prompt_tokens"] == 40
    assert got["models"][0]["cached_prompt_tokens"] == 40
    assert got["total"]["cached_prompt_tokens"] == 40


def test_text_loses_control_and_invisible_characters(center, project, cookie, tmp_path):  # noqa: F811
    odd = {**KEY, "name": "hel‮lo\x1b[31m", "models": [{**MODEL, "model": "gpt​\n5"}]}
    answer(tmp_path, answer_for(TEMPLATE, keys=[odd]))
    _, data, _ = read(center, project, cookie)
    key = data["data"]["keys"][0]
    assert key["name"] == "hello[31m" and key["models"][0]["model"] == "gpt5"


def test_eval_and_unknown_keys(center, project, cookie, tmp_path):  # noqa: F811
    bucket = {k: KEY[k] for k in TOTAL}
    judge = {"key_alias": "agentbreeder-eval-r1", "kind": "eval", "run": "r1", **bucket}
    other = {"key_alias": "agentbreeder-x", "kind": "unknown", "id": "x", **bucket}
    answer(tmp_path, answer_for(TEMPLATE, keys=[{**judge, "models": []}, {**other, "models": []}]))
    _, data, _ = read(center, project, cookie)
    first, second = data["data"]["keys"]
    assert first["kind"] == "eval" and first["run"] == "r1" and "id" not in first
    assert second["kind"] == "unknown" and second["id"] == "x" and "name" not in second


def test_long_lists_are_cut_and_counted(center, project, cookie, tmp_path):  # noqa: F811
    many = [{**KEY, "models": [MODEL] * (cost.SHOWN_MODELS + 3)}] * (cost.SHOWN_KEYS + 5)
    answer(tmp_path, answer_for(TEMPLATE, keys=many, truncated=True))
    _, data, _ = read(center, project, cookie)
    got = data["data"]
    assert len(got["keys"]) == cost.SHOWN_KEYS and got["keys_cut"] == 5
    assert got["keys"][0]["models_cut"] == 3 and got["truncated"] is True


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"currency": "EUR"},
        {"source": "invoice"},
        {"truncated": "no"},
        {"since": "2020-01-01"},
        {"until": "2020-01-01"},
        {"keys": "x"},
        {"keys": [1]},
        {"keys": [{**KEY, "kind": "person"}]},
        {"keys": [{**KEY, "key_alias": "sk-1234"}]},
        {"keys": [{**KEY, "id": "../x"}]},
        {"keys": [{**KEY, "calls": -1}]},
        {"keys": [{**KEY, "calls": True}]},
        {"keys": [{**KEY, "calls": 1.5}]},
        {"keys": [{**KEY, "failed": 9}]},
        {"keys": [{**KEY, "cost_usd": -0.1}]},
        {"keys": [{**KEY, "cost_usd": "0.1"}]},
        {"keys": [{**KEY, "cost_usd": None}]},
        {"keys": [{**KEY, "cost_usd": 10**400}]},
        {"keys": [{**KEY, "models": None}]},
        {"keys": [{**KEY, "models": [{**MODEL, "prompt_tokens": None}]}]},
        {"models": [{**MODEL, "cost_usd": 1e30}]},
        {"total": None},
        {"total": {**TOTAL, "unpriced": 99}},
        {"total": {**TOTAL, "local": 99}},  # more local calls than calls (#1025 O1)
        {"total": {**TOTAL, "local": -1}},
        # #1056: a cached figure is a count, at most the prompt tokens; null is not "none sent".
        {"total": {**TOTAL, "cached_prompt_tokens": 56}},
        {"total": {**TOTAL, "cached_prompt_tokens": None}},
        {"keys": [{**KEY, "cached_prompt_tokens": -1}]},
        {"models": [{**MODEL, "cached_prompt_tokens": "40"}]},
        {"models": [{**MODEL, "cached_prompt_tokens": True}]},
    ],
)
def test_bad_answers_are_invalid(center, project, cookie, tmp_path, change):  # noqa: F811
    answer(tmp_path, answer_for(TEMPLATE, **change))
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["data"] is None
    assert data["error"]["code"] == "COST_INVALID"


def test_nan_and_infinity_are_invalid():
    request = window(7)
    for value in (float("nan"), float("inf")):
        bad = answer_for(request, total={**TOTAL, "cost_usd": value})
        assert cost.cost_answer(bad, request) == (None, cost.INVALID)
    assert cost.cost_answer([1], request) == (None, cost.INVALID)


@pytest.mark.parametrize(
    "error,code",
    [
        ({"code": "COST_NO_GATEWAY", "message": "no gateway"}, "COST_NO_GATEWAY"),
        ({"code": "bad code", "message": "no"}, "COST_FAILED"),
        ({"code": "STATE_BUSY"}, "STATE_BUSY"),
    ],
)
def test_refusals_are_coded(center, project, cookie, tmp_path, error, code):  # noqa: F811
    answer(tmp_path, {"schema_version": 1, "error": error}, 2)
    _, data, _ = read(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == code
    assert data["error"]["message"]


def test_a_cli_failure_without_json_is_reported(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "cost-stderr").write_text("[COST_ERROR] the operator config could not be read\n")
    _, data, _ = read(center, project, cookie)
    assert data["data"] is None and data["error"]["code"] == "COST_ERROR"


def test_cost_reads_are_bounded(center, project, cookie, tmp_path):  # noqa: F811
    for _ in range(server.MAX_COST_IN_FLIGHT):
        assert center.cost_reads.acquire(blocking=False)
    try:
        status, data, _ = read(center, project, cookie)
        assert status == 400 and data["error"]["code"] == "COST_BUSY"
        assert calls(tmp_path) == []
    finally:
        for _ in range(server.MAX_COST_IN_FLIGHT):
            center.cost_reads.release()
    status, data, _ = read(center, project, cookie)
    assert status == 200 and data["error"] is None


# -- the pure checks ---------------------------------------------------------------------------


def test_the_window_ends_today_utc():
    assert cost.cost_request({"days": ["7"]}, today=date(2026, 11, 14)) == {
        "days": 7,
        "since": "2026-11-08",
        "until": "2026-11-14",
    }
    assert cost.cost_request({}, today=date(2026, 11, 14))["since"] == "2026-10-16"


def test_as_of_is_when_the_answer_arrived():
    request = window(7)
    now = datetime(2026, 11, 14, 22, 5, 9, 500, tzinfo=UTC)
    got, problem = cost.cost_answer(answer_for(request), request, now=now)
    assert problem is None and got["as_of"] == "2026-11-14T22:05:09Z"


def test_the_prefix_and_text_limit_match_the_cli():
    assert cost.PREFIX == gateway_keys.PREFIX
    assert cost.MAX_TEXT == llmcost.TEXT_LIMIT
    assert max(cost.WINDOWS) <= llmcost.MAX_DAYS


# -- the real CLI ------------------------------------------------------------------------------


class _Client:
    def __init__(self, rows):
        self.rows = rows

    def spend_logs(self, start, end):
        return self.rows, False


def test_a_real_report_passes_the_checks():
    """`llmcost.report`'s own answer, as `cost --json` prints it, passes unchanged."""
    request = window(30)
    rows = [
        {
            "request_id": f"r{n}",
            "metadata": {"user_api_key_alias": f"agentbreeder-{ID}"},
            "model": "openai/gpt-5.6",
            "model_group": "chat",
            "status": "success" if n else "failure",
            "prompt_tokens": 11,
            "completion_tokens": 20,
            "spend": 0.000444 if n else 0,
        }
        for n in range(3)
    ] + [
        {
            "metadata": {"user_api_key_alias": "agentbreeder-eval-run7"},
            "model": "m",
            "spend": None,
        },
        {"metadata": {"user_api_key_alias": "agentbreeder-gone"}, "model": "m", "spend": 0.1},
    ]
    deployments = [{"id": ID, "name": "hello", "target": "local", "state": "removed",
                    "environment": "dev"}]  # fmt: skip
    start, end = date.fromisoformat(request["since"]), date.fromisoformat(request["until"])
    printed = json.loads(json.dumps(llmcost.report(_Client(rows), deployments, start, end, "gw")))
    got, problem = cost.cost_answer(printed, request)
    assert problem is None, problem
    kinds = {key["kind"] for key in got["keys"]}
    assert kinds == {"deployment", "eval", "unknown"}
    hello = next(key for key in got["keys"] if key["kind"] == "deployment")
    assert hello["state"] == "removed" and hello["calls"] == 3 and hello["failed"] == 1
    assert got["total"]["unpriced"] == 1


def test_the_real_cli_refusal_passes_the_checks(tmp_path, monkeypatch):
    """`cost --stack DIR --json` with no operator config for the stack: its printed error object
    comes back as its own code."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AGENTBREEDER_HOME", str(tmp_path / ".agentbreeder"))
    stack = tmp_path / "stack"
    stack.mkdir()
    request = window(7)
    argv = cost.cost_argv({"path": str(stack), "environment": "dev"}, request)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = cli.main(argv)
        except SystemExit as stop:
            code = stop.code
    assert code == 2
    got, problem = cost.cost_answer(json.loads(out.getvalue()), request)
    assert got is None and problem["code"] != "COST_INVALID" and problem["message"]


def test_local_model_calls_reach_the_page():
    """#1025 O1: `local` (calls to Ollama, no price) passes through, bounded by `calls`."""
    request = window(7)
    local = {**TOTAL, "calls": 2, "failed": 0, "unpriced": 0, "local": 2, "cost_usd": 0.0}
    got, error = cost.cost_answer(answer_for(request, total=local), request)
    assert error is None and got["total"]["local"] == 2
    old = {k: v for k, v in TOTAL.items() if k != "local"}  # an older CLI without the field
    got, error = cost.cost_answer(answer_for(request, total=old), request)
    assert error is None and got["total"]["local"] == 0
