"""_make_sandbox selection + eject frame contract (W4)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services import builder_session_service as svc
from engine.coding_agent.base import AgentEvent
from engine.sandbox.cloud import CloudSandbox
from engine.sandbox.local import LocalSandbox


def test_make_sandbox_local(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "local")
    sb = svc._make_sandbox()
    assert isinstance(sb, LocalSandbox)


def test_make_sandbox_cloud_returns_cloud_sandbox(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "cloud")
    monkeypatch.setenv("AGENTBREEDER_SANDBOX_BACKEND", "fake")
    sb = svc._make_sandbox()
    assert isinstance(sb, CloudSandbox)


def test_make_sandbox_disabled_raises(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "disabled")
    with pytest.raises(svc.CloudSandboxUnavailable):
        svc._make_sandbox()


class _FakeEngine:
    name = "claude"

    async def run(self, instruction, history, sandbox, bounds=None):
        yield AgentEvent(type="done", text="built it")


@pytest.mark.asyncio
async def test_eject_complete_frame_has_code_and_sandbox_seconds(monkeypatch):
    monkeypatch.setenv("AGENTBREEDER_SANDBOX", "cloud")
    monkeypatch.setenv("AGENTBREEDER_SANDBOX_BACKEND", "fake")
    monkeypatch.setattr(svc, "engine_for", lambda name, provider: _FakeEngine())

    db = AsyncMock()
    service = svc.BuilderSessionService(db, svc.SessionEventBus())
    sess = MagicMock()
    sess.state = {"history": [], "files": {}, "agent_yaml": None}

    frames = [
        f
        async for f in service.run_eject(
            sess, provider=None, instruction="x", engine_name="claude"
        )
    ]
    complete = [f for f in frames if f["event"] == "complete"][0]
    payload = json.loads(complete["data"])
    assert payload["code"] == "ok"
    assert "sandbox_seconds" in payload
    assert payload["sandbox_seconds"] >= 0.0
