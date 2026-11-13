"""`agentbreeder watch --stack DIR --json` (#821 M5; guided §14.4, §14.9 R1, R5)."""

# Fixtures imported from test_services_engine are redefined as parameters on purpose.
# ruff: noqa: F811

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

import pytest
from conftest import v2_manifest_data
from test_services_engine import env, fake_docker  # noqa: F401

from agentbreeder import cli, runtime, watch
from agentbreeder.commands import logs as logs_command
from agentbreeder.commands import watch as watch_command
from agentbreeder.services.state import StackState
from agentbreeder.store import Store

STACK = "stack-id"
AGENT = "a" * 32
OTHER = "b" * 32
CONTAINER = "c" * 64
SERVICE_LABELS = {
    watch.LABEL_STACK: STACK,
    watch.LABEL_SERVICE: "postgres",
    "name": "agentbreeder-x-postgres-1",
}


def record(action, labels, container=CONTAINER, at=1794542392):
    return {
        "Type": "container",
        "Action": action,
        "Actor": {"ID": container, "Attributes": labels},
        "time": at,
    }


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def events(rows=None, clock=None):
    rows = {AGENT: {"name": "hello", "state": "ready"}} if rows is None else rows
    return watch.Events(STACK, lambda: dict(rows), clock or Clock())


# -- the event rules ---------------------------------------------------------------------------


def test_a_die_without_a_kill_is_an_event_and_a_start_after_it_is_a_restart():
    feed = events()
    assert feed.feed(record("start", SERVICE_LABELS)) == []  # a plain start is not an event
    died = feed.feed(record("die", {**SERVICE_LABELS, "exitCode": "137"}))
    assert died == [
        {
            "schema_version": 1,
            "event": "died",
            "time": "2026-11-13T03:59:52Z",
            "source": "service",
            "name": "postgres",
            "deployment": None,
            "exit_code": 137,
            "oom": False,
        }
    ]
    assert feed.feed(record("start", SERVICE_LABELS))[0]["event"] == "restarted"
    assert feed.feed(record("start", SERVICE_LABELS)) == []  # only once


@pytest.mark.parametrize("intent", ["kill", "stop"])
def test_a_die_after_a_kill_or_stop_is_not_an_event_until_the_container_starts_again(intent):
    """R1: `docker stop` (kill, kill, stop, die), `rm -f` (kill, die, destroy), compose down,
    however long the stop takes; once the container starts again, its own exit counts."""
    clock = Clock()
    feed = events(clock=clock)
    assert feed.feed(record(intent, SERVICE_LABELS)) == []
    clock.now += 600  # a long stop grace period
    assert feed.feed(record("die", {**SERVICE_LABELS, "exitCode": "0"})) == []
    assert feed.feed(record("start", SERVICE_LABELS)) == []  # `docker start`: not a restart
    assert feed.feed(record("die", {**SERVICE_LABELS, "exitCode": "1"}))[0]["exit_code"] == 1


def test_destroy_forgets_the_container():
    feed = events()
    feed.feed(record("kill", SERVICE_LABELS))
    feed.feed(record("oom", SERVICE_LABELS))
    assert feed.feed(record("destroy", SERVICE_LABELS)) == []
    assert CONTAINER not in feed.intent and CONTAINER not in feed.oom
    assert feed.feed(record("die", SERVICE_LABELS))[0]["oom"] is False


def test_tracked_containers_are_bounded(monkeypatch):
    monkeypatch.setattr(watch, "MAX_TRACKED", 3)
    feed = events()
    for index in range(5):
        feed.feed(record("kill", SERVICE_LABELS, container=f"{index:x}" * 12))
    assert list(feed.intent) == ["2" * 12, "3" * 12, "4" * 12]


def test_exit_codes_are_bounded():
    feed = events()
    for index, (code, expected) in enumerate(
        (("255", 255), ("²", None), ("1" * 5000, None), ("-1", None))
    ):
        labels = {**SERVICE_LABELS, "exitCode": code}
        died = feed.feed(record("die", labels, container=f"{index:x}" * 12))
        assert died[0]["exit_code"] == expected


