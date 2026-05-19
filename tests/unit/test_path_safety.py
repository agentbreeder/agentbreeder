"""Tests for engine.util.path_safety."""

from __future__ import annotations

import pytest

from engine.util.path_safety import UnsafePathError, safe_relative_subdir


def test_allows_empty_string() -> None:
    assert safe_relative_subdir("") == ""


def test_allows_simple_dir() -> None:
    assert safe_relative_subdir("reports") == "reports"


def test_allows_nested_dir() -> None:
    assert safe_relative_subdir("reports/q1/v2") == "reports/q1/v2"


def test_rejects_absolute_unix_path() -> None:
    with pytest.raises(UnsafePathError, match="absolute"):
        safe_relative_subdir("/etc")


def test_rejects_home_expansion() -> None:
    with pytest.raises(UnsafePathError, match="home"):
        safe_relative_subdir("~/notes")


def test_rejects_parent_traversal() -> None:
    with pytest.raises(UnsafePathError, match="traversal"):
        safe_relative_subdir("../etc")


def test_rejects_nested_parent_traversal() -> None:
    with pytest.raises(UnsafePathError, match="traversal"):
        safe_relative_subdir("ok/../../etc")


def test_rejects_null_byte() -> None:
    with pytest.raises(UnsafePathError, match="null"):
        safe_relative_subdir("ok\x00etc")


def test_error_message_includes_offending_input() -> None:
    with pytest.raises(UnsafePathError, match="'/etc'"):
        safe_relative_subdir("/etc")
