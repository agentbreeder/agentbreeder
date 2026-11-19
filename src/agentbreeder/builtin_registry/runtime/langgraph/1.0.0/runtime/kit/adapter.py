"""The adapter interface (spec §7.6): what one framework implements.

An adapter only translates between the kit and its framework. It gets the turn already
prepared (the system prompt with the customer's notes, the history as structured messages, the
message with the knowledge block, the functions it may offer the model) and returns the reply
text. Everything else in the reply, and every rule about models and tools, is the kit's.

The framework's model client must use `kit.base_url` and `kit.http_client()` (the turn-aware
client, whose auth hook sends the running turn's token), with its own retries off: the proxy
owns retries, cost, the step cap and coded errors. Its tools must call the `KitFunction`s it is
given, and nothing else.

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .tools import KitFunction
    from .turn import Kit


@dataclass(frozen=True)
class Turn:
    id: str
    system: str  # the agent's prompt, plus the customer's notes when there are any
    history: tuple[dict, ...]  # {"role": "user" | "assistant", "content": str}, oldest first
    message: str  # the customer's message, after the knowledge block when there is one
    functions: tuple[KitFunction, ...]


class Adapter(Protocol):
    framework: str

    def build(self, kit: Kit) -> None:
        """Once at start-up, after the proxy listens."""

    async def run_turn(self, turn: Turn) -> str:
        """Run one turn to the framework's final answer."""

    def close(self) -> None:
        """On shutdown."""
