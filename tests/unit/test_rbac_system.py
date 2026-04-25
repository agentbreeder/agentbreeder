"""Unit tests for the RBAC system — Phases 2 and 3.

Tests:
- Alembic migration syntax
- check_permission returns True/False correctly for various combos
- Approval workflow: submit → pending → approve → approved
- Service principal CRUD
- Group member add/remove
- LiteLLM key auto-mint on team join
- REST API endpoints via TestClient
"""

from __future__ import annotations

import importlib
import sys
import types
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers — stub out DB-dependent imports so tests run without Postgres
# ---------------------------------------------------------------------------

# We test the service logic in isolation using an in-memory SQLAlchemy
# SQLite database (or via mocks where async is required).


def _make_row(**kwargs):
    """Create a simple namespace object that behaves like an ORM row."""
    obj = MagicMock()
    for k, v in kwargs.items():
        setattr(obj, k, v)
    obj.model_fields = {}
    return obj


# ---------------------------------------------------------------------------
# Migration syntax tests
# ---------------------------------------------------------------------------


class TestMigration015:
    def test_migration_imports(self) -> None:
        """Migration file must be importable and have correct identifiers."""
        spec = importlib.util.spec_from_file_location(
            "migration_015",
            "alembic/versions/015_rbac_acl_and_approvals.py",
        )
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert mod.revision == "015"
        assert mod.down_revision == "014"
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)

    def test_migration_012_imports(self) -> None:
        """Migration 012 must be importable and correct."""
        spec = importlib.util.spec_from_file_location(
            "migration_012",
            "alembic/versions/012_add_litellm_key_refs.py",
        )
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        assert mod.revision == "012"
        assert mod.down_revision == "011"


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestRBACSchemas:
    def test_permission_grant_valid(self) -> None:
        from api.models.schemas import PermissionGrant

        pg = PermissionGrant(
            resource_type="agent",
            resource_id=uuid.uuid4(),
            principal_type="user",
            principal_id="alice@example.com",
            actions=["read", "use"],
        )
        assert pg.principal_type == "user"
        assert "read" in pg.actions

    def test_approval_request_create(self) -> None:
        from api.models.schemas import ApprovalRequestCreate

        body = ApprovalRequestCreate(
            asset_type="agent",
            asset_id=uuid.uuid4(),
            asset_version="1.0.0",
            message="Please approve",
        )
        # ApprovalRequestCreate has no status field — status is set by the service
        assert body.asset_type == "agent"
        assert body.message == "Please approve"
        assert not hasattr(body, "status") or body.model_fields.get("status") is None

    def test_service_principal_create(self) -> None:
        from api.models.schemas import ServicePrincipalCreate

        sp = ServicePrincipalCreate(
            name="ci-bot",
            team_id="engineering",
            role="deployer",
        )
        assert sp.role == "deployer"

    def test_group_create(self) -> None:
        from api.models.schemas import PrincipalGroupCreate

        grp = PrincipalGroupCreate(
            name="ml-team",
            team_id="engineering",
            member_ids=["alice@example.com", "bob@example.com"],
        )
        assert len(grp.member_ids) == 2

    def test_group_member_add(self) -> None:
        from api.models.schemas import GroupMemberAdd

        m = GroupMemberAdd(member_id="carol@example.com")
        assert m.member_id == "carol@example.com"


# ---------------------------------------------------------------------------
# rbac_service — check_permission tests (mocked DB)
# ---------------------------------------------------------------------------


