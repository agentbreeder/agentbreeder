"""Registries, refs and agent.lock (#728)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agentbreeder import cli, registry
from agentbreeder.lock import Lock, materialise
from agentbreeder.registry import Ref, RegistryError
from agentbreeder.strict_yaml import StrictYamlError, load_text


def make_version(root: Path, kind: str, name: str, version: str, files: dict) -> Path:
    path = root / kind / name / version
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(f"kind: {kind}\nname: {name}\nversion: {version}\n")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = tmp_path / "project"
    make_version(project / "registry", "prompt", "support", "1.0.0", {"prompt.md": "Be kind."})
    make_version(project / "registry", "tool", "orders", "1.0.0", {"src/orders.py": "x = 1\n"})
    return project


# -- refs ----------------------------------------------------------------------------------------


def test_ref_parsing():
    ref = Ref.parse("tool/orders@1.2.3")
    assert (ref.kind, ref.name, ref.version, str(ref)) == (
        "tool",
        "orders",
        "1.2.3",
        "tool/orders@1.2.3",
    )


@pytest.mark.parametrize(
    "text,code",
    [
        ("tool/orders", "REF_UNPINNED"),
        ("tool/orders@1", "REF_UNPINNED"),
        ("tool/orders@^1.0.0", "REF_INVALID"),
        ("bogus/orders@1.0.0", "REF_INVALID"),
        ("tool/../x@1.0.0", "REF_INVALID"),
        ("tool/Orders@1.0.0", "REF_INVALID"),
        ("tool/orders@01.0.0", "REF_UNPINNED"),
        ("", "REF_INVALID"),
    ],
)
def test_bad_refs(text, code):
    with pytest.raises(RegistryError) as error:
        Ref.parse(text)
    assert error.value.code == code


# -- hashing and integrity -----------------------------------------------------------------------


def test_hash_is_deterministic_and_content_sensitive(project, tmp_path):
    version = project / "registry/prompt/support/1.0.0"
    first = registry.copy_and_hash(version)
    assert first == registry.copy_and_hash(version)
    copy = tmp_path / "copy"
    assert registry.copy_and_hash(version, copy) == first
    assert (copy / "prompt.md").read_text() == "Be kind."
    (version / "prompt.md").write_text("Be kind!")  # one byte
    assert registry.copy_and_hash(version) != first


def test_hash_covers_paths_and_exec_bit(project):
    version = project / "registry/tool/orders/1.0.0"
    before = registry.copy_and_hash(version)
    (version / "src/orders.py").chmod(0o755)
    assert registry.copy_and_hash(version) != before
    (version / "src/orders.py").chmod(0o644)
    (version / "src/orders.py").rename(version / "src/other.py")
    assert registry.copy_and_hash(version) != before


def test_unsafe_entries_rejected(project, tmp_path):
    version = project / "registry/prompt/support/1.0.0"
    os.symlink("/etc/passwd", version / "link")
    with pytest.raises(RegistryError, match="symlinks"):
        registry.copy_and_hash(version)
    (version / "link").unlink()
    os.link(version / "prompt.md", version / "hard.md")
    with pytest.raises(RegistryError, match="hardlinks"):
        registry.copy_and_hash(version)
    (version / "hard.md").unlink()
    os.mkfifo(version / "pipe")
    with pytest.raises(RegistryError, match="only files"):
        registry.copy_and_hash(version)
    (version / "pipe").unlink()
    linked_root = tmp_path / "linked"
    os.symlink(version, linked_root)
    with pytest.raises(RegistryError):
        registry.copy_and_hash(linked_root)


def test_too_many_files_rejected(project, monkeypatch):
    monkeypatch.setattr(registry, "MAX_FILES", 1)
    with pytest.raises(RegistryError, match="too large"):
        registry.copy_and_hash(project / "registry/tool/orders/1.0.0")


# -- resolution ----------------------------------------------------------------------------------


def test_resolve_search_order_and_conflict(project, tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    make_version(shared, "prompt", "support", "1.0.0", {"prompt.md": "Be kind."})
    make_version(shared, "prompt", "other", "2.0.0", {"prompt.md": "x"})
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    search = registry.roots(project)
    assert search[:2] == [project / "registry", shared]
    assert registry.resolve(Ref.parse("prompt/other@2.0.0"), search).root == shared
    # identical content in two roots is fine
    assert registry.resolve(Ref.parse("prompt/support@1.0.0"), search).root == project / "registry"
    (shared / "prompt/support/1.0.0/prompt.md").write_text("Be mean.")
    with pytest.raises(RegistryError) as error:
        registry.resolve(Ref.parse("prompt/support@1.0.0"), search)
    assert error.value.code == "REF_CONFLICT"


def test_resolve_not_found_and_manifest_mismatch(project):
    search = registry.roots(project)
    with pytest.raises(RegistryError) as error:
        registry.resolve(Ref.parse("prompt/missing@1.0.0"), search)
    assert error.value.code == "REF_NOT_FOUND"
    (project / "registry/prompt/support/1.0.0/manifest.yaml").write_text(
        "kind: prompt\nname: support\nversion: 9.9.9\n"
    )
    with pytest.raises(RegistryError) as error:
        registry.resolve(Ref.parse("prompt/support@1.0.0"), search)
    assert error.value.code == "MANIFEST_INVALID"


def test_resolve_refuses_symlinked_version(project, tmp_path):
    target = make_version(tmp_path / "x", "prompt", "evil", "1.0.0", {"prompt.md": "x"})
    (project / "registry/prompt/evil").mkdir()
    os.symlink(target, project / "registry/prompt/evil/1.0.0")
    with pytest.raises(RegistryError) as error:
        registry.resolve(Ref.parse("prompt/evil@1.0.0"), registry.roots(project))
    assert error.value.code == "ENTRY_UNSAFE"


# -- publish / list / verify / scaffold -----------------------------------------------------------


def test_publish_is_immutable(project, tmp_path):
    source = registry.scaffold("prompt", "greeter", tmp_path)
    ref, digest, path = registry.publish(source, project / "registry")
    assert str(ref) == "prompt/greeter@0.1.0" and digest.startswith("sha256:")
    assert registry.copy_and_hash(path) == digest
    with pytest.raises(RegistryError) as error:
        registry.publish(source, project / "registry")
    assert error.value.code == "VERSION_EXISTS"
    with pytest.raises(RegistryError):
        registry.scaffold("prompt", "greeter", tmp_path)
    with pytest.raises(RegistryError):
        registry.scaffold("runtime", "x", tmp_path)


def test_publish_requires_valid_manifest(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "manifest.yaml").write_text("kind: prompt\nname: x\n")
    with pytest.raises(RegistryError) as error:
        registry.publish(bad, tmp_path / "root")
    assert error.value.code == "MANIFEST_INVALID"
    (bad / "manifest.yaml").write_text("kind: [\n")
    with pytest.raises(RegistryError):
        registry.publish(bad, tmp_path / "root")


def test_list_and_verify(project):
    search = registry.roots(project)
    refs = [row["ref"] for row in registry.listing(search)]
    assert refs == ["prompt/support@1.0.0", "tool/orders@1.0.0"]
    assert [row["ref"] for row in registry.listing(search, "tool/")] == ["tool/orders@1.0.0"]
    assert registry.verify(search) == []
    os.symlink("/etc/hosts", project / "registry/tool/orders/1.0.0/link")
    problems = registry.verify(search)
    assert problems[0]["ref"] == "tool/orders@1.0.0" and "symlinks" in problems[0]["error"]


# -- lock ----------------------------------------------------------------------------------------

REFS = ["prompt/support@1.0.0", "tool/orders@1.0.0"]


def run(project, tmp_path, lock, mode="plain", refs=REFS, paths=(), update=None, name="ctx"):
    return materialise(
        refs=list(refs),
        paths=list(paths),
        project_dir=project,
        search=registry.roots(project),
        lock=lock,
        context=tmp_path / name,
        mode=mode,
        update=update,
    )


def test_lock_plain_adds_then_verifies(project, tmp_path):
    lock = Lock()
    entries, changed = run(project, tmp_path, lock)
    assert changed and set(lock.entries) == set(REFS)
    assert lock.entries["tool/orders@1.0.0"]["source"] == "registry"
    assert (entries[1].path / "src/orders.py").exists()
    _, changed = run(project, tmp_path, lock, name="ctx2")
    assert not changed
    lock.save(project / "agent.lock")
    assert Lock.load(project / "agent.lock").entries == lock.entries


def test_lock_detects_one_byte_tamper(project, tmp_path):
    lock = Lock()
    run(project, tmp_path, lock)
    (project / "registry/prompt/support/1.0.0/prompt.md").write_text("Be kind!")
    for mode in ("plain", "locked", "update"):
        with pytest.raises(RegistryError) as error:
            run(
                project,
                tmp_path,
                lock,
                mode=mode,
                name=f"ctx-{mode}",
                update={"tool/orders@1.0.0"},
            )
        assert error.value.code == "HASH_MISMATCH"


def test_locked_refuses_additions_removals_and_paths(project, tmp_path):
    lock = Lock()
    run(project, tmp_path, lock, refs=REFS[:1])
    with pytest.raises(RegistryError) as error:
        run(project, tmp_path, lock, mode="locked", name="a")
    assert error.value.code == "LOCK_STALE"
    run(project, tmp_path, lock, name="b")
    with pytest.raises(RegistryError) as error:
        run(project, tmp_path, lock, mode="locked", refs=REFS[:1], name="c")
    assert error.value.code == "LOCK_STALE"
    (project / "prompts").mkdir()
    (project / "prompts/dev.md").write_text("draft")
    with pytest.raises(RegistryError) as error:
        run(project, tmp_path, lock, mode="locked", paths=["prompts/dev.md"], name="d")
    assert error.value.code == "REF_UNPINNED"
    _, changed = run(project, tmp_path, lock, mode="locked", name="e")
    assert not changed


def test_plain_prunes_unused(project, tmp_path):
    lock = Lock()
    run(project, tmp_path, lock)
    _, changed = run(project, tmp_path, lock, refs=REFS[:1], name="x")
    assert changed and list(lock.entries) == ["prompt/support@1.0.0"]


def test_path_refs_need_update_after_edit(project, tmp_path):
    (project / "prompts").mkdir()
    (project / "prompts/dev.md").write_text("draft 1")
    lock = Lock()
    run(project, tmp_path, lock, refs=[], paths=["prompts/dev.md"])
    assert "path:prompts/dev.md" in lock.entries
    (project / "prompts/dev.md").write_text("draft 2")
    with pytest.raises(RegistryError, match="build --update"):
        run(project, tmp_path, lock, refs=[], paths=["prompts/dev.md"], name="b")
    _, changed = run(
        project, tmp_path, lock, mode="update", refs=[], paths=["prompts/dev.md"], name="c"
    )
    assert changed
    with pytest.raises(RegistryError):
        run(project, tmp_path, lock, refs=[], paths=["../outside.md"], name="d")
    (project / "prompts/dir").mkdir()
    (project / "prompts/dir/a.md").write_text("a")
    run(project, tmp_path, lock, refs=[], paths=["prompts/dir"], name="e")


def test_locked_loads_only_from_locked_source(project, tmp_path, monkeypatch):
    lock = Lock()
    run(project, tmp_path, lock)
    shared = tmp_path / "shared"
    make_version(shared, "tool", "orders", "1.0.0", {"src/orders.py": "x = 1\n"})
    monkeypatch.setenv(registry.PATH_ENV, str(shared))
    entries, _ = run(project, tmp_path, lock, mode="locked", name="z")
    assert entries[1].source == "registry"


def test_lock_load_errors(tmp_path):
    path = tmp_path / "agent.lock"
    assert Lock.load(path).entries == {}
    path.write_text("{")
    with pytest.raises(RegistryError):
        Lock.load(path)
    path.write_text(json.dumps({"lock_version": 99, "entries": {}}))
    with pytest.raises(RegistryError):
        Lock.load(path)
    path.write_text(json.dumps({"lock_version": 1}))
    with pytest.raises(RegistryError):
        Lock.load(path)


# -- strict yaml ---------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["a: 1\na: 2\n", "a: &x 1\nb: *x\n", "- 1\n", "a: [\n"])
def test_strict_yaml_rejects(text):
    with pytest.raises(StrictYamlError):
        load_text(text)


# -- CLI -----------------------------------------------------------------------------------------


def test_cli_registry_flow(project, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(project)
    assert cli.main(["registry", "new", "tool", "notes", "--dir", str(tmp_path)]) == 0
    source = tmp_path / "tool-notes"
    assert cli.main(["registry", "publish", str(source), "--json"]) == 0
    published = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert published["ref"] == "tool/notes@0.1.0"
    assert cli.main(["registry", "publish", str(source)]) == 2
    assert "VERSION_EXISTS" in capsys.readouterr().err
    assert cli.main(["registry", "list", "tool/", "--json"]) == 0
    assert [r["ref"] for r in json.loads(capsys.readouterr().out)] == [
        "tool/notes@0.1.0",
        "tool/orders@1.0.0",
    ]
    assert cli.main(["registry", "list"]) == 0
    assert cli.main(["registry", "verify"]) == 0
    assert "OK" in capsys.readouterr().out
    os.symlink("/etc/hosts", project / "registry/tool/notes/0.1.0/link")
    assert cli.main(["registry", "verify", "--json"]) == 2
    assert "symlinks" in capsys.readouterr().err


def test_strict_load_file_errors(tmp_path):
    from agentbreeder.strict_yaml import load_file

    with pytest.raises(StrictYamlError):
        load_file(tmp_path / "missing.yaml")
    big = tmp_path / "big.yaml"
    big.write_text("a: " + "x" * 2000)
    with pytest.raises(StrictYamlError, match="limit"):
        load_file(big, limit=1024)
    binary = tmp_path / "bin.yaml"
    binary.write_bytes(b"a: \xff\n")
    with pytest.raises(StrictYamlError, match="UTF-8"):
        load_file(binary)


def test_lock_misc_paths(project, tmp_path):
    lock = Lock()
    with pytest.raises(ValueError):
        run(project, tmp_path, lock, mode="sideways")
    entries, _ = run(project, tmp_path, lock, refs=REFS + REFS[:1])  # duplicate ref collapses
    assert len(entries) == 2
    (project / "p.md").write_text("x")
    entries, _ = run(project, tmp_path, lock, refs=[], paths=["p.md", "p.md"], name="dup")
    assert len(entries) == 1
    with pytest.raises(RegistryError):
        run(project, tmp_path, lock, refs=[], paths=["nope.md"], name="missing")
    # same content, new source root: the lock records the new source in plain mode
    lock = Lock()
    run(project, tmp_path, lock, name="s1")
    lock.entries["tool/orders@1.0.0"]["source"] = "elsewhere"
    _, changed = run(project, tmp_path, lock, name="s2")
    assert changed and lock.entries["tool/orders@1.0.0"]["source"] == "registry"


@pytest.mark.parametrize("part", ["..", ".", "a\\b", "a\x00b", "bad\udcff"])
def test_safe_name_rejects(part):
    with pytest.raises(RegistryError):
        registry._safe_name(part)
