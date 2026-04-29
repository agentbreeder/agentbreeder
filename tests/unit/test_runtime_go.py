"""Unit tests for the Go runtime builder (Track I phase 1, #165)."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.config_parser import (
    AgentConfig,
    CloudType,
    DeployConfig,
    LanguageType,
    ModelConfig,
    RuntimeConfig,
)
from engine.runtimes.go import GoRuntimeFamily


def _make_config(framework: str = "custom") -> AgentConfig:
    return AgentConfig(
        name="go-test",
        version="0.1.0",
        team="t",
        owner="a@b.c",
        runtime=RuntimeConfig(language=LanguageType.go, framework=framework, version="1.22"),
        model=ModelConfig(primary="claude-sonnet-4-20250514"),
        deploy=DeployConfig(cloud=CloudType.local),
    )


def _write_minimal_go_agent(tmp: Path) -> None:
    (tmp / "main.go").write_text("package main\n\nfunc main() {}\n")
    (tmp / "go.mod").write_text("module example\n\ngo 1.22\n")


class TestGoRuntimeBuilder:
    def test_validate_passes_with_main_and_gomod(self, tmp_path: Path) -> None:
        _write_minimal_go_agent(tmp_path)
        result = GoRuntimeFamily().validate(tmp_path, _make_config())
        assert result.valid is True
        assert result.errors == []

    def test_validate_passes_with_byo_dockerfile(self, tmp_path: Path) -> None:
        (tmp_path / "Dockerfile").write_text("FROM scratch\n")
        result = GoRuntimeFamily().validate(tmp_path, _make_config())
        assert result.valid is True

    def test_validate_fails_without_entrypoint(self, tmp_path: Path) -> None:
        result = GoRuntimeFamily().validate(tmp_path, _make_config())
        assert result.valid is False
        assert any("entry point" in e.lower() or "main.go" in e.lower() for e in result.errors)

    def test_validate_fails_without_gomod(self, tmp_path: Path) -> None:
        (tmp_path / "main.go").write_text("package main\n")
        result = GoRuntimeFamily().validate(tmp_path, _make_config())
        assert result.valid is False
        assert any("go.mod" in e for e in result.errors)

    def test_build_emits_dockerfile_with_distroless_runtime(self, tmp_path: Path) -> None:
        _write_minimal_go_agent(tmp_path)
        image = GoRuntimeFamily().build(tmp_path, _make_config())
        assert "FROM golang:1.22-alpine AS builder" in image.dockerfile_content
        assert "gcr.io/distroless/static" in image.dockerfile_content
        assert "ENV AGENT_NAME=" in image.dockerfile_content
        assert image.tag == "agentbreeder/go-test:0.1.0"

    def test_build_uses_byo_dockerfile_when_present(self, tmp_path: Path) -> None:
        _write_minimal_go_agent(tmp_path)
        (tmp_path / "Dockerfile").write_text("FROM custom:image\nEXPOSE 8080\n")
        image = GoRuntimeFamily().build(tmp_path, _make_config())
        assert "FROM custom:image" in image.dockerfile_content
        assert "distroless" not in image.dockerfile_content

    def test_build_injects_template_when_main_missing(self, tmp_path: Path) -> None:
        # Only go.mod and a Dockerfile-less, main.go-less project. The
        # builder injects the template main.go from engine/runtimes/templates/go/.
        (tmp_path / "go.mod").write_text("module example\n\ngo 1.22\n")
        image = GoRuntimeFamily().build(tmp_path, _make_config())
        injected_main = image.context_dir / "main.go"
        assert injected_main.exists()
        assert "agentbreeder.NewServer" in injected_main.read_text()

    def test_unknown_framework_falls_back_to_custom(self, tmp_path: Path) -> None:
        _write_minimal_go_agent(tmp_path)
        cfg = _make_config(framework="eino")  # not yet shipped
        # Should not raise; falls back silently to "custom" with a warning.
        image = GoRuntimeFamily().build(tmp_path, cfg)
        assert image.tag.startswith("agentbreeder/go-test:")

    def test_get_entrypoint_static_path(self) -> None:
        assert GoRuntimeFamily().get_entrypoint(_make_config()) == "/app/agent"

    def test_get_requirements_empty(self) -> None:
        # Go modules manage their own deps; no Python-style requirements.
        assert GoRuntimeFamily().get_requirements(_make_config()) == []


def test_language_registry_routes_go() -> None:
    from engine.runtimes.registry import get_runtime_from_config

    cfg = _make_config()
    runtime = get_runtime_from_config(cfg)
    assert isinstance(runtime, GoRuntimeFamily)


def test_unsupported_polyglot_language_raises() -> None:
    from engine.runtimes.registry import UnsupportedLanguageError, get_runtime_from_config

    cfg = _make_config()
    cfg.runtime.language = LanguageType.kotlin  # type: ignore[assignment]
    with pytest.raises(UnsupportedLanguageError):
        get_runtime_from_config(cfg)
