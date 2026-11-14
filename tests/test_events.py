"""Stored events (#827, M11; guided §14.4, §14.9 R1, R8): fixed kinds, no free text, newest 500."""

from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentbreeder import cli, events, watch
from agentbreeder.commands import events as events_command

PROJECT = "/work/helper"
DEPLOYMENT = "d" * 32
CONTAINER = "c" * 64
STACK = "s" * 12


@pytest.fixture
def store(tmp_path):
    handle = events.EventStore.open(tmp_path / "center")
    yield handle
    handle.close()


def died(name="postgres", exit_code=1, oom=False):
    return {
        "schema_version": 1,
        "event": "died",
        "time": "2026-11-13T10:00:00Z",
        "source": "service",
        "name": name,
        "deployment": None,
        "exit_code": exit_code,
        "oom": oom,
    }


# -- store -------------------------------------------------------------------------------------


def test_store_is_private(tmp_path, store):
    directory = tmp_path / "center"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / events.FILENAME).stat().st_mode) == 0o600


def test_store_refuses_a_symlinked_directory(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    with pytest.raises(events.EventError) as error:
        events.EventStore.open(tmp_path / "link")
    assert error.value.code == "EVENTS_UNSAFE"


def test_append_and_recent_newest_first(store):
    first = store.append(events.from_watch(died("postgres"), PROJECT))
    second = store.append(events.from_watch(died("litellm", 137, oom=True), PROJECT))
    rows = store.recent()
    assert [row["id"] for row in rows] == [second, first]
    assert rows[0]["kind"] == "container_died"
    assert rows[0]["message"] == "service litellm exited (exit 137, out of memory)"
    assert rows[0]["read"] is False


def test_keeps_the_newest_500(store):
    for _ in range(events.KEEP + 7):
        store.append(events.from_watch(died(), PROJECT))
    rows = store.recent(limit=events.KEEP + 50)
    assert len(rows) == events.KEEP
    assert rows[-1]["id"] == 8  # the 7 oldest are gone


def test_recent_filters_by_project_and_unread(store):
    a = store.append(events.from_watch(died(), PROJECT))
    store.append(events.from_watch(died(), "/work/other"))
    assert [r["id"] for r in store.recent(project=PROJECT)] == [a]
    store.mark_read([a])
    assert store.recent(project=PROJECT, unread=True) == []
    assert store.unread_count() == 1
    store.mark_read(None, project="/work/other")
    assert store.unread_count() == 0


def test_reading_a_missing_store_creates_nothing(tmp_path):
    assert events.read_recent(tmp_path / "absent") == []
    assert not (tmp_path / "absent").exists()


def test_a_newer_schema_is_refused(tmp_path, store):
    store.db.execute(f"PRAGMA user_version = {events.SCHEMA_VERSION + 1}")
    store.close()
    with pytest.raises(events.EventError) as error:
        events.EventStore.open(tmp_path / "center")
    assert error.value.code == "EVENTS_TOO_NEW"


def test_append_validates_every_field(store):
    good = events.from_watch(died(), PROJECT)
    bad = [
        {**good, "kind": "anything"},
        {**good, "name": "x\ny"},
        {**good, "code": "lowercase"},
        {**good, "exit_code": 99999},
        {**good, "deployment": "nothex"},
        {**good, "data": {"oom": "yes"}},
        {**good, "data": {"oom": False, "message": "free text"}},
        {**good, "project": "relative/path"},
        {**good, "extra": 1},
    ]
    for event in bad:
        with pytest.raises(events.EventError) as error:
            store.append(event)
        assert error.value.code == "EVENT_INVALID"
    assert store.recent() == []


def test_no_message_column_is_stored(store):
    store.append(events.from_watch(died(), PROJECT))
    columns = [row[1] for row in store.db.execute("PRAGMA table_info(events)")]
    assert "message" not in columns


# -- builders ----------------------------------------------------------------------------------


def test_from_watch_maps_died_and_unhealthy_and_drops_restarted():
    agent = {
        **died(),
        "event": "unhealthy",
        "source": "agent",
        "name": "helper",
        "deployment": DEPLOYMENT,
    }
    agent.pop("exit_code"), agent.pop("oom")
    event = events.from_watch(agent, PROJECT)
    assert event["kind"] == "container_unhealthy" and event["deployment"] == DEPLOYMENT
    assert events.render(event) == "agent helper became unhealthy"
    assert events.from_watch({**died(), "event": "restarted"}, PROJECT) is None
    assert events.from_watch({**died(), "event": "status"}, PROJECT) is None


def test_job_failed_template_and_code():
    event = events.job_failed("services-up", PROJECT, 2, "[DOCKER_UNAVAILABLE] docker is not up")
    assert event["code"] == "DOCKER_UNAVAILABLE" and event["exit_code"] == 2
    assert events.render(event) == "Job services-up failed (exit 2) [DOCKER_UNAVAILABLE]"
    cancelled = events.job_failed("build", PROJECT, None, None, cancelled=True)
    assert events.render(cancelled) == "Job build was cancelled"


def test_job_failed_stores_no_free_text():
    event = events.job_failed("build", PROJECT, 1, "Traceback: secret-value at /home/x")
    assert event["code"] is None
    assert "secret" not in json.dumps(event)
    # A code-shaped prefix that does not fit the pattern is not a code.
    assert events.job_failed("build", PROJECT, 1, "[ab] x")["code"] is None
    assert events.job_failed("build", PROJECT, 1, "[" + "A" * 41 + "] x")["code"] is None


def test_job_failed_skips_success_approval_and_gate():
    assert events.job_failed("build", PROJECT, 0, None) is None
    assert events.job_failed("release", PROJECT, 3, "[APPROVAL_REQUIRED] x") is None
    assert events.job_failed("release", PROJECT, 5, None) is None  # stored as gate_failed
    assert events.job_failed("build", PROJECT, 5, None) is not None


def test_gate_failed_from_release_details():
    details = {
        "agent": "helper",
        "eval": {"passed": 7, "cases": 10},
        "eval_report": "/work/helper/deploy/helper.eval-1.json",
        "failed_cases": [
            {"id": f"case-{n}", "failed_checks": ["x"], "reason": "the judge said a lot"}
            for n in range(3)
        ]
        + [{"id": "bad\x1b[31mid" + "z" * 100, "failed_checks": [], "reason": ""}],
    }
    event = events.gate_failed(PROJECT, details)
    data = event["data"]
    assert (data["passed"], data["cases"]) == (7, 10)
    assert data["failed"][:3] == ["case-0", "case-1", "case-2"]
    assert len(data["failed"][3]) <= 80 and "\x1b" not in data["failed"][3]
    assert "judge" not in json.dumps(event)
    assert data["report"] == "/work/helper/deploy/helper.eval-1.json"
    message = events.render(event)
    assert message.startswith("Eval gate failed for helper: 7/10 passed; failed: case-0, case-1")


def test_gate_failed_caps_the_case_list():
    details = {
        "agent": "helper",
        "eval": {"passed": 0, "cases": 120},
        "eval_report": None,
        "failed_cases": [{"id": f"c{n}", "failed_checks": [], "reason": ""} for n in range(120)],
    }
    event = events.gate_failed(PROJECT, details)
    assert len(event["data"]["failed"]) == events.MAX_CASES
    assert event["data"]["more"] == 120 - events.MAX_CASES
    assert events.render(event).endswith("c4 (+115 more)")  # 5 shown, the rest counted


def test_record_never_raises(tmp_path, monkeypatch):
    (tmp_path / "file").write_text("")
    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "file" / "below"))
    assert events.record(events.from_watch(died(), PROJECT)) is False
    assert events.record(None) is False


