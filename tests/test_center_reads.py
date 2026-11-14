"""The console's in-process read layer (spec §8.1 #3) and the guard on what the server imports."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentbreeder.center import reads
from agentbreeder.center.reads import Reads

AGENT = (
    "kind: agent\nschema_version: 1\nname: tiny\nruntime: runtime/adk@1.0.0\n"
    "model: {alias: m}\nprompt: {path: prompts/dev.md}\n"
)
# The only AgentBreeder modules the long-running server process may load (spec §8.1 #3, §10 I3):
# an allowlist, so a new import of state, secrets, Docker, the operator config, the audit chain,
# registry_deployed or the CLI fails the guard.
ALLOWED = {
    "agentbreeder",
    "agentbreeder._version",
    "agentbreeder.agentfile",
    # `next --json`'s models (#820), checked in process; pure pydantic, no state or Docker.
    "agentbreeder.guide",
    "agentbreeder.lock",
    "agentbreeder.manifest",
    "agentbreeder.redaction",
    "agentbreeder.registry",
    "agentbreeder.strict_yaml",
    "agentbreeder.center",
    "agentbreeder.center.__main__",
    "agentbreeder.center.files",
    "agentbreeder.center.instance",
    "agentbreeder.center.jobs",
    "agentbreeder.center.launch",
    "agentbreeder.center.reads",
    "agentbreeder.center.server",
    # Try it (#823): pure request and answer checks; it imports only center.files.
    "agentbreeder.center.tryit",
    # Agents (#823): pure shaping of `list --all` and `audit list` answers.
    "agentbreeder.center.agents",
    # stack.yaml's agents for the identity strip (#823): pydantic models, no state or Docker.
    "agentbreeder.services",
    "agentbreeder.services.schema",
}


@pytest.fixture
def project(tmp_path) -> Path:
    path = tmp_path / "proj"
    (path / "prompts").mkdir(parents=True)
    (path / "prompts" / "dev.md").write_text("Be helpful.\n")
    (path / "agent.yaml").write_text(AGENT)
    (path / "stack.yaml").write_text("kind: stack\n")
    return path


def _cli(*argv: str) -> object:
    result = subprocess.run(
        [sys.executable, "-m", "agentbreeder", *argv],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_reads_are_cached_by_what_they_read(project):
    layer = Reads()
    first = layer.agent(str(project))
    assert first["data"]["name"] == "tiny" and layer.agent(str(project)) is first
    time.sleep(0.01)
    (project / "agent.yaml").write_text(AGENT.replace("tiny", "small"))
    second = layer.agent(str(project))
    assert second is not first and second["data"]["name"] == "small"
    layer.invalidate()
    assert layer.agent(str(project)) is not second


def test_a_published_version_changes_the_listing(project):
    layer = Reads()
    before = layer.registry_versions(str(project))["data"]
    version = project / "registry" / "prompt" / "local" / "1.0.0"
    version.mkdir(parents=True)
    rows = layer.registry_versions(str(project))["data"]
    assert len(rows) == len(before) + 1 and rows[0]["ref"] == "prompt/local@1.0.0"


def test_read_errors_carry_the_code(project):
    layer = Reads()
    (project / "agent.lock").write_text("not json")
    assert layer.lock(str(project))["error"]["code"] == "LOCK_INVALID"
    (project / "agent.lock").unlink()
    assert layer.lock(str(project)) == {"data": {}, "error": None, "stale": False}
    (project / "agent.yaml").write_text("kind: agent\nname: x\n")
    problem = layer.agent(str(project))["error"]
    assert problem["code"] and not problem["message"].startswith("[")


def test_errors_are_not_cached(project, monkeypatch):
    layer = Reads()
    (project / "agent.lock").write_text("not json")
    assert layer.lock(str(project))["error"]
    assert ("lock", str(project)) not in layer.cache


def test_a_job_end_drops_the_read_cache(tmp_path, project):
    from agentbreeder.center.server import Center

    directory = tmp_path / "center"
    directory.mkdir()
    center = Center(directory)
    center.reads.agent(str(project))
    assert center.reads.cache
    center.runner.invalidate()
    assert center.reads.cache == {}


def test_used_by_matches_the_cli_files_part(project):
    """Declared and locked come from the same `registry.usage` as `registry used-by`."""
    layer = Reads()
    answer = layer.used_by(str(project), "runtime/adk@1.0.0")
    assert answer["error"] is None
    cli = _cli("registry", "used-by", "runtime/adk@1.0.0", "--project", str(project), "--json")
    row = cli["projects"][0]
    for key, value in answer["data"].items():
        if key in row:
            assert row[key] == value, key
    assert answer["data"]["declared"]
    assert layer.used_by(str(project), "runtime/adk@1.0.0") is answer


def test_used_by_refuses_a_bad_ref(project):
    answer = Reads().used_by(str(project), "not a ref")
    assert answer["data"] is None and answer["error"]["code"]


def test_a_fifo_or_huge_file_cannot_hang_or_bloat_the_server(project):
    """A planted FIFO or an oversized file is refused, never read (security review)."""
    layer = Reads()
    os.mkfifo(project / "agent.lock")
    started = time.monotonic()
    answer = layer.lock(str(project))
    assert answer["error"]["code"] == "ENTRY_UNSAFE" and time.monotonic() - started < 5
    (project / "agent.lock").unlink()
    (project / "agent.lock").write_bytes(b" " * (reads.registry.LOCK_FILE_LIMIT + 1))
    assert layer.lock(str(project))["error"]["code"] == "ENTRY_UNSAFE"
    (project / "agent.yaml").unlink()
    os.mkfifo(project / "agent.yaml")
    assert Reads().agent(str(project))["error"]["code"] == "ENTRY_UNSAFE"
    (project / "agent.yaml").unlink()
    assert Reads().agent(str(project))["error"]["code"] == "NO_AGENT_FILE"


def test_lock_entries_match_lock_load(project):
    from agentbreeder import lock as lock_module

    body = {
        "lock_version": lock_module.LOCK_VERSION,
        "entries": {"prompt/x@1.0.0": {"sha256": "a"}},
    }
    (project / "agent.lock").write_text(json.dumps(body))
    assert (
        Reads().lock(str(project))["data"] == lock_module.Lock.load(project / "agent.lock").entries
    )
    for bad in (b"[]", json.dumps({"lock_version": 99}).encode(), b"\xff", b"{"):
        (project / "agent.lock").write_bytes(bad)
        assert Reads().lock(str(project))["error"]["code"] == "LOCK_INVALID"
    (project / "agent.lock").write_text(json.dumps({"lock_version": lock_module.LOCK_VERSION}))
    assert Reads().lock(str(project))["error"]["code"] == "LOCK_INVALID"


def _ready(read, timeout: float = 120) -> dict:
    """Ask until the answer is neither VERIFYING nor stale."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        answer = read()
        pending = answer["error"] is not None and answer["error"]["code"] == "VERIFYING"
        if not pending and not answer["stale"]:
            return answer
        time.sleep(0.05)
    raise AssertionError("verify never finished")


