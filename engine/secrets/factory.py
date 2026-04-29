"""Factory for creating secrets backends and resolving secret:// references."""

from __future__ import annotations

import logging
import re
from typing import Any

from engine.secrets.base import SecretsBackend
from engine.secrets.workspace import (
    WorkspaceSecretsConfig,
    env_fallback_warning_once,
    load_workspace_secrets_config,
)

logger = logging.getLogger(__name__)

# Pattern for secret references in agent.yaml values: secret://KEY_NAME
_SECRET_REF_RE = re.compile(r"^secret://(.+)$")

SUPPORTED_BACKENDS = ("env", "keychain", "aws", "gcp", "vault")


def get_backend(backend: str | None = None, **kwargs: object) -> SecretsBackend:
    """Create a secrets backend by name.

    Args:
        backend: One of "env", "keychain", "aws", "gcp", "vault". When ``None``
            the workspace config (``~/.agentbreeder/workspace.yaml``) is consulted
            and, if absent, an install-mode-aware default is chosen. See
            :func:`engine.secrets.workspace.detect_default_backend`.
        **kwargs: Passed through to the backend constructor. Caller-supplied
            kwargs win over workspace defaults.

    Returns:
        A SecretsBackend instance ready for use.

    Raises:
        ValueError: If the backend name is not recognised.
        ImportError: If the backend's optional dependency is not installed.
    """
    if backend is None:
        # TODO(#146): once the workspace primitive lands, source the backend
        #             choice from the workspace registry instead of the local
        #             ~/.agentbreeder/workspace.yaml file.
        ws = load_workspace_secrets_config()
        backend = ws.backend
        merged: dict[str, Any] = {**ws.options, **kwargs}
        if backend == "keychain" and "workspace" not in merged:
            merged["workspace"] = ws.workspace
        if ws.source == "default" and ws.backend == "env":
            env_fallback_warning_once()
        kwargs = merged

    return _instantiate(str(backend), kwargs)


def _instantiate(backend: str, kwargs: dict[str, Any]) -> SecretsBackend:
    name = backend.lower()
    if name == "env":
        from engine.secrets.env_backend import EnvBackend

        return EnvBackend(**kwargs)
    if name == "keychain":
        from engine.secrets.keychain_backend import KeychainBackend

        return KeychainBackend(**kwargs)
    if name in ("aws", "aws_secrets_manager"):
        from engine.secrets.aws_backend import AWSSecretsManagerBackend

        return AWSSecretsManagerBackend(**kwargs)
    if name in ("gcp", "gcp_secret_manager"):
        from engine.secrets.gcp_backend import GCPSecretManagerBackend

        return GCPSecretManagerBackend(**kwargs)
    if name in ("vault", "hashicorp_vault"):
        from engine.secrets.vault_backend import VaultBackend

        return VaultBackend(**kwargs)
    raise ValueError(
        f"Unknown secrets backend: '{backend}'. Supported: {', '.join(SUPPORTED_BACKENDS)}"
    )


def get_workspace_backend(
    workspace: str | None = None,
) -> tuple[SecretsBackend, WorkspaceSecretsConfig]:
    """Resolve the workspace's configured secrets backend.

    Returns the live backend and the resolved :class:`WorkspaceSecretsConfig` so
    the caller can log or display the workspace name alongside it (CLI list
    command, auto-mirror logic, etc.).
    """
    ws = load_workspace_secrets_config(workspace=workspace)
    options: dict[str, Any] = dict(ws.options)
    if ws.backend == "keychain" and "workspace" not in options:
        options["workspace"] = ws.workspace
    if ws.source == "default" and ws.backend == "env":
        env_fallback_warning_once()
    backend = _instantiate(ws.backend, options)
    return backend, ws


async def resolve_secret_refs(
    data: dict[str, Any] | list[Any] | str | object,
    backend: SecretsBackend,
) -> dict[str, Any] | list[Any] | str | object:
    """Recursively resolve all secret:// references in a config dict.

    Walks the config tree. When a string value matches ``secret://KEY_NAME``,
    it is replaced with the live value from the secrets backend. Non-secret
    values are returned unchanged.

    Args:
        data:    Parsed agent.yaml (or any nested dict/list structure).
        backend: The secrets backend to fetch values from.

    Returns:
        The same structure with all secret:// references replaced.

    Raises:
        ValueError: If a referenced secret is not found in the backend.
    """
    if isinstance(data, dict):
        return {k: await resolve_secret_refs(v, backend) for k, v in data.items()}
    if isinstance(data, list):
        return [await resolve_secret_refs(item, backend) for item in data]
    if isinstance(data, str):
        match = _SECRET_REF_RE.match(data)
        if match:
            key = match.group(1)
            value = await backend.get(key)
            if value is None:
                raise ValueError(
                    f"Secret '{key}' referenced in config but not found in "
                    f"'{backend.backend_name}' backend. "
                    f"Set it with: agentbreeder secret set {key}"
                )
            return value
    return data


def find_secret_refs(data: dict[str, Any] | list[Any] | str | object) -> list[str]:
    """Return a list of all secret key names referenced in a config.

    Useful for pre-flight checks: collect all secret:// references, then
    verify they all exist in the backend before starting a deploy.
    """
    refs: list[str] = []
    if isinstance(data, dict):
        for v in data.values():
            refs.extend(find_secret_refs(v))
    elif isinstance(data, list):
        for item in data:
            refs.extend(find_secret_refs(item))
    elif isinstance(data, str):
        match = _SECRET_REF_RE.match(data)
        if match:
            refs.append(match.group(1))
    return refs
