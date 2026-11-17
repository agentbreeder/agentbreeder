"""#1105: `service/litellm@1.3.0` refuses provider-routing parameters in requests.

The filter (files/agentbreeder_request_filter.py) runs inside the pinned image as LiteLLM's
pre-call hook; here it is loaded with stand-ins for `fastapi` and LiteLLM's `CustomLogger`.
The pinned-image half (two providers, one key per alias) is
tests/test_gateway_request_filter_docker.py.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import stat
import sys
import types

import pytest
import yaml
from conftest import GATEWAY_CAPS

from agentbreeder import gateway_keys, gateway_providers, registry
from agentbreeder.services.schema import load_service

V12 = registry.BUILTIN_ROOT / "service/litellm/1.2.0"
V13 = registry.BUILTIN_ROOT / "service/litellm/1.3.0"
ALIAS = "agentbreeder-" + "a" * 32
MARKER = "-".join(["marker", "z" * 12])  # stands in for key material: must never print

# The parameters spec §4.2 (more-providers) lists for the client-side test, plus the routing
# fields #1105 found. Header-only names (`x-litellm-*`) are not body fields.
SPEC_FIELDS = {
    "api_base",
    "base_url",
    "aws_bedrock_runtime_endpoint",
    "aws_sts_endpoint",
    "aws_region_name",
    "aws_role_name",
    "aws_web_identity_token",
    "vertex_credentials",
    "vertex_project",
    "vertex_location",
    "vertex_ai_project",
    "vertex_ai_location",
    "vertex_ai_credentials",
    "azure_ad_token",
    "azure_ad_token_provider",
    "api_version",
    "api_key",
    "organization",
    "aws_session_name",
    "litellm_credential_name",
    "model_id",
    "extra_headers",
    "custom_llm_provider",
    "user_config",
}


class _HTTPException(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _load(name, path, monkeypatch):
    fastapi = types.ModuleType("fastapi")
    fastapi.HTTPException = _HTTPException
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = type("CustomLogger", (), {})
    for module_name, module in (
        ("fastapi", fastapi),
        ("litellm", types.ModuleType("litellm")),
        ("litellm.integrations", types.ModuleType("litellm.integrations")),
        ("litellm.integrations.custom_logger", custom_logger),
    ):
        monkeypatch.setitem(sys.modules, module_name, module)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rules(monkeypatch):
    return _load(
        "agentbreeder_request_filter", V13 / "files/agentbreeder_request_filter.py", monkeypatch
    )


@pytest.fixture
def start():
    spec = importlib.util.spec_from_file_location("litellm_start_130", V13 / "files/start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _chat(**extra):
    """A request as LiteLLM 1.106.2's pre-call hook sees it: the caller's fields, the proxy's
    metadata (request headers, key fields, `requester_metadata`) and a body snapshot that is a
    shallow copy of the request, so it shares that metadata."""
    data = {"model": "m1", "messages": [{"role": "user", "content": "hi"}], **extra}
    data["metadata"] = {
        **(data.get("metadata") or {}),
        "headers": {"user-agent": "x", "authorization": "redacted"},
        "user_api_key_alias": ALIAS,
        "requester_metadata": dict(data.get("metadata") or {}),
    }
    data["proxy_server_request"] = {"url": "http://gw/v1/chat/completions", "body": dict(data)}
    return data


# --- the filter ----------------------------------------------------------------------------------


def test_the_filter_covers_the_spec_list_and_the_routing_fields(rules):
    assert SPEC_FIELDS <= rules.ROUTING_FIELDS
    assert rules.ALLOWED_CALL_TYPES == {"acompletion", "completion", "aembedding", "embeddings"}


@pytest.mark.parametrize("call_type", ["acompletion", "completion", "aembedding", "embeddings"])
def test_a_plain_request_passes(rules, call_type):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                # a tool's own parameter names are the caller's, not LiteLLM's
                "parameters": {"properties": {"organization": {}, "api_key": {}}},
            },
        }
    ]
    data = _chat(stream=True, temperature=0.2, tools=tools, reasoning_effort="low", user="u")
    assert rules.refusal(data, call_type) is None
    # Caller metadata is not scanned: tags (sub-agent attribution, #1058 S0) and tracing ids.
    meta = {"tags": ["agentbreeder-subagent:billing"], "tenant_id": "t", "trace_id": "x"}
    assert rules.refusal(_chat(metadata=meta), call_type) is None
    assert rules.refusal(_chat(extra_body={"metadata": meta}), call_type) is None
    # The runtime's embedding body (runtime/adk rag.py) and the judge's strict json_schema.
    embed = {"model": "e", "input": ["x"], "dimensions": 768, "task_type": "RETRIEVAL_QUERY"}
    assert rules.refusal({**embed, "title": "t"}, call_type) is None
    judge = {"type": "json_schema", "json_schema": {"name": "grade", "strict": True}}
    assert rules.refusal(_chat(response_format=judge, temperature=0), call_type) is None
    assert rules.refusal({"model": "embed", "input": "x"}, call_type) is None


@pytest.mark.parametrize("field", sorted(SPEC_FIELDS | {"specific_deployment", "headers"}))
@pytest.mark.parametrize(
    "where",
    [
        "body",
        "extra_body",
        "extra_body_string",
        "litellm_embedding_config",
        "fallbacks",
        "nested_fallbacks",
        "context_window_fallbacks",
        "router_settings_override",
    ],
)
def test_a_routing_field_is_refused_wherever_it_is_carried(rules, field, where):
    value = "openai"
    extra = {}
    if where == "body":  # the caller's body, or derived by the proxy from a query parameter
        extra[field] = value
    elif where == "extra_body_string":
        extra["extra_body"] = json.dumps({field: value})
    elif where == "fallbacks":
        extra["fallbacks"] = [{"model": "m1", field: value}]
    elif where == "nested_fallbacks":
        extra["fallbacks"] = [{"m1": [{"model": "m1", field: value}]}]
    elif where == "context_window_fallbacks":
        extra["context_window_fallbacks"] = [{"model": "m1", field: value}]
    elif where == "router_settings_override":
        extra["router_settings_override"] = {"fallbacks": [{"model": "m1", field: value}]}
    else:
        extra[where] = {field: value}
    code, found, _ = rules.refusal(_chat(**extra), "acompletion")
    assert (code, found) == ("GATEWAY_PARAM_REFUSED", field)


def test_litellm_params_is_refused_by_name(rules):
    found = rules.refusal(_chat(litellm_params={"metadata": {}}), "aembedding")
    assert found == ("GATEWAY_PARAM_REFUSED", "litellm_params", "body")


@pytest.mark.parametrize(
    "call_type",
    [
        "pass_through_endpoint",
        "aresponses",
        "anthropic_messages",
        "acreate_file",
        "acreate_batch",
        "_arealtime",
        "aimage_generation",
        "amoderation",
        "atext_completion",
        "call_mcp_tool",
        None,
    ],
)
def test_other_call_types_are_refused(rules, call_type):
    assert rules.refusal(_chat(), call_type) == ("GATEWAY_ROUTE_REFUSED", None, "route")


def test_malformed_requests_fail_closed_or_carry_nothing(rules):
    assert rules.refusal(None, "acompletion")[0] == "GATEWAY_PARAM_REFUSED"

    def nested(levels):
        deep = []
        node = deep
        for _ in range(levels):
            child = []
            node.append({"m": child})
            node = child
        return deep

    # LiteLLM accepts up to 10 levels; deeper than the filter reads is refused, not skipped.
    assert rules.refusal({"fallbacks": nested(10)}, "acompletion") is None
    assert rules.refusal({"fallbacks": nested(50)}, "acompletion") == (
        "GATEWAY_PARAM_REFUSED",
        None,
        "fallbacks",
    )
    assert rules.refusal({"extra_body": "{not json"}, "acompletion") is None
    assert rules.refusal({"extra_body": '["custom_llm_provider"]'}, "acompletion") is None
    deep_json = "{" + '"a":[' * 5000 + "]" * 5000 + "}"
    assert rules.refusal({"extra_body": deep_json}, "acompletion") is None  # no crash


CREDENTIAL_PARAMS = (  # LiteLLM 1.106.2 CredentialLiteLLMParams (litellm/types/router.py)
    "api_key api_base api_version azure_ad_token tenant_id client_id client_secret azure_scope "
    "azure_username azure_password vertex_project vertex_location vertex_credentials region_name "
    "gcs_bucket_name bucket_name aws_access_key_id aws_secret_access_key aws_session_token "
    "aws_region_name aws_session_name aws_profile_name aws_role_name aws_web_identity_token "
    "aws_sts_endpoint aws_external_id aws_session_tags aws_bedrock_runtime_endpoint "
    "aws_bedrock_project_id s3_bucket_name s3_endpoint_url s3_region_name s3_access_key_id "
    "s3_secret_access_key s3_encryption_key_id s3_bucket_owner aws_batch_role_arn "
    "s3_output_bucket_name"
).split()


def test_the_filter_covers_litellms_credential_params(rules):
    assert set(CREDENTIAL_PARAMS) <= rules.ROUTING_FIELDS


def test_provider_specific_credentials_are_refused(rules):
    for field in ("gigachat_access_token", "gigachat_auth_url", "gigachat_scope", "ssl_verify"):
        assert rules.refusal(_chat(**{field: "x"}), "acompletion")[1] == field


def test_the_hook_raises_a_coded_400_and_logs_without_values(rules, capsys):
    data = _chat(custom_llm_provider="openai", api_key=MARKER)
    key = types.SimpleNamespace(key_alias=ALIAS, api_key=MARKER, token=MARKER)
    with pytest.raises(_HTTPException) as caught:
        asyncio.run(rules.handler.async_pre_call_hook(key, None, data, "acompletion"))
    assert caught.value.status_code == 400
    assert caught.value.detail.startswith("GATEWAY_PARAM_REFUSED: ")
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "refused GATEWAY_PARAM_REFUSED field=" in err and f"key_alias={ALIAS}" in err
    assert MARKER not in err and "openai" not in err and MARKER not in caught.value.detail
    passed = asyncio.run(rules.handler.async_pre_call_hook(key, None, _chat(), "acompletion"))
    assert passed is None


def test_the_log_line_prints_only_known_shapes(rules):
    line = rules.log_line("GATEWAY_ROUTE_REFUSED", None, "route", "pass_through_endpoint", "x y")
    assert line == (
        "agentbreeder gateway: refused GATEWAY_ROUTE_REFUSED field=- in=route "
        "call=pass_through_endpoint key_alias=other"
    )
    line = rules.log_line("GATEWAY_PARAM_REFUSED", "Evil\nField", "extra_body.metadata", 7, None)
    assert "\n" not in line and "Evil" not in line
    assert "in=extra_body_metadata call=other key_alias=other" in line
    assert rules.message("GATEWAY_ROUTE_REFUSED", None).startswith("GATEWAY_ROUTE_REFUSED: ")


# --- the 1.3.0 launcher --------------------------------------------------------------------------


def test_the_launcher_lists_match_service_yaml(start):
    service = yaml.safe_load((V13 / "service.yaml").read_text())
    assert set(start.SERVICE_ENV) == set(service["env"]) | {"AGENTBREEDER_SERVICE"}
    assert {k: service["env"][k] for k in start.FORCED} == start.FORCED
    assert start.FORCED["PYTHONPATH"] == "/files"
    assert set(start.GATEWAY_ONLY) == gateway_providers.GATEWAY_ONLY
    assert start.FILTER == "agentbreeder_request_filter.handler"
    assert (V13 / "files/agentbreeder_request_filter.py").is_file()


def test_1_3_0_is_1_2_0_plus_the_filter():
    old = yaml.safe_load((V12 / "service.yaml").read_text())
    new = yaml.safe_load((V13 / "service.yaml").read_text())
    assert new["env"].pop("PYTHONPATH") == "/files"
    assert old == new
    new_caps = gateway_providers.builtin_caps("service/litellm@1.3.0")
    old_caps = gateway_providers.builtin_caps("service/litellm@1.2.0")
    assert (new_caps.imports, new_caps.reaches_host) == (old_caps.imports, old_caps.reaches_host)
    assert load_service(V13 / "service.yaml").reaches_host == [11434]


def test_the_launcher_replaces_an_ambient_pythonpath(start):
    environment = start.environment_for({"PYTHONPATH": "/evil", "PATH": "/bin"}, {})
    assert environment["PYTHONPATH"] == "/files"


def test_with_filter_adds_only_the_callback(start):
    document = {"model_list": [], "litellm_settings": {"drop_params": True}}
    filtered = start.with_filter(document)
    assert filtered == {
        "model_list": [],
        "litellm_settings": {"drop_params": True, "callbacks": [start.FILTER]},
    }
    assert document == {"model_list": [], "litellm_settings": {"drop_params": True}}
    assert start.with_filter({"model_list": []})["litellm_settings"] == {
        "callbacks": [start.FILTER]
    }


@pytest.mark.parametrize("settings", [{"callbacks": ["x.y"]}, {"callbacks": []}, ["callbacks"]])
def test_with_filter_refuses_a_config_naming_callbacks(start, settings):
    with pytest.raises(SystemExit) as caught:
        start.with_filter({"model_list": [], "litellm_settings": settings})
    assert "callbacks" in str(caught.value)


def test_write_config_is_private_and_new(start, tmp_path):
    first = start.write_config({"a": 1}, run_dir=str(tmp_path))
    second = start.write_config({"a": 1}, run_dir=str(tmp_path))
    assert first != second
    with open(first) as handle:
        assert yaml.safe_load(handle) == {"a": 1}
    assert stat.S_IMODE(os.stat(first).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(first)).st_mode) == 0o700


def test_write_config_fails_closed(start, tmp_path):
    with pytest.raises(SystemExit) as caught:
        start.write_config({"a": 1}, run_dir=str(tmp_path / "missing"))
    assert str(caught.value) == "litellm launcher: the filtered gateway config can't be written"


def test_the_launcher_execs_litellm_on_the_filtered_config(start, tmp_path, monkeypatch):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "master-key").write_text("mk-1\n")
    (secrets / "database-url").write_text("postgresql://x\n")
    (secrets / "gemini-api-key").write_text(MARKER + "\n")
    config = tmp_path / "config"
    reviewed = {
        "model_list": [
            {
                "model_name": "m1",
                "litellm_params": {"model": "gemini/x", "api_key": "os.environ/GEMINI_API_KEY"},
            }
        ]
    }
    config.write_text(yaml.safe_dump(reviewed))
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(start, "SECRETS", str(secrets))
    monkeypatch.setattr(start, "CONFIG", str(config))
    monkeypatch.setattr(start.load_config, "__defaults__", (str(config),))
    monkeypatch.setattr(start.write_config, "__defaults__", (str(run),))
    calls = []
    monkeypatch.setattr(start.os, "execve", lambda *args: calls.append(args))
    monkeypatch.setattr(start.os, "environ", {"PATH": "/bin", "PYTHONPATH": "/evil"})
    start.main()
    ((binary, argv, environment),) = calls
    assert binary == "/app/.venv/bin/litellm"
    assert argv[1] == "--config" and argv[3:] == ["--port", "4000"]
    assert argv[2].startswith(str(run)) and argv[2] != str(config)
    with open(argv[2]) as handle:
        text = handle.read()
    assert yaml.safe_load(text) == {**reviewed, "litellm_settings": {"callbacks": [start.FILTER]}}
    assert MARKER not in text  # the copy holds references, never values
    assert environment["PYTHONPATH"] == "/files" and environment["GEMINI_API_KEY"] == MARKER


def test_the_review_still_refuses_callbacks_in_a_stack_config(tmp_path):
    path = tmp_path / "litellm.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "model_list": [{"model_name": "m1", "litellm_params": {"model": "gemini/x"}}],
                "litellm_settings": {"callbacks": ["agentbreeder_request_filter.handler"]},
            }
        )
    )
    with pytest.raises(gateway_keys.GatewayError) as caught:
        gateway_keys.config_review(path, GATEWAY_CAPS)
    assert caught.value.code == "GATEWAY_CONFIG_UNSAFE"


@pytest.mark.parametrize(
    "key",
    [
        "forward_client_headers_to_llm_api",
        "forward_llm_provider_auth_headers",
        "forward_openai_org_id",
        "allow_client_side_credentials",
    ],
)
def test_the_review_refuses_settings_that_write_routing_fields(tmp_path, key):
    """With these on, the proxy itself would put `headers`, `api_key` or `organization` into a
    request, which the filter would refuse; the review keeps them off."""
    path = tmp_path / "litellm.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "model_list": [{"model_name": "m1", "litellm_params": {"model": "gemini/x"}}],
                "general_settings": {key: True},
            }
        )
    )
    with pytest.raises(gateway_keys.GatewayError) as caught:
        gateway_keys.config_review(path, GATEWAY_CAPS)
    assert caught.value.code == "GATEWAY_CONFIG_UNSAFE"


def test_every_key_gets_the_chat_and_embeddings_routes():
    assert gateway_keys.key_routes() == list(gateway_keys.GUARDED_ROUTES)
    assert gateway_keys.key_routes(None, None) == gateway_keys.key_routes(None, ["g"])
    body = gateway_keys.mint_body(
        ALIAS, ["m1"], 1.0, 10, {"agentbreeder": True}, routes=gateway_keys.key_routes()
    )
    assert body["allowed_routes"] == [
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/embeddings",
        "/embeddings",
    ]
