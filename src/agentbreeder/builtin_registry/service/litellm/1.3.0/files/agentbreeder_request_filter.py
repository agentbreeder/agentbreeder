"""AgentBreeder's request filter for the built-in gateway (#1105), loaded by files/start.py.

A model alias's provider, endpoint and credentials come from the reviewed gateway config only.
LiteLLM's router merges request fields over a deployment's `litellm_params`, so this filter
refuses, before any provider call:

- a request carrying a provider-routing, endpoint or credential field (`ROUTING_FIELDS`) at the
  top level, in `extra_body`, `litellm_embedding_config` or a request-level fallback target
  (`GATEWAY_PARAM_REFUSED`). Metadata is not scanned: the router does not route from it, the
  proxy writes its own keys there (`headers`, `user_api_key_*`), and LiteLLM's blocklist already
  covers its endpoint and credential names in metadata;
- any call type other than chat completions and embeddings that reaches this hook (provider
  pass-through, file upload, responses, messages and the rest; `GATEWAY_ROUTE_REFUSED`). Routes
  that never call the hook are closed by the deployment key's `allowed_routes`.

Top-level fields the proxy itself can add (`api_key`, `headers`, `organization`) only appear when
`general_settings` turns on header forwarding, which the gateway config review refuses; the
`api-version` query parameter becomes `api_version` and is refused like the body field.

The refusal is a 400 whose detail starts with the code. One log line per refusal names the code,
the field, where it was, the call type and the key alias; never a value or key material.
LiteLLM's own blocklist (`api_base`, `base_url`, ...) still runs first.
"""

import json
import re
import sys

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

# Fields that choose a provider, a deployment, an endpoint, a credential or a provider tenant.
ROUTING_FIELDS = frozenset(
    {
        # provider and deployment selection
        "custom_llm_provider",
        "litellm_credential_name",
        "specific_deployment",
        "model_id",
        "deployment_id",
        "use_litellm_proxy",
        "use_in_pass_through",
        "use_chat_completions_api",
        "litellm_params",
        # endpoints (LiteLLM refuses most of these too)
        "api_base",
        "base_url",
        "api_type",
        "api_version",
        "user_config",
        "aws_bedrock_runtime_endpoint",
        "aws_sts_endpoint",
        # credentials and the headers that carry them
        "api_key",
        "organization",
        "extra_headers",
        "headers",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_session_token",
        "aws_session_name",
        "aws_profile_name",
        "aws_role_name",
        "aws_web_identity_token",
        "azure_ad_token",
        "azure_ad_token_provider",
        "azure_username",
        "azure_password",
        "azure_scope",
        "client_id",
        "client_secret",
        "tenant_id",
        "vertex_credentials",
        "vertex_ai_credentials",
        "gigachat_access_token",
        "gigachat_auth_url",
        "gigachat_scope",
        "ssl_verify",
        # the rest of LiteLLM 1.106.2's CredentialLiteLLMParams (litellm/types/router.py)
        "aws_external_id",
        "aws_session_tags",
        "aws_bedrock_project_id",
        "aws_batch_role_arn",
        "gcs_bucket_name",
        "bucket_name",
        "s3_bucket_name",
        "s3_endpoint_url",
        "s3_region_name",
        "s3_access_key_id",
        "s3_secret_access_key",
        "s3_encryption_key_id",
        "s3_bucket_owner",
        "s3_output_bucket_name",
        "bedrock_tags",
        "watsonx_region_name",
        "anthropic_federation_rule_id",
        "anthropic_organization_id",
        "anthropic_service_account_id",
        "anthropic_federation_workspace_id",
        "anthropic_identity_token_file",
        "anthropic_identity_token",
        "anthropic_identity_source",
        "anthropic_issuer_url",
        "anthropic_issuer_subject",
        "anthropic_issuer_audience",
        "anthropic_issuer_ttl_seconds",
        "anthropic_issuer_signing_key_ref",
        "anthropic_keycloak_token_url",
        "anthropic_keycloak_client_id",
        "anthropic_keycloak_auth_method",
        "anthropic_keycloak_client_secret_ref",
        "anthropic_keycloak_scope",
        "anthropic_disable_workload_identity_federation",
        "openai_identity_provider_id",
        "openai_service_account_id",
        "openai_identity_token_file",
        # credential and endpoint names LiteLLM's providers read from request kwargs
        "anthropic_workspace_id",
        "aws_workspace_id",
        "azure_endpoint",
        "azure_authority_host",
        "azure_client_id",
        "azure_tenant_id",
        "gcs_path_service_account",
        "watsonx_credentials",
        "watsonx_token",
        # provider tenancy and region
        "region_name",
        "aws_region_name",
        "vertex_project",
        "vertex_location",
        "vertex_ai_project",
        "vertex_ai_location",
    }
)
# LiteLLM's call types for chat completions and embeddings (streaming included).
ALLOWED_CALL_TYPES = frozenset({"acompletion", "completion", "aembedding", "embeddings"})
NESTED = ("extra_body", "litellm_embedding_config")
FALLBACKS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")
PARAM_REFUSED = "GATEWAY_PARAM_REFUSED"
ROUTE_REFUSED = "GATEWAY_ROUTE_REFUSED"
_ALIAS = re.compile(r"agentbreeder-[A-Za-z0-9-]{1,64}")
_LABEL = re.compile(r"[a-z_]{1,40}")
_MAX_DEPTH = 12  # LiteLLM accepts fallbacks nested up to 2 * ROUTER_MAX_FALLBACKS (10)


