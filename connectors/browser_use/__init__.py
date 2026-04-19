"""browser_use connector package.

Issue #71: Computer use / browser agent deploy target.

Wraps the browser-use library as a discoverable MCP-compatible tool so it can
be referenced in agent.yaml as a tool with type: computer_use_20260401.
"""

from .connector import BrowserUseConnector

__all__ = ["BrowserUseConnector"]
