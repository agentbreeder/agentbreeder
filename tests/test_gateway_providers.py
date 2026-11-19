"""#1025 slice P1: the provider table, model patterns, `model_info` allow-list, `GatewayCaps`,
`required_env`/`routes`, and the gateway-only import names.

When P1 shipped no `service/litellm` version declared the new providers' imports (each provider
slice adds its own), so these tests review configs for a fixture gateway (`FULL`) that does,
and check that the built-in 1.0.0 refuses every new provider. The Vertex rules are re-run on
the real built-in 1.4.0 in tests/test_vertex_gateway.py (#1025 V1).
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import GATEWAY_CAPS

from agentbreeder import env_import, gateway_keys, gateway_providers, registry
from agentbreeder.gateway_keys import GatewayError
from agentbreeder.gateway_providers import GATEWAY_ONLY, GatewayCaps
from agentbreeder.lock import BUILTIN_LABEL
from agentbreeder.services.lock import check_gateway_only, reads_gateway_only
from agentbreeder.services.schema import ServiceError, ServiceSpec

FULL = GatewayCaps(
    "service/litellm@9.9.9",
    frozenset(GATEWAY_CAPS.imports | GATEWAY_ONLY),
    frozenset({11434}),
)

OLLAMA = {"model": "ollama_chat/llama3.2:3b", "api_base": "http://host.docker.internal:11434"}
VERTEX = {
    "model": "vertex_ai/gemini-2.5-flash",
    "vertex_credentials": "os.environ/VERTEX_SERVICE_ACCOUNT_JSON",
    "vertex_project": "my-project-1",
    "vertex_location": "us-central1",
}
BEDROCK = {
    "model": "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
    "aws_access_key_id": "os.environ/BEDROCK_ACCESS_KEY_ID",
    "aws_secret_access_key": "os.environ/BEDROCK_SECRET_ACCESS_KEY",
    "aws_region_name": "us-east-1",
}
AZURE = {
    "model": "azure/my-gpt-deployment",
    "api_key": "os.environ/AZURE_OPENAI_API_KEY",
    "api_base": "os.environ/AZURE_OPENAI_ENDPOINT",
    "api_version": "2024-10-21",
}
AZURE_INFO = {"base_model": "azure/gpt-4o-mini"}
ENTRIES = {"ollama": OLLAMA, "vertex": VERTEX, "bedrock": BEDROCK, "azure": AZURE}


def entry(params, info=None, name="m"):
    item = {"model_name": name, "litellm_params": dict(params)}
    if info is not None:
        item["model_info"] = dict(info)
    return item


def config(tmp_path, *entries, **sections):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"model_list": list(entries), **sections}))
    return path


def review(tmp_path, *entries, caps=FULL):
    return gateway_keys.config_review(config(tmp_path, *entries), caps)


def refused(tmp_path, *entries, caps=FULL, code="GATEWAY_CONFIG_UNSAFE") -> str:
    with pytest.raises(GatewayError) as caught:
        review(tmp_path, *entries, caps=caps)
    assert caught.value.code == code
    return str(caught.value)


def provider_entry(provider):
    return entry(ENTRIES[provider], AZURE_INFO if provider == "azure" else None)


# --- the table ---------------------------------------------------------------------------------


def test_each_new_provider_passes_for_a_gateway_that_supports_it(tmp_path):
    result = review(
        tmp_path,
        entry(OLLAMA, name="local"),
        entry(VERTEX, name="vx"),
        entry(BEDROCK, name="br"),
        entry(AZURE, AZURE_INFO, name="az"),
        entry({"model": "gemini/x", "api_key": "os.environ/GEMINI_API_KEY"}, name="gm"),
    )
    assert result.providers == {
        "local": "ollama (no key → host port 11434)",
        "vx": "vertex (VERTEX_SERVICE_ACCOUNT_JSON, project my-project-1, us-central1)",
        "br": "bedrock (BEDROCK_ACCESS_KEY_ID + BEDROCK_SECRET_ACCESS_KEY, us-east-1)",
        "az": "azure (AZURE_OPENAI_API_KEY → endpoint from AZURE_OPENAI_ENDPOINT, 2024-10-21, "
        "priced as azure/gpt-4o-mini)",
        "gm": "gemini (GEMINI_API_KEY)",
    }
    assert result.required_env == {
        "local": (),
        "vx": ("VERTEX_SERVICE_ACCOUNT_JSON",),
        "br": ("BEDROCK_ACCESS_KEY_ID", "BEDROCK_SECRET_ACCESS_KEY"),
        "az": ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
        "gm": ("GEMINI_API_KEY",),
    }
    assert result.routes["vx"] == {
        "provider": "vertex",
        "project": "my-project-1",
        "location": "us-central1",
    }
    assert result.routes["br"] == {"provider": "bedrock", "region": "us-east-1"}
    assert result.routes["local"] == {"provider": "ollama", "host_port": 11434}
    assert result.routes["az"]["base_model"] == "azure/gpt-4o-mini"
    # The grant shows routes for the new providers only (key-only labels say everything).
    assert set(result.grant_routes()) == {"local", "vx", "br", "az"}


def test_key_only_providers_keep_their_review_and_add_no_grant_routes(tmp_path):
    result = review(
        tmp_path,
        entry({"model": "openai/gpt-5", "api_key": "os.environ/OPENAI_API_KEY"}, name="a"),
        entry({"model": "openrouter/google/gemini-3.8-flash"}, name="b"),
        caps=GATEWAY_CAPS,
    )
    assert result.providers == {"a": "openai (OPENAI_API_KEY)", "b": "openrouter (no key)"}
    assert result.required_env == {"a": ("OPENAI_API_KEY",), "b": ()}
    assert result.grant_routes() == {}


def test_the_builtin_1_0_0_gateway_has_exactly_the_key_only_imports():
    caps = gateway_providers.builtin_caps("service/litellm@1.0.0")
    assert caps == GATEWAY_CAPS
    assert gateway_providers.builtin_caps("service/litellm@0.0.1") is None
    assert gateway_providers.builtin_caps("service/postgres-pgvector@1.0.0") is None
    assert gateway_providers.builtin_caps("not a ref") is None


@pytest.mark.parametrize("provider", sorted(ENTRIES))
def test_todays_builtin_gateway_refuses_every_new_provider(tmp_path, provider):
    message = refused(
        tmp_path, provider_entry(provider), caps=GATEWAY_CAPS, code="GATEWAY_PROVIDER_UNSUPPORTED"
    )
    assert f"{provider} needs a gateway that can" in message
    assert "service/litellm@1.0.0 cannot" in message


def test_a_gateway_missing_one_credential_import_is_refused(tmp_path):
    caps = GatewayCaps("service/x@1.0.0", FULL.imports - {"BEDROCK_SECRET_ACCESS_KEY"})
    message = refused(tmp_path, entry(BEDROCK), caps=caps, code="GATEWAY_PROVIDER_UNSUPPORTED")
    assert "import BEDROCK_SECRET_ACCESS_KEY" in message


def test_ollama_needs_a_gateway_that_reaches_the_host_port(tmp_path):
    caps = GatewayCaps("service/x@1.0.0", FULL.imports)
    message = refused(tmp_path, entry(OLLAMA), caps=caps, code="GATEWAY_PROVIDER_UNSUPPORTED")
    assert "reach host port 11434" in message


@pytest.mark.parametrize("caps", [None, "service/litellm@1.0.0", frozenset()])
def test_config_review_needs_gateway_caps(tmp_path, caps):
    path = config(tmp_path, entry({"model": "gemini/x"}))
    with pytest.raises(TypeError):
        gateway_keys.config_review(path, caps)
    with pytest.raises(TypeError):
        gateway_keys.config_review(path)  # no default: an omitted gateway fails closed


# --- model patterns (§5.1) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider,model",
    [
        ("bedrock", "bedrock/arn:aws:bedrock:us-east-1:123:inference-profile/x"),
        ("bedrock", "bedrock/converse/anthropic.claude-3"),
        ("bedrock", "bedrock/invoke/anthropic.claude-3"),
        ("bedrock", "bedrock/imported/x"),
        ("bedrock", "bedrock/Anthropic.Claude"),
        ("bedrock", "bedrock/"),
        ("vertex", "vertex_ai/openai/gpt-4o"),
        ("vertex", "vertex_ai/1234567890"),
        ("vertex", "vertex_ai/claude-3-5-sonnet@20240620"),
        ("vertex", "vertex_ai/gemini-2.5-flash@x"),
        ("vertex", "vertex_ai/Gemini-2.5-flash"),
        ("vertex", "vertex_ai/llama-3"),
        ("azure", "azure/a/b"),
        ("azure", "azure/" + "a" * 65),
        ("azure", "azure/a b"),
        ("ollama", "ollama/a/b"),
        ("ollama", "ollama_chat/"),
        ("ollama", "Ollama/llama3"),
    ],
)
def test_model_pattern_bypasses_are_refused(tmp_path, provider, model):
    params = {**ENTRIES[provider], "model": model}
    message = refused(tmp_path, entry(params, AZURE_INFO if provider == "azure" else None))
    assert "litellm_params.model" in message
    rest = model.partition("/")[2]
    assert not rest or rest not in message.split("PROVIDER one of")[0]


@pytest.mark.parametrize(
    "model",
    [
        "vertex_ai/gemini-embedding-001",
        "vertex_ai/text-embedding-005",
        "vertex_ai/text-multilingual-embedding-002",
    ],
)
def test_vertex_embedding_families_are_accepted(tmp_path, model):
    assert review(tmp_path, entry({**VERTEX, "model": model})).aliases == {"m"}


def test_bedrock_inference_profile_ids_and_ollama_embeddings_are_accepted(tmp_path):
    review(tmp_path, entry({**BEDROCK, "model": "bedrock/us.anthropic.claude-3-haiku-v1:0"}))
    review(tmp_path, entry({**OLLAMA, "model": "ollama/nomic-embed-text"}))


# --- endpoints and credentials (§4.1, §3) ------------------------------------------------------


@pytest.mark.parametrize(
    "api_base",
    [
        "http://host.docker.internal:11434/",
        "https://host.docker.internal:11434",
        "http://host.docker.internal:11435",
        "http://host.docker.internal",
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://169.254.169.254:11434",
        "http://user@host.docker.internal:11434",
        "http://host.docker.internal:11434/api",
        "http://host.docker.internal:11434?x=1",
        "http://host.docker.internal.:11434",
        "http://HOST.docker.internal:11434",
        "http://host.docker.internal:11434\n",
        "os.environ/OLLAMA_HOST",
        11434,
    ],
)
def test_ollama_api_base_is_exactly_the_host_gateway(tmp_path, api_base):
    message = refused(tmp_path, entry({**OLLAMA, "api_base": api_base}))
    assert "api_base" in message
    assert "169.254" not in message and "localhost" not in message


@pytest.mark.parametrize(
    "provider,param",
    [
        ("ollama", "base_url"),
        ("ollama", "api_key"),
        ("vertex", "api_base"),
        ("vertex", "vertex_ai_project"),
        ("vertex", "vertex_ai_location"),
        ("vertex", "vertex_ai_credentials"),
        ("vertex", "api_key"),
        ("vertex", "extra_headers"),
        ("bedrock", "aws_session_token"),
        ("bedrock", "aws_role_name"),
        ("bedrock", "aws_session_name"),
        ("bedrock", "aws_web_identity_token"),
        ("bedrock", "aws_sts_endpoint"),
        ("bedrock", "aws_profile_name"),
        ("bedrock", "aws_bedrock_runtime_endpoint"),
        ("bedrock", "api_base"),
        ("azure", "azure_ad_token"),
        ("azure", "tenant_id"),
        ("azure", "client_id"),
        ("azure", "client_secret"),
        ("azure", "azure_username"),
        ("azure", "azure_password"),
        ("azure", "api_host"),
        ("gemini", "api_base"),
        ("gemini", "vertex_project"),
    ],
)
def test_endpoint_session_and_identity_params_are_refused(tmp_path, provider, param):
    params = ENTRIES.get(provider, {"model": "gemini/x"})
    message = refused(
        tmp_path,
        entry({**params, param: "x.example"}, AZURE_INFO if provider == "azure" else None),
    )
    assert "litellm_params key" in message
    assert "x.example" not in message


@pytest.mark.parametrize(
    "provider,param,value",
    [
        ("vertex", "vertex_credentials", "os.environ/GEMINI_API_KEY"),
        ("vertex", "vertex_credentials", "/var/run/key.json"),
        ("vertex", "vertex_credentials", "os.environ/GOOGLE_APPLICATION_CREDENTIALS"),
        ("bedrock", "aws_access_key_id", "os.environ/BEDROCK_SECRET_ACCESS_KEY"),
        ("bedrock", "aws_secret_access_key", "os.environ/AWS_SECRET_ACCESS_KEY"),
        ("bedrock", "aws_access_key_id", "AKIAEXAMPLE"),
        ("azure", "api_base", "https://attacker.openai.azure.com"),
        ("azure", "api_key", "os.environ/OPENAI_API_KEY"),
        ("gemini", "api_key", "os.environ/VERTEX_SERVICE_ACCOUNT_JSON"),
    ],
)
def test_credential_params_name_only_their_own_import(tmp_path, provider, param, value):
    params = ENTRIES.get(provider, {"model": "gemini/x"})
    message = refused(
        tmp_path,
        entry({**params, param: value}, AZURE_INFO if provider == "azure" else None),
    )
    assert param in message
    assert "attacker" not in message and "AKIA" not in message and "/var/run" not in message


@pytest.mark.parametrize(
    "provider,param",
    [
        ("vertex", "vertex_credentials"),
        ("vertex", "vertex_project"),
        ("vertex", "vertex_location"),
        ("bedrock", "aws_access_key_id"),
        ("bedrock", "aws_secret_access_key"),
        ("bedrock", "aws_region_name"),
        ("azure", "api_key"),
        ("azure", "api_base"),
        ("azure", "api_version"),
        ("ollama", "api_base"),
    ],
)
def test_required_params_are_required(tmp_path, provider, param):
    params = {k: v for k, v in ENTRIES[provider].items() if k != param}
    message = refused(tmp_path, entry(params, AZURE_INFO if provider == "azure" else None))
    assert f"litellm_params.{param} is required for {provider}" in message
    assert "is not allowed" not in message


# --- printed fields (§4.1): a pattern each, str only, no control characters ---------------------

BAD_TEXT = ["\x1b[2K", "a\rb", "a\nb", "proj\n", "café", "", " ", 7, 1.5, True, None]


@pytest.mark.parametrize(
    "provider,param,good",
    [
        ("vertex", "vertex_project", "my-project-1"),
        ("vertex", "vertex_location", "europe-west4"),
        ("bedrock", "aws_region_name", "us-gov-west-1"),
        ("azure", "api_version", "2025-01-01-preview"),
    ],
)
@pytest.mark.parametrize("bad", BAD_TEXT)
def test_printed_params_refuse_control_characters_and_non_strings(
    tmp_path, provider, param, good, bad
):
    info = AZURE_INFO if provider == "azure" else None
    review(tmp_path, entry({**ENTRIES[provider], param: good}, info))
    for value in {bad, f"{good}{bad}"} if isinstance(bad, str) and bad else [bad]:
        message = refused(tmp_path, entry({**ENTRIES[provider], param: value}, info))
        assert param in message
        assert "\x1b" not in message and "\r" not in message and "caf" not in message


@pytest.mark.parametrize(
    "value",
    ["global", "us-central1", "asia-northeast3"],
)
def test_vertex_locations(tmp_path, value):
    review(tmp_path, entry({**VERTEX, "vertex_location": value}))


@pytest.mark.parametrize(
    "value",
    ["Global", "us-central", "us_central1", "us-central123", "metadata.google.internal"],
)
def test_vertex_locations_refused(tmp_path, value):
    refused(tmp_path, entry({**VERTEX, "vertex_location": value}))


@pytest.mark.parametrize("value", ["a", "A-project", "my-project-", "x" * 31, "my_project1"])
def test_vertex_projects_refused(tmp_path, value):
    refused(tmp_path, entry({**VERTEX, "vertex_project": value}))


@pytest.mark.parametrize("bad", [*BAD_TEXT, "azure/a/b", "openai/gpt-4o", "azure/"])
def test_azure_base_model_is_required_and_printed_safely(tmp_path, bad):
    message = refused(tmp_path, entry(AZURE, {"base_model": bad}))
    assert "model_info.base_model" in message
    message = refused(tmp_path, entry(AZURE))
    assert "model_info.base_model is required for azure" in message


@pytest.mark.parametrize("value", ["5m", "0s", "24h", "9999s", -1])
def test_ollama_keep_alive_accepts(tmp_path, value):
    review(tmp_path, entry({**OLLAMA, "keep_alive": value}))


@pytest.mark.parametrize("value", ["5", "5d", "10000s", "-1", -2, 0, True, "5m\n", 1.5])
def test_ollama_keep_alive_refuses(tmp_path, value):
    message = refused(tmp_path, entry({**OLLAMA, "keep_alive": value}))
    assert "keep_alive" in message


def test_keep_alive_is_ollama_only(tmp_path):
    refused(tmp_path, entry({"model": "gemini/x", "keep_alive": "5m"}))


# --- model_info allow-list (D13) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "info",
    [
        {"input_cost_per_token": 0.0},
        {"output_cost_per_token": 0.0},
        {"id": "x"},
        {"access_groups": "x"},
        {"base_model": "azure/gpt-4o"},  # Azure only
        {"mode": "image_generation"},
        {"mode": 1},
        {"max_tokens": 10},
    ],
)
def test_model_info_keys_outside_the_allow_list_are_refused(tmp_path, info):
    message = refused(tmp_path, entry({"model": "gemini/x"}, info), caps=GATEWAY_CAPS)
    assert "model_info" in message


@pytest.mark.parametrize("mode", ["chat", "embedding"])
def test_model_info_mode_is_allowed_everywhere(tmp_path, mode):
    review(tmp_path, entry({"model": "gemini/x"}, {"mode": mode}), caps=GATEWAY_CAPS)
    review(tmp_path, entry(AZURE, {**AZURE_INFO, "mode": mode}))


def test_azure_refuses_price_keys_too(tmp_path):
    refused(tmp_path, entry(AZURE, {**AZURE_INFO, "input_cost_per_token": 0}))


# --- aliases and set_verbose -------------------------------------------------------------------


def test_an_alias_cannot_hide_a_second_new_provider_route(tmp_path):
    message = refused(tmp_path, entry(VERTEX, name="chat"), entry(BEDROCK, name="chat"))
    assert "model_list[1].model_name (repeats an alias with another route)" in message
    refused(tmp_path, entry({"model": "gemini/x"}, name="chat"), entry(OLLAMA, name="chat"))
    refused(tmp_path, entry(OLLAMA, name="chat"), entry({"model": "gemini/x"}, name="chat"))


def test_an_alias_may_repeat_the_same_route(tmp_path):
    result = review(tmp_path, entry(VERTEX, name="chat"), entry(VERTEX, name="chat"))
    assert result.required_env == {"chat": ("VERTEX_SERVICE_ACCOUNT_JSON",)}


def test_key_only_aliases_keep_todays_repeat_rule(tmp_path):
    # Unchanged from before #1025 (the grant shows the last line); not widened here.
    result = review(
        tmp_path,
        entry({"model": "gemini/x", "api_key": "os.environ/GEMINI_API_KEY"}, name="chat"),
        entry({"model": "openai/x", "api_key": "os.environ/OPENAI_API_KEY"}, name="chat"),
        caps=GATEWAY_CAPS,
    )
    assert result.required_env == {"chat": ("GEMINI_API_KEY", "OPENAI_API_KEY")}


def test_set_verbose_stays_refused_with_gateway_only_credentials(tmp_path):
    path = config(tmp_path, entry(VERTEX), litellm_settings={"set_verbose": True})
    with pytest.raises(GatewayError) as caught:
        gateway_keys.config_review(path, FULL)
    assert caught.value.code == "GATEWAY_CONFIG_UNSAFE"
    assert "set_verbose must be false" in str(caught.value)


def test_client_side_credential_settings_stay_outside_the_allow_list(tmp_path):
    for key in ("allow_client_side_credentials", "configurable_clientside_auth_params"):
        for section in ("litellm_settings", "general_settings", "router_settings"):
            path = config(tmp_path, entry(VERTEX), **{section: {key: True}})
            with pytest.raises(GatewayError):
                gateway_keys.config_review(path, FULL)


def test_patterns_end_in_backslash_z():
    with pytest.raises(ValueError):
        gateway_providers._pattern(r"[a-z]+$")
    for provider in gateway_providers.PROVIDERS.values():
        assert provider.model.pattern.endswith(r"\Z")


def test_the_azure_endpoint_pattern_for_a1():
    pattern = gateway_providers.AZURE_ENDPOINT
    assert pattern.fullmatch("https://my-resource.openai.azure.com")
    assert pattern.fullmatch("https://my-resource.openai.azure.com/")
    for bad in (
        "https://xn--e1a.openai.azure.com",
        "https://a--b.openai.azure.com",
        "https://evil.com/.openai.azure.com",
        "https://my-resource.openai.azure.com.evil.com",
        "https://my-resource.openai.azure.com/\n",
        "http://my-resource.openai.azure.com",
        "https://u@my-resource.openai.azure.com",
        "https://my-resource.openai.azure.com:8443",
    ):
        assert not pattern.fullmatch(bad)


def test_key_only_keys_mirror_the_operator_config_names():
    from agentbreeder.operator_config import PROVIDER_KEY_NAMES

    assert frozenset(gateway_keys.PROVIDER_KEYS.values()) == PROVIDER_KEY_NAMES
    assert not PROVIDER_KEY_NAMES & GATEWAY_ONLY


# --- env_import: GATEWAY_ONLY (§3.5, D3) -------------------------------------------------------


@pytest.mark.parametrize("name", sorted(GATEWAY_ONLY))
def test_gateway_only_names_need_the_flag(monkeypatch, name):
    monkeypatch.setenv(name, "value")
    assert env_import.denied(name)
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.lookup(name)
    assert caught.value.code == "IMPORT_DENIED"
    assert "only the built-in service/litellm gateway" in str(caught.value)
    assert env_import.lookup(name, allow_gateway_only=True) == "value"


@pytest.mark.parametrize("name", ["AZURE_FOO", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"])
def test_the_flag_passes_only_the_exact_gateway_only_names(monkeypatch, name):
    monkeypatch.setenv(name, "value")
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.lookup(name, allow_gateway_only=True)
    assert caught.value.code == "IMPORT_DENIED"


def test_the_flag_reads_an_env_file_too(tmp_path, monkeypatch):
    monkeypatch.delenv("BEDROCK_ACCESS_KEY_ID", raising=False)
    path = tmp_path / "keys.env"
    path.write_text("BEDROCK_ACCESS_KEY_ID=from-file\n")
    path.chmod(0o600)
    assert env_import.lookup("BEDROCK_ACCESS_KEY_ID", path, allow_gateway_only=True) == (
        "from-file"
    )
    with pytest.raises(env_import.EnvImportError):
        env_import.lookup("BEDROCK_ACCESS_KEY_ID", path)


@pytest.mark.parametrize("name", sorted(GATEWAY_ONLY))
def test_secret_set_from_env_refuses_gateway_only_names(tmp_path, monkeypatch, capsys, name):
    from agentbreeder import cli

    monkeypatch.setenv(name, "value")
    code = cli.main(["secret", "set", "X", "--state-dir", str(tmp_path), "--from-env", name])
    assert code != 0
    assert "IMPORT_DENIED" in capsys.readouterr().err


@pytest.mark.parametrize("name", sorted(GATEWAY_ONLY))
def test_an_mcp_version_cannot_import_gateway_only_names(name):
    from pydantic import ValidationError

    from agentbreeder.mcp import McpSpec

    data = {
        "upstream": {"remote": {"url": "https://mcp.example.com/mcp"}},
        "auth": {"type": "bearer", "secret": "token"},
        "secrets": {"token": {"import": name}},
        "tools": {"t": {"effect": "read"}},
    }
    with pytest.raises(ValidationError, match="can't be imported"):
        McpSpec.model_validate(data)


# --- lock: only the built-in gateway may declare them ------------------------------------------

GATEWAY_SPEC = yaml.safe_load(
    (registry.BUILTIN_ROOT / "service/litellm/1.0.0/service.yaml").read_text()
)


def spec_with(imports: dict[str, str], **changes) -> ServiceSpec:
    data = copy.deepcopy(GATEWAY_SPEC)
    data["secrets"].update({k: {"import": v, "optional": True} for k, v in imports.items()})
    data.update(changes)
    return ServiceSpec.model_validate(data)


VERTEX_IMPORT = {"vertex-service-account-json": "VERTEX_SERVICE_ACCOUNT_JSON"}


def test_the_builtin_gateway_may_declare_gateway_only_imports():
    specs = {"litellm": spec_with(VERTEX_IMPORT)}
    check_gateway_only(specs, {"litellm": BUILTIN_LABEL}, {})
    assert reads_gateway_only("litellm", specs["litellm"], {"litellm": BUILTIN_LABEL}, {})


@pytest.mark.parametrize(
    "origin,relays,changes",
    [
        ("/repo/registry", {}, {}),  # a project version named service/litellm
        (BUILTIN_LABEL, {"litellm": "github"}, {}),  # a relay is recorded as built-in
        (BUILTIN_LABEL, {}, {"provides": None}),  # a built-in service that is not the gateway
    ],
)
def test_other_services_may_not_declare_gateway_only_imports(origin, relays, changes):
    specs = {"litellm": spec_with(VERTEX_IMPORT, **changes)}
    with pytest.raises(ServiceError) as caught:
        check_gateway_only(specs, {"litellm": origin}, relays)
    assert caught.value.code == "IMPORT_GATEWAY_ONLY"
    assert "VERTEX_SERVICE_ACCOUNT_JSON" in str(caught.value)


# --- engine: the one flag-passing reader, MODEL_CREDENTIAL_MISSING, VERTEX_PROJECT_MISMATCH -----


def fake_plan(spec, origin=BUILTIN_LABEL, relays=None, review=None, secrets=None):
    stack = SimpleNamespace(environment=SimpleNamespace(gateway="litellm"))
    return SimpleNamespace(
        spec=lambda name: spec,
        locked=SimpleNamespace(origins={"litellm": origin}, relays=relays or {}, stack=stack),
        gateway_review=lambda: review,
        secrets={"litellm": secrets or {}},
    )


def test_engine_import_passes_the_flag_only_for_the_builtin_gateway(monkeypatch):
    from agentbreeder.services.engine import Engine

    monkeypatch.setenv("VERTEX_SERVICE_ACCOUNT_JSON", "k")
    spec = spec_with(VERTEX_IMPORT)
    assert Engine._import(fake_plan(spec), "litellm", "VERTEX_SERVICE_ACCOUNT_JSON", None) == "k"
    for plan in (
        fake_plan(spec, origin="/repo/registry"),
        fake_plan(spec, relays={"litellm": "s"}),
        fake_plan(spec_with(VERTEX_IMPORT, provides=None)),
    ):
        with pytest.raises(env_import.EnvImportError):
            Engine._import(plan, "litellm", "VERTEX_SERVICE_ACCOUNT_JSON", None)
    monkeypatch.setenv("AZURE_FOO", "x")
    with pytest.raises(env_import.EnvImportError):
        Engine._import(fake_plan(spec), "litellm", "AZURE_FOO", None)


def service_account(project="my-project-1", **extra):
    """A key with every field `gateway_providers.vertex_key` requires (#1025 V1)."""
    key = {
        "type": "service_account",
        "project_id": project,
        "private_key_id": "k1",
        "private_key": "not-a-real-key",
        "client_email": f"gateway@{project}.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    return json.dumps({**key, **extra}).encode()


def check(tmp_path, *entries, secrets=None, imports=VERTEX_IMPORT):
    from agentbreeder.services.engine import Engine

    plan = fake_plan(spec_with(imports), review=review(tmp_path, *entries), secrets=secrets)
    Engine._check_model_credentials(SimpleNamespace(), plan)


def test_model_credential_missing_names_the_alias_and_variable(tmp_path):
    with pytest.raises(ServiceError) as caught:
        check(tmp_path, entry(VERTEX, name="chat"))
    assert caught.value.code == "MODEL_CREDENTIAL_MISSING"
    assert "model alias chat needs VERTEX_SERVICE_ACCOUNT_JSON" in str(caught.value)
    bedrock = {
        "bedrock-access-key-id": "BEDROCK_ACCESS_KEY_ID",
        "bedrock-secret-access-key": "BEDROCK_SECRET_ACCESS_KEY",
    }
    with pytest.raises(ServiceError, match="BEDROCK_SECRET_ACCESS_KEY"):
        check(
            tmp_path,
            entry(BEDROCK, name="br"),
            secrets={"bedrock-access-key-id": b"AKIA"},
            imports=bedrock,
        )
    check(
        tmp_path,
        entry(BEDROCK, name="br"),
        secrets={"bedrock-access-key-id": b"AKIA", "bedrock-secret-access-key": b"s"},
        imports=bedrock,
    )


def test_key_only_providers_keep_todays_missing_key_behaviour(tmp_path):
    entries = entry({"model": "gemini/x", "api_key": "os.environ/GEMINI_API_KEY"})
    check(tmp_path, entries, imports={})  # no MODEL_CREDENTIAL_MISSING: unchanged


def test_vertex_project_must_be_the_keys_project(tmp_path):
    check(tmp_path, entry(VERTEX), secrets={"vertex-service-account-json": service_account()})
    with pytest.raises(ServiceError) as caught:
        check(
            tmp_path,
            entry(VERTEX, name="chat"),
            secrets={"vertex-service-account-json": service_account("other-project-9")},
        )
    assert caught.value.code == "VERTEX_PROJECT_MISMATCH"
    assert "other-project-9" not in str(caught.value)
    assert "model alias chat names vertex_project my-project-1" in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        b"not json",
        b"[]",
        json.dumps({"type": "authorized_user", "project_id": "my-project-1"}).encode(),
        json.dumps({"type": "external_account", "project_id": "my-project-1"}).encode(),
        json.dumps({"type": "service_account"}).encode(),
        json.dumps({"type": "service_account", "project_id": 3}).encode(),
        service_account(pad="x" * 17000),
    ],
)
def test_a_value_that_is_not_a_service_account_key_is_refused(tmp_path, value):
    with pytest.raises(ServiceError) as caught:
        check(tmp_path, entry(VERTEX), secrets={"vertex-service-account-json": value})
    assert caught.value.code == "VERTEX_KEY_INVALID"
    assert "authorized_user" not in str(caught.value)


