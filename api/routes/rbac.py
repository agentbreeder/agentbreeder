"""RBAC management API — permissions, approvals, service principals, groups, LiteLLM keys.

All RBAC functionality — roles, permissions, approvals, service principals, groups, and
LiteLLM key lifecycle — is fully manageable via these REST endpoints.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.database import get_db
from api.models.database import User
from api.models.schemas import (
    ApiMeta,
    ApiResponse,
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalResponse,
    GroupMemberAdd,
    LiteLLMKeyCreate,
    LiteLLMKeyCreateResponse,
    LiteLLMKeyResponse,
    PermissionCheckResponse,
    PermissionGrant,
    PermissionResponse,
    PrincipalGroupCreate,
    PrincipalGroupResponse,
    PrincipalGroupUpdate,
    ServicePrincipalCreate,
    ServicePrincipalKeyResponse,
    ServicePrincipalResponse,
    ServicePrincipalUpdate,
)
from api.services import rbac_service

router = APIRouter(prefix="/api/v1/rbac", tags=["rbac"])


# ===========================================================================
# Permissions
# ===========================================================================


@router.get("/permissions", response_model=ApiResponse[list[PermissionResponse]])
async def list_permissions(
    resource_type: str | None = Query(None),
    resource_id: uuid.UUID | None = Query(None),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[PermissionResponse]]:
    """List resource permissions, optionally filtered by resource_type and resource_id."""
    perms = await rbac_service.list_permissions(
        db, resource_type=resource_type, resource_id=resource_id
    )
    return ApiResponse(data=perms, meta=ApiMeta(total=len(perms)))


@router.post("/permissions", response_model=ApiResponse[PermissionResponse], status_code=201)
async def grant_permission(
    body: PermissionGrant,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PermissionResponse]:
    """Grant a permission to a principal on a resource."""
    try:
        perm = await rbac_service.grant_permission(db, granter=user.email, body=body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    return ApiResponse(data=perm)


@router.delete("/permissions/{permission_id}", response_model=ApiResponse[dict])
async def revoke_permission(
    permission_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Revoke a permission by ID."""
    deleted = await rbac_service.revoke_permission(db, revoker=user.email, permission_id=permission_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Permission not found")
    await db.commit()
    return ApiResponse(data={"revoked": True})


@router.get("/permissions/check", response_model=ApiResponse[PermissionCheckResponse])
async def check_permission(
    resource_type: str = Query(...),
    resource_id: uuid.UUID = Query(...),
    action: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PermissionCheckResponse]:
    """Check if the current user can perform an action on a resource."""
    allowed, reason = await rbac_service.check_permission(
        db,
        user_email=user.email,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
    )
    return ApiResponse(data=PermissionCheckResponse(allowed=allowed, reason=reason))


# ===========================================================================
# Approvals
# ===========================================================================


@router.get("/approvals", response_model=ApiResponse[list[ApprovalResponse]])
async def list_approvals(
    status: str | None = Query(None, description="pending | approved | rejected"),
    asset_type: str | None = Query(None),
    submitter: str | None = Query(None),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ApprovalResponse]]:
    """List asset approval requests."""
    items = await rbac_service.list_approvals(
        db, status=status, asset_type=asset_type, submitter_id=submitter
    )
    return ApiResponse(data=items, meta=ApiMeta(total=len(items)))


@router.post("/approvals", response_model=ApiResponse[ApprovalResponse], status_code=201)
async def submit_approval(
    body: ApprovalRequestCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ApprovalResponse]:
    """Submit an asset for admin approval."""
    req = await rbac_service.submit_approval(db, submitter=user.email, body=body)
    await db.commit()
    return ApiResponse(data=req)


@router.get("/approvals/{approval_id}", response_model=ApiResponse[ApprovalResponse])
async def get_approval(
    approval_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ApprovalResponse]:
    """Get an approval request by ID."""
    req = await rbac_service.get_approval(db, approval_id)
    if not req:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return ApiResponse(data=req)


