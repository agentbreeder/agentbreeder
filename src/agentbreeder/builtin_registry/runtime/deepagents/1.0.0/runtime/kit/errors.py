"""The model-call rules of `runtime/adk@1.3.0` (`agent.py`), over raw HTTP answers (spec §7.1,
§7.2.6, §7.2.7).

1.3.0 read these facts from LiteLLM and OpenAI objects: a response's `_hidden_params` headers
and a failure's `status_code`, `body` and `litellm_response_headers`. The kit's proxy has the
gateway's HTTP status, headers and JSON body instead, so the same rules are written over those.
`tests/test_runtime_kit_parity.py` feeds the same statuses, error types and headers to 1.3.0's
functions and to these, and compares the results.

Stdlib only.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

TURN_TIMEOUT = 170
MAX_ATTEMPTS = 3
RETRY_STATUSES = frozenset({409, 500, 502, 503})
TRANSIENT_4XX = frozenset({408, 409})  # a timeout or conflict: MODEL_UNAVAILABLE, "try again"
RETRY_BACKOFF = 0.5
RATE_LIMIT_WAIT = 10.0
MAX_RETRY_AFTER = 3600
RETRY_DEADLINE = TURN_TIMEOUT - 40  # seconds into a turn after which no retry starts

# The gateway's response headers (LiteLLM proxy), read case-insensitively.
COST_HEADER = "x-litellm-response-cost"
FALLBACKS_HEADER = "x-litellm-attempted-fallbacks"
GROUP_HEADER = "x-litellm-model-group"
# A gateway alias as stack.yaml names one: never a provider model id, a URL or an API base.
ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
MAX_FALLBACKS = 100

# What a connection that never got an HTTP answer counts as: LiteLLM raised APIConnectionError
# (status 500, retried) and Timeout (status 408, not retried) for these in 1.3.0.
CONNECTION_STATUS = 500
TIMEOUT_STATUS = 408


def _lower(headers: Mapping | None) -> dict:
    if not headers:
        return {}
    try:
        return {str(key).lower(): value for key, value in headers.items()}
    except Exception:  # noqa: BLE001 - a headers object of an unexpected kind
        return {}


def new_tally() -> dict:
    """One turn's model calls: gateway cost, fallbacks and the time spent waiting on limits."""
    return {
        "calls": 0,
        "unknown": 0,
        "usd": 0.0,
        "last": None,  # the latest call's cost, for its model_called event
        "fallbacks": 0,
        "served_by": [],
        "route": None,  # the latest call's (fallbacks, model group), for its model_called event
        "waited": 0.0,
        "deadline": time.monotonic() + RETRY_DEADLINE,
    }


def response_cost(headers: Mapping | None) -> float | None:
    """The gateway-reported cost of one model response in USD, or None if it reported none."""
    value = _lower(headers).get(COST_HEADER)
    if isinstance(value, bool) or value is None:
        return None
    try:
        cost = float(value)
    except (TypeError, ValueError):
        return None
    return cost if math.isfinite(cost) and cost >= 0 else None


def response_fallback(headers: Mapping | None) -> tuple[int, str | None]:
    """How many fallbacks the gateway tried for one response (0 when it said nothing usable) and
    the model group (alias) that served it, or None when absent or not an alias."""
    lowered = _lower(headers)
    raw = lowered.get(FALLBACKS_HEADER)
    count = 0
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw <= MAX_FALLBACKS:
        count = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() and len(raw) <= 3:
        count = int(raw) if int(raw) <= MAX_FALLBACKS else 0
    group = lowered.get(GROUP_HEADER)
    return count, group if isinstance(group, str) and ALIAS.fullmatch(group) else None


def record_response(tally: dict, headers: Mapping | None) -> None:
    """Add one answered call to the turn's tally (1.3.0's `CostClient`)."""
    tally["calls"] += 1
    cost = response_cost(headers)
    tally["last"] = cost
    if cost is None:
        tally["unknown"] += 1
    else:
        tally["usd"] += cost
    fallbacks, group = response_fallback(headers)
    tally["route"] = (fallbacks, group)
    tally["fallbacks"] += fallbacks
    if group is not None and group not in tally["served_by"]:
        tally["served_by"].append(group)


def retry_after(headers: Mapping | None) -> int | None:
    """A `retry-after` in whole seconds (1..MAX_RETRY_AFTER), or None when absent or invalid."""
    raw = _lower(headers).get("retry-after")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 12 or int(raw) == 0:
        return None
    return min(int(raw), MAX_RETRY_AFTER)


def upstream_kind(body: object) -> str | None:
    """The gateway's error `type` (LiteLLM: budget_exceeded, throttling_error, ...) from its
    parsed JSON error body, nested under `error` or not."""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    kind = body.get("type") if isinstance(body, dict) else None
    return kind if isinstance(kind, str) else None


def upstream_message(body: object) -> str:
    """The gateway's error text, for the turn record only (redacted there)."""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    message = body.get("message") if isinstance(body, dict) else None
    return message if isinstance(message, str) else ""


@dataclass(frozen=True)
class Failure:
    """A failed upstream call: its HTTP status (CONNECTION_STATUS or TIMEOUT_STATUS when there
    was no answer), the gateway's error type and its response headers."""

    status: int
    kind: str | None = None
    headers: Mapping | None = None


