"""Single-user durable state. Never store credentials or raw runtime errors."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from agentbreeder import audit as audit_log
from agentbreeder.audit import AuditError, AuditEvent

SCHEMA_VERSION = 3
_V2_DEPLOYMENT_COLUMNS = (
    ("principal_key", "TEXT"),
    ("principal", "TEXT"),
    ("environment", "TEXT"),
    ("revision", "TEXT"),
    ("approval_id", "TEXT"),
    ("compliance", "TEXT"),
)
_V2_TABLES = (
    audit_log.SCHEMA,
    """
    CREATE TABLE approvals (
        id TEXT PRIMARY KEY, revision TEXT NOT NULL, evidence TEXT NOT NULL,
        principal_key TEXT NOT NULL, environment TEXT NOT NULL, agent TEXT NOT NULL,
        deployer TEXT NOT NULL, deployer_verified INTEGER NOT NULL, sponsors TEXT NOT NULL,
        obligations TEXT NOT NULL, state TEXT NOT NULL, checker TEXT, checker_claims TEXT,
        execution_id TEXT, created TEXT NOT NULL, expires TEXT NOT NULL, decided TEXT
    )
    """,
    """
    CREATE TABLE blockers (
        principal_key TEXT NOT NULL, environment TEXT NOT NULL, actor TEXT NOT NULL,
        actor_verified INTEGER NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL,
        PRIMARY KEY (principal_key, environment)
    )
    """,
)

_V3_TABLES = (
    """
    CREATE TABLE held_calls (
        id TEXT PRIMARY KEY, deployment_id TEXT NOT NULL, binding TEXT NOT NULL,
        server TEXT NOT NULL, tool TEXT NOT NULL, arguments_digest TEXT NOT NULL,
        revision TEXT NOT NULL, principal_key TEXT NOT NULL, environment TEXT NOT NULL,
        deployer_names TEXT NOT NULL, state TEXT NOT NULL, checker TEXT, checker_claims TEXT,
        created TEXT NOT NULL, expires TEXT NOT NULL, decided TEXT
    )
    """,
    "CREATE INDEX held_calls_binding ON held_calls (deployment_id, binding)",
)


class DeploymentError(ValueError):
    """Safe, actionable lifecycle error."""

    code = "DEPLOYMENT_ERROR"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, directory: Path):
        # Secret files are bind-mounted from here with `--mount type=bind,src=…`: Docker needs
        # an absolute path, and the option is comma-delimited.
        directory = directory.absolute()
        if "," in str(directory):
            raise DeploymentError("State directory path must not contain a comma")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or directory.stat().st_uid != os.getuid():
            raise DeploymentError("State directory must be owned by the current user")
        if directory.stat().st_mode & 0o077:
            raise DeploymentError("State directory must have permissions 0700")
        self.directory = directory
        path = directory / "deployments.sqlite3"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
            ):
                raise DeploymentError("State database must be a private file owned by this user")
        finally:
            os.close(descriptor)
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self._depth = 0
        try:
            with self.transaction():
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                tables = self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if version not in (0, 1, 2, SCHEMA_VERSION) or (version == 0 and tables):
                    raise DeploymentError(
                        "Unsupported state schema; use the matching AgentBreeder version"
                    )
                if version == 0:
                    self.db.execute("""
                        CREATE TABLE deployments (
                            id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT NOT NULL,
                            manifest TEXT NOT NULL, context TEXT NOT NULL, container TEXT NOT NULL,
                            resource TEXT, state TEXT NOT NULL, endpoint TEXT, error TEXT,
                            created TEXT NOT NULL, updated TEXT NOT NULL
                        )
                    """)
                    self.db.execute("""
                        CREATE TABLE events (
                            sequence INTEGER PRIMARY KEY, deployment_id TEXT NOT NULL,
                            state TEXT NOT NULL, error TEXT, timestamp TEXT NOT NULL
                        )
                    """)
                if version in (0, 1):
                    for column, kind in _V2_DEPLOYMENT_COLUMNS:
                        self.db.execute(f"ALTER TABLE deployments ADD COLUMN {column} {kind}")
                    for statement in _V2_TABLES:
                        self.db.execute(statement)
                if version in (0, 1, 2):
                    for statement in _V3_TABLES:
                        self.db.execute(statement)
                    self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        """One IMMEDIATE transaction; nested calls join the outer transaction."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.db.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._depth = 0
            self.db.execute("ROLLBACK")
            raise
        self._depth = 0
        self.db.execute("COMMIT")

    @contextmanager
    def lock(self):
        descriptor = os.open(
            self.directory / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DeploymentError(
                    "Another local deployment operation is running; retry"
                ) from error
            yield
        finally:
            os.close(descriptor)

    # --- audit -----------------------------------------------------------------------------

    def audit(self, event: AuditEvent) -> dict:
        """Durably append one event in its own transaction (or the caller's)."""
        try:
            with self.transaction():
                return audit_log.append(self.db, event)
        except sqlite3.Error as error:
            raise AuditError("Audit log write failed") from error

    def _append(self, event: AuditEvent | None):
        if event is None:
            return
        try:
            audit_log.append(self.db, event)
        except sqlite3.Error as error:
            raise AuditError("Audit log write failed") from error

    def audit_rows(self, limit: int = 50, attempt_id: str | None = None) -> list[dict]:
        if attempt_id:
            rows = self.db.execute(
                "SELECT * FROM audit WHERE attempt_id=? ORDER BY seq", (attempt_id,)
            )
        else:
            rows = self.db.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
        return [audit_log.decode(row) for row in rows]

    def verify_audit(self) -> dict:
        return audit_log.verify(self.db)

    # --- deployments -----------------------------------------------------------------------

    def get(self, identifier: str) -> dict:
        row = self.db.execute("SELECT * FROM deployments WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise DeploymentError("Deployment not found in this state directory")
        result = dict(row)
        result.pop("manifest")
        result["principal"] = json.loads(result["principal"]) if result["principal"] else None
        result["events"] = [
            dict(event)
            for event in self.db.execute(
                "SELECT state,error,timestamp FROM events WHERE deployment_id=? ORDER BY sequence",
                (identifier,),
            )
        ]
        return result

    def manifest(self, identifier: str) -> dict:
        row = self.db.execute(
            "SELECT manifest FROM deployments WHERE id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise DeploymentError("Deployment not found in this state directory")
        return json.loads(row[0])

    def active(self, name: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM deployments WHERE name=? AND state!='removed'", (name,)
        ).fetchone()
        return dict(row) if row else None

    def active_all(self) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM deployments WHERE state!='removed' ORDER BY created"
            )
        ]

    def active_for_principal(self, principal_key: str, environment: str) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT * FROM deployments WHERE principal_key=? AND environment=? "
                "AND state!='removed'",
                (principal_key, environment),
            )
        ]

    def create(
        self,
        identifier: str,
        manifest: dict,
        digest: str,
        context: str,
        *,
        principal: dict | None = None,
        principal_key: str | None = None,
        environment: str | None = None,
        revision: str | None = None,
        approval_id: str | None = None,
        audit: AuditEvent | None = None,
    ) -> dict:
        now = _now()
        with self.transaction():
            self.db.execute(
                "INSERT INTO deployments (id,name,digest,manifest,context,container,resource,"
                "state,endpoint,error,created,updated,principal_key,principal,environment,"
                "revision,approval_id,compliance) "
                "VALUES (?,?,?,?,?,?,NULL,'pending',NULL,NULL,?,?,?,?,?,?,?,NULL)",
                (
                    identifier,
                    manifest["name"],
                    digest,
                    json.dumps(manifest),
                    context,
                    "agentbreeder-" + identifier,
                    now,
                    now,
                    principal_key,
                    json.dumps(principal) if principal else None,
                    environment,
                    revision,
                    approval_id,
                ),
            )
            self.db.execute(
                "INSERT INTO events(deployment_id,state,timestamp) VALUES (?,'pending',?)",
                (identifier, now),
            )
            if audit:
                audit.deployment_id = identifier
            self._append(audit)
        return self.get(identifier)

    def transition(
        self,
        identifier: str,
        state: str,
        *,
        resource=None,
        endpoint=None,
        error=None,
        audit: AuditEvent | None = None,
    ):
        now = _now()
        with self.transaction():
            self.db.execute(
                "UPDATE deployments SET state=?,resource=COALESCE(?,resource),endpoint=?,"
                "error=?,updated=? WHERE id=?",
                (state, resource, endpoint, error, now, identifier),
            )
            self.db.execute(
                "INSERT INTO events(deployment_id,state,error,timestamp) VALUES (?,?,?,?)",
                (identifier, state, error, now),
            )
            self._append(audit)
        return self.get(identifier)

    def update_binding(
        self,
        identifier: str,
        *,
        revision: str | None,
        approval_id: str | None,
        principal: dict | None = None,
        principal_key: str | None = None,
        environment: str | None = None,
    ):
        with self.transaction():
            self.db.execute(
                "UPDATE deployments SET revision=?,approval_id=?,"
                "principal=COALESCE(?,principal),principal_key=COALESCE(?,principal_key),"
                "environment=COALESCE(?,environment) WHERE id=?",
                (
                    revision,
                    approval_id,
                    json.dumps(principal) if principal else None,
                    principal_key,
                    environment,
                    identifier,
                ),
            )

    def set_compliance(self, identifier: str, compliance: str):
        with self.transaction():
            self.db.execute(
                "UPDATE deployments SET compliance=?,updated=? WHERE id=?",
                (compliance, _now(), identifier),
            )

    # --- approvals -------------------------------------------------------------------------

    def create_approval(self, approval: dict, audit: AuditEvent) -> dict:
        with self.transaction():
            self.db.execute(
                "INSERT INTO approvals (id,revision,evidence,principal_key,environment,agent,"
                "deployer,deployer_verified,sponsors,obligations,state,created,expires) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
                (
                    approval["id"],
                    approval["revision"],
                    approval["evidence"],
                    approval["principal_key"],
                    approval["environment"],
                    approval["agent"],
                    approval["deployer"],
                    int(approval["deployer_verified"]),
                    json.dumps(approval["sponsors"]),
                    json.dumps(approval["obligations"]),
                    approval["created"],
                    approval["expires"],
                ),
            )
            self._append(audit)
        return self.approval(approval["id"])

    def approval(self, identifier: str) -> dict:
        row = self.db.execute("SELECT * FROM approvals WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise DeploymentError("Approval request not found in this state directory")
        return self._approval(row)

    def approvals_for_revision(self, revision: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM approvals WHERE revision=? ORDER BY created DESC", (revision,)
        )
        return [self._approval(row) for row in rows]

    def open_approvals(self, principal_key: str, environment: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM approvals WHERE principal_key=? AND environment=? "
            "AND state IN ('pending','approved')",
            (principal_key, environment),
        )
        return [self._approval(row) for row in rows]

    @staticmethod
    def _approval(row) -> dict:
        result = dict(row)
        result["sponsors"] = json.loads(result["sponsors"])
        result["obligations"] = json.loads(result["obligations"])
        result["checker_claims"] = (
            json.loads(result["checker_claims"]) if result["checker_claims"] else None
        )
        result["deployer_verified"] = bool(result["deployer_verified"])
        return result

    def decide_approval(
        self,
        identifier: str,
        *,
        expected: tuple[str, ...],
        state: str,
        audit: AuditEvent,
        checker: str | None = None,
        checker_claims: dict | None = None,
        execution_id: str | None = None,
    ) -> bool:
        """Compare-and-set transition; returns False if the request was not in `expected`."""
        with self.transaction():
            placeholders = ",".join("?" * len(expected))
            cursor = self.db.execute(
                f"UPDATE approvals SET state=?,checker=COALESCE(?,checker),"
                f"checker_claims=COALESCE(?,checker_claims),"
                f"execution_id=COALESCE(?,execution_id),decided=? "
                f"WHERE id=? AND state IN ({placeholders})",
                (
                    state,
                    checker,
                    json.dumps(checker_claims) if checker_claims else None,
                    execution_id,
                    _now(),
                    identifier,
                    *expected,
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._append(audit)
        return True

    # --- held tool calls (phase 4) ----------------------------------------------------------

    def create_held(self, held: dict, audit: AuditEvent) -> dict:
        with self.transaction():
            self.db.execute(
                "INSERT INTO held_calls (id,deployment_id,binding,server,tool,arguments_digest,"
                "revision,principal_key,environment,deployer_names,state,created,expires) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
                (
                    held["id"],
                    held["deployment_id"],
                    held["binding"],
                    held["server"],
                    held["tool"],
                    held["arguments_digest"],
                    held["revision"],
                    held["principal_key"],
                    held["environment"],
                    json.dumps(held["deployer_names"]),
                    held["created"],
                    held["expires"],
                ),
            )
            self._append(audit)
        return self.held(held["id"])

    def held(self, identifier: str) -> dict:
        row = self.db.execute("SELECT * FROM held_calls WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise DeploymentError("Held call not found in this state directory")
        return self._held(row)

    def held_for_binding(self, deployment_id: str, binding: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM held_calls WHERE deployment_id=? AND binding=? ORDER BY created DESC",
            (deployment_id, binding),
        )
        return [self._held(row) for row in rows]

    @staticmethod
    def _held(row) -> dict:
        result = dict(row)
        result["deployer_names"] = json.loads(result["deployer_names"])
        result["checker_claims"] = (
            json.loads(result["checker_claims"]) if result["checker_claims"] else None
        )
        return result

    def decide_held(
        self,
        identifier: str,
        *,
        expected: tuple[str, ...],
        state: str,
        audit: AuditEvent,
        checker: str | None = None,
        checker_claims: dict | None = None,
        require_unexpired: bool = False,
    ) -> bool:
        """Compare-and-set; a release is consumed exactly once (and, with `require_unexpired`,
        only if unexpired at the moment the write lock is held)."""
        with self.transaction():
            unexpired_at = _now() if require_unexpired else None
            placeholders = ",".join("?" * len(expected))
            expiry = " AND expires > ?" if unexpired_at else ""
            cursor = self.db.execute(
                f"UPDATE held_calls SET state=?,checker=COALESCE(?,checker),"
                f"checker_claims=COALESCE(?,checker_claims),decided=? "
                f"WHERE id=? AND state IN ({placeholders}){expiry}",
                (
                    state,
                    checker,
                    json.dumps(checker_claims) if checker_claims else None,
                    _now(),
                    identifier,
                    *expected,
                    *([unexpired_at] if unexpired_at else []),
                ),
            )
            if cursor.rowcount != 1:
                return False
            self._append(audit)
        return True

    # --- blockers --------------------------------------------------------------------------

    def blocker(self, principal_key: str, environment: str) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM blockers WHERE principal_key=? AND environment=?",
            (principal_key, environment),
        ).fetchone()
        return dict(row) if row else None

    def blocked_reference(self, provider: str, ref: str, environment: str) -> bool:
        """Pre-resolution check: is (connection, ref) blocked here, under any authority?

        Lets the gate honour a blocker even when the IdP is unreachable (audit mode must never
        bypass a blocker). Matches the immutable ID, or a bound deployment's manifest reference.
        """
        keys = [
            row[0]
            for row in self.db.execute(
                "SELECT principal_key FROM blockers WHERE environment=?", (environment,)
            )
        ]
        for key in keys:
            connection, _, identifier = json.loads(key)
            if connection == provider and identifier.lower() == ref.lower():
                return True
        for key in keys:
            for row in self.db.execute(
                "SELECT manifest FROM deployments WHERE principal_key=? AND environment=?",
                (key, environment),
            ):
                identity = json.loads(row[0]).get("identity") or {}
                if identity.get("provider") == provider and identity.get("ref") == ref:
                    return True
        return False

    def add_blocker(
        self, principal_key, environment, actor, actor_verified, reason, audit: AuditEvent
    ):
        with self.transaction():
            self.db.execute(
                "INSERT OR IGNORE INTO blockers VALUES (?,?,?,?,?,?)",
                (principal_key, environment, actor, int(actor_verified), reason, _now()),
            )
            self._append(audit)

    def remove_blocker(self, principal_key, environment, audit: AuditEvent) -> bool:
        with self.transaction():
            cursor = self.db.execute(
                "DELETE FROM blockers WHERE principal_key=? AND environment=?",
                (principal_key, environment),
            )
            self._append(audit)
        return cursor.rowcount == 1
