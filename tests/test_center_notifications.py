"""Notifications (#827 M11, console half): `GET /api/events` reads `agentbreeder events --json`
with fixed argv and passes only allow-listed fields of the registered projects' events;
`POST /api/events/read` marks only ids the console lists. Conditions are the guide's (`next`)."""

from __future__ import annotations

import json
import sys
import textwrap

import pytest
from test_center import api, center, cookie, project, project_dir  # noqa: F401 (fixtures)

from agentbreeder import cli, events
from agentbreeder.center import notifications, server

# `events` records its argv and prints marker/events.json (or exits 2 with marker/events-stderr).
FAKE = textwrap.dedent(
    """
    import json, os, sys
    argv = sys.argv[1:]
    marker = os.environ["AGENTBREEDER_FAKE_DIR"]
    if argv[0] == "events":
        open(marker + "/events-argv", "a").write(json.dumps(argv) + "\\n")
        if os.path.exists(marker + "/events-stderr"):
            sys.stderr.write(open(marker + "/events-stderr").read())
            sys.exit(2)
        print(open(marker + "/events.json").read())
    elif argv[0] == "doctor":
        print("[]")
    else:
        print("ok")
    """
)


def event(project_path: str, **changes) -> dict:
    item = {
        "id": 7,
        "time": "2026-11-19T10:00:00Z",
        "project": project_path,
        "kind": "container_died",
        "source": "service",
        "name": "postgres",
        "deployment": None,
        "exit_code": 1,
        "code": None,
        "data": {"oom": False},
        "read": False,
        "message": "service postgres exited (exit 1)",
    }
    item.update(changes)
    return item


def gate(project_path: str, report: str | None, **data) -> dict:
    data = {"report": report} | data
    return event(
        project_path,
        id=8,
        kind="gate_failed",
        source="release",
        name="support",
        exit_code=None,
        data={"passed": 1, "cases": 3, "failed": ["c2", "c3"], "more": 0} | data,
        message="Eval gate failed for support: 1/3 passed; failed: c2, c3",
    )


def answer(*items) -> dict:
    return {"schema_version": 1, "events": list(items), "unread": 0, "unknown_ids": []}


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    path = tmp_path / "fake_cli.py"
    path.write_text(FAKE)
    monkeypatch.setenv("AGENTBREEDER_FAKE_DIR", str(tmp_path))
    (tmp_path / "events.json").write_text(json.dumps(answer()))
    return [sys.executable, str(path)]


def argvs(tmp_path) -> list[list[str]]:
    path = tmp_path / "events-argv"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def serve(tmp_path, *items):
    (tmp_path / "events.json").write_text(json.dumps(answer(*items)))


# -- pure ----------------------------------------------------------------------------------------


def test_the_rules_match_the_cli():
    assert notifications.KINDS == events.KINDS
    assert notifications.SOURCES == events.SOURCES
    assert notifications.TIME.pattern == events._TIME.pattern
    assert notifications.JOB.pattern == events._JOB.pattern
    assert notifications.NAME.pattern == events._NAME.pattern
    assert notifications.CODE.pattern == events._CODE_VALUE.pattern
    assert (notifications.MAX_CASES, notifications.MAX_CASE_ID) == (
        events.MAX_CASES,
        events.MAX_CASE_ID,
    )


def test_every_argv_parses_with_the_real_cli():
    args = cli._parser().parse_args(notifications.events_argv())
    assert args.json is True and args.project is None and args.limit == notifications.READ_LIMIT
    args = cli._parser().parse_args(notifications.mark_argv([3, 9]))
    assert args.mark_read == [3, 9] and args.project is None


@pytest.mark.parametrize(
    "body",
    [None, [], {}, {"ids": []}, {"ids": [0]}, {"ids": [-1]}, {"ids": [True]}, {"ids": ["1"]},
     {"ids": [1.0]}, {"ids": list(range(1, 52))}, {"ids": [1], "all": True}],
)  # fmt: skip
def test_a_bad_mark_request_is_refused(body):
    with pytest.raises(server.BadRequest) as caught:
        notifications.mark_request(body)
    assert caught.value.code == "EVENTS_READ_INVALID"


def test_a_mark_request_keeps_each_id_once():
    assert notifications.mark_request({"ids": [3, 3, 9]}) == [3, 9]


