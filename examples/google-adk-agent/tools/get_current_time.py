"""Local override of the `get_current_time` tool for the gemini-assistant agent.

Resolved via ``engine.tool_resolver.resolve_tool('tools/get-current-time')``
from agent.py at module load.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
}


def get_current_time() -> dict:
    """Return the current UTC time as an ISO 8601 string."""
    return {"utc_time": datetime.now(UTC).isoformat()}
