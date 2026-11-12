"""One console per user: an `flock` on `up.lock` in the center directory (spec §8.1 #4).

The lock file also holds the running console's URL (never a token), so a second `up` can print it.
The lock is released when the process exits, however it exits; children do not inherit it.
"""

from __future__ import annotations

import fcntl
import os
import re
from pathlib import Path

LOCK_NAME = "up.lock"
URL = re.compile(rb"http://127\.0\.0\.1:[0-9]{1,5}")
# Files a crashed console can leave behind (S-16): key env files for `services up` and, from the
# editor milestones on, editor temp files. Deleted at start, only once the lock is held.
LEFTOVER_PREFIXES = ("env-", "edit-", ".login.html.tmp")


class AlreadyRunning(Exception):
    def __init__(self, url: str | None):
        super().__init__(url or "")
        self.url = url


class SingleInstance:
    def __init__(self, directory: Path):
        self.path = directory / LOCK_NAME
        self.descriptor: int | None = None

    def acquire(self) -> None:
        """Take the lock or raise AlreadyRunning with the running console's URL, if known."""
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            data = os.pread(descriptor, 64, 0)
            os.close(descriptor)
            match = URL.fullmatch(data.strip())
            raise AlreadyRunning(match[0].decode() if match else None) from None
        except BaseException:
            os.close(descriptor)
            raise
        os.ftruncate(descriptor, 0)
        self.descriptor = descriptor

    def publish(self, url: str) -> None:
        """Record the URL for a second `up` to print."""
        assert self.descriptor is not None
        data = url.encode()
        os.ftruncate(self.descriptor, 0)
        os.pwrite(self.descriptor, data, 0)

    def withdraw(self) -> None:
        """Stop advertising the URL (the listener is closed) while still holding the lock."""
        if self.descriptor is not None:
            os.ftruncate(self.descriptor, 0)

    def release_quietly(self) -> None:
        """Release without touching the URL (a probe that found no console running)."""
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def release(self) -> None:
        if self.descriptor is not None:
            os.ftruncate(self.descriptor, 0)
            os.close(self.descriptor)  # closing the descriptor releases the flock
            self.descriptor = None


def remove_leftovers(directory: Path) -> list[str]:
    """S-16: delete key env files and editor temp files a crashed console left. Call only while
    holding the lock, so a second `up` never deletes a running console's files."""
    removed = []
    for path in directory.iterdir():
        if path.name.startswith(LEFTOVER_PREFIXES) and not path.is_dir():
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return sorted(removed)
