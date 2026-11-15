# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Log follows in the console (#821, M5 console half, part 2): one `services logs ... --follow
--json` or `logs ID ... --follow --json` child per following tab (S-10 through `Runner.follow`),
owned by the session that started it, capped at 8 per session and 16 overall, stopped when idle,
on Stop and on shutdown; lines checked against the contract and held in a bounded ring."""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time

import pytest
from test_center import (  # noqa: F401 (fixtures)
    api,
    center,
    cookie,
    project,
    project_dir,
)

from agentbreeder.center import jobs, logfollow, server
from agentbreeder.center.jobs import Runner
from agentbreeder.commands import logs as cli_logs

DEPLOYMENT = "a" * 32

# `services logs DIR NAME --follow --json` and `logs ID --stack DIR ... --follow --json`, as the
# CLI behaves for the console: the tail first, then a line every 50 ms until its stdin ends, then
# the end record. Marker files: `follow-error` (fail at once with a coded error),
# `follow-lines` (print these lines, then wait for stdin), `follow-exit` (print two lines and
# end by itself, as when the container stops). It records its argv and PID.
FAKE = textwrap.dedent(
    """
    import json, os, sys, threading, time
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if "--follow" in argv:
        open(marker + "/follow-argv", "a").write(json.dumps(argv) + "\\n")
        open(marker + "/follow-pids", "a").write(f"{os.getpid()}\\n")
        if argv[0] == "services":
            label = {"source": "service", "target": "local", "name": argv[3]}
        else:
            label = {"source": "agent", "target": "local", "name": "demo",
                     "deployment": argv[1]}
        def say(text, level="info"):
            print(json.dumps({"schema_version": 1, **label, "line": text, "level": level}),
                  flush=True)
        def end(reason):
            print(json.dumps({"schema_version": 1, "event": "end", "reason": reason,
                              "dropped": False}), flush=True)
        if os.path.exists(marker + "/follow-error"):
            sys.stderr.write("noise /Users/someone/secret\\n")
            sys.stderr.write("[DOCKER_LOGS_FAILED] the log stream ended with exit code 1\\n")
            print(json.dumps({"schema_version": 1, "error": {"code": "DOCKER_LOGS_FAILED",
                                                             "message": "x"}}))
            sys.exit(2)
        if os.path.exists(marker + "/follow-exit"):
            say("first")
            say("second")
            end("container")
            sys.exit(0)
        done = threading.Event()
        def watch_stdin():
            sys.stdin.read()
            done.set()
        threading.Thread(target=watch_stdin, daemon=True).start()
        tail = int(argv[argv.index("--tail") + 1])
        try:
            lines_file = marker + "/follow-lines"
            if os.path.exists(lines_file):
                for line in open(lines_file).read().splitlines():
                    print(line, flush=True)
            else:
                for n in range(1, tail + 1):
                    say(f"old {n}")
            n = 0
            while not done.wait(0.05):
                if not os.path.exists(lines_file):
                    n += 1
                    say(f"new {n}", "error" if n % 2 else None)
            end("stdin")
        except BrokenPipeError:
            os.dup2(os.open(os.devnull, os.O_WRONLY), 1)
    elif argv[0] == "doctor":
        print("[]")
    else:
        print("ok")
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_follow.py"
    path.write_text(FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    return [sys.executable, str(path)]


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(logfollow, "REAP_EVERY", 0.05)


def _until(check, timeout: float = 15.0, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _start(center, project, cookie, kind="service", name="litellm", body=None):
    path = f"/api/projects/{project['id']}/logs/{kind}/{name}/follow"
    return api(center, "POST", path, {"tail": 3} if body is None else body, cookie)


def _read(center, cookie, identifier, after=0):
    return api(center, "GET", f"/api/follows/{identifier}?after={after}", cookie=cookie)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _pids(tmp_path) -> list[int]:
    path = tmp_path / "follow-pids"
    return [int(line) for line in path.read_text().split()] if path.exists() else []


def _argv(tmp_path) -> str | None:
    path = tmp_path / "follow-argv"
    return path.read_text().splitlines()[0] if path.exists() and path.read_text() else None


def _second_session(center) -> str:
    url, _ = center.mint_login()
    status, data, _ = api(center, "POST", "/api/session", {"token": url.split("#t=")[1]})
    assert status == 200
    return data["data"]["session"]


# -- the contract -----------------------------------------------------------------------------


def test_the_mirrored_constants_match_the_clis():
    assert logfollow.SCHEMA_VERSION == cli_logs.SCHEMA_VERSION
    assert set(logfollow.REASONS) == set(cli_logs.ENDED) - {"pipe"}
    assert logfollow.MAX_LINE > cli_logs.LINE_LIMIT  # a kept line fits in one record


def test_parse_keeps_only_line_and_level_of_the_followed_source():
    service = {"schema_version": 1, "source": "service", "target": "local", "name": "litellm"}
    record = {**service, "line": "hello", "level": "warning", "host": "leak"}
    assert logfollow.parse(json.dumps(record), "service", "litellm") == {
        "line": "hello",
        "level": "warning",
    }
    agent = {"schema_version": 1, "source": "agent", "name": "demo", "deployment": DEPLOYMENT}
    assert logfollow.parse(
        json.dumps({**agent, "line": "x", "level": None}), "deployment", DEPLOYMENT
    )
    end = {"schema_version": 1, "event": "end", "reason": "container", "dropped": True}
    assert logfollow.parse(json.dumps(end), "service", "litellm") == {
        "event": "end",
        "reason": "container",
        "dropped": True,
    }
    failure = {"schema_version": 1, "error": {"code": "X", "message": "y"}}
    assert logfollow.parse(json.dumps(failure), "service", "x") == {"event": "error"}


@pytest.mark.parametrize(
    "record",
    [
        "not json",
        "[1]",
        {"schema_version": 2, "source": "service", "name": "litellm", "line": "x", "level": None},
        {"schema_version": 1, "source": "service", "name": "postgres", "line": "x", "level": None},
        {"schema_version": 1, "source": "agent", "deployment": "b" * 32, "line": "x"},
        {"schema_version": 1, "source": "service", "name": "litellm", "line": 3, "level": None},
        {"schema_version": 1, "source": "service", "name": "litellm", "line": "x", "level": "x"},
        {"schema_version": 1, "event": "end", "reason": "pipe", "dropped": False},
        {"schema_version": 1, "event": "end", "reason": "stdin", "dropped": 0},
        {"schema_version": 1, "event": "sample"},
    ],
)
def test_parse_refuses_anything_off_contract(record):
    text = record if isinstance(record, str) else json.dumps(record)
    source, name = ("deployment", DEPLOYMENT) if "agent" in text else ("service", "litellm")
    assert logfollow.parse(text, source, name) is None


# -- the routes ---------------------------------------------------------------------------------


def test_a_follow_streams_its_lines_and_stop_ends_its_child(center, project, cookie, tmp_path):
    status, data, _ = _start(center, project, cookie)
    assert status == 200, data
    identifier = data["data"]["id"]
    argv = json.loads(_until(lambda: _argv(tmp_path), what="the argv"))
    assert argv == [
        "services",
        "logs",
        project["path"],
        "litellm",
        "--tail",
        "3",
        "--json",
        "--follow",
    ]
    seen: list[dict] = []
    after = 0

    def more():
        nonlocal after
        status, data, _ = _read(center, cookie, identifier, after)
        assert status == 200
        seen.extend(data["data"]["lines"])
        after = data["data"]["next"]
        return len(seen) >= 6

    _until(more, what="six lines")
    assert [row["line"] for row in seen[:4]] == ["old 1", "old 2", "old 3", "new 1"]
    assert [row["seq"] for row in seen] == list(range(1, len(seen) + 1))
    assert seen[3]["level"] == "error" and seen[4]["level"] is None
    assert set(seen[0]) == {"seq", "line", "level"}
    pid = _pids(tmp_path)[0]
    status, data, _ = api(center, "POST", f"/api/follows/{identifier}/stop", {}, cookie)
    assert status == 200 and data["data"] == {"stopped": True}

    def ended():
        data = _read(center, cookie, identifier, after)[1]["data"]
        return data if not data["running"] else None

    final = _until(ended, what="the follow to end")
    assert final["reason"] == "stdin" and final["stopped"] is True
    assert _read(center, cookie, identifier, after)[1]["error"] is None
    _until(lambda: not _alive(pid), what="the child to exit")


def test_a_deployment_follow_passes_its_stack_and_environment(center, project, cookie, tmp_path):
    status, data, _ = _start(
        center, project, cookie, "deployment", DEPLOYMENT, {"tail": 1, "since": "10m"}
    )
    assert status == 200, data
    argv = json.loads(_until(lambda: _argv(tmp_path), what="the argv"))
    assert argv == [
        "logs",
        DEPLOYMENT,
        "--stack",
        project["path"],
        "--environment",
        project["environment"],
        "--tail",
        "1",
        "--since",
        "10m",
        "--json",
        "--follow",
    ]
    first = _until(
        lambda: _read(center, cookie, data["data"]["id"])[1]["data"]["lines"], what="a line"
    )
    assert first[0]["line"] == "old 1"


@pytest.mark.parametrize(
    "body,code",
    [
        ({"tail": 0}, "TAIL_INVALID"),
        ({"tail": 10001}, "TAIL_INVALID"),
        ({"tail": "200"}, "BAD_REQUEST"),
        ({"tail": True}, "BAD_REQUEST"),
        ({"since": "yesterday"}, "SINCE_INVALID"),
        ({"since": 3}, "BAD_REQUEST"),
    ],
)
def test_a_bad_request_starts_nothing(center, project, cookie, tmp_path, body, code):
    status, data, _ = _start(center, project, cookie, body=body)
    assert status == 400 and data["error"]["code"] == code
    assert not (tmp_path / "follow-argv").exists()


def test_another_session_cannot_read_or_stop_a_follow(center, project, cookie):
    identifier = _start(center, project, cookie)[1]["data"]["id"]
    other = _second_session(center)
    assert _read(center, other, identifier)[0] == 404
    status, _, _ = api(center, "POST", f"/api/follows/{identifier}/stop", {}, other)
    assert status == 404
    assert _read(center, cookie, identifier)[0] == 200
    assert _read(center, cookie, "f" * 32)[0] == 404
    assert _read(center, cookie, identifier, "x")[1]["error"]["code"] == "AFTER_INVALID"


def test_a_follow_needs_a_session(center, project, cookie):
    identifier = _start(center, project, cookie)[1]["data"]["id"]
    assert _read(center, None, identifier)[0] == 401


def test_eight_follows_per_session_then_a_coded_refusal(center, project, cookie, monkeypatch):
    monkeypatch.setattr(logfollow, "MAX_PER_SESSION", 2)
    assert _start(center, project, cookie)[0] == 200
    second = _start(center, project, cookie)[1]["data"]["id"]
    status, data, _ = _start(center, project, cookie)
    assert status == 200 and data["data"] is None
    assert data["error"]["code"] == "FOLLOWS_FULL"
    assert "2 logs are already followed" in data["error"]["message"]
    # Another session has its own allowance; a stopped follow frees one.
    assert _start(center, project, _second_session(center))[1]["data"]["id"]
    api(center, "POST", f"/api/follows/{second}/stop", {}, cookie)
    _until(lambda: not _read(center, cookie, second)[1]["data"]["running"], what="the stop")
    assert _start(center, project, cookie)[1]["data"]["id"]


def test_the_console_wide_cap_refuses_with_its_code(center, project, cookie, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_FOLLOWS", 1)
    assert _start(center, project, cookie)[1]["data"]["id"]
    data = _start(center, project, cookie)[1]
    assert data["error"]["code"] == "FOLLOWS_FULL" and data["data"] is None
    assert len(center.log_follows.follows) == 1  # the refused one is not kept


def test_an_idle_follow_is_stopped_and_later_forgotten(center, project, cookie, monkeypatch):
    monkeypatch.setattr(logfollow, "IDLE_AFTER", 0.3)
    monkeypatch.setattr(logfollow, "FORGET_AFTER", 0.3)
    identifier = _start(center, project, cookie)[1]["data"]["id"]
    _until(lambda: identifier not in center.log_follows.follows, what="the follow to go")
    assert _read(center, cookie, identifier)[0] == 404
    _until(lambda: center.log_follows.reaper is None, what="the reaper to exit")


def test_a_child_that_ends_by_itself_reports_the_container_stopped(
    center, project, cookie, tmp_path
):
    (tmp_path / "follow-exit").touch()
    identifier = _start(center, project, cookie)[1]["data"]["id"]

    def ended():
        data = _read(center, cookie, identifier)[1]["data"]
        return data if not data["running"] else None

    data = _until(ended, what="the end")
    assert [row["line"] for row in data["lines"]] == ["first", "second"]
    assert data["reason"] == "container" and data["stopped"] is False


def test_a_failed_child_shows_only_its_coded_error(center, project, cookie, tmp_path):
    (tmp_path / "follow-error").touch()
    identifier = _start(center, project, cookie)[1]["data"]["id"]

    def failed():
        answer = _read(center, cookie, identifier)[1]
        return answer if not answer["data"]["running"] else None

    answer = _until(failed, what="the failure")
    assert answer["error"]["code"] == "DOCKER_LOGS_FAILED"
    assert answer["stale"] is True
    assert "/Users" not in json.dumps(answer)
    assert answer["data"]["dropped"] == 0  # the stdout error copy is not a dropped line


def test_lines_off_contract_are_counted_not_passed(center, project, cookie, tmp_path):
    good = {"schema_version": 1, "source": "service", "name": "litellm", "line": "ok"}
    lines = [
        "garbage",
        json.dumps({**good, "name": "postgres"}),
        json.dumps({**good, "level": None}),
    ]
    (tmp_path / "follow-lines").write_text("\n".join(lines) + "\n")
    identifier = _start(center, project, cookie)[1]["data"]["id"]
    data = _until(
        lambda: (d := _read(center, cookie, identifier)[1]["data"])["lines"] and d,
        what="the good line",
    )
    assert [row["line"] for row in data["lines"]] == ["ok"]
    assert data["dropped"] == 2


def test_a_shutdown_stops_every_follow(center, project, cookie, tmp_path):
    for _ in range(2):
        _start(center, project, cookie)
    _until(lambda: len(_pids(tmp_path)) == 2, what="two children")
    center.runner.shutdown(grace=1)
    for pid in _pids(tmp_path):
        _until(lambda pid=pid: not _alive(pid), what="the child to exit")


def test_the_follow_routes_are_listed_and_the_read_is_a_poll():
    routes = {route.sample: route for route in server.ROUTES}
    assert routes["/api/follows/" + "0" * 32].poll is True
    assert routes["/api/follows/" + "0" * 32 + "/stop"].method == "POST"
    for kind, name in (("service", "litellm"), ("deployment", "0" * 32)):
        assert routes[f"/api/projects/0123456789ab/logs/{kind}/{name}/follow"].method == "POST"


# -- the ring -----------------------------------------------------------------------------------


class _Runner:
    """A Runner stand-in: `follow` records the callbacks, so lines are fed by hand."""

    stopping = False

    def __init__(self):
        self.on_line = None
        self.ended = []

    def follow(self, argv, on_line, on_end=None, max_line=0):
        self.on_line, self.on_end = on_line, on_end
        return jobs.Follow(None, None, [])

    def end_follow(self, follow):
        self.ended.append(follow)


def _feed(follows, runner, count, text="x"):
    for n in range(count):
        record = {"schema_version": 1, "source": "service", "name": "litellm"}
        runner.on_line(json.dumps({**record, "line": f"{text}{n}", "level": None}))


def test_the_ring_is_bounded_and_a_slow_reader_learns_what_it_missed(monkeypatch):
    monkeypatch.setattr(logfollow, "RING_LINES", 5)
    runner = _Runner()
    follows = logfollow.LogFollows(runner)
    identifier = follows.start(b"s", "service", "litellm", ["x"])["data"]["id"]
    _feed(follows, runner, 8)
    data = follows.read(b"s", identifier, 0)["data"]
    assert [row["seq"] for row in data["lines"]] == [4, 5, 6, 7, 8]
    assert data["missed"] == 3 and data["next"] == 8
    again = follows.read(b"s", identifier, 8)["data"]
    assert again["lines"] == [] and again["missed"] == 0 and again["next"] == 8


def test_the_ring_is_bounded_by_bytes(monkeypatch):
    monkeypatch.setattr(logfollow, "RING_BYTES", 160)
    runner = _Runner()
    follows = logfollow.LogFollows(runner)
    identifier = follows.start(b"s", "service", "litellm", ["x"])["data"]["id"]
    _feed(follows, runner, 3, text="y" * 40)
    data = follows.read(b"s", identifier, 0)["data"]
    assert [row["seq"] for row in data["lines"]] == [2, 3] and data["missed"] == 1


def test_a_read_is_capped_and_says_there_is_more(monkeypatch):
    monkeypatch.setattr(logfollow, "READ_LINES", 2)
    runner = _Runner()
    follows = logfollow.LogFollows(runner)
    identifier = follows.start(b"s", "service", "litellm", ["x"])["data"]["id"]
    _feed(follows, runner, 5)
    first = follows.read(b"s", identifier, 0)["data"]
    assert [row["seq"] for row in first["lines"]] == [1, 2] and first["more"] is True
    rest = follows.read(b"s", identifier, 2)["data"]
    assert [row["seq"] for row in rest["lines"]] == [3, 4] and rest["more"] is True
    monkeypatch.setattr(logfollow, "READ_BYTES", 1)  # one line is always passed
    assert len(follows.read(b"s", identifier, 4)["data"]["lines"]) == 1


def test_a_start_that_raises_unexpectedly_is_a_coded_refusal():
    class Broken(_Runner):
        def follow(self, *args, **kwargs):
            raise RuntimeError("no thread")

    follows = logfollow.LogFollows(Broken())
    answer = follows.start(b"s", "service", "litellm", ["x"])
    assert answer["error"]["code"] == "COMMAND_FAILED" and follows.follows == {}


def test_a_real_runner_refuses_once_stopping(tmp_path):
    state = jobs.CenterState(tmp_path)
    runner = Runner(state, tmp_path, prefix=[sys.executable, "-c", "pass"])
    runner.stopping = True
    answer = logfollow.LogFollows(runner).start(b"s", "service", "litellm", ["x"])
    assert answer["error"]["code"] == "STOPPING"


def test_a_child_that_exits_cleanly_without_an_end_record_reads_as_stopped_or_ended():
    class Exited:
        def poll(self):
            return 0

    for stopping, reason in ((False, "container"), (True, "stdin")):
        runner = _Runner()
        follows = logfollow.LogFollows(runner)
        identifier = follows.start(b"s", "service", "litellm", ["x"])["data"]["id"]
        follows.follows[identifier].stopping = stopping
        runner.on_end(jobs.Follow(Exited(), None, []))
        answer = follows.read(b"s", identifier, 0)
        assert answer["data"]["running"] is False and answer["data"]["reason"] == reason
        assert answer["error"] is None


def test_a_stop_after_a_failure_keeps_the_coded_error():
    class Failed:
        returncode = 2

        def poll(self):
            return 2

    runner = _Runner()
    follows = logfollow.LogFollows(runner)
    identifier = follows.start(b"s", "service", "litellm", ["x"])["data"]["id"]
    runner.on_end(jobs.Follow(Failed(), None, ["[DOCKER_LOGS_FAILED] the stream ended"]))
    assert follows.stop(b"s", identifier)["data"] == {"stopped": True}
    answer = follows.read(b"s", identifier, 0)
    assert answer["error"]["code"] == "DOCKER_LOGS_FAILED"
    assert answer["data"]["stopped"] is False and runner.ended == []


def test_idle_follows_are_stopped_on_their_own_threads(monkeypatch):
    monkeypatch.setattr(logfollow, "IDLE_AFTER", 0.0)
    stopped = []

    class Slow(_Runner):
        def end_follow(self, follow):
            stopped.append(follow)
            time.sleep(1.0)

    runner = Slow()
    follows = logfollow.LogFollows(runner)
    for _ in range(3):
        follows.start(b"s", "service", "litellm", ["x"])
    _until(lambda: len(stopped) == 3, timeout=0.9, what="three stops at once")
