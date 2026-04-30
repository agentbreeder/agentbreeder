"""Gateway logs service — pulls real spend logs from the LiteLLM proxy.

LiteLLM, when configured with a database, persists every proxied request to
its `LiteLLM_SpendLogs` table and exposes the rows via the authenticated
`/spend/logs` REST endpoint. This service wraps that call, normalizes the
response into the dashboard's `LogEntry` shape, and surfaces a clean error
when LiteLLM is unreachable.

Required env vars:

- ``LITELLM_BASE_URL`` — base URL of the LiteLLM proxy (default
  ``http://localhost:4000``).
- ``LITELLM_MASTER_KEY`` — master key used to authenticate against
  ``/spend/logs`` (default ``sk-agentbreeder-quickstart``).

If the LiteLLM proxy is not reachable, callers get an
:class:`GatewayLogsUnavailableError` and should respond with an empty list
plus a 503 (or an error string in the response envelope) — never with
synthetic data.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class GatewayLogsUnavailableError(RuntimeError):
    """Raised when the LiteLLM proxy cannot be queried for spend logs."""


def _base_url() -> str:
    return os.getenv("LITELLM_BASE_URL", "http://localhost:4000")


def _master_key() -> str:
    return os.getenv("LITELLM_MASTER_KEY", "sk-agentbreeder-quickstart")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_master_key()}"}


def _infer_tier(model_name: str, provider: str) -> str:
    """Infer the gateway tier from the LiteLLM spend-log row.

    LiteLLM-routed calls typically carry a ``custom_llm_provider`` of
    ``openai_proxy`` / ``litellm`` or a model name with a ``provider/model``
    prefix. Anything else is treated as a direct API call.
    """
    if not model_name and not provider:
        return "direct"
    p = (provider or "").lower()
    if p in {"litellm", "openai_proxy", "litellm_proxy"}:
        return "litellm"
    if "/" in (model_name or ""):
        return "litellm"
    return "direct"


def _normalize_provider(model_name: str, provider: str) -> str:
    """Normalize the provider field to the dashboard's expected slug.

    Falls back to the ``provider/`` prefix in the model name if LiteLLM
    didn't fill in a provider on the row (older LiteLLM versions).
    """
    if provider:
        return provider.lower()
    if "/" in (model_name or ""):
        return model_name.split("/", 1)[0].lower()
    return "unknown"


def _normalize_entry(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single LiteLLM spend-log row into the LogEntry shape."""
    request_id = row.get("request_id") or row.get("id") or ""
    timestamp = (
        row.get("startTime")
        or row.get("start_time")
        or row.get("created_at")
        or row.get("api_request_start_time")
        or ""
    )
    end_time = row.get("endTime") or row.get("end_time")

    model_name = row.get("model") or ""
    raw_provider = row.get("custom_llm_provider") or row.get("provider") or ""
    provider = _normalize_provider(model_name, raw_provider)
    tier = _infer_tier(model_name, raw_provider)

    # Token counts can come as ints or strings depending on LiteLLM version.
    def _int(field: str, default: int = 0) -> int:
        try:
            return int(row.get(field) or default)
        except (TypeError, ValueError):
            return default

    input_tokens = _int("prompt_tokens")
    output_tokens = _int("completion_tokens")

    # Latency: prefer explicit api_response_ms; otherwise derive from
    # start/end timestamps (ISO 8601 strings).
    latency_ms: int | None = None
    if row.get("api_response_ms") is not None:
        try:
            latency_ms = int(row["api_response_ms"])
        except (TypeError, ValueError):
            latency_ms = None
    if latency_ms is None and timestamp and end_time:
        try:
            from datetime import datetime

            t0 = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
            latency_ms = int((t1 - t0).total_seconds() * 1000)
        except Exception:
            latency_ms = None

    cost_usd = 0.0
    try:
        cost_usd = float(row.get("spend") or row.get("cost") or 0.0)
    except (TypeError, ValueError):
        cost_usd = 0.0

    # Agent attribution: LiteLLM proxies the AgentBreeder agent name in
    # metadata.user_api_key_alias / metadata.tags; fall back to user.
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        # Some LiteLLM versions stringify metadata as JSON
        import json

        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    agent = (
        metadata.get("agent_name")
        or metadata.get("user_api_key_alias")
        or row.get("user")
        or row.get("user_id")
        or ""
    )

    status_str = (row.get("status") or "success").lower()
    if status_str not in {"success", "error"}:
        status_str = "error" if "error" in status_str or "fail" in status_str else "success"

    return {
        "id": str(request_id) or f"req_{timestamp}",
        "timestamp": str(timestamp),
        "agent": str(agent),
        "model": str(model_name),
        "provider": provider,
        "gateway_tier": tier,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms if latency_ms is not None else 0,
        "cost_usd": round(cost_usd, 6),
        "status": status_str,
    }


async def fetch_spend_logs(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch up to ``limit`` recent spend-log rows from LiteLLM.

    Returns a list of normalized LogEntry dicts.

    Raises:
        GatewayLogsUnavailableError: if the LiteLLM proxy is unreachable,
            unauthenticated, or returns a non-200 response.
    """
    url = f"{_base_url()}/spend/logs"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                url,
                headers=_headers(),
                params={"limit": limit},
            )
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("LiteLLM /spend/logs unreachable: %s", exc)
        raise GatewayLogsUnavailableError(f"LiteLLM proxy unreachable: {exc}") from exc

    if resp.status_code != 200:
        logger.warning(
            "LiteLLM /spend/logs returned %s: %s",
            resp.status_code,
            resp.text[:200],
        )
        raise GatewayLogsUnavailableError(f"LiteLLM /spend/logs returned HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise GatewayLogsUnavailableError(f"LiteLLM returned invalid JSON: {exc}") from exc

    # /spend/logs returns either a bare list or {"data": [...]} depending on version.
    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("logs") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    return [_normalize_entry(r) for r in rows if isinstance(r, dict)]