def test_oom_and_unhealthy():
    clock = Clock()
    feed = events(clock=clock)
    assert feed.feed(record("oom", SERVICE_LABELS)) == []
    assert feed.feed(record("die", {**SERVICE_LABELS, "exitCode": "137"}))[0]["oom"] is True
    feed.feed(record("oom", SERVICE_LABELS, container="d" * 64))
    clock.now += watch.OOM_WINDOW + 1  # an old oom is not this exit's cause
    assert feed.feed(record("die", SERVICE_LABELS, container="d" * 64))[0]["oom"] is False
    unhealthy = feed.feed(record("health_status: unhealthy", SERVICE_LABELS))
    assert unhealthy[0]["event"] == "unhealthy"
    assert feed.feed(record("health_status: healthy", SERVICE_LABELS)) == []


def test_agent_containers_match_by_deployment_id_and_only_when_ready():
    rows = {
        AGENT: {"name": "hello", "state": "ready"},
        OTHER: {"name": "hello-candidate", "state": "removing"},
    }
    feed = events(rows)
    agent = {watch.LABEL_DEPLOYMENT: AGENT, watch.LABEL_ROLE: "agent", "exitCode": "1"}
    died = feed.feed(record("die", agent))[0]
    assert (died["source"], died["name"], died["deployment"]) == ("agent", "hello", AGENT)
    assert "role" not in died
    egress = {watch.LABEL_DEPLOYMENT: AGENT, watch.LABEL_ROLE: "egress", "exitCode": "1"}
    assert feed.feed(record("die", egress, container="e" * 64))[0]["role"] == "egress"
    # The running job's container (a candidate being removed) is not an event,
    candidate = {watch.LABEL_DEPLOYMENT: OTHER}
    assert feed.feed(record("die", candidate, container="d" * 64)) == []
    # nor is its health check (a deploy's container may be unhealthy while it starts).
    assert feed.feed(record("health_status: unhealthy", candidate, container="d" * 64)) == []


def test_a_deployment_that_became_ready_is_re_read_before_judging_its_die():
    """A crash just after a deploy went ready is not hidden by the state cached at `start`."""
    rows = {AGENT: {"name": "hello", "state": "starting"}}
    reads = []

    def read():
        reads.append(1)
        return {key: dict(value) for key, value in rows.items()}

    clock = Clock()
    feed = watch.Events(STACK, read, clock)
    agent = {watch.LABEL_DEPLOYMENT: AGENT, "exitCode": "1"}
    assert feed.feed(record("start", agent)) == []
    rows[AGENT]["state"] = "ready"  # the deploy finished
    clock.now += watch.REREAD_STATE
    assert feed.feed(record("die", agent))[0]["event"] == "died"
    assert len(reads) == 2
    assert feed.feed(record("die", agent, container="d" * 64))[0]["event"] == "died"
    assert len(reads) == 2  # cached `ready`: no read needed


def test_unknown_deployments_are_re_read_at_most_every_few_seconds():
    reads = []
    rows: dict = {}

    def read():
        reads.append(1)
        return dict(rows)

    clock = Clock()
    feed = watch.Events(STACK, read, clock)
    labels = {watch.LABEL_DEPLOYMENT: OTHER, "exitCode": "1"}
    assert feed.feed(record("die", labels)) == []  # another stack's agent: ignored
    assert len(reads) == 1  # read at start; too soon to read again
    clock.now += watch.REREAD
    rows[OTHER] = {"name": "new", "state": "ready"}  # deployed since
    assert feed.feed(record("die", labels))[0]["name"] == "new"
    assert len(reads) == 2


def test_relevant_needs_an_agentbreeder_label():
    assert watch.relevant(record("die", SERVICE_LABELS))
    assert watch.relevant(record("die", {watch.LABEL_DEPLOYMENT: AGENT}))
    assert not watch.relevant(record("die", {"name": "someone-else"}))
    assert not watch.relevant({**record("die", SERVICE_LABELS), "Actor": {"ID": "x"}})
    assert not watch.relevant([1])


