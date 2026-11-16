"""Per-deployment LiteLLM virtual keys (spec §10, §18.2).

Every deployment that requests `model_access` gets its own key, minted after the gate and
approval, limited to the granted aliases, budget and rate, and revoked on remove/disable. The
key alias is derived from the deployment ID (`agentbreeder-<id>`), so a retry or a crash between
mint and record needs no extra state: it deletes by alias and mints again (LiteLLM never returns
an existing key's value). `reconcile` lists keys by the alias prefix and revokes orphans.
The key value goes only into the deployment's private secret file; the audit records the alias.
"""

from __future__ import annotations

import http.client
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from agentbreeder import gateway_providers
from agentbreeder.gateway_providers import EntryRefused, GatewayCaps
from agentbreeder.operator_config import Gateway, OperatorConfig, resolve_credential

PREFIX = "agentbreeder-"
TIMEOUT = 15
SPEND_PAGE_LIMIT = 32 * 1024 * 1024  # bytes per /spend/logs/v2 page (1000 rows with metadata)


class _NoRedirect(HTTPRedirectHandler):
    """The master key goes only to the configured URL: a redirect is an error, never followed
    (urllib would resend the Authorization header to the new location)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Never route the master key through a proxy, and never follow a redirect with it.
_OPENER = build_opener(ProxyHandler({}), _NoRedirect())


class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


def alias(deployment_id: str) -> str:
    return PREFIX + deployment_id


# -- what a minted key may carry (#1024 G1b) ----------------------------------------------------
# LiteLLM reads guardrail switches from key metadata (`disable_global_guardrails` and
# `opted_out_global_guardrails` turned the global guardrails off in G0, spec §12.3), so the
# request body and its metadata are allow-lists: a field LiteLLM adds in an upgrade is refused,
# not passed through. Keys never get a `team_id` (team metadata carries the same switches).
MINT_FIELDS = frozenset(
    {
        "key_alias",
        "models",
        "max_budget",
        "budget_duration",
        "rpm_limit",
        "duration",
        "allowed_routes",
        "metadata",
    }
)
MINT_METADATA = frozenset({"agentbreeder", "deployment_id", "agent", "environment", "eval_run"})
# The routes a key in a guardrails-required environment may call: chat completions and
# embeddings, which the Presidio hook covers (spec §12.4). `/openai/*` passthrough runs the
# guardrail but forwards the unmasked body, and `/v1/moderations` is not checked at all.
GUARDED_ROUTES = ("/v1/chat/completions", "/chat/completions", "/v1/embeddings", "/embeddings")


def key_routes(environment, agent_guardrails=None) -> list[str] | None:
    """`GUARDED_ROUTES` for a key in an environment with a required guardrails floor, or for
    a deployment with agent-level guardrails (#1053: they run on these routes only)."""
    if agent_guardrails or (environment is not None and environment.guardrails_required):
        return list(GUARDED_ROUTES)
    return None


# -- agent-level guardrails on LiteLLM's policy engine (#1053, spec §12.8) -----------------------
# One policy per deployment, named like its key alias, that only ADDS guardrails, attached to
# that alias only. The bodies are allow-lists: no `guardrails_remove`, `inherit`, `condition`,
# `pipeline`, `scope`, `teams`, `models` or `tags`, so the policy can't weaken the floor or
# reach another key. Only the master key can call these routes; agent keys are limited to
# GUARDED_ROUTES.
POLICY_FIELDS = frozenset({"policy_name", "description", "guardrails_add"})
ATTACHMENT_FIELDS = frozenset({"policy_name", "keys"})
POLICY_DESCRIPTION = "AgentBreeder agent guardrails (#1053)"


def policy_body(key_alias: str, guardrails: list[str]) -> dict:
    if not key_alias.startswith(PREFIX) or "*" in key_alias:
        raise GatewayError("GATEWAY_POLICY", "a guardrail policy is for one deployment key")
    if not guardrails or any(not _MODEL_NAME.fullmatch(g) for g in guardrails):
        raise GatewayError("GATEWAY_POLICY", "a guardrail policy names gateway guardrails")
    body = {
        "policy_name": key_alias,
        "description": POLICY_DESCRIPTION,
        "guardrails_add": sorted(set(guardrails)),
    }
    attachment = {"policy_name": key_alias, "keys": [key_alias]}
    if set(body) - POLICY_FIELDS or set(attachment) - ATTACHMENT_FIELDS:  # pragma: no cover
        raise GatewayError("GATEWAY_POLICY", "a policy field outside the allow-list")
    return {"policy": body, "attachment": attachment}


def _matches(value: str, patterns) -> bool:
    """LiteLLM's attachment match (`PolicyMatcher.matches_pattern`): exact, `*`, or a trailing
    `*` as a prefix."""
    for pattern in patterns or ():
        if not isinstance(pattern, str):
            continue
        if pattern == "*" or pattern == value:
            return True
        if pattern.endswith("*") and value.startswith(pattern[:-1]):
            return True
    return False


def guardrail_drift(
    key_alias: str, expected: list[str], policies: list[dict], attachments: list[dict]
) -> str | None:
    """Why the gateway's policies for `key_alias` are not exactly `expected` added to the
    floor, or None. Reported, never repaired (`reconcile`: DRIFT_GUARDRAILS)."""
    by_name: dict[str, list[dict]] = {}
    for policy in policies:
        if isinstance(policy, dict) and isinstance(policy.get("policy_name"), str):
            by_name.setdefault(policy["policy_name"], []).append(policy)
    ours = [a for a in attachments if isinstance(a, dict) and a.get("policy_name") == key_alias]
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        # Blank keys mean every key to LiteLLM (`PolicyScope.get_keys` defaults to `*`); teams,
        # models and tags may match any request, so they count as reaching too.
        reaches = (
            attachment.get("scope") == "*"
            or not attachment.get("keys")
            or _matches(key_alias, attachment.get("keys"))
            or any(attachment.get(f) for f in ("teams", "models", "tags"))
        )
        if not reaches:
            continue
        for policy in by_name.get(str(attachment.get("policy_name")), []):
            if policy.get("guardrails_remove") or policy.get("inherit") or policy.get("pipeline"):
                # The policy name is gateway text: never printed.
                return "a policy that can remove guardrails reaches the deployment's key"
    if not expected:
        return "an agent guardrail policy is attached" if ours else None
    if (
        len(ours) != 1
        or ours[0].get("keys") != [key_alias]
        # A `default` attachment is dropped whenever another attachment matches the key.
        or any(ours[0].get(f) for f in ("teams", "models", "tags", "scope", "default"))
    ):
        return "the deployment's guardrail policy is not attached to its key only"
    versions = [
        p
        for p in by_name.get(key_alias, [])
        if p.get("version_status", "production") == "production"
    ]
    if len(versions) != 1:
        return "the deployment's guardrail policy is missing"
    policy = versions[0]
    if policy.get("condition") or sorted(policy.get("guardrails_add") or []) != sorted(expected):
        return "the deployment's guardrail policy does not add the agent's guardrails"
    return None


def mint_body(
    key_alias: str,
    models: list[str],
    budget: float,
    rpm: int,
    metadata: dict,
    duration: str | None = None,
    routes: list[str] | None = None,
) -> dict:
    """The `/key/generate` body, built from the allow-lists above only."""
    if not isinstance(metadata, dict) or set(metadata) - MINT_METADATA:
        raise GatewayError("GATEWAY_KEY_METADATA", "key metadata holds AgentBreeder's keys only")
    if not all(v is None or isinstance(v, (bool, int, float, str)) for v in metadata.values()):
        raise GatewayError("GATEWAY_KEY_METADATA", "key metadata values are plain values")
    body = {
        "key_alias": key_alias,
        "models": list(models),
        "max_budget": budget,
        "budget_duration": "30d",
        "rpm_limit": rpm,
        "metadata": dict(metadata),
    }
    if duration:
        body["duration"] = duration
    if routes is not None:
        if not routes or not set(routes) <= set(GUARDED_ROUTES):
            raise GatewayError("GATEWAY_KEY_ROUTES", "a key's routes come from GUARDED_ROUTES")
        body["allowed_routes"] = list(routes)
    if set(body) - MINT_FIELDS:  # pragma: no cover - a coding error above, never input
        raise GatewayError("GATEWAY_KEY_FIELD", "a key field outside MINT_FIELDS")
    return body


@dataclass
class LiteLLM:
    url: str
    master_key: str

    def _call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        query: dict | None = None,
        limit: int | None = None,
    ):
        target = self.url.rstrip("/") + path + (("?" + urlencode(query)) if query else "")
        request = Request(
            target,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.master_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with _OPENER.open(request, timeout=TIMEOUT) as response:
                data = response.read(limit + 1) if limit else response.read()
        except HTTPError as error:
            if error.code == 404:
                return None
            raise GatewayError(
                "GATEWAY_ERROR", f"LiteLLM {path} returned HTTP {error.code}"
            ) from None
        except (URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as error:
            raise GatewayError(
                "GATEWAY_UNREACHABLE", f"LiteLLM at {self.url} is unreachable"
            ) from error
        if limit and len(data) > limit:
            raise GatewayError("GATEWAY_ERROR", f"LiteLLM {path} answered more than {limit} bytes")
        try:
            return json.loads(data)
        except (ValueError, RecursionError):
            raise GatewayError("GATEWAY_ERROR", f"LiteLLM {path} did not return JSON") from None

    def mint(
        self,
        key_alias: str,
        models: list[str],
        budget: float,
        rpm: int,
        metadata: dict,
        duration: str | None = None,
        routes: list[str] | None = None,
    ) -> str:
        """Mint a virtual key. `budget` renews every 30 days (LiteLLM `budget_duration`).
        `duration` makes the key itself expire (e.g. "6h"): set it for short-lived keys whose
        revoke could be skipped by a killed process. `routes` limits the key to those proxy
        routes (`allowed_routes`; `GUARDED_ROUTES` in a guardrails-required environment)."""
        body = mint_body(key_alias, models, budget, rpm, metadata, duration, routes)
        self.revoke(key_alias)  # idempotent retry: an earlier attempt may have minted it
        result = self._call("POST", "/key/generate", body)
        key = (result or {}).get("key")
        if not isinstance(key, str) or not key:
            raise GatewayError("GATEWAY_ERROR", "LiteLLM did not return a key")
        return key

    def revoke(self, key_alias: str) -> None:
        """Delete by alias. A missing alias is already revoked (404)."""
        self._call("POST", "/key/delete", {"key_aliases": [key_alias]})

    def attach_guardrails(self, key_alias: str, guardrails: list[str]) -> None:
        """Attach a policy that adds `guardrails` to the key `key_alias` (#1053). Mint the key
        first: LiteLLM refuses an attachment naming a key alias that doesn't exist. Retries
        replace an earlier attempt's policy."""
        bodies = policy_body(key_alias, guardrails)
        self.detach_guardrails(key_alias)
        created = self._call("POST", "/policies", bodies["policy"])
        if not isinstance(created, dict) or created.get("policy_name") != key_alias:
            raise GatewayError("GATEWAY_POLICY", "LiteLLM did not create the guardrail policy")
        attached = self._call("POST", "/policies/attachments", bodies["attachment"])
        if not isinstance(attached, dict) or attached.get("keys") != [key_alias]:
            raise GatewayError("GATEWAY_POLICY", "LiteLLM did not attach the guardrail policy")
        problem = self.resolve_problem(key_alias, guardrails)
        if problem:
            raise GatewayError("GATEWAY_POLICY", problem)

    def resolve_problem(self, key_alias: str, guardrails: list[str]) -> str | None:
        """Why the gateway would not run `guardrails` for the key, or None: each must be a
        guardrail the running gateway defines (LiteLLM's policy API accepts any name, and an
        unknown one never runs), and the gateway's own resolution for the key alias must
        include them (the serving worker's live view; service/litellm runs one worker)."""
        listed = self._call("GET", "/guardrails/list", limit=SPEND_PAGE_LIMIT)
        rows = (listed or {}).get("guardrails") if isinstance(listed, dict) else None
        if not isinstance(rows, list):
            raise GatewayError("GATEWAY_ERROR", "LiteLLM returned no guardrail list")
        defined = {r.get("guardrail_name") for r in rows if isinstance(r, dict)}
        if set(guardrails) - defined:
            return "the running gateway does not define every agent guardrail"
        resolved = self._call("POST", "/policies/resolve", {"key_alias": key_alias})
        effective = (resolved or {}).get("effective_guardrails")
        if not isinstance(effective, list) or set(guardrails) - set(effective):
            return "the gateway does not resolve the agent guardrails for the key"
        return None

    def detach_guardrails(self, key_alias: str) -> None:
        """Delete the deployment's policy and its attachments; missing ones are fine."""
        if not key_alias.startswith(PREFIX) or "*" in key_alias:
            raise GatewayError("GATEWAY_POLICY", "a guardrail policy is for one deployment key")
        for attachment in self.policy_attachments():
            if attachment.get("policy_name") == key_alias and attachment.get("attachment_id"):
                identifier = quote(str(attachment["attachment_id"]), safe="")
                self._call("DELETE", f"/policies/attachments/{identifier}")
        self._call("DELETE", f"/policies/name/{quote(key_alias, safe='')}/all-versions")

    def policies(self) -> list[dict]:
        result = self._call("GET", "/policies/list", limit=SPEND_PAGE_LIMIT)
        if result is None:  # 404: a gateway without the policy API holds no policies
            return []
        found = result.get("policies") if isinstance(result, dict) else None
        if not isinstance(found, list):
            raise GatewayError("GATEWAY_ERROR", "LiteLLM returned no policy list")
        return [p for p in found if isinstance(p, dict)]

    def policy_attachments(self) -> list[dict]:
        result = self._call("GET", "/policies/attachments/list", limit=SPEND_PAGE_LIMIT)
        if result is None:
            return []
        found = result.get("attachments") if isinstance(result, dict) else None
        if not isinstance(found, list):
            raise GatewayError("GATEWAY_ERROR", "LiteLLM returned no policy attachment list")
        return [a for a in found if isinstance(a, dict)]

    def guardrail_drift(self, key_alias: str, expected: list[str]) -> str | None:
        drift = guardrail_drift(key_alias, expected, self.policies(), self.policy_attachments())
        return drift or (self.resolve_problem(key_alias, expected) if expected else None)

    def describe(self, key_alias: str) -> dict | None:
        result = self._call(
            "GET", "/key/list", query={"key_alias": key_alias, "return_full_object": "true"}
        )
        keys = (result or {}).get("keys") or []
        if not keys:
            return None
        key = keys[0]
        return {
            "alias": key_alias,
            "spend_usd": key.get("spend"),
            "max_budget_usd": key.get("max_budget"),
            "rpm_limit": key.get("rpm_limit"),
            "models": key.get("models"),
            "blocked": bool(key.get("blocked")),
        }

    def spend_logs(self, start: str, end: str, pages: int = 50) -> tuple[list[dict], bool]:
        """Spend log rows between `start` and `end` ("YYYY-MM-DD HH:MM:SS", UTC), newest first,
        from LiteLLM's `/spend/logs/v2` (#906): one row per model call, kept after a key is
        deleted. Returns (rows, truncated): truncated when more than `pages` pages exist or
        LiteLLM capped its count."""
        found: list[dict] = []
        page, truncated = 1, False
        while True:
            result = self._call(
                "GET",
                "/spend/logs/v2",
                query={"start_date": start, "end_date": end, "page": page, "page_size": 1000},
                limit=SPEND_PAGE_LIMIT,
            )
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise GatewayError("GATEWAY_ERROR", "LiteLLM returned no spend logs")
            found += [_slim(row) for row in result["data"] if isinstance(row, dict)]
            truncated = truncated or bool(result.get("total_is_capped"))
            try:
                total_pages = int(result.get("total_pages") or 1)
            except (TypeError, ValueError, OverflowError):
                total_pages = 1
            if page >= total_pages:
                return found, truncated
            if page >= pages:
                return found, True
            page += 1

    def aliases(self) -> list[str]:
        found, page = [], 1
        while page <= 50:
            result = self._call(
                "GET",
                "/key/list",
                query={"return_full_object": "true", "size": 100, "page": page},
            )
            keys = (result or {}).get("keys") or []
            found += [
                k["key_alias"] for k in keys if str(k.get("key_alias", "")).startswith(PREFIX)
            ]
            if page >= int((result or {}).get("total_pages") or 1):
                break
            page += 1
        return found


_SPEND_FIELDS = (
    "request_id",
    "spend",
    "model",
    "model_group",
    "status",
    "prompt_tokens",
    "completion_tokens",
)


_SLIM_TEXT = 512  # a caller can choose a model name: long strings are cut as they are read


def _slim_value(value):
    if isinstance(value, str):
        return value[:_SLIM_TEXT]
    return value if isinstance(value, (int, float)) else None  # bool is an int: llmcost drops it


def _slim(row: dict) -> dict:
    """Keep only what cost reads, cut short: a row's metadata can hold the whole proxied
    request, so 50 pages of whole rows could hold gigabytes."""
    metadata = row.get("metadata")
    alias = metadata.get("user_api_key_alias") if isinstance(metadata, dict) else None
    slim = {"user_api_key_alias": _slim_value(alias)}
    # The two places LiteLLM records cached prompt tokens (#1056), as ints only.
    extra = metadata.get("additional_usage_values") if isinstance(metadata, dict) else None
    read = extra.get("cache_read_input_tokens") if isinstance(extra, dict) else None
    if isinstance(read, int) and not isinstance(read, bool):
        slim["additional_usage_values"] = {"cache_read_input_tokens": read}
    usage = metadata.get("usage_object") if isinstance(metadata, dict) else None
    details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if isinstance(cached, int) and not isinstance(cached, bool):
        slim["usage_object"] = {"prompt_tokens_details": {"cached_tokens": cached}}
    return {
        **{key: _slim_value(row[key]) for key in _SPEND_FIELDS if key in row},
        "metadata": slim,
    }


def client(operator: OperatorConfig, name: str) -> LiteLLM | None:
    """The gateway client, or None when the gateway has no master key (no minting)."""
    gateway: Gateway | None = operator.model.gateways.get(name)
    if gateway is None or gateway.master_key is None:
        return None
    base = Path(operator.path).parent if operator.path else None
    key = resolve_credential(gateway.master_key, base)
    if not key:
        raise GatewayError("GATEWAY_CREDENTIAL", "The gateway master key is empty")
    return LiteLLM(gateway.url, key)


# A stack's gateway config comes from the repo, so it is reviewed before any key reaches it
# (#739 security review): keys may only flow to their own provider's default endpoint. The
# per-provider rules (#1025) live in gateway_providers; these are the four key-only providers.
PROVIDER_KEYS = dict(gateway_providers.KEY_ONLY_KEYS)
_ENV = "os.environ/"
_SECTIONS = {
    "model_list": None,
    "router_settings": {
        "num_retries",
        "retry_after",
        "allowed_fails",
        "cooldown_time",
        "routing_strategy",
        "timeout",
        "enable_pre_call_checks",
    },
    "litellm_settings": {
        "fallbacks",
        "context_window_fallbacks",
        "drop_params",
        "request_timeout",
        "turn_off_message_logging",
        "num_retries",
        "set_verbose",
        "json_logs",
    },
    # Reviewed by `_guardrails` (#1024 G1b): Presidio entries from an allow-list only.
    "guardrails": None,
    "general_settings": {
        "master_key",
        "database_url",
        "store_prompts_in_spend_logs",
        "disable_spend_logs",
        "disable_error_logs",
        "max_parallel_requests",
    },
}
_ENTRY = {"model_name", "litellm_params", "model_info"}
_MODEL_NAME = re.compile(r"[A-Za-z0-9._:/-]{1,128}")
_FALLBACKS = ("fallbacks", "context_window_fallbacks")
_GENERAL_ENV = {"master_key": _ENV + "LITELLM_MASTER_KEY", "database_url": _ENV + "DATABASE_URL"}
# Prompt and reply logging stays off (#1028): when present, each key must hold exactly this
# boolean. There is no operator opt-in; a stack that needs message logs is a new design.
_LOGGING = {
    ("litellm_settings", "turn_off_message_logging"): True,
    ("litellm_settings", "set_verbose"): False,  # debug logs print request bodies
    ("general_settings", "store_prompts_in_spend_logs"): False,
}

# -- guardrails (#1024 G1b, spec §4.2 as amended by the G0 results §12.2) -----------------------
_GUARDRAIL_ENTRY = {"guardrail_name", "litellm_params"}
_GUARDRAIL_PARAMS = {
    "guardrail",
    "mode",
    "default_on",
    "pii_entities_config",
    "presidio_language",
    "presidio_score_thresholds",
    "timeout",
}
# `during_call` runs beside the provider call and doesn't stop input reaching it (D3).
# `logging_only` is accepted but never counts toward a requirement.
_GUARDRAIL_MODES = ("pre_call", "post_call", "logging_only")
_PII_ACTIONS = ("MASK", "BLOCK")
_ENTITY = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
GUARDRAIL_THRESHOLD_CAP = 0.8  # a threshold of 1.0 turns detection off
GUARDRAIL_TIMEOUT = (1, 60)  # seconds; unset, a stalled analyzer fails after 300 s (§12.5)
# The logging settings a required floor needs (§4.6), with LiteLLM's default when absent.
_GUARDRAIL_LOGGING = {
    ("litellm_settings", "turn_off_message_logging"): (True, False),
    ("litellm_settings", "set_verbose"): (False, False),
    ("general_settings", "store_prompts_in_spend_logs"): (False, False),
    ("general_settings", "disable_error_logs"): (True, False),  # the table stores payloads
}


@dataclass
class ConfigReview:
    aliases: set[str]
    providers: dict[str, str]  # alias -> "gemini (GEMINI_API_KEY)", shown in the services grant
    sha256: str
    # alias -> the aliases LiteLLM may serve it from instead, `fallbacks` and
    # `context_window_fallbacks` merged in that order (#1033: a fallback runs under the
    # caller's key, so `services up` refuses a target the environment doesn't grant).
    fallbacks: dict[str, list[str]] = field(default_factory=dict)
    # The `guardrails:` entries as reviewed, in config order (#1024 G1b).
    guardrails: list[dict] = field(default_factory=list)
    # sha256 of the canonical JSON of the guardrails list plus the §4.6 logging settings: what
    # an operator approves (`approved_sha256`). A `model_list` edit doesn't change it.
    guardrails_sha256: str = ""
    # The §4.6 logging settings as LiteLLM will apply them (defaults filled in).
    logging: dict[str, bool] = field(default_factory=dict)

    # alias -> the variables it needs at `up` (#1025 §5.3): read these, never parse labels.
    required_env: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # alias -> {"provider": ..., plus the printed route fields: project, location, region, ...}
    routes: dict[str, dict] = field(default_factory=dict)
    key_only: set[str] = field(default_factory=set)  # aliases of the four key-only providers

    def grant_routes(self) -> dict[str, dict]:
        """The routes the services grant shows (none for key-only aliases, so their stacks
        keep their grant digest)."""
        return gateway_providers.grant_routes(self.routes, self.key_only)

    def effective(self) -> list[str]:
        """The checks that count toward a floor: `presidio:<mode>` for each `default_on`
        pre_call or post_call guardrail with a non-empty entity map (never `logging_only`)."""
        found = []
        for entry in self.guardrails:
            params = entry["litellm_params"]
            if (
                params.get("default_on") is True
                and params.get("mode") in ("pre_call", "post_call")
                and params.get("pii_entities_config")
            ):
                found.append(f"presidio:{params['mode']}")
        return sorted(set(found))

    def optional(self) -> list[str]:
        """The guardrails an agent may add (#1053): `default_on: false`, pre_call or
        post_call, with a non-empty entity map."""
        return sorted(
            entry["guardrail_name"]
            for entry in self.guardrails
            for params in (entry["litellm_params"],)
            if params.get("default_on") is False
            and params.get("mode") in ("pre_call", "post_call")
            and params.get("pii_entities_config")
        )

    def logging_problem(self) -> str | None:
        """The first §4.6 logging setting that is not as guardrails need it, or None."""
        for (section, key), (expected, _) in _GUARDRAIL_LOGGING.items():
            if self.logging.get(f"{section}.{key}") is not expected:
                return f"set {section}.{key}: {str(expected).lower()}"
        return None

    def guardrails_effective(self, required: bool) -> list[str]:
        """The effective set; with `required`, refuse (GUARDRAILS_INEFFECTIVE) a config with
        no effective pre_call check or without the §4.6 logging settings."""
        effective = self.effective()
        if not required:
            return effective
        if "presidio:pre_call" not in effective:
            raise GatewayError(
                "GUARDRAILS_INEFFECTIVE",
                "guardrails are required: the gateway config needs a default_on pre_call "
                "Presidio guardrail with at least one entity",
            )
        problem = self.logging_problem()
        if problem:
            raise GatewayError("GUARDRAILS_INEFFECTIVE", f"guardrails are required: {problem}")
        return effective


def _unsafe(path: Path, where: str) -> GatewayError:
    # Names the location only: config values are never echoed.
    return GatewayError("GATEWAY_CONFIG_UNSAFE", f"{path.name}: {where} is not allowed")


def _env_strings(value, where: str):
    if isinstance(value, str):
        if value.startswith(_ENV):
            yield where, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _env_strings(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _env_strings(item, f"{where}[{index}]")


def _scalar(value) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _fallbacks(path: Path, where: str, value, aliases: set[str], edges: dict) -> None:
    """`[{alias: [alias, ...]}, ...]` naming declared models only (no inline deployments).
    Adds each edge to `edges`."""
    if not isinstance(value, list):
        raise _unsafe(path, where)
    for item in value:
        if not isinstance(item, dict) or len(item) != 1:
            raise _unsafe(path, where)
        ((source, targets),) = item.items()
        if (
            source not in aliases
            or not isinstance(targets, list)
            or any(not isinstance(t, str) or t not in aliases for t in targets)
        ):
            raise _unsafe(path, f"{where} (fallbacks name declared model_names only)")
        known = edges.setdefault(source, [])
        known.extend(t for t in dict.fromkeys(targets) if t not in known)


def _guardrails(path: Path, value) -> list[dict]:
    """Review the `guardrails:` list: Presidio only, allow-listed fields and values only, no
    `os.environ/` value (the caller's env scan refuses those), and no entity that one guardrail
    masks and a later one blocks (a mask rewrites the text first, so the block never fires).
    `default_on: false` makes a guardrail optional (#1053): it runs only for the deployments
    whose `agent.yaml` names it, after every `default_on` one (spec §12.8), so an optional
    guardrail may not block an entity that any `default_on` or other optional one masks."""
    if not isinstance(value, list):
        raise GatewayError("GATEWAY_CONFIG", f"{path.name}: guardrails must be a list")
    names: set[str] = set()
    masked: set[str] = set()
    optional: list[tuple[str, dict]] = []
    for index, entry in enumerate(value):
        where = f"guardrails[{index}]"
        if not isinstance(entry, dict) or set(entry) - _GUARDRAIL_ENTRY:
            raise _unsafe(path, f"{where} key")
        name = entry.get("guardrail_name")
        if not isinstance(name, str) or not _MODEL_NAME.fullmatch(name) or name in names:
            raise _unsafe(path, f"{where}.guardrail_name (unique; letters, digits and ._:/-)")
        names.add(name)
        params = entry.get("litellm_params")
        if not isinstance(params, dict):
            raise GatewayError("GATEWAY_CONFIG", f"{path.name}: {where}.litellm_params")
        for key in params:
            if key not in _GUARDRAIL_PARAMS:
                shown = key if isinstance(key, str) and key.isidentifier() else "key"
                raise _unsafe(path, f"{where}.litellm_params.{shown}")
        where += ".litellm_params"
        if params.get("guardrail") != "presidio":
            raise _unsafe(path, f"{where}.guardrail (presidio only)")
        if params.get("mode") not in _GUARDRAIL_MODES:
            raise _unsafe(path, f"{where}.mode (one of: {', '.join(_GUARDRAIL_MODES)})")
        if type(params.get("default_on")) is not bool:
            raise _unsafe(path, f"{where}.default_on (true, or false for an agent guardrail)")
        if params.get("presidio_language", "en") != "en":
            raise _unsafe(path, f"{where}.presidio_language (en only)")
        entities = params.get("pii_entities_config")
        if (
            not isinstance(entities, dict)
            or not entities
            or any(
                not isinstance(k, str) or not _ENTITY.fullmatch(k) or v not in _PII_ACTIONS
                for k, v in entities.items()
            )
        ):
            raise _unsafe(path, f"{where}.pii_entities_config (ENTITY: MASK or BLOCK)")
        thresholds = params.get("presidio_score_thresholds", {})
        if not isinstance(thresholds, dict) or any(
            not isinstance(k, str)
            or not _ENTITY.fullmatch(k)
            or type(v) not in (int, float)
            or not 0 <= v <= GUARDRAIL_THRESHOLD_CAP
            for k, v in thresholds.items()
        ):
            raise _unsafe(
                path,
                f"{where}.presidio_score_thresholds (ENTITY: 0 to {GUARDRAIL_THRESHOLD_CAP})",
            )
        if "timeout" in params:
            low, high = GUARDRAIL_TIMEOUT
            timeout = params["timeout"]
            if type(timeout) not in (int, float) or not low <= timeout <= high:
                raise _unsafe(path, f"{where}.timeout (seconds, {low} to {high})")
        if params["mode"] == "logging_only":
            continue
        if not params["default_on"]:
            optional.append((where, entities))
            continue
        blocked = {k for k, v in entities.items() if v == "BLOCK"}
        if blocked & masked:
            raise _unsafe(path, f"{where}: blocks an entity an earlier guardrail masks")
        masked |= {k for k, v in entities.items() if v == "MASK"}
    # Optional guardrails run after the default_on ones, in the order a deployment's policy
    # lists them: refuse a block that any default_on or other optional guardrail could mask.
    for where, entities in optional:
        blocked = {k for k, v in entities.items() if v == "BLOCK"}
        others = set().union(
            *({k for k, v in e.items() if v == "MASK"} for w, e in optional if w != where)
        )
        if blocked & (masked | others):
            raise _unsafe(path, f"{where}: blocks an entity another guardrail masks")
    return [dict(entry) for entry in value]


def config_review(path: Path, caps: GatewayCaps) -> ConfigReview:
    """Parse a LiteLLM proxy config and refuse anything that could send a credential somewhere
    other than its provider: only allow-listed keys per section, no custom endpoints or
    callbacks, and `os.environ/` only for the master key, the database URL and each model's own
    provider credentials (gateway_providers). `caps` is the gateway that will run the config
    (required, so an omitted gateway fails closed): a provider whose imports it lacks is
    refused (`GATEWAY_PROVIDER_UNSUPPORTED`)."""
    if not isinstance(caps, GatewayCaps):
        raise TypeError("config_review needs the gateway's GatewayCaps")
    import hashlib

    from agentbreeder.strict_yaml import StrictYamlError, load_bytes, read_file

    try:
        # One read: the hash below is of the bytes that were parsed (spec §4.2, T9).
        raw = read_file(path, 256 * 1024)
        data = load_bytes(raw, path.name)
    except StrictYamlError as error:
        raise GatewayError("GATEWAY_CONFIG", f"{path.name}: {error}") from None
    if not isinstance(data, dict):
        raise GatewayError("GATEWAY_CONFIG", f"{path.name}: must be a mapping")
    models = data.get("model_list")
    if not isinstance(models, list):
        raise GatewayError("GATEWAY_CONFIG", f"{path.name}: model_list must be a list")
    allowed_env: set[str] = set()
    for section, value in data.items():
        if section not in _SECTIONS:
            raise _unsafe(
                path, f"top-level key {section!r}" if section.isidentifier() else "a key"
            )
        keys = _SECTIONS[section]
        if keys is None:
            continue
        if not isinstance(value, dict):
            raise GatewayError("GATEWAY_CONFIG", f"{path.name}: {section} must be a mapping")
        for key in value:
            if key not in keys:
                raise _unsafe(path, f"{section}.{key}" if str(key).isidentifier() else section)
    for (section, key), expected in _LOGGING.items():
        value = (data.get(section) or {}).get(key, expected)
        if type(value) is not bool or value is not expected:
            raise GatewayError(
                "GATEWAY_CONFIG_UNSAFE",
                f"{path.name}: {section}.{key} must be {str(expected).lower()} "
                "(prompt and reply logging stays off)",
            )
    general = data.get("general_settings") or {}
    for key, expected in _GENERAL_ENV.items():
        if key in general:
            if general[key] != expected:
                raise _unsafe(path, f"general_settings.{key} other than {expected}")
            allowed_env.add(f"general_settings.{key}")

    aliases: set[str] = set()
    providers: dict[str, str] = {}
    required_env: dict[str, tuple[str, ...]] = {}
    routes: dict[str, dict] = {}
    key_only: set[str] = set()
    for index, entry in enumerate(models):
        name = entry.get("model_name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            raise GatewayError("GATEWAY_CONFIG", f"{path.name}: every model needs a model_name")
        where = f"model_list[{index}]"
        if not _MODEL_NAME.fullmatch(name):  # printed in the grant: no control characters
            raise _unsafe(path, f"{where}.model_name (use letters, digits and ._:/-)")
        if set(entry) - _ENTRY:
            raise _unsafe(path, f"{where} key")
        params = entry.get("litellm_params") or {}
        info = entry.get("model_info") or {}
        if not isinstance(params, dict) or not isinstance(info, dict):
            raise _unsafe(path, f"{where} value (plain values only)")
        if not all(map(_scalar, [*params.values(), *info.values()])):
            raise _unsafe(path, f"{where} value (plain values only)")
        route = _entry_route(path, where, params, info, caps)
        if name in routes and (not route.key_only or name not in key_only):
            # The grant shows one line per alias: an alias served by several deployments of a
            # #1025 provider would hide a route (the key-only providers keep today's rule).
            if routes[name] != route.route or providers[name] != route.label:
                raise _unsafe(path, f"{where}.model_name (repeats an alias with another route)")
        allowed_env.update(f"{where}.litellm_params.{param}" for param in route.env_params)
        providers[name] = route.label
        required_env[name] = tuple(
            dict.fromkeys((*required_env.get(name, ()), *route.required_env))
        )
        routes[name] = route.route
        if route.key_only:
            key_only.add(name)
        aliases.add(name)
    edges: dict[str, list[str]] = {}
    for section, keys in _SECTIONS.items():
        values = (data.get(section) or {}) if keys else {}
        for key in _FALLBACKS:  # a fixed merge order: `fallbacks` first
            if key in values:
                _fallbacks(path, f"{section}.{key}", values[key], aliases, edges)
        for key, value in values.items():
            if key not in _FALLBACKS and not _scalar(value):
                raise _unsafe(path, f"{section}.{key} value (plain values only)")
    for where, _ in _env_strings(data, "config"):
        if where.removeprefix("config.") not in allowed_env:
            raise _unsafe(path, f"os.environ reference at {where.removeprefix('config.')}")
    guardrails = _guardrails(path, data["guardrails"]) if "guardrails" in data else []
    logging = {
        f"{section}.{key}": (data.get(section) or {}).get(key, default)
        for (section, key), (_, default) in _GUARDRAIL_LOGGING.items()
    }
    canonical = json.dumps(
        {"guardrails": guardrails, "logging": logging}, sort_keys=True, separators=(",", ":")
    )
    return ConfigReview(
        aliases,
        providers,
        hashlib.sha256(raw).hexdigest(),
        edges,
        guardrails=guardrails,
        guardrails_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        logging=logging,
        required_env=required_env,
        routes=routes,
        key_only=key_only,
    )


def _entry_route(path: Path, where: str, params: dict, info: dict, caps: GatewayCaps):
    try:
        return gateway_providers.review_entry(where, params, info, caps)
    except EntryRefused as refused:
        if not refused.sentence:
            raise _unsafe(path, refused.where) from None
        raise GatewayError(refused.code, f"{path.name}: {refused.where}") from None


def config_aliases(path: Path, caps: GatewayCaps) -> set[str]:
    """The model aliases a LiteLLM proxy config serves (`model_list[].model_name`), after
    `config_review` accepted the config for the gateway `caps`."""
    return config_review(path, caps).aliases
