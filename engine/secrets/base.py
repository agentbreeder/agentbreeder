"""Abstract base class for secrets backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SecretEntry:
    """Metadata about a stored secret (value is never included in listings)."""

    name: str
    masked_value: str  # e.g. "••••abcd"
    backend: str  # "env" | "aws" | "gcp" | "vault" | "doppler"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "masked_value": self.masked_value,
            "backend": self.backend,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "tags": self.tags,
        }


def _mask(value: str) -> str:
    """Return a masked version of a secret value showing only the last 4 chars."""
    if len(value) <= 8:
        return "••••"
    return f"••••{value[-4:]}"


class SecretsBackend(ABC):
    """Interface that all secrets backends implement."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Short identifier for this backend (e.g. 'aws', 'gcp', 'env')."""

    @abstractmethod
    async def get(self, name: str) -> str | None:
        """Retrieve a secret value by name. Returns None if not found."""

    @abstractmethod
    async def set(self, name: str, value: str, *, tags: dict[str, str] | None = None) -> None:
        """Create or update a secret."""

    @abstractmethod
    async def delete(self, name: str) -> None:
        """Delete a secret. Raises KeyError if not found."""

    @abstractmethod
    async def list(self) -> list[SecretEntry]:
        """List all secrets (names + metadata only — never values)."""

    async def rotate(self, name: str, new_value: str) -> None:
        """Rotate a secret to a new value. Default: calls set()."""
        existing = await self.get(name)
        if existing is None:
            raise KeyError(f"Secret '{name}' not found in {self.backend_name} backend")
        await self.set(name, new_value)