def _real_cli(tmp_path):
    from agentbreeder.center.jobs import CenterState, Runner

    directory = tmp_path / "runner"
    directory.mkdir(exist_ok=True)
    return Runner(CenterState(directory), directory, [sys.executable, "-m", "agentbreeder"]).read


def test_registry_reads_match_the_cli(project, tmp_path):
    """`registry list` in process and `registry verify` as a CLI child: the CLI's answers."""
    layer = Reads(run_cli=_real_cli(tmp_path))
    versions = layer.registry_versions(str(project))
    assert versions["error"] is None and versions["data"]
    assert versions["data"] == _cli("registry", "list", "--project", str(project), "--json")
    first = layer.registry_problems(str(project))
    assert first["error"] == {"code": "VERIFYING", "message": "Verifying the registries"}
    problems = _ready(lambda: layer.registry_problems(str(project)))
    assert problems["error"] is None
    assert problems["data"] == _cli("registry", "verify", "--project", str(project), "--json")


def test_verify_keeps_the_last_answer_stale_after_a_job_ends(project):
    calls = []

    def fake(argv, timeout, **options):
        calls.append((argv, timeout))
        time.sleep(0.2)
        return {"data": [len(calls)], "error": None, "stale": False}

    layer = Reads(run_cli=fake)
    assert _ready(lambda: layer.registry_problems(str(project)))["data"] == [1]
    assert calls[0][0][:2] == ["registry", "verify"] and calls[0][1] == reads.VERIFY_TIMEOUT
    assert layer.registry_problems(str(project))["data"] == [1]  # fresh: no second run
    layer.invalidate()
    stale = layer.registry_problems(str(project))
    assert stale["stale"] is True and stale["data"] == [1]
    assert _ready(lambda: layer.registry_problems(str(project)))["data"] == [2]


def test_a_verify_that_fails_or_raises_never_sticks_on_verifying(project):
    def broken(argv, timeout, **options):
        raise RuntimeError("boom")

    layer = Reads(run_cli=broken)
    answer = _ready(lambda: layer.registry_problems(str(project)))
    assert answer["error"]["code"] == "VERIFY_FAILED" and layer.verifying == set()
    timed_out = Reads(
        run_cli=lambda argv, timeout, **options: {
            "data": None,
            "error": {"code": "TIMEOUT", "message": "The command timed out"},
            "stale": False,
        }
    )
    assert _ready(lambda: timed_out.registry_problems(str(project)))["error"]["code"] == "TIMEOUT"
    no_cli = Reads()
    assert no_cli.registry_problems(str(project))["error"]["code"] == "VERIFYING"
    answer = _ready(lambda: no_cli.registry_problems(str(project)), 5)
    assert answer["error"]["code"] == "VERIFY_UNAVAILABLE"


