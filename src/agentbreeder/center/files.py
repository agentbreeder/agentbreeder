"""The console's own files, and the ownership check for project directories (spec §10, S-01).

Standard library only, so the server process imports no engine module for them.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


class BadRequest(ValueError):
    """Safe to show: names what is wrong. `code` is a stable `[CODE]` for the envelope."""

    def __init__(self, message: str, code: str = "BAD_REQUEST"):
        super().__init__(message)
        self.code = code


class UnsafeDirectory(OSError):
    pass


def private_dir(path: Path) -> Path:
    """Create (0700) or check a directory: a real directory you own, never a symlink."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise UnsafeDirectory(f"{path} must be a directory you own (not a symlink)")
    if info.st_mode & 0o077:
        path.chmod(0o700)
    return path


def write_private(path: Path, data: bytes) -> None:
    """Atomically replace `path` with a 0600 file; neither it nor its temp is followed."""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _unsafe(path: Path, reason: str) -> BadRequest:
    return BadRequest(
        f"{path} {reason}; the console only runs projects you alone control", "PROJECT_UNSAFE"
    )


def check_ancestors(path: Path) -> None:
    """Each directory from `path` up to `/` is owned by you or root and not writable by group or
    others, unless it is sticky (`+t`, like `/tmp`), where nobody can rename another's entries."""
    uid = os.getuid()
    for directory in (path, *path.parents):
        try:
            info = os.lstat(directory)
        except OSError as error:
            raise _unsafe(directory, "cannot be read") from error
        if info.st_uid not in (uid, 0):
            raise _unsafe(directory, "is owned by another user")
        if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise _unsafe(directory, "is writable by other users")


def check_project(path: Path) -> None:
    """S-01: the project directory, `agent.yaml` and `stack.yaml` are yours and not writable by
    others, and so is every ancestor (`check_ancestors`). `path` must already be resolved."""
    uid = os.getuid()
    for item, kind in (
        (path, stat.S_ISDIR),
        (path / "agent.yaml", stat.S_ISREG),
        (path / "stack.yaml", stat.S_ISREG),
    ):
        need = (
            "That path must be a directory"
            if item == path
            else f"That directory needs a regular {item.name}"
        )
        try:
            info = os.lstat(item)
        except OSError as error:
            raise BadRequest(need) from error
        if not kind(info.st_mode):
            raise BadRequest(need)
        if info.st_uid != uid:
            raise _unsafe(item, "is owned by another user")
        if info.st_mode & 0o022:
            raise _unsafe(item, "is writable by other users")
    check_ancestors(path.parent)
