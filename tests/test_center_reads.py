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
# Modules the long-running server process must never load (spec §8.1 #3, §10 I3): deployment
# state, stack state and secrets, Docker, the operator config, the audit chain, and the CLI
# itself (which imports all of them).
FORBIDDEN = (
    "agentbreeder.cli",
    "agentbreeder.store",
    "agentbreeder.deployment",
    "agentbreeder.deployer",
    "agentbreeder.runtime",
    "agentbreeder.operator_config",
    "agentbreeder.audit",
    "agentbreeder.secret_files",
    "agentbreeder.services",
    "agentbreeder.services.state",
    "agentbreeder.gate",
    "agentbreeder.release",
    "agentbreeder.promote",
    "agentbreeder.doctor",
    "agentbreeder.kubernetes",
    "agentbreeder.egress",
    "agentbreeder.registry_deployed",
)


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


def _until_ready(read, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        answer = read()
        if answer["error"] is None or answer["error"]["code"] != "VERIFYING":
            return answer
        time.sleep(0.05)
    raise AssertionError("verify never finished")


def test_registry_reads_match_the_cli(project):
    """The same functions as `registry list` and `registry verify`, so the same answers."""
    layer = Reads()
    versions = layer.registry_versions(str(project))
    assert versions["error"] is None and versions["data"]
    assert versions["data"] == _cli("registry", "list", "--project", str(project), "--json")
    first = layer.registry_problems(str(project))
    assert first["error"] == {"code": "VERIFYING", "message": "Verifying the registries"}
    problems = _until_ready(lambda: layer.registry_problems(str(project)))
    assert problems["error"] is None
    assert problems["data"] == _cli("registry", "verify", "--project", str(project), "--json")


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


def test_verify_returns_the_last_answer_as_stale_while_it_reruns(project, monkeypatch):
    layer = Reads()
    _until_ready(lambda: layer.registry_problems(str(project)))
    (project / "registry").mkdir()  # the tree changed: verify runs again
    real = reads.registry.verify
    monkeypatch.setattr(reads.registry, "verify", lambda search: time.sleep(0.5) or real(search))
    again = layer.registry_problems(str(project))
    assert again["stale"] is True and again["error"] is None
    deadline = time.monotonic() + 30
    while layer.registry_problems(str(project))["stale"]:
        assert time.monotonic() < deadline, "the rerun never finished"
        time.sleep(0.05)


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


def test_a_failing_verify_reports_its_code(project, monkeypatch):
    def broken(search):
        raise reads.registry.RegistryError("REGISTRY_BROKEN", "bad tree")

    monkeypatch.setattr(reads.registry, "verify", broken)
    monkeypatch.setattr(reads.registry, "listing", broken)
    layer = Reads()
    assert layer.registry_versions(str(project))["error"]["code"] == "REGISTRY_BROKEN"
    answer = _until_ready(lambda: layer.registry_problems(str(project)))
    assert answer["error"] == {"code": "REGISTRY_BROKEN", "message": "bad tree"}


def test_a_huge_tree_is_read_but_not_cached(project, monkeypatch):
    monkeypatch.setattr(reads, "MAX_TREE_ENTRIES", 1)
    layer = Reads()
    assert layer.registry_versions(str(project))["error"] is None
    assert layer.cache == {}


def test_a_job_end_drops_the_read_cache(tmp_path, project):
    from agentbreeder.center.server import Center

    directory = tmp_path / "center"
    directory.mkdir()
    center = Center(directory)
    center.reads.agent(str(project))
    assert center.reads.cache
    center.runner.invalidate()
    assert center.reads.cache == {}


def test_the_server_process_never_loads_state_secret_docker_config_or_audit(tmp_path, project):
    """The real console process: `python -I -m agentbreeder.center`'s imports, plus every
    in-process read, leave none of the forbidden modules loaded."""
    script = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "import agentbreeder.center.__main__\n"
        "from agentbreeder.center.server import Center\n"
        f"directory = Path({str(tmp_path / 'center')!r})\n"
        "directory.mkdir(mode=0o700)\n"
        "center = Center(directory)\n"
        f"project = {str(project)!r}\n"
        "center.reads.registry_versions(project)\n"
        "center.reads.agent(project)\n"
        "center.reads.lock(project)\n"
        "center.reads.used_by(project, 'runtime/adk@1.0.0')\n"
        "import agentbreeder.registry as registry\n"
        "registry.verify(registry.roots(Path(project)))\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    loaded = set(json.loads(result.stdout))
    assert "agentbreeder.center.reads" in loaded and "agentbreeder.registry" in loaded
    leaked = sorted(
        name
        for name in loaded
        if any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN)
    )
    assert leaked == []


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
