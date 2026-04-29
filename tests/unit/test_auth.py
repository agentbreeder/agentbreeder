"""Tests for authentication — auth service, auth routes, and auth middleware."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.models.enums import UserRole
from api.services.auth import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

client = TestClient(app)

_NOW = datetime(2026, 3, 9, tzinfo=UTC)


def _make_user(
    email: str = "test@example.com",
    name: str = "Test User",
    role: UserRole = UserRole.viewer,
    **kwargs,
):
    """Create a mock User-like object."""
    defaults = {
        "id": kwargs.pop("id", uuid.uuid4()),
        "email": email,
        "name": name,
        "password_hash": hash_password("testpass123"),
        "role": role,
        "team": "engineering",
        "is_active": True,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    defaults.update(kwargs)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ── Password Hashing ──


class TestPasswordHashing:
    def test_hash_and_verify(self):
        hashed = hash_password("mypassword")
        assert hashed != "mypassword"
        assert verify_password("mypassword", hashed)

    def test_wrong_password(self):
        hashed = hash_password("mypassword")
        assert not verify_password("wrongpassword", hashed)

    def test_different_hashes_for_same_password(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt uses random salt


# ── JWT Tokens ──


class TestJWTTokens:
    def test_create_and_decode(self):
        user_id = str(uuid.uuid4())
        token = create_access_token(user_id, "test@test.com", "viewer")
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == user_id
        assert payload["email"] == "test@test.com"
        assert payload["role"] == "viewer"

    def test_invalid_token(self):
        assert decode_access_token("garbage.token.here") is None

    def test_empty_token(self):
        assert decode_access_token("") is None

    @patch("api.services.auth.settings")
    def test_wrong_secret_key(self, mock_settings):
        mock_settings.jwt_secret_key = "key1"
        mock_settings.jwt_algorithm = "HS256"
        mock_settings.access_token_expire_minutes = 1440
        token = create_access_token("id", "e@e.com", "viewer")
        # Decode with different key
        mock_settings.jwt_secret_key = "key2"
        assert decode_access_token(token) is None


# ── Auth Routes — Login ──


class TestLoginRoute:
    @patch("api.routes.auth.authenticate_user")
    @patch("api.routes.auth.create_access_token")
    @patch("api.database.get_db")
    def test_login_success(self, mock_db, mock_create_token, mock_auth):
        user = _make_user()
        mock_auth.return_value = user
        mock_create_token.return_value = "fake-jwt-token"
        mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

        res = client.post(
            "/api/v1/auth/login", json={"email": "test@example.com", "password": "testpass123"}
        )
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["access_token"] == "fake-jwt-token"
        assert data["token_type"] == "bearer"

    @patch("api.routes.auth.authenticate_user")
    @patch("api.database.get_db")
    def test_login_invalid_credentials(self, mock_db, mock_auth):
        mock_auth.return_value = None
        mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

        res = client.post(
            "/api/v1/auth/login", json={"email": "bad@test.com", "password": "wrong"}
        )
        assert res.status_code == 401
        assert "Invalid email or password" in res.json()["detail"]


# ── Auth Routes — Register ──


class TestRegisterRoute:
    @patch("api.routes.auth.create_user")
    @patch("api.routes.auth.get_user_by_email")
    @patch("api.database.get_db")
    def test_register_success(self, mock_db, mock_get_email, mock_create):
        mock_get_email.return_value = None
        user = _make_user()
        mock_create.return_value = user
        mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

        res = client.post(
            "/api/v1/auth/register",
            json={"email": "new@test.com", "name": "New User", "password": "password123"},
        )
        assert res.status_code == 201
        data = res.json()["data"]
        assert data["email"] == "test@example.com"
        assert data["name"] == "Test User"

    @patch("api.routes.auth.get_user_by_email")
    @patch("api.database.get_db")
    def test_register_duplicate_email(self, mock_db, mock_get_email):
        mock_get_email.return_value = _make_user()
        mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

        res = client.post(
            "/api/v1/auth/register",
            json={"email": "exists@test.com", "name": "Dup", "password": "password123"},
        )
        assert res.status_code == 409
        assert "already registered" in res.json()["detail"]

    @patch("api.routes.auth.get_user_by_email")
    @patch("api.database.get_db")
    def test_register_short_password(self, mock_db, mock_get_email):
        mock_get_email.return_value = None
        mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

        res = client.post(
            "/api/v1/auth/register",
            json={"email": "new@test.com", "name": "Short Pw", "password": "1234567"},
        )
        assert res.status_code == 422
        assert "at least 8 characters" in res.json()["detail"]


# ── Auth Routes — Me ──


class TestMeRoute:
    @patch("api.auth.get_user_by_id")
    @patch("api.auth.decode_access_token")
    @patch("api.database.get_db")
    def test_me_authenticated(self, mock_db, mock_decode, mock_get_user):
        user = _make_user()
        mock_decode.return_value = {"sub": str(user.id), "email": user.email, "role": "viewer"}
        mock_get_user.return_value = user
        mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

        res = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer fake-token"})
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["email"] == "test@example.com"
        assert data["name"] == "Test User"

    def test_me_unauthenticated(self):
        res = client.get("/api/v1/auth/me")
        assert res.status_code == 401

    @patch("api.auth.decode_access_token")
    @patch("api.database.get_db")
    def test_me_invalid_token(self, mock_db, mock_decode):
        mock_decode.return_value = None
        mock_db.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_db.return_value.__aexit__ = AsyncMock(return_value=None)

        res = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid"})
        assert res.status_code == 401


# ── Protected Routes ──


class TestProtectedAgentRoutes:
    def test_create_agent_requires_auth(self):
        res = client.post(
            "/api/v1/agents",
            json={
                "name": "test",
                "version": "1.0.0",
                "team": "eng",
                "owner": "a@b.com",
                "framework": "langgraph",
                "model_primary": "gpt-4o",
            },
        )
        assert res.status_code == 401

    def test_update_agent_requires_auth(self):
        res = client.put(f"/api/v1/agents/{uuid.uuid4()}", json={"version": "2.0.0"})
        assert res.status_code == 401

    def test_delete_agent_requires_auth(self):
        res = client.delete(f"/api/v1/agents/{uuid.uuid4()}")
        assert res.status_code == 401

    def test_list_agents_requires_auth(self):
        """GET /api/v1/agents now requires JWT — part of the 247/247 auth gating."""
        res = client.get("/api/v1/agents")
        assert res.status_code == 401


# ── Auth Service — async DB functions ──


class TestAuthServiceFunctions:
    @pytest.mark.asyncio
    async def test_decode_access_token_missing_sub(self):
        """Token without 'sub' claim must return None."""
        import jwt as pyjwt

        from api.config import settings

        payload = {"email": "x@x.com", "role": "viewer"}
        token = pyjwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        assert decode_access_token(token) is None

    @pytest.mark.asyncio
    async def test_get_user_by_email_found(self):
        from api.services.auth import get_user_by_email

        mock_user = _make_user(email="found@test.com")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_user_by_email(mock_db, "found@test.com")
        assert result is mock_user

    @pytest.mark.asyncio
    async def test_get_user_by_email_not_found(self):
        from api.services.auth import get_user_by_email

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_user_by_email(mock_db, "missing@test.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_by_id_found(self):
        from api.services.auth import get_user_by_id

        uid = uuid.uuid4()
        mock_user = _make_user(id=uid)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_user_by_id(mock_db, uid)
        assert result is mock_user

    @pytest.mark.asyncio
    async def test_create_user_returns_user(self):
        from api.services.auth import create_user

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        user = await create_user(mock_db, "new@test.com", "New User", "securepass123")
        assert user.email == "new@test.com"
        assert user.name == "New User"
        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_user_custom_role(self):
        from api.services.auth import create_user

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        user = await create_user(
            mock_db, "admin@test.com", "Admin", "adminpass123", team="ops", role=UserRole.admin
        )
        assert user.team == "ops"
        assert user.role == UserRole.admin
