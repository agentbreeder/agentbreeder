"""SOC 2 / HIPAA compliance control registry.

Each control is a small ``Control`` dataclass that knows how to introspect the
live AgentBreeder deployment (database state, secrets backend, repo files) and
report a ``ControlResult``. The scanner in :mod:`engine.compliance.scanner`
runs every control and persists the aggregate result to ``compliance_scans``.

Six controls ship in #208 — the minimum SOC 2 / HIPAA-aligned baseline we can
honestly claim to enforce against today's codebase:

  1. ``audit_log_retention``   — oldest ``audit_events`` row >= 365d (or sparse)
  2. ``rbac_enforced``         — at least one ``ResourcePermission`` row exists
  3. ``secrets_backend_not_env`` — workspace backend is not the local ``env``
                                  fallback
  4. ``db_ssl_enabled``        — Postgres ``ssl_is_used()`` returns true
                                  (skipped on SQLite test backends)
  5. ``mfa_enabled``           — every active user has a non-empty
                                  ``password_hash`` (proxy until real MFA ships)
  6. ``encryption_at_rest_documented`` — ``docs/security.md`` exists in the
                                  repo (encryption-at-rest is a deployment-time
                                  concern documented there)

Controls return a structured ``ControlResult`` so the report can cite the
*actual* evidence (counts, timestamps, backend names) rather than a static
``"Automated compliance check for X"`` placeholder.

Two follow-up controls — real MFA scanning and scheduled cron-driven scans —
are deferred until the underlying primitives ship; see the PR body for #208.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.audit import AuditEvent
from api.models.database import ResourcePermission, User

logger = logging.getLogger(__name__)


ControlStatus = Literal["pass", "fail", "partial", "skipped"]


@dataclass
class ControlResult:
    """The outcome of running a single control's check function."""

    control_id: str
    name: str
    category: str
    status: ControlStatus
    evidence: dict[str, Any] = field(default_factory=dict)
    details: str = ""
    last_checked: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.control_id,
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "evidence": self.evidence,
            "details": self.details,
            "last_checked": self.last_checked,
        }


CheckFn = Callable[[AsyncSession], Awaitable[ControlResult]]


@dataclass(frozen=True)
class Control:
    """A registered compliance control."""

    control_id: str
    name: str
    category: str
    standards: tuple[str, ...]  # e.g. ("SOC2:CC6", "HIPAA:164.312(a)(1)")
    check: CheckFn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _result(
    control_id: str,
    name: str,
    category: str,
    status: ControlStatus,
    *,
    evidence: dict[str, Any] | None = None,
    details: str = "",
) -> ControlResult:
    return ControlResult(
        control_id=control_id,
        name=name,
        category=category,
        status=status,
        evidence=evidence or {},
        details=details,
    )


def _repo_root() -> Path:
    """Return the repo root by walking up from this file's location.

    ``engine/compliance/controls.py`` is two parents below the repo root.
    """
    return Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Control implementations
# ---------------------------------------------------------------------------


async def check_audit_log_retention(db: AsyncSession) -> ControlResult:
    """Pass if the oldest ``audit_events`` row is >= 365 days old, OR if the
    log is sparse (< 50 rows total) — a brand-new deployment can't have a
    year of history yet, and failing it on day one would be misleading.
    """
    name = "Audit log retention >= 365 days"
    category = "Audit"
    cid = "audit_log_retention"

    try:
        oldest_q = await db.execute(select(func.min(AuditEvent.created_at)))
        oldest = oldest_q.scalar_one_or_none()
        count_q = await db.execute(select(func.count()).select_from(AuditEvent))
        total = int(count_q.scalar_one() or 0)
    except SQLAlchemyError as exc:
        logger.warning("audit_log_retention: query failed: %s", exc)
        return _result(
            cid,
            name,
            category,
            "skipped",
            details=f"Could not query audit_events: {exc}",
        )

    if total == 0:
        return _result(
            cid,
            name,
            category,
            "partial",
            evidence={"row_count": 0},
            details=("No audit events recorded yet — retention cannot be evaluated."),
        )

    if total < 50:
        return _result(
            cid,
            name,
            category,
            "partial",
            evidence={"row_count": total, "oldest": oldest.isoformat() if oldest else None},
            details=(f"Audit log is sparse ({total} rows) — retention cannot yet be confirmed."),
        )

    if oldest is None:
        return _result(
            cid,
            name,
            category,
            "fail",
            evidence={"row_count": total},
            details="audit_events.created_at is NULL — cannot verify retention.",
        )

    # Normalise to aware datetime
    oldest_aware = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
    age_days = (datetime.now(UTC) - oldest_aware).days
    if age_days >= 365:
        return _result(
            cid,
            name,
            category,
            "pass",
            evidence={
                "row_count": total,
                "oldest": oldest_aware.isoformat(),
                "age_days": age_days,
            },
            details=(f"Oldest audit event is {age_days} days old across {total} rows."),
        )
    return _result(
        cid,
        name,
        category,
        "fail",
        evidence={
            "row_count": total,
            "oldest": oldest_aware.isoformat(),
            "age_days": age_days,
        },
        details=(f"Oldest audit event is only {age_days} days old (< 365)."),
    )