class _TooDeep(Exception):
    """Fallbacks nested deeper than the filter reads: refused, never passed unread."""


def _mapping(value):
    """A dict, or a JSON object sent as a string (form and `extra_body` callers do this)."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value[:1] == "{":
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _found(block):
    if not isinstance(block, dict):
        return None
    for key in block:
        if isinstance(key, str) and key in ROUTING_FIELDS:
            return key
    return None


def _fallback_targets(value, depth=0):
    if not isinstance(value, list):
        return
    if depth > _MAX_DEPTH:
        raise _TooDeep
    for item in value:
        item = _mapping(item)
        if item is None:
            continue
        yield item
        for nested in item.values():
            yield from _fallback_targets(nested, depth + 1)


def _blocks(data):
    """(where, block) for every place a request can carry router parameters."""
    yield "body", data
    for name in NESTED:
        nested = _mapping(data.get(name))
        if nested is not None:
            yield name, nested
    for source in (data, _mapping(data.get("router_settings_override")) or {}):
        for name in FALLBACKS:
            for target in _fallback_targets(source.get(name)):
                yield "fallbacks", target


def refusal(data, call_type):
    """(code, field, where) when the request must be refused, else None. `data` is the request
    as LiteLLM's pre-call hook sees it: the caller's body plus what the proxy derived from
    headers and query parameters."""
    if call_type not in ALLOWED_CALL_TYPES:
        return ROUTE_REFUSED, None, "route"
    if not isinstance(data, dict):
        return PARAM_REFUSED, None, "body"
    try:
        for where, block in _blocks(data):
            field = _found(block)
            if field:
                return PARAM_REFUSED, field, where
    except _TooDeep:
        return PARAM_REFUSED, None, "fallbacks"
    return None


def _label(value):
    return value if isinstance(value, str) and _LABEL.fullmatch(value) else "other"


def log_line(code, field, where, call_type, alias):
    alias = alias if isinstance(alias, str) and _ALIAS.fullmatch(alias) else "other"
    return (
        f"agentbreeder gateway: refused {code} field={_label(field) if field else '-'} "
        f"in={_label(where.replace('.', '_'))} call={_label(call_type)} key_alias={alias}"
    )


def message(code, field):
    if code == ROUTE_REFUSED:
        return f"{code}: the gateway serves chat completions and embeddings only"
    named = f" ({field})" if field else ""
    return f"{code}: the gateway refuses provider-routing parameters in requests{named}"


class RequestFilter(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        found = refusal(data, call_type)
        if found is None:
            return None
        code, field, where = found
        alias = getattr(user_api_key_dict, "key_alias", None)
        print(log_line(code, field, where, call_type, alias), file=sys.stderr, flush=True)
        raise HTTPException(status_code=400, detail=message(code, field))


handler = RequestFilter()
