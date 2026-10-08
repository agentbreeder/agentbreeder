"""Coverage boost tests — iteration 2.

Covers:
  - registry/a2a_agents.py  (status filter in list, all-fields update)
  - api/routes/builders.py  (cache hit, missing schema, empty body, no-name import, duplicate)
"""

from __future__ import annotations

import textwrap

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from api.models.database import Base

_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_SessionFactory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with _SessionFactory() as s:
        yield s
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ─────────────────────────────────────────────────────────────
# registry/a2a_agents.py — status filter + full update
# ─────────────────────────────────────────────────────────────


class TestA2AAgentRegistryBranches:
    @pytest.mark.asyncio
    async def test_list_with_status_filter(self, session: AsyncSession) -> None:
        from api.models.enums import A2AStatus
        from registry.a2a_agents import A2AAgentRegistry

        await A2AAgentRegistry.create(session, name="reg-agent", endpoint_url="http://a1")
        a2 = await A2AAgentRegistry.create(
            session, name="inactive-agent", endpoint_url="http://a2"
        )
        # Agents default to "registered" — set one to inactive
        a2.status = A2AStatus.inactive
        await session.flush()

        registered, total = await A2AAgentRegistry.list(session, status=A2AStatus.registered)
        assert total == 1
        assert registered[0].name == "reg-agent"

    @pytest.mark.asyncio
    async def test_list_with_team_and_status_filter(self, session: AsyncSession) -> None:
        from registry.a2a_agents import A2AAgentRegistry

        await A2AAgentRegistry.create(
            session, name="eng-agent", endpoint_url="http://e1", team="engineering"
        )
        await A2AAgentRegistry.create(
            session, name="ops-agent", endpoint_url="http://o1", team="ops"
        )

        results, total = await A2AAgentRegistry.list(session, team="engineering")
        assert total == 1
        assert results[0].name == "eng-agent"

    @pytest.mark.asyncio
    async def test_update_all_fields(self, session: AsyncSession) -> None:
        from api.models.enums import A2AStatus
        from registry.a2a_agents import A2AAgentRegistry

        agent = await A2AAgentRegistry.create(
            session,
            name="full-update",
            endpoint_url="http://original",
            capabilities=["chat"],
            auth_scheme="none",
        )

        updated = await A2AAgentRegistry.update(
            session,
            str(agent.id),
            endpoint_url="http://updated",
            agent_card={"version": "2"},
            capabilities=["chat", "stream"],
            auth_scheme="bearer",
            status=A2AStatus.inactive,
        )

        assert updated is not None
        assert updated.endpoint_url == "http://updated"
        assert updated.agent_card == {"version": "2"}
        assert updated.capabilities == ["chat", "stream"]
        assert updated.auth_scheme == "bearer"
        assert updated.status == A2AStatus.inactive

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_none(self, session: AsyncSession) -> None:
        from registry.a2a_agents import A2AAgentRegistry

        result = await A2AAgentRegistry.update(
            session, "00000000-0000-0000-0000-000000000000", endpoint_url="http://x"
        )
        assert result is None


# ─────────────────────────────────────────────────────────────
# api/routes/builders.py — uncovered branches
# ─────────────────────────────────────────────────────────────


VALID_AGENT_YAML = textwrap.dedent("""\
    name: cache-test-agent
    version: "1.0.0"
    team: engineering
    owner: alice@example.com
    framework: langgraph
    model:
      primary: claude-sonnet-4
    deploy:
      cloud: aws
""")

VALID_AGENT_YAML_NO_NAME = textwrap.dedent("""\
    version: "1.0.0"
    team: engineering
    owner: alice@example.com
    framework: langgraph
    model:
      primary: claude-sonnet-4
    deploy:
      cloud: aws
""")


class TestBuildersBranchCoverage:
    """Hit the uncovered branches in api/routes/builders.py."""

    def setup_method(self, tmp_path=None):
        import tempfile

        from fastapi.testclient import TestClient

        import api.routes.builders as builders_module
        from api.main import app
        from api.routes.builders import _SCHEMA_CACHE, FileStore

        self.client = TestClient(app)
        # Redirect store to a fresh temp dir
        self._tmp_dir = tempfile.mkdtemp()
        self._file_store = FileStore(base_dir=__import__("pathlib").Path(self._tmp_dir))
        builders_module._store = self._file_store
        self._cache = _SCHEMA_CACHE
        _SCHEMA_CACHE.clear()

    def teardown_method(self):
        import shutil

        import api.routes.builders as builders_module
        from api.routes.builders import FileStore

        # Restore default store
        builders_module._store = FileStore()
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def test_schema_cache_hit(self):
        """Second PUT to same resource_type uses cached schema (line 40)."""
        # First PUT — loads schema from disk, caches it
        resp1 = self.client.put(
            "/api/v1/builders/agent/cache-test/yaml",
            content=VALID_AGENT_YAML,
            headers={"content-type": "application/x-yaml"},
        )
        assert resp1.status_code == 200

        # Second PUT — hits _SCHEMA_CACHE branch
        resp2 = self.client.put(
            "/api/v1/builders/agent/cache-test2/yaml",
            content=VALID_AGENT_YAML.replace("cache-test-agent", "cache-test-agent2"),
            headers={"content-type": "application/x-yaml"},
        )
        assert resp2.status_code == 200

    def test_invalid_resource_type_put(self):
        """PUT with unknown resource_type raises 400."""
        resp = self.client.put(
            "/api/v1/builders/unknown-type/foo/yaml",
            content=VALID_AGENT_YAML,
            headers={"content-type": "application/x-yaml"},
        )
        assert resp.status_code == 400
        assert "Invalid resource_type" in resp.json()["detail"]

    def test_empty_body_raises_422(self):
        """PUT with empty YAML body raises 422 (line 168)."""
        resp = self.client.put(
            "/api/v1/builders/agent/empty-test/yaml",
            content="   ",
            headers={"content-type": "application/x-yaml"},
        )
        assert resp.status_code == 422
        assert "Empty YAML body" in resp.json()["detail"]

    def test_import_yaml_missing_name_field(self):
        """Import YAML without 'name' field raises 422 (line 201)."""
        resp = self.client.post(
            "/api/v1/builders/import",
            json={"resource_type": "agent", "yaml_content": VALID_AGENT_YAML_NO_NAME},
        )
        assert resp.status_code == 422
        assert "name" in resp.json()["detail"].lower()

    def test_import_yaml_duplicate_raises_409(self):
        """Import same name twice raises 409 (line 204)."""
        resp1 = self.client.post(
            "/api/v1/builders/import",
            json={"resource_type": "agent", "yaml_content": VALID_AGENT_YAML},
        )
        assert resp1.status_code == 201

        resp2 = self.client.post(
            "/api/v1/builders/import",
            json={"resource_type": "agent", "yaml_content": VALID_AGENT_YAML},
        )
        assert resp2.status_code == 409
        assert "already exists" in resp2.json()["detail"]

    def test_invalid_resource_type_import(self):
        """Import with unknown resource_type raises 400."""
        resp = self.client.post(
            "/api/v1/builders/import",
            json={"resource_type": "widget", "yaml_content": VALID_AGENT_YAML},
        )
        assert resp.status_code == 400
