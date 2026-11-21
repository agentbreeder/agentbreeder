"""How a callback names its turn (runtime/adk@1.5.1, #1058).

A turn is one /invoke request. The parent agent's callbacks carry the turn's own invocation
id. A sub-agent runs in its own ADK invocation, so its callbacks carry a different id: the
turn is then named by the session state key below, which only `SubAgentTool` sets, server-side,
when it creates the sub-agent's session. Nothing a model or a customer writes selects a turn.
"""

from __future__ import annotations

# Only the runtime sets it; no instruction or tool reads session state.
TURN_KEY = "agentbreeder:turn"


class TurnUnknown(Exception):
    """A callback's context names no live turn. Nothing is counted, capped or audited for it:
    the call fails instead (and in the parent agent, the turn ends with the fixed reply)."""

    def __init__(self):
        super().__init__("this call belongs to no running turn")


def caused_by(failure: BaseException | None, kind: type, depth: int = 0) -> bool:
    """Whether `failure`, anything in its cause chain or any member of an exception group in it
    is a `kind` (ADK wraps a plugin callback's exception in a RuntimeError)."""
    while failure is not None and depth < 20:
        if isinstance(failure, kind):
            return True
        if isinstance(failure, BaseExceptionGroup) and any(
            caused_by(member, kind, depth + 1) for member in failure.exceptions
        ):
            return True
        failure, depth = failure.__cause__ or failure.__context__, depth + 1
    return False
