"""Regression tests for ``GET /api/v1/secrets`` (issue #560, bug #5).

Studio's chat-to-build flow at ``/agents/new`` calls this endpoint twice as
part of its bootstrap. When the workspace's configured secrets backend cannot
be initialised in the API server's environment (e.g. ``keychain`` inside a
Docker container, ``aws`` with boto3 missing, network blip), the endpoint
historically raised a 500 — breaking the chat-to-build flow.

Contract (this test file enforces it):

* Empty workspace → ``200`` with ``data == []``.
* Backend initialisation / list call raises → ``200`` with ``data == []`` and
  a non-empty ``errors`` array (chosen over 503 because Studio already
  handles the ``{data, meta, errors}`` shape and an empty list is the most
  useful UX). The decision is documented in the endpoint docstring.
* Unauthenticated → ``401`` (never 500).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def _ws_cfg(name: str = "default") -> MagicMock:
    cfg = MagicMock()
    cfg.workspace = name
    return cfg


# ---------------------------------------------------------------------------
# 200 — empty list
# ---------------------------------------------------------------------------


def test_empty_workspace_returns_200_with_empty_list() -> None:
    """No secrets configured → 200 with ``data: []`` and no errors."""
    backend = MagicMock()
    backend.backend_name = "env"
    backend.list = AsyncMock(return_value=[])

    with patch(
        "api.routes.secrets.get_workspace_backend",
        return_value=(backend, _ws_cfg()),
    ):
        resp = client.get("/api/v1/secrets")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    assert body["errors"] == []


# ---------------------------------------------------------------------------
# 200 — backend raises (the actual bug)
# ---------------------------------------------------------------------------


def test_backend_init_failure_returns_200_with_errors() -> None:
    """If ``get_workspace_backend`` raises (e.g. keychain inside Docker), the
    endpoint MUST still return 200 with an empty list and a user-safe message
    in ``errors`` — never 500. This is the regression for bug #5."""
    with patch(
        "api.routes.secrets.get_workspace_backend",
        side_effect=ImportError("No module named 'keyring'"),
    ):
        resp = client.get("/api/v1/secrets")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    # Sanity: structured error message present, no stack trace / secret leak.
    assert isinstance(body["errors"], list)
    assert len(body["errors"]) >= 1
    assert "keyring" not in body["errors"][0]
    assert "ImportError" not in body["errors"][0]


def test_backend_list_failure_returns_200_with_errors() -> None:
    """If the backend initialises but ``.list()`` raises (e.g. AWS API
    timeout), the endpoint MUST also return 200 with an empty list and a
    sanitized message."""
    backend = MagicMock()
    backend.backend_name = "aws"
    backend.list = AsyncMock(side_effect=RuntimeError("AWS API timeout"))

    with patch(
        "api.routes.secrets.get_workspace_backend",
        return_value=(backend, _ws_cfg()),
    ):
        resp = client.get("/api/v1/secrets")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    assert len(body["errors"]) >= 1
    # Internal exception details MUST NOT leak to the client.
    assert "AWS API timeout" not in body["errors"][0]


# ---------------------------------------------------------------------------
# 401 — unauthenticated (must never be 500)
# ---------------------------------------------------------------------------


@pytest.mark.no_auto_auth
def test_unauthenticated_returns_401() -> None:
    """No Authorization header → 401 from ``get_current_user``; never 500."""
    resp = client.get("/api/v1/secrets")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 200 — happy-path with one secret (ensures the resilience path doesn't break
# the working case).
# ---------------------------------------------------------------------------


def test_returns_secret_summaries_when_present() -> None:
    """When the backend returns entries, they are serialised correctly."""
    from datetime import UTC, datetime

    from engine.secrets.base import SecretEntry

    entry = SecretEntry(
        name="openai/api-key",
        masked_value="••••abcd",
        backend="env",
        updated_at=datetime(2026, 6, 16, tzinfo=UTC),
    )
    backend = MagicMock()
    backend.backend_name = "env"
    backend.list = AsyncMock(return_value=[entry])

    with patch(
        "api.routes.secrets.get_workspace_backend",
        return_value=(backend, _ws_cfg("default")),
    ):
        resp = client.get("/api/v1/secrets")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meta"]["total"] == 1
    assert len(body["data"]) == 1
    item = body["data"][0]
    assert item["name"] == "openai/api-key"
    assert item["backend"] == "env"
    assert item["workspace"] == "default"
    assert item["masked_value"] == "••••abcd"
    assert body["errors"] == []
