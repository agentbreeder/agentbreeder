"""`registry used-by`: declared, locked and deployed, reported separately (#824, M8)."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agentbreeder import cli, registry, registry_deployed
from agentbreeder.registry import Ref
from agentbreeder.services.state import stack_id
from agentbreeder.store import Store

# The build tests' fixtures and fake image builder, to produce a real build record. Bound under
# other names (pytest registers them by these names here), so they do not clash with this
# module's `project` variables; the test fetches them with request.getfixturevalue.
build_tests = importlib.import_module("test_build")
build_project_fixture = build_tests.project
build_operator_fixture = build_tests.operator

NORTHWIND = Path(registry.__file__).parent / "examples" / "northwind"
ORDERS = Ref.parse("tool/orders@1.0.0")
IMAGE = "sha256:" + "a" * 64
OTHER_IMAGE = "sha256:" + "b" * 64
ESC = "\x1b"
AGENT = "northwind-support"


@pytest.fixture(autouse=True)
def stacks(tmp_path, monkeypatch):
    root = tmp_path / "stacks"
    monkeypatch.setenv("AGENTBREEDER_STACKS_DIR", str(root))
    return root


@pytest.fixture
def northwind(tmp_path):
    """A copy of Northwind's project files (no registry needed: used-by reads refs, not
    versions)."""
    project = tmp_path / "northwind"
    project.mkdir()
    for name in ("agent.yaml", "agent.lock", "stack.yaml", "stack.lock"):
        shutil.copy(NORTHWIND / name, project / name)
    return project


def _sha(path: Path) -> str | None:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def write_record(project: Path, image: str = IMAGE, refs=("tool/orders@1.0.0",), **extra):
    """A build record as `build` writes it, for the project's current files."""
    (project / "deploy").mkdir(exist_ok=True)
    record = {
        "name": AGENT,
        "environment": "dev",
        "image": image,
        "agent_yaml_sha256": _sha(project / "agent.yaml"),
        "lock_sha256": _sha(project / "agent.lock"),
        "refs": [{"ref": r} for r in refs],
        **extra,
    }
    (project / f"deploy/{AGENT}.build.json").write_text(json.dumps(record))


def add_deployment(state: Path, identifier: str, name: str, image: str, status="ready"):
    """A recorded deployment; states as store.py writes them (pending, starting, ready,
    failed, removing, removed)."""
    store = Store(state)
    try:
        manifest = {"name": name, "image": image, "target": "local"}
        store.create(identifier, manifest, "sha256:" + "0" * 64, "ctx", environment="dev")
        if status != "pending":
            store.transition(identifier, status, endpoint="http://127.0.0.1:1")
    finally:
        store.close()


def used(project: Path, state: Path, ref: Ref = ORDERS) -> dict:
    row = registry.usage(ref, project)
    return registry_deployed.deployed(ref, row, project, [state])


# -- declared and locked (pure) ------------------------------------------------------------------


def test_usage_reports_declared_and_locked_separately(northwind):
    row = registry.usage(ORDERS, northwind)
    assert row["project"] == str(northwind.resolve())
    assert row["agent"] == AGENT and row["stack"] == "northwind"
    assert row["declared"] == [{"file": "agent.yaml", "name": AGENT, "at": "tools[2].ref"}]
    lock = json.loads((NORTHWIND / "agent.lock").read_text())["entries"]["tool/orders@1.0.0"]
    assert row["locked"] == [{"file": "agent.lock", **lock}]
    assert row["errors"] == []


def test_usage_reads_stack_files_and_separates_the_two(northwind):
    service = registry.usage(Ref.parse("service/northwind-ops@1.0.0"), northwind)
    assert [(d["file"], d["at"]) for d in service["declared"]] == [
        ("stack.yaml", "services.ops.ref")
    ]
    assert [entry["file"] for entry in service["locked"]] == ["stack.lock"]
    runtime = registry.usage(Ref.parse("runtime/adk@1.2.0"), northwind)
    assert runtime["declared"][0]["at"] == "runtime"
    assert runtime["locked"][0]["source"] == "builtin"


