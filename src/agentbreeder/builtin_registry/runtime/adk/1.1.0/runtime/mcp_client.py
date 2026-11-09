"""An agent's `mcp:` tools, called through the stack's MCP relay (#746).

One ADK McpToolset per server, over Streamable HTTP to the relay deploy names in
`AGENTBREEDER_MCP_<NAME>_URL`, with the per-deployment token from
`AGENTBREEDER_MCP_<NAME>_TOKEN_FILE`. The relay is the boundary: it holds the upstream credential,
checks the token's grant and forwards only granted, pinned tools. `tool_filter` here is a
convenience.

Named mcp_client (not mcp) so it can't shadow the `mcp` package ADK imports.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import config as cfg
import redaction
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

# ADK skips an MCP tool with one of these names (mcp_tool._RESERVED_TOOL_NAMES), so a granted
# tool would silently vanish: refuse it at startup instead.
RESERVED = frozenset(
    {
        "transfer_to_agent",
        "adk_request_credential",
        "adk_request_confirmation",
        "adk_request_input",
        "set_model_response",
    }
)
_TOKEN = re.compile(r"[0-9a-f]{64}")
# connect/write/pool, initialize and tools/list (ADK retries once). A tunable: a relay's
# tools/list may run a live upstream check; confirm against real upstreams in #748.
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 125.0  # per read while a tools/call reply streams; the relay's own cap is 120 s
TOOL_LIST_TTL = 60  # each tools/list counts against the relay's per-token rate limit


def remember_secret(value: str) -> None:
    """Values `redaction.redact()` replaces wherever the runtime stores, returns or exports
    text."""
    redaction.remember(value)


def redaction_values() -> frozenset[str]:
    return redaction.values()


@dataclass(frozen=True)
class Server:
    name: str
    version: str
    tools: tuple[str, ...]
    url: str
    token: str

    @property
    def ref(self) -> str:
        return f"mcp/{self.name}@{self.version}"

    @property
    def prefix(self) -> str:
        return self.name.replace("-", "_")  # ADK adds the `_` between prefix and tool name

    def names(self) -> dict[str, str]:
        """Prefixed tool name -> MCP tool name."""
        return {f"{self.prefix}_{tool}": tool for tool in self.tools}


def servers(config: dict) -> list[Server]:
    """The `mcp:` servers of agent.config.json, with the URL and token deploy provides."""
    result = []
    for entry in config.get("mcp") or []:
        name, version, tools = entry["name"], entry["version"], tuple(entry["tools"])
        env = name.upper().replace("-", "_")
        url = os.getenv(f"AGENTBREEDER_MCP_{env}_URL", "")
        parts = urlsplit(url)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise cfg.ConfigError(
                f"AGENTBREEDER_MCP_{env}_URL must be the relay's http(s) URL "
                "(agentbreeder deploy sets it)"
            )
        token = cfg.secret(f"AGENTBREEDER_MCP_{env}_TOKEN")
        if not _TOKEN.fullmatch(token):
            raise cfg.ConfigError(
                f"AGENTBREEDER_MCP_{env}_TOKEN must be 64 lowercase hex characters"
            )
        reserved = sorted(set(tools) & RESERVED)
        if reserved:
            raise cfg.ConfigError(
                f"mcp/{name}: tool names reserved by ADK would be skipped: {', '.join(reserved)}"
            )
        remember_secret(token)
        result.append(Server(name, version, tools, url, token))
    return result


def tool_index(found: list[Server], other_tools: list[str]) -> dict[str, Server]:
    """Prefixed MCP tool name -> its server. Every name the model sees must be unique: across
    servers (`a`+`b_c` vs `a_b`+`c`), and against function tools and knowledge search."""
    seen = {name: "a function tool" for name in other_tools}
    index = {}
    for server in found:
        for prefixed in server.names():
            if prefixed in seen:
                raise cfg.ConfigError(
                    f"MCP tool name {prefixed} ({server.ref}) clashes with {seen[prefixed]}"
                )
            seen[prefixed] = server.ref
            index[prefixed] = server
    return index


class RelayToolset(McpToolset):
    """ADK runs a turn without a toolset whose listing fails. This records that lost capability
    (trace and audit) before ADK's own handling applies, once per turn: ADK caches only a
    successful listing, so without the memo every model step would reconnect (2 attempts of up
    to CONNECT_TIMEOUT each against a stalled relay) and audit again."""

    def __init__(
        self,
        server: Server,
        on_unavailable: Callable[[Server, object, bool], Awaitable[bool]],
    ):
        token = server.token
        super().__init__(
            connection_params=StreamableHTTPConnectionParams(
                url=server.url,
                timeout=CONNECT_TIMEOUT,
                sse_read_timeout=READ_TIMEOUT,
                terminate_on_close=True,
            ),
            tool_filter=list(server.tools),
            tool_name_prefix=server.prefix,
            header_provider=lambda context: {"Authorization": f"Bearer {token}"},
            tool_list_cache_ttl_seconds=TOOL_LIST_TTL,
        )
        self.server = server
        self._on_unavailable = on_unavailable

    async def get_tools(self, readonly_context=None):
        """`on_unavailable(server, context, failed)` returns whether the server already failed
        in this turn; asked first (failed=False), and told on a failure (failed=True)."""
        if await self._on_unavailable(self.server, readonly_context, False):
            raise ConnectionError(f"{self.server.ref}: relay unavailable earlier in this turn")
        try:
            tools = await super().get_tools(readonly_context)
        except Exception:
            await self._on_unavailable(self.server, readonly_context, True)
            raise
        for tool in tools:
            tool.agentbreeder_mcp = self.server.ref  # survives ADK's prefixed copy.copy
        return tools


def failure(result) -> str | None:
    """The error of an MCP tool result. ADK returns MCP failures (relay refusals included) as
    `{"error": ...}` results, and `isError` results as plain results: neither raises."""
    if not isinstance(result, dict):
        return None
    if result.get("error"):
        return str(result["error"])
    if result.get("isError") or result.get("is_error"):
        texts = [
            str(item.get("text", ""))
            for item in result.get("content") or []
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return " ".join(texts) or "the MCP tool reported an error"
    return None


class _OneLine(logging.Formatter):
    """No tracebacks: ADK and the MCP SDK log a lost toolset or a failed call with exc_info."""

    def format(self, record):
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return super().format(record)


def quiet_library_logs() -> None:
    """One plain line per warning from ADK and the MCP SDK, never a multi-line traceback."""
    handler = logging.StreamHandler()
    handler.setFormatter(_OneLine("%(levelname)s %(name)s: %(message)s"))
    for name in ("google_adk", "mcp"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(logging.WARNING)
        logger.propagate = False