def test_a_fifo_manifest_cannot_hang_the_server(project, tmp_path):
    """Verify opens every file; it runs as a CLI child, so a FIFO only costs that child's
    timeout, and the listing (directory names only) answers at once."""
    version = project / "registry" / "prompt" / "x" / "1.0.0"
    version.mkdir(parents=True)
    os.mkfifo(version / "manifest.yaml")
    layer = Reads(
        run_cli=lambda argv, timeout, **options: (
            time.sleep(0.1)
            or {"data": None, "error": {"code": "TIMEOUT", "message": "x"}, "stale": False}
        )
    )
    started = time.monotonic()
    rows = layer.registry_versions(str(project))["data"]
    assert any(row["ref"] == "prompt/x@1.0.0" for row in rows)
    assert layer.registry_problems(str(project))["error"]["code"] == "VERIFYING"
    assert time.monotonic() - started < 5


def test_verify_is_not_rerun_on_every_request(project, monkeypatch):
    monkeypatch.setattr(reads, "MAX_TREE_ENTRIES", 0)
    (project / "registry" / "prompt" / "x").mkdir(parents=True)
    calls = []
    layer = Reads(
        run_cli=lambda argv, timeout, **options: (
            calls.append(1) or {"data": [], "error": None, "stale": False}
        )
    )
    _ready(lambda: layer.registry_problems(str(project)))
    for _ in range(20):
        layer.registry_problems(str(project))
    assert len(calls) == 1
    assert layer.registry_versions(str(project))["error"] is None and layer.cache == {}


def test_a_linked_name_directory_is_covered_by_the_signature(project, tmp_path):
    outside = tmp_path / "outside"
    (outside / "1.0.0").mkdir(parents=True)
    (project / "registry" / "prompt").mkdir(parents=True)
    (project / "registry" / "prompt" / "linked").symlink_to(outside)
    layer = Reads()
    before = {row["ref"] for row in layer.registry_versions(str(project))["data"]}
    (outside / "2.0.0").mkdir()
    after = {row["ref"] for row in layer.registry_versions(str(project))["data"]}
    assert after - before == {"prompt/linked@2.0.0"}


def test_a_read_racing_a_job_end_is_not_cached(project, monkeypatch):
    layer = Reads()
    real = reads.agentfile.load_agent_file

    def racing(path):
        layer.invalidate()  # a job ends while this read runs
        return real(path)

    monkeypatch.setattr(reads.agentfile, "load_agent_file", racing)
    assert layer.agent(str(project))["error"] is None
    assert ("agent", str(project)) not in layer.cache


def test_the_cache_is_bounded(project, monkeypatch):
    monkeypatch.setattr(reads, "MAX_CACHED", 3)
    layer = Reads()
    for version in range(6):
        layer.used_by(str(project), f"prompt/x@1.0.{version}")
    assert len(layer.cache) == 3


def test_a_deeply_nested_lock_is_lock_invalid_not_a_crash(project):
    (project / "agent.lock").write_text("[" * 200000)
    assert Reads().lock(str(project))["error"]["code"] == "LOCK_INVALID"


def test_a_same_size_edit_with_the_same_mtime_is_seen(project):
    layer = Reads()
    path = project / "agent.yaml"
    assert layer.agent(str(project))["data"]["name"] == "tiny"
    info = path.stat()
    path.write_text(AGENT.replace("tiny", "tinz"))
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert layer.agent(str(project))["data"]["name"] == "tinz"  # ctime and inode still change


def test_a_relative_registry_path_is_made_absolute_at_start(tmp_path, monkeypatch):
    from agentbreeder.center import __main__ as entry

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENTBREEDER_REGISTRY_PATH", os.pathsep.join(["shared", "/abs", ""]))
    entry._absolute_registry_path()
    assert os.environ["AGENTBREEDER_REGISTRY_PATH"].split(os.pathsep) == [
        str(tmp_path / "shared"),
        "/abs",
        "",
    ]


