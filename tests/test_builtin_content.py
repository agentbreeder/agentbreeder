"""Shipped built-ins are checked against _content.json, so hardlinked installs work (plan S3).

uv hardlinks installed files from its cache by default on Linux, which the registry's hardlink
refusal used to reject. Files under the installed package's built-in registry and examples are
instead compared with `_content.json`; every other root keeps the hardlink refusal.

Most tests build a fake installed package from the manifest in a temp dir, hardlink every file
(st_nlink == 2, as uv does) and point `_PACKAGED_ROOT` / `BUILTIN_ROOT` at it.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agentbreeder import cli, registry
from agentbreeder.agentfile import load_agent_file
from agentbreeder.lock import Lock, materialise
from agentbreeder.registry import Ref, RegistryError
from agentbreeder.release import _eval_set

SOURCE = Path(registry.__file__).resolve().parent
MANIFEST = json.loads((SOURCE / "_content.json").read_text())["files"]
HASHES = json.loads((Path(__file__).parent / "fixtures" / "builtin_hashes.json").read_text())
ADK = "builtin_registry/runtime/adk/1.0.6"


@pytest.fixture(autouse=True)
def _fresh_manifest_cache():
    registry._shipped_manifest.cache_clear()
    yield
    registry._shipped_manifest.cache_clear()


def _install(tmp_path: Path, file_mode: int | None = None) -> Path:
    """A package laid out as an installer leaves it: only manifest files, each hardlinked."""
    package = tmp_path / "site-packages" / "agentbreeder"
    cache = tmp_path / "uv-cache"
    for relative, entry in MANIFEST.items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, target)
        if file_mode is None:
            target.chmod(0o755 if entry["exec"] else 0o644)
        else:  # umask 077 installs: 0700 for executables, 0600 otherwise
            target.chmod(file_mode | (0o100 if entry["exec"] else 0))
        linked = cache / relative
        linked.parent.mkdir(parents=True, exist_ok=True)
        os.link(target, linked)
    shutil.copyfile(SOURCE / "_content.json", package / "_content.json")
    return package


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    package = _install(tmp_path)
    monkeypatch.setattr(registry, "_PACKAGED_ROOT", package)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", package / "builtin_registry")
    assert (package / ADK / "manifest.yaml").stat().st_nlink == 2
    return package


def _northwind(package: Path) -> Path:
    return package / "examples" / "northwind"


def _materialise_locked(project: Path, context: Path) -> list:
    lock = Lock.load(project / "agent.lock")
    entries, _ = materialise(
        refs=sorted(lock.entries),
        paths=[],
        project_dir=project,
        search=registry.roots(project),
        lock=lock,
        context=context,
        mode="locked",
    )
    return entries


# -- hardlinked installs work end to end -------------------------------------------------------


def test_hardlinked_builtins_pass_verify(installed):
    assert registry.trusted_root(registry.BUILTIN_ROOT)
    assert registry.verify([registry.BUILTIN_ROOT]) == []


def test_hardlinked_builtin_resolves_with_the_recorded_hash(installed):
    hit = registry.resolve(Ref.parse("runtime/adk@1.0.6"), registry.roots())
    assert hit.path == installed / ADK
    assert registry.copy_and_hash(hit.path) == HASHES["runtime/adk@1.0.6"]


def test_hardlinked_northwind_materialises_from_its_lock(installed, tmp_path):
    project = _northwind(installed)
    entries = _materialise_locked(project, tmp_path / "context")
    lock = Lock.load(project / "agent.lock")
    assert {entry.key: entry.sha256 for entry in entries} == {
        ref: entry["sha256"] for ref, entry in lock.entries.items()
    }
    assert any(entry.source == "builtin" for entry in entries)


def test_hardlinked_northwind_eval_set_matches_its_lock(installed):
    project = _northwind(installed)
    agent = load_agent_file(project / "agent.yaml")
    assert _eval_set(agent, project).cases


def test_umask_077_install_modes_pass(tmp_path, monkeypatch):
    package = _install(tmp_path, file_mode=0o600)
    monkeypatch.setattr(registry, "_PACKAGED_ROOT", package)
    assert registry.verify([package / "builtin_registry"]) == []
    adk = package / ADK
    assert registry.copy_and_hash(adk) == HASHES["runtime/adk@1.0.6"]


# -- the manifest comparison catches corruption ------------------------------------------------


def _verify_error(root: Path) -> str:
    problems = registry.verify([root])
    assert problems, "expected verify to report a problem"
    return " ".join(problem["error"] for problem in problems)


def test_changed_byte_is_a_content_mismatch(installed):
    target = installed / ADK / "manifest.yaml"
    target.write_bytes(target.read_bytes() + b"#")
    assert "[CONTENT_MISMATCH] differs from the shipped manifest" in _verify_error(
        registry.BUILTIN_ROOT
    )


def test_flipped_exec_bit_is_a_content_mismatch(installed):
    target = installed / "builtin_registry/service/litellm/1.0.0/files/start.py"
    target.chmod(0o644)
    assert "exec bit differs" in _verify_error(registry.BUILTIN_ROOT)


def test_extra_file_is_refused(installed):
    (installed / ADK / "extra.txt").write_text("x")
    assert "not in the shipped manifest: extra.txt" in _verify_error(registry.BUILTIN_ROOT)


def test_extra_empty_directory_is_refused(installed):
    (installed / ADK / "empty").mkdir()
    assert "not in the shipped manifest: empty/" in _verify_error(registry.BUILTIN_ROOT)


def test_missing_file_is_refused(installed):
    (installed / ADK / "manifest.yaml").unlink()
    with pytest.raises(RegistryError, match="CONTENT_MISMATCH.*missing shipped files"):
        registry.copy_and_hash(installed / ADK)


def test_symlink_is_still_refused(installed):
    (installed / ADK / "link").symlink_to("manifest.yaml")
    assert "[ENTRY_UNSAFE] symlinks are not allowed" in _verify_error(registry.BUILTIN_ROOT)


def test_interpreter_caches_are_ignored(installed):
    cache = installed / ADK / "__pycache__"
    cache.mkdir()
    (cache / "x.cpython-312.pyc").write_bytes(b"\0")
    assert registry.verify([registry.BUILTIN_ROOT]) == []


def test_missing_manifest_is_reported(installed):
    (installed / "_content.json").unlink()
    with pytest.raises(RegistryError, match="CONTENT_MANIFEST_MISSING"):
        registry.copy_and_hash(installed / ADK)


def test_copied_build_context_is_checked_normally(installed, tmp_path):
    """The copy placed in a build context is outside the package, so it gets the usual rules."""
    copy = tmp_path / "context" / "adk"
    assert registry.copy_and_hash(installed / ADK, copy) == HASHES["runtime/adk@1.0.6"]
    assert not registry.trusted_root(copy)


# -- other roots keep the hardlink refusal ------------------------------------------------------


def test_hardlink_in_a_project_registry_is_still_refused(installed, tmp_path):
    version = tmp_path / "project" / "registry" / "prompt" / "p" / "1.0.0"
    version.mkdir(parents=True)
    (version / "manifest.yaml").write_text("kind: prompt\nname: p\nversion: 1.0.0\n")
    os.link(version / "manifest.yaml", tmp_path / "elsewhere")
    with pytest.raises(RegistryError, match="ENTRY_UNSAFE.*hardlinks are not allowed"):
        registry.copy_and_hash(version)


def test_patched_builtin_root_keeps_the_hardlink_rule(tmp_path, monkeypatch):
    """Tests that point BUILTIN_ROOT at a temp dir are not under the package: no trust."""
    builtin = tmp_path / "builtin"
    version = builtin / "runtime" / "x" / "1.0.0"
    version.mkdir(parents=True)
    (version / "manifest.yaml").write_text("kind: runtime\nname: x\nversion: 1.0.0\n")
    os.link(version / "manifest.yaml", tmp_path / "elsewhere")
    monkeypatch.setattr(registry, "BUILTIN_ROOT", builtin)
    assert not registry.trusted_root(builtin)
    assert "hardlinks are not allowed" in _verify_error(builtin)


def test_symlinked_venv_path_is_still_trusted(installed, tmp_path, monkeypatch):
    venv = tmp_path / "venv-link"
    venv.symlink_to(installed.parent)
    via_link = venv / "agentbreeder" / "builtin_registry"
    assert registry.trusted_root(via_link)
    assert registry.verify([via_link]) == []
    # And the other way round: the package root itself recorded through the symlink.
    monkeypatch.setattr(registry, "_PACKAGED_ROOT", venv / "agentbreeder")
    assert registry.verify([installed / "builtin_registry"]) == []


def test_registry_path_pointing_at_the_builtin_root_gets_the_same_check(
    installed, tmp_path, monkeypatch
):
    alias = tmp_path / "alias"
    alias.symlink_to(installed / "builtin_registry")
    monkeypatch.setenv(registry.PATH_ENV, str(alias))
    search = registry.roots()
    assert alias in search and registry.BUILTIN_ROOT in search
    hit = registry.resolve(Ref.parse("runtime/adk@1.0.6"), search)
    assert registry.copy_and_hash(hit.path) == HASHES["runtime/adk@1.0.6"]
    (installed / ADK / "extra.txt").write_text("x")
    problems = registry.verify(search)
    assert {problem["path"] for problem in problems} == {
        str(alias / "runtime/adk/1.0.6"),
        str(installed / ADK),
    }


# -- the real package --------------------------------------------------------------------------


def test_the_real_package_trusts_only_its_shipped_trees(tmp_path):
    assert registry.trusted_root(SOURCE / "builtin_registry")
    assert registry.trusted_root(SOURCE / "examples" / "northwind" / "registry")
    assert not registry.trusted_root(SOURCE / "policy")
    assert not registry.trusted_root(tmp_path)


def test_cli_verify_names_the_corruption_check(monkeypatch, capsys):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    assert cli.main(["registry", "verify"]) == 0
    out = capsys.readouterr().out
    assert "Built-in content matches the shipped manifest (corruption check)." in out


# -- review r6 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("tree", ["builtin_registry", "examples/northwind/registry"])
def test_publish_into_a_shipped_tree_is_refused(installed, tmp_path, tree):
    source = tmp_path / "new"
    source.mkdir()
    (source / "manifest.yaml").write_text("kind: prompt\nname: p\nversion: 9.0.0\n")
    with pytest.raises(RegistryError, match="REGISTRY_READ_ONLY.*content_manifest.py"):
        registry.publish(source, installed / tree)
    assert not (installed / tree / "prompt" / "p").exists()


def test_mismatch_message_names_the_checkout_remedy(installed):
    (installed / ADK / "extra.txt").write_text("x")
    assert "run scripts/content_manifest.py" in _verify_error(registry.BUILTIN_ROOT)


@pytest.mark.parametrize(
    "content",
    [
        '{"files": []}',
        '{"files": {"builtin_registry/x": {"sha256": "ab"}}}',
        '{"files": {"builtin_registry/x": "ab"}}',
        '{"version": 1}',
        "not json",
    ],
)
def test_malformed_manifest_is_a_registry_error(installed, content):
    (installed / "_content.json").write_text(content)
    problems = registry.verify([registry.BUILTIN_ROOT])
    assert problems and all("CONTENT_MANIFEST_MISSING" in p["error"] for p in problems)


def test_cli_verify_omits_the_corruption_line_without_a_trusted_builtin(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    (tmp_path / "builtin").mkdir()
    assert cli.main(["registry", "verify"]) == 0
    assert "corruption check" not in capsys.readouterr().out


def test_cli_verify_json_has_no_prose(monkeypatch, capsys):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    assert cli.main(["registry", "verify", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