def test_only_registered_projects_and_checked_fields_pass():
    projects = {"/p": "abc"}
    data = answer(
        event("/p"),
        event("/elsewhere", id=9),
        event("/p", id=10, extra="x", message="m"),
    )
    shown = notifications.events_answer(data, projects)
    assert [e["id"] for e in shown["events"]] == [7, 10]
    assert shown["events"][0] == {
        "id": 7,
        "project_id": "abc",
        "time": "2026-11-19T10:00:00Z",
        "kind": "container_died",
        "source": "service",
        "name": "postgres",
        "exit_code": 1,
        "code": None,
        "read": False,
        "message": "service postgres exited (exit 1)",
    }
    assert "extra" not in shown["events"][1]
    assert shown["unread"] == 2 and shown["dropped"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "secret"},
        {"source": "release"},
        {"id": 0},
        {"id": True},
        {"time": "yesterday"},
        {"name": "bad name"},
        {"exit_code": 256},
        {"code": "lower"},
        {"read": "no"},
        {"message": ""},
        {"message": "x" * 301},
        {"message": "evil ‮ text"},
        {"message": "ansi \x1b[31m"},
        {"message": "zero​width"},
    ],
)
def test_an_event_breaking_a_rule_is_dropped_and_counted(changes):
    shown = notifications.events_answer(answer(event("/p", **changes)), {"/p": "abc"})
    assert shown["events"] == [] and shown["dropped"] == 1


@pytest.mark.parametrize("data", [None, [], {"schema_version": 2, "events": []}, {"events": {}}])
def test_an_envelope_off_contract_is_refused(data):
    assert notifications.events_answer(data, {"/p": "abc"}) is None


def test_a_gate_failure_in_the_default_folder_offers_the_summary_read():
    report = "/p/deploy/support.eval-20261113-213200-1a2b3c.json"
    shown = notifications.events_answer(answer(gate("/p", report)), {"/p": "abc"})
    assert shown["events"][0]["gate"] == {
        "cases": 3,
        "passed": 1,
        "failed": ["c2", "c3"],
        "more": 0,
        "report": report,
        "report_agent": "support",
        "report_run": "20261113-213200-1a2b3c",
    }


@pytest.mark.parametrize(
    "report",
    [
        None,
        "/elsewhere/deploy/support.eval-20261113-213200-1a2b3c.json",
        "/p/deploy/sub/support.eval-20261113-213200-1a2b3c.json",
        "/p/deployx/support.eval-20261113-213200-1a2b3c.json",
        "/p/deploy/Support.eval-20261113-213200-1a2b3c.json",
    ],
)
def test_any_other_report_path_is_text_only(report):
    shown = notifications.events_answer(answer(gate("/p", report)), {"/p": "abc"})
    assert shown["events"][0]["gate"]["report"] == report
    assert shown["events"][0]["gate"]["report_run"] is None


@pytest.mark.parametrize(
    "data",
    [
        {"passed": 4},
        {"failed": ["c‮"]},
        {"failed": ["x" * 81]},
        {"failed": ["c"] * 51},
        {"report": "relative/path.json"},
        {"report": "/p/\x1b.json"},
        {"more": -1},
    ],
)
def test_a_gate_failure_breaking_a_rule_is_dropped(data):
    shown = notifications.events_answer(
        answer(gate("/p", **({"report": None} | data))), {"/p": "abc"}
    )
    assert shown["events"] == []


# -- routes --------------------------------------------------------------------------------------


def test_events_are_read_with_fixed_argv_for_registered_projects(
    center,  # noqa: F811
    project,  # noqa: F811
    cookie,  # noqa: F811
    tmp_path,
):
    serve(tmp_path, event(project["path"]), event("/not/registered", id=9))
    status, data, _ = api(center, "GET", "/api/events", cookie=cookie)
    assert status == 200, data
    assert [e["id"] for e in data["data"]["events"]] == [7]
    assert data["data"]["events"][0]["project_id"] == project["id"]
    assert argvs(tmp_path) == [["events", "--limit", "200", "--json"]]
    # Polls within the coalescing window share one read; after it, a poll reads again.
    api(center, "GET", "/api/events", cookie=cookie)
    assert len(argvs(tmp_path)) == 1
    stamp, result, ended = center.events_last
    center.events_last = (stamp - server.EVENTS_COALESCE, result, ended)
    api(center, "GET", "/api/events", cookie=cookie)
    assert len(argvs(tmp_path)) == 2


