"""FastAPI authentication dependencies."""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.database import User
from api.services.auth import decode_access_token, get_user_by_id

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the JWT from the Authorization header."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_user_by_id(db, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Like get_current_user but returns None instead of raising for unauthenticated requests."""
    if credentials is None:
        return None

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        return None

    return await get_user_by_id(db, uuid.UUID(payload["sub"]))


def team_from_jwt(authorization_header: str | None) -> str | None:
    """Extract `current_tenant_slug` from a Bearer JWT, if present.

    Returns the slug string when the header is `Bearer <jwt>` and the JWT is
    signed with our `jwt_secret_key` and contains a `current_tenant_slug`
    claim. Returns None otherwise — for missing/non-Bearer headers, invalid
    signatures, expired tokens, or JWTs that simply don't carry the claim.

    Intended for external overlays (e.g. agentbreeder-cloud's SaaS control
    plane) that need to inject tenant context as the OSS `team` scope. Self-
    hosted deployments that don't issue JWTs with this claim are unaffected
    — every caller still falls back to existing team-resolution paths.
    """
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return None
    payload = decode_access_token(authorization_header[len("Bearer ") :])
    if payload is None:
        return None
    slug = payload.get("current_tenant_slug")
    return slug if isinstance(slug, str) else None
