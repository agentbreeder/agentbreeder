"""Deterministic `tar+gzip` of one registry version (M9b spec §6.2, built by M13 #763).

The archive holds the version at `<kind>/<name>/<version>/…` and is a pure function of the
version's tree: entries in sorted path order, directories before what they contain, mtime 0, uid
and gid 0, empty owner names, modes 0644 or 0755 (directories and executable files) only, and a
gzip header with mtime 0 and no file name. The entries come from `registry._entries`, so the same
checks apply as for hashing: regular files and directories only, no symlinks or hardlinks,
UTF-8 names, `MAX_FILES` and `MAX_BYTES`.

Extracting the archive and running `registry.copy_and_hash` on `<kind>/<name>/<version>` gives
the version's tree hash; a test pins that. The tar bytes are identical on every machine; the
gzip stream is identical for one zlib build, so a reader compares tree hashes, never archive
digests from another machine.
"""

from __future__ import annotations

import gzip
import io
import os
import re
import stat
import tarfile
from pathlib import Path

from agentbreeder.registry import MAX_BYTES, RegistryError, _entries, _shown

_PART = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")
GZIP_LEVEL = 9


def _info(name: str, *, directory: bool, executable: bool = False, size: int = 0):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = 0o755 if directory or executable else 0o644
    info.size = 0 if directory else size
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


def _read(path: Path, relative: str, size: int) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:  # removed, or swapped for a symlink (O_NOFOLLOW), after it was listed
        raise RegistryError("ENTRY_UNSAFE", f"changed while packing: {_shown(relative)}") from None
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RegistryError("ENTRY_UNSAFE", f"changed while packing: {_shown(relative)}")
    with os.fdopen(descriptor, "rb") as reader:
        data = reader.read(size + 1)
    if len(data) != size:
        raise RegistryError("ENTRY_UNSAFE", f"changed while packing: {_shown(relative)}")
    return data


def pack(version_dir: Path, kind: str, name: str, version: str) -> bytes:
    """The deterministic `tar+gzip` of `version_dir`, rooted at `<kind>/<name>/<version>/`."""
    if not (_PART.fullmatch(kind) and _PART.fullmatch(name) and _VERSION.fullmatch(version)):
        raise RegistryError("REF_INVALID", "pack needs a kind, a name and an X.Y.Z version")
    if version_dir.is_symlink() or not version_dir.is_dir():
        raise RegistryError("ENTRY_UNSAFE", f"{_shown(version_dir)} must be a real directory")
    prefix = f"{kind}/{name}/{version}"
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for part in (kind, f"{kind}/{name}", prefix):
            archive.addfile(_info(part, directory=True))
        for relative, path, info in _entries(version_dir):
            member = f"{prefix}/{relative}"
            if stat.S_ISDIR(info.st_mode):
                archive.addfile(_info(member, directory=True))
                continue
            data = _read(path, relative, info.st_size)
            entry = _info(
                member, directory=False, executable=bool(info.st_mode & 0o111), size=len(data)
            )
            archive.addfile(entry, io.BytesIO(data))
    compressed = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=compressed, compresslevel=GZIP_LEVEL, mtime=0
    ) as stream:
        stream.write(raw.getvalue())
    result = compressed.getvalue()
    if len(result) > MAX_BYTES:
        raise RegistryError("ENTRY_UNSAFE", "registry version is too large")
    return result
