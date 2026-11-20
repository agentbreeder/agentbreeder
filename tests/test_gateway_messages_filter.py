"""#1089: `service/litellm@1.4.0` lets the Anthropic Messages route (`/v1/messages`) through the
request filter that 1.3.0 (#1105) introduced, with 1.3.0's refusals and the Messages fields that
reach provider-side tools, execution or storage.

The filter is loaded with stand-ins for `fastapi` and LiteLLM's `CustomLogger`, as in
tests/test_gateway_request_filter.py (1.3.0). The pinned-image half is
tests/test_gateway_messages_filter_docker.py (opt-in).
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import types

import pytest
import yaml
from test_gateway_request_filter import (
    ALIAS,
    CREDENTIAL_PARAMS,
    MARKER,
    SPEC_FIELDS,
    V13,
    _chat,
    _HTTPException,
    _load,
)

from agentbreeder import gateway_keys, gateway_providers, registry
from agentbreeder.services.schema import load_service

V14 = registry.BUILTIN_ROOT / "service/litellm/1.4.0"
MESSAGES = "anthropic_messages"
CHAT_CALL_TYPES = ["acompletion", "completion", "aembedding", "embeddings"]

# Anthropic Messages fields the filter refuses on this route, each with its reason in the filter.
REFUSED_FIELDS = {
    "mcp_servers",
    "container",
    "service_tier",
    "inference_geo",
    "speed",
    "betas",
    "anthropic_beta",
    "compaction",
    "safeguards",
}

# The tool types a provider (or LiteLLM itself) runs: server tools, Anthropic-defined client
# tools, MCP toolsets and LiteLLM's MCP gateway and advisor tools. Only plain client tools pass.
TYPED_TOOLS = [
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
    {"type": "web_fetch_20250910", "name": "web_fetch"},
    {"type": "code_execution_20250825", "name": "code_execution"},
    {"type": "computer_20250124", "name": "computer", "display_width_px": 1024},
    {"type": "bash_20250124", "name": "bash"},
    {"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"},
    {"type": "memory_20250818", "name": "memory"},
    {"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"},
    {"type": "mcp_toolset", "mcp_server_name": "x"},
    {"type": "mcp", "server_url": "litellm_proxy", "server_label": "x"},
    {"type": "advisor_20260301", "name": "advisor", "model": "m2", "api_key": "k"},
    {"type": "function", "function": {"name": "f", "parameters": {}}},
    {"type": None, "name": "x", "input_schema": {"type": "object"}},
]


@pytest.fixture
def rules(monkeypatch):
    return _load(
        "agentbreeder_request_filter_140",
        V14 / "files/agentbreeder_request_filter.py",
        monkeypatch,
    )


@pytest.fixture
def old_rules(monkeypatch):
    return _load(
        "agentbreeder_request_filter_130",
        V13 / "files/agentbreeder_request_filter.py",
        monkeypatch,
    )


def _messages(**extra):
    """A `/v1/messages` request as LiteLLM 1.106.2's pre-call hook sees it: the caller's fields,
    the proxy's own metadata under `litellm_metadata` (the route's metadata variable), the
    `anthropic-version` header the proxy copied into `provider_specific_header`, and a body
    snapshot."""
    data = {
        "model": "support-model",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "hi"}],
        **extra,
    }
    data.setdefault(
        "provider_specific_header",
        {
            "custom_llm_provider": "anthropic,bedrock,bedrock_mantle,vertex_ai",
            "extra_headers": {"anthropic-version": "2023-06-01"},
        },
    )
    data["litellm_metadata"] = {
        "headers": {"user-agent": "x", "authorization": "redacted"},
        "user_api_key_alias": ALIAS,
        "requester_metadata": dict(data.get("metadata") or {}),
    }
    data["proxy_server_request"] = {"url": "http://gw/v1/messages", "body": dict(data)}
    return data


CLIENT_TOOL = {
    "name": "mcp__agentbreeder__lookup_order",
    "description": "Look up an order",
    # a tool's own parameter names are the caller's, not LiteLLM's or Anthropic's
    "input_schema": {
        "type": "object",
        "properties": {"api_key": {}, "container": {}, "mcp_servers": {}, "type": {}},
        "required": ["api_key"],
    },
    "cache_control": {"type": "ephemeral"},
}


# --- the call types ------------------------------------------------------------------------------


def test_the_messages_call_types_join_chat_and_embeddings(rules, old_rules):
    assert rules.ALLOWED_CALL_TYPES == old_rules.ALLOWED_CALL_TYPES | {
        "anthropic_messages",
        "aanthropic_messages",
    }
    assert rules.MESSAGES_CALL_TYPES == {"anthropic_messages", "aanthropic_messages"}
    assert rules.ROUTING_FIELDS == old_rules.ROUTING_FIELDS
    assert REFUSED_FIELDS == rules.MESSAGES_REFUSED_FIELDS
    assert not rules.MESSAGES_REFUSED_FIELDS & rules.ROUTING_FIELDS


@pytest.mark.parametrize(
    "call_type",
    [
        "pass_through_endpoint",
        "aresponses",
        "acreate_file",
        "acreate_batch",
        "_arealtime",
        "aimage_generation",
        "amoderation",
        "atext_completion",
        "acount_tokens",  # `/v1/messages/count_tokens`: not a messages call
        "call_mcp_tool",
        None,
    ],
)
def test_other_call_types_are_still_refused(rules, call_type):
    assert rules.refusal(_messages(), call_type) == ("GATEWAY_ROUTE_REFUSED", None, "route")
    message = rules.message("GATEWAY_ROUTE_REFUSED", None)
    assert message == (
        "GATEWAY_ROUTE_REFUSED: the gateway serves chat completions, embeddings and "
        "Anthropic messages only"
    )


# --- a plain Messages request passes -------------------------------------------------------------


@pytest.mark.parametrize("call_type", ["anthropic_messages", "aanthropic_messages"])
def test_a_plain_messages_request_passes(rules, call_type):
    history = [
        {"role": "user", "content": [{"type": "text", "text": "where is order 7?"}]},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "...", "signature": "s"},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "mcp__agentbreeder__lookup_order",
                    "input": {"api_key": "not a credential, a tool argument"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": [{"type": "text", "text": "shipped"}],
                },
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": "iVBO"},
                },
                {
                    "type": "document",
                    "source": {"type": "text", "media_type": "text/plain", "data": "x"},
                },
            ],
        },
    ]
    data = _messages(
        messages=history,
        system=[
            {"type": "text", "text": "You are Northwind", "cache_control": {"type": "ephemeral"}}
        ],
        tools=[
            CLIENT_TOOL,
            {**CLIENT_TOOL, "type": "custom", "name": "b", "strict": True},
            {"name": "c", "defer_loading": False, "input_examples": [{"id": "7"}]},
        ],
        tool_choice={"type": "auto", "disable_parallel_tool_use": True},
        stop_sequences=["END"],
        temperature=0.2,
        top_p=0.9,
        top_k=40,
        stream=True,
        thinking={"type": "enabled", "budget_tokens": 2048},
        metadata={"user_id": "u-1"},
    )
    assert rules.refusal(data, call_type) is None
    assert rules.refusal(_messages(system="plain text"), call_type) is None
    assert rules.refusal(_messages(tools=[]), call_type) is None
    assert rules.refusal(_messages(tools=None, provider_specific_header=None), call_type) is None
    # No anthropic-version header: the proxy writes no provider_specific_header.
    bare = _messages()
    del bare["provider_specific_header"]
    assert rules.refusal(bare, call_type) is None


def test_the_claude_runtime_upstream_body_passes(rules):
    """The body runtime/claude-agent-sdk@1.0.0's proxy sends (F7, PR #1177 `upstream_body`):
    its MESSAGES_FIELDS allow-list, with `tool_choice: none` on the step cap's last call."""
    body = {
        "model": "support-model",
        "messages": [{"role": "user", "content": "hi"}],
        "system": [{"type": "text", "text": "x"}],
        "tools": [CLIENT_TOOL, {"name": "DeferredToolPlaceholder", "input_schema": {}}],
        "tool_choice": {"type": "none"},
        "max_tokens": 16384,
        "temperature": 1,
        "top_p": 0.99,
        "top_k": 5,
        "stop_sequences": [],
        "thinking": {"type": "adaptive"},
    }
    assert rules.refusal(_messages(**body), MESSAGES) is None