# --- a real `services up` (fake Docker) with a fixture built-in gateway ------------------------


@pytest.fixture
def builtin_stack(tmp_path, monkeypatch):
    """test_services_engine's project, with its gateway moved into a fixture built-in registry
    and given the Vertex and Bedrock imports."""
    from test_services_engine import FAKEGW, write_project

    from agentbreeder.operator_config import CONFIG_ENV
    from agentbreeder.services.state import ROOT_ENV

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", "canary")
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    for name in GATEWAY_ONLY:
        monkeypatch.delenv(name, raising=False)
    spec = copy.deepcopy(FAKEGW)
    spec["secrets"].update(
        {
            "vertex-service-account-json": {
                "import": "VERTEX_SERVICE_ACCOUNT_JSON",
                "optional": True,
            },
            "bedrock-access-key-id": {"import": "BEDROCK_ACCESS_KEY_ID", "optional": True},
            "bedrock-secret-access-key": {
                "import": "BEDROCK_SECRET_ACCESS_KEY",
                "optional": True,
            },
        }
    )
    project = write_project(tmp_path / "project", gateway=spec)
    builtin = tmp_path / "builtin"
    shutil.copytree(registry.BUILTIN_ROOT, builtin)  # echo is a built-in
    shutil.copytree(project / "registry/service/fakegw", builtin / "service/fakegw")
    shutil.rmtree(project / "registry/service/fakegw")
    monkeypatch.setattr(registry, "BUILTIN_ROOT", builtin)
    (project / "gw/config.yaml").write_text(
        yaml.safe_dump({"model_list": [entry(VERTEX, None, "m1")]})
    )
    return project


