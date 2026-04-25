"""Service for managing LiteLLM virtual keys.

Calls the LiteLLM /key/generate and /key/delete endpoints and stores
lightweight metadata in the AgentBreeder database so keys can be listed,
filtered by scope/team/agent, and revoked from the dashboard.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.database import LiteLLMKeyRef
from api.models.enums import KeyScopeType
from api.models.schemas import LiteLLMKeyCreate, LiteLLMKeyCreateResponse, LiteLLMKeyResponse

logger = logging.getLogger(__name__)

_LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_MASTER_KEY = os.getenv("LITELLM_MASTER_KEY", "sk-agentbreeder-quickstart")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_LITELLM_MASTER_KEY}",
        "Content-Type": "application/json",
    }


async def _generate_litellm_key(body: LiteLLMKeyCreate) -> tuple[str, str]:
    """Call LiteLLM /key/generate. Returns (key_value, litellm_key_id)."""
    payload: dict = {"key_alias": body.key_alias}
    if body.allowed_models:
        payload["models"] = body.allowed_models
    if body.max_budget is not None:
        payload["max_budget"] = body.max_budget
    if body.budget_duration:
        payload["budget_duration"] = body.budget_duration.value
    if body.tpm_limit is not None:
        payload["tpm_limit"] = body.tpm_limit
    if body.rpm_limit is not None:
        payload["rpm_limit"] = body.rpm_limit
    if body.tags:
        payload["metadata"] = {"tags": body.tags}
    if body.expires_at:
        payload["expires"] = body.expires_at.isoformat()
    # Team attribution so LiteLLM can track spend per team
    if body.team_id:
        payload["team_id"] = body.team_id

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{_LITELLM_BASE_URL}/key/generate",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    key_value: str = data["key"]
    litellm_key_id: str = data.get("key_name", "") or data.get("id", "")
    return key_value, litellm_key_id


async def create_key(
    db: AsyncSession,
    body: LiteLLMKeyCreate,
    created_by: str,
) -> LiteLLMKeyCreateResponse:
    """Generate a virtual key in LiteLLM and persist metadata in AgentBreeder."""
    key_value, litellm_key_id = await _generate_litellm_key(body)
    key_prefix = key_value[:12]

    ref = LiteLLMKeyRef(
        id=uuid.uuid4(),
        key_alias=body.key_alias,
        key_prefix=key_prefix,
        litellm_key_id=litellm_key_id or None,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        team_id=body.team_id,
        agent_name=body.agent_name,
        created_by=created_by,
        allowed_models=body.allowed_models,
        max_budget=body.max_budget,
        budget_duration=body.budget_duration,
        tpm_limit=body.tpm_limit,
        rpm_limit=body.rpm_limit,
        tags=body.tags,
        expires_at=body.expires_at,
        is_active=True,
    )
    db.add(ref)
    await db.commit()
    await db.refresh(ref)

    return LiteLLMKeyCreateResponse(
        **LiteLLMKeyResponse.model_validate(ref).model_dump(),
        key_value=key_value,
    )


async def list_keys(
    db: AsyncSession,
    scope_type: KeyScopeType | None = None,
    team_id: str | None = None,
    agent_name: str | None = None,
    active_only: bool = True,
) -> list[LiteLLMKeyResponse]:
    stmt = select(LiteLLMKeyRef)
    if scope_type:
        stmt = stmt.where(LiteLLMKeyRef.scope_type == scope_type)
    if team_id:
        stmt = stmt.where(LiteLLMKeyRef.team_id == team_id)
    if agent_name:
        stmt = stmt.where(LiteLLMKeyRef.agent_name == agent_name)
    if active_only:
        stmt = stmt.where(LiteLLMKeyRef.is_active.is_(True))
    stmt = stmt.order_by(LiteLLMKeyRef.created_at.desc())

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [LiteLLMKeyResponse.model_validate(r) for r in rows]


async def revoke_key(db: AsyncSession, key_alias: str) -> bool:
    """Revoke a virtual key: delete from LiteLLM and mark inactive in DB."""
    result = await db.execute(select(LiteLLMKeyRef).where(LiteLLMKeyRef.key_alias == key_alias))
    ref = result.scalar_one_or_none()
    if not ref:
        return False

    # Best-effort deletion from LiteLLM
    if ref.litellm_key_id:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    f"{_LITELLM_BASE_URL}/key/delete",
                    headers=_headers(),
                    json={"keys": [ref.litellm_key_id]},
                )
        except Exception:
            logger.warning(
                "LiteLLM /key/delete failed for alias %s — marking inactive only", key_alias
            )

    ref.is_active = False
    ref.updated_at = datetime.utcnow()
    await db.commit()
    return True


async def get_or_create_agent_key(
    db: AsyncSession,
    agent_name: str,
    team_id: str,
    created_by: str = "deploy-engine",
    allowed_models: list[str] | None = None,
) -> tuple[str, str | None]:
    """Return an active key for an agent, creating one if it does not exist.

    Used by the deploy engine to mint a scoped key per agent automatically.
    Returns ``(key_alias, key_value)``.  ``key_value`` is ``None`` when an
    existing key is returned because the plaintext secret is not stored after
    initial creation.  The caller must inject ``key_value`` into the container
    environment as ``LITELLM_VIRTUAL_KEY`` when it is not ``None``.
    """
    result = await db.execute(
        select(LiteLLMKeyRef).where(
            LiteLLMKeyRef.agent_name == agent_name,
            LiteLLMKeyRef.scope_type == KeyScopeType.agent,
            LiteLLMKeyRef.is_active.is_(True),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing.key_alias, None

    body = LiteLLMKeyCreate(
        key_alias=f"agent-{agent_name}",
        scope_type=KeyScopeType.agent,
        scope_id=agent_name,
        team_id=team_id,
        agent_name=agent_name,
        allowed_models=allowed_models,
        tags=["auto-provisioned", f"agent:{agent_name}", f"team:{team_id}"],
    )
    created = await create_key(db, body, created_by=created_by)
    return created.key_alias, created.key_value