# -- acceptance (fakes; the real-Docker run waits for the owner's prune decision) ---------------


def _record(kind_action: tuple[str, str], container=CONTAINER, exit_code="0", labels=None):
    kind, action = kind_action
    base = (
        {"io.agentbreeder.stack": STACK, "com.docker.compose.service": "postgres"}
        if kind == "service"
        else {"io.agentbreeder.deployment": DEPLOYMENT, "io.agentbreeder.role": "agent"}
    )
    attributes = {**base, **(labels or {}), "exitCode": exit_code}
    return {
        "Type": "container",
        "Action": action,
        "time": 1_763_000_000,
        "Actor": {"ID": container, "Attributes": attributes},
    }


def _replay(store, sequence, deployments):
    feed = watch.Events(STACK, lambda: deployments, clock=lambda: 0.0)
    for record in sequence:
        for event in feed.feed(record):
            events.record(events.from_watch(event, PROJECT), store=store)


def test_clean_release_and_services_down_store_zero_events(store):
    # release: candidate stopped and removed (`docker stop`, then `rm`), production replaced
    # (`rm -f`); then `services down` (compose stop + rm).
    release = [
        ("agent", "start"),
        ("agent", "kill"),
        ("agent", "kill"),
        ("agent", "stop"),
        ("agent", "die"),
        ("agent", "destroy"),
        ("agent", "kill"),
        ("agent", "die"),
        ("agent", "destroy"),
    ]
    down = [("service", "kill"), ("service", "stop"), ("service", "die"), ("service", "destroy")]
    deployments = {DEPLOYMENT: {"name": "helper", "state": "ready"}}
    _replay(store, [_record(step) for step in release + down], deployments)
    assert store.recent() == []
    # A job failing is a job event; a release that passed is not.
    assert events.record(events.job_failed("release", PROJECT, 0, None), store=store) is False
    assert store.recent() == []