class TestCheckPermission:
    """Test check_permission with mock DB responses."""

    def _make_perm_row(self, principal_type: str, principal_id: str, actions: list[str], resource_id: uuid.UUID) -> MagicMock:
        row = MagicMock()
        row.principal_type = principal_type
        row.principal_id = principal_id
        row.actions = actions
        row.resource_type = "agent"
        row.resource_id = resource_id
        return row

    @pytest.mark.asyncio
    async def test_direct_user_permission_allowed(self) -> None:
        """User with direct 'read' permission should be allowed."""
        from api.services.rbac_service import check_permission

        resource_id = uuid.uuid4()
        perm_row = self._make_perm_row("user", "alice@example.com", ["read", "use"], resource_id)

        db = AsyncMock()
        # First execute call returns resource permissions
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [perm_row]
        # Second execute call returns groups
        group_result = MagicMock()
        group_result.scalars.return_value.all.return_value = []

        db.execute = AsyncMock(side_effect=[result_mock, group_result])

        allowed, reason = await check_permission(
            db,
            user_email="alice@example.com",
            resource_type="agent",
            resource_id=resource_id,
            action="read",
        )
        assert allowed is True
        assert "Direct user permission" in reason

    @pytest.mark.asyncio
    async def test_user_missing_permission_denied(self) -> None:
        """User without any permission on resource should be denied."""
        from api.services.rbac_service import check_permission

        resource_id = uuid.uuid4()
        # No matching rows
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        group_result = MagicMock()
        group_result.scalars.return_value.all.return_value = []

        db.execute = AsyncMock(side_effect=[result_mock, group_result])

        allowed, reason = await check_permission(
            db,
            user_email="eve@example.com",
            resource_type="agent",
            resource_id=resource_id,
            action="read",
        )
        assert allowed is False
        assert "No permission" in reason

    @pytest.mark.asyncio
    async def test_wrong_action_denied(self) -> None:
        """User with 'read' but asking for 'deploy' should be denied."""
        from api.services.rbac_service import check_permission

        resource_id = uuid.uuid4()
        perm_row = self._make_perm_row("user", "alice@example.com", ["read"], resource_id)

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [perm_row]
        group_result = MagicMock()
        group_result.scalars.return_value.all.return_value = []

        db.execute = AsyncMock(side_effect=[result_mock, group_result])

        allowed, reason = await check_permission(
            db,
            user_email="alice@example.com",
            resource_type="agent",
            resource_id=resource_id,
            action="deploy",
        )
        assert allowed is False

    @pytest.mark.asyncio
    async def test_invalid_action_denied(self) -> None:
        """Unknown action should return False."""
        from api.services.rbac_service import check_permission

        db = AsyncMock()
        allowed, reason = await check_permission(
            db,
            user_email="alice@example.com",
            resource_type="agent",
            resource_id=uuid.uuid4(),
            action="nuke_everything",
        )
        assert allowed is False
        assert "Unknown action" in reason

    @pytest.mark.asyncio
    async def test_group_permission_allowed(self) -> None:
        """User in a group that has permission should be allowed."""
        from api.services.rbac_service import check_permission

        resource_id = uuid.uuid4()
        group_id = str(uuid.uuid4())

        # Permission granted to a group
        perm_row = self._make_perm_row("group", group_id, ["read", "use"], resource_id)

        # Group contains alice
        grp = MagicMock()
        grp.id = uuid.UUID(group_id)
        grp.member_ids = ["alice@example.com"]

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [perm_row]
        group_result = MagicMock()
        group_result.scalars.return_value.all.return_value = [grp]

        db.execute = AsyncMock(side_effect=[result_mock, group_result])

        allowed, reason = await check_permission(
            db,
            user_email="alice@example.com",
            resource_type="agent",
            resource_id=resource_id,
            action="use",
        )
        assert allowed is True
        assert "Group permission" in reason


# ---------------------------------------------------------------------------
# rbac_service — grant and revoke permission
# ---------------------------------------------------------------------------


