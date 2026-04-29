"""Tests for ``GET /api/v1/providers/catalog/status`` (issue #175).

Verifies the dashboard ``/models`` page can ask the API "which catalog
providers already have an api-key set in the workspace secrets backend?"
and get back a deterministic ``{name: bool}`` map covering every catalog
entry. External services (catalog YAML loader, secrets backend) are
mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.services.auth import create_access_token
from engine.providers.catalog import CatalogEntry
from engine.secrets.base import SecretEntry

client = TestClient(app)


def _viewer_headers() -> tuple[dict, uuid.UUID]:
    uid = uuid.uuid4()
    token = create_access_token(str(uid), "viewer@example.com", "viewer")
    return {"Authorization": f"Bearer {token}"}, uid


def _make_user(user_id: uuid.UUID):
    mock = MagicMock()
    mock.id = user_id
    mock.email = "viewer@example.com"
    mock.role = "viewer"
    mock.team = "engineering"
    mock.is_active = True
    return mock


def _entry(name: str, env: str) -> CatalogEntry:
    return CatalogEntry(
        type="openai_compatible",
        base_url=f"https://api.{name}.example/v1",  # type: ignore[arg-type]
        api_key_env=env,
        default_headers={},
        docs=None,
        discovery=None,
        notable_models=[],
        source="builtin",
    )


class _FakeBackendWith:
    """Fake secrets backend that pretends only specific names exist."""

    backend_name = "env"

    def __init__(self, configured_names: set[str]) -> None:
        self._configured = configured_names

    async def list(self) -> list[SecretEntry]:
        ts = datetime(2026, 4, 28, tzinfo=UTC)
        return [
            SecretEntry(name=n, masked_value="••••abcd", backend="env", updated_at=ts)
            for n in self._configured
        ]


def _patch_for_status(
    catalog_entries: dict[str, CatalogEntry],
    configured: set[str],
):
    """Stack: bypass auth, stub catalog list_entries, stub workspace backend."""
    headers, uid = _viewer_headers()
    user = _make_user(uid)

    backend = _FakeBackendWith(configured)
    ws_cfg = MagicMock()
    ws_cfg.workspace = "default"

    patches = [
        patch("api.auth.decode_access_token", return_value={"sub": str(uid)}),
        patch("api.auth.get_user_by_id", new_callable=AsyncMock, return_value=user),
        patch("engine.providers.catalog.list_entries", return_value=catalog_entries),
        patch(
            "engine.secrets.factory.get_workspace_backend",
            return_value=(backend, ws_cfg),
        ),
    ]
    return patches, headers


class TestCatalogStatus:
    @pytest.mark.no_auto_auth
    def test_unauthenticated_returns_401(self) -> None:
        assert client.get("/api/v1/providers/catalog/status").status_code == 401

    def test_returns_false_when_no_secret_configured(self) -> None:
        entries = {
            "nvidia": _entry("nvidia", "NVIDIA_API_KEY"),
            "groq": _entry("groq", "GROQ_API_KEY"),
        }
        patches, headers = _patch_for_status(entries, configured=set())
        for p in patches:
            p.start()
        try:
            resp = client.get("/api/v1/providers/catalog/status", headers=headers)
            assert resp.status_code == 200
            assert resp.json()["data"] == {"nvidia": False, "groq": False}
        finally:
            for p in patches:
                p.stop()

    def test_dashboard_key_marks_configured(self) -> None:
        entries = {"nvidia": _entry("nvidia", "NVIDIA_API_KEY")}
        # Simulate the dashboard Configure modal having written a secret
        # under the deterministic ``<provider>/api-key`` key.
        patches, headers = _patch_for_status(entries, configured={"nvidia/api-key"})
        for p in patches:
            p.start()
        try:
            resp = client.get("/api/v1/providers/catalog/status", headers=headers)
            assert resp.status_code == 200
            assert resp.json()["data"] == {"nvidia": True}
        finally:
            for p in patches:
                p.stop()

    def test_legacy_env_key_also_marks_configured(self) -> None:
        """Secrets imported via the CLI under the env-var name still count."""
        entries = {"groq": _entry("groq", "GROQ_API_KEY")}
        patches, headers = _patch_for_status(entries, configured={"GROQ_API_KEY"})
        for p in patches:
            p.start()
        try:
            resp = client.get("/api/v1/providers/catalog/status", headers=headers)
            assert resp.status_code == 200
            assert resp.json()["data"] == {"groq": True}
        finally:
            for p in patches:
                p.stop()

    def test_partial_configuration(self) -> None:
        entries = {
            "nvidia": _entry("nvidia", "NVIDIA_API_KEY"),
            "groq": _entry("groq", "GROQ_API_KEY"),
            "kimi": _entry("kimi", "MOONSHOT_API_KEY"),
        }
        patches, headers = _patch_for_status(
            entries, configured={"nvidia/api-key", "MOONSHOT_API_KEY"}
        )
        for p in patches:
            p.start()
        try:
            resp = client.get("/api/v1/providers/catalog/status", headers=headers)
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data == {"nvidia": True, "groq": False, "kimi": True}
        finally:
            for p in patches:
                p.stop()

    def test_response_includes_every_catalog_entry(self) -> None:
        """The status map's keys must be a 1:1 reflection of the catalog."""
        entries = {f"p{i}": _entry(f"p{i}", f"P{i}_KEY") for i in range(5)}
        patches, headers = _patch_for_status(entries, configured=set())
        for p in patches:
            p.start()
        try:
            resp = client.get("/api/v1/providers/catalog/status", headers=headers)
            assert resp.status_code == 200
            assert set(resp.json()["data"].keys()) == set(entries.keys())
        finally:
            for p in patches:
                p.stop()
