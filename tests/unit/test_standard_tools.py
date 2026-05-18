"""Unit tests for engine.tools.standard.markdown_writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.tools.standard.markdown_writer import markdown_writer


@pytest.fixture
def tmp_output_dir(tmp_path, monkeypatch):
    """Point DOCUMENT_OUTPUT_DIR at an isolated tmp dir."""
    monkeypatch.setenv("DOCUMENT_OUTPUT_DIR", str(tmp_path))
    return tmp_path


def test_markdown_writer_writes_into_base_dir(tmp_output_dir: Path) -> None:
    result = markdown_writer(title="My Note", content="# hi")
    assert Path(result["path"]).is_file()
    assert Path(result["path"]).is_relative_to(tmp_output_dir.resolve())
    assert result["byte_size"] > 0


def test_markdown_writer_allows_safe_subdir(tmp_output_dir: Path) -> None:
    result = markdown_writer(title="Note", content="x", subdir="reports/q1")
    out = Path(result["path"])
    assert out.is_file()
    assert out.is_relative_to(tmp_output_dir.resolve())
    assert "reports/q1" in str(out)


def test_markdown_writer_rejects_parent_traversal(tmp_output_dir: Path) -> None:
    with pytest.raises(ValueError, match="path"):
        markdown_writer(title="Bad", content="x", subdir="../etc")


def test_markdown_writer_rejects_deeply_nested_traversal(
    tmp_output_dir: Path,
) -> None:
    with pytest.raises(ValueError, match="path"):
        markdown_writer(title="Bad", content="x", subdir="ok/../../etc")


def test_markdown_writer_rejects_absolute_path(tmp_output_dir: Path) -> None:
    with pytest.raises(ValueError, match="path"):
        markdown_writer(title="Bad", content="x", subdir="/etc")


def test_markdown_writer_rejects_home_expansion(tmp_output_dir: Path) -> None:
    with pytest.raises(ValueError, match="path"):
        markdown_writer(title="Bad", content="x", subdir="~/notes")


def test_markdown_writer_rejects_null_byte(tmp_output_dir: Path) -> None:
    with pytest.raises(ValueError, match="path"):
        markdown_writer(title="Bad", content="x", subdir="ok\x00etc")
