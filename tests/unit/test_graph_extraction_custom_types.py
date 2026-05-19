"""HR-3 / #405: custom entity types threaded into the entity-extraction prompt."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.services.graph_extraction import (
    _custom_types_hint,
    _entity_type_enum,
    extract_entities,
)


def test_entity_type_enum_built_ins_only() -> None:
    assert _entity_type_enum(None) == "organization|person|concept|location|event|other"
    assert _entity_type_enum([]) == "organization|person|concept|location|event|other"


def test_entity_type_enum_appends_custom_types() -> None:
    enum_str = _entity_type_enum(
        [{"name": "CONTRACT", "description": "Legal agreements"}, {"name": "PARTY"}]
    )
    assert "organization" in enum_str
    assert "CONTRACT" in enum_str
    assert "PARTY" in enum_str


def test_entity_type_enum_skips_missing_name() -> None:
    enum_str = _entity_type_enum([{"description": "no name"}, {"name": "GOOD"}])
    assert "GOOD" in enum_str
    assert "no name" not in enum_str


def test_custom_types_hint_empty() -> None:
    assert _custom_types_hint(None) == ""
    assert _custom_types_hint([]) == ""


def test_custom_types_hint_renders_with_descriptions() -> None:
    hint = _custom_types_hint(
        [
            {"name": "DIAGNOSIS", "description": "ICD-10 codes and diagnoses"},
            {"name": "MEDICATION"},
        ]
    )
    assert "DIAGNOSIS: ICD-10 codes and diagnoses" in hint
    assert "- MEDICATION" in hint
    assert "Additional domain-specific categories" in hint


@pytest.mark.asyncio
async def test_extract_entities_forwards_custom_types_to_claude() -> None:
    """The LLM call must receive a prompt that lists the user-supplied types."""
    captured: dict[str, object] = {}

    async def fake_claude(text, model, custom_types=None):  # noqa: ANN001
        captured["text"] = text
        captured["custom_types"] = custom_types
        return {"entities": [], "relationships": []}

    with patch("api.services.graph_extraction._call_claude", new=AsyncMock(side_effect=fake_claude)):
        custom = [{"name": "STATUTE", "description": "Specific laws"}]
        # Fresh cache so we actually call the patched LLM.
        await extract_entities(
            "Some legal text.",
            model="claude-sonnet-4",
            cache={},
            custom_types=custom,
        )

    assert captured["custom_types"] == [{"name": "STATUTE", "description": "Specific laws"}]


@pytest.mark.asyncio
async def test_extract_entities_cache_key_separates_custom_types() -> None:
    """Same text + different custom_types must hit the LLM twice, not collide."""
    calls = 0

    async def fake_claude(text, model, custom_types=None):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return {"entities": [], "relationships": []}

    cache: dict = {}
    with patch("api.services.graph_extraction._call_claude", new=AsyncMock(side_effect=fake_claude)):
        await extract_entities("same text", cache=cache, custom_types=[{"name": "A"}])
        await extract_entities("same text", cache=cache, custom_types=[{"name": "B"}])
        # Repeat first → must hit cache (no new call).
        await extract_entities("same text", cache=cache, custom_types=[{"name": "A"}])

    assert calls == 2


def test_schema_accepts_custom_types() -> None:
    """The agent.schema.json now allows entity_extraction.custom_types under knowledge_bases."""
    import json
    from pathlib import Path

    import jsonschema

    schema_path = Path(__file__).resolve().parents[2] / "engine/schema/agent.schema.json"
    schema = json.loads(schema_path.read_text())

    minimal = {
        "name": "domain-agent",
        "version": "0.1.0",
        "team": "platform",
        "owner": "owner@example.com",
        "framework": "claude_sdk",
        "model": {"primary": "claude-sonnet-4"},
        "deploy": {"cloud": "local"},
        "knowledge_bases": [
            {
                "ref": "kb/legal-docs",
                "entity_extraction": {
                    "custom_types": [
                        {"name": "CONTRACT", "description": "Legal agreements"},
                        {"name": "PARTY"},
                    ]
                },
            }
        ],
    }
    jsonschema.validate(minimal, schema)
