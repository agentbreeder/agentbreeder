"""Unit tests for BuilderSessionService.run_interview_turn (Wave 3 C4).

Drives the interview turn directly with a fake provider and a monkeypatched
run_chat_turn_stream so the real frame-emission + history-threading +
durable-commit logic is exercised without a database or Claude API.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.builder_session_service import BuilderSessionService, SessionEventBus


@pytest.mark.asyncio
async def test_run_interview_turn_threads_history_and_persists(monkeypatch):
    from engine.agent_chat_builder import ChatStreamEvent, ChatTurnResult

    async def _fake_stream(provider, history):
        yield ChatStreamEvent(type="token", text="Hi")
        yield ChatStreamEvent(
            type="done",
            result=ChatTurnResult(
                assistant_message="Hi there",
                agent_yaml="name: x",
                valid=True,
                errors=[],
            ),
        )

    monkeypatch.setattr("api.services.builder_session_service.run_chat_turn_stream", _fake_stream)

    db = AsyncMock()
    svc = BuilderSessionService(db, SessionEventBus())
    sess = SimpleNamespace(state={"history": [], "agent_yaml": None, "files": {}})

    frames = [f async for f in svc.run_interview_turn(sess, object(), "build a bot")]

    events = [f["event"] for f in frames]
    assert "token" in events
    assert "spec_update" in events  # agent_yaml present
    assert events[-1] == "done"
    # history threaded: user first, assistant last
    assert sess.state["history"][0] == {"role": "user", "content": "build a bot"}
    assert sess.state["history"][-1]["role"] == "assistant"
    assert sess.state["history"][-1]["content"] == "Hi there"
    assert sess.state["agent_yaml"] == "name: x"
    db.commit.assert_awaited()  # Fix 1: durable commit
