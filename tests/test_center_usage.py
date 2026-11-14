# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Service usage in the console (#822, M6 console half, part 3): one long-lived
`services usage DIR --follow --json` child per project (S-10: stdin a pipe, its own process
group, a 0600 PID file swept at the next `up`, caps, stopped when idle and on shutdown), a
five-minute in-memory ring and `GET /api/projects/ID/usage`."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from test_center import (  # noqa: F401 (fixtures)
    _gone,
    _popen_ended,
    _stop_group,
    _wait_for_line,
    api,
    center,
    cookie,
    project,
    project_dir,
)

from agentbreeder import usage as cli_usage
from agentbreeder import watch
from agentbreeder.center import jobs, server, usage
from agentbreeder.center.jobs import ChildRecords, FollowRefused, Runner

T = "2026-11-14T12:00:00Z"
ROW = {
    "source": "service",
    "name": "litellm",
    "deployment": None,
    "cpu_percent": 1.5,
    "memory_used": 1000,
    "memory_limit": 8000,
    "memory_percent": 12.5,
    "network_rx": 10,
    "network_tx": 20,
    "block_read": 0,
    "block_write": None,
    "pids": 4,
}
AGENT_ROW = {**ROW, "source": "agent", "name": "northwind-support", "deployment": "a" * 32}
SAMPLE = {"schema_version": 1, "event": "sample", "time": T, "containers": [ROW, AGENT_ROW]}
DISK = {
    "schema_version": 1,
    "event": "disk",
    "as_of": T,
    "stale": False,
    "volumes": [{"name": "northwind-pg-data", "size": 4096}],
    "containers": [{"source": "service", "name": "litellm", "deployment": None, "size": 12}],
}