async def check_rbac_enforced(db: AsyncSession) -> ControlResult:
    """Pass if at least one ``ResourcePermission`` row exists.

    A populated ACL means RBAC is actively in use. An empty ACL means every
    request resolves through team-default policy only — usable, but not
    least-privilege at the resource level.
    """
    name = "RBAC enforced (resource ACL in use)"
    category = "Access Control"
    cid = "rbac_enforced"

    try:
        count_q = await db.execute(select(func.count()).select_from(ResourcePermission))
        total = int(count_q.scalar_one() or 0)
    except SQLAlchemyError as exc:
        return _result(
            cid,
            name,
            category,
            "skipped",
            details=f"Could not query resource_permissions: {exc}",
        )

    if total == 0:
        return _result(
            cid,
            name,
            category,
            "partial",
            evidence={"acl_row_count": 0},
            details=("No resource_permissions rows — falling back to team-default policy only."),
        )
    return _result(
        cid,
        name,
        category,
        "pass",
        evidence={"acl_row_count": total},
        details=f"{total} resource ACL entries enforced.",
    )


async def check_secrets_backend_not_env(_db: AsyncSession) -> ControlResult:
    """Pass if the workspace secrets backend is anything other than ``env``.

    The local ``.env`` backend is fine for development but not acceptable for
    SOC 2 / HIPAA evidence — secrets must live in a managed Secrets Manager
    or Vault.
    """
    name = "Secrets backend is not local .env"
    category = "Data Security"
    cid = "secrets_backend_not_env"

    try:
        from engine.secrets.factory import get_workspace_backend

        _, ws = get_workspace_backend()
    except Exception as exc:  # noqa: BLE001 — config errors should not break a scan
        return _result(
            cid,
            name,
            category,
            "skipped",
            details=f"Could not resolve workspace secrets backend: {exc}",
        )

    backend_name = (ws.backend or "").lower()
    if backend_name and backend_name != "env":
        return _result(
            cid,
            name,
            category,
            "pass",
            evidence={"backend": backend_name, "workspace": ws.workspace},
            details=f"Workspace '{ws.workspace}' uses '{backend_name}' backend.",
        )
    return _result(
        cid,
        name,
        category,
        "fail",
        evidence={"backend": backend_name or "env", "workspace": ws.workspace},
        details=("Workspace is using the local .env fallback — move to AWS/GCP/Vault for prod."),
    )


async def check_db_ssl_enabled(db: AsyncSession) -> ControlResult:
    """Pass if PostgreSQL ``ssl_is_used()`` returns true.

    Skipped (not failed) on non-Postgres backends — SQLite is used by tests
    and local quickstart, where TLS-to-DB is meaningless.
    """
    name = "Database connection uses TLS"
    category = "Data Security"
    cid = "db_ssl_enabled"

    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect != "postgresql":
        return _result(
            cid,
            name,
            category,
            "skipped",
            evidence={"dialect": dialect or "unknown"},
            details="DB is not PostgreSQL — TLS check skipped.",
        )

    try:
        result = await db.execute(text("SELECT ssl_is_used()"))
        ssl_used = bool(result.scalar_one())
    except SQLAlchemyError as exc:
        # ``ssl_is_used()`` requires the ``sslinfo`` extension. If absent,
        # fall back to checking pg_stat_ssl which ships in core pg.
        try:
            result = await db.execute(
                text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            )
            ssl_used = bool(result.scalar_one_or_none())
        except SQLAlchemyError as exc2:
            return _result(
                cid,
                name,
                category,
                "skipped",
                details=f"Could not query SSL status: {exc} / fallback: {exc2}",
            )

    if ssl_used:
        return _result(
            cid,
            name,
            category,
            "pass",
            evidence={"ssl": True, "dialect": dialect},
            details="Database connection is TLS-encrypted.",
        )
    return _result(
        cid,
        name,
        category,
        "fail",
        evidence={"ssl": False, "dialect": dialect},
        details="Database connection is plaintext — set ?sslmode=require in DATABASE_URL.",
    )


