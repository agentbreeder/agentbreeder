"""The Claude Agent SDK binding of the runtime kit (multi-framework spec §8.4).

- **The CLI runs in the other container, as another uid** (owner decision D15): the SDK's
  `query()` gets `HostTransport`, whose connection is the stdin and stdout of a CLI the CLI host
  starts for this turn (`cli_link.py`, `cli_host.py`). The SDK's control protocol and its
  in-process MCP server stay in the kit's process.
- **Built-in tools off:** `tools=[]` (the control F0 showed), `allowed_tools` only the kit's
  `mcp__agentbreeder__<name>`, every built-in the pinned CLI lists in `disallowed_tools`,
  `permission_mode="default"`, a deny-by-default `can_use_tool`, `setting_sources=[]`,
  `strict_mcp_config`, no hooks, agents, plugins or extra directories. The CLI host checks the
  command line keeps them.
- **`verbatim_prompts=True`** (D15): no `@path` expansion and no slash-command dispatch.
- **Tools:** one in-process SDK MCP server, `agentbreeder`, with one tool per kit function and
  its declared JSON Schema; every call goes through the kit's wrapper (audit row, TOOL span).
  The `mcp` package's own tracing middleware is removed from that server: it wrote a second
  span per tool call (`tools/call <name>`, which Phoenix reads as TOOL from
  `gen_ai.operation.name`) and three protocol spans per turn.
- **Traces:** the turn's trace context is bound to the proxy (`bind_trace`), so its LLM spans
  nest under the turn's `agent_turn` span.
- **History:** the CLI host writes the conversation's user and assistant text as the CLI's
  transcript in the turn's own directory, and the CLI resumes it (§8.4 (a)); the directory is
  deleted when the turn ends.
- **Model:** the CLI reaches only the kit's proxy (`anthropic_proxy.py`), with the turn token as
  ANTHROPIC_AUTH_TOKEN; the proxy owns cost, retries, coded errors and the step cap. The SDK's
  own `total_cost_usd` (Anthropic prices) is never read.
- **Concurrent turns** wait for one of `max_concurrent_turns` slots (`settings.json`) within the
  turn's own deadline (owner decision D16: a runtime setting). The value, 2, is from the
  measurement in `docs/superpowers/verification/F7-claude-agent-sdk.md`: the CLI container
  (512 MiB) peaked at up to 271 MiB with 2 concurrent Northwind turns and 388 MiB with 3.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultDeny,
    ResultMessage,
    ToolUseBlock,
    Transport,
    create_sdk_mcp_server,
    query,
    tool,
)
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import _configure_can_use_tool
from cli_link import CliLink, HostUnavailable
from kit.context import CURRENT, TOKEN
from link_protocol import PINNED_SDK, load_settings
from mcp.server._otel import OpenTelemetryMiddleware

LOGGERS = ("claude_agent_sdk", "mcp")
MCP_SERVER = "agentbreeder"
HOST_WAIT = 25.0  # within deploy's 30 s health budget
MAX_MESSAGE = 16 * 1024 * 1024  # one stream-json line from the CLI
# Every built-in tool CLI 2.1.317 lists (F0's 25 names, less the two MCP ones). A second layer
# behind `tools=[]`; taken from each CLI version, never copied forward.
BUILTIN_TOOLS = (
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "Agent",
    "NotebookEdit",
    "TodoWrite",
    "Skill",
    "ToolSearch",
    "DeferredToolPlaceholder",
    "LSP",
    "Monitor",
    "KillShell",
    "BashOutput",
    "EnterPlanMode",
    "ExitPlanMode",
    "SendMessage",
    "CronCreate",
)


class HostTransport(Transport):
    """The SDK's transport to a CLI in the CLI container."""

    def __init__(self, link: CliLink, turn: str, argv: list[str], env: dict):
        self.link = link
        self.turn = turn
        self.argv = argv
        self.env = env
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.ready = False

    async def connect(self) -> None:
        sock = await asyncio.to_thread(self.link.run, self.turn, self.argv, self.env)
        self.reader, self.writer = await asyncio.open_unix_connection(sock=sock, limit=MAX_MESSAGE)
        self.ready = True

    async def write(self, data: str) -> None:
        if self.writer is None or self.writer.is_closing():
            raise ConnectionError("the CLI connection is closed")
        self.writer.write(data.encode())
        await self.writer.drain()

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        return self._read()

    async def _read(self) -> AsyncIterator[dict[str, Any]]:
        if self.reader is None:
            return
        finished = False
        while True:
            try:
                line = await self.reader.readline()
            except (ValueError, asyncio.LimitOverrunError) as error:
                raise ConnectionError("the CLI sent an oversize line") from error
            if not line:
                if not finished:
                    # As the SDK's own transport raises on a CLI that exits badly: a CLI killed
                    # (OOM, the lease's deadline, a host restart) is a failed turn, never an
                    # empty reply.
                    raise ConnectionError("the CLI ended without a result")
                return
            try:
                message = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # non-JSON output is not a message
            if isinstance(message, dict):
                finished = finished or message.get("type") == "result"
                yield message

    async def end_input(self) -> None:
        if (
            self.writer is not None
            and not self.writer.is_closing()
            and self.writer.can_write_eof()
        ):
            with contextlib.suppress(OSError):
                self.writer.write_eof()

    async def close(self) -> None:
        self.ready = False
        if self.writer is not None:
            self.writer.close()
            with contextlib.suppress(OSError, asyncio.TimeoutError, ConnectionError):
                await asyncio.wait_for(self.writer.wait_closed(), 2.0)

    def is_ready(self) -> bool:
        return self.ready


