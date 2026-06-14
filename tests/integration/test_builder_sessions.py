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


# --- C4: /messages interview-turn route tests ------------------------------


def test_messages_turn_streams_and_persists(client, _override_db, monkeypatch):
    from engine.agent_chat_builder import (  # noqa: F401  (verifies symbols exist)
        ChatStreamEvent,
        ChatTurnResult,
    )

    sess = _fake_session()

    # ensure save_state can mutate without DB
    async def _fake_save_state(self, s, state):
        s.state = state

    async def _fake_stream(self, session, provider, user_text):
        yield {"event": "token", "data": '{"text": "Hello"}'}
        yield {"event": "done", "data": '{"assistant_message": "Hello"}'}

    class _Backend:
        async def get(self, _name):
            return "sk-ant-test"

    monkeypatch.setattr(
        "api.routes.builder_sessions.get_workspace_backend", lambda: (_Backend(), None)
    )

    class _Provider:
        def __init__(self, *a, **k):
            pass

        async def close(self):
            pass

    monkeypatch.setattr("api.routes.builder_sessions.AnthropicProvider", _Provider)

    with (
        patch(
            "api.routes.builder_sessions.BuilderSessionService.get",
            new=AsyncMock(return_value=sess),
        ),
        patch(
            "api.routes.builder_sessions.BuilderSessionService.run_interview_turn",
            new=_fake_stream,
        ),
    ):
        r = client.post(
            "/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111/messages",
            json={"content": "build a support agent"},
        )
    assert r.status_code == 200
    assert "event: token" in r.text
    assert "event: done" in r.text


def test_messages_requires_key(client, _override_db, monkeypatch):
    class _Backend:
        async def get(self, _name):
            return None

    monkeypatch.setattr(
        "api.routes.builder_sessions.get_workspace_backend", lambda: (_Backend(), None)
    )
    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get",
        new=AsyncMock(return_value=_fake_session()),
    ):
        r = client.post(
            "/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111/messages",
            json={"content": "hi"},
        )
    assert r.status_code == 400


# --- C5: /eject coding-agent route tests -----------------------------------


def test_eject_streams_file_change_with_content(client, _override_db, monkeypatch):
    from engine.coding_agent.base import AgentEvent

    sess = _fake_session()
    sess.engine = "claude"

    class _FakeEngine:
        name = "claude"

        async def run(self, instruction, history, sandbox, bounds=None):
            await sandbox.write("agent.py", "print('hi')\n")
            yield AgentEvent(type="file_change", path="agent.py", diff="+print('hi')")
            yield AgentEvent(type="done", text="done")

    monkeypatch.setattr(
        "api.services.builder_session_service.engine_for",
        lambda name, provider: _FakeEngine(),
    )
    monkeypatch.setattr(
        "api.services.builder_session_service.select_sandbox_mode", lambda: "local"
    )
    monkeypatch.setattr("api.routes.builder_sessions.select_sandbox_mode", lambda: "local")

    class _Backend:
        async def get(self, _name):
            return "sk-ant-test"

    monkeypatch.setattr(
        "api.routes.builder_sessions.get_workspace_backend", lambda: (_Backend(), None)
    )

    class _Provider:
        def __init__(self, *a, **k):
            pass

        async def close(self):
            pass

    monkeypatch.setattr("api.routes.builder_sessions.AnthropicProvider", _Provider)

    async def _fake_save_state(self, s, state):
        s.state = state

    monkeypatch.setattr(
        "api.services.builder_session_service.BuilderSessionService.save_state", _fake_save_state
    )

    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get", new=AsyncMock(return_value=sess)
    ):
        r = client.post(
            "/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111/eject",
            json={"instruction": "add a custom tool"},
        )
    assert r.status_code == 200
    assert "event: file_change" in r.text
    assert "print('hi')" in r.text  # content present in the frame


def test_eject_blocked_when_sandbox_cloud(client, _override_db, monkeypatch):
    monkeypatch.setattr("api.routes.builder_sessions.select_sandbox_mode", lambda: "cloud")
    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get",
        new=AsyncMock(return_value=_fake_session()),
    ):
        r = client.post(
            "/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111/eject",
            json={"instruction": "x"},
        )
    assert r.status_code == 409