def test_declared_counts_only_ref_fields(northwind):
    """Review item 14: free text that happens to be a ref string is not a declaration."""
    text = (northwind / "agent.yaml").read_text()
    (northwind / "agent.yaml").write_text(
        text.replace(
            "description: Customer support",
            "description: tool/orders@1.0.0\nnotes: {x: tool/orders@1.0.0}\n# Customer support",
        )
    )
    row = registry.usage(ORDERS, northwind)
    assert [d["at"] for d in row["declared"]] == ["tools[2].ref"]


def test_usage_declared_but_not_locked_and_the_reverse(northwind):
    text = (northwind / "agent.yaml").read_text()
    (northwind / "agent.yaml").write_text(text.replace("tool/orders@1.0.0", "tool/orders@1.0.1"))
    old = registry.usage(ORDERS, northwind)
    assert old["declared"] == [] and len(old["locked"]) == 1
    new = registry.usage(Ref.parse("tool/orders@1.0.1"), northwind)
    assert len(new["declared"]) == 1 and new["locked"] == []
    assert registry.usage(Ref.parse("tool/unused@1.0.0"), northwind)["declared"] == []


def test_usage_reports_problems_per_file(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "agent.yaml").write_text(f"kind: agent\nname: [{ESC}\n")
    (project / "agent.lock").write_text("{not json")
    (project / "stack.yaml").write_text(
        "kind: stack\nname: Bad_Name\nservices: {db: {ref: tool/orders@1.0.0}}\n"
    )
    row = registry.usage(ORDERS, project)
    assert [e["file"] for e in row["errors"]] == ["agent.yaml", "agent.lock"]
    assert all(ESC not in e["error"] for e in row["errors"])
    assert row["stack"] is None  # an invalid name is not used to find state
    assert row["declared"] == [{"file": "stack.yaml", "name": None, "at": "services.db.ref"}]
    missing = registry.usage(ORDERS, tmp_path / "nope")
    assert "PROJECT_NOT_FOUND" in missing["errors"][0]["error"]


@pytest.mark.parametrize(
    "entry, message",
    [
        ({"sha256": "sha256:" + "z" * 64, "source": "registry"}, "malformed"),
        ({"sha256": f"{ESC}[2J", "source": "registry"}, "malformed"),
        ({"sha256": "sha256:" + "a" * 64, "source": "x" * 600}, "malformed"),
        ("not an object", "malformed"),
    ],
)
def test_lock_values_are_validated(tmp_path, entry, message):
    """Review items 11 and 14: lock values are printed only when well formed."""
    project = tmp_path / "p"
    project.mkdir()
    lock = {"lock_version": 1, "entries": {"tool/orders@1.0.0": entry}}
    (project / "agent.lock").write_text(json.dumps(lock))
    row = registry.usage(ORDERS, project)
    assert row["locked"] == []
    assert row["errors"][0]["file"] == "agent.lock" and message in row["errors"][0]["error"]
    assert ESC not in json.dumps(row)


def test_usage_ignores_a_deploy_manifest_named_agent_yaml(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    (project / "agent.yaml").write_text("schema_version: 2\nname: http-sample\n")
    assert registry.usage(ORDERS, project)["agent"] is None


def test_registry_reads_stay_pure():
    """The console calls registry.py's reads in-process: no state, Docker, secrets or operator
    config may be imported on that path."""
    code = (
        "import json, sys; from pathlib import Path; from agentbreeder import registry;"
        "registry.usage(registry.Ref.parse('tool/orders@1.0.0'), Path(sys.argv[1]));"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('agentbreeder'))))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(NORTHWIND)], capture_output=True, text=True, check=True
    )
    loaded = set(json.loads(done.stdout))
    assert loaded <= {
        "agentbreeder",
        "agentbreeder._version",
        "agentbreeder.registry",
        "agentbreeder.lock",
        "agentbreeder.strict_yaml",
    }, loaded


# -- planted project files (review item 3) -------------------------------------------------------