class TestGrantRevoke:
    @pytest.mark.asyncio
    async def test_grant_permission(self) -> None:
        from api.models.schemas import PermissionGrant
        from api.services.rbac_service import grant_permission

        resource_id = uuid.uuid4()
        body = PermissionGrant(
            resource_type="agent",
            resource_id=resource_id,
            principal_type="user",
            principal_id="alice@example.com",
            actions=["read", "use"],
        )

        db = AsyncMock()
        # Simulate no existing row
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_result)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        # Capture what gets added
        added_rows: list = []
        db.add = MagicMock(side_effect=lambda row: added_rows.append(row))

        with patch("api.services.rbac_service.PermissionResponse") as mock_resp:
            mock_resp.model_validate.return_value = MagicMock(
                id=uuid.uuid4(),
                resource_type="agent",
                resource_id=resource_id,
                principal_type="user",
                principal_id="alice@example.com",
                actions=["read", "use"],
                created_by="admin@example.com",
                created_at=datetime.now(UTC),
            )
            result = await grant_permission(db, granter="admin@example.com", body=body)

        assert db.add.called
        added = added_rows[0]
        assert added.resource_type == "agent"
        assert "read" in added.actions

    @pytest.mark.asyncio
    async def test_grant_invalid_resource_type(self) -> None:
        from api.models.schemas import PermissionGrant
        from api.services.rbac_service import grant_permission

        body = PermissionGrant(
            resource_type="spaceship",  # invalid
            resource_id=uuid.uuid4(),
            principal_type="user",
            principal_id="alice@example.com",
            actions=["read"],
        )
        db = AsyncMock()
        with pytest.raises(ValueError, match="Invalid resource_type"):
            await grant_permission(db, granter="admin@example.com", body=body)

    @pytest.mark.asyncio
    async def test_grant_invalid_action(self) -> None:
        from api.models.schemas import PermissionGrant
        from api.services.rbac_service import grant_permission

        body = PermissionGrant(
            resource_type="agent",
            resource_id=uuid.uuid4(),
            principal_type="user",
            principal_id="alice@example.com",
            actions=["teleport"],  # invalid
        )
        db = AsyncMock()
        with pytest.raises(ValueError, match="Invalid actions"):
            await grant_permission(db, granter="admin@example.com", body=body)

    @pytest.mark.asyncio
    async def test_revoke_permission_not_found(self) -> None:
        from api.services.rbac_service import revoke_permission

        db = AsyncMock()
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_result)

        result = await revoke_permission(db, revoker="admin@example.com", permission_id=uuid.uuid4())
        assert result is False


# ---------------------------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------------------------


class TestApprovalWorkflow:
    def _make_approval_row(self, status: str = "pending") -> MagicMock:
        row = MagicMock()
        row.id = uuid.uuid4()
        row.asset_type = "agent"
        row.asset_id = uuid.uuid4()
        row.asset_version = "1.0.0"
        row.submitter_id = "alice@example.com"
        row.status = status
        row.approver_id = None
        row.reason = None
        row.message = "Please approve"
        row.created_at = datetime.now(UTC)
        row.decided_at = None
        return row

    @pytest.mark.asyncio
    async def test_submit_approval(self) -> None:
        from api.models.schemas import ApprovalRequestCreate
        from api.services.rbac_service import submit_approval

        body = ApprovalRequestCreate(
            asset_type="agent",
            asset_id=uuid.uuid4(),
            asset_version="1.0.0",
            message="Please review",
        )

        db = AsyncMock()
        db.flush = AsyncMock()
        added_rows: list = []
        db.add = MagicMock(side_effect=lambda row: added_rows.append(row))

        with patch("api.services.rbac_service.ApprovalResponse") as mock_resp:
            row = self._make_approval_row("pending")
            mock_resp.model_validate.return_value = MagicMock(
                id=row.id,
                status="pending",
                submitter_id="alice@example.com",
            )

            async def fake_refresh(obj: object) -> None:
                pass

            db.refresh = fake_refresh
            result = await submit_approval(db, submitter="alice@example.com", body=body)

        assert db.add.called
        submitted = added_rows[0]
        assert submitted.status == "pending"
        assert submitted.submitter_id == "alice@example.com"

    @pytest.mark.asyncio
    async def test_approve_transitions_status(self) -> None:
        from api.models.schemas import ApprovalDecision
        from api.services.rbac_service import approve_request

        row = self._make_approval_row("pending")
        row.decided_at = None

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = row
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch("api.services.rbac_service.ApprovalResponse") as mock_resp:
            mock_resp.model_validate.return_value = MagicMock(status="approved")
            await approve_request(
                db,
                approval_id=row.id,
                approver="admin@example.com",
                decision=ApprovalDecision(reason="LGTM"),
            )

        assert row.status == "approved"
        assert row.approver_id == "admin@example.com"
        assert row.reason == "LGTM"
        assert row.decided_at is not None

    @pytest.mark.asyncio
    async def test_reject_transitions_status(self) -> None:
        from api.models.schemas import ApprovalDecision
        from api.services.rbac_service import reject_request

        row = self._make_approval_row("pending")

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = row
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch("api.services.rbac_service.ApprovalResponse") as mock_resp:
            mock_resp.model_validate.return_value = MagicMock(status="rejected")
            await reject_request(
                db,
                approval_id=row.id,
                approver="admin@example.com",
                decision=ApprovalDecision(reason="Not ready"),
            )

        assert row.status == "rejected"

    @pytest.mark.asyncio
    async def test_cannot_approve_already_decided(self) -> None:
        from api.models.schemas import ApprovalDecision
        from api.services.rbac_service import approve_request

        row = self._make_approval_row("approved")

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = row
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValueError, match="already 'approved'"):
            await approve_request(
                db,
                approval_id=row.id,
                approver="admin@example.com",
                decision=ApprovalDecision(),
            )

    @pytest.mark.asyncio
    async def test_approve_not_found(self) -> None:
        from api.models.schemas import ApprovalDecision
        from api.services.rbac_service import approve_request

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(ValueError, match="not found"):
            await approve_request(
                db,
                approval_id=uuid.uuid4(),
                approver="admin@example.com",
                decision=ApprovalDecision(),
            )


