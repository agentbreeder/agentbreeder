"""The running turn, as context variables (spec §7.2.2).

The kit sets both for the turn before it calls the adapter. asyncio tasks and
`asyncio.to_thread` copy the context, so a framework's model calls (through the turn-aware HTTP
client) and its tool calls (through the kit's tool wrapper) see the turn that started them,
also under concurrent sessions and when the framework's agent was built once at start-up.

Stdlib only.
"""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass, field
from typing import Any

# The turn token the framework's model client sends as its API key (proxy.py).
TOKEN: contextvars.ContextVar[str | None] = contextvars.ContextVar("kit_turn_token", default=None)


@dataclass
class TurnContext:
    """What the tool wrapper needs from the running turn."""

    id: str
    session: str
    customer: str
    memory: Any  # memory.Memory
    trace: dict  # the reply's lists and counters
    agent: str  # the agent's name, on every tool row
    config: dict
    services: dict
    extra_searches: int = 0
    proxy_state: Any = None  # proxy.TurnState
    calls: dict = field(default_factory=dict)  # per-call bookkeeping, if a wrapper needs it


CURRENT: contextvars.ContextVar[TurnContext | None] = contextvars.ContextVar(
    "kit_turn", default=None
)


def current() -> TurnContext:
    turn = CURRENT.get()
    if turn is None:
        # A tool called outside a turn (a framework running it on a thread that did not copy
        # the context) fails instead of running without a customer.
        raise RuntimeError("no running turn")
    return turn


async def run_to_completion(function, *args, **kwargs):
    """Run blocking work (a tool, a DB query) in a thread. If the turn is cancelled meanwhile,
    wait for the thread to finish before propagating the cancellation: a thread cannot be
    interrupted, so the turn must not report "stopped" while its write is still in flight."""
    work = asyncio.ensure_future(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        await asyncio.wait({work})
        raise
