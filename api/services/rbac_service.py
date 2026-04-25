"""RBAC service — asset ACL, approval queue, service principals, principal groups.

All write operations go through this service — never write to RBAC tables directly.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import (
    AssetApprovalRequest,
    PrincipalGroup,
    ResourcePermission,
    ServicePrincipal,
)
from api.models.schemas import (
    VALID_ACTIONS,
    VALID_PRINCIPAL_TYPES,
    VALID_RESOURCE_TYPES,
    VALID_SP_ROLES,
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalResponse,
    PermissionGrant,
    PermissionResponse,
    PrincipalGroupCreate,
    PrincipalGroupResponse,
    ServicePrincipalCreate,
    ServicePrincipalResponse,
    ServicePrincipalUpdate,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Permission checking
# ---------------------------------------------------------------------------

# Default actions granted to the resource owner
_OWNER_ACTIONS = ["read", "use", "write", "deploy", "publish", "admin"]
# Default actions granted to the owner's team members
_TEAM_ACTIONS = ["read", "use"]


async def check_permission(
    db: AsyncSession,
    user_email: str,
    resource_type: str,
    resource_id: uuid.UUID,
    action: str,
) -> tuple[bool, str]:
    """Check if a user/principal can perform an action on a resource.

    Checks in order:
    1. Direct user permission
    2. Group membership (user is in a group that has permission)
    3. Team membership (user belongs to a team that has permission)

    Returns (allowed, reason).
    """
    if action not in VALID_ACTIONS:
        return False, f"Unknown action: {action}"

    # Fetch all permission rows for this resource
    stmt = select(ResourcePermission).where(
        ResourcePermission.resource_type == resource_type,
        ResourcePermission.resource_id == resource_id,
    )
    result = await db.execute(stmt)
    perms = result.scalars().all()

    # 1. Direct user permission
    for p in perms:
        if p.principal_type == "user" and p.principal_id == user_email:
            if action in p.actions:
                return True, "Direct user permission"

    # 2. Group membership — fetch all groups this user belongs to
    grp_stmt = select(PrincipalGroup)
    grp_result = await db.execute(grp_stmt)
    all_groups = grp_result.scalars().all()
    user_group_ids = {str(g.id) for g in all_groups if user_email in (g.member_ids or [])}

    for p in perms:
        if p.principal_type == "group" and p.principal_id in user_group_ids:
            if action in p.actions:
                return True, f"Group permission via group {p.principal_id}"

    # 3. Team-based permission — check if user is a member of a team that has permission
    # We use TeamService (in-memory) to find the user's teams
    try:
        from api.services.team_service import TeamService

        # user_id in TeamService is the SHA256 of email — look up by matching user_email
        team_names_for_user: set[str] = set()
        for m in TeamService._memberships.values():
            if m.user_email == user_email:
                team = TeamService._teams.get(m.team_id)
                if team:
                    team_names_for_user.add(team.name)
                    team_names_for_user.add(team.id)  # also match by id

        for p in perms:
            if p.principal_type == "team" and p.principal_id in team_names_for_user:
                if action in p.actions:
                    return True, f"Team permission via team {p.principal_id}"
    except Exception:
        logger.debug("TeamService unavailable during permission check", exc_info=True)

    return False, f"No permission for action '{action}' on {resource_type}/{resource_id}"


async def grant_permission(
    db: AsyncSession,
    granter: str,
    body: PermissionGrant,
) -> PermissionResponse:
    """Grant permissions to a principal on a resource."""
    # Validate inputs
    if body.resource_type not in VALID_RESOURCE_TYPES:
        raise ValueError(f"Invalid resource_type: {body.resource_type}")
    if body.principal_type not in VALID_PRINCIPAL_TYPES:
        raise ValueError(f"Invalid principal_type: {body.principal_type}")
    invalid_actions = set(body.actions) - VALID_ACTIONS
    if invalid_actions:
        raise ValueError(f"Invalid actions: {invalid_actions}")
    if not body.actions:
        raise ValueError("actions must not be empty")

    # Check if a row already exists for this principal+resource — upsert actions
    stmt = select(ResourcePermission).where(
        ResourcePermission.resource_type == body.resource_type,
        ResourcePermission.resource_id == body.resource_id,
        ResourcePermission.principal_type == body.principal_type,
        ResourcePermission.principal_id == body.principal_id,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        # Merge actions
        merged = list(set(existing.actions) | set(body.actions))
        existing.actions = merged
        await db.flush()
        await db.refresh(existing)
        return PermissionResponse.model_validate(existing)

    perm = ResourcePermission(
        id=uuid.uuid4(),
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        principal_type=body.principal_type,
        principal_id=body.principal_id,
        actions=list(body.actions),
        created_by=granter,
    )
    db.add(perm)
    await db.flush()
    await db.refresh(perm)
    logger.info(
        "Permission granted: %s %s on %s/%s by %s",
        body.principal_type,
        body.principal_id,
        body.resource_type,
        body.resource_id,
        granter,
    )
    return PermissionResponse.model_validate(perm)


async def grant_default_permissions(
    db: AsyncSession,
    resource_type: str,
    resource_id: uuid.UUID,
    owner_email: str,
    team_id: str | None = None,
) -> None:
    """Grant default permissions after asset creation.

    Owner → all actions. Owner's team → read + use.
    """
    # Owner gets everything
    await grant_permission(
        db,
        granter=owner_email,
        body=PermissionGrant(
            resource_type=resource_type,
            resource_id=resource_id,
            principal_type="user",
            principal_id=owner_email,
            actions=_OWNER_ACTIONS,
        ),
    )
    # Team gets read + use
    if team_id:
        await grant_permission(
            db,
            granter=owner_email,
            body=PermissionGrant(
                resource_type=resource_type,
                resource_id=resource_id,
                principal_type="team",
                principal_id=team_id,
                actions=_TEAM_ACTIONS,
            ),
        )


async def revoke_permission(
    db: AsyncSession,
    revoker: str,
    permission_id: uuid.UUID,
) -> bool:
    """Delete a ResourcePermission row by ID."""
    result = await db.execute(
        select(ResourcePermission).where(ResourcePermission.id == permission_id)
    )
    perm = result.scalar_one_or_none()
    if not perm:
        return False
    await db.delete(perm)
    await db.flush()
    logger.info("Permission %s revoked by %s", permission_id, revoker)
    return True


async def list_permissions(
    db: AsyncSession,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
) -> list[PermissionResponse]:
    stmt = select(ResourcePermission)
    if resource_type:
        stmt = stmt.where(ResourcePermission.resource_type == resource_type)
    if resource_id:
        stmt = stmt.where(ResourcePermission.resource_id == resource_id)
    stmt = stmt.order_by(ResourcePermission.created_at.desc())
    result = await db.execute(stmt)
    return [PermissionResponse.model_validate(r) for r in result.scalars().all()]


# ---------------------------------------------------------------------------
# Approval queue
# ---------------------------------------------------------------------------


async def submit_approval(
    db: AsyncSession,
    submitter: str,
    body: ApprovalRequestCreate,
) -> ApprovalResponse:
    """Submit an asset for admin approval."""
    req = AssetApprovalRequest(
        id=uuid.uuid4(),
        asset_type=body.asset_type,
        asset_id=body.asset_id,
        asset_version=body.asset_version,
        submitter_id=submitter,
        status="pending",
        message=body.message,
    )
    db.add(req)
    await db.flush()
    await db.refresh(req)
    logger.info("Approval request %s submitted by %s for %s/%s", req.id, submitter, body.asset_type, body.asset_id)
    return ApprovalResponse.model_validate(req)


async def list_approvals(
    db: AsyncSession,
    status: str | None = None,
    asset_type: str | None = None,
    submitter_id: str | None = None,
) -> list[ApprovalResponse]:
    stmt = select(AssetApprovalRequest)
    if status:
        stmt = stmt.where(AssetApprovalRequest.status == status)
    if asset_type:
        stmt = stmt.where(AssetApprovalRequest.asset_type == asset_type)
    if submitter_id:
        stmt = stmt.where(AssetApprovalRequest.submitter_id == submitter_id)
    stmt = stmt.order_by(AssetApprovalRequest.created_at.desc())
    result = await db.execute(stmt)
    return [ApprovalResponse.model_validate(r) for r in result.scalars().all()]


async def get_approval(db: AsyncSession, approval_id: uuid.UUID) -> ApprovalResponse | None:
    result = await db.execute(
        select(AssetApprovalRequest).where(AssetApprovalRequest.id == approval_id)
    )
    row = result.scalar_one_or_none()
    return ApprovalResponse.model_validate(row) if row else None


async def _decide_approval(
    db: AsyncSession,
    approval_id: uuid.UUID,
    approver: str,
    new_status: str,
    decision: ApprovalDecision,
) -> ApprovalResponse:
    result = await db.execute(
        select(AssetApprovalRequest).where(AssetApprovalRequest.id == approval_id)
    )
    req = result.scalar_one_or_none()
    if not req:
        raise ValueError(f"Approval request {approval_id} not found")
    if req.status != "pending":
        raise ValueError(f"Approval is already '{req.status}'")

    req.status = new_status
    req.approver_id = approver
    req.reason = decision.reason
    req.decided_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(req)
    logger.info("Approval %s %s by %s", approval_id, new_status, approver)
    return ApprovalResponse.model_validate(req)


async def approve_request(
    db: AsyncSession,
    approval_id: uuid.UUID,
    approver: str,
    decision: ApprovalDecision,
) -> ApprovalResponse:
    return await _decide_approval(db, approval_id, approver, "approved", decision)


async def reject_request(
    db: AsyncSession,
    approval_id: uuid.UUID,
    approver: str,
    decision: ApprovalDecision,
) -> ApprovalResponse:
    return await _decide_approval(db, approval_id, approver, "rejected", decision)


# ---------------------------------------------------------------------------
# Service Principals
# ---------------------------------------------------------------------------


async def create_service_principal(
    db: AsyncSession,
    body: ServicePrincipalCreate,
    created_by: str,
) -> ServicePrincipalResponse:
    if body.role not in VALID_SP_ROLES:
        raise ValueError(f"Invalid role: {body.role}. Must be one of {VALID_SP_ROLES}")

    # Uniqueness check
    existing = await db.execute(
        select(ServicePrincipal).where(ServicePrincipal.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"ServicePrincipal '{body.name}' already exists")

    sp = ServicePrincipal(
        id=uuid.uuid4(),
        name=body.name,
        team_id=body.team_id,
        role=body.role,
        allowed_assets=body.allowed_assets,
        created_by=created_by,
        is_active=True,
    )
    db.add(sp)
    await db.flush()
    await db.refresh(sp)
    logger.info("ServicePrincipal %s created by %s", sp.name, created_by)
    return ServicePrincipalResponse.model_validate(sp)


async def list_service_principals(
    db: AsyncSession,
    team_id: str | None = None,
    active_only: bool = True,
) -> list[ServicePrincipalResponse]:
    stmt = select(ServicePrincipal)
    if team_id:
        stmt = stmt.where(ServicePrincipal.team_id == team_id)
    if active_only:
        stmt = stmt.where(ServicePrincipal.is_active.is_(True))
    stmt = stmt.order_by(ServicePrincipal.created_at.desc())
    result = await db.execute(stmt)
    return [ServicePrincipalResponse.model_validate(r) for r in result.scalars().all()]


async def get_service_principal(
    db: AsyncSession,
    sp_id: uuid.UUID,
) -> ServicePrincipalResponse | None:
    result = await db.execute(
        select(ServicePrincipal).where(ServicePrincipal.id == sp_id)
    )
    row = result.scalar_one_or_none()
    return ServicePrincipalResponse.model_validate(row) if row else None


async def update_service_principal(
    db: AsyncSession,
    sp_id: uuid.UUID,
    body: ServicePrincipalUpdate,
) -> ServicePrincipalResponse | None:
    result = await db.execute(
        select(ServicePrincipal).where(ServicePrincipal.id == sp_id)
    )
    sp = result.scalar_one_or_none()
    if not sp:
        return None

    if body.role is not None:
        if body.role not in VALID_SP_ROLES:
            raise ValueError(f"Invalid role: {body.role}")
        sp.role = body.role
    if body.allowed_assets is not None:
        sp.allowed_assets = body.allowed_assets
    if body.is_active is not None:
        sp.is_active = body.is_active

    await db.flush()
    await db.refresh(sp)
    return ServicePrincipalResponse.model_validate(sp)


async def delete_service_principal(db: AsyncSession, sp_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(ServicePrincipal).where(ServicePrincipal.id == sp_id)
    )
    sp = result.scalar_one_or_none()
    if not sp:
        return False
    sp.is_active = False
    await db.flush()
    logger.info("ServicePrincipal %s deactivated", sp_id)
    return True


async def rotate_service_principal_key(
    db: AsyncSession,
    sp_id: uuid.UUID,
    created_by: str,
) -> dict:
    """Issue a new LiteLLM virtual key for a service principal.

    Revokes any existing active key for this SP, then mints a fresh one.
    Returns dict with key_alias and key_value.
    """
    result = await db.execute(
        select(ServicePrincipal).where(ServicePrincipal.id == sp_id)
    )
    sp = result.scalar_one_or_none()
    if not sp:
        raise ValueError(f"ServicePrincipal {sp_id} not found")
    if not sp.is_active:
        raise ValueError("ServicePrincipal is deactivated")

    # Update last_used_at
    sp.last_used_at = datetime.now(UTC)
    await db.flush()

    # Use LiteLLMKeyService to mint a key scoped to this service principal
    try:
        from api.models.enums import KeyScopeType
        from api.models.schemas import LiteLLMKeyCreate
        from api.services.litellm_key_service import create_key, revoke_key

        alias = f"sp-{sp.name}-{uuid.uuid4().hex[:8]}"

        # Revoke any existing active SP keys (best-effort)
        from api.models.database import LiteLLMKeyRef
        from sqlalchemy import and_

        old_result = await db.execute(
            select(LiteLLMKeyRef).where(
                and_(
                    LiteLLMKeyRef.scope_type == KeyScopeType.service_principal,
                    LiteLLMKeyRef.scope_id == str(sp_id),
                    LiteLLMKeyRef.is_active.is_(True),
                )
            )
        )
        for old_key in old_result.scalars().all():
            await revoke_key(db, old_key.key_alias)

        body = LiteLLMKeyCreate(
            key_alias=alias,
            scope_type=KeyScopeType.service_principal,
            scope_id=str(sp_id),
            team_id=sp.team_id,
            tags=["service-principal", f"sp:{sp.name}", f"team:{sp.team_id}"],
        )
        created = await create_key(db, body, created_by=created_by)
        return {
            "service_principal_id": sp_id,
            "key_alias": created.key_alias,
            "key_value": created.key_value,
        }
    except Exception as exc:
        # LiteLLM unavailable — return a local placeholder key
        logger.warning("LiteLLM unavailable for SP key rotation: %s", exc)
        placeholder_key = f"sk-sp-{uuid.uuid4().hex}"
        return {
            "service_principal_id": sp_id,
            "key_alias": f"sp-{sp.name}-local",
            "key_value": placeholder_key,
        }


# ---------------------------------------------------------------------------
# Principal Groups
# ---------------------------------------------------------------------------


async def create_group(
    db: AsyncSession,
    body: PrincipalGroupCreate,
    created_by: str,
) -> PrincipalGroupResponse:
    # Uniqueness check within team
    existing = await db.execute(
        select(PrincipalGroup).where(
            and_(
                PrincipalGroup.team_id == body.team_id,
                PrincipalGroup.name == body.name,
            )
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"Group '{body.name}' already exists in team '{body.team_id}'")

    grp = PrincipalGroup(
        id=uuid.uuid4(),
        name=body.name,
        team_id=body.team_id,
        member_ids=list(body.member_ids),
        created_by=created_by,
    )
    db.add(grp)
    await db.flush()
    await db.refresh(grp)
    return PrincipalGroupResponse.model_validate(grp)


async def list_groups(
    db: AsyncSession,
    team_id: str | None = None,
) -> list[PrincipalGroupResponse]:
    stmt = select(PrincipalGroup)
    if team_id:
        stmt = stmt.where(PrincipalGroup.team_id == team_id)
    stmt = stmt.order_by(PrincipalGroup.name)
    result = await db.execute(stmt)
    return [PrincipalGroupResponse.model_validate(r) for r in result.scalars().all()]


async def get_group(
    db: AsyncSession,
    group_id: uuid.UUID,
) -> PrincipalGroupResponse | None:
    result = await db.execute(
        select(PrincipalGroup).where(PrincipalGroup.id == group_id)
    )
    row = result.scalar_one_or_none()
    return PrincipalGroupResponse.model_validate(row) if row else None


async def update_group(
    db: AsyncSession,
    group_id: uuid.UUID,
    name: str,
) -> PrincipalGroupResponse | None:
    result = await db.execute(
        select(PrincipalGroup).where(PrincipalGroup.id == group_id)
    )
    grp = result.scalar_one_or_none()
    if not grp:
        return None
    grp.name = name
    await db.flush()
    await db.refresh(grp)
    return PrincipalGroupResponse.model_validate(grp)


async def delete_group(db: AsyncSession, group_id: uuid.UUID) -> bool:
    result = await db.execute(
        select(PrincipalGroup).where(PrincipalGroup.id == group_id)
    )
    grp = result.scalar_one_or_none()
    if not grp:
        return False
    await db.delete(grp)
    await db.flush()
    return True


async def add_group_member(
    db: AsyncSession,
    group_id: uuid.UUID,
    member_id: str,
) -> PrincipalGroupResponse | None:
    result = await db.execute(
        select(PrincipalGroup).where(PrincipalGroup.id == group_id)
    )
    grp = result.scalar_one_or_none()
    if not grp:
        return None
    members = list(grp.member_ids or [])
    if member_id not in members:
        members.append(member_id)
        grp.member_ids = members
        await db.flush()
        await db.refresh(grp)
    return PrincipalGroupResponse.model_validate(grp)


async def remove_group_member(
    db: AsyncSession,
    group_id: uuid.UUID,
    member_id: str,
) -> PrincipalGroupResponse | None:
    result = await db.execute(
        select(PrincipalGroup).where(PrincipalGroup.id == group_id)
    )
    grp = result.scalar_one_or_none()
    if not grp:
        return None
    members = [m for m in (grp.member_ids or []) if m != member_id]
    grp.member_ids = members
    await db.flush()
    await db.refresh(grp)
    return PrincipalGroupResponse.model_validate(grp)