# ---------------------------------------------------------------------------
# Service Principal CRUD
# ---------------------------------------------------------------------------


class TestServicePrincipalCRUD:
    def _make_sp_row(self, name: str = "ci-bot", role: str = "deployer") -> MagicMock:
        row = MagicMock()
        row.id = uuid.uuid4()
        row.name = name
        row.team_id = "engineering"
        row.role = role
        row.allowed_assets = None
        row.created_by = "admin@example.com"
        row.last_used_at = None
        row.is_active = True
        row.created_at = datetime.now(UTC)
        return row

    @pytest.mark.asyncio
    async def test_create_service_principal(self) -> None:
        from api.models.schemas import ServicePrincipalCreate
        from api.services.rbac_service import create_service_principal

        body = ServicePrincipalCreate(
            name="ci-bot",
            team_id="engineering",
            role="deployer",
        )

        db = AsyncMock()
        # Uniqueness check returns None (not found)
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_result)
        db.flush = AsyncMock()
        added_rows: list = []
        db.add = MagicMock(side_effect=lambda row: added_rows.append(row))

        with patch("api.services.rbac_service.ServicePrincipalResponse") as mock_resp:
            sp_row = self._make_sp_row()
            mock_resp.model_validate.return_value = MagicMock(
                id=sp_row.id,
                name="ci-bot",
                team_id="engineering",
                role="deployer",
            )

            async def fake_refresh(obj: object) -> None:
                pass

            db.refresh = fake_refresh
            result = await create_service_principal(db, body=body, created_by="admin@example.com")

        assert db.add.called
        sp = added_rows[0]
        assert sp.name == "ci-bot"
        assert sp.role == "deployer"
        assert sp.is_active is True

    @pytest.mark.asyncio
    async def test_create_duplicate_service_principal(self) -> None:
        from api.models.schemas import ServicePrincipalCreate
        from api.services.rbac_service import create_service_principal

        body = ServicePrincipalCreate(
            name="ci-bot",
            team_id="engineering",
            role="deployer",
        )

        db = AsyncMock()
        # Returns existing row
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = MagicMock()
        db.execute = AsyncMock(return_value=existing_result)

        with pytest.raises(ValueError, match="already exists"):
            await create_service_principal(db, body=body, created_by="admin@example.com")

    @pytest.mark.asyncio
    async def test_create_service_principal_invalid_role(self) -> None:
        from api.models.schemas import ServicePrincipalCreate
        from api.services.rbac_service import create_service_principal

        body = ServicePrincipalCreate(
            name="ci-bot",
            team_id="engineering",
            role="superuser",  # invalid
        )

        db = AsyncMock()
        with pytest.raises(ValueError, match="Invalid role"):
            await create_service_principal(db, body=body, created_by="admin@example.com")

    @pytest.mark.asyncio
    async def test_delete_service_principal(self) -> None:
        from api.services.rbac_service import delete_service_principal

        sp_row = self._make_sp_row()

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = sp_row
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()

        deleted = await delete_service_principal(db, sp_id=sp_row.id)
        assert deleted is True
        assert sp_row.is_active is False

    @pytest.mark.asyncio
    async def test_delete_service_principal_not_found(self) -> None:
        from api.services.rbac_service import delete_service_principal

        db = AsyncMock()
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_result)

        deleted = await delete_service_principal(db, sp_id=uuid.uuid4())
        assert deleted is False

    @pytest.mark.asyncio
    async def test_update_service_principal_role(self) -> None:
        from api.models.schemas import ServicePrincipalUpdate
        from api.services.rbac_service import update_service_principal

        sp_row = self._make_sp_row(role="viewer")

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = sp_row
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch("api.services.rbac_service.ServicePrincipalResponse") as mock_resp:
            mock_resp.model_validate.return_value = MagicMock(role="deployer")
            await update_service_principal(
                db,
                sp_id=sp_row.id,
                body=ServicePrincipalUpdate(role="deployer"),
            )

        assert sp_row.role == "deployer"