def retry_delay(failure: Failure, attempt: int, tally: dict | None) -> float | None:
    """Seconds to wait before trying a failed model call again, or None to give up. A spent
    budget is never retried; a rate limit only while the turn's waits stay within
    RATE_LIMIT_WAIT (its retry-after, else the backoff); a transient failure with backoff."""
    if attempt >= MAX_ATTEMPTS:
        return None
    if tally is not None and time.monotonic() + RETRY_BACKOFF > tally["deadline"]:
        return None
    if failure.kind == "budget_exceeded":
        return None
    backoff = RETRY_BACKOFF * 2 ** (attempt - 1)
    if failure.status == 429:
        wait = retry_after(failure.headers) or backoff
        waited = tally["waited"] if tally is not None else 0.0
        if waited + wait > RATE_LIMIT_WAIT:
            return None
        if tally is not None and time.monotonic() + wait > tally["deadline"]:
            return None
        if tally is not None:
            tally["waited"] += wait
        return wait
    return backoff if failure.status in RETRY_STATUSES else None


class ModelError(Exception):
    """A failed model call, as a fixed code for the caller. It carries no upstream text: that
    is in the turn record, redacted, under the turn id (`correlation_id` in the reply)."""

    def __init__(self, code: str, retry_after: int | None = None, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after
        self.status = status  # the gateway's status, for the log line; None when not upstream
        self.turn_id: str | None = None
        self.trace_id: str | None = None


def classify(failure: Failure) -> ModelError:
    """The coded error for a failed model call."""
    status = failure.status
    if failure.kind == "budget_exceeded":
        return ModelError("MODEL_BUDGET_EXCEEDED", status=status)
    if status == 429:
        return ModelError("MODEL_RATE_LIMITED", retry_after(failure.headers), status=status)
    if status in (401, 403):
        return ModelError("MODEL_ACCESS_DENIED", status=status)
    if 400 <= status < 500 and status not in TRANSIENT_4XX:
        return ModelError("MODEL_REQUEST_REFUSED", status=status)
    return ModelError("MODEL_UNAVAILABLE", status=status)


def turn_fallbacks(tally: dict) -> tuple[int | None, list[str]]:
    """The turn's (`fallbacks`, `served_by`): (None, []) when no gateway call was made."""
    if tally["calls"] == 0:
        return None, []
    return tally["fallbacks"], list(tally["served_by"])


def root_attributes(tally: dict) -> dict:
    """The fallback attributes of the turn's root span (none without a gateway call)."""
    fallbacks, served_by = turn_fallbacks(tally)
    if fallbacks is None:
        return {}
    attributes: dict = {"agentbreeder.fallbacks": fallbacks}
    if served_by:
        attributes["agentbreeder.served_by"] = tuple(served_by)
    return attributes


def turn_cost_usd(tally: dict) -> float | None:
    """The turn's summed cost, or None when no gateway call was made or any lacked a cost."""
    if tally["calls"] == 0 or tally["unknown"]:
        return None
    return round(tally["usd"], 10)


# A failed turn's reply: a fixed message per code, with the next step (1.3.0's `server.py`,
# plus the kit profile's MODEL_STEP_LIMIT, spec §7.2.4). Upstream text never reaches the caller.
MODEL_ERRORS = {
    "MODEL_BUDGET_EXCEEDED": (
        402,
        "This agent's model budget is spent, so the gateway refused the model call. Sending it "
        "again won't help: raise model.budget_usd in agent.yaml (within the environment's "
        "ceiling) and deploy again, or wait for the budget period to reset.",
    ),
    "MODEL_RATE_LIMITED": (
        429,
        "The gateway's rate limit for this agent was reached{after}. Wait and send the message "
        "again, or raise model.rpm in agent.yaml (within the environment's ceiling) and deploy "
        "again.",
    ),
    "MODEL_ACCESS_DENIED": (
        502,
        "The gateway refused this agent's key for its model. Check that the environment grants "
        "the model alias (environment.models) and deploy again.",
    ),
    "MODEL_REQUEST_REFUSED": (
        502,
        "The gateway refused the model request, so sending it again won't help. The agent's log "
        "has the detail under this correlation_id.",
    ),
    "MODEL_UNAVAILABLE": (502, "The model service is unavailable; please try again."),
    "MODEL_STEP_LIMIT": (
        502,
        "The agent used its model calls for this turn; ask again or raise model.max_model_calls.",
    ),
}


def model_error_response(error: ModelError) -> tuple[int, dict, dict]:
    """(status, body, headers) for a failed turn: the code, its fixed message, the turn's
    correlation id and trace id, and for a rate limit the gateway's retry-after."""
    code = error.code if error.code in MODEL_ERRORS else "MODEL_UNAVAILABLE"
    status, message = MODEL_ERRORS[code]
    wait = error.retry_after if code == "MODEL_RATE_LIMITED" else None
    body = {
        "error": message.format(after=f" (retry in {wait} s)" if wait else ""),
        "code": code,
        "correlation_id": error.turn_id,
        "trace_id": error.trace_id,
        "retry_after_s": wait,
    }
    return status, body, ({"Retry-After": str(wait)} if wait else {})