async def check_mfa_enabled(db: AsyncSession) -> ControlResult:
    """Proxy MFA check.

    Real MFA is not yet wired in AgentBreeder — this control passes when every
    active user has a non-empty ``password_hash``, which is the strongest
    auth signal we currently store. Marked ``partial`` until real MFA lands.
    """
    name = "MFA enabled for active users"
    category = "Access Control"
    cid = "mfa_enabled"

    try:
        active_q = await db.execute(
            select(func.count()).select_from(User).where(User.is_active.is_(True))
        )
        active_total = int(active_q.scalar_one() or 0)
        unhashed_q = await db.execute(
            select(func.count())
            .select_from(User)
            .where(
                User.is_active.is_(True),
                (User.password_hash.is_(None)) | (User.password_hash == ""),
            )
        )
        unhashed = int(unhashed_q.scalar_one() or 0)
    except SQLAlchemyError as exc:
        return _result(
            cid,
            name,
            category,
            "skipped",
            details=f"Could not query users: {exc}",
        )

    if active_total == 0:
        return _result(
            cid,
            name,
            category,
            "partial",
            evidence={"active_users": 0},
            details="No active users — MFA cannot be evaluated.",
        )
    if unhashed > 0:
        return _result(
            cid,
            name,
            category,
            "fail",
            evidence={"active_users": active_total, "users_without_password_hash": unhashed},
            details=f"{unhashed} active users have no password_hash — auth is misconfigured.",
        )
    # All active users have hashed credentials. Real MFA is still TODO.
    return _result(
        cid,
        name,
        category,
        "partial",
        evidence={
            "active_users": active_total,
            "users_without_password_hash": 0,
            "todo": "Real TOTP/WebAuthn MFA not yet implemented.",
        },
        details=(
            f"All {active_total} active users have hashed credentials. "
            "True MFA pending — this control will tighten when MFA ships."
        ),
    )


async def check_encryption_at_rest_documented(_db: AsyncSession) -> ControlResult:
    """Pass if ``docs/security.md`` exists in the repo root.

    Encryption-at-rest is a deployment-time concern (RDS / Cloud SQL / EBS
    encryption flags). The auditable artifact is the documented procedure;
    this control verifies the doc exists.
    """
    name = "Encryption at rest documented"
    category = "Data Security"
    cid = "encryption_at_rest_documented"

    doc = _repo_root() / "docs" / "security.md"
    if doc.exists() and doc.is_file() and doc.stat().st_size > 0:
        return _result(
            cid,
            name,
            category,
            "pass",
            evidence={"path": str(doc.relative_to(_repo_root()))},
            details="docs/security.md exists and is non-empty.",
        )
    return _result(
        cid,
        name,
        category,
        "fail",
        evidence={"expected_path": "docs/security.md"},
        details="docs/security.md is missing — write the encryption-at-rest runbook.",
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


CONTROL_REGISTRY: tuple[Control, ...] = (
    Control(
        control_id="audit_log_retention",
        name="Audit log retention >= 365 days",
        category="Audit",
        standards=("SOC2:CC7.2", "HIPAA:164.312(b)"),
        check=check_audit_log_retention,
    ),
    Control(
        control_id="rbac_enforced",
        name="RBAC enforced (resource ACL in use)",
        category="Access Control",
        standards=("SOC2:CC6.1", "HIPAA:164.308(a)(4)"),
        check=check_rbac_enforced,
    ),
    Control(
        control_id="secrets_backend_not_env",
        name="Secrets backend is not local .env",
        category="Data Security",
        standards=("SOC2:CC6.7", "HIPAA:164.312(a)(2)(iv)"),
        check=check_secrets_backend_not_env,
    ),
    Control(
        control_id="db_ssl_enabled",
        name="Database connection uses TLS",
        category="Data Security",
        standards=("SOC2:CC6.7", "HIPAA:164.312(e)(1)"),
        check=check_db_ssl_enabled,
    ),
    Control(
        control_id="mfa_enabled",
        name="MFA enabled for active users",
        category="Access Control",
        standards=("SOC2:CC6.1", "HIPAA:164.312(d)"),
        check=check_mfa_enabled,
    ),
    Control(
        control_id="encryption_at_rest_documented",
        name="Encryption at rest documented",
        category="Data Security",
        standards=("SOC2:CC6.1", "HIPAA:164.312(a)(2)(iv)"),
        check=check_encryption_at_rest_documented,
    ),
)
