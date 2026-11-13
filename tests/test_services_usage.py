"""`agentbreeder services usage [DIR] [--follow] [--json]` (#821 M5 S-10, M6; console §4.2)."""

# Fixtures imported from test_services_engine and test_watch are redefined as parameters on
# purpose.
# ruff: noqa: F811

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest
from test_services_engine import env, fake_docker  # noqa: F401
from test_watch import _deploy, _state, stack, stopper  # noqa: F401

from agentbreeder import cli, runtime, usage, watch
from agentbreeder.commands import usage as usage_command
from agentbreeder.services.compose import DockerCLI
from agentbreeder.services.schema import ServiceError

AGENT = "a" * 32
OTHER = "b" * 32
ID1, ID2, ID3 = "1" * 12, "2" * 12, "3" * 12


def stats(identifier, cpu="0.50%", mem="120.4MiB / 512MiB"):
    return {
        "BlockIO": "4.1kB / 0B",
        "CPUPerc": cpu,
        "Container": identifier,
        "ID": identifier,
        "MemPerc": "23.51%",
        "MemUsage": mem,
        "Name": "/Users/someone/leak",
        "NetIO": "9.41kB / 2.13kB",
        "PIDs": "3",
    }


def frame_bytes(*records) -> bytes:
    """One `docker stats` refresh as Docker 27.5 writes it into a pipe."""
    lines = [json.dumps(r).encode() for r in records]
    body = b"\x1b[J\x1b[H" + lines[0] + b"\x1b[K\n"
    body += b"".join(line + b"\x1b[K\n" for line in lines[1:])
    return body + b"\x1b[K\n"


# -- parsing -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("0B", 0),
        ("120.4MiB", round(120.4 * 1024**2)),
        ("48.18MB", 48_180_000),
        ("9.41kB", 9410),
        ("528KiB", 528 * 1024),
        ("1.5 GB", 1_500_000_000),
        ("--", None),
        ("N/A", None),
        ("12XB", None),
        ("1e9B", None),
        (None, None),
        (12, None),
    ],
)
def test_size(text, expected):
    assert usage.size(text) == expected


def test_percent():
    assert usage.percent("0.02%") == 0.02
    assert usage.percent("250.5%") == 250.5  # several cores
    assert usage.percent("--") is None
    assert usage.percent(None) is None


def test_frames_split_on_the_home_sequence_and_complete_early():
    frames = usage.Frames()
    first = frame_bytes(stats(ID1), stats(ID2)).splitlines(keepends=True)
    done = []
    for line in first:
        done += frames.feed(line, {ID1, ID2})
    assert [sorted(f) for f in done] == [[ID1, ID2]]  # complete once both arrived
    done = []
    for line in frame_bytes(stats(ID1)).splitlines(keepends=True):
        done += frames.feed(line, {ID1, ID2})
    assert done == []  # ID2 missing: waits for the next refresh
    done = frames.feed(b"\x1b[H" + json.dumps(stats(ID1)).encode() + b"\n", {ID1, ID2})
    assert [sorted(f) for f in done] == [[ID1]]


def test_frames_skip_garbage_and_split_on_a_repeated_container():
    frames = usage.Frames()
    for junk in (b"\x1b[K\n", b"not json\n", b"[1]\n", b'{"Container": "nope"}\n'):
        assert frames.feed(junk, {ID1, ID2}) == []
    assert frames.feed(json.dumps(stats(ID1)).encode() + b"\n", {ID1, ID2}) == []
    done = frames.feed(json.dumps(stats(ID1)).encode() + b"\n", {ID1, ID2})
    assert [sorted(f) for f in done] == [[ID1]]  # no home sequence, but a new refresh


def test_frames_are_bounded(monkeypatch):
    monkeypatch.setattr(usage, "MAX_CONTAINERS", 2)
    frames = usage.Frames()
    for identifier in (ID1, ID2, ID3):
        frames.feed(json.dumps(stats(identifier)).encode() + b"\n", frozenset())
    assert sorted(frames.current) == [ID1, ID2]