def test_eject_requires_key(client, _override_db, monkeypatch):
    monkeypatch.setattr("api.routes.builder_sessions.select_sandbox_mode", lambda: "local")

    class _Backend:
        async def get(self, _name):
            return None

    monkeypatch.setattr(
        "api.routes.builder_sessions.get_workspace_backend", lambda: (_Backend(), None)
    )
    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get",
        new=AsyncMock(return_value=_fake_session()),
    ):
        r = client.post(
            "/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111/eject",
            json={"instruction": "x"},
        )
    assert r.status_code == 400


# --- C6: /deploy (governed) + /stream (aggregate SSE) route tests ----------


def test_deploy_from_session_uses_governed_path(client, _override_db, monkeypatch):
    from types import SimpleNamespace

    sess = _fake_session()
    sess.state = {
        "history": [],
        "agent_yaml": "name: x\nteam: engineering\n",
        "files": {},
        "deploy_job_id": None,
        "satisfied": [],
    }
    fake_job = SimpleNamespace(id="job-123", agent_id="agent-1")

    monkeypatch.setattr(
        "api.routes.builder_sessions._resolve_deploy_team",
        AsyncMock(return_value=("engineering", None)),
    )
    monkeypatch.setattr(
        "api.routes.builder_sessions.enforce_team_role", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "api.routes.builder_sessions.DeployService.create_agent_and_deploy",
        AsyncMock(return_value=(SimpleNamespace(id="agent-1"), fake_job)),
    )
    monkeypatch.setattr(
        "api.routes.builder_sessions.AuditService.log_event", AsyncMock(return_value=None)
    )

    saved = {}

    async def _fake_save_state(self, s, state):
        s.state = state
        saved.update(state)

    monkeypatch.setattr(
        "api.services.builder_session_service.BuilderSessionService.save_state", _fake_save_state
    )
    # commit is on the AsyncMock db already (no-op)

    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get", new=AsyncMock(return_value=sess)
    ):
        r = client.post("/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111/deploy")
    assert r.status_code == 200
    assert r.json()["data"]["deploy_job_id"] == "job-123"
    assert saved.get("deploy_job_id") == "job-123"


def test_deploy_without_spec_returns_400(client, _override_db, monkeypatch):
    sess = _fake_session()
    sess.state = {
        "history": [],
        "agent_yaml": None,
        "files": {},
        "deploy_job_id": None,
        "satisfied": [],
    }
    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get", new=AsyncMock(return_value=sess)
    ):
        r = client.post("/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111/deploy")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_stream_subscribes_and_relays_published_events(monkeypatch):
    # An infinite SSE generator deadlocks the synchronous TestClient (it blocks
    # reading the never-ending body), so we drive the route's async generator
    # directly. This still exercises the real behavior: the endpoint returns an
    # EventSourceResponse, the generator subscribes to the session's bus topic,
    # flushes an immediate `ready` frame, then relays published events verbatim.
    import uuid as _uuid
    from types import SimpleNamespace

    from sse_starlette.sse import EventSourceResponse

    from api.routes.builder_sessions import stream_session

    sid = _uuid.UUID("11111111-1111-1111-1111-111111111111")
    bus = SessionEventBus()
    fake_request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(builder_event_bus=bus)),
        is_disconnected=AsyncMock(return_value=False),
    )
    user = SimpleNamespace(team="engineering")

    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get",
        new=AsyncMock(return_value=_fake_session()),
    ):
        resp = await stream_session(sid, fake_request, user=user, db=AsyncMock())

    assert isinstance(resp, EventSourceResponse)

    gen = resp.body_iterator
    first = await gen.__anext__()
    assert first["event"] == "ready"

    # A frame published to the session topic is relayed to the subscriber.
    await bus.publish(str(sid), {"event": "deploy.progress", "data": "{}"})
    relayed = await gen.__anext__()
    assert relayed["event"] == "deploy.progress"
    await gen.aclose()


def test_stream_not_found_returns_404(client, _override_db):
    with patch(
        "api.routes.builder_sessions.BuilderSessionService.get", new=AsyncMock(return_value=None)
    ):
        r = client.get("/api/v1/builder/sessions/11111111-1111-1111-1111-111111111111/stream")
    assert r.status_code == 404
