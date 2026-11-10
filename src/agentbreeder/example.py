"""Copy a shipped example out of the installed package (spec §5.4, plan S4).

`agentbreeder example list` and `agentbreeder example <name> [DIR]`. The examples ship inside the
package, so an installed AgentBreeder (pip, uvx, npx) carries them. The copy is driven by the
package's `_content.json`, never by walking the directory: local state next to a checkout's
examples (`.agentbreeder/`, `deploy/`, `stack/.secrets/`) is neither copied nor refused. Every file
is checked against the manifest (bytes and exec bit) before anything is written. The files are
staged in a hidden directory inside the destination and moved into place, so an existing empty
destination (even the working directory) keeps its own inode and attributes. On failure the copy
removes what it created (the staged files, moved entries, and any directories it made).
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

from agentbreeder import registry

DESCRIPTIONS = {
    "http-agent": "the local deploy sample: a JSON echo server (not an AI agent)",
    "northwind": "the Northwind support agent: agent.yaml plus its backing services (stack.yaml)",
}


class ExampleError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


def _package() -> Path:
    return registry._PACKAGED_ROOT.resolve()


def _manifest() -> dict[str, dict]:
    try:
        return registry._shipped_manifest(_package())
    except registry.RegistryError as error:
        raise ExampleError(error.code, str(error)) from error


def names() -> list[str]:
    return sorted(
        {
            key.split("/")[1]
            for key in _manifest()
            if key.startswith("examples/") and "/" in key[9:]
        }
    )


def _files(name: str) -> dict[str, dict]:
    prefix = f"examples/{name}/"
    files = {}
    for key, entry in sorted(_manifest().items()):
        if not key.startswith(prefix):
            continue
        relative = key.removeprefix(prefix)
        try:
            for part in relative.split("/"):
                registry._safe_name(part)
        except registry.RegistryError as error:
            raise ExampleError("CONTENT_MISMATCH", f"unsafe manifest path {key!r}") from error
        files[relative] = entry
    return files


def _read(path: Path, relative: str, entry: dict) -> bytes:
    """Read one shipped file without following symlinks; it must match the manifest."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ExampleError("CONTENT_MISMATCH", f"cannot read shipped file {relative}") from error
    with os.fdopen(descriptor, "rb") as reader:
        info = os.fstat(reader.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ExampleError("CONTENT_MISMATCH", f"not a regular file: {relative}")
        data = reader.read()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ExampleError("CONTENT_MISMATCH", f"differs from the shipped manifest: {relative}")
    if bool(info.st_mode & 0o111) != entry["exec"]:
        raise ExampleError("CONTENT_MISMATCH", f"exec bit differs from the manifest: {relative}")
    return data


def _check_destination(destination: Path, allow: Path | None = None) -> bool:
    """True if `destination` exists (and is an empty real directory, apart from `allow`)."""
    try:
        info = destination.lstat()
    except FileNotFoundError:
        return False
    except NotADirectoryError as error:
        raise ExampleError(
            "DESTINATION_UNSAFE", f"a parent of {destination} is not a directory"
        ) from error
    except OSError as error:
        raise ExampleError("COPY_FAILED", f"cannot inspect {destination}: {error}") from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ExampleError(
            "DESTINATION_UNSAFE", f"{destination} exists and is not a directory; pick a new path"
        )
    try:
        occupied = any(child != allow for child in destination.iterdir())
    except OSError as error:
        raise ExampleError("COPY_FAILED", f"cannot read {destination}: {error}") from error
    if occupied:
        raise ExampleError("DESTINATION_NOT_EMPTY", f"{destination} is not empty")
    return True


def _make_directories(destination: Path) -> list[Path]:
    """Create `destination` and any missing parents; return what was created, outermost first."""
    missing = []
    current = destination
    while not os.path.lexists(current):
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
    return list(reversed(missing))


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def copy(name: str, destination: Path) -> Path:
    """Copy example `name` to `destination` (absent, or an empty real directory)."""
    if name not in names():
        raise ExampleError(
            "EXAMPLE_UNKNOWN", f"no example {name!r}; available: {', '.join(names())}"
        )
    destination = Path(os.path.abspath(destination))
    existed = _check_destination(destination)
    source = _package() / "examples" / name
    files = _files(name)
    for relative, entry in files.items():  # check every file before writing anything
        _read(source / relative, relative, entry)
    created: list[Path] = []
    moved: list[Path] = []
    staging: Path | None = None
    try:
        created = [] if existed else _make_directories(destination)
        staging = Path(tempfile.mkdtemp(prefix=".agentbreeder-example-", dir=destination))
        for relative, entry in files.items():
            data = _read(source / relative, relative, entry)  # re-checked: what is written
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "xb") as writer:
                writer.write(data)
            os.chmod(target, 0o755 if entry["exec"] else 0o644)
        for directory in staging.rglob("*"):
            if directory.is_dir():
                os.chmod(directory, 0o755)
        _check_destination(destination, allow=staging)
        for entry in sorted(staging.iterdir()):
            target = destination / entry.name
            if os.path.lexists(target):
                raise ExampleError("DESTINATION_NOT_EMPTY", f"{target} appeared while copying")
            os.rename(entry, target)
            moved.append(target)
        staging.rmdir()
        if not existed:
            os.chmod(destination, 0o755)
    except BaseException as error:
        for target in reversed(moved):
            _remove(target)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        for directory in reversed(created):
            try:
                directory.rmdir()
            except OSError:
                pass
        if isinstance(error, OSError):
            code = (
                "DESTINATION_NOT_EMPTY"
                if error.errno in (errno.EEXIST, errno.ENOTEMPTY)
                else "COPY_FAILED"
            )
            raise ExampleError(code, f"cannot copy {name} to {destination}: {error}") from error
        raise
    return destination
