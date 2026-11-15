# ruff: noqa: F811  (pytest fixtures imported from test_center are parameters here)
"""Container events in the console (#821, M5 console half, part 3): one long-lived
`watch --stack DIR --json --record` child per project (the usage feed's plumbing: S-10 caps,
idle stop, shutdown), its checked status and events in memory, `GET /api/projects/ID/watch`,
and the `job_failed` events the job runner stores (#827)."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
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
    start,
    wait,
)

from agentbreeder import events, kubernetes
from agentbreeder import watch as cli_watch
from agentbreeder.center import jobs, server, usage, watch
from agentbreeder.commands import watch as watch_command

T = "2026-11-14T12:00:00Z"
DEPLOYMENT = "a" * 32
STATUS = {
    "schema_version": 1,
    "event": "status",
    "time": T,
    "services": [
        {"name": "postgres", "state": "running", "health": "healthy"},
        {"name": "litellm", "state": "exited", "health": None},
    ],
    "deployments": [
        {"id": DEPLOYMENT, "name": "northwind-support", "target": "local", "state": "ready"},
        {
            "id": "b" * 32,
            "name": "northwind-support",
            "target": "kubernetes",
            "state": "ready",
            "pod": {"running": False, "reason": "CrashLoopBackOff"},
        },
    ],
}
DIED = {
    "schema_version": 1,
    "event": "died",
    "time": T,
    "source": "service",
    "name": "postgres",
    "deployment": None,
    "exit_code": 0,
    "oom": False,
}
UNHEALTHY = {
    "schema_version": 1,
    "event": "unhealthy",
    "time": None,
    "source": "agent",
    "name": "northwind-support",
    "deployment": DEPLOYMENT,
    "role": "egress",
}
RESTARTED = {k: v for k, v in DIED.items() if k not in ("exit_code", "oom")}
RESTARTED["event"] = "restarted"
PLANTED = "planted-value-0123456789"  # a stand-in for a value a job's output might carry

# `watch` and the jobs, as the CLI behaves for the console. `watch`: the lines of `watch-lines`
# (default: a status record and a `died`), then waits for its stdin to end and prints the end
# record; `watch-error` fails at once with a coded error. Jobs (`build`, `reconcile`,
# `release`): what `job-mode` says, so the stored `job_failed` events can be checked.
FAKE = textwrap.dedent(
    """
    import json, os, sys, time
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    mode = open(marker + "/job-mode").read() if os.path.exists(marker + "/job-mode") else "ok"
    if argv[0] == "watch":
        open(marker + "/watch-argv", "a").write(json.dumps(argv) + "\\n")
        if os.path.exists(marker + "/watch-error"):
            sys.stderr.write("noise /Users/someone/" + PLANTED + "\\n")
            sys.stderr.write("[STACK_NOT_UP] this stack was never brought up\\n")
            print(json.dumps({"schema_version": 1, "error": {"code": "STACK_NOT_UP",
                                                             "message": "x"}}))
            sys.exit(2)
        lines = marker + "/watch-lines"
        if os.path.exists(lines):
            for line in open(lines).read().splitlines():
                print(line, flush=True)
        else:
            print(json.dumps(STATUS), flush=True)
            print(json.dumps(DIED), flush=True)
        sys.stdin.read()
        print(json.dumps({"schema_version": 1, "event": "end", "reason": "stdin",
                          "dropped": 0}), flush=True)
    elif argv[0] in ("build", "reconcile", "release"):
        print("working with " + PLANTED, flush=True)
        if mode == "coded":
            sys.stderr.write("[RECONCILE_FAILED] it failed with " + PLANTED + "\\n")
            sys.exit(2)
        if mode == "plain":
            sys.stderr.write("Traceback: " + PLANTED + "\\n")
            sys.exit(1)
        if mode == "gate":
            sys.exit(5)
        if mode == "approval":
            sys.exit(3)
        if mode == "sleep":
            try:
                print("started", flush=True)
                time.sleep(30)
            except KeyboardInterrupt:
                sys.stderr.write("[INTERRUPTED] stopped\\n")
                sys.exit(130)
        if mode == "sleep-gate":  # a release whose eval gate fails while it is being cancelled
            try:
                print("started", flush=True)
                time.sleep(30)
            except KeyboardInterrupt:
                sys.exit(5)
        print("done")
    elif argv[0] == "doctor":
        print("[]")
    else:
        print("ok")
    """
)


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_watch.py"
    header = f"STATUS = {STATUS!r}\nDIED = {DIED!r}\nPLANTED = {PLANTED!r}\n"
    path.write_text(header + FAKE)
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


def _poll(center, project, cookie):
    status, data, _ = api(center, "GET", f"/api/projects/{project['id']}/watch", cookie=cookie)
    assert status == 200
    return data


def _argv(tmp_path) -> list[list[str]]:
    path = tmp_path / "watch-argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _stored(center) -> list[dict]:
    path = center.directory / events.FILENAME
    if not path.exists():
        return []
    db = sqlite3.connect(path)
    try:
        columns = ", ".join(events.FIELDS)
        rows = db.execute(f"SELECT {columns} FROM events ORDER BY id").fetchall()
    finally:
        db.close()
    return [dict(zip(events.FIELDS, row, strict=True)) for row in rows]


def _without(record: dict, *keys: str) -> dict:
    return {k: v for k, v in record.items() if k not in keys}


# -- the contract -----------------------------------------------------------------------------


def test_the_mirrored_patterns_match_the_clis():
    assert watch.WORD.pattern == watch_command._WORD.pattern
    assert watch.POD_REASON.pattern == kubernetes._WORD.pattern
    assert watch.MAX_LINE == watch_command.LINE_LIMIT
    assert watch.SCHEMA_VERSION == cli_watch.SCHEMA_VERSION
    assert watch.TIME.fullmatch(watch_command._now())


def test_parse_keeps_only_the_contracts_fields():
    extra = {**STATUS, "host": "leak", "services": [{**STATUS["services"][0], "labels": "x"}]}
    parsed = watch.parse(json.dumps(extra))
    assert parsed["services"] == [STATUS["services"][0]]
    assert "host" not in parsed
    assert watch.parse(json.dumps(STATUS)) == _without(STATUS, "schema_version")
    assert watch.parse(json.dumps({**STATUS, "services": None}))["services"] is None
    unread_pod = {**STATUS["deployments"][1], "pod": None}
    assert watch.parse(json.dumps({**STATUS, "deployments": [unread_pod]}))["deployments"] == [
        unread_pod
    ]
    with_mounts = {**DIED, "mounts": ["/Users/someone"]}
    assert watch.parse(json.dumps(with_mounts)) == _without(DIED, "schema_version")
    assert watch.parse(json.dumps(UNHEALTHY))["role"] == "egress"
    assert watch.parse(json.dumps(RESTARTED))["event"] == "restarted"
    assert watch.parse(json.dumps({**DIED, "exit_code": None, "oom": True}))["oom"] is True
    assert watch.parse('{"schema_version": 1, "event": "end", "reason": "stdin"}') == {
        "event": "end"
    }
    assert watch.parse('{"schema_version": 1, "error": {"code": "X"}}') == {"event": "error"}


def _pod(value) -> str:
    return json.dumps({**STATUS, "deployments": [{**STATUS["deployments"][1], "pod": value}]})


def _deployment(**changes) -> str:
    return json.dumps({**STATUS, "deployments": [{**STATUS["deployments"][0], **changes}]})


def _service(**changes) -> str:
    row = {"name": "pg", "state": None, "health": None, **changes}
    return json.dumps({**STATUS, "services": [row]})


@pytest.mark.parametrize(
    "record",
    [
        "not json",
        "[]",
        "[" * 100000,
        json.dumps({**STATUS, "schema_version": 2}),
        json.dumps({**STATUS, "event": "other"}),
        json.dumps({**STATUS, "time": "yesterday"}),
        json.dumps({**STATUS, "services": "x"}),
        json.dumps({**STATUS, "services": [STATUS["services"][0]] * 257}),
        json.dumps({**STATUS, "services": ["x"]}),
        _service(name="Bad Name"),
        _service(state="RUNNING"),
        _service(health=3),
        json.dumps({**STATUS, "deployments": None}),
        json.dumps({**STATUS, "deployments": ["x"]}),
        _deployment(id="zz"),
        _deployment(name="a‮b"),
        _deployment(name=""),
        _deployment(target="aws"),
        _deployment(state="Ready!"),
        _pod("x"),
        _pod({}),
        _pod({"running": False, "reason": "a b"}),
        json.dumps({**DIED, "time": "now"}),
        json.dumps({**DIED, "source": "host"}),
        json.dumps({**DIED, "name": "Bad"}),
        json.dumps({**DIED, "deployment": DEPLOYMENT}),
        json.dumps({**DIED, "role": "egress"}),
        json.dumps({**DIED, "exit_code": -1}),
        json.dumps({**DIED, "exit_code": 1000}),
        json.dumps({**DIED, "exit_code": True}),
        json.dumps({**DIED, "exit_code": "0"}),
        json.dumps({**DIED, "oom": "no"}),
        json.dumps({**UNHEALTHY, "deployment": None}),
        json.dumps({**UNHEALTHY, "name": "x" * 65}),
        json.dumps({**UNHEALTHY, "role": "Sidecar"}),
    ],
)
def test_parse_refuses_anything_off_contract(record):
    assert watch.parse(record) is None


def test_what_the_cli_prints_passes():
    """The CLI's own records (`watch.Events`, `commands.watch._status`), not hand-written ones."""
    rows = {DEPLOYMENT: {"name": "northwind-support", "state": "ready"}}
    feed = cli_watch.Events("stack-1", lambda: rows, clock=lambda: 0.0)

    def record(action, labels, exit_code=None):
        attributes = {**labels, **({"exitCode": exit_code} if exit_code else {})}
        return {
            "Type": "container",
            "Action": action,
            "time": 1_763_000_000,
            "Actor": {"ID": "c" * 64, "Attributes": attributes},
        }

    service = {cli_watch.LABEL_STACK: "stack-1", cli_watch.LABEL_SERVICE: "postgres"}
    printed = feed.feed(record("die", service, "137")) + feed.feed(record("start", service))
    agent = {cli_watch.LABEL_DEPLOYMENT: DEPLOYMENT, cli_watch.LABEL_ROLE: "egress"}
    printed += feed.feed(record("health_status: unhealthy", agent))
    assert [event["event"] for event in printed] == ["died", "restarted", "unhealthy"]
    for event in printed:
        assert watch.parse(json.dumps(event)) is not None, event

    class Docker:
        def containers(self, state):
            return {"postgres": {"state": "running", "health": "healthy"}}

    deployments = {DEPLOYMENT: {"name": "northwind-support", "state": "ready", "manifest": "{}"}}
    status = watch_command._status(None, Docker(), deployments)
    assert watch.parse(json.dumps(status))["deployments"][0]["target"] == "local"