def _planted(project: Path, tmp_path: Path, how: str) -> str:
    if how == "lock-fifo":
        os.remove(project / "agent.lock")
        os.mkfifo(project / "agent.lock")
        return "agent.lock"
    if how == "lock-dev-zero":
        os.remove(project / "agent.lock")
        os.symlink("/dev/zero", project / "agent.lock")
        return "agent.lock"
    if how == "yaml-fifo-link":
        os.mkfifo(tmp_path / "fifo")
        os.remove(project / "agent.yaml")
        os.symlink(tmp_path / "fifo", project / "agent.yaml")
        return "agent.yaml"
    if how == "yaml-outside":
        shutil.copy(project / "agent.yaml", tmp_path / "outside.yaml")
        os.remove(project / "agent.yaml")
        os.symlink(tmp_path / "outside.yaml", project / "agent.yaml")
        return "agent.yaml"
    (project / "agent.yaml").write_text("x" * (registry.USAGE_FILE_LIMIT + 1))
    return "agent.yaml"


@pytest.mark.parametrize(
    "how", ["lock-fifo", "lock-dev-zero", "yaml-fifo-link", "yaml-outside", "yaml-too-big"]
)
def test_planted_project_files_are_errors_not_hangs(northwind, tmp_path, how):
    file = _planted(northwind, tmp_path, how)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentbreeder",
            "registry",
            "used-by",
            "tool/orders@1.0.0",
            "--project",
            str(northwind),
            "--state-dir",
            str(tmp_path / "state"),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=60,  # a hang or a read of /dev/zero would blow through this
    )
    assert done.returncode == 2, done.stderr
    errors = json.loads(done.stdout)["projects"][0]["errors"]
    assert [e["file"] for e in errors] == [file]
    assert "ENTRY_UNSAFE" in errors[0]["error"]


def test_deeply_nested_files_are_errors_not_tracebacks(northwind, tmp_path):
    """Review item 7: RecursionError from agent.lock, the build record or a manifest column."""
    deep = "[" * 200_000
    (northwind / "agent.lock").write_text(deep)
    row = registry.usage(ORDERS, northwind)
    assert row["errors"][0]["file"] == "agent.lock"
    assert "too deeply nested" in row["errors"][0]["error"]
    (northwind / "deploy").mkdir()
    (northwind / f"deploy/{AGENT}.build.json").write_text(deep)
    state = tmp_path / "state"
    add_deployment(state, "d1", AGENT, IMAGE)
    db = sqlite3.connect(state / "deployments.sqlite3")
    db.execute("UPDATE deployments SET manifest=?", (deep,))
    db.commit()
    db.close()
    result = registry_deployed.deployed(ORDERS, row, northwind, [state])
    assert [e["file"] for e in result["errors"]] == ["agent.lock", f"deploy/{AGENT}.build.json"]
    assert result["deployed_undetermined"][0]["image"] is None


# -- deployed (state, read-only) -----------------------------------------------------------------


def test_deployed_means_ready_and_other_states_are_separate(northwind, tmp_path):
    """Review item 5: only `ready` is deployed; pending, starting, failed and removing are
    recorded_not_running; removed is left out."""
    state = tmp_path / "state"
    write_record(northwind)
    add_deployment(state, "d1", AGENT, IMAGE)
    add_deployment(state, "d2", f"{AGENT}-candidate", IMAGE)
    add_deployment(state, "d3", AGENT, IMAGE, status="failed")
    add_deployment(state, "d4", AGENT, IMAGE, status="pending")
    add_deployment(state, "d5", AGENT, IMAGE, status="starting")
    add_deployment(state, "d6", AGENT, IMAGE, status="removed")
    add_deployment(state, "d7", "someone-else", IMAGE)
    row = used(northwind, state)
    assert [d["deployment"] for d in row["deployed"]] == ["d1", "d2"]
    first = row["deployed"][0]
    assert first["image"] == IMAGE and first["environment"] == "dev" and first["state"] == "ready"
    assert first["build_record"] == f"deploy/{AGENT}.build.json"
    assert first["state_dir"] == str(state.resolve())
    assert [(d["deployment"], d["state"]) for d in row["recorded_not_running"]] == [
        ("d3", "failed"),
        ("d4", "pending"),
        ("d5", "starting"),
    ]
    assert row["deployed_undetermined"] == []


