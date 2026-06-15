"""Gated e2e: the Codex engine writes real code against a live OpenAI model.

Skipped unless AGENTBREEDER_E2E_OPENAI_KEY is set (BYO key, costs tokens).
Mirrors test_builder_eject_e2e.py (the Claude path) for engine parity.
"""

from __future__ import annotations

import os

import pytest

from engine.coding_agent.base import AgentBounds
from engine.coding_agent.engines import engine_for
from engine.providers.models import ProviderConfig, ProviderType
from engine.providers.openai_provider import OpenAIProvider
from engine.sandbox.local import LocalSandbox

_KEY = os.environ.get("AGENTBREEDER_E2E_OPENAI_KEY")

pytestmark = pytest.mark.skipif(not _KEY, reason="AGENTBREEDER_E2E_OPENAI_KEY not set")


@pytest.mark.asyncio
async def test_eject_writes_agent_py_against_live_codex():
    provider = OpenAIProvider(ProviderConfig(provider_type=ProviderType.openai, api_key=_KEY))
    sandbox = LocalSandbox()
    try:
        engine = engine_for("codex", provider=provider)
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
