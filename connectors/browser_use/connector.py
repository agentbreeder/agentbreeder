"""Browser-use connector.

Issue #71: Computer use / browser agent deploy target.

Wraps the browser-use library as a deployable MCP-compatible tool so agent.yaml
can reference it as:

    tools:
      - name: browser
        type: computer_use_20260401

When deploy.cloud is claude-managed, the tool type is forwarded directly to the
Anthropic Managed Agents runtime and no container is built.  For containerised
deployments (aws/gcp/local) the tool is packaged as an MCP sidecar using the
standard engine/mcp/packager.py path.

The browser-use library (https://github.com/browser-use/browser-use) is an
optional dependency — the connector gracefully reports unavailability when it is
not installed.
"""

from __future__ import annotations

import logging

from connectors.base import BaseConnector

logger = logging.getLogger(__name__)

# Canonical tool-type string for Claude computer-use (2026-04 revision)
COMPUTER_USE_TOOL_TYPE = "computer_use_20260401"


class BrowserUseConnector(BaseConnector):
    """Connector that surfaces the browser-use library as a computer_use tool."""

    @property
    def name(self) -> str:
        return "browser_use"

    async def is_available(self) -> bool:
        """Return True if browser-use is installed in the current environment."""
        try:
            import browser_use  # noqa: F401

            return True
        except ImportError:
            return False

    async def scan(self) -> list[dict]:
        """Discover the browser-use tool and return a registry-compatible descriptor."""
        if not await self.is_available():
            logger.warning(
                "browser-use library not installed — skipping browser_use scan. "
                "Install with: pip install browser-use"
            )
            return []

        return [
            {
                "name": "browser-use",
                "description": (
                    "AI-powered browser automation — lets agents navigate the web, "
                    "fill forms, click elements, and extract structured data from any page."
                ),
                "source": self.name,
                "type": "mcp_server",
                "tool_type": COMPUTER_USE_TOOL_TYPE,
                "install": "pip install browser-use",
                "docs": "https://github.com/browser-use/browser-use",
                "tags": ["browser", "computer-use", "automation", "web"],
            }
        ]