def test_up_with_a_builtin_vertex_gateway(builtin_stack, monkeypatch):
    from test_services_engine import FakeDocker, up

    docker = FakeDocker()
    with pytest.raises(ServiceError) as caught:
        up(builtin_stack, docker)
    assert caught.value.code == "MODEL_CREDENTIAL_MISSING"
    assert not [e for e in docker.events if e[0] == "up"]  # refused before any container

    monkeypatch.setenv("VERTEX_SERVICE_ACCOUNT_JSON", service_account("other-project-9").decode())
    with pytest.raises(ServiceError, match="VERTEX_PROJECT_MISMATCH"):
        up(builtin_stack, FakeDocker())

    monkeypatch.setenv("VERTEX_SERVICE_ACCOUNT_JSON", service_account().decode())
    grants = []
    from test_services_engine import engine

    instance = engine(builtin_stack, FakeDocker())
    try:
        result = instance.up("dev", confirm=lambda grant: grants.append(grant) or True)
    finally:
        instance.close()
    assert result["services"]["gateway"] == "created"
    section = grants[0]["gateway_config"]
    assert section["providers"] == {
        "m1": "vertex (VERTEX_SERVICE_ACCOUNT_JSON, project my-project-1, us-central1)"
    }
    assert section["routes"] == {
        "m1": {"provider": "vertex", "project": "my-project-1", "location": "us-central1"}
    }

    # Stored from that run: a later run without the variable still has the key (§4.4).
    monkeypatch.delenv("VERTEX_SERVICE_ACCOUNT_JSON")
    result, _ = up(builtin_stack, FakeDocker())
    assert result["services"]["gateway"] in ("created", "running", "unchanged", "recreated")


