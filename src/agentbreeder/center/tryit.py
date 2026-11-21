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
- A failed model call (`runtime/adk@1.3.0` and later, #1062): when the agent's error answer has a
  `code` in MODEL_CODES, that code replaces AGENT_ERROR, with the runtime's fixed message (it has
  the next step), the `correlation_id` (the turn id, which the agent's log line carries) and, for a
  rate limit, `retry_after_s`. A successful turn also passes on the gateway's `fallbacks` and
  `served_by` aliases (1.3.0), and `unknown_tools` (tool names the model asked for that the agent
  does not have; the runtime refused them) and `cached_prompt_tokens` (1.4.0). Older runtimes send
  none of these, and each is null or empty.
- `customer_id` and `session_id` are sent as typed; the runtime does not authenticate them
  (S-12c, #780), which the screen says.
- A turn's trace (#907): `traces --trace ID --stack DIR --environment ENV --json` (contract:
  docs/traces.md), built by `trace_argv` and checked by `trace_answer`. Only the span tree's
  names, kinds, models, token counts (and whether each span's counts are in the trace's total,
  #1009), tool names, latency, status and error text are passed on, with whether the trace is
  still arriving (no root span yet, #1010), and the root span's facts (`route_facts`: fallbacks,
  serving aliases, cached prompt tokens, refused unknown tool names and a guardrail block's name
  and entity type, never the matched text).
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
# A failed model call's code from runtime/adk@1.3.0, and MODEL_BLOCKED (a gateway guardrail,
# runtime/adk@1.4.0): `commands/invoke.py` MODEL_CODES (a test keeps them equal). Any other
# error answer is AGENT_ERROR.
MODEL_CODES = frozenset(
    {
        "MODEL_BLOCKED",
        "MODEL_BUDGET_EXCEEDED",
        "MODEL_RATE_LIMITED",
        "MODEL_ACCESS_DENIED",
        "MODEL_REQUEST_REFUSED",
        "MODEL_UNAVAILABLE",
        # A kit runtime's step cap (agent protocol "Profiles", #1083 F2b; Try it's line, #1085 S2):
        # the turn reached its model-call limit with the model still asking for tools.
        "MODEL_STEP_LIMIT",
    }
)
MAX_MODEL_ERROR = 600  # the runtime's fixed messages are about 250 characters
MAX_RETRY_AFTER = 24 * 3600  # seconds; a longer wait is not believed
MAX_ROUTE = 10  # aliases and unknown tool names: the runtime and `traces` keep at most 10
# `phoenix_traces.ALIAS`, `.UNKNOWN_TOOL` and `.ENTITY` (runtime/adk@1.3.0 and 1.4.0).
ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
UNKNOWN_TOOL = re.compile(r"[A-Za-z0-9_.?\[\]-]{1,80}")
ENTITY = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
# A turn's sub-agents and skills (runtime/adk@1.5.0, #1193): `agentfile.SUBAGENT_NAME` and
# `MAX_SUBAGENTS`, `registry.NAME` and `VERSION`, `skill_check.REFERENCE_NAME` and
# `REFERENCES_MAX` (a test keeps them equal), and agent.yaml's 10 skills.
SUBAGENT = re.compile(r"[a-z][a-z0-9_]{0,31}")
MAX_SUBAGENTS = 5
SKILL = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")
REFERENCE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\.(md|txt)")
MAX_RESOURCES = 20
MAX_SKILLS = 10


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


def _matching(value: object, pattern: re.Pattern, limit: int = MAX_ROUTE) -> list[str]:
    """The strings among the first `limit` items of `value` that match `pattern`; anything else
    is dropped."""
    if not isinstance(value, list):
        return []
    return [item for item in value[:limit] if isinstance(item, str) and pattern.fullmatch(item)]


def _entries(value: object, limit: int, pattern: re.Pattern) -> list[dict]:
    """At most `limit` objects of `value` whose `name` matches `pattern`, from the first
    `limit * 4` items; anything else is dropped."""
    if not isinstance(value, list):
        return []
    found = [
        item
        for item in value[: limit * 4]
        if isinstance(item, dict) and _id(item.get("name"), pattern)
    ]
    return found[:limit]


def _subagents(value: object) -> list[dict]:
    """A turn's `subagents[]` (runtime/adk@1.5.0): each sub-agent's name, how many times it was
    called, its model calls and the tool names it called. Nothing else (no cost or aliases)."""
    return [
        {
            "name": item["name"],
            "calls": _count(item.get("calls")),
            "model_calls": _count(item.get("model_calls")),
            "tools": _names(item.get("tools")),
        }
        for item in _entries(value, MAX_SUBAGENTS, SUBAGENT)
    ]


def _skills(value: object) -> list[dict]:
    """A turn's `skills[]` (runtime/adk@1.5.0): each skill loaded, its version and the reference
    files read from it."""
    return [
        {
            "name": item["name"],
            "version": _id(item.get("version"), VERSION),
            "resources": _matching(item.get("resources"), REFERENCE, MAX_RESOURCES),
        }
        for item in _entries(value, MAX_SKILLS, SKILL)
    ]


def _seconds(value: object) -> int | None:
    """`retry_after_s` as whole seconds (rounded up), or None when it is not a believable wait."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or not 0 < value <= MAX_RETRY_AFTER:
        return None
    return math.ceil(value)


def _cached(value: object, prompt: int | None) -> int | None:
    """Cached prompt tokens are a part of the prompt tokens: a figure above them is dropped."""
    cached = _count(value)
    return cached if cached is not None and (prompt is None or cached <= prompt) else None


def _guardrail(value: object) -> dict | None:
    """`traces`' `{"action": "blocked", "name", "entity"}` (runtime/adk@1.4.0), else None. Only
    the guardrail's name and the entity type; `traces` never reads the matched text."""
    if not isinstance(value, dict) or value.get("action") != "blocked":
        return None
    return {
        "action": "blocked",
        "name": _id(value.get("name"), ALIAS),
        "entity": _id(value.get("entity"), ENTITY),
    }


def route_facts(found: dict, prompt: int | None) -> dict:
    """A trace's root-span facts from `traces --json` (docs/traces.md): the gateway's fallbacks
    and serving aliases (runtime/adk@1.3.0), cached prompt tokens, refused unknown tool names and
    a guardrail block (1.4.0). Null or empty for older runtimes."""
    return {
        "fallbacks": _count(found.get("fallbacks")),
        "served_by": _matching(found.get("served_by"), ALIAS),
        "cached_prompt_tokens": _cached(found.get("cached_prompt_tokens"), prompt),
        "unknown_tools": _matching(found.get("unknown_tools"), UNKNOWN_TOOL),
        "guardrail": _guardrail(found.get("guardrail")),
    }


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
        code = response.get("code")
        if status != 200 and isinstance(code, str) and code in MODEL_CODES:
            # The runtime's fixed message for the code, with its next step (#1062).
            detail = _text(response.get("error"), MAX_MODEL_ERROR) or "the model call failed"
            details = {
                "sent": True,
                "latency_ms": latency_ms,
                "status": status,
                "correlation_id": _id(response.get("correlation_id"), HEX32),
                "retry_after_s": _seconds(response.get("retry_after_s"))
                if code == "MODEL_RATE_LIMITED"
                else None,
            }
            return details, {"code": code, "message": f"The agent answered {status}: {detail}"}
        detail = _text(response.get("error"), MAX_ERROR) or "no reply in the answer"
        problem = {"code": "AGENT_ERROR", "message": f"The agent answered {status}: {detail}"}
        return {"sent": True, "latency_ms": latency_ms}, problem
    prompt = _count(response.get("prompt_tokens"))
    turn = {
        "deployment": _id(data.get("deployment"), HEX32),
        "reply": reply[:MAX_REPLY],
        "truncated": len(reply) > MAX_REPLY,
        "session": _id(response.get("session_id"), ID),
        "customer": _id(response.get("customer_id"), ID),
        "turn": _id(response.get("turn_id"), HEX32),
        "model": _text(response.get("model"), MAX_SHORT),
        "runtime": _text(response.get("runtime"), MAX_SHORT),
        # The runtime's framework, as its manifest names it (agent protocol v1, #1085 S2).
        "framework": _text(response.get("framework"), MAX_SHORT),
        "tools": _names(response.get("tools")),
        "sources": _names(response.get("sources")),
        "model_calls": _count(response.get("model_calls")),
        "tokens": {
            "prompt": prompt,
            "output": _count(response.get("output_tokens")),
            # runtime/adk@1.4.0 (#1056): a part of `prompt`, as the provider reported it.
            "cached": _cached(response.get("cached_prompt_tokens"), prompt),
        },
        # runtime/adk@1.3.0 (#1039): how many gateway fallbacks were tried, and which aliases
        # served the turn's model calls.
        "fallbacks": _count(response.get("fallbacks")),
        "served_by": _matching(response.get("served_by"), ALIAS),
        # runtime/adk@1.4.0 (#1074): tool names the model asked for that the agent does not have;
        # the runtime refused them, so they are not tool calls.
        "unknown_tools": _matching(response.get("unknown_tools"), UNKNOWN_TOOL),
        # runtime/adk@1.5.0 (#1193): the sub-agents called and the skills loaded this turn.
        "subagents": _subagents(response.get("subagents")),
        "skills": _skills(response.get("skills")),
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
    tokens = _span_tokens(value.get("tokens"))
    counted = value.get("tokens_counted")
    return {
        "span_id": value["span_id"],
        "parent_id": _id(value.get("parent_id"), HEX16),
        "name": _text(value.get("name"), MAX_TEXT) or "?",
        "kind": _id(value.get("kind"), KIND) or "UNKNOWN",
        "status": _pick(value.get("status"), SPAN_STATUS) or "unset",
        "latency_ms": _count(value.get("latency_ms")),
        "model": _text(value.get("model"), MAX_TEXT),
        "tokens": tokens,
        "tool": _text(value.get("tool"), MAX_TEXT),
        "error": _text(value.get("error"), MAX_ERROR),
        # #1009: False when the same counts are on a span under it (not in the trace's total).
        "tokens_counted": counted if tokens is not None and isinstance(counted, bool) else None,
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
    prompt = _count(tokens.get("prompt"))
    trace = {
        "trace_id": trace_id,
        "deployment_id": _id(found.get("deployment_id"), HEX32),
        "attribution": _pick(found.get("attribution"), ATTRIBUTION),
        "latency_ms": _count(found.get("latency_ms")),
        "status": _pick(found.get("status"), ("ok", "error")),
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
        **route_facts(found, prompt),
        "spans": spans,
        "truncated": found.get("spans_truncated") is True
        or (isinstance(raw, list) and len(raw) > MAX_SPANS),
        "notes": [n[:MAX_ERROR] for n in notes[:MAX_NOTES] if isinstance(n, str) and n],
        # #1010: no root span yet; the spans, tokens and attribution are partial.
        "incomplete": found.get("incomplete") is True,
    }
    return trace, None