def test_a_scoped_read_carries_only_that_projects_events(
    center,  # noqa: F811
    project,  # noqa: F811
    cookie,  # noqa: F811
    tmp_path,
):
    serve(tmp_path, event(project["path"]), event(project["path"], id=8, read=True))
    _, data, _ = api(center, "GET", f"/api/events?project={project['id']}", cookie=cookie)
    assert [e["id"] for e in data["data"]["events"]] == [7, 8]
    assert data["data"]["unread"] == 1
    status, data, _ = api(center, "GET", "/api/events?project=ffffffffffff", cookie=cookie)
    assert status == 400 and data["error"]["code"] == "PROJECT_NOT_FOUND"


def test_the_events_poll_does_not_refresh_the_idle_timer():
    route = next(r for r in server.ROUTES if r.pattern == r"/api/events")
    assert route.poll is True and route.method == "GET"


def test_a_cli_error_is_passed_on(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "events-stderr").write_text("[EVENTS_UNREADABLE] the store is busy\n")
    status, data, _ = api(center, "GET", "/api/events", cookie=cookie)
    assert status == 200 and data["data"] is None
    assert data["error"]["code"] == "EVENTS_UNREADABLE"


def test_an_answer_off_contract_is_events_invalid(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "events.json").write_text(json.dumps({"schema_version": 9}))
    _, data, _ = api(center, "GET", "/api/events", cookie=cookie)
    assert data["error"]["code"] == "EVENTS_INVALID"


def test_mark_read_sends_only_listed_ids(center, project, cookie, tmp_path):  # noqa: F811
    serve(tmp_path, event(project["path"]), event("/not/registered", id=9))
    status, data, _ = api(center, "POST", "/api/events/read", {"ids": [7]}, cookie)
    assert status == 200, data
    assert ["events", "--mark-read", "7", "--limit", "1", "--json"] in argvs(tmp_path)
    status, data, _ = api(center, "POST", "/api/events/read", {"ids": [9]}, cookie)
    assert status == 400 and data["error"]["code"] == "EVENTS_READ_INVALID"
    assert not any("9" in argv for argv in argvs(tmp_path) if "--mark-read" in argv)


def test_mark_read_needs_a_session(center, project, tmp_path):  # noqa: F811
    status, _, _ = api(center, "POST", "/api/events/read", {"ids": [7]})
    assert status in (401, 403)


def test_a_job_ending_makes_the_next_poll_read_again(center, project, cookie, tmp_path):  # noqa: F811
    serve(tmp_path, event(project["path"]))
    api(center, "GET", "/api/events", cookie=cookie)
    api(center, "GET", "/api/events", cookie=cookie)
    assert len(argvs(tmp_path)) == 1
    center.runner.ended += 1  # a job ended: it may have stored an event
    api(center, "GET", "/api/events", cookie=cookie)
    assert len(argvs(tmp_path)) == 2


def test_concurrent_polls_share_one_read(center, project, cookie, tmp_path):  # noqa: F811
    import threading

    serve(tmp_path, event(project["path"]))
    threads = [
        threading.Thread(target=lambda: api(center, "GET", "/api/events", cookie=cookie))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(argvs(tmp_path)) == 1


def test_a_scoped_mark_read_accepts_only_that_projects_ids(center, project, cookie, tmp_path):  # noqa: F811
    serve(tmp_path, event(project["path"]))
    status, data, _ = api(
        center, "POST", f"/api/events/read?project={project['id']}", {"ids": [7]}, cookie
    )
    assert status == 200, data
    assert data["data"]["events"][0]["id"] == 7


def test_a_failed_read_before_mark_read_passes_the_cli_error_on(center, project, cookie, tmp_path):  # noqa: F811
    (tmp_path / "events-stderr").write_text("[EVENTS_UNREADABLE] the store is busy\n")
    status, data, _ = api(center, "POST", "/api/events/read", {"ids": [7]}, cookie)
    assert data["error"]["code"] == "EVENTS_UNREADABLE", (status, data)
    assert not any("--mark-read" in argv for argv in argvs(tmp_path))
