import pytest

from engine.coding_agent.engines import ClaudeAgentEngine, CodexEngine, engine_for
from tests.unit.fakes.fake_provider import FakeProvider, text
from tests.unit.fakes.fake_sandbox import FakeSandbox


def test_engine_for_claude():
    e = engine_for("claude", provider=FakeProvider([]))
    assert isinstance(e, ClaudeAgentEngine)
    assert e.name == "claude"


def test_engine_for_codex():
    e = engine_for("codex", provider=FakeProvider([]))
    assert isinstance(e, CodexEngine)
    assert e.name == "codex"


def test_engine_for_unknown_raises():
    with pytest.raises(ValueError):
        engine_for("bard", provider=FakeProvider([]))


@pytest.mark.asyncio
async def test_engine_run_streams_done():
    provider = FakeProvider([[text("hi")]])
    engine = engine_for("claude", provider=provider)
    events = [e async for e in engine.run("build", [], FakeSandbox())]
    assert events[-1].type == "done"
