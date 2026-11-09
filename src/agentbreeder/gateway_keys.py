"""Per-deployment LiteLLM virtual keys (spec §10, §18.2).

Every deployment that requests `model_access` gets its own key, minted after the gate and
approval, limited to the granted aliases, budget and rate, and revoked on remove/disable. The
key alias is derived from the deployment ID (`agentbreeder-<id>`), so a retry or a crash between
mint and record needs no extra state: it deletes by alias and mints again (LiteLLM never returns
an existing key's value). `reconcile` lists keys by the alias prefix and revokes orphans.
The key value goes only into the deployment's private secret file; the audit records the alias.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from agentbreeder.operator_config import Gateway, OperatorConfig, resolve_credential

PREFIX = "agentbreeder-"
TIMEOUT = 15
_OPENER = build_opener(ProxyHandler({}))  # never route the master key through a proxy


class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


def alias(deployment_id: str) -> str:
    return PREFIX + deployment_id


@dataclass
class LiteLLM:
    url: str
    master_key: str

    def _call(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
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
                return json.load(response)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise GatewayError(
                "GATEWAY_ERROR", f"LiteLLM {path} returned HTTP {error.code}"
            ) from None
        except (URLError, TimeoutError, OSError, ValueError) as error:
            raise GatewayError(
                "GATEWAY_UNREACHABLE", f"LiteLLM at {self.url} is unreachable"
            ) from error

    def mint(
        self,
        key_alias: str,
        models: list[str],
        budget: float,
        rpm: int,
        metadata: dict,
        duration: str | None = None,
    ) -> str:
        """Mint a virtual key. `budget` renews every 30 days (LiteLLM `budget_duration`).
        `duration` makes the key itself expire (e.g. "6h"): set it for short-lived keys whose
        revoke could be skipped by a killed process."""
        self.revoke(key_alias)  # idempotent retry: an earlier attempt may have minted it
        body = {
            "key_alias": key_alias,
            "models": models,
            "max_budget": budget,
            "budget_duration": "30d",
            "rpm_limit": rpm,
            "metadata": metadata,
        }
        if duration:
            body["duration"] = duration
        result = self._call("POST", "/key/generate", body)
        key = (result or {}).get("key")
        if not isinstance(key, str) or not key:
            raise GatewayError("GATEWAY_ERROR", "LiteLLM did not return a key")
        return key

    def revoke(self, key_alias: str) -> None:
        """Delete by alias. A missing alias is already revoked (404)."""
        self._call("POST", "/key/delete", {"key_aliases": [key_alias]})

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
# (#739 security review): keys may only flow to their own provider's default endpoint.
PROVIDER_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
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
_PARAMS = {
    "model",
    "api_key",
    "timeout",
    "stream_timeout",
    "max_retries",
    "dimensions",
    "rpm",
    "tpm",
    "max_tokens",
    "temperature",
    "top_p",
    "drop_params",
    "reasoning_effort",
}
_GENERAL_ENV = {"master_key": _ENV + "LITELLM_MASTER_KEY", "database_url": _ENV + "DATABASE_URL"}


@dataclass
class ConfigReview:
    aliases: set[str]
    providers: dict[str, str]  # alias -> "gemini (GEMINI_API_KEY)", shown in the services grant
    sha256: str


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


def config_review(path: Path) -> ConfigReview:
    """Parse a LiteLLM proxy config and refuse anything that could send a credential somewhere
    other than its provider: only allow-listed keys per section, no custom endpoints or
    callbacks, and `os.environ/` only for the master key, the database URL and each model's own
    provider key."""
    import hashlib

    from agentbreeder.strict_yaml import StrictYamlError, load_file

    try:
        data = load_file(path, 256 * 1024)
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
    general = data.get("general_settings") or {}
    for key, expected in _GENERAL_ENV.items():
        if key in general:
            if general[key] != expected:
                raise _unsafe(path, f"general_settings.{key} other than {expected}")
            allowed_env.add(f"general_settings.{key}")

    aliases: set[str] = set()
    providers: dict[str, str] = {}
    for index, entry in enumerate(models):
        name = entry.get("model_name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            raise GatewayError("GATEWAY_CONFIG", f"{path.name}: every model needs a model_name")
        where = f"model_list[{index}]"
        if set(entry) - _ENTRY:
            raise _unsafe(path, f"{where} key")
        params = entry.get("litellm_params") or {}
        if not isinstance(params, dict) or set(params) - _PARAMS:
            raise _unsafe(path, f"{where}.litellm_params key (custom endpoints are refused)")
        model = params.get("model")
        provider = model.split("/", 1)[0] if isinstance(model, str) and "/" in model else None
        key = params.get("api_key")
        if key is not None:
            expected = PROVIDER_KEYS.get(provider)
            if expected is None or key != _ENV + expected:
                raise _unsafe(path, f"{where}.litellm_params.api_key (use the provider's own key)")
            allowed_env.add(f"{where}.litellm_params.api_key")
        label = provider if provider in PROVIDER_KEYS else "unknown"
        providers[name] = f"{label} ({key[len(_ENV) :] if key else 'no key'})"
        aliases.add(name)
    for where, _ in _env_strings(data, "config"):
        if where.removeprefix("config.") not in allowed_env:
            raise _unsafe(path, f"os.environ reference at {where.removeprefix('config.')}")
    return ConfigReview(aliases, providers, hashlib.sha256(path.read_bytes()).hexdigest())


def config_aliases(path: Path) -> set[str]:
    """The model aliases a LiteLLM proxy config serves (`model_list[].model_name`), after
    `config_review` accepted the config."""
    return config_review(path).aliases
