"""Try it (#823, M7): one chat turn with a project's production deployment through
`agentbreeder invoke NAME - --json` (console spec §4.5, §8.5; S-12).

Pure functions: the server checks the request with `turn_request`, builds the argv with
`turn_argv`, runs it as an uncached read with the message on stdin, and passes the answer through
`turn_answer` before the browser sees it.

- The message goes to the child's stdin, never argv (S-12a). Turns are not stored: no job, no job
  lines, nothing in `center.sqlite3` (which survives `--purge`); the browser keeps the
  conversation in memory.
- Only production: a name ending in `-candidate` is refused, and `--candidate` is never sent.
- The agent's answer is untrusted. Only an allow-listed set of fields is passed on, each checked
  for type and size; the reply stays text (the browser shows it through <Text>, with no Markdown
  or auto-linking, S-12b). A trace id is passed only as 32 lowercase hex characters; there is no
  trace link (`trace.url` stays null): the span tree is read on request instead (below).
  `cost` is the turn's cost in USD that `runtime/adk@1.2.0` reports (`cost_usd`, the sum of
  LiteLLM's per-call estimates, #906), passed only as a finite number from 0 up to `MAX_USD`;
  older runtimes don't report it and it is null.
- `customer_id` and `session_id` are sent as typed; the runtime does not authenticate them
  (S-12c, #780), which the screen says.
- A turn's trace (#907): `traces --trace ID --stack DIR --environment ENV --json` (contract:
  docs/traces.md), built by `trace_argv` and checked by `trace_answer`. Only the span tree's
  names, kinds, models, token counts, tool names, latency, status and error text are passed on.
  `traces` never reads prompts, replies or tool arguments and results; error text is redacted and
  cut by `traces` but is the runtime's own text, so it can quote what the agent was given.
"""

from __future__ import annotations

import math
import re
from typing import Any

from agentbreeder.center.files import BadRequest

# `agentfile.NAME_PATTERN` (a test keeps them equal; the server does not load the manifest code).
AGENT_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?$")
ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # `invoke.ID`: the runtime's session and customer rule
HEX32 = re.compile(r"[0-9a-f]{32}")  # trace, turn and deployment ids
CODE = re.compile(r"[A-Z][A-Z0-9_]{2,39}")
MAX_MESSAGE = 4000  # `invoke.MAX_MESSAGE`, the ADK runtime's own limit (characters)
TIMEOUT = 200  # `invoke`'s whole exchange has a 180 s deadline; its state checks come first
MAX_REPLY = 64 * 1024  # characters of reply passed on
MAX_ITEMS = 50  # tools and sources per turn
MAX_ITEM = 200  # characters per tool name or source title
MAX_SHORT = 100  # model alias, framework, runtime ref
MAX_ERROR = 300
MAX_USD = 10**6  # a turn's cost at or above this is not believed
# A turn's trace (#907): `traces`' own limits (phoenix_traces.py) bound what is passed on.
HEX16 = re.compile(r"[0-9a-f]{16}")  # span ids
KIND = re.compile(r"[A-Z]{1,20}")  # OpenInference span kinds, upper-cased by `traces`
TRACE_TIMEOUT = 150  # `traces` stops itself after 120 s (TRACES_LIMIT)
MAX_SPANS = 500  # `phoenix_traces.MAX_SPANS`
MAX_TEXT = 200  # `phoenix_traces.TEXT`
MAX_NOTES = 10
SPAN_STATUS = ("ok", "error", "unset")
ATTRIBUTION = ("exact", "window")


def turn_request(name: str, body: dict) -> dict:
    """The checked request: `{message, session, customer}`. Raises BadRequest."""
    if not AGENT_NAME.fullmatch(name):
        raise BadRequest("Invalid agent name", "NAME_INVALID")
    if name.endswith("-candidate"):
        raise BadRequest(
            "Try it calls production only; a candidate exists while its release runs",
            "CANDIDATE_REFUSED",
        )
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        raise BadRequest("Type a message first", "MESSAGE_INVALID")
    message = message.strip()
    if len(message) > MAX_MESSAGE:
        raise BadRequest(f"Messages are limited to {MAX_MESSAGE} characters", "MESSAGE_TOO_LONG")
    checked: dict[str, Any] = {"message": message}
    for field in ("session", "customer"):
        value = body.get(field)
        if value in (None, ""):
            checked[field] = None
        elif isinstance(value, str) and ID.fullmatch(value):
            checked[field] = value
        else:
            raise BadRequest(
                f"The {field} ID is 1-64 of A-Z, a-z, 0-9, _ and -", f"{field.upper()}_INVALID"
            )
    return checked