def test_a_project_gateway_may_not_import_gateway_only_names(builtin_stack, monkeypatch):
    from test_services_engine import up

    project_copy = builtin_stack / "registry/service/fakegw"
    shutil.copytree(registry.BUILTIN_ROOT / "service/fakegw", project_copy)
    shutil.rmtree(registry.BUILTIN_ROOT / "service/fakegw")
    monkeypatch.setenv("VERTEX_SERVICE_ACCOUNT_JSON", service_account().decode())
    with pytest.raises(ServiceError) as caught:
        up(builtin_stack)
    assert caught.value.code == "IMPORT_GATEWAY_ONLY"


def test_a_key_only_stack_grant_has_no_routes(builtin_stack):
    from test_services_engine import FakeDocker, engine

    (builtin_stack / "gw/config.yaml").write_text(
        yaml.safe_dump({"model_list": [entry({"model": "gemini/x"}, None, "m1")]})
    )
    grants = []
    instance = engine(builtin_stack, FakeDocker())
    try:
        instance.up("dev", confirm=lambda grant: grants.append(grant) or True)
    finally:
        instance.close()
    assert set(grants[0]["gateway_config"]) == {"sha256", "providers"}


def test_new_agent_and_stack_for_review_against_the_gateway_they_write():
    from agentbreeder import newagent, stackfor

    assert "ref: {gateway}\n" in newagent.STACK_YAML
    assert "ref: {gateway}\n" in stackfor.STACK_YAML
    assert gateway_providers.builtin_caps(newagent.GATEWAY) == GATEWAY_CAPS
    # Every provider's starter names a gateway that can serve it (#1025 O1: Ollama, 1.2.0).
    gateways = {name: p.gateway for name, p in newagent.PROVIDERS.items()}
    assert gateways.pop("ollama") == newagent.LOCAL_GATEWAY == "service/litellm@1.2.0"
    assert set(gateways.values()) == {newagent.GATEWAY}
    local = gateway_providers.builtin_caps(newagent.LOCAL_GATEWAY)
    assert local.reaches_host == {gateway_providers.OLLAMA_PORT}
    assert local.imports == GATEWAY_CAPS.imports  # Ollama needs no import


def test_no_shipped_config_uses_model_info():
    root = Path(__file__).parents[1]
    for path in [*root.glob("src/agentbreeder/examples/*/stack/litellm/*.yaml")]:
        assert "model_info" not in path.read_text()