# -- the route and the child ------------------------------------------------------------------


def test_a_poll_starts_one_watch_and_returns_status_and_events(center, project, cookie, tmp_path):
    first = _poll(center, project, cookie)
    assert first["error"] is None and first["data"]["running"] is True
    data = _until(
        lambda: (d := _poll(center, project, cookie)["data"])["events"] and d["status"] and d,
        what="the status and the event",
    )
    assert data["status"] == _without(STATUS, "schema_version", "event")
    assert data["events"] == [{"id": 1, **_without(DIED, "schema_version")}]
    assert data["dropped"] == 0 and data["held"] == 0
    assert _argv(tmp_path) == [["watch", "--stack", project["path"], "--json", "--record"]]
    pid = center.watch.feeds[project["id"]].follow.process.pid
    assert os.getpgid(pid) == pid  # its own process group (S-10)
    rows = json.loads((center.directory / jobs.CHILDREN_FILE).read_text())
    assert [row["pid"] for row in rows] == [pid]
    center.runner.shutdown(grace=1.0)


def test_events_are_newest_first_with_growing_ids_and_bad_lines_counted(
    center, project, cookie, tmp_path
):
    lines = [
        json.dumps(DIED),
        "garbage",
        json.dumps({**DIED, "name": "Bad"}),
        json.dumps(UNHEALTHY),
        json.dumps(RESTARTED),
    ]
    (tmp_path / "watch-lines").write_text("\n".join(lines) + "\n")
    data = _until(
        lambda: len((d := _poll(center, project, cookie)["data"])["events"]) == 3 and d,
        what="three events",
    )
    assert [(e["id"], e["event"]) for e in data["events"]] == [
        (3, "restarted"),
        (2, "unhealthy"),
        (1, "died"),
    ]
    assert data["dropped"] == 2 and data["status"] is None
    center.runner.shutdown(grace=1.0)