def turn_argv(project: dict, name: str, request: dict) -> list[str]:
    """`invoke NAME - --stack DIR --environment ENV --json`: the message is read from stdin."""
    argv = ["invoke", name, "-", "--stack", project["path"]]
    argv += ["--environment", project["environment"], "--json"]
    if request["session"]:
        argv += ["--session", request["session"]]
    if request["customer"]:
        argv += ["--customer", request["customer"]]
    return argv


def _text(value: object, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _count(value: object) -> int | None:
    # bool is an int subclass; a count is never one.
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**12:
        return value
    return None


def _usd(value: object) -> float | None:
    # bool is an int subclass; JSON can also carry NaN and Infinity.
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < MAX_USD:
        number = float(value)
        return number if math.isfinite(number) else None
    return None


def _names(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names = [item[:MAX_ITEM] for item in value[: MAX_ITEMS * 4] if isinstance(item, str) and item]
    return names[:MAX_ITEMS]


def _id(value: object, pattern: re.Pattern) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def maybe_sent(problem: dict) -> bool:
    """For a failure with no JSON answer (`invoke --json` prints one for each of its own coded
    errors): False when the child never ran or `invoke` stopped before sending (it could not
    start, the console is stopping, or exit 2, which `invoke` uses only when nothing was sent,
    such as a usage error); True otherwise (a timeout or a crash may come after the request)."""
    code, message = problem.get("code"), str(problem.get("message", ""))
    if code == "STOPPING":
        return False
    before = "could not start" in message or "(exit 2)" in message
    return not (code == "COMMAND_FAILED" and before)


def turn_answer(data: object, latency_ms: int) -> tuple[dict | None, dict | None]:
    """`(turn, None)` for a reply, or `(details, problem)`: `problem` is `{code, message}` and
    `details` says whether the message may have reached the agent (`sent`, so the screen can
    say a retry is a second turn)."""
    if not isinstance(data, dict):
        return None, {"code": "TRY_INVALID", "message": "The answer does not match its contract"}
    failure = data.get("error")
    if isinstance(failure, dict):  # invoke's coded error object (exit 1 or 2)
        code = failure.get("code")
        message = _text(failure.get("message"), MAX_ERROR) or "invoke failed"
        problem = {
            "code": code if isinstance(code, str) and CODE.fullmatch(code) else "INVOKE_FAILED",
            "message": message,
        }
        return {"sent": failure.get("sent") is True, "latency_ms": latency_ms}, problem
    response = data.get("response")
    status = _count(data.get("status"))
    if not isinstance(response, dict) or status is None:
        return None, {"code": "TRY_INVALID", "message": "The answer does not match its contract"}
    reply = response.get("reply")
    if status != 200 or not isinstance(reply, str):
        detail = _text(response.get("error"), MAX_ERROR) or "no reply in the answer"
        problem = {"code": "AGENT_ERROR", "message": f"The agent answered {status}: {detail}"}
        return {"sent": True, "latency_ms": latency_ms}, problem
    turn = {
        "deployment": _id(data.get("deployment"), HEX32),
        "reply": reply[:MAX_REPLY],
        "truncated": len(reply) > MAX_REPLY,
        "session": _id(response.get("session_id"), ID),
        "customer": _id(response.get("customer_id"), ID),
        "turn": _id(response.get("turn_id"), HEX32),
        "model": _text(response.get("model"), MAX_SHORT),
        "runtime": _text(response.get("runtime"), MAX_SHORT),
        "tools": _names(response.get("tools")),
        "sources": _names(response.get("sources")),
        "model_calls": _count(response.get("model_calls")),
        "tokens": {
            "prompt": _count(response.get("prompt_tokens")),
            "output": _count(response.get("output_tokens")),
        },
        "latency_ms": latency_ms,
        # Seam: #907 fills `url` from a recorded Phoenix origin.
        "trace": {"id": _id(response.get("trace_id"), HEX32), "url": None},
        "cost": _usd(response.get("cost_usd")),
    }
    return turn, None


# -- a turn's trace (#907) -----------------------------------------------------------------------


def trace_argv(project: dict, trace_id: str) -> list[str]:
    """`traces --trace ID --stack DIR --environment ENV --json`: a fixed argv; the id is checked
    by the route (32 lowercase hex characters)."""
    if not HEX32.fullmatch(trace_id):
        raise BadRequest("A trace ID is 32 lowercase hex characters", "TRACE_ID_INVALID")
    return [
        "traces", "--trace", trace_id, "--stack", project["path"],
        "--environment", project["environment"], "--json",
    ]  # fmt: skip


def _pick(value: object, choices: tuple[str, ...]) -> str | None:
    return value if isinstance(value, str) and value in choices else None


def _span_tokens(value: object) -> dict | None:
    if not isinstance(value, dict):
        return None
    prompt, completion = _count(value.get("prompt")), _count(value.get("completion"))
    if prompt is None and completion is None:
        return None
    return {"prompt": prompt, "completion": completion}


def _span(value: object) -> dict | None:
    if not isinstance(value, dict) or _id(value.get("span_id"), HEX16) is None:
        return None
    return {
        "span_id": value["span_id"],
        "parent_id": _id(value.get("parent_id"), HEX16),
        "name": _text(value.get("name"), MAX_TEXT) or "?",
        "kind": _id(value.get("kind"), KIND) or "UNKNOWN",
        "status": _pick(value.get("status"), SPAN_STATUS) or "unset",
        "latency_ms": _count(value.get("latency_ms")),
        "model": _text(value.get("model"), MAX_TEXT),
        "tokens": _span_tokens(value.get("tokens")),
        "tool": _text(value.get("tool"), MAX_TEXT),
        "error": _text(value.get("error"), MAX_ERROR),
    }


def trace_answer(data: object, trace_id: str) -> tuple[dict | None, dict | None]:
    """`(trace, None)`, or `(None, problem)` with `problem` `{code, message}`: `traces`' own coded
    error, TRACES_NOT_FOUND when its answer holds no trace with this id, or TRACE_INVALID when the
    answer does not match its contract."""
    invalid = {"code": "TRACE_INVALID", "message": "The trace answer does not match its contract"}
    if not isinstance(data, dict):
        return None, invalid
    failure = data.get("error")
    if isinstance(failure, dict):  # `traces --json`'s coded error object (exit 2)
        code = failure.get("code")
        return None, {
            "code": code if isinstance(code, str) and CODE.fullmatch(code) else "TRACES_FAILED",
            "message": _text(failure.get("message"), MAX_ERROR) or "traces failed",
        }
    traces = data.get("traces")
    if not isinstance(traces, list):
        return None, invalid
    found = next(
        (t for t in traces if isinstance(t, dict) and t.get("trace_id") == trace_id), None
    )
    if found is None:
        return None, {
            "code": "TRACES_NOT_FOUND",
            "message": f"trace {trace_id} is not in Phoenix (yet)",
        }
    raw = found.get("spans")
    spans: list[dict] = []
    seen: set[str] = set()
    for item in raw[:MAX_SPANS] if isinstance(raw, list) else []:
        span = _span(item)
        if span is not None and span["span_id"] not in seen:
            seen.add(span["span_id"])
            spans.append(span)
    tokens = found.get("tokens") if isinstance(found.get("tokens"), dict) else {}
    tools = found.get("tool_calls") if isinstance(found.get("tool_calls"), list) else []
    notes = data.get("notes") if isinstance(data.get("notes"), list) else []
    trace = {
        "trace_id": trace_id,
        "deployment_id": _id(found.get("deployment_id"), HEX32),
        "attribution": _pick(found.get("attribution"), ATTRIBUTION),
        "latency_ms": _count(found.get("latency_ms")),
        "status": _pick(found.get("status"), ("ok", "error")),
        "models": _names(found.get("models")),
        "tokens": {
            "prompt": _count(tokens.get("prompt")),
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
        "spans": spans,
        "truncated": found.get("spans_truncated") is True
        or (isinstance(raw, list) and len(raw) > MAX_SPANS),
        "notes": [n[:MAX_ERROR] for n in notes[:MAX_NOTES] if isinstance(n, str) and n],
    }
    return trace, None
