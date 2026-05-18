"""Unit tests for engine.tool_resolver schema validation (W4-07)."""

from __future__ import annotations

import pytest

from engine.tool_resolver import (
    ToolInputValidationError,
    ToolNotFoundError,
    _validate_against_schema,
    validate_tool_input,
)


class TestValidateAgainstSchema:
    """Direct tests of the validator helper."""

    def test_passes_valid_input(self) -> None:
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        _validate_against_schema({"query": "hello"}, schema, "demo")

    def test_rejects_missing_required(self) -> None:
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        with pytest.raises(ToolInputValidationError, match="query"):
            _validate_against_schema({}, schema, "demo")

    def test_rejects_wrong_type(self) -> None:
        schema = {
            "type": "object",
            "properties": {"max_results": {"type": "integer"}},
        }
        with pytest.raises(ToolInputValidationError, match="max_results"):
            _validate_against_schema({"max_results": "not-an-int"}, schema, "demo")

    def test_rejects_non_dict_input(self) -> None:
        schema = {"type": "object", "properties": {}}
        with pytest.raises(ToolInputValidationError, match="must be a dict"):
            _validate_against_schema("string-input", schema, "demo")  # type: ignore[arg-type]

    def test_empty_schema_is_noop(self) -> None:
        _validate_against_schema({"anything": 1}, {}, "demo")
        _validate_against_schema({}, {}, "demo")

    def test_includes_field_path_in_error(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}},
                    "required": ["x"],
                },
            },
            "required": ["nested"],
        }
        with pytest.raises(ToolInputValidationError) as exc_info:
            _validate_against_schema({"nested": {}}, schema, "demo")
        # Path should reference 'nested' (which is missing 'x' inside)
        assert "demo" in str(exc_info.value)

    def test_enum_violation(self) -> None:
        schema = {
            "type": "object",
            "properties": {"depth": {"type": "string", "enum": ["basic", "advanced"]}},
        }
        with pytest.raises(ToolInputValidationError):
            _validate_against_schema({"depth": "extreme"}, schema, "demo")


class TestValidateToolInput:
    """Integration: resolve tool ref and validate against its SCHEMA."""

    def test_rejects_non_tool_ref(self) -> None:
        with pytest.raises(ToolNotFoundError):
            validate_tool_input("not-a-tool-ref", {})

    def test_validates_web_search_query_required(self, monkeypatch) -> None:
        # web_search has SCHEMA requiring "query"
        with pytest.raises(ToolInputValidationError, match="query"):
            validate_tool_input("tools/web-search", {})

    def test_validates_web_search_accepts_valid_input(self) -> None:
        validate_tool_input("tools/web-search", {"query": "hello"})

    def test_validates_markdown_writer_required(self) -> None:
        # markdown_writer has SCHEMA with required fields
        with pytest.raises(ToolInputValidationError):
            validate_tool_input("tools/markdown-writer", {})

    def test_unknown_tool_no_schema_is_noop(self) -> None:
        # Tool exists with no SCHEMA attribute → no validation
        # Use a name that won't resolve to any module
        validate_tool_input("tools/nonexistent-tool-xyz", {"anything": 1})
