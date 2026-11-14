"""`registry publish --json` reports `root_kind: "project"` for a project's own `./registry`
(#898): `--to` resolves to `DIR/registry` and DIR holds `agent.yaml` or `stack.yaml`."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentbreeder import cli, registry


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")


@pytest.fixture
def source(tmp_path):
    return registry.scaffold("prompt", "greeter", tmp_path / "drafts")


def _publish(capsys, *args: str) -> dict:
    assert cli.main(["registry", "publish", *args, "--json"]) == 0
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _project(tmp_path: Path, marker: str = "agent.yaml") -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / marker).write_text("# marker\n")
    return project


@pytest.mark.parametrize("marker", ["agent.yaml", "stack.yaml"])
def test_publish_from_the_project_directory_is_project(
    tmp_path, source, capsys, monkeypatch, marker
):
    project = _project(tmp_path, marker)
    monkeypatch.chdir(project)
    preview = _publish(capsys, str(source), "--dry-run")
    assert preview["root_kind"] == "project"
    done = _publish(capsys, str(source))  # the default --to registry
    assert done["root_kind"] == "project"
    assert done["root"] == str((project / "registry").resolve())
    assert (project / "registry/prompt/greeter/0.1.0").is_dir()


def test_publish_to_an_absolute_project_registry_is_project(tmp_path, source, capsys):
    project = _project(tmp_path)
    result = _publish(capsys, str(source), "--to", str(project / "registry"))
    assert result["root_kind"] == "project"


def test_publish_through_a_symlink_to_a_project_registry_is_project(tmp_path, source, capsys):
    project = _project(tmp_path)
    (project / "registry").mkdir()
    link = tmp_path / "shortcut"
    link.symlink_to(project / "registry", target_is_directory=True)
    result = _publish(capsys, str(source), "--to", str(link), "--dry-run")
    assert result["root_kind"] == "project"


def test_publish_without_a_project_marker_is_path(tmp_path, source, capsys):
    (tmp_path / "plain").mkdir()
    result = _publish(capsys, str(source), "--to", str(tmp_path / "plain/registry"))
    assert result["root_kind"] == "path"


def test_a_marker_that_is_not_a_file_does_not_count(tmp_path, source, capsys):
    (tmp_path / "plain/agent.yaml").mkdir(parents=True)
    result = _publish(capsys, str(source), "--to", str(tmp_path / "plain/registry"))
    assert result["root_kind"] == "path"


def test_a_broken_symlink_marker_does_not_count(tmp_path, source, capsys):
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain/stack.yaml").symlink_to(tmp_path / "missing.yaml")
    result = _publish(capsys, str(source), "--to", str(tmp_path / "plain/registry"))
    assert result["root_kind"] == "path"


def test_a_root_not_named_registry_is_path(tmp_path, source, capsys):
    project = _project(tmp_path)
    result = _publish(capsys, str(source), "--to", str(project / "shared"))
    assert result["root_kind"] == "path"


def test_the_builtin_root_stays_builtin(tmp_path, source, capsys):
    builtin = tmp_path / "builtin"
    result = _publish(capsys, str(source), "--to", str(builtin), "--dry-run")
    assert result["root_kind"] == "builtin"


def test_text_output_is_unchanged(tmp_path, source, capsys, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    assert cli.main(["registry", "publish", str(source)]) == 0
    words = capsys.readouterr().out.split()
    assert words[0] == "prompt/greeter@0.1.0" and words[2].endswith(
        os.path.join("registry", "prompt", "greeter", "0.1.0")
    )


def test_publish_project_unit(tmp_path):
    project = _project(tmp_path)
    assert registry.publish_project(project / "registry") == project.resolve()
    assert registry.publish_project(project / "registry" / "..") is None
    assert registry.publish_project(project / "other") is None


def test_publish_project_is_none_when_undecidable(tmp_path, monkeypatch):
    def broken(self, strict=False):
        raise RuntimeError("Symlink loop")

    monkeypatch.setattr(Path, "resolve", broken)
    assert registry.publish_project(tmp_path / "registry") is None


def test_root_kind_matches_a_relative_and_an_absolute_project_root(tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.chdir(project)
    assert registry.root_kind(Path("registry"), project.resolve()) == "project"
    assert registry.root_kind(project / "registry", Path(".")) == "project"
    assert registry.root_kind(tmp_path / "elsewhere", project) == "path"