def test_other_stacks_steps_and_malformed_records_are_ignored():
    feed = events()
    assert feed.feed(record("die", {**SERVICE_LABELS, watch.LABEL_STACK: "other"})) == []
    assert feed.feed(record("die", {**SERVICE_LABELS, watch.LABEL_STEP: "init"})) == []
    assert feed.feed(record("die", {**SERVICE_LABELS, watch.LABEL_SERVICE: "Bad\x1b[2J"})) == []
    assert feed.feed(record("die", SERVICE_LABELS, container="not-hex")) == []
    assert feed.feed({**record("die", SERVICE_LABELS), "Type": "network"}) == []
    assert feed.feed({**record("die", SERVICE_LABELS), "Action": 3}) == []
    assert feed.feed({"Type": "container", "Actor": "x"}) == []
    assert feed.feed("not a dict") == []
    assert feed.feed(record("die", {watch.LABEL_DEPLOYMENT: "short"})) == []
    bad_role = {watch.LABEL_DEPLOYMENT: AGENT, watch.LABEL_ROLE: "Evil Role", "exitCode": "x"}
    died = feed.feed(record("die", bad_role, at=-5))[0]
    assert "role" not in died and died["exit_code"] is None and died["time"] is None


def test_text_lines():
    feed = events()
    died = feed.feed(record("die", {**SERVICE_LABELS, "exitCode": "1"}))[0]
    assert watch.text(died) == "2026-11-13T03:59:52Z died: service postgres, exit 1"
    feed.feed(record("oom", SERVICE_LABELS, container="f" * 64))
    oom = feed.feed(record("die", SERVICE_LABELS, container="f" * 64))[0]
    assert watch.text(oom).endswith("exited, out of memory")
    egress = {watch.LABEL_DEPLOYMENT: AGENT, watch.LABEL_ROLE: "egress"}
    unhealthy = feed.feed(record("health_status: unhealthy", egress, container="e" * 64))[0]
    assert watch.text(unhealthy) == "2026-11-13T03:59:52Z unhealthy: agent hello (egress)"


# -- the command -------------------------------------------------------------------------------


@pytest.fixture
def stack(env, fake_docker, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["services", "up", str(env), "--environment", "dev", "--yes"]) == 0
    capsys.readouterr()
    monkeypatch.setattr("agentbreeder.services.compose.DockerCLI", lambda: fake_docker)
    fake_docker._argv = lambda arguments: ["docker", *arguments]
    return env


def _state(project):
    from agentbreeder.services.engine import open_state

    return open_state(project)[1]


def _deploy(project, identifier=AGENT, name="hello", target="local", state="ready"):
    directory = _state(project).deploy_state("dev")
    store = Store(directory)
    try:
        manifest = v2_manifest_data(name=name, port=8080)
        manifest["target"] = target
        store.create(identifier, manifest, "sha256:" + "d" * 64, "test-context")
        store.transition(identifier, state, endpoint="http://127.0.0.1:5000")
    finally:
        store.close()


