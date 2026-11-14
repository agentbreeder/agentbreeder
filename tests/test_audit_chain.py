"""Audit chain integrity: what it detects and, explicitly, what it cannot detect."""

from __future__ import annotations

import json

from conftest import v2_manifest_data

from agentbreeder import audit
from agentbreeder.audit import AuditEvent
from agentbreeder.deployment import DeploymentService
from agentbreeder.manifest import Manifest
from agentbreeder.store import Store


def _event(attempt="a1", action="attempt_started", decision="started"):
    return AuditEvent(attempt, action, decision, "os:me", False, "enforce")


def test_chain_links_and_verifies(state):
    for i in range(3):
        state.audit(_event(f"a{i}"))
        state.audit(_event(f"a{i}", "deploy", "deny"))
    report = state.verify_audit()
    assert report["ok"] and report["rows"] == 6 and report["incomplete_attempts"] == []
    rows = list(reversed(state.audit_rows(limit=10)))
    assert rows[0]["prev_hash"] == audit.GENESIS_HASH
    assert all(b["prev_hash"] == a["hash"] for a, b in zip(rows, rows[1:], strict=False))


def test_modified_row_without_recomputed_hashes_is_detected(state):
    state.audit(_event())
    state.audit(_event("a1", "deploy", "deny"))
    state.db.execute("UPDATE audit SET decision='allow' WHERE seq=2")
    report = state.verify_audit()
    assert not report["ok"]
    assert report["first_invalid_seq"] == 2 and report["problem"] == "hash mismatch"


def test_deleted_middle_row_is_detected(state):
    for i in range(3):
        state.audit(_event(f"a{i}"))
    state.db.execute("DELETE FROM audit WHERE seq=2")
    report = state.verify_audit()
    assert not report["ok"] and report["first_invalid_seq"] == 3


def test_documented_limits_recomputed_chain_and_tail_deletion_not_detected(state):
    """A database owner can rewrite the chain or drop a valid tail. No tamper-evidence claim."""
    for i in range(3):
        state.audit(_event(f"a{i}"))
        state.audit(_event(f"a{i}", "deploy", "deny"))
    state.db.execute("DELETE FROM audit WHERE seq > 4")
    assert state.verify_audit()["ok"]  # valid-tail deletion: undetectable locally
    state.db.execute("UPDATE audit SET decision='allow' WHERE seq=2")
    prev = audit.GENESIS_HASH
    for raw in state.db.execute(f"SELECT {','.join(audit.COLUMNS)} FROM audit ORDER BY seq"):
        row = dict(zip(audit.COLUMNS, raw, strict=True))
        row["prev_hash"] = prev
        row["hash"] = audit.row_hash(prev, row)
        state.db.execute(
            "UPDATE audit SET prev_hash=?, hash=? WHERE seq=?", (prev, row["hash"], row["seq"])
        )
        prev = row["hash"]
    report = state.verify_audit()
    assert report["ok"]  # full-chain recomputation: undetectable locally
    assert "recompute the chain" in report["limitations"]
    assert "delete a valid tail" in report["limitations"]


def test_incomplete_attempt_reported_never_as_allow(state):
    state.audit(_event("crashed"))
    state.audit(_event("done"))
    state.audit(_event("done", "deploy", "allow"))
    state.audit(_event("allowed-not-run"))
    state.audit(_event("allowed-not-run", "deploy", "allow"))
    state.audit(_event("done", "transition", "starting"))
    state.audit(_event("done", "transition", "ready"))
    state.audit(_event("aborted"))
    state.audit(_event("aborted", "deploy", "allow"))
    state.audit(_event("aborted", "deploy", "aborted"))
    state.audit(_event("override"))
    state.audit(_event("override", "deploy", "deny"))
    state.audit(_event("override", "audit_mode_proceed", "override"))
    report = state.verify_audit()
    assert report["ok"]
    assert report["incomplete_attempts"] == ["allowed-not-run", "crashed", "override"]


def test_rows_are_sanitized_json(state, identity_env, runtime):
    service = DeploymentService(state, runtime, identity_env.load())
    service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    row = next(r for r in state.audit_rows(limit=50) if r["action"] == "deploy")
    snapshot = row["principal_snapshot"]
    assert set(snapshot) >= {"provider", "authority", "id", "kind", "roles", "groups"}
    assert "alice@example.com" not in json.dumps(snapshot)  # IDs only, no names
    assert row["operator_config_digest"].startswith("sha256:")
    assert row["image_digest"] == "sha256:" + "a" * 64


def test_v1_state_migrates_to_v2(tmp_path):
    import sqlite3

    directory = tmp_path / "old"
    directory.mkdir(mode=0o700)
    path = directory / "deployments.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE deployments (id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT NOT NULL,
          manifest TEXT NOT NULL, context TEXT NOT NULL, container TEXT NOT NULL, resource TEXT,
          state TEXT NOT NULL, endpoint TEXT, error TEXT, created TEXT NOT NULL,
          updated TEXT NOT NULL);
        CREATE TABLE events (sequence INTEGER PRIMARY KEY, deployment_id TEXT NOT NULL,
          state TEXT NOT NULL, error TEXT, timestamp TEXT NOT NULL);
        INSERT INTO deployments VALUES ('old','x','d','{}','c','agentbreeder-old',NULL,'ready',
          NULL,NULL,'t','t');
        PRAGMA user_version=1;
        """
    )
    db.close()
    path.chmod(0o600)
    store = Store(directory)
    try:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert store.get("old")["package"] is None
        assert store.get("old")["principal"] is None
        assert store.verify_audit()["rows"] == 0
    finally:
        store.close()
