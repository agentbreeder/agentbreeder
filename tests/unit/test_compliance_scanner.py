"""Unit tests for the SOC 2 / HIPAA compliance scanner (#208).

Each control has its own targeted test that mocks or pre-populates the DB
to drive the control into pass / fail / partial / skipped paths.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.audit import AuditEvent
from api.models.database import Base, ResourcePermission, User
from engine.compliance import CONTROL_REGISTRY, run_compliance_scan
from engine.compliance.controls import (
    check_audit_log_retention,
    check_db_ssl_enabled,
    check_encryption_at_rest_documented,
    check_mfa_enabled,
    check_rbac_enforced,
    check_secrets_backend_not_env,
)
from engine.compliance.scanner import _overall_status


@pytest.fixture
async def db_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with SessionFactory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registry_has_six_controls(self) -> None:
        assert len(CONTROL_REGISTRY) == 6

    def test_every_control_has_id_name_category_standards(self) -> None:
        for c in CONTROL_REGISTRY:
            assert c.control_id
            assert c.name
            assert c.category
            assert isinstance(c.standards, tuple)
            assert all(s.startswith(("SOC2:", "HIPAA:")) for s in c.standards)

    def test_control_ids_are_unique(self) -> None:
        ids = [c.control_id for c in CONTROL_REGISTRY]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# audit_log_retention
# ---------------------------------------------------------------------------


class TestAuditLogRetention:
    @pytest.mark.asyncio
    async def test_partial_when_no_rows(self, db_session: AsyncSession) -> None:
        result = await check_audit_log_retention(db_session)
        assert result.status == "partial"
        assert result.evidence["row_count"] == 0

    @pytest.mark.asyncio
    async def test_partial_when_sparse(self, db_session: AsyncSession) -> None:
        for i in range(10):
            db_session.add(
                AuditEvent(
                    actor=f"user-{i}",
                    action="deploy",
                    resource_type="agent",
                    resource_name=f"agent-{i}",
                )
            )
        await db_session.commit()
        result = await check_audit_log_retention(db_session)
        assert result.status == "partial"
        assert result.evidence["row_count"] == 10

    @pytest.mark.asyncio
    async def test_pass_when_oldest_is_old_enough(self, db_session: AsyncSession) -> None:
        # 50+ rows, oldest 400 days back.
        old = datetime.now(UTC) - timedelta(days=400)
        for i in range(60):
            ev = AuditEvent(
                actor=f"u-{i}",
                action="x",
                resource_type="agent",
                resource_name=f"a-{i}",
            )
            ev.created_at = old if i == 0 else datetime.now(UTC)
            db_session.add(ev)
        await db_session.commit()
        result = await check_audit_log_retention(db_session)
        assert result.status == "pass"
        assert result.evidence["age_days"] >= 365

    @pytest.mark.asyncio
    async def test_fail_when_oldest_too_recent(self, db_session: AsyncSession) -> None:
        # 60 rows but all created today — we can't claim 365d retention.
        for i in range(60):
            db_session.add(
                AuditEvent(
                    actor=f"u-{i}",
                    action="x",
                    resource_type="agent",
                    resource_name=f"a-{i}",
                )
            )
        await db_session.commit()
        result = await check_audit_log_retention(db_session)
        assert result.status == "fail"
        assert result.evidence["age_days"] < 365


# ---------------------------------------------------------------------------
# rbac_enforced
# ---------------------------------------------------------------------------


class TestRbacEnforced:
    @pytest.mark.asyncio
    async def test_partial_when_no_acl_rows(self, db_session: AsyncSession) -> None:
        result = await check_rbac_enforced(db_session)
        assert result.status == "partial"
        assert result.evidence["acl_row_count"] == 0

    @pytest.mark.asyncio
    async def test_pass_when_acl_populated(self, db_session: AsyncSession) -> None:
        db_session.add(
            ResourcePermission(
                resource_type="agent",
                resource_id=uuid.uuid4(),
                principal_type="user",
                principal_id="alice@example.com",
                actions=["read", "deploy"],
                created_by="admin@example.com",
            )
        )
        await db_session.commit()
        result = await check_rbac_enforced(db_session)
        assert result.status == "pass"
        assert result.evidence["acl_row_count"] == 1


# ---------------------------------------------------------------------------
# secrets_backend_not_env
# ---------------------------------------------------------------------------


class TestSecretsBackendNotEnv:
    @pytest.mark.asyncio
    async def test_fail_when_env_backend(self, db_session: AsyncSession, monkeypatch) -> None:

        class _FakeWS:
            backend = "env"
            workspace = "default"

        def _fake_get(workspace=None):
            return (object(), _FakeWS())

        monkeypatch.setattr(
            "engine.secrets.factory.get_workspace_backend",
            _fake_get,
            raising=True,
        )
        result = await check_secrets_backend_not_env(db_session)
        assert result.status == "fail"
        assert result.evidence["backend"] == "env"

    @pytest.mark.asyncio
    async def test_pass_when_aws_backend(self, db_session: AsyncSession, monkeypatch) -> None:
        class _FakeWS:
            backend = "aws"
            workspace = "default"

        def _fake_get(workspace=None):
            return (object(), _FakeWS())

        monkeypatch.setattr(
            "engine.secrets.factory.get_workspace_backend",
            _fake_get,
            raising=True,
        )
        result = await check_secrets_backend_not_env(db_session)
        assert result.status == "pass"
        assert result.evidence["backend"] == "aws"


# ---------------------------------------------------------------------------
# db_ssl_enabled
# ---------------------------------------------------------------------------


class TestDbSslEnabled:
    @pytest.mark.asyncio
    async def test_skipped_on_sqlite(self, db_session: AsyncSession) -> None:
        result = await check_db_ssl_enabled(db_session)
        assert result.status == "skipped"
        assert result.evidence["dialect"] == "sqlite"


# ---------------------------------------------------------------------------
# mfa_enabled
# ---------------------------------------------------------------------------


class TestMfaEnabled:
    @pytest.mark.asyncio
    async def test_partial_when_no_users(self, db_session: AsyncSession) -> None:
        result = await check_mfa_enabled(db_session)
        assert result.status == "partial"
        assert result.evidence["active_users"] == 0

    @pytest.mark.asyncio
    async def test_partial_when_users_have_passwords(self, db_session: AsyncSession) -> None:
        db_session.add(
            User(
                email="alice@example.com",
                name="Alice",
                password_hash="$argon2id$dummy",
                is_active=True,
            )
        )
        await db_session.commit()
        result = await check_mfa_enabled(db_session)
        # Real MFA still TODO -> always 'partial' (not 'pass')
        assert result.status == "partial"
        assert result.evidence["active_users"] == 1
        assert result.evidence["users_without_password_hash"] == 0
        assert "todo" in result.evidence


# ---------------------------------------------------------------------------
# encryption_at_rest_documented
# ---------------------------------------------------------------------------


class TestEncryptionAtRestDocumented:
    @pytest.mark.asyncio
    async def test_pass_when_security_md_exists(self, db_session: AsyncSession) -> None:
        # docs/security.md ships with the repo as part of this PR.
        result = await check_encryption_at_rest_documented(db_session)
        assert result.status == "pass"
        assert result.evidence["path"].endswith("security.md")


# ---------------------------------------------------------------------------
# Scanner orchestration
# ---------------------------------------------------------------------------


class TestRunScan:
    @pytest.mark.asyncio
    async def test_scan_returns_one_result_per_control(self, db_session: AsyncSession) -> None:
        summary = await run_compliance_scan(db_session)
        assert summary.controls_total == 6
        assert len(summary.results) == 6

    @pytest.mark.asyncio
    async def test_scan_aggregate_counts_match(self, db_session: AsyncSession) -> None:
        summary = await run_compliance_scan(db_session)
        results = summary.results
        assert summary.controls_passed == sum(1 for r in results if r.status == "pass")
        assert summary.controls_failed == sum(1 for r in results if r.status == "fail")
        assert summary.controls_partial == sum(1 for r in results if r.status == "partial")
        assert summary.controls_skipped == sum(1 for r in results if r.status == "skipped")

    @pytest.mark.asyncio
    async def test_scan_traps_per_control_exceptions(self, db_session: AsyncSession) -> None:
        from engine.compliance.controls import Control

        async def _explode(_db):
            raise RuntimeError("boom")

        bad = Control(
            control_id="bad",
            name="Always raises",
            category="Test",
            standards=("SOC2:CC1",),
            check=_explode,
        )
        # Need at least one normal control + the bad one.
        from engine.compliance.controls import check_rbac_enforced

        ok = Control(
            control_id="rbac",
            name="rbac",
            category="Access Control",
            standards=("SOC2:CC6.1",),
            check=check_rbac_enforced,
        )
        summary = await run_compliance_scan(db_session, controls=(ok, bad))
        assert summary.controls_total == 2
        bad_result = next(r for r in summary.results if r.control_id == "bad")
        assert bad_result.status == "skipped"
        assert "boom" in bad_result.evidence.get("error", "")

    def test_overall_status_helper(self) -> None:
        from engine.compliance.controls import ControlResult

        def _r(status):
            return ControlResult(control_id="x", name="x", category="x", status=status)

        assert _overall_status([_r("pass"), _r("pass")]) == "compliant"
        assert _overall_status([_r("pass"), _r("partial")]) == "partial"
        assert _overall_status([_r("partial"), _r("fail")]) == "non_compliant"
        assert _overall_status([_r("pass"), _r("skipped")]) == "compliant"


# ---------------------------------------------------------------------------
# Aborted-transaction recovery (rollback after SQL errors)
# ---------------------------------------------------------------------------


class _FailingSession:
    """Session stub whose queries always fail — every control must roll back
    so the (real) Postgres transaction is no longer aborted for later
    controls and the final ``compliance_scans`` INSERT."""

    def __init__(self) -> None:
        self.rollbacks = 0
        # Present as PostgreSQL so dialect-gated controls run their queries.
        from types import SimpleNamespace

        self.bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    async def execute(self, *_args, **_kwargs):
        from sqlalchemy.exc import SQLAlchemyError

        raise SQLAlchemyError("simulated query failure")

    async def rollback(self) -> None:
        self.rollbacks += 1


class TestSqlErrorRollback:
    @pytest.mark.asyncio
    async def test_audit_log_retention_rolls_back(self) -> None:
        db = _FailingSession()
        result = await check_audit_log_retention(db)  # type: ignore[arg-type]
        assert result.status == "skipped"
        assert db.rollbacks == 1

    @pytest.mark.asyncio
    async def test_rbac_enforced_rolls_back(self) -> None:
        db = _FailingSession()
        result = await check_rbac_enforced(db)  # type: ignore[arg-type]
        assert result.status == "skipped"
        assert db.rollbacks == 1

    @pytest.mark.asyncio
    async def test_mfa_enabled_rolls_back(self) -> None:
        db = _FailingSession()
        result = await check_mfa_enabled(db)  # type: ignore[arg-type]
        assert result.status == "skipped"
        assert db.rollbacks == 1

    @pytest.mark.asyncio
    async def test_db_ssl_rolls_back_twice_when_fallback_also_fails(self) -> None:
        db = _FailingSession()
        result = await check_db_ssl_enabled(db)  # type: ignore[arg-type]
        assert result.status == "skipped"
        # Primary sslinfo query AND the pg_stat_ssl fallback both fail —
        # each must clear the aborted transaction.
        assert db.rollbacks == 2

    @pytest.mark.asyncio
    async def test_scanner_rolls_back_when_control_raises(self) -> None:
        from engine.compliance.controls import Control

        async def _exploding_check(db):  # noqa: ANN001
            raise RuntimeError("control blew up")

        control = Control(
            control_id="exploding",
            name="Exploding control",
            category="Test",
            standards=(),
            check=_exploding_check,
        )
        db = _FailingSession()
        summary = await run_compliance_scan(db, controls=(control,))  # type: ignore[arg-type]
        assert summary.results[0].status == "skipped"
        assert db.rollbacks == 1