@pytest.mark.parametrize("field", ["context_management", "output_config", "cache_control"])
def test_provider_options_that_run_nothing_pass(rules, field):
    """Context editing, structured output and automatic caching change the answer, not where
    the request goes or what runs: they pass, as provider options do on chat."""
    assert rules.refusal(_messages(**{field: {"type": "x"}}), MESSAGES) is None


# --- what the Messages route refuses -------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(REFUSED_FIELDS))
@pytest.mark.parametrize("where", ["body", "extra_body", "extra_body_string", "fallbacks"])
def test_a_refused_messages_field_is_refused(rules, field, where):
    value = [{"type": "url", "url": "https://mcp.example", "name": "x"}]
    if where == "body":
        data = _messages(**{field: value})
    elif where == "extra_body":
        data = _messages(extra_body={field: value})
    elif where == "fallbacks":
        data = _messages(fallbacks=[{"model": "m2", field: value}])
    else:
        data = _messages(extra_body=json.dumps({field: value}))
    code, found, _ = rules.refusal(data, MESSAGES)
    assert (code, found) == ("GATEWAY_PARAM_REFUSED", field)


def test_fallback_targets_get_the_messages_checks(rules):
    tool = _messages(fallbacks=[{"m2": [{"model": "m3", "tools": [TYPED_TOOLS[0]]}]}])
    assert rules.refusal(tool, MESSAGES) == ("GATEWAY_PARAM_REFUSED", "tools", "fallbacks")
    header = {"extra_headers": {"anthropic-beta": "x"}}
    found = rules.refusal(
        _messages(fallbacks=[{"model": "m2", "provider_specific_header": header}]), MESSAGES
    )
    assert found == ("GATEWAY_PARAM_REFUSED", "provider_specific_header", "fallbacks")
    file_block = {"type": "image", "source": {"type": "file", "file_id": "f"}}
    target = {"model": "m2", "messages": [{"role": "user", "content": [file_block]}]}
    found = rules.refusal(_messages(context_window_fallbacks=[target]), MESSAGES)
    assert found == ("GATEWAY_PARAM_REFUSED", "messages", "fallbacks")
    assert rules.refusal(_messages(fallbacks=[{"model": "m2"}, "m3"]), MESSAGES) is None