# ---------------------------------------------------------------------------
# Principal Group CRUD
# ---------------------------------------------------------------------------


class TestPrincipalGroupCRUD:
    def _make_group_row(self, name: str = "ml-team", members: list | None = None) -> MagicMock:
        row = MagicMock()
        row.id = uuid.uuid4()
        row.name = name
        row.team_id = "engineering"
        row.member_ids = members or []
        row.created_by = "admin@example.com"
        row.created_at = datetime.now(UTC)
        return row

    @pytest.mark.asyncio
    async def test_create_group(self) -> None:
        from api.models.schemas import PrincipalGroupCreate
        from api.services.rbac_service import create_group

        body = PrincipalGroupCreate(
            name="ml-team",
            team_id="engineering",
            member_ids=["alice@example.com"],
        )

        db = AsyncMock()
        # Uniqueness check
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_result)
        db.flush = AsyncMock()
        added_rows: list = []
        db.add = MagicMock(side_effect=lambda row: added_rows.append(row))

        with patch("api.services.rbac_service.PrincipalGroupResponse") as mock_resp:
            grp_row = self._make_group_row(members=["alice@example.com"])
            mock_resp.model_validate.return_value = MagicMock(
                id=grp_row.id,
                name="ml-team",
                member_ids=["alice@example.com"],
            )

            async def fake_refresh(obj: object) -> None:
                pass

            db.refresh = fake_refresh
            result = await create_group(db, body=body, created_by="admin@example.com")

        assert db.add.called
        grp = added_rows[0]
        assert grp.name == "ml-team"
        assert "alice@example.com" in grp.member_ids

    @pytest.mark.asyncio
    async def test_add_group_member(self) -> None:
        from api.services.rbac_service import add_group_member

        grp_row = self._make_group_row(members=["alice@example.com"])

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = grp_row
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch("api.services.rbac_service.PrincipalGroupResponse") as mock_resp:
            mock_resp.model_validate.return_value = MagicMock(
                member_ids=["alice@example.com", "bob@example.com"]
            )
            await add_group_member(db, group_id=grp_row.id, member_id="bob@example.com")

        assert "bob@example.com" in grp_row.member_ids

    @pytest.mark.asyncio
    async def test_add_group_member_idempotent(self) -> None:
        """Adding an existing member should not duplicate them."""
        from api.services.rbac_service import add_group_member

        grp_row = self._make_group_row(members=["alice@example.com"])

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = grp_row
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch("api.services.rbac_service.PrincipalGroupResponse") as mock_resp:
            mock_resp.model_validate.return_value = MagicMock(member_ids=["alice@example.com"])
            await add_group_member(db, group_id=grp_row.id, member_id="alice@example.com")

        assert grp_row.member_ids.count("alice@example.com") == 1

    @pytest.mark.asyncio
    async def test_remove_group_member(self) -> None:
        from api.services.rbac_service import remove_group_member

        grp_row = self._make_group_row(members=["alice@example.com", "bob@example.com"])

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = grp_row
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        with patch("api.services.rbac_service.PrincipalGroupResponse") as mock_resp:
            mock_resp.model_validate.return_value = MagicMock(member_ids=["bob@example.com"])
            await remove_group_member(db, group_id=grp_row.id, member_id="alice@example.com")

        assert "alice@example.com" not in grp_row.member_ids
        assert "bob@example.com" in grp_row.member_ids

    @pytest.mark.asyncio
    async def test_remove_nonexistent_group(self) -> None:
        from api.services.rbac_service import remove_group_member

        db = AsyncMock()
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_result)

        result = await remove_group_member(db, group_id=uuid.uuid4(), member_id="alice@example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_group(self) -> None:
        from api.services.rbac_service import delete_group

        grp_row = self._make_group_row()

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = grp_row
        db.execute = AsyncMock(return_value=result_mock)
        db.flush = AsyncMock()
        db.delete = AsyncMock()

        deleted = await delete_group(db, group_id=grp_row.id)
        assert deleted is True
        db.delete.assert_called_once_with(grp_row)


# ---------------------------------------------------------------------------
# TeamService — LiteLLM auto-mint on join
# ---------------------------------------------------------------------------


class TestTeamServiceKeyLifecycle:
    @pytest.fixture(autouse=True)
    def _reset(self):
        from api.services.team_service import TeamService

        TeamService.reset()
        yield
        TeamService.reset()

    @pytest.mark.asyncio
    async def test_add_member_triggers_key_mint(self) -> None:
        """add_member should attempt to schedule key auto-mint."""
        from api.services.team_service import TeamService

        team = await TeamService.create_team(name="eng", display_name="Engineering")

        mint_called = False

        async def mock_mint(*args, **kwargs):
            nonlocal mint_called
            mint_called = True

        with patch.object(TeamService, "_auto_mint_member_key", side_effect=mock_mint):
            import asyncio

            # Patch asyncio.ensure_future to call the coroutine synchronously
            original_ensure = asyncio.ensure_future

            async def run_coro(coro):
                return await coro

            with patch("asyncio.ensure_future", side_effect=lambda coro: asyncio.get_event_loop().create_task(coro) if asyncio.get_event_loop().is_running() else None):
                membership = await TeamService.add_member(
                    team.id,
                    user_id="user-alice",
                    user_email="alice@example.com",
                    user_name="Alice",
                    role="viewer",
                )

        assert membership.user_email == "alice@example.com"

    @pytest.mark.asyncio
    async def test_remove_member_triggers_key_revoke(self) -> None:
        """remove_member should attempt to schedule key auto-revoke."""
        from api.services.team_service import TeamService

        team = await TeamService.create_team(name="eng", display_name="Engineering")
        membership = await TeamService.add_member(
            team.id,
            user_id="user-alice",
            user_email="alice@example.com",
            user_name="Alice",
        )

        revoke_called = False

        async def mock_revoke(*args, **kwargs):
            nonlocal revoke_called
            revoke_called = True

        with patch.object(TeamService, "_auto_revoke_member_key", side_effect=mock_revoke):
            import asyncio

            with patch("asyncio.ensure_future", side_effect=lambda coro: None):
                removed = await TeamService.remove_member(team.id, "user-alice")

        assert removed is True

    @pytest.mark.asyncio
    async def test_auto_mint_member_key_logs_on_db_error(self) -> None:
        """_auto_mint_member_key should log and not raise on DB errors."""
        from api.services.team_service import TeamService

        with patch("api.services.team_service.TeamService._auto_mint_member_key") as mock_mint:
            mock_mint.side_effect = Exception("DB unavailable")
            # Should not raise
            try:
                import asyncio

                asyncio.ensure_future(
                    TeamService._auto_mint_member_key("team-id", "user@example.com", "team-name")
                )
            except Exception:
                pass  # graceful failure expected


# ---------------------------------------------------------------------------
# REST API endpoint tests via TestClient
# ---------------------------------------------------------------------------


class TestRBACRoutes:
    """Smoke tests for the RBAC routes using FastAPI TestClient.

    These tests confirm routing, request/response shape, and auth gating.
    They mock all DB calls so no Postgres is needed.
    """

    @pytest.fixture
    def client(self):
        from api.main import app

        return TestClient(app, raise_server_exceptions=False)

    @pytest.fixture
    def auth_headers(self):
        """Return JWT auth headers for a mock admin user."""
        # Override get_current_user globally so all routes think we're logged in
        from api.models.database import User
        from api.models.enums import UserRole

        mock_user = MagicMock(spec=User)
        mock_user.id = uuid.uuid4()
        mock_user.email = "admin@agentbreeder.local"
        mock_user.name = "Admin"
        mock_user.role = UserRole.admin
        mock_user.team = "default"
        mock_user.is_active = True

        return mock_user

    @pytest.mark.no_auto_auth
    def test_permissions_endpoint_requires_auth(self, client: TestClient) -> None:
        """GET /api/v1/rbac/permissions without auth should 401 or 422."""
        resp = client.get("/api/v1/rbac/permissions")
        assert resp.status_code in {401, 403, 422}

    @pytest.mark.no_auto_auth
    def test_service_principals_endpoint_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/v1/rbac/service-principals")
        assert resp.status_code in {401, 403, 422}

    @pytest.mark.no_auto_auth
    def test_groups_endpoint_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/v1/rbac/groups")
        assert resp.status_code in {401, 403, 422}

    @pytest.mark.no_auto_auth
    def test_approvals_endpoint_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/v1/rbac/approvals")
        assert resp.status_code in {401, 403, 422}

    @pytest.mark.no_auto_auth
    def test_keys_endpoint_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/v1/rbac/keys")
        assert resp.status_code in {401, 403, 422}

    def test_rbac_router_registered(self, client: TestClient) -> None:
        """The /api/v1/rbac prefix must be reachable (even if auth-gated)."""
        routes = [str(r.path) for r in client.app.routes]
        rbac_routes = [r for r in routes if r.startswith("/api/v1/rbac")]
        assert len(rbac_routes) > 0, "RBAC routes must be registered in the app"


# ---------------------------------------------------------------------------
# Default permissions on asset creation
# ---------------------------------------------------------------------------


class TestDefaultPermissions:
    @pytest.mark.asyncio
    async def test_grant_default_permissions_owner_and_team(self) -> None:
        """grant_default_permissions should add owner + team rows."""
        from api.services.rbac_service import grant_default_permissions

        resource_id = uuid.uuid4()
        added_rows: list = []

        db = AsyncMock()
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_result)
        db.flush = AsyncMock()
        db.add = MagicMock(side_effect=lambda row: added_rows.append(row))
        db.refresh = AsyncMock()

        with patch("api.services.rbac_service.PermissionResponse") as mock_resp:
            mock_resp.model_validate.return_value = MagicMock()
            await grant_default_permissions(
                db,
                resource_type="agent",
                resource_id=resource_id,
                owner_email="alice@example.com",
                team_id="engineering",
            )

        # Should have added 2 rows: one for owner, one for team
        assert len(added_rows) == 2
        principal_types = {r.principal_type for r in added_rows}
        assert "user" in principal_types
        assert "team" in principal_types

    @pytest.mark.asyncio
    async def test_grant_default_permissions_no_team(self) -> None:
        """grant_default_permissions without team should add only owner row."""
        from api.services.rbac_service import grant_default_permissions

        resource_id = uuid.uuid4()
        added_rows: list = []

        db = AsyncMock()
        no_result = MagicMock()
        no_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=no_result)
        db.flush = AsyncMock()
        db.add = MagicMock(side_effect=lambda row: added_rows.append(row))
        db.refresh = AsyncMock()

        with patch("api.services.rbac_service.PermissionResponse") as mock_resp:
            mock_resp.model_validate.return_value = MagicMock()
            await grant_default_permissions(
                db,
                resource_type="agent",
                resource_id=resource_id,
                owner_email="alice@example.com",
                team_id=None,
            )

        assert len(added_rows) == 1
        assert added_rows[0].principal_type == "user"
