"""Factory for creating secrets backends and resolving secret:// references."""

from __future__ import annotations

import re

from engine.secrets.base import SecretsBackend

# Pattern for secret references in agent.yaml values: secret://KEY_NAME
_SECRET_REF_RE = re.compile(r'^secret://(.+)$')


def get_backend(backend: str = "env", **kwargs: object) -> SecretsBackend:
    """Create a secrets backend by name.

    Args:
        backend: One of "env", "aws", "gcp", "vault"
        **kwargs: Passed through to the backend constructor.

    Returns:
        A SecretsBackend instance ready for use.

    Raises:
        ValueError: If the backend name is not recognised.
        ImportError: If the backend's optional dependency is not installed.
    """
    backend = backend.lower()
    if backend == "env":
        from engine.secrets.env_backend import EnvBackend
        return EnvBackend(**kwargs)  # type: ignore[arg-type]
    if backend in ("aws", "aws_secrets_manager"):
        from engine.secrets.aws_backend import AWSSecretsManagerBackend
        return AWSSecretsManagerBackend(**kwargs)  # type: ignore[arg-type]
    if backend in ("gcp", "gcp_secret_manager"):
        from engine.secrets.gcp_backend import GCPSecretManagerBackend
        return GCPSecretManagerBackend(**kwargs)  # type: ignore[arg-type]
    if backend in ("vault", "hashicorp_vault"):
        from engine.secrets.vault_backend import VaultBackend
        return VaultBackend(**kwargs)  # type: ignore[arg-type]
    raise ValueError(
        f"Unknown secrets backend: '{backend}'. "
        "Supported: env, aws, gcp, vault"
    )


async def resolve_secret_refs(
    data: dict | list | str | object,
    backend: SecretsBackend,
) -> dict | list | str | object:
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
                    f"Set it with: garden secret set {key}"
                )
            return value
    return data


def find_secret_refs(data: dict | list | str | object) -> list[str]:
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
