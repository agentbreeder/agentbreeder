import pytest

from api.services.builder_session_service import (  # noqa: F401  (service used in C3)
    BuilderSessionService,
    SessionEventBus,
)


def test_builder_session_model_importable():
    from api.models.database import BuilderSession

    assert BuilderSession.__tablename__ == "builder_sessions"


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe():
    bus = SessionEventBus()
    async with bus.subscribe("s1") as q:
        await bus.publish("s1", {"event": "token", "data": "{}"})
        evt = await q.get()
        assert evt["event"] == "token"


@pytest.mark.asyncio
async def test_event_bus_isolated_per_session():
    bus = SessionEventBus()
    async with bus.subscribe("s1") as q1:
        await bus.publish("s2", {"event": "x", "data": "{}"})
        assert q1.empty()


# --- C3: create / get / list route tests -----------------------------------

from unittest.mock import AsyncMock, patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def _override_db():
    from api.database import get_db

    app.dependency_overrides[get_db] = lambda: AsyncMock()
    yield
    app.dependency_overrides.pop(get_db, None)


def _fake_session(sid="11111111-1111-1111-1111-111111111111", team="engineering"):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=sid,
        team=team,
        engine="claude",
        state={
            "history": [],
            "agent_yaml": None,
            "files": {},
            "deploy_job_id": None,
            "satisfied": [],
        },
    )


def test_create_session_returns_id(client, _override_db):
    with patch(
        "api.routes.builder_sessions.BuilderSessionService.create",
        new=AsyncMock(return_value=_fake_session()),
    ):
        r = client.post("/api/v1/builder/sessions", json={"engine": "claude"})
    assert r.status_code == 200
    assert r.json()["data"]["engine"] == "claude"


def test_create_rejects_bad_engine(client, _override_db):
    r = client.post("/api/v1/builder/sessions", json={"engine": "bard"})
    assert r.status_code == 400


def test_get_session_found(client, _override_db):
    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get",
        new=AsyncMock(return_value=_fake_session()),
    ):
        r = client.get("/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111")
    assert r.status_code == 200
    assert r.json()["data"]["id"] == "11111111-1111-1111-1111-111111111111"


def test_get_session_not_found(client, _override_db):
    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get",
        new=AsyncMock(return_value=None),
    ):
        r = client.get("/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111")
    assert r.status_code == 404


def test_list_sessions(client, _override_db):
    with patch(
        "api.routes.builder_sessions.BuilderSessionService.list_for_team",
        new=AsyncMock(return_value=[_fake_session()]),
    ):
        r = client.get("/api/v1/builder/sessions")
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1
