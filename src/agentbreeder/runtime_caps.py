"""What a `runtime/` version's `manifest.yaml` lists under `capabilities` (`mcp`, `subagents`,
`skills`, `skill_scripts`, ...).

Since #1083 F1 the engine reads a runtime through `runtime_profile`, which also gives its
limits, reserved tool names and protocol, and adds the capabilities a manifest without `protocol`
implies (`LEGACY_ADK`: `knowledge` and `notes`). This module keeps the manifest's own list for
callers that want exactly what the version declares.
"""

from __future__ import annotations

from pathlib import Path

from agentbreeder import runtime_profile
from agentbreeder.strict_yaml import StrictYamlError, load_file

CAPABILITY = runtime_profile.CAPABILITY
CapabilitiesError = runtime_profile.ProfileError


def runtime_capabilities(runtime_dir: Path) -> frozenset[str]:
    """The `capabilities` the runtime version in `runtime_dir` declares (none when the key is
    absent). `mcp` means it has an MCP client for the stack relay (runtime/adk@1.1.0 and
    later)."""
    try:
        meta = load_file(runtime_dir / "manifest.yaml")
    except StrictYamlError as error:
        raise CapabilitiesError("ARTIFACT_INVALID", str(error)) from error
    if not isinstance(meta, dict):
        raise CapabilitiesError("ARTIFACT_INVALID", "invalid capabilities")
    return runtime_profile.declared_capabilities(meta)
