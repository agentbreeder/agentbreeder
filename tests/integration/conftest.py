"""Shared fixtures for integration tests.

Wires a default admin user into the FastAPI app for all integration tests so
that auth-gated routes pass without a real DB or real JWT.  Using a
session-scoped autouse fixture (instead of module-level overrides) ensures the
overrides survive even when pytest collects + runs unit tests in the same
invocation (unit-test teardown pops overrides from the shared app object).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from api.auth import get_current_user, get_optional_user
from api.main import app
from api.models.enums import UserRole

_DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
_DEFAULT_USER = MagicMock()
_DEFAULT_USER.id = _DEFAULT_USER_ID
_DEFAULT_USER.email = "integration-test@agentbreeder.io"
_DEFAULT_USER.name = "Integration Test Admin"
_DEFAULT_USER.role = UserRole.admin
_DEFAULT_USER.team = "engineering"
_DEFAULT_USER.is_active = True


@pytest.fixture(autouse=True)
def _integration_auth():
    """Inject a default admin into every integration test."""

    async def _mock_get_current_user():
        return _DEFAULT_USER

    async def _mock_get_optional_user():
        return _DEFAULT_USER

    app.dependency_overrides[get_current_user] = _mock_get_current_user
    app.dependency_overrides[get_optional_user] = _mock_get_optional_user
    yield
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_optional_user, None)