def test_only_the_newest_events_are_kept(center, project, cookie, tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "MAX_EVENTS", 3)
    (tmp_path / "watch-lines").write_text((json.dumps(DIED) + "\n") * 5)
    data = _until(
        lambda: (
            (d := _poll(center, project, cookie)["data"])["events"]
            and d["events"][0]["id"] == 5
            and d
        ),
        what="the fifth event",
    )
    assert [event["id"] for event in data["events"]] == [5, 4, 3]
    center.runner.shutdown(grace=1.0)


def test_a_restart_during_a_services_up_job_is_held():
    """§14.4: the CLI cannot see the console's jobs; its `services up` restarts are not events."""

    class Runner:
        jobs: list = []

        def running(self):
            return self.jobs

    runner = Runner()
    feeds = watch.Watch(runner)
    feed = feeds.new_feed({"id": "p1"})
    runner.jobs = [{"project": "p1", "kind": "services-up"}]
    feeds.on_line(feed, json.dumps(RESTARTED))
    feeds.on_line(feed, json.dumps(DIED))  # a death is shown even then
    runner.jobs = [{"project": "p2", "kind": "services-up"}, {"project": "p1", "kind": "build"}]
    feeds.on_line(feed, json.dumps(RESTARTED))
    data = feeds.data(feed, 0.0)
    assert [event["event"] for event in data["events"]] == ["restarted", "died"]
    assert data["held"] == 1
    feeds.on_line(feed, '{"schema_version": 1, "event": "end"}')
    feeds.on_line(feed, '{"schema_version": 1, "error": {}}')
    assert feed.dropped == 0