def test_undetermined_reasons(northwind, tmp_path):
    """Review item 6: anything that cannot be matched is undetermined, with the reason."""
    state = tmp_path / "state"
    add_deployment(state, "d1", AGENT, IMAGE)

    def reason():
        return [d["reason"] for d in used(northwind, state)["deployed_undetermined"]]

    assert reason() == ["no build record (deploy/<agent>.build.json) in this project"]
    write_record(northwind, image=OTHER_IMAGE)
    assert reason() == ["its image is not the build record's (rebuilt since, or built elsewhere)"]
    write_record(northwind, refs=["tool/web-search@1.0.0"])
    assert reason() == ["the build record for its image does not list this ref"]
    write_record(northwind)
    (northwind / "agent.yaml").write_text((northwind / "agent.yaml").read_text() + "\n")
    assert reason() == ["agent.yaml or agent.lock changed since the build record was written"]
    write_record(northwind, name="another-agent")
    row = used(northwind, state)
    assert row["deployed_undetermined"][0]["reason"] == "the build record could not be read"
    assert "BUILD_RECORD_INVALID" in row["errors"][0]["error"]


def test_a_service_ref_is_never_matched_against_deployments(northwind, tmp_path):
    """#854: a service ref is declared and locked, never "deployed": the running agent is not
    listed as undetermined for it. The keys stay (the documented JSON shape)."""
    state = tmp_path / "state"
    write_record(northwind)
    add_deployment(state, "d1", AGENT, IMAGE)
    row = used(northwind, state, Ref.parse("service/northwind-ops@1.0.0"))
    assert row["declared"] and row["locked"]
    assert row["deployed"] == row["recorded_not_running"] == row["deployed_undetermined"] == []
    assert row["errors"] == []


def test_an_unrelated_candidate_named_agent_is_not_reported(northwind, tmp_path):
    state = tmp_path / "state"
    write_record(northwind)
    add_deployment(state, "d1", f"{AGENT}-candidate", OTHER_IMAGE)
    row = used(northwind, state)
    assert row["deployed"] == row["deployed_undetermined"] == row["recorded_not_running"] == []


def test_build_record_is_read_safely(northwind, tmp_path):
    write_record(northwind)
    assert registry_deployed.build_record(northwind, AGENT)["image"] == IMAGE
    for bad in ("{", json.dumps({"name": AGENT, "image": 1, "refs": []})):
        (northwind / f"deploy/{AGENT}.build.json").write_text(bad)
        with pytest.raises(registry.RegistryError):
            registry_deployed.build_record(northwind, AGENT)
    os.remove(northwind / f"deploy/{AGENT}.build.json")
    os.symlink(tmp_path / "elsewhere.json", northwind / f"deploy/{AGENT}.build.json")
    with pytest.raises(registry.RegistryError, match="regular file"):
        registry_deployed.build_record(northwind, AGENT)
    assert registry_deployed.build_record(northwind, "../x") is None


def test_stack_state_is_found_like_stack_dir(northwind, tmp_path, stacks):
    state = stacks / stack_id("northwind", northwind) / "envs" / "dev" / "state"
    add_deployment(state, "d1", AGENT, IMAGE)
    local = tmp_path / "local"
    found = registry_deployed.state_dirs(northwind, "northwind", [local, tmp_path / "x/../local"])
    assert found == [local, state]  # each directory once, by resolved path
    assert registry_deployed.state_dirs(northwind, "Bad Name", []) == []


def test_state_is_opened_read_only(northwind, tmp_path):
    state = tmp_path / "state"
    add_deployment(state, "d1", AGENT, IMAGE)
    database = state / "deployments.sqlite3"
    before = (database.read_bytes(), sorted(p.name for p in state.iterdir()))
    registry_deployed.deployments(state)
    assert (database.read_bytes(), sorted(p.name for p in state.iterdir())) == before
    missing = tmp_path / "missing"
    assert registry_deployed.deployments(missing) == [] and not missing.exists()


def _code(state: Path) -> str:
    with pytest.raises(registry.RegistryError) as raised:
        registry_deployed.deployments(state)
    return raised.value.code


