"""Unit tests for ToolRegistryMetadata validator (W4-12)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from registry.tools import ToolRegistryMetadata


class TestToolRegistryMetadata:
    def test_valid_minimum(self) -> None:
        m = ToolRegistryMetadata(name="search", description="Web search tool")
        assert m.name == "search"
        assert m.description == "Web search tool"
        assert m.tool_type == "mcp_server"
        assert m.source == "manual"

    def test_strips_whitespace_on_name(self) -> None:
        m = ToolRegistryMetadata(name="  search  ", description="x")
        assert m.name == "search"

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValidationError):
            ToolRegistryMetadata(name="", description="x")

    def test_rejects_whitespace_only_name(self) -> None:
        with pytest.raises(ValidationError, match="non-empty"):
            ToolRegistryMetadata(name="   ", description="x")

    def test_allows_empty_description_for_backward_compat(self) -> None:
        # Empty descriptions are tolerated (warning logged), not rejected.
        m = ToolRegistryMetadata(name="search", description="")
        assert m.description == ""

    def test_rejects_non_string_description(self) -> None:
        with pytest.raises(ValidationError):
            ToolRegistryMetadata(name="search", description=123)  # type: ignore[arg-type]

    def test_accepts_valid_schema(self) -> None:
        m = ToolRegistryMetadata(
            name="search",
            description="x",
            schema_definition={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        )
        assert m.schema_definition is not None

    def test_rejects_invalid_schema_type(self) -> None:
        with pytest.raises(ValidationError, match="JSON Schema"):
            ToolRegistryMetadata(
                name="search",
                description="x",
                schema_definition={"type": "magical"},
            )

    def test_rejects_non_list_required(self) -> None:
        with pytest.raises(ValidationError, match="required"):
            ToolRegistryMetadata(
                name="search",
                description="x",
                schema_definition={"type": "object", "required": "not-a-list"},
            )

    def test_rejects_required_with_non_string_elements(self) -> None:
        with pytest.raises(ValidationError, match="required"):
            ToolRegistryMetadata(
                name="search",
                description="x",
                schema_definition={"type": "object", "required": [1, 2]},
            )

    def test_rejects_non_dict_properties(self) -> None:
        with pytest.raises(ValidationError, match="properties"):
            ToolRegistryMetadata(
                name="search",
                description="x",
                schema_definition={"type": "object", "properties": []},
            )

    def test_accepts_http_endpoint(self) -> None:
        m = ToolRegistryMetadata(
            name="search", description="x", endpoint="https://api.example.com/v1"
        )
        assert m.endpoint == "https://api.example.com/v1"

    def test_rejects_unparseable_endpoint(self) -> None:
        with pytest.raises(ValidationError, match="URL"):
            ToolRegistryMetadata(name="search", description="x", endpoint="not a url")

    def test_rejects_non_http_scheme(self) -> None:
        with pytest.raises(ValidationError, match="scheme"):
            ToolRegistryMetadata(name="search", description="x", endpoint="ftp://example.com/path")

    def test_endpoint_none_allowed(self) -> None:
        m = ToolRegistryMetadata(name="search", description="x", endpoint=None)
        assert m.endpoint is None

    def test_endpoint_empty_string_allowed(self) -> None:
        m = ToolRegistryMetadata(name="search", description="x", endpoint="")
        assert m.endpoint in ("", None)