def test_the_server_process_loads_only_allowed_modules(tmp_path, project):
    """The real console process, `python -I`, driven through every read (the views, used-by
    and a finished verify): only allowlisted AgentBreeder modules are loaded."""
    script = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "import agentbreeder.center.__main__\n"
        "from agentbreeder.center import server\n"
        f"directory = Path({str(tmp_path / 'center')!r})\n"
        "directory.mkdir(mode=0o700)\n"
        "center = server.Center(directory)\n"
        f"project = center.register(Path({str(project)!r}))\n"
        "for view in ('registry', 'agent', 'lock'):\n"
        "    center.read_view(project, view)\n"
        "request = server.Request(center, {'project': project['id']}, None,\n"
        "                         {'ref': ['runtime/adk@1.0.0']})\n"
        "server._used_by(request)\n"
        "deadline = time.monotonic() + 120\n"
        "while center.reads.verifying and time.monotonic() < deadline:\n"
        "    time.sleep(0.05)\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('agentbreeder'))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    loaded = set(json.loads(result.stdout.strip().splitlines()[-1]))
    assert {"agentbreeder.center.reads", "agentbreeder.registry"} <= loaded
    assert loaded - ALLOWED == set()


def test_verify_reports_problems_from_the_real_cli(project, tmp_path):
    """`registry verify --json` exits 2 when it finds a problem; its rows are the answer."""
    (project / "registry" / "prompt" / "broken" / "1.0.0").mkdir(parents=True)
    layer = Reads(run_cli=_real_cli(tmp_path))
    answer = _ready(lambda: layer.registry_problems(str(project)))
    assert answer["error"] is None and answer["data"], answer
    assert answer["data"] == _cli_any("registry", "verify", "--project", str(project), "--json")


def test_verify_reruns_after_its_ttl(project, monkeypatch):
    """Edits inside a version are invisible to the listing signature; verify reruns anyway."""
    monkeypatch.setattr(reads, "VERIFY_TTL", 0.2)
    calls = []
    layer = Reads(
        run_cli=lambda argv, timeout, **options: (
            calls.append(1) or {"data": [len(calls)], "error": None, "stale": False}
        )
    )
    _ready(lambda: layer.registry_problems(str(project)))
    time.sleep(0.3)
    assert layer.registry_problems(str(project))["stale"] is True
    assert _ready(lambda: layer.registry_problems(str(project)))["data"] == [2]


def test_a_tracked_read_is_stopped_with_the_console(tmp_path):
    """A verify child blocked on a file must not outlive the console."""
    import threading

    from agentbreeder.center.jobs import CenterState, Runner

    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(60)\n")
    directory = tmp_path / "runner"
    directory.mkdir()
    runner = Runner(CenterState(directory), directory, [sys.executable, str(script)])
    answers = []
    thread = threading.Thread(
        target=lambda: answers.append(runner.read(["x"], timeout=120, cache=False, track=True))
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not runner.children and time.monotonic() < deadline:
        time.sleep(0.05)
    (child,) = list(runner.children)
    runner.shutdown(grace=0.5)
    thread.join(10)
    assert child.poll() is not None and answers and answers[0]["error"]
    assert runner.children == set() and runner.cache == {}


def test_an_allowed_exit_without_json_keeps_its_code(tmp_path):
    from agentbreeder.center.jobs import CenterState, Runner

    script = tmp_path / "coded.py"
    script.write_text("import sys\nsys.stderr.write('[LOCK_INVALID] bad lock\\n')\nsys.exit(2)\n")
    directory = tmp_path / "runner"
    directory.mkdir()
    runner = Runner(CenterState(directory), directory, [sys.executable, str(script)])
    answer = runner.read(["x"], ok_codes=(0, 2))
    assert answer["error"] == {"code": "LOCK_INVALID", "message": "bad lock"}


def _cli_any(*argv: str) -> object:
    result = subprocess.run(
        [sys.executable, "-m", "agentbreeder", *argv], capture_output=True, text=True, timeout=120
    )
    assert result.returncode in (0, 2), result.stderr
    return json.loads(result.stdout)


def test_a_grandchild_holding_the_pipes_cannot_hold_a_read_past_its_timeout(tmp_path):
    from agentbreeder.center.jobs import CenterState, Runner

    directory = tmp_path / "runner"
    directory.mkdir()
    runner = Runner(CenterState(directory), directory, ["/bin/sh", "-c"])
    started = time.monotonic()
    answer = runner.read(["sleep 30 & sleep 60"], timeout=1)
    assert answer["error"]["code"] == "TIMEOUT" and time.monotonic() - started < 10


def test_no_tracked_read_starts_once_the_console_is_stopping(tmp_path):
    from agentbreeder.center.jobs import CenterState, Runner

    directory = tmp_path / "runner"
    directory.mkdir()
    runner = Runner(CenterState(directory), directory, [sys.executable, "-c", "print(1)"])
    runner.shutdown(grace=0)
    assert runner.read(["x"], track=True)["error"]["code"] == "STOPPING"
    assert runner.children == set()