def test_unsafe_or_foreign_state_is_refused(tmp_path):
    """Review item 4: ownership and modes as store.py, no symlink, a real table, schema 2+,
    no WAL (and no -wal/-shm created), and bounded queries."""
    real = tmp_path / "real"
    add_deployment(real, "d1", AGENT, IMAGE)
    linked = tmp_path / "linked"
    linked.mkdir(mode=0o700)
    os.symlink(real / "deployments.sqlite3", linked / "deployments.sqlite3")
    assert _code(linked) == "STATE_UNSAFE"
    (real / "deployments.sqlite3").chmod(0o640)
    assert _code(real) == "STATE_UNSAFE"
    (real / "deployments.sqlite3").chmod(0o600)
    real.chmod(0o755)
    assert _code(real) == "STATE_UNSAFE"
    real.chmod(0o700)

    def database(name: str, *statements: str) -> Path:
        directory = tmp_path / name
        directory.mkdir(mode=0o700)
        db = sqlite3.connect(directory / "deployments.sqlite3")
        for statement in statements:
            db.execute(statement)
        db.commit()
        db.close()
        (directory / "deployments.sqlite3").chmod(0o600)
        return directory

    view = database(
        "view",
        "PRAGMA user_version=3",
        "CREATE VIEW deployments AS WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x + 1 "
        "FROM r) SELECT x AS id, '' AS name, '{}' AS manifest, 'ready' AS state, '' AS "
        "environment, '' AS endpoint, 0 AS created FROM r",
    )
    assert _code(view) == "STATE_UNREADABLE"
    old = database("old", "PRAGMA user_version=1", "CREATE TABLE deployments (id TEXT)")
    assert _code(old) == "STATE_UNREADABLE"
    wal = database("wal", "PRAGMA journal_mode=WAL", "PRAGMA user_version=3")
    for leftover in ("deployments.sqlite3-wal", "deployments.sqlite3-shm"):
        (wal / leftover).unlink(missing_ok=True)
    assert _code(wal) == "STATE_UNSUPPORTED"
    assert sorted(p.name for p in wal.iterdir()) == ["deployments.sqlite3"]
    junk = database("junk")
    (junk / "deployments.sqlite3").write_bytes(b"not a database at all" * 10)
    assert _code(junk) == "STATE_UNREADABLE"


def test_state_queries_are_bounded(northwind, tmp_path, monkeypatch):
    """Review round 2, item 6: a truncated read keeps the newest rows and says so; an over-cap
    manifest is undetermined with a reason, not dropped; text columns are capped."""
    state = tmp_path / "state"
    for n in range(5):
        add_deployment(state, f"d{n}", AGENT, IMAGE)
    write_record(northwind)
    monkeypatch.setattr(registry_deployed, "STATE_ROW_LIMIT", 3)
    assert [d["deployment"] for d in registry_deployed.deployments(state)] == ["d2", "d3", "d4"]
    row = used(northwind, state)
    assert [d["deployment"] for d in row["deployed"]] == ["d2", "d3", "d4"]
    assert row["errors"][0]["error"].startswith("[STATE_TRUNCATED] more than 3 deployments")
    monkeypatch.setattr(registry_deployed, "STATE_ROW_LIMIT", 1000)
    monkeypatch.setattr(registry_deployed, "MANIFEST_COLUMN_LIMIT", 10)
    assert [d["image"] for d in registry_deployed.deployments(state)] == [None] * 5
    row = used(northwind, state)
    assert row["deployed"] == [] and len(row["deployed_undetermined"]) == 5
    assert "over the size cap" in row["deployed_undetermined"][0]["reason"]
    monkeypatch.setattr(registry_deployed, "MANIFEST_COLUMN_LIMIT", 256 * 1024)
    db = sqlite3.connect(state / "deployments.sqlite3")
    db.execute("UPDATE deployments SET endpoint = ? WHERE id = 'd0'", ("e" * 5000,))
    db.commit()
    db.close()
    endpoint = registry_deployed.deployments(state)[0]["endpoint"]
    assert len(endpoint) == registry_deployed.COLUMN_LIMIT
    monkeypatch.setattr(registry_deployed, "QUERY_SECONDS", -1.0)  # the handler aborts at once
    monkeypatch.setattr(registry_deployed, "PROGRESS_STEPS", 1)
    assert _code(state) == "STATE_UNREADABLE"


