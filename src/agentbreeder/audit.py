"""Append-only, hash-chained audit records in the local SQLite store.

The chain detects corruption and edits that do not recompute later hashes. It does NOT detect a
database owner recomputing the whole chain, replacing the database or deleting a valid tail. No
adversarial tamper-evidence is claimed without an independent checkpoint/witness.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

GENESIS_HASH = "0" * 64
JSON_FIELDS = (
    "checks_evaluated",
    "checks_unavailable",
    "reasons",
    "principal_snapshot",
    "context",
    "policy_ids",
    "bundle_digests",
)
COLUMNS = (
    "seq",
    "at",
    "attempt_id",
    "deployment_id",
    "actor",
    "actor_verified",
    "action",
    "decision",
    "enforcement_mode",
    "checks_evaluated",
    "checks_unavailable",
    "reasons",
    "principal_snapshot",
    "evidence_digest",
    "context",
    "revision_digest",
    "policy_ids",
    "bundle_digests",
    "operator_config_digest",
    "image_digest",
    "prev_hash",
    "hash",
)
TERMINAL = {"allow", "deny", "pending_approval", "aborted"}
EXECUTION_TERMINAL = {"ready", "failed", "removed", "disabled"}
SCHEMA = """
CREATE TABLE audit (
    seq INTEGER PRIMARY KEY, at TEXT NOT NULL, attempt_id TEXT NOT NULL, deployment_id TEXT,
    actor TEXT NOT NULL, actor_verified INTEGER NOT NULL, action TEXT NOT NULL,
    decision TEXT NOT NULL, enforcement_mode TEXT NOT NULL, checks_evaluated TEXT NOT NULL,
    checks_unavailable TEXT NOT NULL, reasons TEXT NOT NULL, principal_snapshot TEXT,
    evidence_digest TEXT, context TEXT NOT NULL, revision_digest TEXT, policy_ids TEXT NOT NULL,
    bundle_digests TEXT NOT NULL, operator_config_digest TEXT, image_digest TEXT,
    prev_hash TEXT NOT NULL, hash TEXT NOT NULL
)
"""


class AuditError(RuntimeError):
    """Audit persistence failed; no runtime action may proceed."""


@dataclass
class AuditEvent:
    attempt_id: str
    action: str
    decision: str
    actor: str
    actor_verified: bool
    enforcement_mode: str
    deployment_id: str | None = None
    checks_evaluated: list[str] = field(default_factory=list)
    checks_unavailable: list[str] = field(default_factory=list)
    reasons: list[dict] = field(default_factory=list)
    principal_snapshot: dict | None = None
    evidence_digest: str | None = None
    context: dict = field(default_factory=dict)
    revision_digest: str | None = None
    policy_ids: list[str] = field(default_factory=list)
    bundle_digests: dict = field(default_factory=dict)
    operator_config_digest: str | None = None
    image_digest: str | None = None


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def row_hash(prev_hash: str, row: dict) -> str:
    body = {k: row[k] for k in COLUMNS if k not in ("hash", "prev_hash")}
    return hashlib.sha256((prev_hash + canonical(body)).encode()).hexdigest()


def append(db: sqlite3.Connection, event: AuditEvent) -> dict:
    """Append inside the caller's open transaction (BEGIN IMMEDIATE serializes writers)."""
    last = db.execute("SELECT seq, hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS_HASH)
    row: dict[str, Any] = {
        "seq": seq,
        "at": datetime.now(UTC).isoformat(),
        "attempt_id": event.attempt_id,
        "deployment_id": event.deployment_id,
        "actor": event.actor,
        "actor_verified": int(event.actor_verified),
        "action": event.action,
        "decision": event.decision,
        "enforcement_mode": event.enforcement_mode,
        "evidence_digest": event.evidence_digest,
        "revision_digest": event.revision_digest,
        "operator_config_digest": event.operator_config_digest,
        "image_digest": event.image_digest,
    }
    for name in JSON_FIELDS:
        value = getattr(event, name)
        row[name] = None if value is None else canonical(value)
    row["prev_hash"] = prev
    row["hash"] = row_hash(prev, row)
    db.execute(
        f"INSERT INTO audit ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
        tuple(row[c] for c in COLUMNS),
    )
    return row


def decode(row: sqlite3.Row | dict) -> dict:
    result = dict(row)
    for name in JSON_FIELDS:
        if result.get(name) is not None:
            result[name] = json.loads(result[name])
    result["actor_verified"] = bool(result["actor_verified"])
    return result


def verify(db: sqlite3.Connection) -> dict:
    """Recompute links and hashes. Reports the first broken row and incomplete attempts."""
    prev, expected_seq, count = GENESIS_HASH, 1, 0
    attempts: dict[str, bool] = {}
    executing: set[str] = set()
    for raw in db.execute(f"SELECT {','.join(COLUMNS)} FROM audit ORDER BY seq"):
        row = dict(zip(COLUMNS, raw, strict=True))
        if row["seq"] != expected_seq or row["prev_hash"] != prev:
            return _report(False, count, prev, attempts, row["seq"], "broken link or sequence")
        if row_hash(prev, row) != row["hash"]:
            return _report(False, count, prev, attempts, row["seq"], "hash mismatch")
        attempt = row["attempt_id"]
        if row["action"] == "attempt_started":
            attempts.setdefault(attempt, False)
        elif row["action"] == "transition" and attempt in attempts:
            # Once execution intent exists, only a terminal transition completes the attempt.
            attempts[attempt] = row["decision"] in EXECUTION_TERMINAL
        elif row["decision"] in TERMINAL and attempt in attempts and attempt not in executing:
            attempts[attempt] = True
        if (row["action"] == "transition" and row["decision"] in ("starting", "removing")) or (
            (row["action"] in ("deploy", "remove") and row["decision"] == "allow")
            or row["action"] == "audit_mode_proceed"
        ):
            # An allowed execution stays incomplete until a terminal transition or abort.
            executing.add(attempt)
            attempts[attempt] = False
        elif row["action"] == "transition" and row["decision"] in EXECUTION_TERMINAL:
            executing.discard(attempt)
        elif row["decision"] in ("aborted", "deny") and attempt in attempts:
            executing.discard(attempt)
            attempts[attempt] = True
        prev, expected_seq, count = row["hash"], expected_seq + 1, count + 1
    return _report(True, count, prev, attempts, None, None)


def _report(ok, count, head, attempts, bad_seq, problem) -> dict:
    return {
        "ok": ok,
        "rows": count,
        "head": head,
        "first_invalid_seq": bad_seq,
        "problem": problem,
        "incomplete_attempts": sorted(a for a, done in attempts.items() if not done),
        "limitations": (
            "Detects corruption and edits that do not recompute the chain. A database owner can "
            "recompute the chain, replace the database or delete a valid tail without detection."
        ),
    }