def test_an_idle_watch_stops_and_its_events_outlive_it(
    center, project, cookie, tmp_path, monkeypatch
):
    monkeypatch.setattr(usage, "IDLE_AFTER", 0.3)
    _until(lambda: _poll(center, project, cookie)["data"]["events"], what="the event")
    feed = center.watch.feeds[project["id"]]
    _until(lambda: not feed.running(), what="the idle watch to stop")
    assert feed.problem is None and feed.ended is None  # stopped as idle: not a failure
    again = _poll(center, project, cookie)
    assert again["data"]["running"] is True and again["data"]["events"]
    _until(lambda: len(_argv(tmp_path)) == 2, what="a new watch")
    center.runner.shutdown(grace=1.0)


def test_a_failed_watch_shows_its_coded_error_only(center, project, cookie, tmp_path, monkeypatch):
    (tmp_path / "watch-error").write_text("")
    _poll(center, project, cookie)
    data = _until(lambda: (d := _poll(center, project, cookie))["error"] and d, what="the error")
    assert data["error"] == {
        "code": "STACK_NOT_UP",
        "message": "this stack was never brought up",
    }
    assert data["stale"] is True and data["data"]["running"] is False
    assert PLANTED not in json.dumps(data) and "someone" not in json.dumps(data)  # S-17
    _poll(center, project, cookie)
    assert len(_argv(tmp_path)) == 1  # not restarted on every poll
    monkeypatch.setattr(usage, "RESTART_AFTER", 0.0)
    (tmp_path / "watch-error").unlink()
    assert _poll(center, project, cookie)["data"]["running"] is True
    center.runner.shutdown(grace=1.0)


def test_watch_and_usage_share_the_cap_on_follows(center, project, cookie, monkeypatch):
    monkeypatch.setattr(jobs, "MAX_FOLLOWS", 0)
    data = _poll(center, project, cookie)
    assert data["error"]["code"] == "FOLLOWS_FULL" and data["data"]["running"] is False


