"""The Traces tab of an agent (#907, console half): one deployment's recent traces through
`agentbreeder traces ID --stack DIR --environment ENV --last N --json` (contract: docs/traces.md),
run as an uncached CLI child.

Pure functions: the server checks the query with `list_request`, builds the argv with
`list_argv` and passes the answer through `list_answer` before the browser sees it.

- Phoenix is read only by the CLI child, with the stack's stored read key (the ingest key on
  phoenix 1.0.x); the console server never reads a credential. `traces` never reads prompts,
  replies or tool arguments and results, and redacts every string with the same values as `logs`.
- Only an allow-listed summary per trace is passed on: its id, start, latency, status,
  attribution, models, token counts, tool calls (name, status, latency), span count, whether
  it is still arriving (no root span yet, #1010), and the root span's facts (#1062,
  `tryit.route_facts`): gateway fallbacks and serving aliases, cached prompt tokens, refused
  unknown tool names, and a guardrail block's name and entity type. The span tree opens per
  trace through the Try it trace route (`traces --trace ID`, `tryit.trace_answer`), so this
  answer carries no spans.
  Phoenix's origin, the project name and the session and turn ids stay on the server.
- A trace attributed to another deployment is dropped: the CLI attributes every listed trace to
  the ID it was asked for, so one that is not is a contract break, never shown as this one's.
- A refusal (exit 2, `{"schema_version": 1, "error": {code, message}}`) comes back as the
  envelope's coded error.
"""

from __future__ import annotations

import re

from agentbreeder.center.files import BadRequest
from agentbreeder.center.tryit import (
    ATTRIBUTION,
    CODE,
    HEX32,
    MAX_ERROR,
    MAX_ITEMS,
    MAX_NOTES,
    MAX_SPANS,
    MAX_TEXT,
    SPAN_STATUS,
    _count,
    _id,
    _names,
    _pick,
    _text,
    route_facts,
)

DEFAULT_LAST = 20  # `phoenix_traces.DEFAULT_LAST`
MAX_LAST = 50  # the console's own cap (the CLI allows 200): each trace carries up to 500 spans
TIMEOUT = 150  # `traces` stops itself after 120 s (TRACES_LIMIT)
# `phoenix_traces` writes `datetime.isoformat()` of a UTC time.
WHEN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(\+00:00|Z)"
)
MAX_OVERLAPPING = 50
INVALID = {
    "code": "TRACES_INVALID",
    "message": "The traces answer does not match its contract; run `agentbreeder traces` in a "
    "terminal",
}


def list_request(query: dict[str, list[str]]) -> int:
    """The checked `last` (1 to MAX_LAST, default DEFAULT_LAST). Raises BadRequest."""
    last = query.get("last", [""])[0] or str(DEFAULT_LAST)
    if not re.fullmatch(r"[1-9][0-9]{0,2}", last) or int(last) > MAX_LAST:
        raise BadRequest(f"Last must be 1 to {MAX_LAST}", "LAST_INVALID")
    return int(last)


def list_argv(project: dict, deployment_id: str, last: int) -> list[str]:
    """`traces ID --stack DIR --environment ENV --last N --json`: a fixed argv; the ID is checked
    by the route (32 lowercase hex characters) and again here."""
    if not HEX32.fullmatch(deployment_id):
        raise BadRequest("A deployment ID is 32 lowercase hex characters", "ID_INVALID")
    if not 1 <= last <= MAX_LAST:
        raise BadRequest(f"Last must be 1 to {MAX_LAST}", "LAST_INVALID")
    return [
        "traces", deployment_id, "--stack", project["path"],
        "--environment", project["environment"], "--last", str(last), "--json",
    ]  # fmt: skip


def _when(value: object) -> str | None:
    return value if isinstance(value, str) and WHEN.fullmatch(value) else None


def _trace(found: object, deployment_id: str) -> dict | None:
    if not isinstance(found, dict) or _id(found.get("trace_id"), HEX32) is None:
        return None
    if found.get("deployment_id") != deployment_id:
        return None  # another deployment's trace: never shown as this one's
    tokens = found.get("tokens") if isinstance(found.get("tokens"), dict) else {}
    tools = found.get("tool_calls") if isinstance(found.get("tool_calls"), list) else []
    spans = found.get("spans")
    prompt = _count(tokens.get("prompt"))
    return {
        "trace_id": found["trace_id"],
        "start_time": _when(found.get("start_time")),
        "latency_ms": _count(found.get("latency_ms")),
        "status": _pick(found.get("status"), ("ok", "error")),
        "attribution": _pick(found.get("attribution"), ATTRIBUTION),
        "models": _names(found.get("models")),
        "tokens": {
            "prompt": prompt,
            "completion": _count(tokens.get("completion")),
            "total": _count(tokens.get("total")),
        },
        "tool_calls": [
            {
                "name": _text(item.get("name"), MAX_TEXT) or "?",
                "status": _pick(item.get("status"), SPAN_STATUS) or "unset",
                "latency_ms": _count(item.get("latency_ms")),
            }
            for item in tools[:MAX_SPANS]
            if isinstance(item, dict)
        ][:MAX_ITEMS],
        **route_facts(found, prompt),  # fallbacks, aliases, cached, unknown tools, guardrail
        "span_count": min(len(spans), MAX_SPANS) if isinstance(spans, list) else 0,
        "spans_truncated": found.get("spans_truncated") is True
        or (isinstance(spans, list) and len(spans) > MAX_SPANS),
        "incomplete": found.get("incomplete") is True,  # #1010: no root span yet
    }


def list_answer(data: object, deployment_id: str) -> tuple[dict | None, dict | None]:
    """`(answer, None)` with `answer` `{traces, overlapping, truncated, dropped, notes}`, or
    `(None, problem)` with `problem` `{code, message}`: `traces`' own coded error, or
    TRACES_INVALID when the answer does not match its contract."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return None, INVALID
    failure = data.get("error")
    if isinstance(failure, dict):  # `traces --json`'s coded error object (exit 2)
        code = failure.get("code")
        return None, {
            "code": code if isinstance(code, str) and CODE.fullmatch(code) else "TRACES_FAILED",
            "message": _text(failure.get("message"), MAX_ERROR) or "traces failed",
        }
    raw = data.get("traces")
    if not isinstance(raw, list):
        return None, INVALID
    traces: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    for found in raw[: MAX_LAST * 4]:
        trace = _trace(found, deployment_id)
        if trace is None or trace["trace_id"] in seen:
            dropped += 1
            continue
        seen.add(trace["trace_id"])
        traces.append(trace)
    traces = traces[:MAX_LAST]
    overlapping = data.get("overlapping") if isinstance(data.get("overlapping"), list) else []
    notes = data.get("notes") if isinstance(data.get("notes"), list) else []
    return {
        "traces": traces,
        "overlapping": [
            o for o in overlapping[:MAX_OVERLAPPING] if isinstance(o, str) and HEX32.fullmatch(o)
        ],
        "truncated": data.get("truncated") is True or len(raw) > MAX_LAST * 4,
        "dropped": dropped,
        "notes": [n[:MAX_ERROR] for n in notes[:MAX_NOTES] if isinstance(n, str) and n],
    }, None