@pytest.mark.parametrize("field", sorted(SPEC_FIELDS | set(CREDENTIAL_PARAMS) | {"headers"}))
@pytest.mark.parametrize("where", ["body", "extra_body", "fallbacks"])
def test_routing_and_credential_fields_are_refused_on_messages(rules, field, where):
    if where == "body":
        data = _messages(**{field: "x"})
    elif where == "extra_body":
        data = _messages(extra_body={field: "x"})
    else:
        data = _messages(fallbacks=[{"model": "m2", field: "x"}])
    code, found, _ = rules.refusal(data, MESSAGES)
    assert (code, found) == ("GATEWAY_PARAM_REFUSED", field)


@pytest.mark.parametrize("tool", TYPED_TOOLS, ids=lambda t: str(t.get("type")))
def test_typed_tools_are_refused(rules, tool):
    found = rules.refusal(_messages(tools=[CLIENT_TOOL, tool]), MESSAGES)
    assert found == ("GATEWAY_PARAM_REFUSED", "tools", "body")
    found = rules.refusal(_messages(extra_body={"tools": [tool]}), MESSAGES)
    assert found == ("GATEWAY_PARAM_REFUSED", "tools", "extra_body")


@pytest.mark.parametrize(
    "tools",
    [
        {"name": "x"},
        "web_search",
        [None],
        [["x"]],
        [{"type": 7}],
        [{"name": "x", "input_schema": {}, "allowed_callers": ["code_execution_20250825"]}],
        [{"name": "x", "input_schema": {}, "server_url": "https://mcp.example"}],
    ],
)
def test_malformed_tools_fail_closed(rules, tools):
    assert rules.refusal(_messages(tools=tools), MESSAGES) == (
        "GATEWAY_PARAM_REFUSED",
        "tools",
        "body",
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"anthropic-beta": "mcp-client-2025-04-04"},
        {"anthropic-beta": "code-execution-2025-08-25,files-api-2025-04-14"},
        {"Anthropic-Beta": "web-search-2025-03-05"},
        {"authorization": "Bearer sk-ant-oat01-" + "x" * 8},
        {"typesafe-danger-zone-x": "1"},
        {"x-api-key": "k"},
        {"anthropic-version": "2023-06-01", "anthropic-beta": "x"},
        {"anthropic-version": 7},
    ],
)
def test_provider_headers_other_than_the_api_version_are_refused(rules, headers):
    """LiteLLM copies `anthropic-beta` and an Anthropic OAuth `Authorization` into
    `provider_specific_header`, which reaches the provider; a caller can also send that field in
    the body. Only `anthropic-version` passes on this route."""
    for value in (
        {"custom_llm_provider": "anthropic", "extra_headers": headers},
        [
            {"custom_llm_provider": "anthropic", "extra_headers": {"anthropic-version": "1"}},
            {"custom_llm_provider": "anthropic", "extra_headers": headers},
        ],
    ):
        found = rules.refusal(_messages(provider_specific_header=value), MESSAGES)
        assert found == ("GATEWAY_PARAM_REFUSED", "provider_specific_header", "body")


