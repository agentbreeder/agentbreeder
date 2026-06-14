"""Scripted streaming provider double.

Each entry in ``script`` is a list of StreamChunk objects representing one
generate_stream() turn. Successive generate_stream() calls pop the next turn.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from engine.providers.models import StreamChunk, ToolCall


class FakeProvider:
    def __init__(self, script: list[list[StreamChunk]]) -> None:
        self._script = list(script)
        self.calls: list[list[dict[str, Any]]] = []
        self.closed = False

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: Any = None,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append(list(messages))
        turn = self._script.pop(0) if self._script else []
        for chunk in turn:
            yield chunk

    async def close(self) -> None:
        self.closed = True


def text(s: str) -> StreamChunk:
    return StreamChunk(content=s)


def call(tool_id: str, name: str, args_json: str) -> StreamChunk:
    return StreamChunk(
        tool_calls=[ToolCall(id=tool_id, function_name=name, function_arguments=args_json)]
    )
