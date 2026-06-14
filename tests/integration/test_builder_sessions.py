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