async def deny(tool_name: str, tool_input: dict, context: Any) -> PermissionResultDeny:
    """Deny by default: only consulted for a tool outside `allowed_tools`, which `tools=[]`
    already refuses (F0 never saw it called)."""
    return PermissionResultDeny(message="This tool is not available.", interrupt=False)


def as_tool(function):
    """A kit function as an SDK MCP tool with its declared JSON Schema."""

    async def handler(arguments: dict) -> dict:
        text = await function(**(arguments if isinstance(arguments, dict) else {}))
        return {"content": [{"type": "text", "text": text}]}

    return tool(function.name, function.description or function.name, function.parameters)(handler)


class RunFailed(RuntimeError):
    """The CLI's run ended without an answer. The kit's proxy state still decides first: a
    coded model error, a withheld step, or a refused extra call after a main-loop answer."""


def reply_text(messages: list) -> str:
    """The turn's answer: the result's text. A run with no result, or one that ended in error,
    raises RunFailed; a last assistant message that still asked for a tool is no answer
    (§7.2.4) and gives ""."""
    results = [m for m in messages if isinstance(m, ResultMessage)]
    if not results or results[-1].is_error:
        raise RunFailed("the CLI's run ended without an answer")
    assistants = [m for m in messages if isinstance(m, AssistantMessage)]
    if assistants and any(isinstance(b, ToolUseBlock) for b in assistants[-1].content):
        return ""
    return str(results[-1].result or "")


def without_tracing(server: dict) -> dict:
    """The SDK MCP server config with the `mcp` package's OpenTelemetryMiddleware removed (the
    kit writes the one TOOL span per call)."""
    instance = server["instance"]
    instance.middleware = [
        m for m in instance.middleware if not isinstance(m, OpenTelemetryMiddleware)
    ]
    return server


class ClaudeAdapter:
    framework = "claude-agent-sdk"

    def __init__(self, link: CliLink | None = None, settings: dict | None = None):
        self.link = link
        self.settings = settings
        self.kit = None
        self.slots: asyncio.Semaphore | None = None

    def build(self, kit) -> None:
        if claude_agent_sdk.__version__ != PINNED_SDK:
            raise RuntimeError(f"this runtime was checked on claude-agent-sdk {PINNED_SDK}")
        self.kit = kit
        self.settings = self.settings or load_settings()
        self.slots = asyncio.Semaphore(self.settings["max_concurrent_turns"])
        if self.link is None:
            self.link = CliLink()
            self.link.start()
        if not self.link.wait_ready(HOST_WAIT):
            raise HostUnavailable("the CLI host did not connect")

    def options(self, turn, session_id: str) -> ClaudeAgentOptions:
        server = without_tracing(
            create_sdk_mcp_server(MCP_SERVER, tools=[as_tool(f) for f in turn.functions])
        )
        return ClaudeAgentOptions(
            tools=[],
            allowed_tools=[f"mcp__{MCP_SERVER}__{f.name}" for f in turn.functions],
            disallowed_tools=list(BUILTIN_TOOLS),
            permission_mode="default",
            can_use_tool=deny,
            setting_sources=[],
            strict_mcp_config=True,
            mcp_servers={MCP_SERVER: server},
            system_prompt=turn.system,
            model=self.kit.alias,
            resume=session_id if turn.history else None,
            verbatim_prompts=True,
            max_turns=2 * self.kit.max_steps + 4,  # a backstop the proxy's cap reaches first
        )

    def argv(self, options: ClaudeAgentOptions, message: str) -> list[str]:
        """The CLI's arguments, as the pinned SDK builds them for these options."""
        # The CLI path is a placeholder: the CLI host runs its own binary.
        built = dataclasses.replace(_configure_can_use_tool(options), cli_path="claude")
        return SubprocessCLITransport(prompt=message, options=built)._build_command()[1:]

    async def run_turn(self, turn) -> str:
        async with self.slots:
            session_id = str(uuid.uuid4())
            history = [dict(item) for item in turn.history]
            state = getattr(CURRENT.get(), "proxy_state", None)
            bind_trace = getattr(getattr(self.kit, "proxy", None), "bind_trace", None)
            if state is not None and bind_trace is not None:
                bind_trace(state)  # the turn's LLM spans nest under its `agent_turn` span
            handle = await asyncio.to_thread(self.link.lease, session_id, history)
            try:
                options = self.options(turn, session_id)
                transport = HostTransport(
                    self.link,
                    handle,
                    self.argv(options, turn.message),
                    {
                        "token": TOKEN.get() or "",
                        "alias": self.kit.alias,
                        "sdk_version": claude_agent_sdk.__version__,
                    },
                )
                messages = [
                    m
                    async for m in query(prompt=turn.message, options=options, transport=transport)
                ]
                return reply_text(messages)
            finally:
                await asyncio.shield(asyncio.to_thread(self.link.end, handle))

    def close(self) -> None:
        if self.link is not None:
            self.link.close()
