"""LLM cost in the console (#906, console half): `agentbreeder cost --stack DIR --environment ENV
--since DAY --until DAY --json` (contract: docs/cost.md and docs/commands/cost.md), run as an
uncached CLI child for Home's cost card and an agent's Overview.

Pure functions: the server checks the query with `cost_request`, builds the argv with `cost_argv`
and passes the answer through `cost_answer` before the browser sees it.

- The gateway is asked only by the CLI child, with the master key from the operator config; the
  console server never reads a credential, and nothing here could carry one.
- Amounts are USD as LiteLLM computed them from its price list: an estimate, not a bill. The
  answer must say so (`currency` USD, `source` "litellm spend logs") or it is refused.
- The window is a choice of days (`WINDOWS`) ending today (UTC); the server turns it into the
  `--since`/`--until` days and checks that the answer covers exactly those days.
- Every count and amount is type-checked; any bad one makes the whole answer
  COST_INVALID, never a quiet zero. Text fields from the gateway lose their control and invisible
  format characters and are cut. Only known keys go on.
- `as_of` is when the server got the answer (UTC). Spend logs reach LiteLLM's database about a
  minute after a call, so the latest calls can be missing.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from typing import Any

from agentbreeder.center.files import BadRequest

WINDOWS = (7, 30, 90)  # days ending today; the CLI allows up to 366
DEFAULT_WINDOW = 30
PREFIX = "agentbreeder-"  # `gateway_keys.PREFIX` (a test keeps them equal)
KINDS = ("deployment", "eval", "unknown")
DEPLOYMENT_ID = re.compile(r"[0-9a-f]{32}")
CODE = re.compile(r"[A-Z][A-Z0-9_]{2,39}")
MAX_TEXT = 200  # `llmcost.TEXT_LIMIT`: model names and ids
MAX_ALIAS = MAX_TEXT + len(PREFIX)
MAX_FIELD = 64  # name, target, state, environment, gateway
MAX_MESSAGE = 600
MAX_COUNT = 10**12
MAX_USD = 1e18  # 50 pages of 1,000 rows of at most 1e12 USD each stay below this
MAX_ROWS = 50_000  # `llmcost`: at most 50 pages of 1,000 rows, so at most this many keys or models
SHOWN_KEYS = 200  # keys passed on (sorted by cost); the rest are counted in `keys_cut`
SHOWN_MODELS = 100  # models passed on, overall and per key; the rest in `models_cut`
# 50 pages of up to 32 MiB from the gateway, after the state is read.
TIMEOUT = 120
INVALID = {
    "code": "COST_INVALID",
    "message": "The cost answer does not match its contract; run `agentbreeder cost --stack DIR "
    "--json` in a terminal",
}


class _Invalid(ValueError):
    pass


def cost_request(query: dict[str, list[str]], today: date | None = None) -> dict:
    """The checked query: `{days, since, until}` (UTC days, both included). Raises BadRequest."""
    raw = query.get("days", [str(DEFAULT_WINDOW)])[0]
    if raw not in {str(days) for days in WINDOWS}:
        choices = ", ".join(str(days) for days in WINDOWS)
        raise BadRequest(f"days must be one of {choices}", "COST_WINDOW")
    days = int(raw)
    until = today or datetime.now(UTC).date()
    since = until - timedelta(days=days - 1)
    return {"days": days, "since": since.isoformat(), "until": until.isoformat()}


def cost_argv(project: dict, request: dict) -> list[str]:
    """`cost` with the registered project's stack and environment; only the server's own days,
    never a value from the browser."""
    argv = ["cost", "--stack", project["path"], "--environment", project["environment"]]
    return [*argv, "--since", request["since"], "--until", request["until"], "--json"]


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        ch
        for ch in value
        if ch >= " " and not "\x7f" <= ch <= "\x9f" and unicodedata.category(ch) != "Cf"
    )
    return cleaned[:limit] or None


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
        raise _Invalid
    return value


def _usd(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Invalid
    try:
        number = float(value)
    except OverflowError:
        raise _Invalid from None
    if not math.isfinite(number) or not 0 <= number <= MAX_USD:
        raise _Invalid
    return number


def _bucket(item: Any) -> dict:
    """The counts every grouping has. Raises _Invalid."""
    if not isinstance(item, dict):
        raise _Invalid
    bucket = {
        key: _count(item.get(key))
        for key in ("calls", "failed", "prompt_tokens", "completion_tokens", "unpriced")
    }
    if bucket["failed"] > bucket["calls"] or bucket["unpriced"] > bucket["calls"]:
        raise _Invalid
    bucket["cost_usd"] = _usd(item.get("cost_usd"))
    return bucket


def _model(item: Any) -> dict:
    bucket = _bucket(item)
    return {
        "model_group": _text(item.get("model_group"), MAX_TEXT),
        "model": _text(item.get("model"), MAX_TEXT),
        **bucket,
    }


def _models(value: Any) -> tuple[list[dict], int]:
    if not isinstance(value, list) or len(value) > MAX_ROWS:
        raise _Invalid
    return [_model(item) for item in value[:SHOWN_MODELS]], max(0, len(value) - SHOWN_MODELS)


def _key(item: Any) -> dict:
    bucket = _bucket(item)
    alias = item.get("key_alias")
    kind = item.get("kind")
    if not isinstance(alias, str) or not alias.startswith(PREFIX) or len(alias) > MAX_ALIAS:
        raise _Invalid
    if kind not in KINDS:
        raise _Invalid
    models, cut = _models(item.get("models"))
    key: dict[str, Any] = {"key_alias": _text(alias, MAX_ALIAS), "kind": kind}
    if kind == "deployment":
        identifier = item.get("id")
        if not isinstance(identifier, str) or not DEPLOYMENT_ID.fullmatch(identifier):
            raise _Invalid
        key["id"] = identifier
        for field in ("name", "target", "state", "environment"):
            key[field] = _text(item.get(field), MAX_FIELD)
    elif kind == "eval":
        key["run"] = _text(item.get("run"), MAX_TEXT)
    else:
        key["id"] = _text(item.get("id"), MAX_TEXT)
    return {**key, **bucket, "models": models, "models_cut": cut}


def _refusal(failure: dict) -> dict:
    code = failure.get("code")
    return {
        "code": code if isinstance(code, str) and CODE.fullmatch(code) else "COST_FAILED",
        "message": _text(failure.get("message"), MAX_MESSAGE) or "cost failed",
    }


def cost_answer(
    data: object, request: dict, now: datetime | None = None
) -> tuple[dict | None, dict | None]:
    """`(cost, None)`, or `(None, problem)` with `problem` `{code, message}`: the CLI's own coded
    refusal, or COST_INVALID when the answer does not match its contract (another window, another
    currency or source, a count or amount that is not one)."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return None, INVALID
    failure = data.get("error")
    if isinstance(failure, dict):  # the coded refusal (exit 2)
        return None, _refusal(failure)
    if (
        data.get("since") != request["since"]
        or data.get("until") != request["until"]
        or data.get("currency") != "USD"
        or data.get("source") != "litellm spend logs"
        or not isinstance(data.get("truncated"), bool)
    ):
        return None, INVALID
    keys = data.get("keys")
    try:
        if not isinstance(keys, list) or len(keys) > MAX_ROWS:
            raise _Invalid
        shown = [_key(item) for item in keys[:SHOWN_KEYS]]
        models, models_cut = _models(data.get("models"))
        total = _bucket(data.get("total"))
    except _Invalid:
        return None, INVALID
    when = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "days": request["days"],
        "since": request["since"],
        "until": request["until"],
        "as_of": when,
        "gateway": _text(data.get("gateway"), MAX_FIELD),
        "currency": "USD",
        "source": "litellm spend logs",
        "truncated": data["truncated"],
        "keys": shown,
        "keys_cut": max(0, len(keys) - SHOWN_KEYS),
        "models": models,
        "models_cut": models_cut,
        "total": total,
    }, None