@router.post("/approvals/{approval_id}/approve", response_model=ApiResponse[ApprovalResponse])
async def approve_request(
    approval_id: uuid.UUID,
    body: ApprovalDecision = ApprovalDecision(),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ApprovalResponse]:
    """Approve a pending asset request (admin only)."""
    if str(user.role) not in {"admin"}:
        raise HTTPException(status_code=403, detail="Only admins can approve requests")
    try:
        req = await rbac_service.approve_request(db, approval_id, approver=user.email, decision=body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return ApiResponse(data=req)


@router.post("/approvals/{approval_id}/reject", response_model=ApiResponse[ApprovalResponse])
async def reject_request(
    approval_id: uuid.UUID,
    body: ApprovalDecision = ApprovalDecision(),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ApprovalResponse]:
    """Reject a pending asset request (admin only)."""
    if str(user.role) not in {"admin"}:
        raise HTTPException(status_code=403, detail="Only admins can reject requests")
    try:
        req = await rbac_service.reject_request(db, approval_id, approver=user.email, decision=body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return ApiResponse(data=req)


# ===========================================================================
# Service Principals
# ===========================================================================


@router.get("/service-principals", response_model=ApiResponse[list[ServicePrincipalResponse]])
async def list_service_principals(
    team_id: str | None = Query(None),
    active_only: bool = Query(True),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[ServicePrincipalResponse]]:
    """List service principals."""
    items = await rbac_service.list_service_principals(
        db, team_id=team_id, active_only=active_only
    )
    return ApiResponse(data=items, meta=ApiMeta(total=len(items)))


@router.post(
    "/service-principals",
    response_model=ApiResponse[ServicePrincipalResponse],
    status_code=201,
)
async def create_service_principal(
    body: ServicePrincipalCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ServicePrincipalResponse]:
    """Create a service principal and auto-mint its initial LiteLLM key."""
    try:
        sp = await rbac_service.create_service_principal(db, body=body, created_by=user.email)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()

    # Auto-mint key (best-effort — SP is created even if key mint fails)
    try:
        await rbac_service.rotate_service_principal_key(db, sp_id=sp.id, created_by=user.email)
        await db.commit()
    except Exception:
        pass  # key mint is non-blocking

    return ApiResponse(data=sp)


@router.get(
    "/service-principals/{sp_id}",
    response_model=ApiResponse[ServicePrincipalResponse],
)
async def get_service_principal(
    sp_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ServicePrincipalResponse]:
    """Get a service principal by ID."""
    sp = await rbac_service.get_service_principal(db, sp_id)
    if not sp:
        raise HTTPException(status_code=404, detail="ServicePrincipal not found")
    return ApiResponse(data=sp)


@router.put(
    "/service-principals/{sp_id}",
    response_model=ApiResponse[ServicePrincipalResponse],
)
async def update_service_principal(
    sp_id: uuid.UUID,
    body: ServicePrincipalUpdate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ServicePrincipalResponse]:
    """Update a service principal's role, asset allowlist, or active status."""
    try:
        sp = await rbac_service.update_service_principal(db, sp_id=sp_id, body=body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not sp:
        raise HTTPException(status_code=404, detail="ServicePrincipal not found")
    await db.commit()
    return ApiResponse(data=sp)


@router.delete("/service-principals/{sp_id}", response_model=ApiResponse[dict])
async def delete_service_principal(
    sp_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Soft-delete (deactivate) a service principal."""
    deleted = await rbac_service.delete_service_principal(db, sp_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="ServicePrincipal not found")
    await db.commit()
    return ApiResponse(data={"deleted": True})


@router.post(
    "/service-principals/{sp_id}/rotate-key",
    response_model=ApiResponse[ServicePrincipalKeyResponse],
)
async def rotate_service_principal_key(
    sp_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ServicePrincipalKeyResponse]:
    """Issue a new API key for a service principal (revokes the old one)."""
    try:
        result = await rbac_service.rotate_service_principal_key(
            db, sp_id=sp_id, created_by=user.email
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    return ApiResponse(
        data=ServicePrincipalKeyResponse(
            service_principal_id=result["service_principal_id"],
            key_alias=result["key_alias"],
            key_value=result["key_value"],
        )
    )


# ===========================================================================
# Principal Groups
# ===========================================================================


@router.get("/groups", response_model=ApiResponse[list[PrincipalGroupResponse]])
async def list_groups(
    team_id: str | None = Query(None),
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[PrincipalGroupResponse]]:
    """List principal groups."""
    items = await rbac_service.list_groups(db, team_id=team_id)
    return ApiResponse(data=items, meta=ApiMeta(total=len(items)))


@router.post("/groups", response_model=ApiResponse[PrincipalGroupResponse], status_code=201)
async def create_group(
    body: PrincipalGroupCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PrincipalGroupResponse]:
    """Create a principal group."""
    try:
        grp = await rbac_service.create_group(db, body=body, created_by=user.email)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return ApiResponse(data=grp)


@router.get("/groups/{group_id}", response_model=ApiResponse[PrincipalGroupResponse])
async def get_group(
    group_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PrincipalGroupResponse]:
    """Get a group by ID."""
    grp = await rbac_service.get_group(db, group_id)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    return ApiResponse(data=grp)


@router.put("/groups/{group_id}", response_model=ApiResponse[PrincipalGroupResponse])
async def update_group(
    group_id: uuid.UUID,
    body: PrincipalGroupUpdate,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PrincipalGroupResponse]:
    """Update a group's name."""
    if not body.name:
        raise HTTPException(status_code=422, detail="name is required")
    grp = await rbac_service.update_group(db, group_id=group_id, name=body.name)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.commit()
    return ApiResponse(data=grp)


@router.delete("/groups/{group_id}", response_model=ApiResponse[dict])
async def delete_group(
    group_id: uuid.UUID,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Delete a group."""
    deleted = await rbac_service.delete_group(db, group_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.commit()
    return ApiResponse(data={"deleted": True})


@router.post(
    "/groups/{group_id}/members",
    response_model=ApiResponse[PrincipalGroupResponse],
    status_code=201,
)
async def add_group_member(
    group_id: uuid.UUID,
    body: GroupMemberAdd,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PrincipalGroupResponse]:
    """Add a member to a group."""
    grp = await rbac_service.add_group_member(db, group_id=group_id, member_id=body.member_id)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.commit()
    return ApiResponse(data=grp)


@router.delete(
    "/groups/{group_id}/members/{member_id}",
    response_model=ApiResponse[PrincipalGroupResponse],
)
async def remove_group_member(
    group_id: uuid.UUID,
    member_id: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[PrincipalGroupResponse]:
    """Remove a member from a group."""
    grp = await rbac_service.remove_group_member(db, group_id=group_id, member_id=member_id)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    await db.commit()
    return ApiResponse(data=grp)


# ===========================================================================
# LiteLLM Key Management
# ===========================================================================


@router.get("/keys", response_model=ApiResponse[list[LiteLLMKeyResponse]])
async def list_keys(
    team_id: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[LiteLLMKeyResponse]]:
    """List LiteLLM virtual keys for the current user's scope or a specific team."""
    from api.services.litellm_key_service import list_keys as svc_list

    # Admins can see all keys; non-admins see only their own team's keys
    effective_team = team_id
    if str(user.role) != "admin" and not team_id:
        effective_team = user.team

    keys = await svc_list(db, team_id=effective_team)
    return ApiResponse(data=keys, meta=ApiMeta(total=len(keys)))


@router.post("/keys", response_model=ApiResponse[LiteLLMKeyCreateResponse], status_code=201)
async def mint_key(
    body: LiteLLMKeyCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[LiteLLMKeyCreateResponse]:
    """Mint a new LiteLLM virtual key."""
    from api.services.litellm_key_service import create_key

    try:
        created = await create_key(db, body, created_by=user.email)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LiteLLM key generation failed: {exc}") from exc
    return ApiResponse(data=created)


@router.delete("/keys/{key_alias}", response_model=ApiResponse[dict])
async def revoke_key(
    key_alias: str,
    _user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """Revoke a LiteLLM virtual key by alias."""
    from api.services.litellm_key_service import revoke_key as svc_revoke

    revoked = await svc_revoke(db, key_alias)
    if not revoked:
        raise HTTPException(status_code=404, detail="Key not found")
    await db.commit()
    return ApiResponse(data={"revoked": True})


@router.get("/keys/{key_alias}/reveal", response_model=ApiResponse[dict])
async def reveal_key(
    key_alias: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    """One-time reveal of a key prefix (admin only). The full key is never stored."""
    if str(user.role) != "admin":
        raise HTTPException(status_code=403, detail="Only admins can reveal keys")

    from sqlalchemy import select

    from api.models.database import LiteLLMKeyRef

    result = await db.execute(
        select(LiteLLMKeyRef).where(LiteLLMKeyRef.key_alias == key_alias)
    )
    ref = result.scalar_one_or_none()
    if not ref:
        raise HTTPException(status_code=404, detail="Key not found")

    return ApiResponse(
        data={
            "key_alias": ref.key_alias,
            "key_prefix": ref.key_prefix,
            "note": "Full key value is never stored — only the prefix is available.",
        }
    )