def test_sample_row_drops_docker_names_and_parses_numbers():
    source = {"source": "service", "name": "postgres", "deployment": None}
    row = usage.sample_row(stats(ID1), source)
    assert row == {
        **source,
        "cpu_percent": 0.5,
        "memory_used": round(120.4 * 1024**2),
        "memory_limit": 512 * 1024**2,
        "memory_percent": 23.51,
        "network_rx": 9410,
        "network_tx": 2130,
        "block_read": 4100,
        "block_write": 0,
        "pids": 3,
    }
    stopped = {
        "Container": ID1,
        "CPUPerc": "--",
        "MemUsage": "-- / --",
        "NetIO": "--",
        "BlockIO": "1B / 2B / 3B",
        "PIDs": "--",
    }
    gone = usage.sample_row(stopped, source)
    assert all(gone[key] is None for key in gone if key not in source)
    assert "/Users/someone" not in json.dumps(row)


def test_resolve_matches_like_watch():
    services = [
        [ID1, "postgres", ""],
        [ID2, "postgres", "init"],  # a one-shot step
        [ID3, "BAD\x1b", ""],  # a name we will not print
        ["not-an-id", "litellm", ""],
    ]
    agents = [
        ["4" * 12, AGENT, "agent"],
        ["5" * 12, AGENT, "egress"],
        ["6" * 12, OTHER, "agent"],  # another stack's deployment
        ["7" * 12, "nope", "agent"],
        ["8" * 12, AGENT, "ROLE!"],
        ["x", AGENT, "agent"],
    ]
    found = usage.resolve(services, agents, "stack", {AGENT: {"name": "hello"}})
    assert found == {
        ID1: {"source": "service", "name": "postgres", "deployment": None},
        "4" * 12: {"source": "agent", "name": "hello", "deployment": AGENT},
        "5" * 12: {"source": "agent", "name": "hello", "deployment": AGENT, "role": "egress"},
        "8" * 12: {"source": "agent", "name": "hello", "deployment": AGENT},
    }
    assert usage.service_label("s1") == f"{watch.LABEL_STACK}=s1"


def test_disk_keeps_this_stacks_volumes_and_containers():
    project, stack_id = "agentbreeder-s1", "s1"
    document = {
        "Volumes": [
            {"Name": "agentbreeder-s1-data", "Labels": f"{watch.LABEL_STACK}=s1", "Size": "48MB"},
            {"Name": "agentbreeder-s1-x", "Labels": f"{watch.LABEL_STACK}=s2", "Size": "1B"},
            {"Name": "agentbreeder-s2-data", "Labels": f"{watch.LABEL_STACK}=s1", "Size": "1B"},
            {"Name": "agentbreeder-s1-bad name", "Labels": "", "Size": "1B"},
            {"Name": "agentbreeder-s1-na", "Labels": {watch.LABEL_STACK: "s1"}, "Size": "N/A"},
            "junk",
        ],
        "Containers": [
            {"ID": ID1 + "f" * 52, "Size": "12kB (virtual 1GB)", "Mounts": "/Users/x"},
            {"ID": ID2 + "f" * 52, "Size": "1B"},  # not this stack's
            {"ID": "short", "Size": "1B"},
            {"ID": ID3 + "f" * 52, "Size": None},
            "junk",
        ],
    }
    containers = {
        ID1: {"source": "service", "name": "postgres", "deployment": None},
        ID3: {"source": "agent", "name": "hello", "deployment": AGENT, "role": "egress"},
    }
    reading = usage.disk(document, project, stack_id, containers)
    assert reading == {
        "volumes": [
            {"name": "agentbreeder-s1-data", "size": 48_000_000},
            {"name": "agentbreeder-s1-na", "size": None},
        ],
        "containers": [
            {**containers[ID3], "size": None},
            {**containers[ID1], "size": 12_000},
        ],
    }


