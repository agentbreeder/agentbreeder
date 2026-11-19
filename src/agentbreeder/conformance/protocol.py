"""Agent protocol v1 as data (docs/agent-protocol.md is the prose; a test keeps them in step).

Protocol v1 is what `runtime/adk@1.3.0` serves (multi-framework spec §10.1). Nothing here is
read by `build` or `deploy`: it is the conformance kit's statement of what it checks.
"""

from __future__ import annotations

import re

VERSION = 1
PROFILES = ("v1", "kit")  # `kit` is v1 plus the additions in spec §10.2

HEALTH_PATH = "/health"
INVOKE_PATH = "/invoke"
MAX_BODY = 64 * 1024  # bytes of request body
MAX_MESSAGE = 4000  # characters of `message`
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # session_id and customer_id
GUEST = "guest"  # the customer when a request names none
ALLOWED_HOSTS = ("localhost", "127.0.0.1", "agent")
MAX_MODEL_ATTEMPTS = 3  # tries of one model call, the first included

NUMBER = (int, float)
# The reply to a turn that worked: field -> (accepted types, whether null is allowed).
REPLY_FIELDS: dict[str, tuple[tuple[type, ...], bool]] = {
    "reply": ((str,), False),
    "session_id": ((str,), False),
    "customer_id": ((str,), False),
    "model": ((str,), False),
    "framework": ((str,), False),
    "runtime": ((str,), False),
    "turn_id": ((str,), False),
    "trace_id": ((str,), True),
    "cost_usd": (NUMBER, True),
    "fallbacks": ((int,), True),
    "served_by": ((list,), False),
    "rate_limit_wait_s": (NUMBER, False),
    "sources": ((list,), False),
    "tools": ((list,), False),
    "web": ((list,), False),
    "tickets": ((list,), False),
    "memory_saved": ((list,), False),
    "kb_searches": ((int,), False),
    "model_calls": ((int,), False),
    "retrieval": ((str,), False),
    "prompt_tokens": ((int,), False),
    "output_tokens": ((int,), False),
    "mcp_unavailable": ((list,), False),
}

# A failed model call: code -> HTTP status. The body is `ERROR_FIELDS`.
MODEL_ERRORS = {
    "MODEL_BUDGET_EXCEEDED": 402,
    "MODEL_RATE_LIMITED": 429,
    "MODEL_ACCESS_DENIED": 502,
    "MODEL_REQUEST_REFUSED": 502,
    "MODEL_UNAVAILABLE": 502,
}
ERROR_FIELDS = ("error", "code", "correlation_id", "trace_id", "retry_after_s")

# Every status `/invoke` answers with, and when.
STATUSES = {
    200: "the turn's reply",
    400: "the body is not the JSON object v1 asks for, an id is not valid, or the body is "
    "empty, over 64 KiB or sent with Transfer-Encoding",
    402: "MODEL_BUDGET_EXCEEDED",
    404: "another path, or a Host that is not allowed",
    409: "a turn for this session is still running (1.3.0: only while a timed-out turn stops)",
    413: "the message is over 4,000 characters",
    415: "the Content-Type is not application/json",
    429: "MODEL_RATE_LIMITED",
    500: "the turn failed for another reason (a fixed message)",
    502: "MODEL_ACCESS_DENIED, MODEL_REQUEST_REFUSED or MODEL_UNAVAILABLE",
    503: "the agent's database is unavailable, or the agent is misconfigured",
    504: "the turn took longer than the runtime's deadline",
}

# The root span of a turn (spec §12): the attributes every runtime writes.
ROOT_SPAN = "agent_turn"
ROOT_ATTRIBUTES = ("session.id", "user.id", "agent.turn_id", "agent.framework", "agent.name")
SPAN_KIND = "openinference.span.kind"
DEPLOYMENT_ATTRIBUTE = "agentbreeder.deployment_id"

# Image labels a kit runtime's Dockerfile carries (spec §6.2). Earlier ADK images have none.
LABEL_RUNTIME = "org.agentbreeder.runtime"
LABEL_FRAMEWORK = "org.agentbreeder.framework"
LABEL_PROTOCOL = "org.agentbreeder.protocol"


def reply_problems(reply: object) -> list[str]:
    """What is wrong with a turn's reply against v1: missing fields and wrong types."""
    if not isinstance(reply, dict):
        return ["the reply is not a JSON object"]
    problems = []
    for name, (kinds, nullable) in REPLY_FIELDS.items():
        if name not in reply:
            problems.append(f"{name} is missing")
            continue
        value = reply[name]
        if value is None:
            if not nullable:
                problems.append(f"{name} is null")
        elif isinstance(value, bool) or not isinstance(value, kinds):
            problems.append(f"{name} has type {type(value).__name__}")
    for name in ("served_by", "sources", "mcp_unavailable"):
        value = reply.get(name)
        if isinstance(value, list) and not all(isinstance(item, str) for item in value):
            problems.append(f"{name} must hold strings")
    return problems
