"""`agentbreeder list --all` (#823, guided §14.5): every stack's deployments, read-only."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agentbreeder import allstacks, cli
from agentbreeder.services.state import PROJECT_KEY, StackState
from agentbreeder.store import Store

SRC = str(Path(__file__).resolve().parents[1] / "src")


def deploy(state: Path, name: str, environment: str | None = None, **manifest) -> None:
    store = Store(state)
    try:
        identifier = f"{name}-id"
        store.create(
            identifier,
            {"name": name, "image": f"{name}:1", **manifest},
            "digest",
            "context",
            environment=environment,
        )
        store.transition(identifier, "running", endpoint="http://127.0.0.1:9000")
    finally:
        store.close()


def make_stack(
    root: Path, name: str, project: Path, environment: str = "dev", record: bool = True
) -> Path:
    project.mkdir(parents=True, exist_ok=True)
    state = StackState(name, project, root)
    if record:
        state.put(PROJECT_KEY, str(project.resolve()))
    directory = state.directory
    state.close()
    env_dir = directory / "envs" / environment
    env_dir.mkdir(parents=True, mode=0o700)
    (env_dir / "operator.yaml").write_text("x: 1\n")
    return directory


@pytest.fixture
def roots(tmp_path, monkeypatch):
    root = tmp_path / "stacks"
    monkeypatch.setenv("AGENTBREEDER_STACKS_DIR", str(root))
    return tmp_path / "local", root


def snapshot(path: Path) -> dict:
    return {
        str(p.relative_to(path)): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def test_lists_the_default_state_and_every_stack_without_writing(roots, tmp_path):
    default, root = roots
    deploy(default, "hand", environment=None)
    one = make_stack(root, "northwind", tmp_path / "nw")
    deploy(one / "envs/dev/state", "northwind-support", environment="dev", target="kind")
    two = make_stack(root, "starter", tmp_path / "st", record=False)
    deploy(two / "envs/dev/state", "starter", environment="dev")
    before = snapshot(tmp_path)

    result = allstacks.collect(default)

    assert snapshot(tmp_path) == before
    assert result["schema_version"] == 1
    assert result["skipped"] == []
    by_name = {row["name"]: row for row in result["deployments"]}
    assert set(by_name) == {"hand", "northwind-support", "starter"}
    assert by_name["hand"]["stack"] is None and by_name["hand"]["project"] is None
    nw = by_name["northwind-support"]
    assert nw["stack"] == one.name
    assert nw["project"] == str((tmp_path / "nw").resolve())
    assert nw["environment"] == "dev" and nw["target"] == "kind"
    assert nw["state"] == "running" and nw["endpoint"] == "http://127.0.0.1:9000"
    assert by_name["starter"]["project"] is None  # brought up before the path was recorded


def test_nothing_anywhere_creates_nothing(roots, tmp_path):
    default, root = roots
    assert allstacks.collect(default) == {"schema_version": 1, "deployments": [], "skipped": []}
    assert not default.exists() and not root.exists()


def test_unsafe_and_invalid_entries_are_reported_and_skipped(roots, tmp_path):
    default, root = roots
    good = make_stack(root, "good", tmp_path / "good")
    deploy(good / "envs/dev/state", "good")
    elsewhere = make_stack(tmp_path / "other", "linked", tmp_path / "linked")
    deploy(elsewhere / "envs/dev/state", "linked")
    os.symlink(elsewhere, root / elsewhere.name)  # a symlinked stack directory
    (root / "Bad\x1b]52;c;x\x07-00000000").mkdir()
    (root / ".staging").mkdir()  # hidden: ignored silently
    env_linked = make_stack(root, "envlink", tmp_path / "envlink", environment="prod")
    target = env_linked / "envs" / "prod"
    moved = tmp_path / "moved-prod"
    target.rename(moved)
    os.symlink(moved, target)

    result = allstacks.collect(default)

    assert [row["name"] for row in result["deployments"]] == ["good"]
    codes = {(entry["stack"], entry["code"]) for entry in result["skipped"]}
    assert (elsewhere.name, "STACK_UNSAFE") in codes
    assert (env_linked.name, "STACK_UNSAFE") in codes
    invalid = [entry for entry in result["skipped"] if entry["code"] == "STACK_NAME_INVALID"]
    assert len(invalid) == 1 and "\x1b" not in invalid[0]["stack"]
    assert all(".staging" != (entry["stack"] or "") for entry in result["skipped"])


def test_an_old_or_unsupported_schema_is_reported_and_the_rest_listed(roots, tmp_path):
    default, root = roots
    old = make_stack(root, "old", tmp_path / "old")
    deploy(old / "envs/dev/state", "old")
    db = sqlite3.connect(old / "envs/dev/state/deployments.sqlite3")
    db.execute("PRAGMA user_version=2")
    db.commit()
    db.close()
    future = make_stack(root, "future", tmp_path / "future")
    deploy(future / "envs/dev/state", "future")
    db = sqlite3.connect(future / "envs/dev/state/deployments.sqlite3")
    db.execute("PRAGMA user_version=99")
    db.commit()
    db.close()
    fine = make_stack(root, "fine", tmp_path / "fine")
    deploy(fine / "envs/dev/state", "fine")

    result = allstacks.collect(default)

    assert [row["name"] for row in result["deployments"]] == ["fine"]
    codes = {entry["stack"]: entry["code"] for entry in result["skipped"]}
    assert codes == {old.name: "STATE_NEEDS_MIGRATION", future.name: "STATE_UNSUPPORTED"}
    assert all(entry["environment"] == "dev" for entry in result["skipped"])
    for entry in result["skipped"]:
        assert set(entry) == {"stack", "environment", "code", "reason"}


def test_a_stacks_root_that_is_not_a_directory_is_reported(roots, tmp_path):
    # AGENTBREEDER_STACKS_DIR itself is resolved (`stacks_root`), as for every stack command.
    default, root = roots
    root.write_text("not a directory")
    result = allstacks.collect(default)
    assert [entry["code"] for entry in result["skipped"]] == ["STACKS_ROOT_UNSAFE"]


def test_an_unreadable_stack_db_is_reported(roots, tmp_path):
    default, root = roots
    stack = make_stack(root, "broken", tmp_path / "broken")
    (stack / "stack.db").chmod(0o644)  # group/world bits: refused as unsafe
    result = allstacks.collect(default)
    assert result["skipped"] == [
        {
            "stack": stack.name,
            "environment": None,
            "code": "STATE_UNSAFE",
            "reason": "stack.db must be a 0600 file you own",
        }
    ]


def test_cli_all_json_and_text(roots, tmp_path, capsys):
    default, root = roots
    deploy(default, "hand")
    stack = make_stack(root, "demo", tmp_path / "demo")
    deploy(stack / "envs/dev/state", "demo", environment="dev")
    old = make_stack(root, "old", tmp_path / "old")
    deploy(old / "envs/dev/state", "old")
    db = sqlite3.connect(old / "envs/dev/state/deployments.sqlite3")
    db.execute("PRAGMA user_version=2")
    db.commit()
    db.close()

    assert cli.main(["list", "--all", "--json", "--state-dir", str(default)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert [row["name"] for row in printed["deployments"]] == ["hand", "demo"]
    assert printed["skipped"][0]["code"] == "STATE_NEEDS_MIGRATION"

    assert cli.main(["list", "--all", "--state-dir", str(default)]) == 0
    out, err = capsys.readouterr()
    assert "hand-id  hand  local  running  default state  -" in out
    assert f"demo-id  demo  local  running  {stack.name}  dev" in out
    assert f"skipped {old.name} (dev): [STATE_NEEDS_MIGRATION]" in err


def test_cli_all_with_nothing(roots, capsys):
    default, _ = roots
    assert cli.main(["list", "--all", "--state-dir", str(default)]) == 0
    assert capsys.readouterr().out == "No deployments\n"


def test_cli_list_without_all_is_unchanged(roots, capsys):
    default, _ = roots
    deploy(default, "hand")
    assert cli.main(["list", "--json", "--state-dir", str(default)]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [row["name"] for row in rows] == ["hand"] and "stack" not in rows[0]


def test_all_and_stack_together_are_refused(roots, tmp_path, capsys):
    with pytest.raises(SystemExit) as raised:
        cli.main(["list", "--all", "--stack", str(tmp_path)])
    assert raised.value.code == 2
    assert "use --all or --stack, not both" in capsys.readouterr().err


def test_cli_subprocess_end_to_end(roots, tmp_path):
    default, root = roots
    stack = make_stack(root, "demo", tmp_path / "demo")
    deploy(stack / "envs/dev/state", "demo", environment="dev")
    done = subprocess.run(
        [sys.executable, "-m", "agentbreeder", "list", "--all", "--json"],
        env={
            **os.environ,
            "PYTHONPATH": SRC,
            "AGENTBREEDER_STACKS_DIR": str(root),
            "AGENTBREEDER_STATE_DIR": str(default),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    printed = json.loads(done.stdout)
    assert printed["deployments"][0]["project"] == str((tmp_path / "demo").resolve())
    assert not default.exists()


def test_skipped_entries_always_have_the_documented_keys(roots, tmp_path):
    default, root = roots
    root.mkdir(mode=0o700)
    (root / "BAD").mkdir()
    open_stack = make_stack(root, "open", tmp_path / "open")
    open_stack.chmod(0o777)  # others could rename its entries
    try:
        result = allstacks.collect(default)
    finally:
        open_stack.chmod(0o700)
    assert {entry["code"] for entry in result["skipped"]} == {
        "STACK_NAME_INVALID",
        "STACK_UNSAFE",
    }
    for entry in result["skipped"]:
        assert set(entry) == {"stack", "environment", "code", "reason"}


def test_an_environment_without_deployments_lists_nothing(roots, tmp_path):
    default, root = roots
    make_stack(root, "quiet", tmp_path / "quiet")
    assert allstacks.collect(default) == {"schema_version": 1, "deployments": [], "skipped": []}


@pytest.mark.parametrize("manifest", ["{not json", "[1, 2]", "null", '"text"'])
def test_a_damaged_manifest_is_reported_not_a_traceback(roots, tmp_path, manifest):
    default, root = roots
    stack = make_stack(root, "damaged", tmp_path / "damaged")
    deploy(stack / "envs/dev/state", "damaged")
    db = sqlite3.connect(stack / "envs/dev/state/deployments.sqlite3")
    db.execute("UPDATE deployments SET manifest=?", (manifest,))
    db.commit()
    db.close()
    result = allstacks.collect(default)
    assert result["deployments"] == []
    assert [entry["code"] for entry in result["skipped"]] == ["STATE_UNREADABLE"]


def test_an_unlistable_envs_directory_is_reported(roots, tmp_path, monkeypatch):
    default, root = roots
    make_stack(root, "locked", tmp_path / "locked")

    def denied(directory):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(allstacks, "written_environments", denied)
    result = allstacks.collect(default)
    assert [entry["code"] for entry in result["skipped"]] == ["STACK_UNREADABLE"]


@pytest.mark.parametrize(
    "recorded", ["relative/path", "/a/../b", "/x\x1b]52;c;y\x07", "/a\u202eb", ""]
)
def test_a_recorded_project_that_is_not_a_clean_absolute_path_is_null(roots, tmp_path, recorded):
    default, root = roots
    stack = make_stack(root, "odd", tmp_path / "odd", record=False)
    writer = StackState("odd", tmp_path / "odd", root)
    writer.put(PROJECT_KEY, recorded)
    writer.close()
    deploy(stack / "envs/dev/state", "odd")
    (row,) = allstacks.collect(default)["deployments"]
    assert row["project"] is None


def test_text_rows_escape_controls(roots, tmp_path, capsys):
    default, _ = roots
    deploy(default, "hand", target="lo\x1b[2Jcal")
    assert cli.main(["list", "--all", "--state-dir", str(default)]) == 0
    assert "\x1b" not in capsys.readouterr().out
