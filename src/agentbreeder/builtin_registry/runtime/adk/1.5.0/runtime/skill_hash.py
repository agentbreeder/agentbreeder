"""The hash of a placed skill (runtime/adk@1.5.0, #1059). Standard library only.

`agentbreeder build` places a skill at `artifacts/skill/<name>/` (without `scripts/`) and
records this hash of the placed files in agent.config.json; the runtime computes it again at
start and refuses to start on a difference. `build` has its own copy of the algorithm, and a
repository test loads this file to check the two agree.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

HEADER = b"agentbreeder-skill-placed-v1\n"


def placed_files(directory: Path) -> dict[str, bytes]:
    """Every file under a placed skill directory, by POSIX relative path. Only regular files
    and directories are allowed (no links, nothing special), read without following links."""
    found: dict[str, bytes] = {}
    for parent, dirnames, filenames in os.walk(directory, followlinks=False):
        for name in sorted(dirnames + filenames):
            path = Path(parent, name)
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("only files and directories are allowed")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as reader:
                found[path.relative_to(directory).as_posix()] = reader.read()
    return found


def placed_hash(files: dict[str, bytes]) -> str:
    """`sha256:<hex>` over a fixed header, then for each file in path order its path and its
    bytes, each framed by its length. Modes and empty directories are not part of it."""
    digest = hashlib.sha256(HEADER)
    for relative in sorted(files):
        name, data = relative.encode(), files[relative]
        digest.update(b"%d:" % len(name) + name + b"%d:" % len(data) + data)
    return "sha256:" + digest.hexdigest()
