"""Stack state dir: ids, private files, owned secrets, ports, mounts (#738)."""

from __future__ import annotations

import os
import stat

import pytest

from agentbreeder.services import state as state_module
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import (
    StackState,
    private_dir,
    read_private,
    stack_id,
    stacks_root,
    write_private,
)


@pytest.fixture
def state(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    value = StackState("demo", project, tmp_path / "stacks")
    yield value
    value.close()


def test_stack_id_depends_on_name_and_absolute_path(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = stack_id("northwind", tmp_path / "a")
    assert first.startswith("northwind-") and len(first) == len("northwind-") + 8
    assert first == stack_id("northwind", tmp_path / "a" / ".." / "a")
    assert first != stack_id("northwind", tmp_path / "b")


def test_stacks_root_env(tmp_path, monkeypatch):
    monkeypatch.setenv(state_module.ROOT_ENV, str(tmp_path / "x"))
    assert stacks_root() == (tmp_path / "x").resolve()
    monkeypatch.delenv(state_module.ROOT_ENV)
    assert stacks_root().name == "stacks"


def test_layout_is_private(state):
    info = state.directory.stat()
    assert stat.S_IMODE(info.st_mode) == 0o700
    assert stat.S_IMODE((state.directory / "stack.db").stat().st_mode) == 0o600
    assert state.project == f"agentbreeder-{state.id}"
    assert state.compose_path.parent == state.directory
    assert state.deploy_state("dev") == state.directory / "envs" / "dev" / "state"
    with pytest.raises(ServiceError, match="STACK_ENVIRONMENT"):
        state.env_dir("../x")
    assert state.environments() == []
    (state.directory / "envs" / "dev").mkdir(parents=True)
    (state.directory / "envs" / "dev" / "operator.yaml").write_text("x")
    (state.directory / "envs" / "Bad").mkdir()
    assert state.environments() == ["dev"]


def test_private_dir_refuses_symlinks_and_tightens_mode(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o755)
    private_dir(real)
    assert stat.S_IMODE(real.stat().st_mode) == 0o700
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(ServiceError, match="STATE_UNSAFE"):
        private_dir(tmp_path / "link")
    (tmp_path / "file").write_text("x")
    with pytest.raises(ServiceError, match="STATE_UNSAFE"):
        private_dir(tmp_path / "file" / "child")


def test_write_and_read_private(tmp_path):
    path = tmp_path / "secret"
    write_private(path, b"value")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_private(path) == b"value"
    assert read_private(tmp_path / "missing") is None
    path.chmod(0o644)
    with pytest.raises(ServiceError, match="0600"):
        read_private(path)
    (tmp_path / "link").symlink_to(path)
    with pytest.raises(ServiceError, match="regular file"):
        read_private(tmp_path / "link")
    write_private(tmp_path / "public", b"x", mode=0o444)
    assert stat.S_IMODE((tmp_path / "public").stat().st_mode) == 0o444


def test_write_private_cleans_up_on_failure(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("disk")

    (tmp_path / "w").mkdir()
    monkeypatch.setattr(state_module.os, "replace", fail)
    with pytest.raises(OSError):
        write_private(tmp_path / "w" / "x", b"v")
    assert list((tmp_path / "w").iterdir()) == []


def test_secrets_are_owned_and_preseeded_files_refused(state):
    assert state.read_secret("db", "password") is None
    state.write_secret("db", "password", b"hunter2-value", "generate")
    assert state.read_secret("db", "password") == b"hunter2-value"
    assert state.owner("db", "password") == "generate"
    seeded = state.directory / "service-secrets" / "db" / "planted"
    seeded.write_bytes(b"attacker")
    seeded.chmod(0o600)
    with pytest.raises(ServiceError, match="SECRET_NOT_OWNED"):
        state.read_secret("db", "planted")
    assert state.known_secrets() == [b"hunter2-value"]
    with pytest.raises(ServiceError, match="SECRET_NAME"):
        state.read_secret("db", "../x")
    with pytest.raises(ValueError):
        state.write_secret("db", "x", b"v", "stolen")
    state.write_secret("db", "empty", b"v", "import")
    (state.directory / "service-secrets" / "db" / "empty").write_bytes(b"")
    with pytest.raises(ServiceError, match="SECRET_EMPTY"):
        state.read_secret("db", "empty")
    assert state.known_secrets() == [b"hunter2-value"]  # unreadable ones are skipped


def test_host_ports_are_recorded_and_unique(state):
    offered = iter([5000, 5000, 5001, 5002, 5003])
    assert state.host_port("a", "http", lambda: next(offered)) == 5000
    assert state.ports() == {}  # allocated, not recorded until up is confirmed
    state.record_port("a", "http", 5000)
    assert state.host_port("a", "http", lambda: 1) == 5000  # reused
    assert state.host_port("b", "http", lambda: next(offered)) == 5001  # 5000 skipped
    assert state.host_port("c", "http", lambda: next(offered), {5002}) == 5003
    state.record_port("b", "http", 5001)
    assert state.ports() == {"a": {"http": 5000}, "b": {"http": 5001}}
    with pytest.raises(ServiceError, match="PORT_ALLOCATION"):
        state.host_port("c", "http", lambda: 5000)


def test_generation_is_salted_and_stable(state):
    one = state.generation({"a": b"1"})
    assert one == state.generation({"a": b"1"})
    assert one != state.generation({"a": b"2"})
    assert len(one) == 16


def test_mount_dirs_are_per_generation(state):
    first = state.mount_dir("db", {"superuser": b"s1", "role.support": b"r1"})
    assert first == state.mount_dir("db", {"superuser": b"s1", "role.support": b"r1"})
    assert (first / "superuser").read_bytes() == b"s1"
    assert (first / "role" / "support").read_bytes() == b"r1"
    assert stat.S_IMODE((first / "superuser").stat().st_mode) == 0o444
    assert stat.S_IMODE((state.directory / "mounts").stat().st_mode) == 0o700
    second = state.mount_dir("db", {"superuser": b"s2"})
    assert second != first
    state.prune_mounts({second})
    assert not first.exists() and second.exists()
    (state.directory / "mounts" / f".{second.name}.tmp").mkdir()
    third = state.mount_dir("db", {"superuser": b"s3"})
    assert third.exists()


def test_prune_without_mounts_is_a_noop(state):
    state.prune_mounts(set())


def test_copy_file_is_content_addressed(state, tmp_path):
    source = tmp_path / "config.yaml"
    source.write_text("a: 1\n")
    copy = state.copy_file(source)
    assert copy.read_text() == "a: 1\n" and copy.name == "config.yaml"
    assert state.copy_file(source) == copy
    source.write_text("a: 2\n")
    assert state.copy_file(source) != copy
    tree = tmp_path / "files"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "x.py").write_text("print(1)\n")
    copied = state.copy_file(tree)
    assert (copied / "sub" / "x.py").read_text() == "print(1)\n"
    assert not os.access(copied / "sub" / "x.py", os.W_OK)
    (tree / "link").symlink_to(tree / "sub" / "x.py")
    with pytest.raises(ServiceError, match="symlinks"):
        state.copy_file(tree)


def test_copy_file_replaces_stale_staging(state, tmp_path):
    source = tmp_path / "f"
    source.write_text("x")
    digest = state_module._tree_digest(source)
    staging = state.directory / "files" / f".{digest[:16]}.tmp"
    staging.mkdir(parents=True)
    assert state.copy_file(source).read_text() == "x"
