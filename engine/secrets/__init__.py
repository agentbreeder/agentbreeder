"""Secrets management — pluggable backend for resolving secret:// references."""

from engine.secrets.base import SecretEntry, SecretsBackend
from engine.secrets.factory import (
    SUPPORTED_BACKENDS,
    find_secret_refs,
    get_backend,
    get_workspace_backend,
    resolve_secret_refs,
)
from engine.secrets.keychain_backend import KeychainBackend
from engine.secrets.workspace import (
    DEFAULT_WORKSPACE_NAME,
    WORKSPACE_FILE_ENV,
    WorkspaceSecretsConfig,
    detect_default_backend,
    env_fallback_warning_once,
    load_workspace_secrets_config,
    reset_env_fallback_warning,
)

__all__ = [
    "DEFAULT_WORKSPACE_NAME",
    "SUPPORTED_BACKENDS",
    "WORKSPACE_FILE_ENV",
    "KeychainBackend",
    "SecretEntry",
    "SecretsBackend",
    "WorkspaceSecretsConfig",
    "detect_default_backend",
    "env_fallback_warning_once",
    "find_secret_refs",
    "get_backend",
    "get_workspace_backend",
    "load_workspace_secrets_config",
    "reset_env_fallback_warning",
    "resolve_secret_refs",
]