@pytest.mark.parametrize(
    "value",
    [
        "anthropic-beta: x",
        ["x"],
        {"extra_headers": "x"},
        {"extra_headers": {"anthropic-version": "1"}, "headers": {"x-api-key": "k"}},
        [],
    ],
)
def test_malformed_provider_headers_fail_closed(rules, value):
    found = rules.refusal(_messages(provider_specific_header=value), MESSAGES)
    assert found == ("GATEWAY_PARAM_REFUSED", "provider_specific_header", "body")


@pytest.mark.parametrize(
    "block",
    [
        {"type": "image", "source": {"type": "file", "file_id": "file_1"}},
        {"type": "document", "source": {"type": "file", "file_id": "file_1"}},
        {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}},
        {"type": "document", "source": {"type": "url", "url": "https://example.com/a.pdf"}},
        {
            "type": "document",
            "source": {
                "type": "content",
                "content": [{"type": "image", "source": {"type": "file", "file_id": "f"}}],
            },
        },
        {"type": "container_upload", "file_id": "file_1"},
        {
            "type": "tool_result",
            "tool_use_id": "t",
            "content": [{"type": "image", "source": {"type": "file", "file_id": "f"}}],
        },
    ],
)
def test_files_api_and_url_content_is_refused(rules, block):
    """A Files API id names a file in the gateway's provider account, shared by every key; a
    URL source is fetched by the provider from its own network."""
    data = _messages(messages=[{"role": "user", "content": [block]}])
    assert rules.refusal(data, MESSAGES) == ("GATEWAY_PARAM_REFUSED", "messages", "body")
    data = _messages(system=[block])
    assert rules.refusal(data, MESSAGES) == ("GATEWAY_PARAM_REFUSED", "system", "body")


def test_tool_inputs_and_text_are_not_read_as_content(rules):
    """A tool's arguments and results are the agent's data: shapes that look like refused
    blocks inside a `tool_use` input or a JSON text never refuse the turn."""
    lookalike = {
        "source": {"type": "file", "file_id": "f"},
        "type": "container_upload",
        "deep": [[[[[[[[[[[[{"a": 1}]]]]]]]]]]]],
    }
    history = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t", "name": "n", "input": lookalike}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t",
                    "content": [{"type": "text", "text": json.dumps(lookalike)}],
                }
            ],
        },
    ]
    assert rules.refusal(_messages(messages=history), MESSAGES) is None