# `services usage DIR --follow --json`, as the CLI behaves for the console: samples until its
# stdin ends, then the end record; a "docker stats" grandchild in its process group that it
# stops itself. Marker files: `usage-error` (fail at once with a coded error), `usage-lines`
# (print these lines instead of the default records), `usage-stubborn` (never read stdin and
# ignore SIGTERM: what the PID-file sweep is for). It records its argv, PID and grandchild PID.
FAKE = textwrap.dedent(
    """
    import json, os, signal, subprocess, sys, threading, time
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[:2] == ["services", "usage"]:
        open(marker + "/usage-argv", "a").write(json.dumps(argv) + "\\n")
        if os.path.exists(marker + "/usage-error"):
            sys.stderr.write("noise /Users/someone/secret\\n")
            sys.stderr.write("[DOCKER_STATS_FAILED] the containers could not be listed\\n")
            print(json.dumps({"schema_version": 1, "error": {"code": "DOCKER_STATS_FAILED",
                                                             "message": "x"}}))
            sys.exit(2)
        stats = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        open(marker + "/usage-pids", "a").write(f"{os.getpid()} {stats.pid}\\n")
        if os.path.exists(marker + "/usage-stubborn"):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            print("stubborn", flush=True)
            while True:
                time.sleep(1)
        done = threading.Event()
        def watch_stdin():
            sys.stdin.read()
            done.set()
        threading.Thread(target=watch_stdin, daemon=True).start()
        lines_file = marker + "/usage-lines"
        if os.path.exists(lines_file):
            for line in open(lines_file).read().splitlines():
                print(line, flush=True)
        else:
            print(json.dumps(DISK), flush=True)
        try:  # as the CLI: a reader that left (BrokenPipeError) stops it too
            while not done.wait(0.1):
                if not os.path.exists(lines_file):
                    print(json.dumps(SAMPLE), flush=True)
        except BrokenPipeError:
            pass
        stats.kill()
        stats.wait()
        try:
            print(json.dumps({"schema_version": 1, "event": "end", "reason": "stdin",
                              "dropped": 0}), flush=True)
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
    path = tmp_path / "fake_usage.py"
    path.write_text(f"SAMPLE = {SAMPLE!r}\nDISK = {DISK!r}\n" + FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    return [sys.executable, str(path)]


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(usage, "REAP_EVERY", 0.05)


def _until(check, timeout: float = 15.0, what: str = "condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _pids(tmp_path) -> list[tuple[int, int]]:
    path = tmp_path / "usage-pids"
    if not path.exists():
        return []
    return [tuple(map(int, line.split())) for line in path.read_text().splitlines()]


def _argv_count(tmp_path) -> int:
    path = tmp_path / "usage-argv"
    return len(path.read_text().splitlines()) if path.exists() else 0


def _poll(center, project, cookie):
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/usage", cookie=cookie)
    assert status == 200
    return data


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# -- the contract -----------------------------------------------------------------------------


def test_the_mirrored_constants_match_the_clis():
    assert usage.SCHEMA_VERSION == cli_usage.SCHEMA_VERSION
    assert usage.MAX_CONTAINERS == cli_usage.MAX_CONTAINERS
    assert usage.NAME_LIMIT == cli_usage.NAME_LIMIT
    assert usage.SERVICE.pattern == watch._SERVICE.pattern
    assert usage.DEPLOYMENT.pattern == watch._DEPLOYMENT.pattern
    assert usage.ROLE.pattern == watch._ROLE.pattern
    assert usage.VOLUME.pattern == cli_usage._VOLUME.pattern
    from agentbreeder.commands import usage as command

    assert usage.TIME.fullmatch(command._now())


def test_parse_keeps_only_the_contracts_fields():
    extra = {**ROW, "labels": {"secret": "x"}, "mounts": ["/Users/someone"]}
    sidecar = {**AGENT_ROW, "role": "egress"}
    record = {**SAMPLE, "containers": [extra, sidecar], "host": "leak"}
    parsed = usage.parse(json.dumps(record))
    assert parsed == {"event": "sample", "time": T, "containers": [ROW, sidecar]}
    disk = {key: value for key, value in DISK.items() if key != "schema_version"}
    assert usage.parse(json.dumps(DISK)) == disk
    assert usage.parse(json.dumps({**DISK, "as_of": None, "stale": True}))["as_of"] is None
    assert usage.parse('{"schema_version": 1, "event": "end", "reason": "stdin"}') == {
        "event": "end"
    }


@pytest.mark.parametrize(
    "record",
    [
        "not json",
        "[]",
        json.dumps({**SAMPLE, "schema_version": 2}),
        json.dumps({**SAMPLE, "event": "other"}),
        json.dumps({**SAMPLE, "time": "yesterday"}),
        json.dumps({**SAMPLE, "containers": "x"}),
        json.dumps({**SAMPLE, "containers": [ROW] * 257}),
        json.dumps({**SAMPLE, "containers": ["x"]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "source": "host"}]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "name": "Bad Name"}]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "name": 3}]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "deployment": "a" * 32}]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "role": "egress"}]}),
        json.dumps({**SAMPLE, "containers": [{**AGENT_ROW, "name": "a‮b"}]}),
        json.dumps({**SAMPLE, "containers": [{**AGENT_ROW, "name": ""}]}),
        json.dumps({**SAMPLE, "containers": [{**AGENT_ROW, "name": "x" * 65}]}),
        json.dumps({**SAMPLE, "containers": [{**AGENT_ROW, "deployment": None}]}),
        json.dumps({**SAMPLE, "containers": [{**AGENT_ROW, "role": "Sidecar"}]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "cpu_percent": -1}]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "pids": True}]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "memory_used": "1 GB"}]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "memory_used": 1e19}]}),
        json.dumps({**SAMPLE, "containers": [{**ROW, "cpu_percent": "NAN"}]}).replace(
            '"NAN"', "NaN"
        ),
        json.dumps({**DISK, "stale": "no"}),
        json.dumps({**DISK, "as_of": 5}),
        json.dumps({**DISK, "volumes": [{"name": "../x", "size": 1}]}),
        json.dumps({**DISK, "volumes": ["x"]}),
        json.dumps({**DISK, "containers": [{**ROW, "size": -5}]}),
    ],
)
def test_parse_refuses_anything_off_contract(record):
    assert usage.parse(record) is None


# -- the route and the child ------------------------------------------------------------------


def test_a_poll_starts_one_child_and_returns_the_ring(center, project, cookie, tmp_path):
    first = _poll(center, project, cookie)
    assert first["error"] is None and first["data"]["running"] is True
    assert first["data"]["window"] == 300
    data = _until(
        lambda: (d := _poll(center, project, cookie)["data"])["samples"] and d["disk"] and d,
        what="a sample and a disk reading",
    )
    assert data["samples"][0] == {"time": T, "containers": [ROW, AGENT_ROW]}
    assert data["disk"] == {k: v for k, v in DISK.items() if k not in ("schema_version", "event")}
    assert data["dropped"] == 0
    assert _argv_count(tmp_path) == 1  # one child however many polls
    argv = json.loads((tmp_path / "usage-argv").read_text())
    assert argv == ["services", "usage", project["path"], "--follow", "--json"]
    (pid, _stats) = _pids(tmp_path)[0]
    records = json.loads((center.directory / jobs.CHILDREN_FILE).read_text())
    assert [row["pid"] for row in records] == [pid]
    assert stat.S_IMODE((center.directory / jobs.CHILDREN_FILE).stat().st_mode) == 0o600
    assert os.getpgid(pid) == pid  # its own process group
    center.runner.shutdown(grace=1.0)


def test_lines_off_contract_are_counted_not_passed(center, project, cookie, tmp_path):
    lines = ["garbage", json.dumps({**SAMPLE, "containers": [{**ROW, "name": "Bad"}]})]
    (tmp_path / "usage-lines").write_text("\n".join([*lines, json.dumps(SAMPLE)]) + "\n")
    data = _until(
        lambda: (d := _poll(center, project, cookie)["data"])["samples"] and d,
        what="the good sample",
    )
    assert data["dropped"] == 2 and len(data["samples"]) == 1
    center.runner.shutdown(grace=1.0)


def test_an_idle_child_stops_on_stdin_eof_and_restarts_on_the_next_poll(
    center, project, cookie, tmp_path, monkeypatch
):
    monkeypatch.setattr(usage, "IDLE_AFTER", 0.3)
    _poll(center, project, cookie)
    (pid, stats) = _until(lambda: _pids(tmp_path), what="the child")[0]
    _until(lambda: not _alive(pid) and not _alive(stats), what="the idle child to stop")
    _until(
        lambda: json.loads((center.directory / jobs.CHILDREN_FILE).read_text()) == [],
        what="its PID record to go",
    )
    feed = center.usage.feeds[project["id"]]
    _until(lambda: feed.stopped is feed.follow and feed.follow.pump.is_alive() is False)
    assert feed.problem is None and feed.ended is None  # stopped as idle: not a failure
    again = _poll(center, project, cookie)
    assert again["error"] is None and again["data"]["running"] is True
    assert again["data"]["samples"]  # the ring outlives the child
    _until(lambda: _argv_count(tmp_path) == 2, what="a new child")
    center.runner.shutdown(grace=1.0)


def test_a_failed_child_shows_its_coded_error_and_waits_before_a_restart(
    center, project, cookie, tmp_path, monkeypatch
):
    (tmp_path / "usage-error").write_text("")
    _poll(center, project, cookie)
    data = _until(
        lambda: (d := _poll(center, project, cookie))["error"] and d, what="the coded error"
    )
    assert data["error"] == {
        "code": "DOCKER_STATS_FAILED",
        "message": "the containers could not be listed",
    }
    assert data["stale"] is True and data["data"]["running"] is False
    assert data["data"]["dropped"] == 0  # the stdout copy of the error is not off-contract
    assert "secret" not in json.dumps(data)  # free stderr never reaches the browser (S-17)
    _poll(center, project, cookie)
    assert _argv_count(tmp_path) == 1  # not restarted on every poll
    monkeypatch.setattr(usage, "RESTART_AFTER", 0.0)
    (tmp_path / "usage-error").unlink()
    data = _poll(center, project, cookie)
    assert data["error"] is None and data["data"]["running"] is True
    _until(lambda: _argv_count(tmp_path) == 2, what="a new child")
    center.runner.shutdown(grace=1.0)


def test_the_cap_on_follows_refuses_with_a_code(center, project, cookie, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_FOLLOWS", 0)
    data = _poll(center, project, cookie)
    assert data["error"]["code"] == "FOLLOWS_FULL"
    assert data["data"]["running"] is False


def test_one_child_per_project_and_shutdown_stops_it_and_its_reader(
    center, project, cookie, tmp_path, monkeypatch
):
    for _ in range(3):
        _poll(center, project, cookie)
    (pid, stats) = _until(lambda: _pids(tmp_path), what="the child")[0]
    assert _argv_count(tmp_path) == 1
    assert len(center.runner.follows) == 1
    center.runner.shutdown(grace=1.0)
    _until(lambda: not _alive(pid) and not _alive(stats), what="shutdown to stop both")
    monkeypatch.setattr(usage, "RESTART_AFTER", 0.0)  # the stopped child is not what blocks
    feed = center.usage.feeds[project["id"]]
    _until(lambda: not feed.running(), what="its pump to record the end")
    data = _poll(center, project, cookie)
    assert data["error"]["code"] == "STOPPING"


def test_a_poll_does_not_keep_the_session_alive():
    route = next(r for r in server.ROUTES if r.handler is server._usage)
    assert route.poll is True
    found, match, _ = server.find_route("GET", "/api/projects/0123456789ab/usage")
    assert found is route and match["project"] == "0123456789ab"


def test_the_ring_keeps_five_minutes(tmp_path):
    now = [1000.0]
    feeds = usage.Usage(Runner(jobs.CenterState(tmp_path), tmp_path), clock=lambda: now[0])
    feed = usage.Feed()
    for _ in range(3):
        feeds._line(feed, json.dumps(SAMPLE))
        now[0] += 100
    feeds._line(feed, json.dumps(DISK))
    feeds._trim(feed, now[0])  # the first is exactly 300 s old: kept
    assert len(feed.samples) == 3
    feeds._trim(feed, now[0] + 1)
    assert len(feed.samples) == 2 and feed.disk is not None
    now[0] += 1000
    feeds._trim(feed, now[0])
    assert not feed.samples and feed.disk is not None  # disk keeps its own as_of


def test_end_follow_kills_a_child_that_ignores_its_stdin(tmp_path):
    runner = Runner(jobs.CenterState(tmp_path), tmp_path, prefix=[sys.executable])
    script = tmp_path / "stubborn.py"
    script.write_text(
        "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "print('up', flush=True)\ntime.sleep(60)\n"
    )
    lines, ended = [], []
    follow = runner.follow([str(script)], lines.append, ended.append)
    _until(lambda: lines == ["up"], what="its first line")
    started = time.monotonic()
    runner.end_follow(follow, grace=0.3)
    assert follow.process.poll() == -signal.SIGKILL
    assert time.monotonic() - started < 5
    _until(lambda: ended == [follow], what="on_end")
    assert follow.error()["code"] == "COMMAND_FAILED"
    assert runner.follows == set() and runner.records._load() == []


def test_a_follow_that_cannot_start_is_refused(tmp_path):
    runner = Runner(jobs.CenterState(tmp_path), tmp_path, prefix=[str(tmp_path / "missing")])
    with pytest.raises(FollowRefused) as refused:
        runner.follow(["x"], lambda line: None)
    assert refused.value.code == "COMMAND_FAILED"


def test_a_bad_line_handler_does_not_stop_the_pump(tmp_path):
    runner = Runner(jobs.CenterState(tmp_path), tmp_path, prefix=[sys.executable, "-c"])
    seen = []

    def handler(line):
        seen.append(line)
        raise RuntimeError("bad")

    follow = runner.follow(["print('a'); print('b')"], handler)
    follow.pump.join(timeout=10)
    assert seen == ["a", "b"] and follow.error() is None


# -- the PID file and the sweep (S-10) --------------------------------------------------------


def _group_leader(tmp_path, ignore_term: bool = False) -> subprocess.Popen:
    code = "import signal, time\n"
    if ignore_term:
        code += "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    code += "print('up', flush=True)\ntime.sleep(60)\n"
    child = subprocess.Popen(
        [sys.executable, "-c", code], start_new_session=True, stdout=subprocess.PIPE, text=True
    )
    assert child.stdout.readline() == "up\n"
    return child


def test_the_sweep_kills_only_matching_group_leaders(tmp_path):
    records = ChildRecords(tmp_path)
    leftover = _group_leader(tmp_path)
    stubborn = _group_leader(tmp_path, ignore_term=True)
    reused = _group_leader(tmp_path)  # its PID recorded with another start time
    member = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        records.add(leftover.pid, jobs.process_start(leftover.pid))
        records.add(stubborn.pid, jobs.process_start(stubborn.pid))
        records.add(reused.pid, "Thu Jan  1 00:00:00 1970")
        records.add(member.pid, jobs.process_start(member.pid))  # not a group leader
        records.add(2**22 + 7, "Thu Jan  1 00:00:00 1970")  # no such process
        assert stat.S_IMODE(records.path.stat().st_mode) == 0o600
        assert records.sweep(grace=0.3) == 2
        assert _popen_ended(leftover)(5) and _popen_ended(stubborn)(5)
        assert reused.poll() is None and member.poll() is None
        assert not records.path.exists()
    finally:
        for child in (leftover, stubborn, reused, member):
            child.kill()
            child.wait()


def test_the_pid_file_is_never_trusted_when_it_is_not_ours(tmp_path):
    records = ChildRecords(tmp_path)
    assert records.sweep() == 0  # no file
    for text in ("not json", json.dumps({"pid": 5}), json.dumps([{"pid": 1, "started": "x"}, 3])):
        records.path.write_text(text)
        records.path.chmod(0o600)  # private, so only the content is refused
        assert records._load() == []
    records.path.unlink()
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps([{"pid": 12345, "started": "x"}]))
    records.path.symlink_to(target)
    assert records._load() == []


def test_a_pid_file_that_cannot_be_written_still_runs_the_follow(tmp_path, monkeypatch):
    runner = Runner(jobs.CenterState(tmp_path), tmp_path, prefix=[sys.executable, "-c"])

    def refuse(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(jobs, "write_private", refuse)
    follow = runner.follow(["print('a')"], lambda line: None)
    follow.pump.join(timeout=10)
    assert follow.process.returncode == 0


def test_process_start_is_none_for_no_process():
    assert jobs.process_start(2**22 + 7) is None
    assert jobs.process_start(os.getpid())


# -- a killed console (the coordinator's orphan test) -----------------------------------------


def _usage_console(tmp_path, fake_cli, center_dir: Path) -> subprocess.Popen:
    """A real console process (`server.serve_process`) that registers a project and polls its
    usage once, then serves; it prints `USAGE <pid>` once the child runs."""
    folder = tmp_path / "p"
    folder.mkdir(exist_ok=True)
    (folder / "agent.yaml").write_text("kind: agent\n")
    (folder / "stack.yaml").write_text("kind: stack\n")
    script = tmp_path / "usage_console.py"
    script.write_text(
        "import sys, time\n"
        "from argparse import Namespace\n"
        "from pathlib import Path\n"
        "from agentbreeder.center import server\n"
        f"fake = {fake_cli!r}\n"
        "real = server.Center.__init__\n"
        "def init(self, directory, environment='dev', prefix=None):\n"
        "    real(self, directory, environment, fake)\n"
        "server.Center.__init__ = init\n"
        "orig = server.make_server\n"
        "def make(center, port):\n"
        "    s = orig(center, port)\n"
        f"    project = center.register(Path({str(folder)!r}))\n"
        "    center.usage.read(project)\n"
        "    feed = center.usage.feeds[project['id']]\n"
        "    print('USAGE', feed.follow.process.pid, flush=True)\n"
        "    return s\n"
        "server.make_server = make\n"
        "args = Namespace(dir=None, environment='dev', port=0, no_browser=True, yes=False)\n"
        "sys.exit(server.serve_process(args))\n"
    )
    env = {**os.environ, "AGENTBREEDER_CENTER_DIR": str(center_dir)}
    return subprocess.Popen(
        [sys.executable, str(script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        start_new_session=True,
    )


@pytest.mark.parametrize("stubborn", [False, True])
def test_a_killed_console_leaves_no_usage_follow(tmp_path, fake_cli, stubborn):
    """SIGKILL the console: its `services usage --follow` child sees its stdin end and stops
    with its reader. One that ignores its stdin is killed, with its group, by the next `up`."""
    if stubborn:
        (tmp_path / "usage-stubborn").write_text("")
    center_dir = tmp_path / "center"
    console = _usage_console(tmp_path, fake_cli, center_dir)
    child = stats = None
    second = None
    try:
        child = int(_wait_for_line(console.stdout, r"^USAGE ").split()[1])
        stats = _until(lambda: _pids(tmp_path), what="the reader")[0][1]
        os.killpg(console.pid, signal.SIGKILL)  # the console alone: the child leads its own group
        console.wait(timeout=10)
        if not stubborn:
            assert _gone(child, 10) and _gone(stats, 10), "the usage follow outlived the console"
            return
        time.sleep(1.0)
        assert _alive(child) and _alive(stats)  # what the sweep is for
        (tmp_path / "usage-stubborn").unlink()
        second = _usage_console(tmp_path, fake_cli, center_dir)
        _wait_for_line(second.stderr, r"^Stopped 1 process\(es\) left by a console")
        assert _gone(child, 10) and _gone(stats, 10), "the sweep left the leftover running"
        child = stats = None
    finally:
        for process in (console, second):
            if process is not None and process.returncode is None:
                _stop_group(process.pid, _popen_ended(process))
        for pid in (child, stats):
            if pid is not None:
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass


# -- defensive branches -----------------------------------------------------------------------


def test_an_on_end_that_raises_still_cleans_up(tmp_path):
    runner = Runner(jobs.CenterState(tmp_path), tmp_path, prefix=[sys.executable, "-c"])

    def broken(follow):
        raise RuntimeError("bad")

    follow = runner.follow(["print('a')"], lambda line: None, broken)
    follow.pump.join(timeout=10)
    assert not follow.pump.is_alive() and runner.follows == set()


def test_a_pump_that_fails_stops_the_child(tmp_path, monkeypatch):
    real = jobs.subprocess.Popen

    class Broken:
        closed = False

        def __iter__(self):
            raise OSError("read failed")

        def close(self):
            self.closed = True

    def popen(argv, *args, **kwargs):
        process = real(argv, *args, **kwargs)
        if argv[0] == sys.executable:  # the follow, not `ps`
            process.stdout = Broken()
        return process

    monkeypatch.setattr(jobs.subprocess, "Popen", popen)
    runner = Runner(jobs.CenterState(tmp_path), tmp_path, prefix=[sys.executable, "-c"])
    follow = runner.follow(["import time; time.sleep(60)"], lambda line: None)
    follow.pump.join(timeout=20)
    assert follow.process.poll() is not None and runner.follows == set()


def test_drain_tolerates_a_closed_pipe():
    class Closed:
        def __iter__(self):
            raise ValueError("I/O operation on closed file")

    keep = ["x"]
    jobs._drain(Closed(), keep)
    assert keep == ["x"]


def test_end_follow_tolerates_a_stdin_that_cannot_close(tmp_path):
    runner = Runner(jobs.CenterState(tmp_path), tmp_path, prefix=[sys.executable, "-c"])
    follow = runner.follow(["import time; time.sleep(60)"], lambda line: None)
    real = follow.process.stdin

    class Stuck:
        def close(self):
            raise OSError("broken pipe")

    follow.process.stdin = Stuck()
    runner.end_follow(follow, grace=0.3)
    real.close()
    assert follow.process.poll() is not None


def test_the_sweep_survives_signal_and_unlink_failures(tmp_path, monkeypatch):
    records = ChildRecords(tmp_path)
    leftover = _group_leader(tmp_path, ignore_term=True)
    try:
        records.add(leftover.pid, jobs.process_start(leftover.pid))
        monkeypatch.setattr(jobs.os, "killpg", _refuse)
        assert records.sweep(grace=0.1) == 0  # SIGTERM refused: nothing counted
        records.add(leftover.pid, jobs.process_start(leftover.pid))
        calls = []

        def term_only(pid, number):
            calls.append(number)
            if number != signal.SIGTERM:
                raise PermissionError("no")

        monkeypatch.setattr(jobs.os, "killpg", term_only)
        monkeypatch.setattr(jobs, "_group_alive", lambda pid: True)
        monkeypatch.setattr(Path, "unlink", _refuse)
        assert records.sweep(grace=0.1) == 1
        assert calls == [signal.SIGTERM, signal.SIGKILL]
    finally:
        leftover.kill()
        leftover.wait()


def _refuse(*args, **kwargs):
    raise PermissionError("no")


def test_an_ended_follow_that_is_no_longer_the_feeds_is_ignored(tmp_path):
    feeds = usage.Usage(Runner(jobs.CenterState(tmp_path), tmp_path))
    feed = usage.Feed()

    class Old:
        def error(self):
            return {"code": "X", "message": "y"}

    feed.token = object()
    feeds._ended(feed, object(), Old())
    assert feed.problem is None and feed.ended is None


def test_process_start_and_group_checks_fail_closed(monkeypatch):
    assert jobs._group_alive(2**22 + 7) is False  # no such group

    def broken(*args, **kwargs):
        raise OSError("no ps")

    monkeypatch.setattr(jobs.subprocess, "run", broken)
    assert jobs.process_start(os.getpid()) is None


def test_a_large_sample_passes_and_an_over_long_line_is_dropped(center, project, cookie, tmp_path):
    rows = [{**AGENT_ROW, "name": f"agent-{i}", "deployment": f"{i:032x}"} for i in range(40)]
    big = json.dumps({**SAMPLE, "containers": rows})
    assert 10_000 < len(big) < usage.MAX_LINE
    huge = json.dumps({**SAMPLE, "pad": "x" * (usage.MAX_LINE * 2)})
    (tmp_path / "usage-lines").write_text("\n".join([huge, big]) + "\n")
    data = _until(
        lambda: (d := _poll(center, project, cookie)["data"])["samples"] and d,
        what="the large sample",
    )
    assert len(data["samples"][0]["containers"]) == 40
    assert data["dropped"] == 0  # the over-long line never reached the parser
    center.runner.shutdown(grace=1.0)


def test_a_reader_left_by_a_crashed_cli_is_killed(tmp_path):
    """The CLI dies without stopping its reader (same group): the pump kills the group."""
    runner = Runner(jobs.CenterState(tmp_path), tmp_path, prefix=[sys.executable, "-c"])
    code = (
        "import os, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "os._exit(1)\n"
    )
    lines = []
    follow = runner.follow([code], lines.append)
    follow.pump.join(timeout=20)
    reader = int(lines[0])
    assert _gone(reader, 10), "the reader outlived its CLI"
    assert follow.process.stdout.closed and follow.process.stdin.closed


def test_a_pid_file_others_can_write_is_not_trusted(tmp_path):
    records = ChildRecords(tmp_path)
    records.add(os.getpid(), "x")
    assert records._load()
    records.path.chmod(0o620)
    assert records._load() == []


def test_an_unexpected_start_failure_never_leaves_the_feed_starting(
    center, project, cookie, monkeypatch
):
    def broken(*args, **kwargs):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(center.runner, "follow", broken)
    data = _poll(center, project, cookie)
    assert data["error"]["code"] == "COMMAND_FAILED" and data["data"]["running"] is False
    assert center.usage.feeds[project["id"]].starting is False


def test_a_thread_that_cannot_start_leaves_no_child(tmp_path, monkeypatch):
    runner = Runner(jobs.CenterState(tmp_path), tmp_path, prefix=[sys.executable, "-c"])
    real = jobs.threading.Thread.start
    started = []

    def refuse(self):
        if getattr(self, "_target", None) is jobs._reap_group:
            raise RuntimeError("can't start new thread")
        real(self)

    real_popen = jobs.subprocess.Popen

    def popen(argv, *args, **kwargs):
        process = real_popen(argv, *args, **kwargs)
        if argv[0] == sys.executable:
            started.append(process)
        return process

    monkeypatch.setattr(jobs.threading.Thread, "start", refuse)
    monkeypatch.setattr(jobs.subprocess, "Popen", popen)
    with pytest.raises(FollowRefused) as refused:
        runner.follow(["import time; time.sleep(60)"], lambda line: None)
    assert refused.value.code == "COMMAND_FAILED"
    assert started[0].poll() is not None
    assert runner.follows == set() and runner.records._load() == []
