"""Team management service — RBAC, memberships, API key encryption.

In-memory store (same pattern as memory_service.py, rag_service.py).
Will be replaced by real SQLAlchemy sessions when the DB layer is connected.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal data models
# ---------------------------------------------------------------------------


class TeamData(BaseModel):
    """Internal team record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    display_name: str
    description: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MembershipData(BaseModel):
    """Internal membership record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    team_id: str
    user_id: str
    user_email: str
    user_name: str
    role: str  # "admin" | "deployer" | "viewer"
    joined_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApiKeyData(BaseModel):
    """Internal API key record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    team_id: str
    provider: str
    encrypted_key: str
    key_hint: str  # last 4 chars: "...abcd"
    created_by: str  # email
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Encryption helpers (Fernet-compatible via SECRET_KEY)
# ---------------------------------------------------------------------------


def _get_fernet_key() -> bytes:
    """Derive a 32-byte Fernet key from SECRET_KEY env var."""
    secret = os.environ.get("SECRET_KEY", "agentbreeder-dev-secret-key-change-me")
    # Derive a URL-safe base64-encoded 32-byte key from the secret
    raw = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def _encrypt_key(plain: str) -> str:
    """Encrypt a plaintext API key."""
    try:
        from cryptography.fernet import Fernet

        f = Fernet(_get_fernet_key())
        return f.encrypt(plain.encode()).decode()
    except ImportError:
        # Fallback: simple base64 (dev only — cryptography not installed)
        logger.warning("cryptography package not installed — using base64 fallback (NOT secure)")
        return base64.b64encode(plain.encode()).decode()


def _decrypt_key(encrypted: str) -> str:
    """Decrypt an encrypted API key."""
    try:
        from cryptography.fernet import Fernet

        f = Fernet(_get_fernet_key())
        return f.decrypt(encrypted.encode()).decode()
    except ImportError:
        return base64.b64decode(encrypted.encode()).decode()


# ---------------------------------------------------------------------------
# Role hierarchy
# ---------------------------------------------------------------------------

ROLE_HIERARCHY = {"admin": 3, "deployer": 2, "viewer": 1}

