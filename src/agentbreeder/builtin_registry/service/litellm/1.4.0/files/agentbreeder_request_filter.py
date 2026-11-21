"""AgentBreeder's request filter for the built-in gateway (#1105, #1089), loaded by files/start.py.

A model alias's provider, endpoint and credentials come from the reviewed gateway config only.
LiteLLM's router merges request fields over a deployment's `litellm_params`, so this filter
refuses, before any provider call:

- a request carrying a provider-routing, endpoint or credential field (`ROUTING_FIELDS`) at the
  top level, in `extra_body`, `litellm_embedding_config` or a request-level fallback target
  (`GATEWAY_PARAM_REFUSED`). Metadata is not scanned: the router does not route from it, the
  proxy writes its own keys there (`headers`, `user_api_key_*`), and LiteLLM's blocklist already
  covers its endpoint and credential names in metadata;
- any call type other than chat completions, embeddings and Anthropic messages that reaches this
  hook (provider pass-through, file upload, responses and the rest; `GATEWAY_ROUTE_REFUSED`).
  Routes that never call the hook are closed by the deployment key's `allowed_routes`.

1.4.0 (#1089) serves LiteLLM's Anthropic Messages route (`/v1/messages`, call type
`anthropic_messages`) for the Claude Agent SDK runtime. Chat completions and embeddings are
decided exactly as in 1.3.0. A Messages request gets every check above, and is also refused
(`GATEWAY_PARAM_REFUSED`) when it reaches provider-side tools, execution or storage, or picks a
provider tier or region (read from LiteLLM 1.106.2's `/v1/messages` path):

- `mcp_servers`: the provider connects to remote MCP servers from its own network;
- `container`: a provider code-execution container and its skills;
- `service_tier`, `speed`: a provider capacity tier or fast mode, priced outside the alias's
  configured model and not checked against LiteLLM's cost map;
- `inference_geo`: the provider region (1.3.0 refuses `region_name` and the like);
- `betas`, `anthropic_beta`: beta flags switch on server tools and the Files API (LiteLLM adds
  the betas its own features need);
- `compaction`, `safeguards`: provider-side features LiteLLM turns into beta flags, which no
  AgentBreeder runtime sends;
- `tools` with any `type` but `custom`: provider server tools (web search and fetch, code
  execution, tool search), Anthropic-defined tools (computer, bash, text editor, memory), MCP
  toolsets, LiteLLM's MCP gateway (`mcp`) and advisor tools (a second model call, with
  credentials it can take from the tool). A client tool passes when it has only CLIENT_TOOL_KEYS
  (so no `allowed_callers`, which hands a tool to code execution); `tools` that is not a list of
  objects is refused;
- `provider_specific_header` carrying anything but `anthropic-version`: LiteLLM copies the
  `anthropic-beta` header and an Anthropic OAuth `Authorization` there, and sends it to the
  provider; a caller can also send the field in the body;
- a content block in `messages` or `system` (and in a `tool_result`'s or a document's nested
  content) that is a `container_upload`, or whose `source` is a Files API id (`file`: a file in
  the gateway's provider account, which every key shares) or a `url` the provider would fetch
  from its own network. Only the content-block structure is read, never a `tool_use` input or
  text; content nested deeper than the filter reads is refused.

`metadata` on this route is Anthropic's own (`user_id`); the proxy's is `litellm_metadata`.
Neither is scanned, as on chat.

Not covered: `/v1/messages/count_tokens`. LiteLLM matches a key's `allowed_routes` by prefix, so
a key allowed `/v1/messages` reaches it, and its handler calls no pre-call hook.

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
# LiteLLM's call types for chat completions, embeddings and Anthropic messages (streaming
# included).
MESSAGES_CALL_TYPES = frozenset({"anthropic_messages", "aanthropic_messages"})
ALLOWED_CALL_TYPES = (
    frozenset({"acompletion", "completion", "aembedding", "embeddings"}) | MESSAGES_CALL_TYPES
)
# Anthropic Messages fields refused on the Messages route (see the module docstring).
MESSAGES_REFUSED_FIELDS = frozenset(
    {
        "mcp_servers",
        "container",
        "service_tier",
        "speed",
        "inference_geo",
        "betas",
        "anthropic_beta",
        "compaction",
        "safeguards",
    }
)
# The keys a client tool may have (Anthropic's custom tool, without `allowed_callers`).
CLIENT_TOOL_KEYS = frozenset(
    {
        "type",
        "name",
        "description",
        "input_schema",
        "cache_control",
        "strict",
        "input_examples",
        "eager_input_streaming",
        "defer_loading",
    }
)
MESSAGES_HEADERS = frozenset({"anthropic-version"})  # all a Messages request may send on
REFUSED_SOURCES = frozenset({"file", "url"})  # Files API ids; URLs the provider fetches
REFUSED_BLOCKS = frozenset({"container_upload"})
NESTED = ("extra_body", "litellm_embedding_config")
FALLBACKS = ("fallbacks", "context_window_fallbacks", "content_policy_fallbacks")
PARAM_REFUSED = "GATEWAY_PARAM_REFUSED"
ROUTE_REFUSED = "GATEWAY_ROUTE_REFUSED"
_ALIAS = re.compile(r"agentbreeder-[A-Za-z0-9-]{1,64}")
_LABEL = re.compile(r"[a-z_]{1,40}")
_MAX_DEPTH = 12  # LiteLLM accepts fallbacks nested up to 2 * ROUTER_MAX_FALLBACKS (10)
_MAX_CONTENT_DEPTH = 8  # blocks inside tool_result or document content, nested


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


def _client_tools(tools):
    """True when `tools` holds client tools only: objects with no `type`, or `custom`."""
    if not isinstance(tools, list):
        return False
    return all(
        isinstance(tool, dict)
        and tool.get("type", "custom") == "custom"
        and not set(tool) - CLIENT_TOOL_KEYS
        for tool in tools
    )


def _api_version_only(value):
    """True when `provider_specific_header` (one entry or a list) carries `anthropic-version`
    and nothing else."""
    entries = value if isinstance(value, list) else [value]
    if not entries:
        return False
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"custom_llm_provider", "extra_headers"}:
            return False
        headers = entry.get("extra_headers")
        if not isinstance(headers, dict):
            return False
        for name, header in headers.items():
            if not isinstance(name, str) or name.lower() not in MESSAGES_HEADERS:
                return False
            if not isinstance(header, str):
                return False
    return True


def _refused_blocks(blocks, depth=0):
    """True when a list of content blocks holds a refused block or source, or nests deeper than
    the filter reads. Only the block structure is read: a block's `content` (tool results) and
    a `content` source's blocks (documents), never a `tool_use` input or text."""
    if not isinstance(blocks, list):
        return False
    if depth > _MAX_CONTENT_DEPTH:
        return True
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") in REFUSED_BLOCKS:
            return True
        source = block.get("source")
        if isinstance(source, dict):
            if source.get("type") in REFUSED_SOURCES:
                return True
            if _refused_blocks(source.get("content"), depth + 1):
                return True
        if _refused_blocks(block.get("content"), depth + 1):
            return True
    return False