def test_content_deeper_than_the_filter_reads_is_refused(rules):
    node: list = [{"type": "text", "text": "x"}]
    for _ in range(30):
        node = [{"type": "tool_result", "tool_use_id": "t", "content": node}]
    data = _messages(messages=[{"role": "user", "content": node}])
    assert rules.refusal(data, MESSAGES) == ("GATEWAY_PARAM_REFUSED", "messages", "body")
    node = [{"type": "text", "text": "x"}]
    for _ in range(3):  # what a real history nests: tool results inside a message
        node = [{"type": "tool_result", "tool_use_id": "t", "content": node}]
    assert rules.refusal(_messages(messages=[{"role": "user", "content": node}]), MESSAGES) is None


def test_the_first_refusal_is_reported(rules):
    data = _messages(custom_llm_provider="openai", mcp_servers=[{}], tools=[TYPED_TOOLS[0]])
    assert rules.refusal(data, MESSAGES)[1] == "custom_llm_provider"


# --- chat completions and embeddings are unchanged -----------------------------------------------


def _corpus():
    """Chat and embedding requests, including Messages-only fields that 1.3.0 lets through on
    chat: 1.4.0 must decide every one exactly as 1.3.0 does."""
    yield _chat()
    yield _chat(stream=True, tools=[{"type": "function", "function": {"name": "f"}}])
    yield {"model": "embed", "input": "x"}
    yield None
    yield _chat(fallbacks=[{"m": [{"m": []}]}] * 2)
    for field in sorted(REFUSED_FIELDS | {"provider_specific_header", "tools", "system"}):
        yield _chat(**{field: [{"type": "web_search_20250305", "name": "w"}]})
    yield _chat(
        messages=[{"role": "user", "content": [{"type": "file", "file": {"file_id": "x"}}]}]
    )
    yield _chat(provider_specific_header={"extra_headers": {"anthropic-beta": "x"}})
    for field, where in itertools.product(
        sorted(SPEC_FIELDS | set(CREDENTIAL_PARAMS)), ("body", "extra_body", "fallbacks")
    ):
        if where == "body":
            yield _chat(**{field: "x"})
        elif where == "extra_body":
            yield _chat(extra_body={field: "x"})
        else:
            yield _chat(fallbacks=[{"model": "m", field: "x"}])


@pytest.mark.parametrize("call_type", [*CHAT_CALL_TYPES, "pass_through_endpoint", None])
def test_chat_and_embeddings_are_decided_as_1_3_0_decides_them(rules, old_rules, call_type):
    checked = 0
    for request in _corpus():
        expected = old_rules.refusal(copy.deepcopy(request), call_type)
        assert rules.refusal(copy.deepcopy(request), call_type) == expected, request
        checked += 1
    assert checked > 200
    assert old_rules.message("GATEWAY_PARAM_REFUSED", "api_key") == rules.message(
        "GATEWAY_PARAM_REFUSED", "api_key"
    )


# --- the hook ------------------------------------------------------------------------------------


def test_the_hook_refuses_a_messages_field_with_a_coded_400_and_no_values(rules, capsys):
    data = _messages(
        mcp_servers=[{"type": "url", "url": "https://x", "authorization_token": MARKER}]
    )
    key = types.SimpleNamespace(key_alias=ALIAS, api_key=MARKER, token=MARKER)
    with pytest.raises(_HTTPException) as caught:
        asyncio.run(rules.handler.async_pre_call_hook(key, None, data, MESSAGES))
    assert caught.value.status_code == 400
    assert caught.value.detail == (
        "GATEWAY_PARAM_REFUSED: the gateway refuses provider-side tools, execution and storage "
        "in Messages requests (mcp_servers)"
    )
    err = capsys.readouterr().err
    assert err == (
        "agentbreeder gateway: refused GATEWAY_PARAM_REFUSED field=mcp_servers in=body "
        f"call=anthropic_messages key_alias={ALIAS}\n"
    )
    assert MARKER not in err and MARKER not in caught.value.detail
    assert asyncio.run(rules.handler.async_pre_call_hook(key, None, _messages(), MESSAGES)) is None


