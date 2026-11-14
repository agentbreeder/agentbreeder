"""A tool's execution log in the console (#828, M12 console half, part 2): `agentbreeder tool log
REF --json` (contract: docs/commands/tool.md; M12 spec delta §4), run as an uncached CLI child.

Pure functions: the server checks the query with `log_request`, builds the argv with `log_argv`
and passes the answer through `log_answer` before the browser sees it.

- The database is read only by the CLI child, with the host-form secrets `services up` wrote; the
  console server never reads a credential (§3.5).
- Arguments and error text are customer data: they are asked for only with `content=1` (the
  screen's "Show arguments", behind the customer-data marker) and never cached, stored or logged.
  Without it they are not even selected, and any that come back are dropped here.
- The log is what the runtime claims it called: the agent's database role can insert any row.
  The CLI validates every value; this checks them again against local copies of its rules (tests
  keep them equal; the server does not load `agentbreeder.toollog`, which imports psycopg), so a
  changed or broken answer is an error, never a quiet empty log.
- A refusal (exit 2, `{"schema_version": 1, "error": {code, message}}`) comes back as the
  envelope's coded error; the screen shows it as "Unavailable" with the CLI's next step.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentbreeder import registry
from agentbreeder.center.files import BadRequest

# `toollog`'s own rules (a test keeps them equal).
TOOL_NAME = re.compile(rf"tool/{registry.NAME.pattern}")
TOOL_REF = re.compile(rf"tool/{registry.NAME.pattern}@{registry.VERSION.pattern}")
AGENT = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
DB_ROLE = re.compile(r"[a-z][a-z0-9_]{0,62}")
FUNCTION = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
HEX = re.compile(r"[0-9a-f]{1,64}")
SCHEMA = re.compile(r"[a-z_][a-z0-9_]{0,62}")
SINCE = re.compile(r"[1-9][0-9]{0,5}[smh]|[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z")
WINDOW = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
# `services/operator` publishes Phoenix on the loopback address only.
PHOENIX = re.compile(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}")
CODE = re.compile(r"[A-Z][A-Z0-9_]{2,39}")
MAX_LIMIT = 1000  # `toollog.MAX_LIMIT`
DEFAULT_LIMIT = 100  # `toollog.DEFAULT_LIMIT`
DEFAULT_SINCE = "24h"  # `toollog.DEFAULT_SINCE`
MAX_LATENCY = 86_400_000  # `toollog._latency`'s bound
ERROR_LIMIT = 2000  # `toollog.ERROR_LIMIT`
MAX_ARGS = 64 * 1024  # characters of one call's arguments, as JSON, passed on
MAX_NOTE = 400
MAX_NOTES = 10
MAX_MESSAGE = 500
# The CLI stops its query after 5 s; resolving the stack and connecting twice come first.
TIMEOUT = 60
STATUSES = ("ok", "error")
CREDENTIALS = ("console", "agent")
INVALID = {
    "code": "TOOL_LOG_INVALID",
    "message": "The tool log answer does not match its contract; run `agentbreeder tool log` "
    "in a terminal",
}


def log_request(query: dict[str, list[str]]) -> dict:
    """The checked query: `{ref, since, agent, limit, content}`. Raises BadRequest."""

    def one(name: str) -> str:
        return query.get(name, [""])[0]

    ref = one("ref")
    if not (TOOL_NAME.fullmatch(ref) or TOOL_REF.fullmatch(ref)):
        raise BadRequest("Ref must be tool/NAME or tool/NAME@X.Y.Z", "REF_INVALID")
    since = one("since") or DEFAULT_SINCE
    if not SINCE.fullmatch(since):
        raise BadRequest(
            "Since must look like 90s, 10m, 2h or 2026-11-11T09:00:00Z", "SINCE_INVALID"
        )
    agent = one("agent") or None
    if agent is not None and not AGENT.fullmatch(agent):
        raise BadRequest("Invalid agent name", "NAME_INVALID")
    limit = one("limit") or str(DEFAULT_LIMIT)
    if not re.fullmatch(r"[1-9][0-9]{0,3}", limit) or int(limit) > MAX_LIMIT:
        raise BadRequest(f"Limit must be 1 to {MAX_LIMIT}", "LIMIT_INVALID")
    content = one("content")
    if content not in ("", "0", "1"):
        raise BadRequest("Content is 1 or left out", "CONTENT_INVALID")
    return {
        "ref": ref,
        "since": since,
        "agent": agent,
        "limit": int(limit),
        "content": content == "1",
    }


def log_argv(project: dict, request: dict) -> list[str]:
    """`tool log` with the registered project and environment; only checked values from the
    request, never a path."""
    argv = ["tool", "log", request["ref"], "--project", project["path"]]
    argv += ["--environment", project["environment"], "--since", request["since"]]
    argv += ["--limit", str(request["limit"])]
    if request["agent"]:
        argv += ["--agent", request["agent"]]
    if request["content"]:
        argv.append("--content")
    return [*argv, "--json"]


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value if ch >= " " and not "\x7f" <= ch <= "\x9f")
    return cleaned[:limit]


def _match(pattern: re.Pattern, value: Any) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _count(value: Any, top: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
        return None
    return value


def _args(value: Any) -> dict | None:
    """One call's arguments as the CLI shaped them (redacted, cut, depth-limited); an object of
    JSON values only, else null. Bounded by its JSON size."""
    if not isinstance(value, dict):
        return None
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return None
    return value if len(text) <= MAX_ARGS else None


def _call(item: Any, content: bool) -> dict | None:
    if not isinstance(item, dict):
        return None
    status = item.get("status")
    call = {
        "at": _match(AT, item.get("at")),
        "agent": _match(AGENT, item.get("agent")),
        "tool": _match(TOOL_REF, item.get("tool")),
        "function": _match(FUNCTION, item.get("function")),
        "status": status if status in STATUSES else None,
        "latency_ms": _count(item.get("latency_ms"), MAX_LATENCY),
        "turn_id": _match(HEX, item.get("turn_id")),
        "trace_id": _match(HEX, item.get("trace_id")),
        "db_role": _match(DB_ROLE, item.get("db_role")),
    }
    if content:
        call["args"] = _args(item.get("args"))
        call["error"] = _text(item.get("error"), ERROR_LIMIT)
    return call


def log_answer(data: object, request: dict) -> tuple[dict | None, dict | None]:
    """`(log, None)`, or `(None, problem)` with `problem` `{code, message}`: the CLI's own coded
    refusal, or TOOL_LOG_INVALID when the answer does not match its contract. Only known keys go
    on; arguments only when they were asked for."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return None, INVALID
    failure = data.get("error")
    if isinstance(failure, dict):  # the coded refusal (exit 2)
        code = failure.get("code")
        return None, {
            "code": code if isinstance(code, str) and CODE.fullmatch(code) else "TOOL_LOG_FAILED",
            "message": _text(failure.get("message"), MAX_MESSAGE) or "tool log failed",
        }
    window = data.get("window")
    calls = data.get("calls")
    if (
        data.get("ref") != request["ref"]
        or data.get("content") is not request["content"]
        or data.get("credential") not in CREDENTIALS
        or not isinstance(window, dict)
        or not isinstance(calls, list)
        or len(calls) > request["limit"]
    ):
        return None, INVALID
    shaped = [_call(item, request["content"]) for item in calls]
    if any(call is None for call in shaped):
        return None, INVALID
    notes = data.get("notes") if isinstance(data.get("notes"), list) else []
    phantom = data.get("phantom_rows")
    limit = data.get("limit")
    return {
        "ref": request["ref"],
        "environment": _text(data.get("environment"), 64),
        "database": _text(data.get("database"), 64),
        "credential": data["credential"],
        "schema": _match(SCHEMA, data.get("schema")),
        "window": {
            "since": _match(WINDOW, window.get("since")),
            "until": _match(WINDOW, window.get("until")),
        },
        "limit": limit if _count(limit, MAX_LIMIT) else request["limit"],
        "truncated": data.get("truncated") is True,
        "content": request["content"],
        "db_role_verified": data.get("db_role_verified") is True,
        "phoenix_url": _match(PHOENIX, data.get("phoenix_url")),
        "phantom_rows": _count(phantom, 10**9) or 0,
        "notes": [note for note in (_text(n, MAX_NOTE) for n in notes[:MAX_NOTES]) if note],
        "calls": shaped,
    }, None
