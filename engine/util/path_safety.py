"""Validators for user-supplied path fragments.

Use ``safe_relative_subdir(value)`` to validate that a user-supplied string
is a safe relative path: no null bytes, no leading ``/``, no leading ``~``,
no ``..`` path segments. Returns the original value unchanged when valid;
raises ``UnsafePathError`` otherwise.
"""

from __future__ import annotations

from pathlib import PurePosixPath


class UnsafePathError(ValueError):
    """Raised when a user-supplied path fragment is unsafe."""


def safe_relative_subdir(value: str) -> str:
    """Validate ``value`` is a safe relative-path fragment. Return as-is when valid.

    Empty string is allowed (caller means "use the base directory").

    Raises:
        UnsafePathError on null bytes, absolute paths, home expansion, or
        parent-directory traversal.
    """
    if value == "":
        return value
    if "\x00" in value:
        raise UnsafePathError(f"path must not contain null bytes: {value!r}")
    if value.startswith("/"):
        raise UnsafePathError(f"path must be relative, not absolute: {value!r}")
    if value.startswith("~"):
        raise UnsafePathError(f"path must not begin with home-directory expansion: {value!r}")
    parts = PurePosixPath(value).parts
    if any(part == ".." for part in parts):
        raise UnsafePathError(f"path must not contain parent-directory traversal: {value!r}")
    return value