def test_texts():
    assert usage.human(None) == "-"
    assert usage.human(512) == "512 B"
    assert usage.human(1536) == "1.5 KiB"
    assert usage.human(3 * 1024**5) == "3072.0 TiB"
    empty = {"time": "T", "containers": []}
    assert usage.sample_text(empty) == "T usage: nothing running"
    row = usage.sample_row(stats(ID1), {"source": "agent", "name": "hello", "role": "egress"})
    row["cpu_percent"] = None
    text = usage.sample_text({"time": "T", "containers": [row]})
    assert "hello (egress)" in text and "cpu       -" in text and "512.0 MiB" in text
    disk = {"as_of": None, "stale": True, "volumes": [], "containers": []}
    assert usage.disk_text(disk) == "disk as of never (stale: the last reading failed): nothing"
    disk = {
        "as_of": "T",
        "stale": False,
        "volumes": [{"name": "v", "size": 1}],
        "containers": [{"name": "pg", "size": 0}],
    }
    assert usage.disk_text(disk) == "disk as of T: v 1 B, pg 0 B"


# -- DockerCLI ---------------------------------------------------------------------------------


def test_labelled_containers_and_disk_usage_shell_out_as_expected():
    calls = []
    outputs = iter(
        [
            f'["{ID1}","postgres",""]\nnot json\n["short"]\n["{ID2}","x","init"]\n',
            json.dumps({"Volumes": []}),
            "nope",
            "[1]",
        ]
    )

    def run(argv, **kwargs):
        calls.append((argv, kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, next(outputs), "")

    docker = DockerCLI(run=run)
    docker.context = "ctx"
    rows = docker.labelled_containers("io.agentbreeder.stack=s1", usage.SERVICE_KEYS)
    assert rows == [[ID1, "postgres", ""], [ID2, "x", "init"]]
    argv = calls[0][0]
    assert argv[:4] == ["docker", "--context", "ctx", "ps"]
    assert argv[4:6] == ["--filter", "label=io.agentbreeder.stack=s1"]
    assert argv[7] == (
        '[{{json .ID}},{{json (.Label "com.docker.compose.service")}},'
        '{{json (.Label "io.agentbreeder.step")}}]'
    )
    assert docker.disk_usage() == {"Volumes": []}
    assert calls[1][0][3:] == ["system", "df", "-v", "--format", "{{json .}}"]
    for _ in range(2):
        with pytest.raises(ServiceError, match="no JSON"):
            docker.disk_usage()


# -- the command -------------------------------------------------------------------------------


def _child(monkeypatch, chunks, linger=60):
    """`docker stats`: write `chunks` (bytes), then wait to be stopped. Ignores SIGTERM, as
    Docker 27.5 does."""
    payload = b"".join(chunks)
    script = (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"sys.stdout.buffer.write({payload!r}); sys.stdout.flush(); time.sleep({linger})\n"
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


def _containers(monkeypatch, fake_docker, answers):
    """`labelled_containers` answers, one (services, agents) pair per resolution; the last one
    repeats. An exception instance is raised instead."""
    calls = []

    def labelled(label, keys):
        if keys == usage.SERVICE_KEYS:  # each resolution lists services first
            calls.append(label)
        index = min(len(calls) - 1, len(answers) - 1)
        answer = answers[index]
        if isinstance(answer, Exception):
            raise answer
        return answer[0] if keys == usage.SERVICE_KEYS else answer[1]

    monkeypatch.setattr(fake_docker, "labelled_containers", labelled, raising=False)
    return calls


def _disk(monkeypatch, fake_docker, documents):
    calls = []

    def disk_usage():
        document = documents[min(len(calls), len(documents) - 1)]
        calls.append(1)
        if isinstance(document, Exception):
            raise document
        return document

    monkeypatch.setattr(fake_docker, "disk_usage", disk_usage, raising=False)
    return calls


def _stop_when(monkeypatch, stopper, count):
    printed = []
    original = usage_command._emit

    def emit(args, event, text):
        original(args, event, text)
        printed.append(event)
        if len(printed) >= count:
            stopper[0].stop("stdin")

    monkeypatch.setattr(usage_command, "_emit", emit)
    return printed


def _out(capsys):
    captured = capsys.readouterr()
    return [json.loads(line) for line in captured.out.splitlines()], captured.err


def test_follow_prints_samples_disk_and_the_end_record(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    _deploy(stack)
    state = _state(stack)
    volume = f"{state.project}-data"
    _containers(
        monkeypatch,
        fake_docker,
        [([[ID1, "postgres", ""], [ID3, "postgres", "init"]], [[ID2, AGENT, "agent"]])],
    )
    _disk(
        monkeypatch,
        fake_docker,
        [
            {
                "Volumes": [
                    {"Name": volume, "Labels": f"{watch.LABEL_STACK}={state.id}", "Size": "1MB"}
                ]
            }
        ],
    )
    started = _child(monkeypatch, [frame_bytes(stats(ID1), stats(ID2))])
    monkeypatch.setenv("DOCKER_HOST", "tcp://elsewhere:2375")
    monkeypatch.setenv("SOME_API_KEY", "seeded-marker-" + "q" * 20)
    _stop_when(monkeypatch, stopper, 2)  # the sample and the disk reading, in any order
    began = time.monotonic()
    assert cli.main(["services", "usage", str(stack), "--follow", "--json"]) == 0
    assert time.monotonic() - began < 10  # the child ignores SIGTERM: it was killed at once
    out, err = _out(capsys)
    sample = next(e for e in out if e["event"] == "sample")
    assert [(r["source"], r["name"]) for r in sample["containers"]] == [
        ("agent", "hello"),
        ("service", "postgres"),
    ]
    disk = next(e for e in out if e["event"] == "disk")
    assert disk["volumes"] == [{"name": volume, "size": 1_000_000}] and not disk["stale"]
    assert disk["as_of"]
    assert out[-1] == {"schema_version": 1, "event": "end", "reason": "stdin", "dropped": 0}
    assert "Usage ended: stdin ended" in err
    argv, environment, process = started[0]
    assert argv == ["docker", "stats", "--format", "{{json .}}", ID1, ID2]
    assert "DOCKER_HOST" not in environment and "SOME_API_KEY" not in environment
    assert process.poll() is not None  # S-10: stopped and reaped
    assert "/Users/someone" not in json.dumps(out)


def test_nothing_running_starts_no_stats_child(stack, fake_docker, capsys, monkeypatch, stopper):
    _containers(monkeypatch, fake_docker, [([], [])])
    _disk(monkeypatch, fake_docker, [ServiceError("DOCKER_FAILED", "x")])
    started = _child(monkeypatch, [])
    assert cli.main(["services", "usage", str(stack), "--json"]) == 0  # one reading
    out, _ = _out(capsys)
    assert started == []  # never `docker stats` without IDs: that would show every container
    assert [e["event"] for e in out] in (["sample", "disk"], ["disk", "sample"])
    assert next(e for e in out if e["event"] == "sample")["containers"] == []
    disk = next(e for e in out if e["event"] == "disk")
    assert disk == {
        "schema_version": 1,
        "event": "disk",
        "as_of": None,
        "stale": True,
        "volumes": [],
        "containers": [],
    }


def test_a_changed_set_replaces_the_child_and_the_disk_cache_goes_stale(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    monkeypatch.setattr(usage_command, "RESOLVE", 0.2)
    monkeypatch.setattr(usage_command, "DISK", 0.2)
    monkeypatch.setattr(usage_command, "SAMPLE_EVERY", 0.0)
    _containers(
        monkeypatch,
        fake_docker,
        [([[ID1, "postgres", ""]], []), ([[ID1, "postgres", ""], [ID2, "litellm", ""]], [])],
    )
    _disk(monkeypatch, fake_docker, [{"Volumes": []}, ServiceError("DOCKER_FAILED", "x")])
    started = _child(monkeypatch, [frame_bytes(stats(ID1))])
    printed = []
    original = usage_command._emit

    def emit(args, event, text):
        original(args, event, text)
        printed.append(event)
        stale = any(e["event"] == "disk" and e["stale"] for e in printed)
        if len(started) == 2 and stale:
            stopper[0].stop("stdin")

    monkeypatch.setattr(usage_command, "_emit", emit)
    assert cli.main(["services", "usage", str(stack), "--follow"]) == 0
    assert [s[0][4:] for s in started] == [[ID1], [ID1, ID2]]
    assert all(s[2].poll() is not None for s in started)  # the replaced one too
    text = capsys.readouterr().out
    assert "postgres" in text and "(stale: the last reading failed)" in text


def test_stats_ending_by_itself_is_an_error(stack, fake_docker, capsys, monkeypatch, stopper):
    _containers(monkeypatch, fake_docker, [([[ID1, "postgres", ""]], [])])
    _disk(monkeypatch, fake_docker, [{}])
    _child(monkeypatch, [], linger=0)
    assert cli.main(["services", "usage", str(stack), "--follow", "--json"]) == 2
    out, err = _out(capsys)
    assert "[DOCKER_STATS_FAILED] docker stats ended unexpectedly" in err
    assert out[-1]["error"]["code"] == "DOCKER_STATS_FAILED"


def test_listing_failures_end_the_follow_after_a_few(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    monkeypatch.setattr(usage_command, "RESOLVE", 0.05)
    _containers(monkeypatch, fake_docker, [ServiceError("DOCKER_FAILED", "gone")])
    _disk(monkeypatch, fake_docker, [{}])
    began = time.monotonic()
    assert cli.main(["services", "usage", str(stack), "--follow", "--json"]) == 2
    assert time.monotonic() - began < 10
    assert "could not be listed" in capsys.readouterr().err


def test_one_reading_fails_at_once_when_docker_cannot_list(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    _containers(monkeypatch, fake_docker, [ServiceError("DOCKER_FAILED", "gone")])
    assert cli.main(["services", "usage", str(stack)]) == 2
    assert "[DOCKER_STATS_FAILED]" in capsys.readouterr().err


def test_a_child_that_cannot_start(stack, fake_docker, capsys, monkeypatch, stopper):
    _containers(monkeypatch, fake_docker, [([[ID1, "postgres", ""]], [])])
    _disk(monkeypatch, fake_docker, [{}])

    def refuse(argv, environment, merge_stderr=False):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(runtime, "start_follow", refuse)
    assert cli.main(["services", "usage", str(stack), "--json"]) == 2
    assert "[DOCKER_STATS_FAILED] docker stats could not start" in capsys.readouterr().err


def test_a_stop_before_the_first_resolution(stack, fake_docker, capsys, monkeypatch, stopper):
    def slow(label, keys):
        stopper[0].stop("signal")  # SIGTERM while the first listing runs
        return []

    monkeypatch.setattr(fake_docker, "labelled_containers", slow, raising=False)
    _disk(monkeypatch, fake_docker, [{}])
    started = _child(monkeypatch, [])
    assert cli.main(["services", "usage", str(stack), "--follow", "--json"]) == 0
    out, _ = _out(capsys)
    assert out[-1]["reason"] == "signal" and started == []


def test_a_closed_stdout_ends_quietly(stack, fake_docker, capsys, monkeypatch, stopper):
    _containers(monkeypatch, fake_docker, [([[ID1, "postgres", ""]], [])])
    _disk(monkeypatch, fake_docker, [{}])
    started = _child(monkeypatch, [frame_bytes(stats(ID1))])

    def broken(args, event, text):
        raise BrokenPipeError

    monkeypatch.setattr(usage_command, "_emit", broken)
    monkeypatch.setattr("agentbreeder.commands.watch._quiet_stdout", lambda: None)
    assert cli.main(["services", "usage", str(stack), "--follow", "--json"]) == 0
    assert started[0][2].poll() is not None


def test_a_ctrl_c_stops_it(stack, fake_docker, capsys, monkeypatch, stopper):
    _containers(monkeypatch, fake_docker, [([[ID1, "postgres", ""]], [])])
    _disk(monkeypatch, fake_docker, [{}])
    started = _child(monkeypatch, [frame_bytes(stats(ID1))])

    def interrupt(args, event, text):
        raise KeyboardInterrupt

    monkeypatch.setattr(usage_command, "_emit", interrupt)
    assert cli.main(["services", "usage", str(stack), "--follow", "--json"]) == 0
    out, err = _out(capsys)
    assert out[-1]["reason"] == "interrupt" and "Usage ended: interrupted" in err
    assert started[0][2].poll() is not None


def test_dropped_records_are_reported(stack, fake_docker, capsys, monkeypatch, stopper):
    _containers(monkeypatch, fake_docker, [([[ID1, "postgres", ""]], [])])
    _disk(monkeypatch, fake_docker, [{}])
    _child(monkeypatch, [frame_bytes(stats(ID1))])
    original = usage_command.watch_command._Records

    def records():
        made = original()
        made.dropped = 3
        return made

    monkeypatch.setattr(usage_command.watch_command, "_Records", records)
    _stop_when(monkeypatch, stopper, 2)
    assert cli.main(["services", "usage", str(stack), "--follow", "--json"]) == 0
    out, err = _out(capsys)
    assert "3 records were dropped" in err and out[-1]["dropped"] == 3


def test_pump_drops_over_long_lines(monkeypatch):
    monkeypatch.setattr(usage_command, "LINE_LIMIT", 64)
    records = usage_command.watch_command._Records()
    long = json.dumps(stats(ID1)).encode() + b"\n"
    good = b"\x1b[H" + json.dumps({"Container": ID2}).encode() + b"\n"

    class Process:
        stdout = __import__("io").BytesIO(long + good)

    usage_command._pump(Process, 7, frozenset({ID2}), records)
    items = []
    while not records.queue.empty():
        items.append(records.queue.get())
    assert items[-1] == ("end", 7)
    empty = dict.fromkeys(usage.STATS_FIELDS)
    assert items == [("frame", 7, {ID2: empty}), ("end", 7)]  # the long one: dropped


def test_usage_refuses_a_stack_never_brought_up(env, capsys):
    assert cli.main(["services", "usage", str(env), "--json"]) == 2
    captured = capsys.readouterr()
    assert "[STACK_NOT_UP]" in captured.err
    assert json.loads(captured.out)["error"]["code"] == "STACK_NOT_UP"


def test_usage_without_docker(stack, fake_docker, capsys, monkeypatch):
    def refuse():
        raise ServiceError("DOCKER_UNAVAILABLE", "no docker")

    monkeypatch.setattr(fake_docker, "authorize", refuse)
    assert cli.main(["services", "usage", str(stack)]) == 2
    assert "[DOCKER_UNAVAILABLE] no docker" in capsys.readouterr().err


# -- review round 1 ----------------------------------------------------------------------------


def test_frames_keep_only_the_stats_fields():
    frames = usage.Frames()
    (done,) = frames.feed(json.dumps(stats(ID1)).encode() + b"\n", {ID1})
    assert set(done[ID1]) == set(usage.STATS_FIELDS)  # `Name` (a home path here) is gone


def test_resolve_bounds_and_skips_deployment_names():
    rows = [["4" * 12, AGENT, "agent"], ["5" * 12, OTHER, "agent"]]
    deployments = {AGENT: {"name": "two\nlines" + "x" * 99}, OTHER: {}}
    found = usage.resolve([], rows, "stack", deployments)
    assert list(found) == ["4" * 12]  # the row without a name is skipped
    name = found["4" * 12]["name"]
    assert "\n" not in name and len(name) == usage.NAME_LIMIT


def test_next_does_not_spin_on_a_pending_worker():
    assert usage_command._next(10.0, False, 5.0, 12.0) == 10.0
    assert usage_command._next(10.0, True, 5.0, 9.0) == 10.0
    assert usage_command._next(10.0, True, 5.0, 12.0) == 15.0


def test_samples_are_throttled(monkeypatch):
    printed = []
    monkeypatch.setattr(usage_command, "_emit", lambda args, event, text: printed.append(event))
    loop = usage_command._Loop(None, None, None, None, None, None)
    loop.containers = {ID1: {"source": "service", "name": "postgres", "deployment": None}}
    loop.sample({ID1: stats(ID1)})
    loop.sample({ID1: stats(ID1)})  # within SAMPLE_EVERY: dropped
    loop.sample({}, force=True)
    assert [len(e["containers"]) for e in printed] == [1, 0]


def _children(monkeypatch, scripts):
    """`docker stats` children, the n-th running scripts[n] (the last one repeats)."""
    started = []

    def start(argv, environment, merge_stderr=False):
        script = scripts[min(len(started), len(scripts) - 1)]
        process = subprocess.Popen(
            [sys.executable, "-c", script], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE
        )
        started.append((argv, environment, process))
        return process

    monkeypatch.setattr(runtime, "start_follow", start)
    return started


def _printing(chunk: bytes, linger=60) -> str:
    return (
        "import signal, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"sys.stdout.buffer.write({chunk!r}); sys.stdout.flush(); time.sleep({linger})\n"
    )


def test_one_reading_from_a_child_that_ended_at_once_is_retried(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    # Docker 27.5: `docker stats` exits 1 at once when a listed container is already gone.
    _containers(monkeypatch, fake_docker, [([[ID1, "postgres", ""]], [])])
    _disk(monkeypatch, fake_docker, [{}])
    started = _children(monkeypatch, [_printing(b"", 0), _printing(frame_bytes(stats(ID1)))])
    assert cli.main(["services", "usage", str(stack), "--json"]) == 0
    out, _ = _out(capsys)
    assert sorted(e["event"] for e in out) == ["disk", "sample"]
    assert next(e for e in out if e["event"] == "sample")["containers"][0]["name"] == "postgres"
    assert [s[0][4:] for s in started] == [[ID1], [ID1]]  # the same set, a new child
    assert all(s[2].poll() is not None for s in started)


def test_one_reading_has_a_deadline(stack, fake_docker, capsys, monkeypatch, stopper):
    monkeypatch.setattr(usage_command, "ONCE_TIMEOUT", 0.3)
    _containers(monkeypatch, fake_docker, [([[ID1, "postgres", ""]], [])])
    _disk(monkeypatch, fake_docker, [{}])
    started = _child(monkeypatch, [])  # never a frame
    assert cli.main(["services", "usage", str(stack)]) == 2
    assert "[DOCKER_STATS_FAILED] no reading within 0 s" in capsys.readouterr().err
    assert started[0][2].poll() is not None


def test_a_ctrl_c_while_replacing_still_stops_the_old_child(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    monkeypatch.setattr(usage_command, "RESOLVE", 0.1)
    _containers(
        monkeypatch,
        fake_docker,
        [([[ID1, "postgres", ""]], []), ([[ID2, "litellm", ""]], [])],
    )
    _disk(monkeypatch, fake_docker, [{}])
    started = _child(monkeypatch, [frame_bytes(stats(ID1))])
    original = runtime.stop_follow
    calls = []

    def stop(process, grace=2.0):
        calls.append(process)
        if len(calls) == 1:
            raise KeyboardInterrupt  # before the old child was signalled
        original(process, grace=grace)

    monkeypatch.setattr(runtime, "stop_follow", stop)
    assert cli.main(["services", "usage", str(stack), "--follow", "--json"]) == 0
    out, _ = _out(capsys)
    assert out[-1]["reason"] == "interrupt"
    assert len(started) == 1 and started[0][2].poll() is not None  # swept at the end


def test_a_listing_failure_then_success_resets_the_count(
    stack, fake_docker, capsys, monkeypatch, stopper
):
    monkeypatch.setattr(usage_command, "RESOLVE", 0.05)
    monkeypatch.setattr(usage_command, "RESOLVE_FAILURES", 2)
    error = ServiceError("DOCKER_FAILED", "gone")
    # Two failures with a success between them: without the reset, the second would end it.
    _containers(monkeypatch, fake_docker, [error, ([], []), error, ([[ID1, "postgres", ""]], [])])
    _disk(monkeypatch, fake_docker, [{}])
    _child(monkeypatch, [frame_bytes(stats(ID1))])
    original = usage_command._emit

    def emit(args, event, text):
        original(args, event, text)
        if event["event"] == "sample" and event["containers"]:
            stopper[0].stop("stdin")

    monkeypatch.setattr(usage_command, "_emit", emit)
    assert cli.main(["services", "usage", str(stack), "--follow", "--json"]) == 0
    out, _ = _out(capsys)
    assert out[-1]["reason"] == "stdin"
