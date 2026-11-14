"""Deterministic registry packer (M9b spec §6.2, built by M13 #763)."""

from __future__ import annotations

import gzip
import io
import os
import sys
import tarfile
from pathlib import Path

import pytest

from agentbreeder import registry
from agentbreeder.pack import pack
from agentbreeder.registry import RegistryError, copy_and_hash


def make_version(root: Path) -> Path:
    version = root / "tool" / "orders" / "1.2.3"
    (version / "src" / "deep").mkdir(parents=True)
    (version / "manifest.yaml").write_text("kind: tool\nname: orders\nversion: 1.2.3\n")
    (version / "src" / "orders.py").write_text("x = 1\n")
    (version / "src" / "deep" / "café.txt").write_text("ünïcode\n")
    run = version / "run.sh"
    run.write_text("#!/bin/sh\n")
    run.chmod(0o750)
    return version


def members(archive: bytes) -> list[tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        return tar.getmembers()


def extract(archive: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        if sys.version_info >= (3, 12) or hasattr(tarfile, "data_filter"):
            tar.extractall(destination, filter="data")
        else:  # pragma: no cover - Python 3.11 before 3.11.4
            tar.extractall(destination)


def test_round_trip_gives_the_tree_hash(tmp_path):
    version = make_version(tmp_path / "reg")
    out = tmp_path / "out"
    extract(pack(version, "tool", "orders", "1.2.3"), out)
    assert copy_and_hash(out / "tool" / "orders" / "1.2.3") == copy_and_hash(version)


def test_bytes_do_not_depend_on_mtime_owner_mode_or_umask(tmp_path):
    first = make_version(tmp_path / "a")
    old = os.umask(0o002)
    try:
        second = make_version(tmp_path / "b")
    finally:
        os.umask(old)
    for path in second.rglob("*"):
        os.utime(path, (1_700_000_000, 1_700_000_000))
    (second / "manifest.yaml").chmod(0o600)
    assert pack(first, "tool", "orders", "1.2.3") == pack(second, "tool", "orders", "1.2.3")


def test_entries_are_sorted_and_normalised(tmp_path):
    version = make_version(tmp_path / "reg")
    archive = pack(version, "tool", "orders", "1.2.3")
    header = gzip.GzipFile(fileobj=io.BytesIO(archive))
    header.read()
    assert header.mtime == 0
    assert archive[3] == 0  # FLG: no file name or comment in the gzip header
    infos = members(archive)
    names = [info.name for info in infos]
    assert names[:3] == ["tool", "tool/orders", "tool/orders/1.2.3"]
    assert names[3:] == sorted(names[3:])
    for info in infos:
        assert (info.mtime, info.uid, info.gid, info.uname, info.gname) == (0, 0, 0, "", "")
        assert info.isdir() or info.isreg()
        expected = 0o755 if info.isdir() or info.name.endswith("run.sh") else 0o644
        assert info.mode == expected, info.name
    assert "tool/orders/1.2.3/src/deep/café.txt" in names


@pytest.mark.parametrize(
    "kind,name,version",
    [("Tool", "x", "1.0.0"), ("tool", "../x", "1.0.0"), ("tool", "x", "01.0.0")],
)
def test_refuses_bad_prefix(tmp_path, kind, name, version):
    with pytest.raises(RegistryError) as raised:
        pack(make_version(tmp_path), kind, name, version)
    assert raised.value.code == "REF_INVALID"


def test_refuses_symlinks_and_size(tmp_path, monkeypatch):
    version = make_version(tmp_path / "reg")
    (version / "link").symlink_to(version / "run.sh")
    with pytest.raises(RegistryError, match="symlinks"):
        pack(version, "tool", "orders", "1.2.3")
    (version / "link").unlink()
    monkeypatch.setattr(registry, "MAX_BYTES", 4)
    with pytest.raises(RegistryError, match="too large"):
        pack(version, "tool", "orders", "1.2.3")
    with pytest.raises(RegistryError, match="real directory"):
        pack(tmp_path / "missing", "tool", "orders", "1.2.3")


def test_output_cap(tmp_path, monkeypatch):
    import agentbreeder.pack as packer

    version = make_version(tmp_path / "reg")
    monkeypatch.setattr(packer, "MAX_BYTES", 10)
    with pytest.raises(RegistryError, match="too large"):
        pack(version, "tool", "orders", "1.2.3")


def test_a_file_swapped_while_packing_is_refused(tmp_path, monkeypatch):
    import agentbreeder.pack as packer

    version = make_version(tmp_path / "reg")
    entries = registry._entries(version)

    def swapped(source):
        target = version / "src" / "orders.py"
        target.unlink()
        target.mkdir()
        return entries

    monkeypatch.setattr(packer, "_entries", swapped)
    with pytest.raises(RegistryError, match="changed while packing"):
        pack(version, "tool", "orders", "1.2.3")


def test_a_file_that_grows_while_packing_is_refused(tmp_path, monkeypatch):
    import agentbreeder.pack as packer

    version = make_version(tmp_path / "reg")
    entries = registry._entries(version)

    def grown(source):
        (version / "src" / "orders.py").write_text("x = 1\ny = 2\n")
        return entries

    monkeypatch.setattr(packer, "_entries", grown)
    with pytest.raises(RegistryError, match="changed while packing"):
        pack(version, "tool", "orders", "1.2.3")


def test_a_file_swapped_for_a_symlink_while_packing_is_refused(tmp_path, monkeypatch):
    import agentbreeder.pack as packer

    version = make_version(tmp_path / "reg")
    entries = registry._entries(version)

    def swapped(source):
        target = version / "src" / "orders.py"
        target.unlink()
        target.symlink_to(version / "run.sh")
        return entries

    monkeypatch.setattr(packer, "_entries", swapped)
    with pytest.raises(RegistryError, match="changed while packing"):
        pack(version, "tool", "orders", "1.2.3")


def test_skipped_entries_match_the_hasher(tmp_path):
    version = make_version(tmp_path / "reg")
    (version / "empty").mkdir()
    (version / "src" / "__pycache__").mkdir()
    (version / "src" / "__pycache__" / "orders.cpython-312.pyc").write_bytes(b"\0")
    (version / "src" / "stale.pyc").write_bytes(b"\0")
    archive = pack(version, "tool", "orders", "1.2.3")
    names = [info.name for info in members(archive)]
    assert "tool/orders/1.2.3/empty" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    out = tmp_path / "out"
    extract(archive, out)
    assert copy_and_hash(out / "tool" / "orders" / "1.2.3") == copy_and_hash(version)
