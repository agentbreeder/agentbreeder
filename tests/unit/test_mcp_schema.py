"""Unit tests for mcp-server.schema.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).parent.parent.parent / "engine" / "schema" / "mcp-server.schema.json"


def test_schema_is_valid_json() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    assert schema["title"] == "MCP Server Configuration"


def test_schema_validates_valid_config() -> None:
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema not installed")

    schema = json.loads(SCHEMA_PATH.read_text())
    config = {
        "name": "my-search-tools",
        "version": "1.0.0",
        "type": "mcp-server",
        "runtime": {"language": "node", "framework": "mcp-ts", "version": "20"},
        "transport": "http",
        "tools": [
            {
                "name": "search_web",
                "description": "Search the web",
                "schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ],
    }
    jsonschema.validate(instance=config, schema=schema)


def test_schema_rejects_missing_tools() -> None:
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema not installed")

    schema = json.loads(SCHEMA_PATH.read_text())
    config = {
        "name": "my-server",
        "version": "1.0.0",
        "type": "mcp-server",
        "runtime": {"language": "node", "framework": "mcp-ts"},
        # tools is missing
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=config, schema=schema)


def test_schema_rejects_invalid_type() -> None:
    try:
        import jsonschema
    except ImportError:
        pytest.skip("jsonschema not installed")

    schema = json.loads(SCHEMA_PATH.read_text())
    config = {
        "name": "my-server",
        "version": "1.0.0",
        "type": "agent",  # wrong — must be "mcp-server"
        "runtime": {"language": "node", "framework": "mcp-ts"},
        "tools": [{"name": "foo", "description": "bar"}],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=config, schema=schema)
