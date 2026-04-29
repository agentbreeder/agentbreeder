"""Workspace-level secrets configuration (Track K, scaffolds for issue #146).

A *workspace* is the unit that owns a set of secrets and a default secrets
backend. Track A (issue #146) will introduce the full workspace primitive
(name, members, env). Until that ships we keep a minimal config file at
``~/.agentbreeder/workspace.yaml`` with the shape::

    workspace: default
    secrets:
      backend: keychain          # env | keychain | aws | gcp | vault
      options:                   # backend-specific kwargs
        # region: us-east-1      (aws)
        # project_id: my-proj    (gcp)
        # mount: secret          (vault)

If the file does not exist we fall back to a sensible install-mode default
(see :func:`detect_default_backend`).

When issue #146 lands the registry will be the source of truth for these
values; the lookup helpers here are the integration point.
"""

from __future__ import annotations

import logging
import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

# Module-level guard for the env-fallback deprecation warning so we emit it
# at most once per process even if the workspace is loaded repeatedly.
_warned_env_fallback = False

DEFAULT_WORKSPACE_NAME = "default"
WORKSPACE_FILE_ENV = "AGENTBREEDER_WORKSPACE_FILE"


@dataclass(frozen=True)
class WorkspaceSecretsConfig:
    """Resolved workspace-level secrets configuration."""

    workspace: str
    backend: str
    options: dict[str, Any]
    source: str  # "config" | "default" | "env-fallback"


def _workspace_file_path() -> Path:
    """Return the workspace.yaml path, honoring the override env var."""
    override = os.environ.get(WORKSPACE_FILE_ENV)
    if override:
        return Path(override)
    return Path.home() / ".agentbreeder" / "workspace.yaml"


def detect_default_backend() -> str:
    """Pick a sensible secrets backend when no workspace config exists.

    Heuristics (matches Track K spec):

    * ``AGENTBREEDER_INSTALL_MODE=cloud``  →  ``aws``
    * ``AGENTBREEDER_INSTALL_MODE=team`` and ``VAULT_ADDR`` set →  ``vault``
    * ``AGENTBREEDER_INSTALL_MODE=team``                       →  ``env``
    * Otherwise (single-user CLI)                              →  ``keychain``
    """
    mode = os.environ.get("AGENTBREEDER_INSTALL_MODE", "").lower()
    if mode == "cloud":
        return "aws"
    if mode == "team":
        return "vault" if os.environ.get("VAULT_ADDR") else "env"
    return "keychain"


def load_workspace_secrets_config(
    *,
    path: Path | str | None = None,
    workspace: str | None = None,
) -> WorkspaceSecretsConfig:
    """Load the workspace secrets binding.

    Args:
        path: Override the workspace YAML path (mostly for tests).
        workspace: Override the workspace name (CLI flag).

    Returns:
        :class:`WorkspaceSecretsConfig` describing the chosen backend.

    Behaviour:
        * If the file exists and is valid → returns its values (``source=config``).
        * If the file is missing → returns the install-mode default
          (``source=default``).
        * If the file exists but the chosen backend cannot be resolved we still
          return what's in the file — the caller surfaces backend errors.
    """
    file_path = Path(path) if path else _workspace_file_path()
    if file_path.exists():
        try:
            return _parse_workspace_yaml(file_path, workspace_override=workspace)
        except Exception as exc:
            logger.warning(
                "Failed to parse workspace file %s: %s — falling back to defaults",
                file_path,
                exc,
            )

    # No workspace config present.
    backend = detect_default_backend()
    return WorkspaceSecretsConfig(
        workspace=workspace or DEFAULT_WORKSPACE_NAME,
        backend=backend,
        options={},
        source="default",
    )


def _parse_workspace_yaml(path: Path, *, workspace_override: str | None) -> WorkspaceSecretsConfig:
    """Parse a workspace.yaml file into a :class:`WorkspaceSecretsConfig`."""
    import yaml  # ruamel.yaml is also fine, but PyYAML ships with FastAPI

    text = path.read_text()
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Workspace file {path} must contain a mapping at top level")

    name = workspace_override or cast("str", data.get("workspace") or DEFAULT_WORKSPACE_NAME)
    secrets_section = data.get("secrets") or {}
    if not isinstance(secrets_section, dict):
        raise ValueError(f"Workspace file {path}: 'secrets' must be a mapping")

    backend = cast("str", secrets_section.get("backend") or detect_default_backend())
    options = secrets_section.get("options") or {}
    if not isinstance(options, dict):
        raise ValueError(f"Workspace file {path}: 'secrets.options' must be a mapping")

    return WorkspaceSecretsConfig(
        workspace=name,
        backend=str(backend).lower(),
        options=cast("dict[str, Any]", options),
        source="config",
    )


def env_fallback_warning_once() -> None:
    """Emit the env-backend fallback deprecation warning at most once."""
    global _warned_env_fallback
    if _warned_env_fallback:
        return
    _warned_env_fallback = True
    msg = (
        "No workspace secrets backend configured — falling back to the legacy "
        "'env' backend. Create ~/.agentbreeder/workspace.yaml or run "
        "'agentbreeder secret init' to choose a backend. "
        "(See https://www.agentbreeder.io/docs/secrets — TODO #146.)"
    )
    warnings.warn(msg, DeprecationWarning, stacklevel=2)
    logger.warning("%s", msg)


def reset_env_fallback_warning() -> None:
    """Test helper — reset the once-per-process warning flag."""
    global _warned_env_fallback
    _warned_env_fallback = False
