"""Secrets management — pluggable backend for resolving secret:// references."""

from engine.secrets.base import SecretEntry, SecretsBackend
from engine.secrets.factory import get_backend, resolve_secret_refs

__all__ = ["SecretsBackend", "SecretEntry", "get_backend", "resolve_secret_refs"]