def _child(monkeypatch, records, linger=60):
    """`docker events`: print `records` (one JSON per line), then wait to be stopped."""
    lines = "".join(json.dumps(r) + "\n" for r in records)
    script = (
        f"import sys, time; sys.stdout.write({lines!r}); sys.stdout.flush(); time.sleep({linger})"
    )
    started = []

    def start(argv, environment, merge_stderr=False):
        process = subprocess.Popen(
            [sys.executable, "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE
        )
        started.append((argv, environment, process))
        return process

    monkeypatch.setattr(runtime, "start_follow", start)
    return started


@pytest.fixture
def stopper(monkeypatch):
    """Capture the command's _Follow so a test can stop it as stdin's end would."""
    holder = []
    original = logs_command._Follow

    def make():
        follow = original()
        holder.append(follow)
        return follow

    monkeypatch.setattr(logs_command, "_Follow", make)
    monkeypatch.setattr(logs_command, "_stdin_descriptor", lambda: None)
    return holder


def _stop_when(monkeypatch, stopper, count):
    """Stop the watch (as stdin's end would) once `count` records were printed."""
    printed = []
    original = watch_command._emit

    def emit(args, event, text):
        original(args, event, text)
        printed.append(event)
        if len(printed) >= count:
            stopper[0].stop("stdin")

    monkeypatch.setattr(watch_command, "_emit", emit)
    return printed


def test_watch_prints_status_events_and_the_end_record(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    _deploy(stack)
    state_id = _state(stack).id
    fake_docker.running = {
        "postgres": {"id": "x", "state": "running", "health": "healthy"},
        "BAD\x1b": {"id": "y", "state": "running", "health": ""},
        "litellm": {"id": "z", "state": "Running\x1b[2J", "health": "x" * 99},
    }
    service = {**SERVICE_LABELS, watch.LABEL_STACK: state_id, "exitCode": "1"}
    agent = {watch.LABEL_DEPLOYMENT: AGENT, "exitCode": "2"}
    started = _child(
        monkeypatch,
        [record("die", service), record("kill", agent, container="e" * 64),
         record("die", agent, container="e" * 64), record("die", agent, container="f" * 64),
         record("die", {"name": "unrelated"}, container="1" * 64)],
    )  # fmt: skip
    monkeypatch.setenv("DOCKER_HOST", "tcp://elsewhere:2375")
    monkeypatch.setenv("SOME_API_KEY", "seeded-marker-" + "q" * 20)
    _stop_when(monkeypatch, stopper, 3)  # the status and two events, in any order
    assert cli.main(["watch", "--stack", str(stack), "--json"]) == 0
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    status = next(e for e in out if e["event"] == "status")
    assert status["services"] == [
        {"name": "litellm", "state": None, "health": None},  # invalid words: null
        {"name": "postgres", "state": "running", "health": "healthy"},
    ]  # and the invalid name is left out
    assert status["deployments"] == [
        {"id": AGENT, "name": "hello", "target": "local", "state": "ready"}
    ]
    kinds = [(e["event"], e.get("name"), e.get("exit_code")) for e in out if e["event"] == "died"]
    assert kinds == [("died", "postgres", 1), ("died", "hello", 2)]  # the killed one: nothing
    assert out[-1] == {"schema_version": 1, "event": "end", "reason": "stdin", "dropped": 0}
    argv, environment, process = started[0]
    assert argv[:4] == ["docker", "events", "--format", "{{json .}}"]
    assert "type=container" in argv and "event=die" in argv
    assert "DOCKER_HOST" not in environment and "SOME_API_KEY" not in environment
    assert process.poll() is not None  # S-10: the child was stopped and reaped


def test_a_stop_while_the_child_starts_still_stops_it(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    started = _child(monkeypatch, [])
    start = runtime.start_follow

    def start_then_signal(argv, environment, merge_stderr=False):
        process = start(argv, environment)
        stopper[0].stop("signal")  # SIGTERM between the child's start and its record
        return process

    monkeypatch.setattr(runtime, "start_follow", start_then_signal)
    began = time.monotonic()
    assert cli.main(["watch", "--stack", str(stack), "--json"]) == 0
    assert time.monotonic() - began < 10  # not the child's 60 s
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["reason"] == "signal"
    assert started[0][2].poll() is not None


def test_a_closed_stdout_ends_quietly(stack, fake_docker, capsys, monkeypatch, stopper):
    started = _child(monkeypatch, [])

    def broken(args, event, text):
        raise BrokenPipeError

    monkeypatch.setattr(watch_command, "_emit", broken)
    monkeypatch.setattr(watch_command, "_quiet_stdout", lambda: None)
    assert cli.main(["watch", "--stack", str(stack), "--json"]) == 0
    assert started[0][2].poll() is not None


def test_watch_resyncs_and_prints_text(stack, fake_docker, capsys, monkeypatch, stopper):
    monkeypatch.setattr(watch_command, "RESYNC", 0.3)
    _child(monkeypatch, [])
    _stop_when(monkeypatch, stopper, 2)
    assert cli.main(["watch", "--stack", str(stack)]) == 0
    captured = capsys.readouterr()
    statuses = [line for line in captured.out.splitlines() if " status: " in line]
    assert len(statuses) == 2 and "echo" in statuses[0]
    assert "Watch ended: stdin ended" in captured.err


def test_a_failing_or_hung_status_does_not_stop_later_ones(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    monkeypatch.setattr(watch_command, "RESYNC", 0.2)
    calls = []

    def status(state, docker, deployments):
        calls.append(1)
        if len(calls) == 1:
            raise KeyError("name")  # a malformed row
        if len(calls) == 2:
            time.sleep(60)  # a hung kubectl: the next resync must not wait for it
        return {
            "schema_version": 1,
            "event": "status",
            "time": "T",
            "services": [],
            "deployments": [],
        }

    monkeypatch.setattr(watch_command, "_status", status)
    _child(monkeypatch, [])
    _stop_when(monkeypatch, stopper, 2)
    began = time.monotonic()
    assert cli.main(["watch", "--stack", str(stack), "--json"]) == 0
    assert time.monotonic() - began < 10
    out = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert out[0]["services"] is None and out[0]["deployments"] == []  # the failed one
    assert out[1]["services"] == []  # the third, started while the second hung


def test_watch_reports_docker_events_ending(stack, fake_docker, capsys, monkeypatch, stopper):
    _child(monkeypatch, [], linger=0)
    assert cli.main(["watch", "--stack", str(stack), "--json"]) == 2
    captured = capsys.readouterr()
    assert "[DOCKER_EVENTS_FAILED] docker events ended unexpectedly" in captured.err
    assert json.loads(captured.out.splitlines()[-1])["error"]["code"] == "DOCKER_EVENTS_FAILED"


@pytest.mark.parametrize("code, reason", [(130, "interrupt"), (143, "signal"), (1, None)])
def test_docker_events_exiting_on_a_stop_signal_is_a_stop(
    stack, fake_docker, capsys, monkeypatch, stopper, code, reason
):
    # The terminal's Ctrl-C (or a group SIGTERM) can reach `docker events` before the CLI, and
    # the docker CLI exits 128 + N (#863 review).
    def start(argv, environment, merge_stderr=False):
        script = f"import sys; sys.exit({code})"
        return subprocess.Popen(
            [sys.executable, "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE
        )

    monkeypatch.setattr(runtime, "start_follow", start)
    assert cli.main(["watch", "--stack", str(stack), "--json"]) == (0 if reason else 2)
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    if reason:
        assert last["reason"] == reason
    else:
        assert last["error"]["code"] == "DOCKER_EVENTS_FAILED"


def test_watch_status_with_a_kubernetes_pod_and_unreadable_compose(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    from agentbreeder.services.schema import ServiceError

    _deploy(stack, target="kubernetes")
    _deploy(stack, identifier=OTHER, name="gone", target="kubernetes")

    class Kube:
        def authorize(self, context):
            pass

        def inspect(self, row):
            return None if row["id"] == OTHER else {"name": "hello"}

        def pod(self, info):
            return {"name": "p", "reason": "CrashLoopBackOff", "readable": True}

    monkeypatch.setattr(logs_command, "_kubernetes", lambda: Kube())

    def broken(state):
        raise ServiceError("DOCKER_FAILED", "compose ps failed")

    monkeypatch.setattr(fake_docker, "containers", broken)
    _child(monkeypatch, [])
    _stop_when(monkeypatch, stopper, 1)
    assert cli.main(["watch", "--stack", str(stack), "--json"]) == 0
    status = json.loads(capsys.readouterr().out.splitlines()[0])
    assert status["services"] is None
    pods = {d["name"]: d["pod"] for d in status["deployments"]}
    assert pods == {
        "hello": {"running": False, "reason": "CrashLoopBackOff"},
        "gone": {"running": False, "reason": "GONE"},
    }
    monkeypatch.setattr(logs_command, "_kubernetes", lambda: (_ for _ in ()).throw(OSError()))
    assert watch_command._pod({"context": "x"}) is None


def test_watch_refuses_a_stack_never_brought_up(env, capsys):
    assert cli.main(["watch", "--stack", str(env), "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("[STACK_NOT_UP]")
    assert json.loads(captured.out)["error"]["code"] == "STACK_NOT_UP"
    assert StackState.open_readonly("demo", env) is None  # and created nothing


def test_watch_refuses_a_missing_stack_file(tmp_path, capsys):
    assert cli.main(["watch", "--stack", str(tmp_path)]) == 2
    assert capsys.readouterr().err.startswith("[")


def test_watch_without_docker(stack, fake_docker, capsys, monkeypatch):
    from agentbreeder.services.schema import ServiceError

    def refuse():
        raise ServiceError("DOCKER_UNAVAILABLE", "no docker")

    monkeypatch.setattr(fake_docker, "authorize", refuse)
    assert cli.main(["watch", "--stack", str(stack)]) == 2
    assert capsys.readouterr().err.startswith("[DOCKER_UNAVAILABLE] no docker")


def test_pump_keeps_only_bounded_relevant_records(monkeypatch):
    monkeypatch.setattr(watch_command, "LINE_LIMIT", 1024)
    monkeypatch.setattr(watch_command, "QUEUE_LIMIT", 2)
    keep = json.dumps(record("die", SERVICE_LABELS))
    other = json.dumps(record("die", {"name": "x"}))
    lines = ["x" * 4000, "nope", "[" * 100000, other, keep, keep, keep]
    script = f"import sys; sys.stdout.write({chr(10).join(lines) + chr(10)!r})"
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
    records = watch_command._Records()
    thread = threading.Thread(target=watch_command._pump, args=(process, records), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while records.dropped == 0 and time.monotonic() < deadline:
        time.sleep(0.01)  # nobody reads yet: two fit, the third is dropped
    assert records.dropped == 1
    got = [records.queue.get(timeout=10) for _ in range(3)]
    assert [item["Action"] for item in got[:2]] == ["die", "die"] and got[2] is None
    thread.join(10)
    process.wait()


def test_pump_survives_deeply_nested_json(monkeypatch):
    monkeypatch.setattr(watch_command, "LINE_LIMIT", 1024 * 1024)
    script = "import sys; sys.stdout.write('[' * 200000 + chr(10))"
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
    records = watch_command._Records()
    thread = threading.Thread(target=watch_command._pump, args=(process, records))
    thread.start()
    assert records.queue.get(timeout=10) is None  # skipped, then the end marker
    thread.join(10)
    process.wait()


def test_status_text():
    status = {
        "time": "T",
        "services": [{"name": "postgres", "state": "running", "health": "healthy"}],
        "deployments": [{"name": "hello", "target": "local", "state": "ready"}],
    }
    assert watch_command._status_text(status) == "T status: postgres healthy, hello (local) ready"
    assert watch_command._status_text({**status, "services": None}).startswith(
        "T status: services unreadable"
    )
    unknown = {**status, "services": [{"name": "x", "state": None, "health": None}]}
    assert watch_command._status_text(unknown).startswith("T status: x ?")


def test_record_stores_died_events_only(
    stack, fake_docker, capsys, monkeypatch, stopper, tmp_path
):
    """`--record` (#827): the printed died event is stored; a stop and a restart are not."""
    from agentbreeder import events as stored_events

    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "center"))
    service = {**SERVICE_LABELS, watch.LABEL_STACK: _state(stack).id, "exitCode": "1"}
    _child(
        monkeypatch,
        [record("kill", service, container="e" * 64), record("die", service, container="e" * 64),
         record("die", service), record("start", service)],
    )  # fmt: skip
    _stop_when(monkeypatch, stopper, 3)  # the status, died and restarted
    assert cli.main(["watch", "--stack", str(stack), "--json", "--record"]) == 0
    rows = stored_events.read_recent(tmp_path / "center")
    assert [(r["kind"], r["name"], r["exit_code"]) for r in rows] == [
        ("container_died", "postgres", 1)
    ]
    assert rows[0]["project"] == str(stack.resolve())


def test_record_refuses_an_unsafe_store(stack, fake_docker, capsys, monkeypatch, tmp_path):
    (tmp_path / "file").write_text("")
    monkeypatch.setenv("AGENTBREEDER_CENTER_DIR", str(tmp_path / "file" / "center"))
    assert cli.main(["watch", "--stack", str(stack), "--json", "--record"]) == 2
    assert capsys.readouterr().err.startswith("[EVENTS_UNSAFE] ")
