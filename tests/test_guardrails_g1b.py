"""#1024 G1b: Presidio guardrails in the gateway config review, the guardrails hash `services up`
records, the gate's hash check, the key mint allow-list and `allowed_routes`.

Docker is faked here; tests/test_guardrails_docker.py runs a stack with the pinned images.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from conftest import GATEWAY_CAPS, actor, v2_manifest_data
from test_gateway_keys import gateway, manifest, minting, running  # noqa: F401  (fixtures)
from test_services_engine import FakeDocker, engine

from agentbreeder import gate as gate_module
from agentbreeder import gateway_keys, registry
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.gateway_keys import GatewayError, config_review, mint_body
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import CONFIG_ENV, load_operator_config
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import ROOT_ENV

PII_MASK = {
    "guardrail_name": "pii-mask",
    "litellm_params": {
        "guardrail": "presidio",
        "mode": "pre_call",
        "default_on": True,
        "pii_entities_config": {"EMAIL_ADDRESS": "MASK", "PHONE_NUMBER": "MASK"},
        "presidio_language": "en",
        "presidio_score_thresholds": {"EMAIL_ADDRESS": 0.5},
        "timeout": 30,
    },
}
PII_BLOCK = {
    "guardrail_name": "pii-block",
    "litellm_params": {
        "guardrail": "presidio",
        "mode": "pre_call",
        "default_on": True,
        "pii_entities_config": {"US_SSN": "BLOCK", "CREDIT_CARD": "BLOCK"},
    },
}
CONFIG = {
    "model_list": [{"model_name": "chat", "litellm_params": {"model": "openai/fake-model"}}],
    "litellm_settings": {"turn_off_message_logging": True, "set_verbose": False},
    "general_settings": {"store_prompts_in_spend_logs": False, "disable_error_logs": True},
    "guardrails": [PII_MASK, PII_BLOCK],
}


def _write(tmp_path: Path, data: dict, name: str = "config.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _review(tmp_path, data=None):
    return config_review(_write(tmp_path, CONFIG if data is None else data), GATEWAY_CAPS)


def _with_params(**params):
    data = copy.deepcopy(CONFIG)
    data["guardrails"][0]["litellm_params"].update(params)
    return data


# -- config review: the allow-list ------------------------------------------------------------


def test_a_presidio_config_is_reviewed(tmp_path):
    review = _review(tmp_path)
    assert [g["guardrail_name"] for g in review.guardrails] == ["pii-mask", "pii-block"]
    assert review.effective() == ["presidio:pre_call"]
    assert review.guardrails_effective(True) == ["presidio:pre_call"]
    assert len(review.guardrails_sha256) == 64


@pytest.mark.parametrize(
    "field",
    [
        # spec §4.2 and the G0 additions (§12.2)
        "presidio_ad_hoc_recognizers",
        "output_parse_pii",
        "mock_redacted_text",
        "presidio_filter_scope",
        "api_base",
        "api_key",
        "callbacks",
        "presidio_entities_deny_list",
        "presidio_analyze_chunk_size_bytes",
        "stream_scope",
        "apply_to_output",
        "mock_testing",
        "logging_only",
    ],
)
def test_each_unlisted_guardrail_field_is_refused(tmp_path, field):
    with pytest.raises(GatewayError) as error:
        _review(tmp_path, _with_params(**{field: "x"}))
    assert error.value.code == "GATEWAY_CONFIG_UNSAFE"
    assert field in str(error.value)


@pytest.mark.parametrize(
    "params, where",
    [
        ({"guardrail": "aporia"}, "presidio only"),
        ({"mode": "during_call"}, "mode"),
        ({"mode": "post_call_streaming"}, "mode"),
        ({"default_on": None}, "default_on"),  # false is an agent guardrail (#1053)
        ({"default_on": "true"}, "default_on"),
        ({"presidio_language": "es"}, "en only"),
        ({"pii_entities_config": {}}, "pii_entities_config"),
        ({"pii_entities_config": {"EMAIL_ADDRESS": "REDACT"}}, "pii_entities_config"),
        ({"pii_entities_config": {"email": "MASK"}}, "pii_entities_config"),
        ({"pii_entities_config": ["EMAIL_ADDRESS"]}, "pii_entities_config"),
        ({"presidio_score_thresholds": {"EMAIL_ADDRESS": 0.81}}, "thresholds"),
        ({"presidio_score_thresholds": {"EMAIL_ADDRESS": 1.0}}, "thresholds"),
        ({"presidio_score_thresholds": {"EMAIL_ADDRESS": True}}, "thresholds"),
        ({"presidio_score_thresholds": {"EMAIL_ADDRESS": -0.1}}, "thresholds"),
        ({"timeout": 0}, "timeout"),
        ({"timeout": 61}, "timeout"),
        ({"timeout": "30"}, "timeout"),
        ({"timeout": True}, "timeout"),
    ],
)
def test_guardrail_values_outside_the_allow_list_are_refused(tmp_path, params, where):
    with pytest.raises(GatewayError) as error:
        _review(tmp_path, _with_params(**params))
    assert error.value.code == "GATEWAY_CONFIG_UNSAFE"
    assert where in str(error.value)


def test_os_environ_and_path_values_are_refused(tmp_path):
    data = _with_params(presidio_language="os.environ/LANG")
    with pytest.raises(GatewayError, match="GATEWAY_CONFIG_UNSAFE"):
        _review(tmp_path, data)
    data = copy.deepcopy(CONFIG)
    data["guardrails"][0]["litellm_params"]["pii_entities_config"]["EMAIL_ADDRESS"] = (
        "os.environ/X"
    )
    with pytest.raises(GatewayError, match="GATEWAY_CONFIG_UNSAFE"):
        _review(tmp_path, data)


@pytest.mark.parametrize(
    "entry",
    [
        {**PII_MASK, "guardrail_name": "pii-block"},  # duplicate name
        {**PII_MASK, "guardrail_name": "bad name"},
        {**PII_MASK, "extra": 1},
        {"guardrail_name": "x"},
        "pii-mask",
    ],
)
def test_malformed_entries_are_refused(tmp_path, entry):
    data = copy.deepcopy(CONFIG)
    data["guardrails"].append(entry)
    with pytest.raises(GatewayError):
        _review(tmp_path, data)


def test_guardrails_must_be_a_list(tmp_path):
    data = {**CONFIG, "guardrails": {"pii-mask": PII_MASK}}
    with pytest.raises(GatewayError, match="GATEWAY_CONFIG"):
        _review(tmp_path, data)


def test_an_entity_masked_first_cannot_be_blocked_later(tmp_path):
    # A mask rewrites the text before later guardrails run (spec §12.2): the block never fires.
    data = copy.deepcopy(CONFIG)
    data["guardrails"][1]["litellm_params"]["pii_entities_config"]["EMAIL_ADDRESS"] = "BLOCK"
    with pytest.raises(GatewayError, match="blocks an entity an earlier guardrail masks"):
        _review(tmp_path, data)
    # Blocked first, masked later is fine (the block runs first).
    data["guardrails"].reverse()
    data["guardrails"][1]["litellm_params"]["pii_entities_config"].pop("EMAIL_ADDRESS")
    assert _review(tmp_path, data).guardrails


def test_a_logging_only_mask_does_not_shadow_a_later_block(tmp_path):
    data = copy.deepcopy(CONFIG)
    data["guardrails"][0]["litellm_params"]["mode"] = "logging_only"
    data["guardrails"][1]["litellm_params"]["pii_entities_config"]["EMAIL_ADDRESS"] = "BLOCK"
    assert _review(tmp_path, data).effective() == ["presidio:pre_call"]


def test_configs_without_guardrails_are_unchanged(tmp_path):
    data = {k: v for k, v in CONFIG.items() if k != "guardrails"}
    review = _review(tmp_path, data)
    assert review.guardrails == [] and review.effective() == []
    northwind = Path(registry.__file__).parent / "examples/northwind/stack/litellm/config.yaml"
    assert config_review(northwind, GATEWAY_CAPS).guardrails == []


# -- config review: one read, the guardrails hash --------------------------------------------


def test_the_file_is_read_once(tmp_path, monkeypatch):
    import hashlib

    from agentbreeder import strict_yaml

    path = _write(tmp_path, CONFIG)
    reads = []
    original = strict_yaml.read_file
    monkeypatch.setattr(
        strict_yaml, "read_file", lambda *a, **k: reads.append(a) or original(*a, **k)
    )

    def refuse(self):  # the old code hashed a second read_bytes()
        raise AssertionError("config_review read the file twice")

    monkeypatch.setattr(Path, "read_bytes", refuse)
    review = config_review(path, GATEWAY_CAPS)
    assert len(reads) == 1
    monkeypatch.undo()
    assert review.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_guardrails_hash_ignores_model_list_edits(tmp_path):
    before = _review(tmp_path)
    data = copy.deepcopy(CONFIG)
    data["model_list"].append({"model_name": "m2", "litellm_params": {"model": "gemini/x"}})
    after = _review(tmp_path, data)
    assert after.sha256 != before.sha256
    assert after.guardrails_sha256 == before.guardrails_sha256


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["guardrails"][0]["litellm_params"]["pii_entities_config"].pop("PHONE_NUMBER"),
        lambda d: d["guardrails"].reverse(),
        lambda d: d["general_settings"].pop("disable_error_logs"),
        lambda d: d["guardrails"][1]["litellm_params"].update(mode="post_call"),
    ],
)
def test_guardrails_hash_follows_guardrails_and_logging(tmp_path, edit):
    data = copy.deepcopy(CONFIG)
    edit(data)
    assert _review(tmp_path, data).guardrails_sha256 != _review(tmp_path).guardrails_sha256


def test_guardrails_hash_ignores_yaml_formatting(tmp_path):
    flow = _write(tmp_path, CONFIG, "a.yaml")
    flow.write_text(yaml.safe_dump(CONFIG, default_flow_style=True))
    assert (
        config_review(flow, GATEWAY_CAPS).guardrails_sha256 == _review(tmp_path).guardrails_sha256
    )


# -- guardrails_effective -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "edit, message",
    [
        (lambda d: d.pop("guardrails"), "pre_call"),
        (
            lambda d: [g["litellm_params"].update(mode="logging_only") for g in d["guardrails"]],
            "pre_call",
        ),
        (
            lambda d: [g["litellm_params"].update(mode="post_call") for g in d["guardrails"]],
            "pre_call",
        ),
        (lambda d: d["litellm_settings"].pop("turn_off_message_logging"), "turn_off"),
        (lambda d: d["general_settings"].pop("disable_error_logs"), "disable_error_logs"),
        (lambda d: d["general_settings"].update(disable_error_logs=False), "disable_error_logs"),
    ],
)
def test_a_vacuous_config_is_ineffective_when_required(tmp_path, edit, message):
    data = copy.deepcopy(CONFIG)
    edit(data)
    review = _review(tmp_path, data)
    with pytest.raises(GatewayError) as error:
        review.guardrails_effective(True)
    assert error.value.code == "GUARDRAILS_INEFFECTIVE" and message in str(error.value)
    review.guardrails_effective(False)  # not required: no refusal


def test_unsafe_logging_values_are_refused_before_effectiveness(tmp_path):
    data = copy.deepcopy(CONFIG)
    data["litellm_settings"]["set_verbose"] = True
    with pytest.raises(GatewayError, match="GATEWAY_CONFIG_UNSAFE"):
        _review(tmp_path, data)


# -- key mint allow-list ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "disable_global_guardrails",
        "opted_out_global_guardrails",
        "guardrails",
        "policies",
        "permissions",
        "litellm_disabled_callbacks",
        "logging",
        "tags",
        "team_id",
    ],
)
def test_mint_refuses_metadata_outside_the_allow_list(key):
    with pytest.raises(GatewayError) as error:
        mint_body("agentbreeder-x", ["chat"], 1.0, 10, {"agentbreeder": True, key: True})
    assert error.value.code == "GATEWAY_KEY_METADATA"


def test_mint_refuses_nested_metadata_values():
    with pytest.raises(GatewayError, match="GATEWAY_KEY_METADATA"):
        mint_body("a", ["chat"], 1.0, 10, {"agent": {"disable_global_guardrails": True}})


def test_mint_body_is_built_from_allowed_fields_only():
    body = mint_body(
        "agentbreeder-x",
        ["chat"],
        1.0,
        10,
        {"agentbreeder": True, "deployment_id": "x", "agent": "a", "environment": "dev"},
        duration="6h",
        routes=list(gateway_keys.GUARDED_ROUTES),
    )
    assert set(body) <= gateway_keys.MINT_FIELDS
    assert body["allowed_routes"] == list(gateway_keys.GUARDED_ROUTES)
    assert "allowed_routes" not in mint_body("a", ["chat"], 1.0, 10, {})


@pytest.mark.parametrize("routes", [[], ["/key/generate"], ["/openai/v1/chat/completions"]])
def test_mint_routes_come_from_the_guarded_list(routes):
    with pytest.raises(GatewayError, match="GATEWAY_KEY_ROUTES"):
        mint_body("a", ["chat"], 1.0, 10, {}, routes=routes)


def test_guarded_routes_are_chat_and_embeddings():
    assert set(gateway_keys.GUARDED_ROUTES) == {
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/embeddings",
        "/embeddings",
    }


def test_mint_checks_before_revoking(monkeypatch):
    calls = []
    client = gateway_keys.LiteLLM("http://127.0.0.1:9", "sk-master")
    monkeypatch.setattr(client, "_call", lambda *a, **k: calls.append(a) or {"key": "sk-x"})
    with pytest.raises(GatewayError):
        client.mint("a", ["chat"], 1.0, 10, {"guardrails": []})
    assert calls == []  # a refused mint touches nothing, not even the old key
    assert client.mint("a", ["chat"], 1.0, 10, {}, routes=["/v1/chat/completions"]) == "sk-x"
    assert calls[-1][2]["allowed_routes"] == ["/v1/chat/completions"]


# -- gate -------------------------------------------------------------------------------------


SHA = "b" * 64
VERIFIED = {
    "required": True,
    "approved_sha256": SHA,
    "running_sha256": SHA,
    "effective": ["presidio:pre_call"],
}


def _deploy(identity_env, state, runtime, floor):
    identity_env.config["environments"]["dev"]["guardrails"] = floor
    service = DeploymentService(state, runtime, identity_env.load())
    return service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)


@pytest.mark.parametrize("enforcement", ["enforce", "audit"])
@pytest.mark.parametrize(
    "floor, code",
    [
        ({"required": True, "approved_sha256": SHA}, "GUARDRAILS_UNVERIFIED"),
        ({**VERIFIED, "effective": []}, "GUARDRAILS_UNVERIFIED"),
        ({**VERIFIED, "effective": ["presidio:post_call"]}, "GUARDRAILS_UNVERIFIED"),
        ({**VERIFIED, "running_sha256": "c" * 64}, "GUARDRAILS_CHANGED"),
    ],
)
def test_gate_checks_the_running_hash(identity_env, state, runtime, floor, code, enforcement):
    identity_env.config["enforcement"] = enforcement
    with pytest.raises(GateDenied) as error:
        _deploy(identity_env, state, runtime, floor)
    assert error.value.code == code
    assert code not in gate_module.OVERRIDABLE
    assert runtime.mock_calls == []
    rows = state.audit_rows(attempt_id=error.value.attempt_id)
    assert not any(r["action"] == "audit_mode_proceed" for r in rows)


def test_gate_allows_the_approved_running_config(identity_env, state, runtime):
    assert _deploy(identity_env, state, runtime, VERIFIED)["state"] == "ready"


def test_changed_message_names_short_hashes_only(identity_env, state, runtime):
    with pytest.raises(GateDenied) as error:
        _deploy(identity_env, state, runtime, {**VERIFIED, "running_sha256": "c" * 64})
    assert "c" * 12 in str(error.value) and "c" * 13 not in str(error.value)


def test_cedar_set_is_the_verified_effective_list(identity_env):
    identity_env.config["environments"]["dev"]["guardrails"] = {
        **VERIFIED,
        "effective": ["presidio:post_call", "presidio:pre_call"],
    }
    identity_env.config["environments"]["prod"]["guardrails"] = {
        **VERIFIED,
        "running_sha256": "c" * 64,
    }
    gate = gate_module.IdentityGate(identity_env.load())

    def guardrails(environment):
        request = gate.policy_request(
            "approve",
            actor("alice"),
            agent="a",
            context=gate_module.DeploymentContext(environment, "local"),
            principal=None,
            risk_tier=None,
        )
        return request.resource["properties"]["guardrails"]

    assert guardrails("dev") == ["presidio:post_call", "presidio:pre_call"]
    assert guardrails("prod") == []  # changed: nothing is verified
    assert guardrails("nowhere") == []


@pytest.mark.parametrize("floor", [VERIFIED, None])
def test_deploy_key_gets_guarded_routes(minting, gateway, state, runtime, floor):  # noqa: F811
    if floor:
        minting.config["environments"]["dev"]["guardrails"] = floor
    running(runtime)
    result = DeploymentService(state, runtime, minting.load()).deploy(manifest(), local=True)
    minted = gateway.keys[gateway_keys.alias(result["id"])]
    if floor:
        assert minted["allowed_routes"] == list(gateway_keys.GUARDED_ROUTES)
    else:
        assert "allowed_routes" not in minted


# -- services up: the record written into the operator config ----------------------------------


STACK = {
    "kind": "stack",
    "schema_version": 1,
    "name": "guarded",
    "services": {
        "postgres": {"ref": "service/postgres-pgvector@1.2.0", "with": {"databases": ["litellm"]}},
        "analyzer": {"ref": "service/presidio-analyzer@1.0.0"},
        "anonymizer": {"ref": "service/presidio-anonymizer@1.0.0"},
        "litellm": {
            "ref": "service/litellm@1.1.0",
            "with": {"config": "litellm.yaml"},
            "wire": {
                "database-url": "postgres.databases.litellm",
                "presidio-analyzer": "analyzer.url",
                "presidio-anonymizer": "anonymizer.url",
            },
        },
    },
    "environment": {
        "gateway": "litellm",
        "models": ["chat"],
        "ceilings": {"budget_usd": 5, "rpm": 60},
        "agents": {"guarded-agent": {"sponsor": "lead@example.com", "review_by": "2027-06-30"}},
    },
}


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    project = tmp_path / "project"
    project.mkdir()
    _write(project, CONFIG, "litellm.yaml")
    return project


def _up(project, stack=None, config=None, confirm=True):
    (project / "stack.yaml").write_text(yaml.safe_dump(stack or STACK))
    if config is not None:
        _write(project, config, "litellm.yaml")
    (project / "stack.lock").unlink(missing_ok=True)
    seen = []
    instance = engine(project, FakeDocker())
    try:
        instance.up("dev", confirm=lambda grant: seen.append(grant) or confirm)
        directory = instance.state.env_dir("dev")
        operator = yaml.safe_load((directory / "operator.yaml").read_text())
        compose = yaml.safe_load(instance.state.compose_path.read_text())
    finally:
        instance.close()
    return seen[0], operator, compose


def _guarded(**floor):
    data = copy.deepcopy(STACK)
    data["environment"]["guardrails"] = floor
    return data


def test_up_records_the_mounted_config_hash(stack, tmp_path):
    expected = config_review(stack / "litellm.yaml", GATEWAY_CAPS).guardrails_sha256
    grant, operator, compose = _up(stack, _guarded(required=True, approved_sha256=expected))
    record = operator["environments"]["dev"]["guardrails"]
    assert record == {
        "required": True,
        "approved_sha256": expected,
        "running_sha256": expected,
        "effective": ["presidio:pre_call"],
    }
    assert grant["guardrails"] == record
    assert grant["gateway_config"]["guardrails_sha256"] == expected
    litellm = compose["services"]["litellm"]["environment"]
    assert litellm["PRESIDIO_ANALYZER_API_BASE"] == "http://analyzer:3000"
    assert litellm["PRESIDIO_ANONYMIZER_API_BASE"] == "http://anonymizer:3000"
    assert litellm["LITELLM_LOG"] == "WARNING"
    for name in ("analyzer", "anonymizer"):
        service = compose["services"][name]
        assert service["networks"] == ["stack"]  # never `agents`, never `edge`
        assert "ports" not in service and service["read_only"] is True
    # The written file loads, and the gate verifies it.
    loaded = load_operator_config(Path(operator_path(stack)))
    floor = loaded.model.environments["dev"].guardrails
    assert floor.verified == ["presidio:pre_call"]


def operator_path(project) -> Path:
    instance = engine(project, FakeDocker())
    try:
        return instance.state.env_dir("dev") / "operator.yaml"
    finally:
        instance.close()


def test_up_records_an_unapproved_hash_and_the_grant_says_so(stack):
    grant, operator, _ = _up(stack, _guarded(required=True, approved_sha256="d" * 64))
    record = operator["environments"]["dev"]["guardrails"]
    assert record["running_sha256"] != record["approved_sha256"]
    from agentbreeder.services.plan import grant_text

    assert "does NOT match the running config" in grant_text(grant)


@pytest.mark.parametrize(
    "edit, message",
    [
        (lambda s, c: c.pop("guardrails"), "pre_call"),
        (lambda s, c: c["general_settings"].pop("disable_error_logs"), "disable_error_logs"),
        (
            lambda s, c: [s["services"]["litellm"]["wire"].pop(k) for k in ("presidio-analyzer",)],
            "presidio-analyzer",
        ),
    ],
)
def test_up_refuses_an_ineffective_required_floor(stack, edit, message):
    data, config = _guarded(required=True, approved_sha256=SHA), copy.deepcopy(CONFIG)
    edit(data, config)
    with pytest.raises(ServiceError) as error:
        _up(stack, data, config)
    assert error.value.code == "GUARDRAILS_INEFFECTIVE" and message in str(error.value)
    assert not operator_path(stack).exists()


def test_up_refuses_guardrails_without_presidio_even_when_not_required(stack):
    data = copy.deepcopy(STACK)
    data["services"]["litellm"]["wire"] = {"database-url": "postgres.databases.litellm"}
    with pytest.raises(ServiceError, match="GUARDRAILS_INEFFECTIVE"):
        _up(stack, data)


def test_up_refuses_a_required_floor_on_litellm_1_0_0(stack):
    data = _guarded(required=True, approved_sha256=SHA)
    data["services"]["litellm"]["ref"] = "service/litellm@1.0.0"
    data["services"]["litellm"]["wire"] = {"database-url": "postgres.databases.litellm"}
    with pytest.raises(ServiceError, match="GUARDRAILS_INEFFECTIVE"):
        _up(stack, data)


def test_a_stack_without_a_floor_writes_no_record_and_keeps_its_grant(stack):
    config = {k: v for k, v in CONFIG.items() if k != "guardrails"}
    grant, operator, _ = _up(stack, config=config)
    assert "guardrails" not in operator["environments"]["dev"]
    assert "guardrails" not in grant and "guardrails_sha256" not in grant["gateway_config"]


def test_a_floor_not_required_records_what_runs(stack):
    _, operator, _ = _up(stack, _guarded(required=False))
    record = operator["environments"]["dev"]["guardrails"]
    assert record["required"] is False and record["effective"] == ["presidio:pre_call"]
    assert "approved_sha256" not in record


def test_stack_floor_needs_the_reviewed_hash(stack):
    with pytest.raises(ServiceError, match="STACK_INVALID"):
        _up(stack, _guarded(required=True))


def test_the_grant_text_lists_entities_not_values(stack):
    from agentbreeder.services.plan import grant_text

    expected = config_review(stack / "litellm.yaml", GATEWAY_CAPS).guardrails_sha256
    grant, _, _ = _up(stack, _guarded(required=True, approved_sha256=expected))
    text = grant_text(grant)
    assert f"guardrails sha256 {expected}" in text
    assert "pii-block (pre_call): CREDIT_CARD BLOCK, US_SSN BLOCK" in text
    assert "matches the running config" in text
    json.dumps(grant)  # the grant stays JSON (its digest)


def test_a_floor_not_required_records_no_effective_check_when_it_would_not_work(stack):
    # Setting `required` later by hand must not verify a config `up` never checked (#1024).
    config = copy.deepcopy(CONFIG)
    config["general_settings"].pop("disable_error_logs")
    _, operator, _ = _up(stack, _guarded(required=False), config)
    record = operator["environments"]["dev"]["guardrails"]
    assert record["effective"] == [] and len(record["running_sha256"]) == 64


FAKE_PRESIDIO = {
    "image": "python:3.12-slim@sha256:" + "0" * 64,
    "ports": {"http": {"port": 3000}},
    "outputs": {"url": {"type": "url", "port": "http"}},
}


# A same-version shadow of the built-in is already refused by the registry (REF_CONFLICT).
@pytest.mark.parametrize(
    "name, version", [("fakepresidio", "1.0.0"), ("presidio-analyzer", "9.0.0")]
)
def test_presidio_inputs_must_be_the_built_in_services(stack, name, version):
    # A project service (even one under the built-in's name) answering `/analyze` with nothing
    # would make every check pass vacuously.
    path = stack / "registry" / "service" / name / version
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(f"kind: service\nname: {name}\nversion: {version}\n")
    (path / "service.yaml").write_text(yaml.safe_dump(FAKE_PRESIDIO))
    for required in (True, False):
        data = _guarded(required=required, approved_sha256=SHA)
        data["services"]["analyzer"] = {"ref": f"service/{name}@{version}"}
        with pytest.raises(ServiceError) as error:
            _up(stack, data)
        assert error.value.code == "GUARDRAILS_INEFFECTIVE"
        assert "built-in service/presidio-analyzer" in str(error.value)


def test_the_grant_shows_thresholds_timeout_and_logging(stack):
    from agentbreeder.services.plan import grant_text

    grant, _, _ = _up(stack, _guarded(required=False))
    text = grant_text(grant)
    assert "pii-mask (pre_call): EMAIL_ADDRESS MASK, PHONE_NUMBER MASK" in text
    assert "threshold EMAIL_ADDRESS 0.5; timeout 30s" in text
    assert "logging: disable_error_logs true, store_prompts_in_spend_logs false" in text


def test_a_required_floor_needs_the_built_in_gateway(stack):
    from test_services_engine import FAKEGW

    spec = {**FAKEGW, "inputs": {"presidio-analyzer": {"type": "url"},
                                    "presidio-anonymizer": {"type": "url"}}}  # fmt: skip
    version = stack / "registry" / "service" / "fakegw" / "1.0.0"
    (version / "files").mkdir(parents=True)
    (version / "files" / "index.html").write_text("gw\n")
    (version / "manifest.yaml").write_text("kind: service\nname: fakegw\nversion: 1.0.0\n")
    (version / "service.yaml").write_text(yaml.safe_dump(spec))
    data = _guarded(required=True, approved_sha256=SHA)
    data["services"]["litellm"]["ref"] = "service/fakegw@1.0.0"
    data["services"]["litellm"]["wire"].pop("database-url")
    with pytest.raises(ServiceError) as error:
        _up(stack, data)
    assert error.value.code == "GUARDRAILS_INEFFECTIVE" and "built-in" in str(error.value)
