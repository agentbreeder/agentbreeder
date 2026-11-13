"""`registry fork REF [--as NAME] [--version X.Y.Z]` (#825, M9a; console §8.4)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentbreeder import cli, registry
from agentbreeder.registry import Ref, RegistryError

SHIPPED = registry.BUILTIN_ROOT  # the real built-in registry, captured before any monkeypatch
ECHO = Ref.parse("service/echo@1.0.0")


def make_version(root: Path, kind: str, name: str, version: str, files: dict) -> Path:
    directory = root / kind / name / version
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text(
        f"# a comment that a fork keeps\nkind: {kind}\nname: {name}\nversion: {version}\n"
    )
    for relative, content in files.items():
        (directory / relative).write_text(content)
    return directory


def files_of(directory: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project that searches only the shipped built-in registry."""
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A project with an AGENTBREEDER_REGISTRY_PATH root and an empty built-in root."""
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    shared = tmp_path / "shared"
    make_version(shared, "prompt", "greeting", "1.0.0", {"prompt.md": "Hello.\n"})
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    project = tmp_path / "project"
    project.mkdir()
    return project, shared


# -- built-in ------------------------------------------------------------------------------------


def test_forking_a_builtin_service_gives_an_editable_project_copy(project, tmp_path):
    """The M9a acceptance: the fork is a project version, and editing a copy of it and
    publishing a bump gives a new resolvable version; the built-in is untouched."""
    before = files_of(SHIPPED / ECHO.relative())
    result = registry.fork_version(ECHO, project, version="1.0.1")
    target = project / "registry/service/echo/1.0.1"
    assert result["ref"] == "service/echo@1.0.1" and result["source_ref"] == str(ECHO)
    assert result["root_kind"] == "project" and result["path"] == str(target.resolve())
    assert result["runs_code"] is True and result["dry_run"] is False
    assert result["source"] == str((SHIPPED / ECHO.relative()).resolve())
    assert list(project.glob("registry/.import-*")) == []

    copied = files_of(target)
    manifest = copied.pop("manifest.yaml").decode()
    original = before.copy()
    original_manifest = original.pop("manifest.yaml").decode()
    assert copied == original  # every other file byte for byte
    assert manifest == original_manifest.replace("name: echo", 'name: "echo"').replace(
        "version: 1.0.0", 'version: "1.0.1"'
    )

    search = registry.roots(project)
    hit = registry.resolve(Ref.parse("service/echo@1.0.1"), search)
    assert hit.root == project / "registry"
    shown = registry.show(Ref.parse("service/echo@1.0.1"), search, project)
    assert shown["sha256"] == result["sha256"]
    assert files_of(SHIPPED / ECHO.relative()) == before
    assert registry.verify(search) == []

    # Edit = copy the version, change it, bump, publish (versions are immutable).
    draft = tmp_path / "draft"
    shutil.copytree(target, draft)
    manifest_path = draft / "manifest.yaml"
    manifest_path.write_text(manifest_path.read_text().replace('"1.0.1"', '"1.0.2"'))
    (draft / "service.yaml").write_text((draft / "service.yaml").read_text() + "# changed\n")
    ref, _, _ = registry.publish(draft, project / "registry")
    assert str(ref) == "service/echo@1.0.2"
    assert registry.resolve(ref, search).root == project / "registry"


def test_fork_under_a_new_name_keeps_the_version(project):
    result = registry.fork_version(ECHO, project, name="my-echo")
    assert result["ref"] == "service/my-echo@1.0.0"
    target = project / "registry/service/my-echo/1.0.0"
    assert registry.read_manifest(target, Ref.parse("service/my-echo@1.0.0"))["name"] == "my-echo"


def test_fork_to_a_version_the_builtin_has_is_a_conflict(project):
    with pytest.raises(RegistryError) as error:
        registry.fork_version(
            Ref.parse("service/postgres-pgvector@1.0.0"), project, version="1.1.0"
        )
    assert error.value.code == "REF_CONFLICT"
    assert not (project / "registry/service/postgres-pgvector").exists()


@pytest.mark.parametrize(
    "options",
    [{}, {"name": "echo"}, {"version": "1.0.0"}, {"name": "echo", "version": "1.0.0"}],
)
def test_an_unchanged_fork_is_refused(project, options):
    with pytest.raises(RegistryError) as error:
        registry.fork_version(ECHO, project, **options)
    assert error.value.code == "FORK_UNCHANGED"
    assert "--as NAME or --version X.Y.Z" in str(error.value)
    assert not (project / "registry").exists()


@pytest.mark.parametrize("options", [{}, {"version": "1.0.1"}])
def test_fork_of_an_unknown_ref(project, options):
    with pytest.raises(RegistryError) as error:
        registry.fork_version(Ref.parse("service/nope@1.0.0"), project, **options)
    assert error.value.code == "REF_NOT_FOUND"  # reported before FORK_UNCHANGED
    assert not (project / "registry").exists()


@pytest.mark.parametrize(
    ("options", "code"),
    [
        ({"name": ""}, "REF_INVALID"),
        ({"name": "Bad"}, "REF_INVALID"),
        ({"version": "1.0"}, "REF_UNPINNED"),
    ],
)
def test_fork_rejects_a_bad_name_or_version(project, options, code):
    with pytest.raises(RegistryError) as error:
        registry.fork_version(ECHO, project, **options)
    assert error.value.code == code
    assert not (project / "registry/service").exists()


# -- path and project roots -------------------------------------------------------------------


def test_fork_from_a_registry_path_root(isolated):
    project, shared = isolated
    result = registry.fork_version(Ref.parse("prompt/greeting@1.0.0"), project, version="1.1.0")
    target = project / "registry/prompt/greeting/1.1.0"
    assert result["source_ref"] == "prompt/greeting@1.0.0" and result["runs_code"] is False
    assert (target / "manifest.yaml").read_text().startswith("# a comment that a fork keeps\n")
    assert (target / "prompt.md").read_text() == "Hello.\n"
    assert (shared / "prompt/greeting/1.0.0").is_dir()


def test_fork_within_the_project(isolated):
    project, _ = isolated
    make_version(project / "registry", "prompt", "local", "1.0.0", {"prompt.md": "Hi.\n"})
    result = registry.fork_version(Ref.parse("prompt/local@1.0.0"), project, name="local-v2")
    assert result["ref"] == "prompt/local-v2@1.0.0"
    assert (project / "registry/prompt/local-v2/1.0.0/prompt.md").read_text() == "Hi.\n"


def test_fork_refuses_a_source_that_changed_after_it_was_resolved(isolated, monkeypatch):
    """`place` re-reads the manifest from its staged copy; a source that now declares another
    ref is refused before anything is written."""
    project, shared = isolated
    other = make_version(shared, "prompt", "other", "2.0.0", {"prompt.md": "Other.\n"})
    ref = Ref.parse("prompt/greeting@1.0.0")
    monkeypatch.setattr(registry, "resolve", lambda r, s: registry.Resolved(r, other, shared))
    with pytest.raises(RegistryError) as error:
        registry.fork_version(ref, project, version="1.1.0")
    assert error.value.code == "MANIFEST_INVALID"
    assert "now declares prompt/other@2.0.0, not prompt/greeting@1.0.0" in str(error.value)
    assert list((project / "registry").iterdir()) == []  # staging removed, nothing written


def test_place_refuses_a_staged_manifest_that_is_not_the_source_ref(isolated):
    project, shared = isolated
    source = shared / "prompt/greeting/1.0.0"
    for dry_run in (True, False):
        with pytest.raises(RegistryError) as error:
            registry.place(
                source,
                project / "registry",
                version="1.1.0",
                dry_run=dry_run,
                source_ref=Ref.parse("prompt/greeting@0.9.0"),
            )
        assert error.value.code == "MANIFEST_INVALID"
    assert list((project / "registry").iterdir()) == []
    ok = registry.place(
        source,
        project / "registry",
        version="1.1.0",
        source_ref=Ref.parse("prompt/greeting@1.0.0"),
    )
    assert ok["ref"] == "prompt/greeting@1.1.0"


def test_fork_dry_run_and_expect_digest(isolated):
    project, shared = isolated
    ref = Ref.parse("prompt/greeting@1.0.0")
    preview = registry.fork_version(ref, project, version="1.1.0", dry_run=True)
    assert preview["dry_run"] is True and not (project / "registry").exists()
    assert {entry["path"] for entry in preview["files"]} == {"manifest.yaml", "prompt.md"}

    (shared / "prompt/greeting/1.0.0/prompt.md").write_text("Changed after review.\n")
    with pytest.raises(RegistryError) as error:
        registry.fork_version(ref, project, version="1.1.0", expect_digest=preview["sha256"])
    assert error.value.code == "DIGEST_MISMATCH"
    assert not (project / "registry/prompt").exists()

    again = registry.fork_version(ref, project, version="1.1.0", dry_run=True)
    done = registry.fork_version(ref, project, version="1.1.0", expect_digest=again["sha256"])
    assert done["sha256"] == again["sha256"] and done["dry_run"] is False


# -- CLI -----------------------------------------------------------------------------------------


def test_cli_fork_text_json_and_errors(project, capsys, monkeypatch):
    monkeypatch.chdir(project)
    assert cli.main(["registry", "fork", str(ECHO), "--version", "1.0.1", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Would fork service/echo@1.0.1 sha256:")
    assert (
        "(from service/echo@1.0.0)" in captured.out and "--expect-digest sha256:" in captured.out
    )
    assert "runs inside agents" in captured.err
    assert not (project / "registry").exists()

    assert cli.main(["registry", "fork", str(ECHO), "--as", "mine", "--json"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("{\n")  # indented, as `show` and `import`
    result = json.loads(output)
    assert result["ref"] == "service/mine@1.0.0" and result["root_kind"] == "project"

    assert cli.main(["registry", "fork", str(ECHO)]) == 2
    assert "[FORK_UNCHANGED]" in capsys.readouterr().err
    assert cli.main(["registry", "fork", "service/echo"]) == 2
    assert "[REF_UNPINNED]" in capsys.readouterr().err
    assert (
        cli.main(
            [
                "registry",
                "fork",
                str(ECHO),
                "--version",
                "1.0.1",
                "--expect-digest",
                "sha256:" + "0" * 64,
            ]
        )
        == 2
    )
    assert "[DIGEST_MISMATCH]" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(
            [
                "registry",
                "fork",
                str(ECHO),
                "--version",
                "1.0.1",
                "--dry-run",
                "--expect-digest",
                "sha256:" + "0" * 64,
            ]
        )
    assert "not allowed with" in capsys.readouterr().err


def test_cli_end_to_end_fork_a_builtin(tmp_path):
    """Through a real process: preview the fork of a shipped service, write the reviewed bytes,
    and resolve the project copy."""
    project = tmp_path / "project"
    project.mkdir()
    env = {**os.environ, "AGENTBREEDER_REGISTRY_PATH": ""}

    def run(*args: str):
        return subprocess.run(
            [sys.executable, "-m", "agentbreeder", "registry", *args],
            capture_output=True,
            text=True,
            cwd=project,
            env=env,
            timeout=120,
        )

    args = ("fork", str(ECHO), "--version", "1.0.1")
    preview = run(*args, "--dry-run", "--json")
    assert preview.returncode == 0, preview.stderr
    digest = json.loads(preview.stdout)["sha256"]
    done = run(*args, "--expect-digest", digest, "--json")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["path"] == str(
        (project / "registry/service/echo/1.0.1").resolve()
    )
    shown = run("show", "service/echo@1.0.1", "--json")
    assert json.loads(shown.stdout)["sha256"] == digest
    assert json.loads(shown.stdout)["root_kind"] == "project"
