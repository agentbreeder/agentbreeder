"""The shared provider-loop driver.

Drives one ``generate_stream()`` turn at a time, executing the model's
write/read/list/exec tool calls against the sandbox and feeding the results
back as OpenAI-format tool messages (role="tool", tool_call_id=...), which the
provider abstraction normalises per backend. Bounded by AgentBounds.
"""

from __future__ import annotations

import difflib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from engine.coding_agent.base import (
    CODING_TOOLS,
    TOOL_NAMES,
    AgentBounds,
    AgentEvent,
)
from engine.providers.models import ToolCall
from engine.sandbox.base import Sandbox

logger = logging.getLogger(__name__)


def _unified_diff(path: str, old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


async def _apply_tool(sandbox: Sandbox, tc: ToolCall) -> tuple[str, AgentEvent | None]:
    """Execute one tool call.

    Returns ``(result_text_for_model, optional file_change event)``.
    """
    try:
        args = json.loads(tc.function_arguments or "{}")
    except json.JSONDecodeError:
        return (f"ERROR: malformed arguments for {tc.function_name}", None)

    name = tc.function_name
    if name == "write_file":
        path, content = args.get("path", ""), args.get("content", "")
        try:
            old = await sandbox.read(path)
        except FileNotFoundError:
            old = ""
        await sandbox.write(path, content)
        diff = _unified_diff(path, old, content)
        return (
            f"wrote {path} ({len(content)} bytes)",
            AgentEvent(type="file_change", path=path, diff=diff),
        )
    if name == "read_file":
        try:
            return (await sandbox.read(args.get("path", "")), None)
        except FileNotFoundError:
            return (f"ERROR: file not found: {args.get('path')}", None)
    if name == "list_files":
        files = await sandbox.list(args.get("directory", "."))
        return ("\n".join(files), None)
    if name == "run_command":
        res = await sandbox.exec(args.get("cmd", []), timeout=float(args.get("timeout", 30)))
        body = f"exit={res.exit_code} timed_out={res.timed_out}\n{res.stdout}\n{res.stderr}"
        return (body, None)
    return (f"ERROR: unknown tool {name}", None)


async def run_coding_loop(
    *,
    provider: Any,
    model: str,
    system_prompt: str,
    instruction: str,
    history: list[dict[str, str]],
    sandbox: Sandbox,
    bounds: AgentBounds | None = None,
) -> AsyncIterator[AgentEvent]:
    """Run the coding agent until it stops calling tools or a bound trips."""
    bounds = bounds or AgentBounds()
    started = time.monotonic()
    tokens_seen = 0

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": instruction})

    for _turn in range(bounds.max_turns):
        if time.monotonic() - started > bounds.wall_clock_s:
            yield AgentEvent(type="done", text="stopped: wall-clock bound reached")
            return

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        async for chunk in provider.generate_stream(messages, model=model, tools=CODING_TOOLS):
            if chunk.content:
                text_parts.append(chunk.content)
                tokens_seen += len(chunk.content)
                yield AgentEvent(type="token", text=chunk.content)
            if chunk.tool_calls:
                tool_calls.extend(chunk.tool_calls)

        if not tool_calls:
            yield AgentEvent(type="done", text="".join(text_parts))
            return

        messages.append(
            {
                "role": "assistant",
                "content": "".join(text_parts),
                "tool_calls": [tc.model_dump() for tc in tool_calls],
            }
        )

        for tc in tool_calls:
            if tc.function_name not in TOOL_NAMES:
                result = f"ERROR: tool {tc.function_name} is not available"
                fc = None
            else:
                yield AgentEvent(type="tool_call", tool_name=tc.function_name)
                result, fc = await _apply_tool(sandbox, tc)
            if fc is not None:
                yield fc
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result[:20_000],
                }
            )

        if tokens_seen > bounds.max_tokens:
            yield AgentEvent(type="done", text="stopped: token bound reached")
            return

    yield AgentEvent(type="done", text="stopped: max turns reached")
