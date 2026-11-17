"""What a `runtime/` version supports beyond serving an agent, read from its `manifest.yaml`
`capabilities` list (`mcp`, `subagents`, `skills`, `skill_scripts`, ...).

One parser for every caller: `build` refuses an agent whose parts its runtime cannot run, and
`new agent --from-spec` (`agentspec`) refuses the same spec before it writes a project that
could not build (#1058 SP, console spec §4.1). It loads only `strict_yaml`, so the console's
server process may import it.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentbreeder.strict_yaml import StrictYamlError, load_file

CAPABILITY = re.compile(r"[a-z][a-z0-9_-]{0,31}")


class CapabilitiesError(ValueError):
    """The manifest cannot be read, or its `capabilities` is not a list of distinct names. The
    message never holds manifest text beyond the strict loader's own location."""


def runtime_capabilities(runtime_dir: Path) -> frozenset[str]:
    """The `capabilities` of the runtime version in `runtime_dir` (none when the key is
    absent). `mcp` means it has an MCP client for the stack relay (runtime/adk@1.1.0 and
    later)."""
    try:
        meta = load_file(runtime_dir / "manifest.yaml")
    except StrictYamlError as error:
        raise CapabilitiesError(str(error)) from error
    values = meta.get("capabilities", []) if isinstance(meta, dict) else None
    if (
        not isinstance(values, list)
        or not all(isinstance(v, str) and CAPABILITY.fullmatch(v) for v in values)
        or len(set(values)) != len(values)
    ):
        raise CapabilitiesError("invalid capabilities")
    return frozenset(values)