def _refused_content(field, value):
    if field == "system":
        return _refused_blocks(value)
    if not isinstance(value, list):
        return False
    return any(isinstance(m, dict) and _refused_blocks(m.get("content")) for m in value)


def _messages_refusal(data):
    """(field, where) for the Messages-only checks, else None: in every place a request can
    carry parameters (the body, `extra_body`, fallback targets), as for the routing fields."""
    for where, block in _blocks(data):
        for key in block:
            if isinstance(key, str) and key in MESSAGES_REFUSED_FIELDS:
                return key, where
        tools = block.get("tools")
        if tools is not None and not _client_tools(tools):
            return "tools", where
        headers = block.get("provider_specific_header")
        if headers is not None and not _api_version_only(headers):
            return "provider_specific_header", where
        for field in ("messages", "system"):
            if _refused_content(field, block.get(field)):
                return field, where
    return None


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
        if call_type in MESSAGES_CALL_TYPES:
            found = _messages_refusal(data)
            if found:
                return PARAM_REFUSED, *found
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


_MESSAGES_FIELDS = MESSAGES_REFUSED_FIELDS | {
    "tools",
    "provider_specific_header",
    "messages",
    "system",
}


def message(code, field):
    if code == ROUTE_REFUSED:
        return (
            f"{code}: the gateway serves chat completions, embeddings and Anthropic messages only"
        )
    named = f" ({field})" if field else ""
    if field in _MESSAGES_FIELDS:
        return (
            f"{code}: the gateway refuses provider-side tools, execution and storage in Messages "
            f"requests{named}"
        )
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
