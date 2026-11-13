"""`registry import`, and `--dry-run`/`--expect-digest` on `import` and `publish` (#825, M9a; S-04,
S-05)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agentbreeder import cli, registry
from agentbreeder.registry import Ref, RegistryError

DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def make_source(directory: Path, kind: str, name: str, version: str, files: dict) -> Path:
    directory.mkdir(parents=True)
    (directory / "manifest.yaml").write_text(
        f"# kept unless the name or version changes\nkind: {kind}\nname: {name}\n"
        f"version: {version}\ndescription: imported {name}\n"
    )
    for relative, content in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return directory


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")  # isolate from shipped
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.fixture
def elsewhere(tmp_path):
    """A prompt version in another directory, as `registry import` finds it."""
    return make_source(
        tmp_path / "elsewhere" / "1.2.0", "prompt", "greeting", "1.2.0", {"prompt.md": "Hello.\n"}
    )


def _import(source: Path, project: Path, **options) -> dict:
    return registry.import_version(source, project, **options)


# -- import --------------------------------------------------------------------------------------


def test_import_makes_the_version_resolvable(project, elsewhere):
    result = _import(elsewhere, project)
    target = project / "registry/prompt/greeting/1.2.0"
    assert result["ref"] == "prompt/greeting@1.2.0" and result["source_ref"] == result["ref"]
    assert result["path"] == str(target.resolve()) and result["root_kind"] == "project"
    assert result["dry_run"] is False and "files" not in result
    assert DIGEST.fullmatch(result["sha256"])
    hit = registry.resolve(Ref.parse("prompt/greeting@1.2.0"), registry.roots(project))
    assert hit.path == target
    # The digest is the one `show` reports for the published version.
    view = registry.show(Ref.parse("prompt/greeting@1.2.0"), registry.roots(project), project)
    assert view["sha256"] == result["sha256"]
    # The source manifest is copied byte for byte when nothing is renamed (comments kept).
    assert (target / "manifest.yaml").read_text() == (elsewhere / "manifest.yaml").read_text()
    assert list(project.glob("registry/.import-*")) == []  # staging is cleaned up


def test_import_as_a_new_name_and_version_rewrites_the_manifest(project, elsewhere):
    result = _import(elsewhere, project, name="welcome", version="2.0.0")
    assert result["ref"] == "prompt/welcome@2.0.0"
    assert result["source_ref"] == "prompt/greeting@1.2.0"
    target = project / "registry/prompt/welcome/2.0.0"
    manifest = registry.read_manifest(target, Ref.parse("prompt/welcome@2.0.0"))
    assert manifest["description"] == "imported greeting"  # other fields kept
    assert registry.copy_and_hash(target) == result["sha256"]
    assert (elsewhere / "manifest.yaml").read_text().startswith("# kept")  # source untouched


def test_import_version_only(project, elsewhere):
    assert _import(elsewhere, project, version="1.2.1")["ref"] == "prompt/greeting@1.2.1"


@pytest.mark.parametrize(
    "options,code",
    [
        ({"name": "Bad_Name"}, "REF_INVALID"),
        ({"version": "1.0"}, "REF_UNPINNED"),
        ({"version": "01.0.0"}, "REF_UNPINNED"),
    ],
)
def test_import_refuses_bad_names_and_versions(project, elsewhere, options, code):
    with pytest.raises(RegistryError) as error:
        _import(elsewhere, project, **options)
    assert error.value.code == code
    assert not (project / "registry" / "prompt").exists()


def test_import_is_immutable(project, elsewhere):
    _import(elsewhere, project)
    with pytest.raises(RegistryError) as error:
        _import(elsewhere, project)
    assert error.value.code == "VERSION_EXISTS"
    with pytest.raises(RegistryError) as error:
        _import(elsewhere, project, dry_run=True)  # a preview says so too
    assert error.value.code == "VERSION_EXISTS"


def test_import_runs_the_publish_safety_checks(project, elsewhere):
    os.symlink("/etc/hosts", elsewhere / "link")
    with pytest.raises(RegistryError, match="symlinks are not allowed"):
        _import(elsewhere, project)
    (elsewhere / "link").unlink()
    os.link(elsewhere / "prompt.md", elsewhere / "hard.md")
    with pytest.raises(RegistryError, match="hardlinks are not allowed"):
        _import(elsewhere, project)
    assert not (project / "registry/prompt/greeting").exists()
    assert list(project.glob("registry/.import-*")) == []


def test_import_refuses_too_many_files(project, elsewhere, monkeypatch):
    monkeypatch.setattr(registry, "MAX_FILES", 2)
    (elsewhere / "extra.md").write_text("x")
    with pytest.raises(RegistryError, match="too large"):
        _import(elsewhere, project)


@pytest.mark.parametrize(
    "manifest,match",
    [
        (None, "manifest.yaml is missing"),
        ("kind: prompt\nname: greeting\n", "needs kind, name and X.Y.Z version"),
        ("kind: [\n", "MANIFEST_INVALID"),
        (b"kind: prompt\xff\n", "not UTF-8"),
    ],
)
def test_import_requires_a_valid_manifest(project, elsewhere, manifest, match):
    path = elsewhere / "manifest.yaml"
    if manifest is None:
        path.unlink()
    elif isinstance(manifest, bytes):
        path.write_bytes(manifest)
    else:
        path.write_text(manifest)
    with pytest.raises(RegistryError, match=match) as error:
        _import(elsewhere, project)
    assert error.value.code == "MANIFEST_INVALID"
    assert ".import-" not in str(error.value)  # errors name the source, never the staging copy


def test_import_of_a_missing_or_file_source(project, tmp_path):
    with pytest.raises(RegistryError, match="must be a real directory"):
        _import(tmp_path / "nope", project)
    (tmp_path / "file").write_text("x")
    with pytest.raises(RegistryError, match="must be a real directory"):
        _import(tmp_path / "file", project)


def test_import_refuses_a_symlinked_kind_or_name_directory(project, elsewhere, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "registry").mkdir()
    os.symlink(outside, project / "registry" / "prompt")
    with pytest.raises(RegistryError, match="must not be a symlink") as error:
        _import(elsewhere, project)
    assert error.value.code == "ENTRY_UNSAFE"
    assert list(outside.iterdir()) == []  # nothing written through the link


def test_import_refuses_a_conflict_with_another_root(project, elsewhere, tmp_path, monkeypatch):
    other = tmp_path / "shared"
    make_source(
        other / "prompt/greeting/1.2.0",
        "prompt",
        "greeting",
        "1.2.0",
        {"prompt.md": "Different\n"},
    )
    monkeypatch.setenv(registry.PATH_ENV, str(other))
    with pytest.raises(RegistryError) as error:
        _import(elsewhere, project)
    assert error.value.code == "REF_CONFLICT"
    assert not (project / "registry/prompt/greeting").exists()


def test_import_of_identical_content_in_another_root_is_allowed(project, elsewhere, monkeypatch):
    other = elsewhere.parent.parent / "shared"
    copy = other / "prompt/greeting/1.2.0"
    copy.parent.mkdir(parents=True)
    registry.copy_and_hash(elsewhere, copy)
    monkeypatch.setenv(registry.PATH_ENV, str(other))
    _import(elsewhere, project)
    registry.resolve(Ref.parse("prompt/greeting@1.2.0"), registry.roots(project))


def test_import_into_a_shipped_tree_is_refused(project, elsewhere, monkeypatch):
    monkeypatch.setattr(registry, "trusted_root", lambda path: True)
    with pytest.raises(RegistryError) as error:
        _import(elsewhere, project)
    assert error.value.code == "REGISTRY_READ_ONLY"
    assert not (project / "registry").exists()


@pytest.mark.parametrize(
    "kind,runs_code",
    [
        ("tool", True),
        ("service", True),
        ("eval", True),
        ("mcp", True),
        ("runtime", True),
        ("prompt", False),
        ("knowledge", False),
    ],
)
def test_code_bearing_kinds_are_marked(project, tmp_path, kind, runs_code):
    """S-05: the console asks for the "this code runs in agents" confirm when `runs_code`."""
    source = make_source(tmp_path / "src", kind, "thing", "1.0.0", {"README.md": "x\n"})
    result = _import(source, project, dry_run=True)
    assert result["runs_code"] is runs_code
    assert registry.runs_code(kind) is runs_code


# -- S-04: preview, then write exactly the reviewed bytes ----------------------------------------


def test_dry_run_writes_nothing_and_returns_the_digest_and_files(project, elsewhere):
    preview = _import(elsewhere, project, name="welcome", dry_run=True)
    assert preview["dry_run"] is True
    assert not (project / "registry").exists()
    paths = {entry["path"]: entry for entry in preview["files"]}
    assert set(paths) == {"manifest.yaml", "prompt.md"}
    assert paths["prompt.md"]["text"] == "Hello.\n"
    assert 'name: "welcome"' in paths["manifest.yaml"]["text"]  # the rewritten manifest is shown
    written = _import(elsewhere, project, name="welcome", expect_digest=preview["sha256"])
    assert written["sha256"] == preview["sha256"]


def test_a_changed_source_after_the_preview_is_refused(project, elsewhere):
    preview = _import(elsewhere, project, dry_run=True)
    (elsewhere / "prompt.md").write_text("Hello, changed after review.\n")
    with pytest.raises(RegistryError) as error:
        _import(elsewhere, project, expect_digest=preview["sha256"])
    assert error.value.code == "DIGEST_MISMATCH"
    assert preview["sha256"] in str(error.value)
    assert not (project / "registry/prompt/greeting").exists()
    assert list(project.glob("registry/.import-*")) == []


def test_a_different_rename_than_the_preview_is_refused(project, elsewhere):
    preview = _import(elsewhere, project, name="welcome", dry_run=True)
    with pytest.raises(RegistryError) as error:
        _import(elsewhere, project, name="hello", expect_digest=preview["sha256"])
    assert error.value.code == "DIGEST_MISMATCH"


def test_an_exec_bit_change_after_the_preview_is_refused(project, elsewhere):
    preview = _import(elsewhere, project, dry_run=True)
    os.chmod(elsewhere / "prompt.md", 0o755)
    with pytest.raises(RegistryError, match="DIGEST_MISMATCH"):
        _import(elsewhere, project, expect_digest=preview["sha256"])


@pytest.mark.parametrize("bad", ["", "sha256:abc", "md5:" + "0" * 64, "sha256:" + "G" * 64])
def test_a_malformed_expected_digest_is_refused(project, elsewhere, bad):
    with pytest.raises(RegistryError) as error:
        _import(elsewhere, project, expect_digest=bad)
    assert error.value.code == "DIGEST_INVALID"


def test_publish_takes_the_same_preview_and_expected_digest(project, tmp_path):
    source = registry.scaffold("tool", "notes", tmp_path)
    root = project / "registry"
    preview = registry.place(source, root, dry_run=True)
    assert preview["ref"] == "tool/notes@0.1.0" and preview["runs_code"] is True
    assert not root.exists()
    (source / "src" / "notes.py").write_text("print('changed')\n")
    with pytest.raises(RegistryError, match="DIGEST_MISMATCH"):
        registry.place(source, root, expect_digest=preview["sha256"])
    fresh = registry.place(source, root, dry_run=True)
    done = registry.place(source, root, expect_digest=fresh["sha256"])
    assert done["sha256"] == fresh["sha256"] == registry.copy_and_hash(root / "tool/notes/0.1.0")


def test_publish_keeps_its_tuple_api(project, tmp_path):
    source = registry.scaffold("prompt", "greeter", tmp_path)
    ref, digest, path = registry.publish(source, project / "registry")
    assert (str(ref), path) == ("prompt/greeter@0.1.0", project / "registry/prompt/greeter/0.1.0")
    assert registry.copy_and_hash(path) == digest


def test_a_version_published_between_check_and_rename_is_version_exists(
    project, elsewhere, monkeypatch
):
    """Two tabs: the second rename fails, and that is reported as "version exists"."""
    real = os.rename

    def racing(source, target):
        Path(target).mkdir(parents=True)
        (Path(target) / "manifest.yaml").write_text("someone else's\n")
        return real(source, target)

    monkeypatch.setattr(registry.os, "rename", racing)
    with pytest.raises(RegistryError) as error:
        _import(elsewhere, project)
    assert error.value.code == "VERSION_EXISTS"
    target = project / "registry/prompt/greeting/1.2.0"
    assert (target / "manifest.yaml").read_text() == "someone else's\n"


# -- CLI -----------------------------------------------------------------------------------------


def test_cli_import_text_and_json(project, elsewhere, capsys, monkeypatch):
    monkeypatch.chdir(project)
    assert cli.main(["registry", "import", str(elsewhere), "--as", "welcome", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("Would import prompt/welcome@1.2.0 sha256:")
    assert "nothing written" in captured.out
    assert "steers the agent" in captured.err
    assert not (project / "registry").exists()
    assert cli.main(["registry", "import", str(elsewhere), "--as", "welcome", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ref"] == "prompt/welcome@1.2.0" and result["root_kind"] == "project"
    assert cli.main(["registry", "show", "prompt/welcome@1.2.0", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sha256"] == result["sha256"]


def test_cli_import_errors(project, elsewhere, capsys):
    args = ["registry", "import", str(elsewhere), "--project", str(project)]
    assert cli.main([*args, "--expect-digest", "sha256:" + "0" * 64]) == 2
    assert "[DIGEST_MISMATCH]" in capsys.readouterr().err
    with pytest.raises(SystemExit) as raised:
        cli.main([*args, "--expect-digest", "nope"])
    assert raised.value.code == 2
    assert "--expect-digest" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main([*args, "--dry-run", "--expect-digest", "sha256:" + "0" * 64])
    assert "not allowed with" in capsys.readouterr().err


def _cli(*args: str, cwd: Path, env: dict | None = None):
    return subprocess.run(
        [sys.executable, "-m", "agentbreeder", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        timeout=120,
    )


def test_cli_end_to_end_preview_then_import(tmp_path):
    """The console's flow through a real process: preview, review, write the reviewed bytes;
    a change in between is refused and nothing is written."""
    project = tmp_path / "project"
    project.mkdir()
    source = make_source(
        tmp_path / "tools" / "orders", "tool", "orders", "1.0.0", {"src/orders.py": "x = 1\n"}
    )
    env = {"AGENTBREEDER_REGISTRY_PATH": ""}
    args = ("registry", "import", str(source), "--version", "1.1.0")
    preview = _cli(*args, "--dry-run", "--json", cwd=project, env=env)
    assert preview.returncode == 0, preview.stderr
    reviewed = json.loads(preview.stdout)
    assert reviewed["runs_code"] is True and reviewed["dry_run"] is True
    assert "runs inside agents" in preview.stderr

    (source / "src/orders.py").write_text("import os\nos.system('id')\n")
    refused = _cli(*args, "--expect-digest", reviewed["sha256"], cwd=project, env=env)
    assert refused.returncode == 2 and "[DIGEST_MISMATCH]" in refused.stderr
    assert not (project / "registry/tool/orders").exists()

    again = json.loads(_cli(*args, "--dry-run", "--json", cwd=project, env=env).stdout)
    done = _cli(*args, "--expect-digest", again["sha256"], "--json", cwd=project, env=env)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["sha256"] == again["sha256"]
    shown = _cli("registry", "show", "tool/orders@1.1.0", "--json", cwd=project, env=env)
    assert json.loads(shown.stdout)["sha256"] == again["sha256"]


def test_cli_end_to_end_publish_expect_digest(tmp_path):
    source = make_source(tmp_path / "p", "prompt", "p", "0.1.0", {"prompt.md": "Hi.\n"})
    root = tmp_path / "registry"
    preview = _cli(
        "registry", "publish", str(source), "--to", str(root), "--dry-run", "--json", cwd=tmp_path
    )
    assert preview.returncode == 0, preview.stderr
    digest = json.loads(preview.stdout)["sha256"]
    done = _cli(
        "registry",
        "publish",
        str(source),
        "--to",
        str(root),
        "--expect-digest",
        digest,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split()[:2] == ["prompt/p@0.1.0", digest]  # text output unchanged


# -- review r1 -----------------------------------------------------------------------------------


def test_a_rename_keeps_every_other_byte_of_the_manifest(project, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    text = (
        "# a comment\nkind: prompt\nname: greeting  # old name\nversion: 1.2.0\n"
        "compat: 1.10\nnote: |\n  line one\n  name: not a key\nlist: [1, 2]\n"
    )
    (source / "manifest.yaml").write_text(text)
    _import(source, project, name="welcome", version="2.0.0")
    written = (project / "registry/prompt/welcome/2.0.0/manifest.yaml").read_text()
    assert written == text.replace("name: greeting  # old name", 'name: "welcome"').replace(
        "version: 1.2.0", 'version: "2.0.0"'
    )
    assert "compat: 1.10\n" in written and "  name: not a key\n" in written


@pytest.mark.parametrize(
    "manifest",
    [
        "{kind: prompt, name: greeting, version: 1.2.0}\n",
        "kind: prompt\nname:\n  greeting\nversion: 1.2.0\n",
    ],
)
def test_a_rename_of_an_unusual_manifest_fails_closed(project, tmp_path, manifest):
    source = tmp_path / "src"
    source.mkdir()
    (source / "manifest.yaml").write_text(manifest)
    with pytest.raises(RegistryError, match="MANIFEST_INVALID") as error:
        _import(source, project, name="welcome")
    assert ".import-" not in str(error.value) and "agentbreeder-import-" not in str(error.value)
    _import(source, project)  # without a rename it is copied as is


def test_a_rename_that_grows_the_manifest_past_the_limit_names_the_source(
    project, elsewhere, monkeypatch
):
    size = len((elsewhere / "manifest.yaml").read_bytes())
    monkeypatch.setattr(registry, "MANIFEST_LIMIT", size + 2)
    with pytest.raises(RegistryError, match="exceeds") as error:
        _import(elsewhere, project, name="a-much-longer-name")
    assert str(elsewhere) in str(error.value) and ".import-" not in str(error.value)


def test_an_empty_name_or_version_is_refused(project, elsewhere):
    for options in ({"name": ""}, {"version": ""}):
        with pytest.raises(RegistryError, match="REF_"):
            _import(elsewhere, project, **options)


def test_an_unwritable_name_directory_is_not_version_exists(project, elsewhere):
    parent = project / "registry/prompt/greeting"
    parent.mkdir(parents=True)
    os.chmod(parent, 0o500)
    try:
        with pytest.raises(RegistryError) as error:
            _import(elsewhere, project)
    finally:
        os.chmod(parent, 0o755)
    assert error.value.code == "ENTRY_UNSAFE"
    assert ".import-" not in str(error.value)


def test_a_symlinked_kind_directory_in_another_root_is_refused(
    project, elsewhere, tmp_path, monkeypatch
):
    other = tmp_path / "shared"
    other.mkdir()
    (tmp_path / "linked").mkdir()
    os.symlink(tmp_path / "linked", other / "prompt")
    monkeypatch.setenv(registry.PATH_ENV, str(other))
    with pytest.raises(RegistryError, match="would not resolve") as error:
        _import(elsewhere, project)
    assert error.value.code == "ENTRY_UNSAFE"
    assert not (project / "registry/prompt/greeting").exists()


# -- review r2 -----------------------------------------------------------------------------------


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_source_that_contains_the_root_is_refused(project, dry_run):
    (project / "manifest.yaml").write_text("kind: prompt\nname: p\nversion: 1.0.0\n")
    (project / "registry").mkdir()
    with pytest.raises(RegistryError, match="inside the source") as error:
        registry.place(project, project / "registry", dry_run=dry_run)
    assert error.value.code == "ENTRY_UNSAFE"
    assert list((project / "registry").iterdir()) == []


def test_a_rename_keeps_crlf_line_endings(project, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "manifest.yaml").write_bytes(
        b"kind: prompt\r\nname: greeting\r\nversion: 1.0.0\r\ndescription: x\r\n"
    )
    _import(source, project, name="welcome")
    assert (project / "registry/prompt/welcome/1.0.0/manifest.yaml").read_bytes() == (
        b'kind: prompt\r\nname: "welcome"\r\nversion: "1.0.0"\r\ndescription: x\r\n'
    )