def test_a_crashed_service_stores_one_event_and_its_restart_none(store):
    sequence = [("service", "start"), ("service", "die"), ("service", "start")]
    _replay(store, [_record(s, exit_code="1") for s in sequence], {})
    rows = store.recent()
    assert [(r["kind"], r["name"], r["exit_code"]) for r in rows] == [
        ("container_died", "postgres", 1)
    ]


def test_the_condition_comes_from_next_and_clears(monkeypatch):
    """Conditions are never stored: `events` reads them from the function `next` uses."""
    calls = []

    def compute(project, environment="dev", output=None, probe=None):
        calls.append(project)
        crashed = len(calls) == 1
        conditions = (
            [SimpleNamespace(code="SERVICES_UNHEALTHY", message="postgres is exited ()")]
            if crashed
            else []
        )
        return SimpleNamespace(conditions=conditions)

    monkeypatch.setattr("agentbreeder.guide_checks.compute", compute)
    assert [c["code"] for c in events.conditions(Path("p"))] == ["SERVICES_UNHEALTHY"]
    assert events.conditions(Path("p")) == []


# -- CLI ---------------------------------------------------------------------------------------


@pytest.fixture
def center(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "center"))
    return tmp_path / "center"


def test_cli_lists_events_as_json(center, capsys, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    events.record(events.from_watch(died(), str(project.resolve())))
    events.record(events.job_failed("build", "/elsewhere", 2, "[BUILD_FAILED] x"))
    assert cli.main(["events", "--project", str(project), "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["schema_version"] == events_command.SCHEMA_VERSION
    assert [e["kind"] for e in body["events"]] == ["container_died"]
    assert body["events"][0]["message"] == "service postgres exited (exit 1)"
    assert body["unread"] == 1
    assert isinstance(body["conditions"], list)  # computed by next's function (stubbed below)


def test_cli_conditions_come_from_next(center, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "agentbreeder.events.conditions",
        lambda project, environment: [{"code": "SERVICES_UNHEALTHY", "message": "postgres down"}],
    )
    assert cli.main(["events", "--project", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Needs attention:" in out and "! postgres down" in out and "No events." in out


def test_cli_text_and_mark_read(center, capsys):
    first = events.EventStore.open(center)
    try:
        identifier = first.append(events.job_failed("build", PROJECT, 2, "[BUILD_FAILED] x"))
    finally:
        first.close()
    assert cli.main(["events"]) == 0
    out = capsys.readouterr().out
    assert "Job build failed (exit 2) [BUILD_FAILED]" in out and "unread" in out
    assert cli.main(["events", "--mark-read", str(identifier)]) == 0
    capsys.readouterr()
    assert cli.main(["events", "--unread", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == []


def test_cli_with_no_store_prints_nothing_and_writes_nothing(center, capsys):
    assert cli.main(["events", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": events_command.SCHEMA_VERSION,
        "events": [],
        "unread": 0,
        "unknown_ids": [],
    }
    assert not center.exists()


def test_cli_errors_are_coded(center, capsys):
    center.mkdir(mode=0o700)
    (center / events.FILENAME).write_text("not a database")
    assert cli.main(["events", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[EVENTS_UNREADABLE] ")
    assert json.loads(captured.out)["error"]["code"] == "EVENTS_UNREADABLE"


def test_cli_limit_is_bounded(center, capsys):
    with pytest.raises(SystemExit):
        cli.main(["events", "--limit", "0"])
    with pytest.raises(SystemExit):
        cli.main(["events", "--limit", "501"])


def test_sqlite_error_type_is_not_leaked(center, capsys, monkeypatch):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error at /secret/path")

    monkeypatch.setattr(events, "read_recent", broken)
    assert cli.main(["events"]) == 2
    assert "/secret/path" not in capsys.readouterr().err


def _gate(**data):
    base = {"passed": 1, "cases": 2, "failed": ["a"], "more": 0, "report": None}
    return {
        **events.gate_failed(PROJECT, {"agent": "helper", "eval": {}}),
        "data": {**base, **data},
    }


@pytest.mark.parametrize(
    "data",
    [
        {"passed": 3},  # more than cases
        {"cases": True},
        {"failed": "a"},
        {"failed": ["a"] * (events.MAX_CASES + 1)},
        {"failed": ["bad\x00id"]},
        {"more": -1},
        {"report": ""},
        {"report": 7},
        {"extra": 1},
    ],
)
def test_gate_data_is_validated(store, data):
    with pytest.raises(events.EventError):
        store.append(_gate(**data))


def test_other_kinds_data_is_validated(store):
    job = events.job_failed("build", PROJECT, 1, None)
    unhealthy = events.from_watch({**died(), "event": "unhealthy"}, PROJECT)
    for event in ({**job, "data": {}}, {**unhealthy, "data": {"oom": False}}, {**job, "data": []}):
        with pytest.raises(events.EventError):
            store.append(event)
    for event in ({**job, "source": "agent"}, {**job, "time": "yesterday"}):
        with pytest.raises(events.EventError):
            store.append(event)
    assert store.append(_gate()) > 0


def test_rows_written_by_something_else_are_never_shown(store):
    store.append(events.from_watch(died(), PROJECT))
    store.db.execute("UPDATE events SET data = 'not json'")
    assert store.recent() == []


def test_read_only_open_versions(tmp_path):
    directory = tmp_path / "center"
    directory.mkdir()
    db = sqlite3.connect(directory / events.FILENAME)
    db.execute("CREATE TABLE x (y)")
    db.commit()
    assert events.read_recent(directory) == []  # version 0: never written
    assert events.read_unread(directory) == 0
    db.execute(f"PRAGMA user_version = {events.SCHEMA_VERSION + 1}")
    db.commit()
    db.close()
    with pytest.raises(events.EventError) as error:
        events.read_recent(directory)
    assert error.value.code == "EVENTS_TOO_NEW"


def test_job_failed_without_an_exit_code():
    event = events.job_failed("build", PROJECT, None, None)
    assert events.render(event) == "Job build failed"
    assert events.code_of(None) is None


def test_container_died_without_an_exit_code():
    event = events.from_watch(died(exit_code=None), PROJECT)
    assert events.render(event) == "service postgres exited (no exit code)"


def test_project_dir_of_a_file(tmp_path):
    (tmp_path / "agent.yaml").write_text("")
    assert events.project_dir(tmp_path / "agent.yaml") == str(tmp_path.resolve())


def test_conditions_uses_guide_checks(monkeypatch, tmp_path):
    seen = {}

    def compute(project, environment="dev", output=None, probe=None):
        seen["args"] = (project, environment)
        return SimpleNamespace(conditions=[SimpleNamespace(code="X_Y_Z", message="m")])

    monkeypatch.setattr("agentbreeder.guide_checks.compute", compute)
    assert events.conditions(tmp_path, "staging") == [{"code": "X_Y_Z", "message": "m"}]
    assert seen["args"] == (tmp_path, "staging")


def test_cli_mark_all_read_for_a_project(center, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("agentbreeder.events.conditions", lambda project, environment: [])
    project = str(tmp_path.resolve())
    events.record(events.from_watch(died(), project))
    events.record(events.from_watch(died(), "/elsewhere"))
    assert cli.main(["events", "--project", str(tmp_path), "--mark-all-read", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["unread"] == 0 and body["conditions"] == []
    assert events.read_unread(center) == 1


def test_cli_marking_with_no_store_creates_nothing(center, capsys):
    assert cli.main(["events", "--mark-all-read"]) == 0
    assert "No events." in capsys.readouterr().out and not center.exists()


def test_cli_rejects_bad_arguments(center, capsys):
    assert cli.main(["events", "--environment", "Bad!", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "ENVIRONMENT_INVALID"
    for argv in (["--limit", "x"], ["--mark-read", "1a"]):
        with pytest.raises(SystemExit):
            cli.main(["events", *argv])


@pytest.mark.parametrize(
    "raised, code",
    [("guide", "STATE_BUSY"), ("other", "NEXT_FAILED")],
)
def test_cli_conditions_errors_are_coded(center, capsys, monkeypatch, tmp_path, raised, code):
    from agentbreeder import guide_checks

    def broken(project, environment):
        if raised == "guide":
            raise guide_checks.GuideError("STATE_BUSY", "A running job holds the local state")
        raise RuntimeError("/secret/path")

    monkeypatch.setattr("agentbreeder.events.conditions", broken)
    assert cli.main(["events", "--project", str(tmp_path), "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["conditions"] is None and body["conditions_error"]["code"] == code
    assert "/secret/path" not in json.dumps(body)
    assert cli.main(["events", "--project", str(tmp_path)]) == 0
    assert f"Needs attention: unknown ([{code}]" in capsys.readouterr().out


def test_record_survives_unencodable_text_and_failing_builders(store):
    """A lone surrogate (a non-UTF-8 directory name) is stripped, and no failure escapes."""
    event = events.gate_failed(
        "/tmp/a\udcffb", {"agent": "helper", "eval": {"passed": 0, "cases": 1}}
    )
    assert events.record(event, store=store) is False  # cleaned path differs: refused, not raised
    assert events.record(lambda: events.gate_failed(PROJECT, {"eval": "not a dict"})) is False

    def boom():
        raise RuntimeError("x")

    assert events.record(boom) is False
    assert "\udcff" not in events._clean("a\udcffb", 10)


def test_clean_never_ends_in_a_space_after_cutting():
    case = "a" * 79 + " b"
    assert events._clean(case, 80) == "a" * 79
    details = {
        "agent": "helper",
        "eval": {"passed": 0, "cases": 1},
        "failed_cases": [{"id": case}],
    }
    assert events.validate(events.gate_failed(PROJECT, details))


def test_exit_codes_beyond_255_are_dropped_not_the_event():
    assert events.from_watch(died(exit_code=999), PROJECT)["exit_code"] is None


def test_read_only_open_refuses_a_symlinked_store(tmp_path, store):
    (tmp_path / "link").symlink_to(tmp_path / "center")
    with pytest.raises(events.EventError) as error:
        events.read_recent(tmp_path / "link")
    assert error.value.code == "EVENTS_UNSAFE"


def _two_events(center):
    store = events.EventStore.open(center)
    try:
        return [
            store.append(events.job_failed("build", PROJECT, 2, "[BUILD_FAILED] x")),
            store.append(events.job_failed("build", PROJECT, 1, "[BUILD_FAILED] y")),
        ]
    finally:
        store.close()


def test_store_mark_read_returns_unknown_ids_once_in_order(store):
    known = store.append(events.job_failed("build", PROJECT, 2, "[BUILD_FAILED] x"))
    assert store.mark_read([999, known, 7, 999]) == [999, 7]
    assert store.unread_count() == 0
    assert store.mark_read([known]) == []  # already read still counts as known
    assert store.mark_read(None) == [] and store.mark_read(None, PROJECT) == []


def test_cli_unknown_mark_read_id_warns_and_exits_0(center, capsys):
    """#897: an unknown id warns on stderr, exit 0, and the events are listed as usual."""
    _two_events(center)
    assert cli.main(["events", "--mark-read", "999"]) == 0
    captured = capsys.readouterr()
    assert captured.err == "ignored unknown event id 999\n"
    assert "Recent events (2 unread):" in captured.out


def test_cli_unknown_mark_read_id_in_json(center, capsys):
    _two_events(center)
    assert cli.main(["events", "--mark-read", "999", "--json"]) == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["unknown_ids"] == [999] and body["unread"] == 2
    assert captured.err == "ignored unknown event id 999\n"


def test_cli_mixed_known_and_unknown_ids_marks_the_known_ones(center, capsys):
    first, second = _two_events(center)
    argv = ["events", "--mark-read", "999", "--mark-read", str(first), "--mark-read", "999"]
    assert cli.main([*argv, "--json"]) == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["unknown_ids"] == [999]
    assert captured.err == "ignored unknown event id 999\n"
    assert {e["id"]: e["read"] for e in body["events"]} == {first: True, second: False}
    assert body["unread"] == 1


def test_cli_known_ids_and_mark_all_read_report_no_unknown_ids(center, capsys):
    first, _ = _two_events(center)
    assert cli.main(["events", "--mark-read", str(first), "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["unknown_ids"] == [] and captured.err == ""
    assert cli.main(["events", "--mark-read", "999", "--mark-all-read", "--json"]) == 0
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["unknown_ids"] == [] and body["unread"] == 0 and captured.err == ""


def test_cli_mark_read_with_no_store_reports_every_id_and_creates_nothing(center, capsys):
    assert cli.main(["events", "--mark-read", "3", "--mark-read", "1", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["unknown_ids"] == [3, 1]
    assert captured.err == "ignored unknown event id 3\nignored unknown event id 1\n"
    assert not center.exists()