# Action → minimum role required
ACTION_ROLES: dict[str, int] = {
    "read": 1,  # viewer
    "create": 2,  # deployer
    "deploy": 2,  # deployer
    "update": 2,  # deployer
    "delete": 3,  # admin
    "manage": 3,  # admin (manage members, keys)
}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TeamService:
    """In-memory team management service.

    All data stored in class-level dicts — persists within a process.
    """

    _teams: dict[str, TeamData] = {}
    _memberships: dict[str, MembershipData] = {}  # keyed by membership id
    _api_keys: dict[str, ApiKeyData] = {}  # keyed by api key id

    # -- Reset (for tests) --------------------------------------------------

    @classmethod
    def reset(cls) -> None:
        """Clear all data (used in tests)."""
        cls._teams.clear()
        cls._memberships.clear()
        cls._api_keys.clear()

    # -- Team CRUD ----------------------------------------------------------

    @classmethod
    async def create_team(
        cls,
        *,
        name: str,
        display_name: str,
        description: str = "",
    ) -> TeamData:
        """Create a new team."""
        # Check uniqueness
        for t in cls._teams.values():
            if t.name == name:
                raise ValueError(f"Team with name '{name}' already exists")

        team = TeamData(
            name=name,
            display_name=display_name,
            description=description,
        )
        cls._teams[team.id] = team
        logger.info("Created team %s (%s)", team.name, team.id)
        return team

    @classmethod
    async def list_teams(
        cls,
        *,
        user_id: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[TeamData], int]:
        """List teams. If user_id is given, only teams the user belongs to."""
        if user_id:
            user_team_ids = {m.team_id for m in cls._memberships.values() if m.user_id == user_id}
            teams = [t for t in cls._teams.values() if t.id in user_team_ids]
        else:
            teams = list(cls._teams.values())

        teams.sort(key=lambda t: t.name)
        total = len(teams)
        start = (page - 1) * per_page
        return teams[start : start + per_page], total

    @classmethod
    async def get_team(cls, team_id: str) -> TeamData | None:
        return cls._teams.get(team_id)

    @classmethod
    async def get_team_by_name(cls, name: str) -> TeamData | None:
        for t in cls._teams.values():
            if t.name == name:
                return t
        return None

    @classmethod
    async def update_team(
        cls,
        team_id: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
    ) -> TeamData | None:
        team = cls._teams.get(team_id)
        if not team:
            return None
        if display_name is not None:
            team.display_name = display_name
        if description is not None:
            team.description = description
        team.updated_at = datetime.now(UTC)
        return team

    @classmethod
    async def delete_team(cls, team_id: str) -> bool:
        if team_id not in cls._teams:
            return False
        del cls._teams[team_id]
        # Cascade delete memberships and keys
        cls._memberships = {k: v for k, v in cls._memberships.items() if v.team_id != team_id}
        cls._api_keys = {k: v for k, v in cls._api_keys.items() if v.team_id != team_id}
        logger.info("Deleted team %s", team_id)
        return True

    # -- Membership ---------------------------------------------------------

    @classmethod
    async def add_member(
        cls,
        team_id: str,
        *,
        user_id: str,
        user_email: str,
        user_name: str,
        role: str = "viewer",
    ) -> MembershipData:
        """Add a member to a team. Raises ValueError if already a member."""
        if team_id not in cls._teams:
            raise ValueError(f"Team {team_id} not found")

        if role not in ROLE_HIERARCHY:
            raise ValueError(f"Invalid role: {role}")

        # Check for existing membership
        for m in cls._memberships.values():
            if m.team_id == team_id and m.user_id == user_id:
                raise ValueError(f"User {user_id} is already a member of team {team_id}")

        membership = MembershipData(
            team_id=team_id,
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
            role=role,
        )
        cls._memberships[membership.id] = membership
        logger.info("Added %s to team %s as %s", user_email, team_id, role)

        # Auto-mint LiteLLM key for the new member (best-effort, non-blocking)
        try:
            import asyncio

            asyncio.ensure_future(
                cls._auto_mint_member_key(
                    team_id=team_id,
                    user_email=user_email,
                    team_name=cls._teams[team_id].name if team_id in cls._teams else team_id,
                )
            )
        except Exception:
            logger.debug("LiteLLM key auto-mint skipped (event loop unavailable)")

        return membership

    @classmethod
    async def remove_member(cls, team_id: str, user_id: str) -> bool:
        """Remove a member from a team."""
        to_remove = None
        removed_email: str | None = None
        for mid, m in cls._memberships.items():
            if m.team_id == team_id and m.user_id == user_id:
                to_remove = mid
                removed_email = m.user_email
                break
        if to_remove:
            del cls._memberships[to_remove]
            logger.info("Removed user %s from team %s", user_id, team_id)

            # Auto-revoke LiteLLM key for removed member (best-effort)
            if removed_email:
                try:
                    import asyncio

                    asyncio.ensure_future(
                        cls._auto_revoke_member_key(
                            team_id=team_id,
                            user_email=removed_email,
                        )
                    )
                except Exception:
                    logger.debug("LiteLLM key auto-revoke skipped (event loop unavailable)")

            return True
        return False

    @classmethod
    async def update_member_role(
        cls, team_id: str, user_id: str, role: str
    ) -> MembershipData | None:
        """Update a member's role."""
        if role not in ROLE_HIERARCHY:
            raise ValueError(f"Invalid role: {role}")

        for m in cls._memberships.values():
            if m.team_id == team_id and m.user_id == user_id:
                m.role = role
                return m
        return None

    @classmethod
    async def get_team_members(cls, team_id: str) -> list[MembershipData]:
        """List all members of a team."""
        return sorted(
            [m for m in cls._memberships.values() if m.team_id == team_id],
            key=lambda m: m.user_name,
        )

    @classmethod
    async def get_member_count(cls, team_id: str) -> int:
        return sum(1 for m in cls._memberships.values() if m.team_id == team_id)

    @classmethod
    async def get_user_teams(cls, user_id: str) -> list[TeamData]:
        """List all teams a user belongs to."""
        team_ids = {m.team_id for m in cls._memberships.values() if m.user_id == user_id}
        return sorted(
            [t for t in cls._teams.values() if t.id in team_ids],
            key=lambda t: t.name,
        )

    @classmethod
    async def get_user_role_in_team(cls, user_id: str, team_id: str) -> str | None:
        """Get a user's role in a specific team. Returns None if not a member."""
        for m in cls._memberships.values():
            if m.team_id == team_id and m.user_id == user_id:
                return m.role
        return None

    # -- Permission checking ------------------------------------------------

    @classmethod
    async def can_user_do(
        cls,
        user_id: str,
        action: str,
        resource_type: str = "agent",
        resource_team: str | None = None,
    ) -> tuple[bool, str]:
        """Check if a user can perform an action.

        Returns (allowed, reason).

        Rules:
        - admin: all actions on their team resources
        - deployer: create, read, deploy on their team resources
        - viewer: read-only on their team resources
        - cross-team: admins of any team can read any team's resources
        - no membership = no access
        """
        if action not in ACTION_ROLES:
            return False, f"Unknown action: {action}"

        required_level = ACTION_ROLES[action]

        if resource_team is None:
            # No team context — check if user has sufficient role in ANY team
            max_level = 0
            for m in cls._memberships.values():
                if m.user_id == user_id:
                    level = ROLE_HIERARCHY.get(m.role, 0)
                    if level > max_level:
                        max_level = level
            if max_level >= required_level:
                return True, "Sufficient role found"
            return False, "Insufficient permissions"

        # Check user's role in the resource's team
        user_role = await cls.get_user_role_in_team(user_id, resource_team)

        if user_role is not None:
            level = ROLE_HIERARCHY.get(user_role, 0)
            if level >= required_level:
                return True, f"User has {user_role} role in team"
            return False, f"User has {user_role} role but {action} requires higher permissions"

        # Cross-team access: admins of other teams can read any team
        if action == "read":
            for m in cls._memberships.values():
                if m.user_id == user_id and m.role == "admin":
                    return True, "Admin cross-team read access"

        return False, "User is not a member of this team"

    # -- API Keys -----------------------------------------------------------

    @classmethod
    async def set_api_key(
        cls,
        team_id: str,
        *,
        provider: str,
        api_key: str,
        created_by: str,
    ) -> ApiKeyData:
        """Set (or replace) an API key for a provider on a team."""
        if team_id not in cls._teams:
            raise ValueError(f"Team {team_id} not found")

        # Remove existing key for this team+provider
        cls._api_keys = {
            k: v
            for k, v in cls._api_keys.items()
            if not (v.team_id == team_id and v.provider == provider)
        }

        encrypted = _encrypt_key(api_key)
        hint = "..." + api_key[-4:] if len(api_key) >= 4 else "..." + api_key

        key_data = ApiKeyData(
            team_id=team_id,
            provider=provider,
            encrypted_key=encrypted,
            key_hint=hint,
            created_by=created_by,
        )
        cls._api_keys[key_data.id] = key_data
        logger.info("Set API key for %s on team %s", provider, team_id)
        return key_data

    @classmethod
    async def list_api_keys(cls, team_id: str) -> list[ApiKeyData]:
        """List API keys for a team (encrypted — use key_hint for display)."""
        return sorted(
            [k for k in cls._api_keys.values() if k.team_id == team_id],
            key=lambda k: k.provider,
        )

    @classmethod
    async def delete_api_key(cls, key_id: str) -> bool:
        if key_id in cls._api_keys:
            del cls._api_keys[key_id]
            return True
        return False

    @classmethod
    async def get_decrypted_key(cls, team_id: str, provider: str) -> str | None:
        """Get the decrypted API key for a team+provider. Returns None if not set."""
        for k in cls._api_keys.values():
            if k.team_id == team_id and k.provider == provider:
                return _decrypt_key(k.encrypted_key)
        return None

    @classmethod
    async def test_api_key(cls, key_id: str) -> dict[str, Any]:
        """Test an API key by attempting a simple API call. Returns status dict."""
        key_data = cls._api_keys.get(key_id)
        if not key_data:
            return {"success": False, "error": "API key not found"}

        # For now, just validate that the key can be decrypted
        try:
            decrypted = _decrypt_key(key_data.encrypted_key)
            if len(decrypted) < 4:
                return {"success": False, "error": "Key too short"}
            return {"success": True, "provider": key_data.provider, "hint": key_data.key_hint}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -- LiteLLM key lifecycle hooks ----------------------------------------

    @classmethod
    async def _auto_mint_member_key(
        cls,
        team_id: str,
        user_email: str,
        team_name: str,
    ) -> None:
        """Auto-mint a LiteLLM user-scoped key when a member joins a team.

        Runs as a background task — failures are logged but not propagated.
        Requires a live DB session (skipped in-memory-only mode).
        """
        try:
            from api.database import async_session
            from api.models.enums import KeyScopeType
            from api.models.schemas import LiteLLMKeyCreate
            from api.services.litellm_key_service import create_key

            key_alias = f"user-{user_email}-{team_name}"
            async with async_session() as db:
                body = LiteLLMKeyCreate(
                    key_alias=key_alias,
                    scope_type=KeyScopeType.user,
                    scope_id=user_email,
                    team_id=team_id,
                    tags=["auto-provisioned", f"user:{user_email}", f"team:{team_name}"],
                )
                await create_key(db, body, created_by="team-service")
                await db.commit()
                logger.info("Auto-minted LiteLLM key %s for %s joining team %s", key_alias, user_email, team_name)
        except Exception as exc:
            logger.debug("LiteLLM key auto-mint failed for %s: %s", user_email, exc)

    @classmethod
    async def _auto_revoke_member_key(
        cls,
        team_id: str,
        user_email: str,
    ) -> None:
        """Auto-revoke a user's LiteLLM keys when they leave a team.

        Runs as a background task — failures are logged but not propagated.
        """
        try:
            from api.database import async_session
            from api.models.database import LiteLLMKeyRef
            from api.models.enums import KeyScopeType
            from api.services.litellm_key_service import revoke_key
            from sqlalchemy import select

            async with async_session() as db:
                stmt = select(LiteLLMKeyRef).where(
                    LiteLLMKeyRef.scope_type == KeyScopeType.user,
                    LiteLLMKeyRef.scope_id == user_email,
                    LiteLLMKeyRef.team_id == team_id,
                    LiteLLMKeyRef.is_active.is_(True),
                )
                result = await db.execute(stmt)
                keys = result.scalars().all()
                for key in keys:
                    await revoke_key(db, key.key_alias)
                if keys:
                    await db.commit()
                    logger.info(
                        "Auto-revoked %d LiteLLM key(s) for %s leaving team %s",
                        len(keys),
                        user_email,
                        team_id,
                    )
        except Exception as exc:
            logger.debug("LiteLLM key auto-revoke failed for %s: %s", user_email, exc)

    # -- Seed default team --------------------------------------------------

    @classmethod
    async def seed_default_team(cls) -> TeamData | None:
        """Create a default team if none exist."""
        if cls._teams:
            return None

        team = await cls.create_team(
            name="default",
            display_name="Default Team",
            description="Auto-created default team",
        )
        logger.info("Seeded default team: %s", team.id)
        return team
