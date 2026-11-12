"""Read-only state opens and `agentbreeder migrate` (#819, guided §14.2, §14.9 R6)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agentbreeder import cli
from agentbreeder.services import engine
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import StackState
from agentbreeder.store import SCHEMA_VERSION, DeploymentError, StateNeedsMigration, Store

SRC = str(Path(__file__).resolve().parents[1] / "src")


def make_state(directory: Path) -> Path:
    Store(directory).close()
    return directory


def downgrade_to_v2(directory: Path) -> None:
    db = sqlite3.connect(directory / "deployments.sqlite3")
    db.execute("DROP INDEX held_calls_binding")
    db.execute("DROP TABLE held_calls")
    db.execute("PRAGMA user_version=2")
    db.commit()
    db.close()


def run_cli(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agentbreeder", *argv],
        env={**os.environ, "PYTHONPATH": SRC},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_open_readonly_creates_nothing(tmp_path):
    missing = tmp_path / "state"
    with pytest.raises(DeploymentError, match="No AgentBreeder state"):
        Store.open_readonly(missing)
    assert not missing.exists()
    missing.mkdir(mode=0o700)
    with pytest.raises(DeploymentError, match="No AgentBreeder state"):
        Store.open_readonly(missing)
    assert list(missing.iterdir()) == []


def test_open_readonly_refuses_unsafe_state(tmp_path):
    state = make_state(tmp_path / "state")
    state.chmod(0o755)
    with pytest.raises(DeploymentError, match="0700"):
        Store.open_readonly(state)
    state.chmod(0o700)
    (state / "deployments.sqlite3").chmod(0o644)
    with pytest.raises(DeploymentError, match="private file"):
        Store.open_readonly(state)


def test_open_readonly_reads_and_cannot_write(tmp_path):
    state = make_state(tmp_path / "state")
    store = Store.open_readonly(state)
    try:
        assert store.active_all() == [] and store.readonly
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            store.db.execute("PRAGMA user_version=9")
    finally:
        store.close()


def test_old_schema_names_migrate_and_migrate_upgrades(tmp_path):
    state = make_state(tmp_path / "state")
    downgrade_to_v2(state)
    with pytest.raises(StateNeedsMigration, match="run `agentbreeder migrate`"):
        Store.open_readonly(state)
    listed = run_cli("list", "--state-dir", str(state))
    assert listed.returncode == 2
    assert "[STATE_NEEDS_MIGRATION]" in listed.stderr and "migrate" in listed.stderr
    # list did not migrate it.
    db = sqlite3.connect(state / "deployments.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    db.close()
    migrated = run_cli("migrate", "--state-dir", str(state), "--json")
    assert migrated.returncode == 0, migrated.stderr
    result = json.loads(migrated.stdout)
    assert result["previous_version"] == 2 and result["schema_version"] == SCHEMA_VERSION
    assert result["migrated"] is True
    assert run_cli("list", "--state-dir", str(state)).stdout.strip() == ""
    again = run_cli("migrate", "--state-dir", str(state))
    assert again.returncode == 0 and "already at schema" in again.stdout


def test_migrate_never_creates_state(tmp_path, capsys):
    missing = tmp_path / "nothing"
    assert cli.main(["migrate", "--state-dir", str(missing)]) == 2
    assert "No AgentBreeder state" in capsys.readouterr().err
    assert not missing.exists()


def test_a_held_write_lock_does_not_block_list(tmp_path, capsys):
    state = make_state(tmp_path / "state")
    writer = sqlite3.connect(state / "deployments.sqlite3", isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")  # a running job between its writes
    try:
        assert cli.main(["list", "--state-dir", str(state), "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == []
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_an_open_reader_takes_no_write_lock(tmp_path):
    state = make_state(tmp_path / "state")
    reader = Store.open_readonly(state)
    try:
        reader.active_all()
        writer = Store(state)  # BEGIN IMMEDIATE inside: would time out behind a write lock
        try:
            with writer.transaction():
                writer.db.execute("PRAGMA user_version")
        finally:
            writer.close()
    finally:
        reader.close()


HOT_JOURNAL = textwrap.dedent(
    """
    import os, sqlite3, sys
    db = sqlite3.connect(sys.argv[1], isolation_level=None)
    db.execute("PRAGMA cache_size=1")
    db.execute("BEGIN IMMEDIATE")
    for i in range(3000):
        db.execute(
            "INSERT INTO events(deployment_id,state,timestamp) VALUES (?,?,?)",
            ("x" * 200, "pending", str(i)),
        )
    os._exit(0)  # crash mid-transaction: the rollback journal stays behind, hot
    """
)


def test_a_hot_journal_is_reported_and_migrate_recovers_it(tmp_path):
    state = make_state(tmp_path / "state")
    database = state / "deployments.sqlite3"
    subprocess.run([sys.executable, "-c", HOT_JOURNAL, str(database)], check=True, timeout=60)
    journal = state / "deployments.sqlite3-journal"
    assert journal.exists() and journal.stat().st_size > 0
    with pytest.raises(StateNeedsMigration, match="interrupted write"):
        Store.open_readonly(state)
    assert journal.exists()  # a reader never rolls it back
    assert run_cli("migrate", "--state-dir", str(state)).returncode == 0
    store = Store.open_readonly(state)
    try:
        assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    finally:
        store.close()


# -- stacks ------------------------------------------------------------------------------------


def test_stack_context_creates_no_phantom_stack(tmp_path, monkeypatch):
    root = tmp_path / "stacks"
    monkeypatch.setenv("AGENTBREEDER_STACKS_DIR", str(root))
    project = tmp_path / "p"
    project.mkdir()
    (project / "stack.yaml").write_text("kind: stack\n")
    monkeypatch.setattr(engine, "load", lambda path: type("S", (), {"name": "demo"})())
    with pytest.raises(ServiceError, match="services up"):
        engine.context(project, None)
    assert not root.exists()
    root.mkdir(mode=0o700)
    with pytest.raises(ServiceError, match="STACK_NOT_UP|services up"):
        engine.context(project, "dev")
    assert list(root.iterdir()) == []


def test_stack_state_open_readonly(tmp_path):
    root = tmp_path / "stacks"
    project = tmp_path / "p"
    project.mkdir()
    assert StackState.open_readonly("demo", project, root) is None
    assert not root.exists()
    writer = StackState("demo", project, root)
    writer.put("project", str(project))
    writer.close()
    reader = StackState.open_readonly("demo", project, root)
    try:
        assert reader.get("project") == str(project)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.put("x", "y")
    finally:
        reader.close()