def test_state_errors_are_reported_per_directory(northwind, tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()  # 0755 by default: not private
    row = registry_deployed.deployed(ORDERS, registry.usage(ORDERS, northwind), northwind, [bad])
    assert row["errors"][0]["error"].startswith("[STATE_UNSAFE]")


# -- CLI -------------------------------------------------------------------------------------


def test_cli_used_by_text_and_json(northwind, tmp_path, capsys):
    state = tmp_path / "state"
    write_record(northwind)
    add_deployment(state, "d1", AGENT, IMAGE)
    add_deployment(state, "d2", AGENT, IMAGE, status="failed")
    argv = ["registry", "used-by", "tool/orders@1.0.0", "--project", str(northwind)]
    assert cli.main([*argv, "--state-dir", str(state)]) == 0
    out = capsys.readouterr().out
    assert "  declared      agent.yaml at tools[2].ref" in out
    assert "  locked        agent.lock sha256:" in out
    assert f"  deployed      d1  {AGENT}  ready (local, dev)  {IMAGE}" in out
    assert f"  not running   d2  {AGENT}  failed (local, dev)  {IMAGE}" in out
    assert "Deployed comes from recorded local state" in out
    projects = [str(northwind), str(northwind / "."), str(tmp_path)]
    assert cli.main([*argv[:-1], *projects, "--state-dir", str(state), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ref"] == "tool/orders@1.0.0"
    assert [len(p["deployed"]) for p in result["projects"]] == [1, 0]  # northwind once
    assert set(result["projects"][0]) == {
        "project",
        "agent",
        "stack",
        "declared",
        "locked",
        "deployed",
        "recorded_not_running",
        "deployed_undetermined",
        "errors",
    }
    assert cli.main(["registry", "used-by", "tool/orders", "--project", str(northwind)]) == 2
    assert "REF_UNPINNED" in capsys.readouterr().err
    assert cli.main([*argv, str(tmp_path / "nope"), "--state-dir", str(state)]) == 2
    assert "PROJECT_NOT_FOUND" in capsys.readouterr().out


def test_e2e_northwind_orders_declared_locked_and_deployed(northwind, tmp_path, stacks):
    """Acceptance: used-by for tool/orders@1.0.0 shows Northwind as declared, locked and
    deployed (a recorded `ready` deployment of the image its build record lists; the real
    Docker run is in the M8 verification)."""
    state = stacks / stack_id("northwind", northwind) / "envs" / "dev" / "state"
    write_record(northwind, refs=["tool/orders@1.0.0", "prompt/northwind-support@1.0.1"])
    add_deployment(state, "nw-1", AGENT, IMAGE)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentbreeder",
            "registry",
            "used-by",
            "tool/orders@1.0.0",
            "--project",
            str(northwind),
            "--json",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "AGENTBREEDER_STATE_DIR": str(tmp_path / "local")},
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    project = json.loads(done.stdout)["projects"][0]
    assert project["declared"][0]["file"] == "agent.yaml"
    assert project["locked"][0]["file"] == "agent.lock"
    assert project["deployed"][0]["deployment"] == "nw-1"
    assert not (tmp_path / "local").exists()  # nothing created by a read


def test_a_record_written_by_the_real_build_is_understood(request, tmp_path):
    """Review round 2, item 7: the build record comes from build() itself (fake image builder),
    so a change in its shape breaks this test rather than silently turning deployed into
    undetermined."""
    from agentbreeder.build import build

    project = request.getfixturevalue("build_project_fixture")
    operator = request.getfixturevalue("build_operator_fixture")
    fake = build_tests.FakeBuilder()
    result = build(project / "agent.yaml", operator.load(), "dev", builder=fake)
    state = tmp_path / "state"
    add_deployment(state, "h1", "helper", result.image)
    row = used(project, state)
    assert [d["deployment"] for d in row["deployed"]] == ["h1"], row
    assert row["deployed"][0]["build_record"] == "deploy/helper.build.json"
    assert row["deployed_undetermined"] == [] and row["errors"] == []


def test_usage_file_limit_matches_what_build_accepts(tmp_path):
    """Review round 2, item 5: used-by never parses an agent.yaml or stack.yaml that build or
    services up would refuse for its size."""
    from agentbreeder import agentfile
    from agentbreeder.services import schema

    assert registry.USAGE_FILE_LIMIT == agentfile.MAX_BYTES == schema.MAX_BYTES
    project = tmp_path / "p"
    project.mkdir()
    (project / "agent.yaml").write_text("kind: agent\n# " + "x" * agentfile.MAX_BYTES + "\n")
    row = registry.usage(ORDERS, project)
    assert row["errors"][0]["file"] == "agent.yaml" and "64 KiB" in row["errors"][0]["error"]
