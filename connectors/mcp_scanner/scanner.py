"""MCP Server Auto-Discovery — scans for local MCP servers and registers them as tools.

Reads MCP server configurations from:
1. Project .mcp.json files
2. Well-known MCP config locations (~/.mcp.json)
3. Running processes on known MCP ports

Discovered servers are registered in the Tool registry with source="mcp_scanner".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from connectors.base import BaseConnector

logger = logging.getLogger(__name__)

# Default locations to search for MCP configurations
DEFAULT_MCP_CONFIG_PATHS = [
    Path.cwd() / ".mcp.json",
    Path.home() / ".mcp.json",
]

# Well-known MCP server ports to probe
DEFAULT_PROBE_PORTS = [3000, 3001, 3002, 3003, 3004, 3005]


class MCPScanner(BaseConnector):
    """Scans for MCP servers and returns discovered tools."""

    def __init__(
        self,
        config_paths: list[Path] | None = None,
        probe_ports: list[int] | None = None,
        probe_host: str = "localhost",
        timeout: float = 2.0,
    ) -> None:
        self._config_paths = config_paths if config_paths is not None else DEFAULT_MCP_CONFIG_PATHS
        self._probe_ports = probe_ports if probe_ports is not None else DEFAULT_PROBE_PORTS
        self._probe_host = probe_host
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "mcp_scanner"

    async def is_available(self) -> bool:
        """MCP scanner is always available (reads local config files)."""
        return True

    async def scan(self) -> list[dict]:
        """Scan for MCP servers from config files and network probes."""
        discovered: list[dict] = []

        # Phase 1: Read MCP config files
        for config_path in self._config_paths:
            found = self._scan_config_file(config_path)
            discovered.extend(found)

        # Phase 2: Probe well-known ports for running MCP servers
        probed = await self._probe_ports_for_mcp()
        # Only add probed servers that aren't already discovered by config
        known_names = {d["name"] for d in discovered}
        for server in probed:
            if server["name"] not in known_names:
                discovered.append(server)

        logger.info("MCP scanner discovered %d servers", len(discovered))
        return discovered

    def _scan_config_file(self, config_path: Path) -> list[dict]:
        """Parse an .mcp.json file and extract server definitions."""
        if not config_path.exists():
            logger.debug("MCP config not found: %s", config_path)
            return []

        try:
            data = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read MCP config %s: %s", config_path, e)
            return []

        servers = data.get("mcpServers", {})
        results: list[dict] = []

        for server_name, server_config in servers.items():
            command = server_config.get("command", "")
            args = server_config.get("args", [])
            description = server_config.get("description", f"MCP server: {server_name}")

            # Try to extract the npm package name from args
            package_name = _extract_package_name(args)

            results.append({
                "name": server_name,
                "description": description,
                "tool_type": "mcp_server",
                "source": "mcp_scanner",
                "endpoint": None,
                "schema_definition": {
                    "command": command,
                    "args": args,
                    "package": package_name,
                    "config_file": str(config_path),
                },
            })
            logger.debug("Found MCP server '%s' in %s", server_name, config_path)

        return results

    async def _probe_ports_for_mcp(self) -> list[dict]:
        """Probe well-known ports for running MCP-compatible HTTP servers."""
        results: list[dict] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for port in self._probe_ports:
                url = f"http://{self._probe_host}:{port}"
                server_info = await self._probe_single_port(client, url)
                if server_info:
                    results.append(server_info)

        return results

    async def _probe_single_port(
        self, client: httpx.AsyncClient, base_url: str
    ) -> dict | None:
        """Probe a single URL for an MCP-compatible server."""
        # Try common MCP/health endpoints
        for path in ["/health", "/", "/mcp"]:
            try:
                resp = await client.get(f"{base_url}{path}")
                if resp.status_code == 200:
                    body = resp.text
                    # Try to parse as JSON for server info
                    try:
                        data = resp.json()
                        name = data.get("name", f"mcp-{base_url.split(':')[-1]}")
                        description = data.get(
                            "description", f"MCP server at {base_url}"
                        )
                    except (json.JSONDecodeError, ValueError):
                        name = f"mcp-{base_url.split(':')[-1]}"
                        description = f"MCP server at {base_url}"

                    return {
                        "name": name,
                        "description": description,
                        "tool_type": "mcp_server",
                        "source": "mcp_scanner",
                        "endpoint": base_url,
                        "schema_definition": {"probed": True},
                    }
            except httpx.HTTPError:
                continue

        return None


def _extract_package_name(args: list[str]) -> str | None:
    """Extract npm package name from npx args like ['-y', '@modelcontextprotocol/server-filesystem']."""
    for arg in args:
        if arg.startswith("@") or (not arg.startswith("-") and "/" not in arg and arg != "-y"):
            return arg
    return None
