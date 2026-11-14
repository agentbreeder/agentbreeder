"""Try it (#823, M7): one chat turn through `invoke NAME - --json`, the message on stdin (S-12a),
uncached and never stored; the agent's answer allow-listed and checked before the browser."""

from __future__ import annotations

import json
import sys
import textwrap
import threading
import time

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)

from agentbreeder import agentfile, invoke
from agentbreeder.center import jobs, server, tryit

REPLY = {
    "reply": "Order 1001 shipped on 3 November.",
    "session_id": "s-1",
    "customer_id": "C-1001",
    "model": "chat",
    "framework": "google-adk",
    "runtime": "runtime/adk@1.0.6",
    "turn_id": "b" * 32,
    "trace_id": "a" * 32,
    "tools": ["orders"],
    "sources": ["Returns policy"],
    "model_calls": 2,
    "prompt_tokens": 812,
    "output_tokens": 64,
    "retrieval": "hybrid",
    "kb_searches": 1,
    "web": [],
    "tickets": [],
    "memory_saved": [],
}
ANSWER = {
    "schema_version": 1,
    "deployment": "c" * 32,
    "name": "support",
    "endpoint": "http://127.0.0.1:9001",
    "status": 200,
    "response": REPLY,
}

# `invoke` only: the argv and stdin are recorded; `invoke.json` and `invoke-exit` set the answer.
FAKE_INVOKE = textwrap.dedent(
    """
    import json, os, sys, time
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[0] == "invoke":
        open(marker + "/invoke-argv", "w").write(json.dumps(argv))
        open(marker + "/invoke-stdin", "w", encoding="utf-8").write(
            sys.stdin.buffer.read().decode("utf-8"))
        if os.path.exists(marker + "/invoke-sleep"):
            time.sleep(float(open(marker + "/invoke-sleep").read()))
        if os.path.exists(marker + "/invoke-stderr"):
            sys.stderr.write(open(marker + "/invoke-stderr").read())
            sys.exit(2)
        print(open(marker + "/invoke.json").read())
        code = marker + "/invoke-exit"
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
    path.write_text(FAKE_INVOKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    (tmp_path / "invoke.json").write_text(json.dumps(ANSWER))
    return [sys.executable, str(path)]


def send(center, project, cookie, body, name="support"):  # noqa: F811
    return api(center, "POST", f"/api/projects/{project['id']}/agents/{name}/try", body, cookie)


def answer(tmp_path, value, code=0):
    (tmp_path / "invoke.json").write_text(json.dumps(value))
    (tmp_path / "invoke-exit").write_text(str(code))


# -- the request ----------------------------------------------------------------------------------


def test_a_turn_sends_the_message_on_stdin_never_argv(center, project, cookie, tmp_path):  # noqa: F811
    message = "Where is order 1001? ünïcode ✓ --json"
    body = {"message": f"  {message}\n", "session": "s-1", "customer": "C-1001"}
    status, data, _ = send(center, project, cookie, body)
    assert status == 200 and data["error"] is None
    argv = json.loads((tmp_path / "invoke-argv").read_text())
    assert argv == [
        "invoke", "support", "-", "--stack", project["path"], "--environment", "dev", "--json",
        "--session", "s-1", "--customer", "C-1001",
    ]  # fmt: skip
    assert message not in " ".join(argv)
    assert (tmp_path / "invoke-stdin").read_text(encoding="utf-8") == message


def test_a_turn_without_session_or_customer_sends_neither(center, project, cookie, tmp_path):  # noqa: F811
    send(center, project, cookie, {"message": "hi", "session": "", "customer": None})
    argv = json.loads((tmp_path / "invoke-argv").read_text())
    assert "--session" not in argv and "--customer" not in argv


@pytest.mark.parametrize(
    "name,body,code",
    [
        ("support-candidate", {"message": "hi"}, "CANDIDATE_REFUSED"),
        ("support", {}, "MESSAGE_INVALID"),
        ("support", {"message": "   "}, "MESSAGE_INVALID"),
        ("support", {"message": 7}, "MESSAGE_INVALID"),
        ("support", {"message": "x" * 4001}, "MESSAGE_TOO_LONG"),
        ("support", {"message": "hi", "session": "a b"}, "SESSION_INVALID"),
        ("support", {"message": "hi", "session": "s" * 65}, "SESSION_INVALID"),
        ("support", {"message": "hi", "customer": ["C-1"]}, "CUSTOMER_INVALID"),
    ],
)
def test_bad_requests_never_reach_the_cli(center, project, cookie, tmp_path, name, body, code):  # noqa: F811
    status, data, _ = send(center, project, cookie, body, name)
    assert status == 400 and data["error"]["code"] == code
    assert not (tmp_path / "invoke-argv").exists()


@pytest.mark.parametrize("name", ["Support", "-x", "a" * 54, "a_b", "x-"])
def test_names_outside_the_agent_rule_are_refused(center, project, cookie, tmp_path, name):  # noqa: F811
    status, _, _ = send(center, project, cookie, {"message": "hi"}, name)
    assert status in (400, 404)
    assert not (tmp_path / "invoke-argv").exists()


def test_the_name_rule_and_limits_match_the_cli():
    assert tryit.AGENT_NAME.pattern == agentfile.NAME_PATTERN
    assert tryit.ID.pattern == invoke.ID.pattern
    assert tryit.MAX_MESSAGE == invoke.MAX_MESSAGE
    assert tryit.TIMEOUT > invoke.TIMEOUT
    # The route's segment admits every valid name (the handler applies the exact rule).
    route = next(r for r in server.ROUTES if r.handler is server._try_turn)
    assert route.regex.fullmatch(f"/api/projects/0123456789ab/agents/{'a' * 53}/try")


# -- the answer -----------------------------------------------------------------------------------


def test_a_reply_is_allow_listed_with_latency_and_seams(center, project, cookie):  # noqa: F811
    status, data, _ = send(center, project, cookie, {"message": "hi"})
    assert status == 200
    turn = data["data"]
    assert turn == {
        "deployment": "c" * 32,
        "reply": REPLY["reply"],
        "truncated": False,
        "session": "s-1",
        "customer": "C-1001",
        "turn": "b" * 32,
        "model": "chat",
        "runtime": "runtime/adk@1.0.6",
        "tools": ["orders"],
        "sources": ["Returns policy"],
        "model_calls": 2,
        "tokens": {"prompt": 812, "output": 64},
        "latency_ms": turn["latency_ms"],
        "trace": {"id": "a" * 32, "url": None},
        "cost": None,
    }
    assert isinstance(turn["latency_ms"], int) and turn["latency_ms"] >= 0


def test_odd_fields_are_dropped_not_passed_on(center, project, cookie, tmp_path):  # noqa: F811
    odd = dict(
        REPLY,
        reply="r" * (tryit.MAX_REPLY + 5),
        trace_id="javascript:alert(1)",
        turn_id="A" * 32,
        session_id="bad id",
        tools=["t" * 500, 7, None, "", *[f"x{i}" for i in range(80)]],
        sources="not a list",
        model_calls=True,
        prompt_tokens=-1,
        output_tokens="64",
        model={"nested": 1},
        html="<script>",
    )
    answer(tmp_path, dict(ANSWER, response=odd, deployment="../x"))
    turn = send(center, project, cookie, {"message": "hi"})[1]["data"]
    assert len(turn["reply"]) == tryit.MAX_REPLY and turn["truncated"] is True
    assert turn["trace"] == {"id": None, "url": None}
    assert turn["turn"] is None and turn["session"] is None and turn["deployment"] is None
    assert len(turn["tools"]) == tryit.MAX_ITEMS and turn["tools"][0] == "t" * tryit.MAX_ITEM
    assert turn["sources"] == []
    assert turn["model_calls"] is None
    assert turn["tokens"] == {"prompt": None, "output": None}
    assert turn["model"] is None
    assert "html" not in turn


def test_invokes_coded_error_is_shown_with_whether_it_was_sent(center, project, cookie, tmp_path):  # noqa: F811
    failure = {"code": "NOT_READY", "message": "support is starting, not ready", "sent": False}
    answer(tmp_path, {"schema_version": 1, "error": failure}, 2)
    status, data, _ = send(center, project, cookie, {"message": "hi"})
    assert status == 200
    assert data["error"] == {"code": "NOT_READY", "message": "support is starting, not ready"}
    assert data["data"]["sent"] is False and data["stale"] is False
    answer(tmp_path, {"schema_version": 1, "error": dict(failure, code="TIMEOUT", sent=True)}, 1)
    assert send(center, project, cookie, {"message": "hi"})[1]["data"]["sent"] is True
    answer(tmp_path, {"schema_version": 1, "error": {"code": "<b>", "message": None}}, 1)
    assert send(center, project, cookie, {"message": "hi"})[1]["error"] == {
        "code": "INVOKE_FAILED",
        "message": "invoke failed",
    }


def test_an_agent_error_is_a_sent_failure(center, project, cookie, tmp_path):  # noqa: F811
    answer(tmp_path, dict(ANSWER, status=502, response={"error": "model quota"}), 1)
    _, data, _ = send(center, project, cookie, {"message": "hi"})
    assert data["error"]["code"] == "AGENT_ERROR"
    assert data["error"]["message"] == "The agent answered 502: model quota"
    assert data["data"]["sent"] is True


@pytest.mark.parametrize("value", [[], "x", {"status": 200}, {"status": "200", "response": {}}])
def test_an_answer_off_contract_is_refused(center, project, cookie, tmp_path, value):  # noqa: F811
    answer(tmp_path, value)
    _, data, _ = send(center, project, cookie, {"message": "hi"})
    assert data["error"]["code"] == "TRY_INVALID" and data["data"] is None


def test_no_json_gives_the_coded_line_and_no_stale_answer(center, project, cookie, tmp_path):  # noqa: F811
    assert send(center, project, cookie, {"message": "hi"})[1]["error"] is None
    (tmp_path / "invoke-stderr").write_text("[NO_STATE] no state here\n")
    _, data, _ = send(center, project, cookie, {"message": "hi"})
    assert data["error"]["code"] == "NO_STATE"
    assert data["stale"] is False and data["data"]["sent"] is True  # never the previous reply


def test_a_usage_error_without_json_is_not_sent(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "invoke-stderr").write_text("usage: agentbreeder invoke ...\nerror: bad flag\n")
    _, data, _ = send(center, project, cookie, {"message": "hi"})
    assert data["error"]["code"] == "COMMAND_FAILED" and "(exit 2)" in data["error"]["message"]
    assert data["data"]["sent"] is False


@pytest.mark.parametrize(
    "problem,sent",
    [
        ({"code": "STOPPING", "message": "The console is stopping"}, False),
        ({"code": "COMMAND_FAILED", "message": "The command could not start"}, False),
        ({"code": "COMMAND_FAILED", "message": "The command failed (exit 2); run it"}, False),
        ({"code": "COMMAND_FAILED", "message": "The command failed (exit 1); run it"}, True),
        ({"code": "TIMEOUT", "message": "The command timed out"}, True),
        ({"code": "BAD_OUTPUT", "message": "unreadable"}, True),
    ],
)
def test_maybe_sent(problem, sent):
    assert tryit.maybe_sent(problem) is sent


def test_maybe_sent_matches_the_runners_own_wording(tmp_path):
    # `maybe_sent` reads Runner's messages; rewording them must fail here, not flip `sent`.
    assert tryit.maybe_sent(jobs.cli_error("usage: x", 2)) is False
    assert tryit.maybe_sent(jobs.cli_error("Traceback", 1)) is True
    (tmp_path / "c").mkdir(mode=0o700)
    runner = jobs.Runner(jobs.CenterState(tmp_path / "c"), tmp_path, [str(tmp_path / "missing")])
    problem = runner.read([], cache=False)["error"]
    assert problem["code"] == "COMMAND_FAILED" and tryit.maybe_sent(problem) is False


def test_turns_are_not_jobs_and_are_not_stored(center, project, cookie, tmp_path):  # noqa: F811
    private = "my-private-question-7f3a"
    send(center, project, cookie, {"message": private})
    assert center.runner.running() == [] and center.state.recent_jobs() == []
    for path in (tmp_path / "center").rglob("*"):
        if path.is_file():
            assert private.encode() not in path.read_bytes(), path
    assert center.runner.cache == {}


def test_turns_in_flight_are_capped(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "invoke-sleep").write_text("1.5")
    results = []

    def one():
        results.append(send(center, project, cookie, {"message": "hi"})[0])

    threads = [threading.Thread(target=one) for _ in range(server.MAX_TURNS_IN_FLIGHT)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 10
    while len(center.runner.children) < server.MAX_TURNS_IN_FLIGHT and time.monotonic() < deadline:
        time.sleep(0.02)
    status, data, _ = send(center, project, cookie, {"message": "hi"})
    assert status == 400 and data["error"]["code"] == "TRY_BUSY"
    for thread in threads:
        thread.join()
    assert results == [200] * server.MAX_TURNS_IN_FLIGHT
    assert send(center, project, cookie, {"message": "hi"})[0] == 200  # the slots came back


def test_a_turn_is_a_tracked_child_that_shutdown_stops(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "invoke-sleep").write_text("60")
    thread = threading.Thread(target=lambda: send(center, project, cookie, {"message": "hi"}))
    thread.start()
    deadline = time.monotonic() + 10
    while not center.runner.children and time.monotonic() < deadline:
        time.sleep(0.02)
    assert center.runner.children
    started = time.monotonic()
    center.runner.shutdown(grace=0.5)
    thread.join(10)
    assert not thread.is_alive() and time.monotonic() - started < 5


# -- Runner.read with input -----------------------------------------------------------------------


def test_read_with_input_writes_stdin_and_without_it_gives_devnull(tmp_path):
    script = tmp_path / "echo.py"
    script.write_text(
        "import json, sys\nprint(json.dumps({'in': sys.stdin.buffer.read().decode('utf-8')}))\n"
    )
    (tmp_path / "c").mkdir(mode=0o700)
    runner = jobs.Runner(jobs.CenterState(tmp_path / "c"), tmp_path, [sys.executable, str(script)])
    assert runner.read([], cache=False, input="héllo ✓")["data"] == {"in": "héllo ✓"}
    assert runner.read([], cache=False)["data"] == {"in": ""}
