"""LLM cost per deployment and per model from LiteLLM's spend logs (#906).

Every deployment that has `model_access` calls the gateway with its own virtual key, whose alias
is `agentbreeder-<deployment id>` (`gateway_keys`); eval judges use `agentbreeder-eval-<run>`.
LiteLLM writes one spend log row per model call with that alias in its metadata, the model, the
token counts and the cost it computed from its own model price map. The rows outlive the key
(a removed deployment's calls are still counted), unlike a key's `spend`, which resets every
budget window. Rows from any other key (the master key, a key someone minted by hand) are not
counted. Amounts are in USD as LiteLLM computed them: an estimate from list prices, not a bill.

Gateway rows are data from a local service: every field is type-checked here, and text fields are
passed on as they are (callers print them through `safe_text`).
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta

from agentbreeder.gateway_keys import PREFIX

EVAL_PREFIX = PREFIX + "eval-"
DEFAULT_DAYS = 30
MAX_DAYS = 366
TEXT_LIMIT = 200  # model names, aliases: longer values are cut
MAX_CALL_USD = 1e12  # a price above this is not a price: counted as unpriced (no overflow)
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class CostError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


def window(since: str | None, until: str | None, today: date | None = None) -> tuple[date, date]:
    """The UTC days to report, both included: the last DEFAULT_DAYS days by default."""
    today = today or datetime.now(UTC).date()

    def parse(value: str, flag: str) -> date:
        try:
            return date.fromisoformat(value) if _DAY.fullmatch(value) else _bad(flag)
        except ValueError:
            return _bad(flag)

    end = parse(until, "--until") if until else today
    start = parse(since, "--since") if since else end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        raise CostError("COST_WINDOW", "--since is after --until")
    if (end - start).days + 1 > MAX_DAYS:
        raise CostError("COST_WINDOW", f"the window is longer than {MAX_DAYS} days")
    return start, end


def _bad(flag: str) -> date:
    raise CostError("COST_WINDOW", f"{flag} must be a date like 2026-11-13 (UTC)")


def _text(value) -> str | None:
    return value[:TEXT_LIMIT] if isinstance(value, str) and value else None


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _usd(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)  # an int too large for a float raises OverflowError
    except OverflowError:
        return None
    return number if math.isfinite(number) and 0 <= number <= MAX_CALL_USD else None


def _bucket(extra: dict) -> dict:
    return {
        **extra,
        "calls": 0,
        "failed": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
        "unpriced": 0,
    }


def _add(bucket: dict, call: dict) -> None:
    bucket["calls"] += 1
    bucket["failed"] += 0 if call["ok"] else 1
    bucket["prompt_tokens"] += call["prompt_tokens"]
    bucket["completion_tokens"] += call["completion_tokens"]
    if call["cost_usd"] is None:
        bucket["unpriced"] += 1
    else:
        bucket["cost_usd"] += call["cost_usd"]


def _call(row: dict) -> dict | None:
    metadata = row.get("metadata")
    alias = metadata.get("user_api_key_alias") if isinstance(metadata, dict) else None
    if not isinstance(alias, str) or not alias.startswith(PREFIX):
        return None
    return {
        "alias": alias[: TEXT_LIMIT + len(PREFIX)],
        "request_id": _text(row.get("request_id")),
        "model": _text(row.get("model")),
        "model_group": _text(row.get("model_group")),
        "ok": row.get("status") != "failure",
        "prompt_tokens": _count(row.get("prompt_tokens")),
        "completion_tokens": _count(row.get("completion_tokens")),
        "cost_usd": _usd(row.get("spend")),
    }


def _round(bucket: dict) -> dict:
    bucket["cost_usd"] = round(bucket["cost_usd"], 8)
    return bucket


def _key(call: dict) -> tuple:
    return (call["model_group"] or "", call["model"] or "")


def summarize(rows: list[dict], deployments: list[dict]) -> dict:
    """Group spend log rows by deployment key and by model. `deployments` are this state's
    records (id, name, target, state, environment); a key alias that names none of them is
    reported as `unknown` (another state directory, or history that was removed)."""
    known = {record["id"]: record for record in deployments}
    seen: set[str] = set()
    by_alias: dict[str, dict] = {}
    by_alias_model: dict[str, dict[tuple, dict]] = {}
    by_model: dict[tuple, dict] = {}
    total = _bucket({})
    for row in rows:
        call = _call(row) if isinstance(row, dict) else None
        if call is None:
            continue
        if call["request_id"]:  # pages can overlap when calls land while they are read
            if call["request_id"] in seen:
                continue
            seen.add(call["request_id"])
        alias = call["alias"]
        if alias not in by_alias:
            identifier = alias[len(PREFIX) :]
            record = known.get(identifier)
            if alias.startswith(EVAL_PREFIX):
                extra = {"kind": "eval", "run": identifier[len("eval-") :]}
            elif record is not None:
                extra = {
                    "kind": "deployment",
                    "id": identifier,
                    "name": record.get("name"),
                    "target": record.get("target") or "local",
                    "state": record.get("state"),
                    "environment": record.get("environment"),
                }
            else:
                extra = {"kind": "unknown", "id": identifier}
            by_alias[alias] = _bucket({"key_alias": alias, **extra})
            by_alias_model[alias] = {}
        models = by_alias_model[alias]
        key = _key(call)
        if key not in models:
            models[key] = _bucket({"model_group": key[0] or None, "model": key[1] or None})
        if key not in by_model:
            by_model[key] = _bucket({"model_group": key[0] or None, "model": key[1] or None})
        for bucket in (by_alias[alias], models[key], by_model[key], total):
            _add(bucket, call)

    def order(bucket: dict) -> tuple:
        return (-bucket["cost_usd"], -bucket["calls"], bucket.get("key_alias") or "")

    keys = []
    for alias, bucket in by_alias.items():
        bucket["models"] = [_round(m) for m in sorted(by_alias_model[alias].values(), key=order)]
        keys.append(_round(bucket))
    return {
        "keys": sorted(keys, key=order),
        "models": [_round(m) for m in sorted(by_model.values(), key=order)],
        "total": _round(total),
    }


def report(client, deployments: list[dict], start: date, end: date, gateway: str) -> dict:
    """Read the window's spend logs from the gateway and summarize them."""
    rows, truncated = client.spend_logs(f"{start} 00:00:00", f"{end} 23:59:59")  # LiteLLM: lte
    return {
        "schema_version": 1,
        "since": start.isoformat(),
        "until": end.isoformat(),
        "gateway": gateway,
        "currency": "USD",
        "source": "litellm spend logs",
        "truncated": truncated,
        **summarize(rows, deployments),
    }