def test_a_routing_field_on_messages_keeps_the_routing_message(rules):
    found = rules.refusal(_messages(api_base="https://x"), MESSAGES)
    assert rules.message(found[0], found[1]) == (
        "GATEWAY_PARAM_REFUSED: the gateway refuses provider-routing parameters in requests "
        "(api_base)"
    )


# --- the version ---------------------------------------------------------------------------------


def test_1_4_0_is_1_3_0_with_a_new_filter():
    assert (V14 / "service.yaml").read_bytes() == (V13 / "service.yaml").read_bytes()
    assert (V14 / "files/start.py").read_bytes() == (V13 / "files/start.py").read_bytes()
    assert sorted(p.name for p in (V14 / "files").iterdir() if p.name != "__pycache__") == [
        "agentbreeder_request_filter.py",
        "start.py",
    ]
    manifest = yaml.safe_load((V14 / "manifest.yaml").read_text())
    assert (manifest["kind"], manifest["name"], manifest["version"]) == (
        "service",
        "litellm",
        "1.4.0",
    )
    new = gateway_providers.builtin_caps("service/litellm@1.4.0")
    old = gateway_providers.builtin_caps("service/litellm@1.3.0")
    assert (new.imports, new.reaches_host) == (old.imports, old.reaches_host)
    assert load_service(V14 / "service.yaml").provides == "gateway/litellm"


# --- the key side (seam for #1089 F7) ------------------------------------------------------------


def test_only_an_anthropic_messages_key_gets_the_messages_route():
    assert gateway_keys.key_routes() == list(gateway_keys.GUARDED_ROUTES)
    assert gateway_keys.key_routes(None, None, "openai-chat") == list(gateway_keys.GUARDED_ROUTES)
    routes = gateway_keys.key_routes(None, None, model_api="anthropic-messages")
    assert routes == [*gateway_keys.GUARDED_ROUTES, "/v1/messages"]
    body = gateway_keys.mint_body(ALIAS, ["m1"], 1.0, 10, {"agentbreeder": True}, routes=routes)
    assert body["allowed_routes"][-1] == "/v1/messages"
    with pytest.raises(gateway_keys.GatewayError) as caught:
        gateway_keys.mint_body(
            ALIAS, ["m1"], 1.0, 10, {"agentbreeder": True}, routes=["/v1/responses"]
        )
    assert caught.value.code == "GATEWAY_KEY_ROUTES"


@pytest.mark.parametrize("call_type", ["acompletion", MESSAGES])
def test_malformed_requests_fail_closed_or_carry_nothing_on_1_4_0(rules, call_type):
    """1.3.0's malformed-request cases, on 1.4.0's filter and on the Messages route."""
    assert rules.refusal(None, call_type)[0] == "GATEWAY_PARAM_REFUSED"
    deep: list = []
    node = deep
    for _ in range(50):
        child: list = []
        node.append({"m": child})
        node = child
    assert rules.refusal({"fallbacks": deep}, call_type) == (
        "GATEWAY_PARAM_REFUSED",
        None,
        "fallbacks",
    )
    assert rules.refusal({"extra_body": "{not json"}, call_type) is None
    assert rules.refusal({"extra_body": '["custom_llm_provider"]'}, call_type) is None
    deep_json = "{" + '"a":[' * 5000 + "]" * 5000 + "}"
    assert rules.refusal({"extra_body": deep_json}, call_type) is None  # no crash


def test_non_object_content_blocks_carry_nothing(rules):
    data = _messages(messages=[{"role": "user", "content": ["x", 7, None]}], system=["x"])
    assert rules.refusal(data, MESSAGES) is None