def test_a_watch_that_cannot_start_names_the_command(center, project, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(center.runner, "follow", broken)
    answer = center.watch.read(project)
    assert answer["error"] == {
        "code": "COMMAND_FAILED",
        "message": "The watch command could not start",
    }


def test_the_watch_route_is_a_poll():
    route = next(r for r in server.ROUTES if r.handler is server._watch)
    assert route.poll is True and route.method == "GET"


# -- job_failed (#827) ---------------------------------------------------------------------------


def _job(center, project, cookie, kind, mode, tmp_path):
    (tmp_path / "job-mode").write_text(mode)
    return wait(center, start(center, project, cookie, kind)[1]["data"]["job"], cookie)


def test_a_failed_job_stores_its_code_and_nothing_else(center, project, cookie, tmp_path):
    job = _job(center, project, cookie, "reconcile", "coded", tmp_path)
    assert job["state"] == "failed"
    (event,) = _stored(center)
    assert {k: event[k] for k in ("kind", "source", "name", "exit_code", "code")} == {
        "kind": "job_failed",
        "source": "job",
        "name": "reconcile",
        "exit_code": 2,
        "code": "RECONCILE_FAILED",
    }
    assert event["project"] == events.project_dir(project["path"])
    assert json.loads(event["data"]) == {"cancelled": False}
    raw = (center.directory / events.FILENAME).read_bytes()
    assert PLANTED.encode() not in raw and b"it failed" not in raw
    mode = stat.S_IMODE((center.directory / events.FILENAME).stat().st_mode)
    assert mode == 0o600
    shown = events.read_recent(center.directory)
    assert shown[0]["message"] == "Job reconcile failed (exit 2) [RECONCILE_FAILED]"


def test_an_uncoded_failure_stores_no_code(center, project, cookie, tmp_path):
    _job(center, project, cookie, "build", "plain", tmp_path)
    (event,) = _stored(center)
    assert event["code"] is None and event["exit_code"] == 1
    assert PLANTED.encode() not in (center.directory / events.FILENAME).read_bytes()


def test_success_approval_and_a_failed_gate_store_nothing(center, project, cookie, tmp_path):
    _job(center, project, cookie, "reconcile", "ok", tmp_path)
    _job(center, project, cookie, "release", "approval", tmp_path)
    _job(center, project, cookie, "release", "gate", tmp_path)  # `release` stores gate_failed
    assert not (center.directory / events.FILENAME).exists()


def test_a_cancelled_job_is_stored_as_cancelled(center, project, cookie, tmp_path):
    (tmp_path / "job-mode").write_text("sleep")
    job = start(center, project, cookie, "reconcile")[1]["data"]["job"]
    _until(lambda: center.state.job(job)["lines"], what="the job's first line")
    api(center, "POST", f"/api/jobs/{job}/cancel", {}, cookie)
    assert wait(center, job, cookie)["exit_code"] == 130
    (event,) = _stored(center)
    assert json.loads(event["data"]) == {"cancelled": True} and event["code"] is None
    assert events.read_recent(center.directory)[0]["message"] == "Job reconcile was cancelled"


def test_a_store_that_cannot_open_loses_the_event_not_the_job(
    center, project, cookie, tmp_path, monkeypatch
):
    def refuse(*args, **kwargs):
        raise events.EventError("EVENTS_UNSAFE", "no")

    monkeypatch.setattr(events.EventStore, "open", refuse)
    job = _job(center, project, cookie, "reconcile", "coded", tmp_path)
    assert job["state"] == "failed"
    assert _stored(center) == []


def test_a_job_the_console_stops_is_stored_as_cancelled(center, project, cookie, tmp_path):
    (tmp_path / "job-mode").write_text("sleep")
    job = start(center, project, cookie, "reconcile")[1]["data"]["job"]
    _until(lambda: center.state.job(job)["lines"], what="the job's first line")
    assert center.runner.shutdown(grace=2.0) == [job]
    (event,) = _stored(center)
    assert json.loads(event["data"]) == {"cancelled": True} and event["code"] is None


def test_a_cancelled_release_that_its_gate_stopped_stores_nothing(
    center, project, cookie, tmp_path
):
    """Exit 5 is the gate's outcome (`release` stores `gate_failed`), not a cancel."""
    (tmp_path / "job-mode").write_text("sleep-gate")
    job = start(center, project, cookie, "release")[1]["data"]["job"]
    _until(lambda: center.state.job(job)["lines"], what="the job's first line")
    api(center, "POST", f"/api/jobs/{job}/cancel", {}, cookie)
    assert wait(center, job, cookie)["exit_code"] == 5
    assert _stored(center) == []
