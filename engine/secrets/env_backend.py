"""Local .env file secrets backend — the default for development."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path

from engine.secrets.base import SecretEntry, SecretsBackend, _mask

# Keys we never migrate or list (they're infrastructure, not secrets)
_SKIP_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "TERM",
        "LANG",
        "PWD",
        "GARDEN_ENV",
        "DATABASE_URL",
        "REDIS_URL",
    }
)


def _find_env_file() -> Path:
    """Locate the nearest .env file — cwd first, then home/.garden/.env."""
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    return cwd_env  # will create in cwd on first write


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a key→value dict. Handles quoted values."""
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def _write_env_file(path: Path, data: dict[str, str]) -> None:
    """Write a key→value dict back to a .env file, preserving comments."""
    if path.exists():
        # Update existing file, preserving comments and order
        lines = path.read_text().splitlines()
        written: set[str] = set()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", stripped)
            if match:
                key = match.group(1)
                if key in data:
                    new_lines.append(f"{key}={data[key]}")
                    written.add(key)
                # If key not in data (deleted), skip it
            else:
                new_lines.append(line)
        # Append any new keys not already in file
        for key, value in data.items():
            if key not in written:
                new_lines.append(f"{key}={value}")
        path.write_text("\n".join(new_lines) + "\n")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(f"{k}={v}" for k, v in data.items()) + "\n")


class EnvBackend(SecretsBackend):
    """Secrets stored in a local .env file (default for development)."""

    def __init__(self, env_file: Path | str | None = None) -> None:
        self._path = Path(env_file) if env_file else _find_env_file()

    @property
    def backend_name(self) -> str:
        return "env"

    async def get(self, name: str) -> str | None:
        data = _parse_env_file(self._path)
        # Support both the exact key and an uppercased variant
        return data.get(name) or data.get(name.upper()) or os.environ.get(name)

    async def set(self, name: str, value: str, *, tags: dict[str, str] | None = None) -> None:
        data = _parse_env_file(self._path)
        data[name] = value
        _write_env_file(self._path, data)

    async def delete(self, name: str) -> None:
        data = _parse_env_file(self._path)
        if name not in data and name.upper() not in data:
            raise KeyError(f"Secret '{name}' not found in .env")
        data.pop(name, None)
        data.pop(name.upper(), None)
        _write_env_file(self._path, data)

    async def list(self) -> list[SecretEntry]:
        data = _parse_env_file(self._path)
        now = datetime.now(tz=UTC)
        return [
            SecretEntry(
                name=key,
                masked_value=_mask(value),
                backend="env",
                created_at=now,
                updated_at=now,
            )
            for key, value in data.items()
            if key not in _SKIP_KEYS and value  # skip empty and infra keys
        ]

    def list_raw(self) -> dict[str, str]:
        """Return raw key→value pairs (used by migrate). Not part of public API."""
        return {k: v for k, v in _parse_env_file(self._path).items() if k not in _SKIP_KEYS and v}
