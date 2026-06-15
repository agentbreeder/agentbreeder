"""Gated end-to-end test: the coding agent writes real code against a live model.

Skipped unless AGENTBREEDER_E2E_ANTHROPIC_KEY is set (BYO key, costs tokens).
Proves the provider-loop (B0-B3) genuinely produces files end-to-end, not just
against fakes. One transcript per engine could be added later (Codex needs an
OpenAI key); this covers the Claude path.
"""

from __future__ import annotations

import os

import pytest

from engine.coding_agent.base import AgentBounds
from engine.coding_agent.engines import engine_for
from engine.providers.anthropic_provider import AnthropicProvider
from engine.providers.models import ProviderConfig, ProviderType
from engine.sandbox.local import LocalSandbox

_KEY = os.environ.get("AGENTBREEDER_E2E_ANTHROPIC_KEY")

pytestmark = pytest.mark.skipif(not _KEY, reason="AGENTBREEDER_E2E_ANTHROPIC_KEY not set")


@pytest.mark.asyncio
async def test_eject_writes_agent_py_against_live_claude():
    provider = AnthropicProvider(
        ProviderConfig(provider_type=ProviderType.anthropic, api_key=_KEY)
    )
    sandbox = LocalSandbox()
    try:
        engine = engine_for("claude", provider=provider)
        instruction = (
            "Create a minimal Python agent project for this spec:\n"
            "name: hello-agent\nframework: custom\n"
            "Write agent.py with a `run(input: str) -> str` function that echoes "
            "the input, and tools/__init__.py. Use write_file for each file, then stop."
        )
        file_changes: list[str] = []
        async for evt in engine.run(instruction, [], sandbox, AgentBounds(max_turns=6)):
            if evt.type == "file_change":
                file_changes.append(evt.path)
        assert any(p.endswith("agent.py") for p in file_changes), file_changes
        files = await sandbox.list(".")
        assert any(p.endswith("agent.py") for p in files), files
    finally:
        await sandbox.close()
        await provider.close()
