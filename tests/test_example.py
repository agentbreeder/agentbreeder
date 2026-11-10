"""`agentbreeder example` copies a shipped example out of the package (spec §5.4, plan S4)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agentbreeder import cli, example, registry
from agentbreeder.example import ExampleError

SOURCE = Path(registry.__file__).resolve().parent
MANIFEST = json.loads((SOURCE / "_content.json").read_text())["files"]
EXECUTABLE = "examples/http-agent/server.py"  # marked executable in the fake package below


def _shipped(name: str) -> set[str]:
    prefix = f"examples/{name}/"
    return {key.removeprefix(prefix) for key in MANIFEST if key.startswith(prefix)}


def _tree(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


@pytest.fixture(autouse=True)
def _fresh_manifest_cache():
    registry._shipped_manifest.cache_clear()
    yield
    registry._shipped_manifest.cache_clear()


@pytest.fixture
def package(tmp_path, monkeypatch):
    """An installed package laid out from the manifest, with local state beside the examples."""
    package = tmp_path / "site-packages" / "agentbreeder"
    files = json.loads(json.dumps(MANIFEST))
    files[EXECUTABLE]["exec"] = True
    for relative, entry in files.items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / relative, target)
        target.chmod(0o755 if entry["exec"] else 0o644)
    (package / "_content.json").write_text(json.dumps({"version": 1, "files": files}))
    northwind = package / "examples" / "northwind"
    for state in (".agentbreeder/operator.yaml", "deploy/x.json", "stack/.secrets/key", "x.log"):
        (northwind / state).parent.mkdir(parents=True, exist_ok=True)
        (northwind / state).write_text("local state")
    monkeypatch.setattr(registry, "_PACKAGED_ROOT", package)
    return package


@pytest.fixture
def work(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def _leftovers(parent: Path) -> list[str]:
    return sorted(path.name for path in parent.iterdir() if path.name.startswith("."))


# -- listing and copying ------------------------------------------------------------------------


def test_names_come_from_the_manifest(package):
    assert example.names() == ["http-agent", "northwind"]


def test_copy_writes_exactly_the_shipped_files(package, work):
    destination = example.copy("northwind", work / "nw")
    assert _tree(destination) == _shipped("northwind")
    assert (destination / "stack" / ".dockerignore").is_file()
    assert (destination / "stack" / "db" / "commerce.sql").is_file()


def test_local_state_beside_the_source_is_not_copied_and_does_not_block(package, work):
    destination = example.copy("northwind", work / "nw")
    for state in (".agentbreeder", "deploy", "stack/.secrets", "x.log"):
        assert not (destination / state).exists(), state


def test_modes_follow_the_manifest_exec_bit(package, work):
    destination = example.copy("http-agent", work / "http")
    assert (destination / "server.py").stat().st_mode & 0o777 == 0o755
    assert (destination / "README.md").stat().st_mode & 0o777 == 0o644


def test_umask_077_install_is_copied_with_normal_modes(package, work):
    for path in (package / "examples").rglob("*"):
        if path.is_file():
            path.chmod(0o700 if os.access(path, os.X_OK) else 0o600)
    destination = example.copy("http-agent", work / "http")
    assert (destination / "server.py").stat().st_mode & 0o777 == 0o755
    assert (destination / "agent.yaml").stat().st_mode & 0o777 == 0o644


def test_empty_directory_destination_is_used(package, work):
    (work / "nw").mkdir()
    example.copy("northwind", work / "nw")
    assert (work / "nw" / "agent.yaml").is_file()
    assert _leftovers(work) == []


def test_default_destination_is_relative_to_the_working_directory(package, work):
    assert example.copy("http-agent", Path("http-agent")) == work / "http-agent"


# -- destinations that are refused --------------------------------------------------------------


def test_non_empty_destination_is_refused(package, work):
    (work / "nw").mkdir()
    (work / "nw" / "keep.txt").write_text("mine")
    with pytest.raises(ExampleError, match="DESTINATION_NOT_EMPTY"):
        example.copy("northwind", work / "nw")
    assert _tree(work / "nw") == {"keep.txt"}


def test_symlink_destination_is_refused(package, work):
    (work / "real").mkdir()
    (work / "nw").symlink_to(work / "real")
    with pytest.raises(ExampleError, match="DESTINATION_UNSAFE"):
        example.copy("northwind", work / "nw")
    assert list((work / "real").iterdir()) == []


def test_file_destination_is_refused(package, work):
    (work / "nw").write_text("mine")
    with pytest.raises(ExampleError, match="DESTINATION_UNSAFE"):
        example.copy("northwind", work / "nw")
    assert (work / "nw").read_text() == "mine"


def test_unknown_example_is_refused(package, work):
    with pytest.raises(ExampleError, match="EXAMPLE_UNKNOWN.*http-agent, northwind"):
        example.copy("nope", work / "x")


# -- the source is checked before anything is written ---------------------------------------


def test_tampered_source_is_refused_before_writing(package, work):
    agent = package / "examples" / "northwind" / "agent.yaml"
    agent.write_text(agent.read_text() + "# changed\n")
    with pytest.raises(ExampleError, match="CONTENT_MISMATCH.*agent.yaml"):
        example.copy("northwind", work / "nw")
    assert list(work.iterdir()) == []


def test_symlinked_source_file_is_refused(package, work):
    readme = package / "examples" / "http-agent" / "README.md"
    readme.unlink()
    readme.symlink_to(SOURCE / "examples" / "http-agent" / "README.md")
    with pytest.raises(ExampleError, match="CONTENT_MISMATCH.*README.md"):
        example.copy("http-agent", work / "http")
    assert list(work.iterdir()) == []


def test_missing_source_file_is_refused(package, work):
    (package / "examples" / "http-agent" / "Dockerfile").unlink()
    with pytest.raises(ExampleError, match="CONTENT_MISMATCH.*Dockerfile"):
        example.copy("http-agent", work / "http")
    assert list(work.iterdir()) == []


def test_failure_mid_copy_leaves_nothing(package, work, monkeypatch):
    calls = {"n": 0}
    original = example._read
    count = len(_shipped("northwind"))

    def failing(path, relative, entry):
        calls["n"] += 1
        if calls["n"] == count + 3:  # third file of the copying pass
            raise ExampleError("CONTENT_MISMATCH", "changed while copying")
        return original(path, relative, entry)

    monkeypatch.setattr(example, "_read", failing)
    with pytest.raises(ExampleError):
        example.copy("northwind", work / "nw")
    assert list(work.iterdir()) == []


def test_failed_move_leaves_an_empty_destination_untouched(package, work, monkeypatch):
    (work / "nw").mkdir()
    moves = {"n": 0}
    original = os.rename

    def flaky(src, dst):
        moves["n"] += 1
        if moves["n"] == 3:
            raise OSError("rename failed")
        return original(src, dst)

    monkeypatch.setattr(example.os, "rename", flaky)
    with pytest.raises(ExampleError, match="COPY_FAILED"):
        example.copy("northwind", work / "nw")
    assert [path.name for path in work.iterdir()] == ["nw"]
    assert list((work / "nw").iterdir()) == []


def test_working_directory_destination_keeps_its_inode(package, work, monkeypatch):
    project = work / "proj"
    project.mkdir()
    before = project.stat().st_ino
    monkeypatch.chdir(project)
    example.copy("http-agent", Path("."))
    assert project.stat().st_ino == before
    assert (Path.cwd() / "agent.yaml").is_file()  # visible from the shell's directory


def test_failure_removes_parent_directories_it_created(package, work, monkeypatch):
    def failing(path, relative, entry):
        raise ExampleError("CONTENT_MISMATCH", "changed while copying")

    calls = {"n": 0}
    original = example._read

    def second_pass_fails(path, relative, entry):
        calls["n"] += 1
        if calls["n"] > len(_shipped("http-agent")):
            return failing(path, relative, entry)
        return original(path, relative, entry)

    monkeypatch.setattr(example, "_read", second_pass_fails)
    with pytest.raises(ExampleError):
        example.copy("http-agent", work / "a" / "b" / "c")
    assert list(work.iterdir()) == []


def test_file_appearing_during_the_copy_is_kept_and_the_copy_refused(package, work, monkeypatch):
    destination = work / "nw"
    calls = {"n": 0}
    original = example._read

    def intruder(path, relative, entry):
        calls["n"] += 1
        if calls["n"] == len(_shipped("northwind")) + 1:
            (destination / "README.md").write_text("mine")
        return original(path, relative, entry)

    monkeypatch.setattr(example, "_read", intruder)
    with pytest.raises(ExampleError, match="DESTINATION_NOT_EMPTY"):
        example.copy("northwind", destination)
    assert _tree(destination) == {"README.md"}
    assert (destination / "README.md").read_text() == "mine"


def test_unwritable_parent_is_a_clean_error(package, work, capsys):
    locked = work / "ro"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert cli.main(["example", "http-agent", str(locked / "x")]) == 2
    finally:
        locked.chmod(0o700)
    assert "COPY_FAILED" in capsys.readouterr().err
    assert list(locked.iterdir()) == []


def test_parent_that_is_a_file_is_a_clean_error(package, work):
    (work / "f").write_text("")
    with pytest.raises(ExampleError, match="DESTINATION_UNSAFE"):
        example.copy("http-agent", work / "f" / "x")


def test_directory_modes_ignore_the_umask(package, work):
    previous = os.umask(0o077)
    try:
        destination = example.copy("northwind", work / "nw")
    finally:
        os.umask(previous)
    for directory in [destination, *[p for p in destination.rglob("*") if p.is_dir()]]:
        assert directory.stat().st_mode & 0o777 == 0o755, directory
    assert (destination / "agent.yaml").stat().st_mode & 0o777 == 0o644


def test_exec_bit_mismatch_is_refused(package, work):
    (package / EXECUTABLE).chmod(0o644)
    with pytest.raises(ExampleError, match="exec bit differs"):
        example.copy("http-agent", work / "http")
    assert list(work.iterdir()) == []


def test_unsafe_manifest_path_is_refused(package, work):
    manifest = json.loads((package / "_content.json").read_text())
    manifest["files"]["examples/http-agent/../../escape"] = {"sha256": "0" * 64, "exec": False}
    (package / "_content.json").write_text(json.dumps(manifest))
    with pytest.raises(ExampleError, match="unsafe manifest path"):
        example.copy("http-agent", work / "http")
    assert list(work.iterdir()) == []


def test_unreadable_manifest_is_an_example_error(package, work):
    (package / "_content.json").write_text("not json")
    with pytest.raises(ExampleError, match="CONTENT_MANIFEST_MISSING"):
        example.names()


# -- CLI ------------------------------------------------------------------------------------------


def test_cli_list(package, capsys):
    assert cli.main(["example", "list"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("http-agent")
    assert "northwind" in out


def test_cli_list_with_a_directory_is_not_a_listing(package, work, capsys):
    assert cli.main(["example", "list", "x"]) == 2
    assert "EXAMPLE_UNKNOWN" in capsys.readouterr().err


def test_cli_copies_to_the_default_directory(package, work, capsys):
    assert cli.main(["example", "northwind"]) == 0
    assert (work / "northwind" / "agent.yaml").is_file()
    assert "Next: read" in capsys.readouterr().out


def test_cli_reports_errors(package, work, capsys):
    (work / "northwind").mkdir()
    (work / "northwind" / "x").write_text("")
    assert cli.main(["example", "northwind"]) == 2
    assert "DESTINATION_NOT_EMPTY" in capsys.readouterr().err


# -- the copied Northwind works ---------------------------------------------------------------


def test_copied_northwind_validates(work, capsys):
    destination = example.copy("northwind", work / "nw")
    assert cli.main(["validate", str(destination / "agent.yaml")]) == 0
    assert cli.main(["validate", str(destination / "stack.yaml")]) == 0
    out = capsys.readouterr().out
    assert "Valid agent file: northwind-support" in out
    assert "Stack file schema is valid" in out


def test_validate_reports_an_invalid_stack(tmp_path, capsys):
    bad = tmp_path / "stack.yaml"
    bad.write_text("kind: stack\nservices: {}\n")
    assert cli.main(["validate", str(bad)]) == 2
    assert "STACK_INVALID" in capsys.readouterr().err


def test_copied_northwind_builds_locked_without_docker(work, identity_env):
    """agent.lock records `source: builtin` and project-relative sources, so it survives the copy:
    a locked build resolves every ref (builder stubbed, no Docker)."""
    from agentbreeder.build import build

    identity_env.config["gateways"] = {
        "litellm-local": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": "http://host.docker.internal:4000",
        }
    }
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm-local",
            "models": ["support-model", "support-embedding"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
            "bindings": {"DATABASE_URL": "secret://northwind-db"},
            "agents": {
                "northwind-support": {
                    "identity": {"provider": "static", "ref": "agent-1", "environment": "dev"},
                    "candidate_identity": {
                        "provider": "static",
                        "ref": "agent-1",
                        "environment": "dev",
                    },
                }
            },
        }
    )
    destination = example.copy("northwind", work / "nw")
    lock_before = (destination / "agent.lock").read_bytes()
    contexts = []

    def builder(context: Path, iidfile: Path) -> str:
        contexts.append(sorted(p.relative_to(context).as_posix() for p in context.rglob("*")))
        return "sha256:" + "b" * 64

    result = build(destination / "agent.yaml", identity_env.load(), mode="locked", builder=builder)
    assert contexts and result is not None
    assert (destination / "agent.lock").read_bytes() == lock_before


@pytest.mark.parametrize("target", ["locked", "locked/x"])
def test_unreadable_destination_is_a_clean_error(package, work, capsys, target):
    (work / "locked").mkdir()
    (work / "locked").chmod(0o000)
    try:
        assert cli.main(["example", "http-agent", str(work / target)]) == 2
    finally:
        (work / "locked").chmod(0o700)
    assert "COPY_FAILED" in capsys.readouterr().err
    assert list((work / "locked").iterdir()) == []
