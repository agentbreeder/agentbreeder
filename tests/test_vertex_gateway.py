"""#1025 slice V1: Vertex AI through `service/litellm@1.5.0` (config-only: nothing here calls
Vertex AI, and no test in this file needs Docker).

The 1.5.0 version (1.4.0 plus one gateway-only import), the service-account key rules on both
sides (`gateway_providers.vertex_key` at `services up`, the launcher's copy in the container),
the launcher's project binding, slice P1's review and import rules re-run on the real built-in
(spec §14), and `new agent --model vertex`. The pinned-image half (redirects, client-side
parameters, credential readback, logs) is tests/test_vertex_gateway_docker.py, opt-in.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import date

import pytest
import yaml
from conftest import GATEWAY_CAPS

from agentbreeder import cli, env_import, gateway_keys, gateway_providers, newagent, registry
from agentbreeder.gateway_keys import GatewayError
from agentbreeder.services.schema import ServiceError, load_service

V14 = registry.BUILTIN_ROOT / "service/litellm/1.4.0"
V15 = registry.BUILTIN_ROOT / "service/litellm/1.5.0"
REF = "service/litellm@1.5.0"
KEY_NAME = "VERTEX_SERVICE_ACCOUNT_JSON"
TODAY = date(2026, 11, 18)
PROJECT = "my-project-1"
# Stand-ins, assembled so no scanner reads them as a real key.
PRIVATE = (
    "-----BEGIN " + "PRIVATE KEY-----\nMARKERPRIVATEKEYBODY\n-----END " + "PRIVATE KEY-----\n"
)
KEY = {
    "type": "service_account",
    "project_id": PROJECT,
    "private_key_id": "markerprivatekeyid",
    "private_key": PRIVATE,
    "client_email": f"gateway@{PROJECT}.iam.gserviceaccount.com",
    "client_id": "100000000000000000001",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/gateway",
    "universe_domain": "googleapis.com",
}
VERTEX = {
    "model": "vertex_ai/gemini-3.8-flash",
    "vertex_credentials": f"os.environ/{KEY_NAME}",
    "vertex_project": PROJECT,
    "vertex_location": "us-central1",
}


def key_json(**changes) -> str:
    """A service-account key as one line; a change of None drops the field."""
    data = {**KEY, **changes}
    return json.dumps({k: v for k, v in data.items() if v is not None})


def entry(params=None, name="m", **changes):
    return {"model_name": name, "litellm_params": {**(params or VERTEX), **changes}}


def config_text(*entries, **sections) -> str:
    return yaml.safe_dump({"model_list": list(entries or [entry()]), **sections})


def review(tmp_path, *entries, ref=REF, **sections):
    path = tmp_path / "config.yaml"
    path.write_text(config_text(*entries, **sections))
    return gateway_keys.config_review(path, gateway_providers.builtin_caps(ref))


def refused(tmp_path, *entries, ref=REF, code="GATEWAY_CONFIG_UNSAFE", **sections) -> str:
    with pytest.raises(GatewayError) as caught:
        review(tmp_path, *entries, ref=ref, **sections)
    assert caught.value.code == code
    return str(caught.value)


@pytest.fixture
def start():
    spec = importlib.util.spec_from_file_location("litellm_start_150", V15 / "files/start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the version -------------------------------------------------------------------------------


def test_1_5_0_is_1_4_0_plus_the_vertex_import():
    old = yaml.safe_load((V14 / "service.yaml").read_text())
    new = yaml.safe_load((V15 / "service.yaml").read_text())
    assert new["secrets"].pop("vertex-service-account-json") == {
        "import": KEY_NAME,
        "optional": True,
    }
    assert old == new  # the image digest, env, ports, user and reaches_host included
    for name in ("agentbreeder_request_filter.py",):  # 1.4.0's filter, byte for byte
        assert (V15 / "files" / name).read_bytes() == (V14 / "files" / name).read_bytes()
    assert sorted(p.name for p in (V15 / "files").iterdir() if p.is_file()) == [
        "agentbreeder_request_filter.py",
        "start.py",
    ]
    assert load_service(V15 / "service.yaml").reaches_host == [11434]


def test_each_version_imports_only_the_providers_its_slice_shipped():
    """D12: a version never declares a provider whose slice has not shipped. Only 1.5.0
    imports a gateway-only credential, and only Vertex's."""
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"):
        caps = gateway_providers.builtin_caps(f"service/litellm@{version}")
        assert caps.imports == GATEWAY_CAPS.imports, version
    caps = gateway_providers.builtin_caps(REF)
    assert caps.imports - GATEWAY_CAPS.imports == {KEY_NAME}
    assert caps.imports & gateway_providers.GATEWAY_ONLY == {KEY_NAME}
    assert caps.reaches_host == {11434}  # cumulative: Ollama still works


def test_the_manifest_says_config_only():
    manifest = yaml.safe_load((V15 / "manifest.yaml").read_text())
    assert manifest["version"] == "1.5.0"
    assert "Config-only: not yet run against Vertex AI" in manifest["description"]


def test_the_launcher_lists_match_service_yaml_and_the_shared_rules(start):
    service = yaml.safe_load((V15 / "service.yaml").read_text())
    assert set(start.SERVICE_ENV) == set(service["env"]) | {"AGENTBREEDER_SERVICE"}
    assert {k: service["env"][k] for k in start.FORCED} == start.FORCED
    assert set(start.GATEWAY_ONLY) == gateway_providers.GATEWAY_ONLY
    imports = {name: s["import"] for name, s in service["secrets"].items() if "import" in s}
    assert {n: v for n, v in start.NAMES.items() if n in imports} == imports
    assert start.NAMES[start.VERTEX_SECRET] == start.VERTEX_KEY_NAME == KEY_NAME
    for name in ("KEY_NAME", "KEY_FIELDS", "KEY_TYPE", "TOKEN_URI", "UNIVERSE"):
        assert getattr(start, f"VERTEX_{name}") == getattr(gateway_providers, f"VERTEX_{name}")
    assert start.VERTEX_PROJECT.pattern == gateway_providers.VERTEX_PROJECT.pattern
    assert start.LIMIT == gateway_providers.VERTEX_KEY_LIMIT
    assert gateway_providers.PROVIDERS["vertex"].prefixes == (start.VERTEX_PROVIDER,)
    assert gateway_providers.PROVIDERS["vertex"].credentials == {"vertex_credentials": KEY_NAME}


# --- the review, on the real built-in (slice P1's fixture tests, re-run for V1) ----------------


def test_a_vertex_entry_passes_on_1_5_0_and_prints_its_route(tmp_path):
    result = review(tmp_path, entry(name="chat"))
    label = f"vertex ({KEY_NAME}, project {PROJECT}, us-central1)"
    assert result.providers == {"chat": label}
    assert result.required_env == {"chat": (KEY_NAME,)}
    assert result.routes["chat"] == {
        "provider": "vertex",
        "project": PROJECT,
        "location": "us-central1",
    }


@pytest.mark.parametrize("version", ["1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"])
def test_earlier_versions_still_refuse_vertex(tmp_path, version):
    message = refused(
        tmp_path, ref=f"service/litellm@{version}", code="GATEWAY_PROVIDER_UNSUPPORTED"
    )
    assert KEY_NAME in message and version in message


def test_1_5_0_still_refuses_bedrock_and_azure(tmp_path):
    bedrock = {
        "model": "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
        "aws_access_key_id": "os.environ/BEDROCK_ACCESS_KEY_ID",
        "aws_secret_access_key": "os.environ/BEDROCK_SECRET_ACCESS_KEY",
        "aws_region_name": "us-east-1",
    }
    azure = {
        "model_name": "m",
        "litellm_params": {
            "model": "azure/my-deployment",
            "api_key": "os.environ/AZURE_OPENAI_API_KEY",
            "api_base": "os.environ/AZURE_OPENAI_ENDPOINT",
            "api_version": "2024-10-21",
        },
        "model_info": {"base_model": "azure/gpt-4o-mini"},
    }
    for item in (entry(bedrock), azure):
        assert REF in refused(tmp_path, item, code="GATEWAY_PROVIDER_UNSUPPORTED")


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "vertex_ai/openai/gpt-4o"},
        {"model": "vertex_ai/1234567890"},
        {"model": "vertex_ai/claude-3-5-sonnet@20240620"},
        {"model": "vertex_ai/gemini-3.8-flash@x"},
        {"model": "vertex_ai/Gemini-3.8-flash"},
        {"model": "Vertex_AI/gemini-3.8-flash"},
        {"model": "vertex_ai_beta/gemini-3.8-flash"},
        {"model": "vertex_ai/gemini-3.8-flash\n"},
        {"api_base": "https://attacker.example"},
        {"base_url": "https://attacker.example"},
        {"api_key": "os.environ/GEMINI_API_KEY"},
        {"vertex_ai_project": PROJECT},
        {"vertex_ai_location": "us-central1"},
        {"vertex_ai_credentials": f"os.environ/{KEY_NAME}"},
        {"vertex_api_version": "v1beta1"},
        {"extra_headers": "header-marker"},
        {"vertex_credentials": "/var/run/key.json"},
        {"vertex_credentials": "os.environ/GOOGLE_APPLICATION_CREDENTIALS"},
        {"vertex_credentials": "os.environ/GEMINI_API_KEY"},
        {"vertex_credentials": '{"type": "service_account"}'},
        {"vertex_project": "other\x1b[31m"},
        {"vertex_project": "my-project-1\n"},
        {"vertex_project": "My-Project"},
        {"vertex_project": 12345678},
        {"vertex_location": "us-central1\r"},
        {"vertex_location": "us-central1.evil.example"},
        {"vertex_location": "üs-central1"},
        {"vertex_location": True},
    ],
)
def test_malformed_vertex_entries_are_refused_without_echoing_a_value(tmp_path, changes):
    message = refused(tmp_path, entry(**changes))
    for value in changes.values():
        if isinstance(value, str) and not value.startswith("os.environ/"):
            assert value.strip() not in message


@pytest.mark.parametrize("missing", ["vertex_credentials", "vertex_project", "vertex_location"])
def test_every_vertex_param_is_required(tmp_path, missing):
    params = {k: v for k, v in VERTEX.items() if k != missing}
    assert missing in refused(tmp_path, entry(params))


@pytest.mark.parametrize(
    "model",
    [
        "vertex_ai/gemini-embedding-2",
        "vertex_ai/text-embedding-005",
        "vertex_ai/text-multilingual-embedding-002",
    ],
)
def test_vertex_embedding_models_pass(tmp_path, model):
    assert review(tmp_path, entry(model=model, dimensions=768)).aliases == {"m"}


def test_verbose_logging_is_refused_with_a_vertex_key(tmp_path):
    refused(tmp_path, entry(), litellm_settings={"set_verbose": True})
    review(tmp_path, entry(), litellm_settings={"set_verbose": False})


def test_client_side_credential_settings_stay_refused(tmp_path):
    for section, key in (
        ("general_settings", "allow_client_side_credentials"),
        ("litellm_settings", "allow_client_side_credentials"),
        ("router_settings", "configurable_clientside_auth_params"),
        ("litellm_settings", "vertex_project"),
        ("litellm_settings", "vertex_location"),
        ("litellm_settings", "vertex_credentials"),
    ):
        refused(tmp_path, entry(), **{section: {key: True}})


def test_model_info_takes_no_price_and_no_base_model_for_vertex(tmp_path):
    for info in ({"input_cost_per_token": 0}, {"base_model": "azure/gpt-4o"}, {"id": "x"}):
        refused(tmp_path, {**entry(), "model_info": info})
    review(tmp_path, {**entry(), "model_info": {"mode": "chat"}})


# --- the key rules, the same on both sides ------------------------------------------------------

KEY_CASES = {
    "as issued": (key_json(), True),
    "no universe_domain": (key_json(universe_domain=None), True),
    "unknown extra field": (key_json(extra="dropped"), True),
    "trailing newline": (key_json() + "\n", True),
    "authorized_user": (key_json(type="authorized_user"), False),
    "external_account": (
        key_json(type="external_account", credential_source={"url": "http://169.254.169.254/"}),
        False,
    ),
    "impersonated": (key_json(type="impersonated_service_account"), False),
    "no type": (key_json(type=None), False),
    "another token endpoint": (key_json(token_uri="https://attacker.example/token"), False),
    "http token endpoint": (key_json(token_uri="http://oauth2.googleapis.com/token"), False),
    "token endpoint with a newline": (
        key_json(token_uri="https://oauth2.googleapis.com/token\n"),
        False,
    ),
    "no token endpoint": (key_json(token_uri=None), False),
    "another universe": (key_json(universe_domain="attacker.example"), False),
    "universe not a string": (
        key_json(universe_domain=None)[:-1] + ',"universe_domain":1}',
        False,
    ),
    "no private key": (key_json(private_key=None), False),
    "empty private key": (key_json(private_key=""), False),
    "no client_email": (key_json(client_email=None), False),
    "no client_id": (key_json(client_id=None), False),
    "no private_key_id": (key_json(private_key_id=None), False),
    "project not a string": (key_json(project_id=None)[:-1] + ',"project_id":7}', False),
    "project not a project id": (key_json(project_id="x\x1b[31m"), False),
    "duplicate type": ('{"type":"authorized_user",' + key_json()[1:], False),
    "duplicate token_uri": (key_json()[:-1] + ',"token_uri":"https://attacker.example"}', False),
    "a list": ("[" + key_json() + "]", False),
    "a string": ('"service_account"', False),
    "not json": ("not json", False),
    "empty object": ("{}", False),
    "deeply nested": ("[" * 100000, False),
    "two lines": (json.dumps(KEY, indent=1), False),
    "nul": (key_json() + "\0", False),
    "empty": ("", False),
    "over 16 KiB": (key_json(pad="x" * 17000), False),
}
CANONICAL = json.dumps(
    {name: KEY[name] for name in gateway_providers.VERTEX_KEY_FIELDS},
    separators=(",", ":"),
)


@pytest.mark.parametrize("case", sorted(KEY_CASES))
def test_the_host_and_the_launcher_agree_on_every_key(start, tmp_path, monkeypatch, case):
    text, accepted = KEY_CASES[case]
    try:
        host = gateway_providers.vertex_canonical(gateway_providers.vertex_key(text.encode()))
    except ValueError as error:
        host = None
        assert str(error) == "not a service-account key"  # fixed: never the value
    # The launcher reads the value from its secret file, with the file rules first.
    (tmp_path / start.VERTEX_SECRET).write_bytes(text.encode())
    monkeypatch.setattr(start, "SECRETS", str(tmp_path))
    try:
        raw = start.read(start.VERTEX_SECRET)
        key = None if raw is None else start.vertex_key(raw)
        launched = None if key is None else start.vertex_canonical(key)
    except SystemExit as stopped:
        launched = None
        assert "MARKER" not in str(stopped) and "marker" not in str(stopped)
    assert (host is not None) is accepted, case
    assert launched == host, case
    if accepted:
        assert host == CANONICAL  # the seven fields, in order, whatever else the file held


def test_the_canonical_key_is_one_ascii_line_that_parses_back():
    key = gateway_providers.vertex_key(key_json(client_email="gäteway@example.com").encode())
    text = gateway_providers.vertex_canonical(key)
    assert "\n" not in text and text.isascii()
    assert json.loads(text)["private_key"] == PRIVATE  # the PEM's newlines survive, escaped
    assert list(json.loads(text)) == list(gateway_providers.VERTEX_KEY_FIELDS)


def test_non_utf8_bytes_are_not_a_key():
    with pytest.raises(ValueError, match="not a service-account key"):
        gateway_providers.vertex_key(b'{"type": "service_account", "x": "\xff"}')
    with pytest.raises(ValueError):
        gateway_providers.vertex_key(None)


# --- the launcher ------------------------------------------------------------------------------


def _main(start, tmp_path, monkeypatch, config, key=None, **secrets):
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    values = {"master-key": "sk-1", "database-url": "postgresql://x", **secrets}
    if key is not None:
        values[start.VERTEX_SECRET] = key
    for name, value in values.items():
        (secret_dir / name).write_text(value + "\n")
    path = tmp_path / "config"
    path.write_text(config)
    run = tmp_path / "run"
    run.mkdir()
    monkeypatch.setattr(start, "SECRETS", str(secret_dir))
    monkeypatch.setattr(start, "RUN_DIR", str(run))
    monkeypatch.setattr(start.load_config, "__defaults__", (str(path),))
    monkeypatch.setattr(start.write_config, "__defaults__", (str(run),))
    calls = []
    monkeypatch.setattr(start.os, "execve", lambda *args: calls.append(args))
    monkeypatch.setattr(
        start.os, "environ", {"PATH": "/bin", "GOOGLE_APPLICATION_CREDENTIALS": "/k"}
    )
    start.main()
    return calls


def test_the_launcher_hands_litellm_the_canonical_key(start, tmp_path, monkeypatch, capsys):
    ((binary, argv, environment),) = _main(
        start, tmp_path, monkeypatch, config_text(), key_json(extra="dropped")
    )
    assert binary == "/app/.venv/bin/litellm"
    assert environment[KEY_NAME] == CANONICAL
    assert "extra" not in environment[KEY_NAME] and "auth_uri" not in environment[KEY_NAME]
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in environment
    assert environment["NO_GCE_CHECK"] == "true"
    with open(argv[2]) as handle:  # the reviewed config plus the #1105 filter, nothing else
        launched = yaml.safe_load(handle)
    assert launched["model_list"] == [entry()]
    assert launched["litellm_settings"] == {"callbacks": [start.FILTER]}
    assert "MARKER" not in open(argv[2]).read()  # references, never the key
    assert "no provider key" not in capsys.readouterr().err  # a Vertex key is a provider key


@pytest.mark.parametrize(
    "case", sorted(name for name, (_, ok) in KEY_CASES.items() if not ok and "\0" not in _)
)
def test_the_launcher_stops_on_a_key_it_does_not_accept(start, tmp_path, monkeypatch, case):
    text = KEY_CASES[case][0]
    with pytest.raises(SystemExit) as caught:
        _main(start, tmp_path, monkeypatch, config_text(), text)
    message = str(caught.value)
    assert message.startswith("litellm launcher: ")
    assert "vertex-service-account-json" in message or KEY_NAME in message
    for leaked in ("MARKER", "marker", "attacker", "authorized_user", "external_account"):
        assert leaked not in message


def test_the_launcher_stops_when_the_config_names_another_project(start, tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as caught:
        _main(start, tmp_path, monkeypatch, config_text(), key_json(project_id="other-project-9"))
    assert str(caught.value) == (
        "litellm launcher: a vertex_ai entry's vertex_project is not the service-account "
        "key's project"
    )
    # One entry on the key's project and one off it: still stopped.
    (tmp_path / "b").mkdir()
    both = config_text(entry(name="a"), entry(name="b", vertex_project="other-project-9"))
    with pytest.raises(SystemExit, match="vertex_project"):
        _main(start, tmp_path / "b", monkeypatch, both, key_json())


@pytest.mark.parametrize(
    "changes",
    [
        {"vertex_credentials": None},  # would fall back to application default credentials
        {"vertex_credentials": "/run/secrets/vertex-service-account-json"},
        {"vertex_credentials": "os.environ/GOOGLE_APPLICATION_CREDENTIALS"},
        {"vertex_project": None},
        {"vertex_project": 7},
    ],
)
def test_the_launcher_stops_on_a_vertex_entry_that_skips_the_key(
    start, tmp_path, monkeypatch, changes
):
    params = {k: v for k, v in {**VERTEX, **changes}.items() if v is not None}
    with pytest.raises(SystemExit, match="vertex"):
        _main(start, tmp_path, monkeypatch, config_text(entry(params)), key_json())


def test_the_launcher_stops_when_the_key_was_not_imported(start, tmp_path, monkeypatch):
    with pytest.raises(SystemExit) as caught:
        _main(start, tmp_path, monkeypatch, config_text())
    assert f"needs {KEY_NAME}, which this gateway did not import" in str(caught.value)
    # An entry that does not even reference the key, with no key: stopped too.
    (tmp_path / "b").mkdir()
    bare = config_text(entry({"model": "vertex_ai/gemini-3.8-flash"}))
    with pytest.raises(SystemExit, match="vertex_credentials"):
        _main(start, tmp_path / "b", monkeypatch, bare)


def test_the_launcher_treats_vertex_ai_beta_as_vertex(start, tmp_path, monkeypatch):
    """The review refuses the prefix; if it ever reached the launcher it is held to the same
    rules, not passed to a default credential chain."""
    beta = config_text(entry({"model": "vertex_ai_beta/gemini-3.8-flash"}))
    with pytest.raises(SystemExit, match="vertex_credentials"):
        _main(start, tmp_path, monkeypatch, beta, key_json())


def test_a_value_that_is_not_a_key_never_reaches_litellm_when_unused(start, tmp_path, monkeypatch):
    """A stack that does not use Vertex still starts with a stale or wrong value stored, and
    LiteLLM does not get it."""
    gemini = config_text(
        {
            "model_name": "m",
            "litellm_params": {"model": "gemini/x", "api_key": "os.environ/GEMINI_API_KEY"},
        }
    )
    ((_, _, environment),) = _main(
        start, tmp_path, monkeypatch, gemini, key_json(type="authorized_user"),
        **{"gemini-api-key": "g"},
    )  # fmt: skip
    assert KEY_NAME not in environment and environment["GEMINI_API_KEY"] == "g"
    # Nor a good key that no entry reads.
    (tmp_path / "b").mkdir()
    ((_, _, environment),) = _main(
        start, tmp_path / "b", monkeypatch, gemini, key_json(), **{"gemini-api-key": "g"}
    )
    assert KEY_NAME not in environment


def test_only_a_vertex_entrys_credentials_may_read_the_key(start, tmp_path, monkeypatch):
    reference = f"os.environ/{KEY_NAME}"
    gemini = {"model": "gemini/x", "api_key": reference}
    cases = (
        config_text({"model_name": "g", "litellm_params": gemini}),
        config_text(entry(), {"model_name": "g", "litellm_params": gemini}),
        config_text(entry(extra=reference)),
        config_text(entry(), general_settings={"master_key": reference}),
        config_text(entry(vertex_credentials=reference + "_2")),
    )
    for index, config in enumerate(cases):
        directory = tmp_path / str(index)
        directory.mkdir()
        with pytest.raises(SystemExit) as caught:
            _main(start, directory, monkeypatch, config, key_json())
        assert "vertex" in str(caught.value), index


def test_a_bad_key_named_outside_a_vertex_entry_still_stops(start, tmp_path, monkeypatch):
    odd = config_text(
        {
            "model_name": "m",
            "litellm_params": {"model": "gemini/x", "note": f"os.environ/{KEY_NAME}"},
        }
    )
    with pytest.raises(SystemExit, match="not a service-account key"):
        _main(start, tmp_path, monkeypatch, odd, key_json(type="authorized_user"))


def test_the_launcher_keeps_the_earlier_rules(start, tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="callbacks"):
        _main(
            start, tmp_path, monkeypatch,
            config_text(litellm_settings={"callbacks": ["x.y"]}), key_json(),
        )  # fmt: skip
    (tmp_path / "b").mkdir()
    with pytest.raises(SystemExit, match="gateway config"):
        _main(start, tmp_path / "b", monkeypatch, "model_list: [unclosed\n", key_json())
    (tmp_path / "c").mkdir()
    bedrock = config_text(entry({"model": "bedrock/x", "k": "os.environ/BEDROCK_ACCESS_KEY_ID"}))
    with pytest.raises(SystemExit, match="BEDROCK_ACCESS_KEY_ID, which this gateway did not"):
        _main(start, tmp_path / "c", monkeypatch, bedrock, key_json())


# --- services up on the real built-in (fake Docker) ---------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """The `new agent --model vertex` starter: the real built-in service/litellm@1.5.0."""
    from agentbreeder.operator_config import CONFIG_ENV
    from agentbreeder.services.state import ROOT_ENV

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    for name in gateway_providers.GATEWAY_ONLY:
        monkeypatch.delenv(name, raising=False)
    plan = newagent.plan(
        "hello",
        "vertex",
        tmp_path / "hello",
        today=TODAY,
        options={"vertex_project": PROJECT, "vertex_location": "us-central1"},
    )
    newagent.create(plan)
    return tmp_path / "hello"


def _up(project, **options):
    from test_services_engine import FakeDocker, engine

    grants, docker = [], FakeDocker()
    instance = engine(project, docker)
    try:
        result = instance.up("dev", confirm=lambda grant: grants.append(grant) or True, **options)
        stored = instance.state.read_secret("litellm", "vertex-service-account-json")
    finally:
        instance.close()
    return result, grants, stored, docker


def test_up_needs_the_key_before_any_container(project):
    from test_services_engine import FakeDocker, engine

    docker = FakeDocker()
    instance = engine(project, docker)
    try:
        with pytest.raises(ServiceError) as caught:
            instance.up("dev", confirm=lambda grant: True)
    finally:
        instance.close()
    assert caught.value.code == "MODEL_CREDENTIAL_MISSING"
    assert f"model alias chat needs {KEY_NAME}" in str(caught.value)
    assert not [event for event in docker.events if event[0] == "up"]


@pytest.mark.parametrize(
    "case",
    sorted(name for name, (text, ok) in KEY_CASES.items() if not ok and text and "\0" not in text),
)
def test_up_refuses_a_value_that_is_not_a_key(project, monkeypatch, case):
    monkeypatch.setenv(KEY_NAME, KEY_CASES[case][0])
    with pytest.raises(ServiceError) as caught:
        _up(project)
    assert caught.value.code == "VERTEX_KEY_INVALID"
    for leaked in ("MARKER", "marker", "attacker", "authorized_user"):
        assert leaked not in str(caught.value)


def test_up_binds_the_project_to_the_key(project, monkeypatch):
    monkeypatch.setenv(KEY_NAME, key_json(project_id="other-project-9"))
    with pytest.raises(ServiceError) as caught:
        _up(project)
    assert caught.value.code == "VERTEX_PROJECT_MISMATCH"
    assert "other-project-9" not in str(caught.value)


def test_up_with_the_key_shows_the_route_and_stores_it(project, monkeypatch):
    from agentbreeder.services.plan import grant_text

    monkeypatch.setenv(KEY_NAME, key_json())
    result, grants, stored, docker = _up(project)
    assert result["services"]["litellm"] == "created"
    label = f"vertex ({KEY_NAME}, project {PROJECT}, us-central1)"
    section = grants[0]["gateway_config"]
    assert section["providers"] == {"chat": label, "judge": label}
    assert section["routes"]["chat"] == {
        "provider": "vertex",
        "project": PROJECT,
        "location": "us-central1",
    }
    text = grant_text(grants[0])
    assert f"chat: {label}" in text
    assert "can reach the host" not in text  # no Ollama entry: no host mapping
    assert "MARKER" not in text and "marker" not in json.dumps(grants[0])
    assert stored == key_json().encode()  # the launcher canonicalises what it reads
    compose = yaml.safe_load(docker_compose(project))
    assert "extra_hosts" not in compose["services"]["litellm"]
    assert "MARKER" not in json.dumps(compose) and "marker" not in json.dumps(compose)

    # Stored: a later run without the variable still has the key (§4.4), and the same grant.
    monkeypatch.delenv(KEY_NAME)
    _, again, stored_again, _ = _up(project)
    assert stored_again == stored and again == []  # the same grant: not asked again


def docker_compose(project) -> str:
    from test_services_engine import FakeDocker, engine

    instance = engine(project, FakeDocker())
    try:
        return instance.state.compose_path.read_text()
    finally:
        instance.close()


def test_up_reads_the_key_from_an_env_file_line(project, tmp_path):
    """An env file is read line by line: the one-line key works bare and single-quoted."""
    for index, line in enumerate((f"{KEY_NAME}={key_json()}", f"{KEY_NAME}='{key_json()}'")):
        env_file = tmp_path / f"keys-{index}.env"
        env_file.write_text(line + "\n")
        env_file.chmod(0o600)
        assert env_import.parse_env_file(env_file)[KEY_NAME] == key_json()
    _, grants, stored, _ = _up(project, env_file=env_file)
    assert stored == key_json().encode() and grants


def test_a_project_copy_of_the_gateway_may_not_import_the_key(project, monkeypatch):
    """A project registry version named service/litellm@1.5.0 is project origin (§3.5)."""
    shutil.copytree(V15, project / "registry/service/litellm/1.5.0")
    (project / "agent.lock").unlink(missing_ok=True)
    monkeypatch.setenv(KEY_NAME, key_json())
    with pytest.raises(ServiceError) as caught:
        _up(project)
    assert caught.value.code == "IMPORT_GATEWAY_ONLY"


def test_nothing_else_imports_the_key(tmp_path, monkeypatch):
    monkeypatch.setenv(KEY_NAME, key_json())
    assert env_import.denied(KEY_NAME)
    with pytest.raises(env_import.EnvImportError) as caught:
        env_import.lookup(KEY_NAME)
    assert caught.value.code == "IMPORT_DENIED"
    assert env_import.lookup(KEY_NAME, allow_gateway_only=True) == key_json()
    code = cli.main(["secret", "set", "X", "--state-dir", str(tmp_path), "--from-env", KEY_NAME])
    assert code != 0


# --- new agent --model vertex -------------------------------------------------------------------

OPTIONS = {"vertex_project": PROJECT, "vertex_location": "us-central1"}
ARGV = ["--vertex-project", PROJECT, "--vertex-location", "us-central1"]


def test_vertex_is_a_starter_but_not_offered_elsewhere():
    assert sorted(set(newagent.STARTERS) - set(newagent.PROVIDERS)) == ["vertex"]
    assert newagent.STARTERS["vertex"].verified is False  # config-only until a recorded run
    assert all(not provider.options for provider in newagent.PROVIDERS.values())
    assert newagent.gateway_for("vertex_ai/gemini-3.8-flash") == newagent.VERTEX_GATEWAY == REF
    table = gateway_providers.PROVIDERS["vertex"]
    assert {param for param, _ in newagent.STARTERS["vertex"].options} == set(table.required)
    assert newagent.STARTERS["vertex"].credential in table.credentials
    assert table.model.fullmatch(newagent.STARTERS["vertex"].model)


def test_new_agent_vertex_writes_a_stack_on_1_5_0(tmp_path):
    plan = newagent.plan("hello", "vertex", tmp_path / "hello", today=TODAY, options=OPTIONS)
    assert plan.model_id == "vertex_ai/gemini-3.8-flash"
    stack = yaml.safe_load(plan.files["stack.yaml"])
    assert stack["services"]["litellm"]["ref"] == REF
    assert f"{KEY_NAME}=..." in plan.files["stack.yaml"]
    config = yaml.safe_load(plan.files["stack/litellm/config.yaml"])
    assert [item["litellm_params"] for item in config["model_list"]] == [VERTEX, VERTEX]
    summary = plan.summary(dry_run=True)
    assert summary["verified"] is False
    assert (summary["key"], summary["keys"]) == (KEY_NAME, [KEY_NAME])
    assert summary["options"] == OPTIONS
    newagent.create(plan)
    result = gateway_keys.config_review(
        tmp_path / "hello/stack/litellm/config.yaml", gateway_providers.builtin_caps(REF)
    )
    assert result.aliases == {"chat", "judge"}
    assert (tmp_path / "hello/agent.lock").is_file()


def test_other_starters_have_no_options_key(tmp_path):
    summary = newagent.plan("hello", "gemini", tmp_path / "h", today=TODAY).summary(dry_run=True)
    assert "options" not in summary


@pytest.mark.parametrize(
    "options,code",
    [
        ({}, "MODEL_OPTION_REQUIRED"),
        ({"vertex_project": PROJECT}, "MODEL_OPTION_REQUIRED"),
        ({"vertex_location": "global"}, "MODEL_OPTION_REQUIRED"),
        ({**OPTIONS, "vertex_project": "Bad_Project"}, "MODEL_OPTION_INVALID"),
        ({**OPTIONS, "vertex_project": "evil\x1b[31m-project"}, "MODEL_OPTION_INVALID"),
        ({**OPTIONS, "vertex_project": ""}, "MODEL_OPTION_INVALID"),
        ({**OPTIONS, "vertex_location": "us-central1\nx: y"}, "MODEL_OPTION_INVALID"),
        (
            {**OPTIONS, "vertex_location": "us-central1, api_base: http://x"},
            "MODEL_OPTION_INVALID",
        ),
        ({**OPTIONS, "api_base": "http://x"}, "MODEL_OPTION_UNSUPPORTED"),
    ],
)
def test_vertex_options_are_required_and_checked(tmp_path, options, code):
    with pytest.raises(newagent.NewAgentError) as caught:
        newagent.plan("hello", "vertex", tmp_path / "hello", today=TODAY, options=options)
    assert caught.value.code == code
    assert "evil" not in str(caught.value) and "http://x" not in str(caught.value)


def test_options_go_with_vertex_only(tmp_path):
    with pytest.raises(newagent.NewAgentError) as caught:
        newagent.plan("hello", "gemini", tmp_path / "hello", today=TODAY, options=OPTIONS)
    assert caught.value.code == "MODEL_OPTION_UNSUPPORTED"
    # Unset flags (None) are no options at all.
    unset = {"vertex_project": None, "vertex_location": None}
    assert newagent.plan("hello", "gemini", tmp_path / "hello", today=TODAY, options=unset)


@pytest.mark.parametrize(
    "model_id,ok",
    [
        ("vertex_ai/gemini-2.5-flash", True),
        ("vertex_ai/gemini-3.8-flash", True),
        ("vertex_ai/gemini-embedding-2", False),  # `chat` and `judge` need a chat model
        ("vertex_ai/text-embedding-005", False),
        ("vertex_ai/text-multilingual-embedding-002", False),
        ("vertex_ai/claude-3-5-sonnet", False),
        ("vertex_ai/openai/gpt-4o", False),
        ("vertex_ai/1234567890", False),
        ("vertex_ai/gemini-3.8-flash@x", False),
        ("gemini/gemini-3.8-flash", False),
        ("vertex_ai/", False),
    ],
)
def test_vertex_model_ids(tmp_path, model_id, ok):
    if ok:
        plan = newagent.plan(
            "hello", "vertex", tmp_path / "h", today=TODAY, model_id=model_id, options=OPTIONS
        )
        assert plan.model_id == model_id
        return
    with pytest.raises(newagent.NewAgentError) as caught:
        newagent.plan(
            "hello", "vertex", tmp_path / "h", today=TODAY, model_id=model_id, options=OPTIONS
        )
    assert caught.value.code == "MODEL_ID_INVALID"
    rest = model_id.partition("/")[2]
    assert not rest or rest not in str(caught.value)  # the given id is never echoed


def test_the_other_providers_routes_are_what_they_were():
    """The key-only and Ollama starters' litellm_params text, unchanged by `options`."""
    assert {name: provider.route() for name, provider in newagent.PROVIDERS.items()} == {
        "anthropic": "api_key: os.environ/ANTHROPIC_API_KEY",
        "gemini": "api_key: os.environ/GEMINI_API_KEY",
        "openai": "api_key: os.environ/OPENAI_API_KEY",
        "openrouter": "api_key: os.environ/OPENROUTER_API_KEY",
        "ollama": "api_base: http://host.docker.internal:11434",
    }
    assert newagent.STARTERS["vertex"].route(OPTIONS) == (
        f"vertex_credentials: os.environ/{KEY_NAME}, "
        f'vertex_project: "{PROJECT}", vertex_location: "us-central1"'
    )


def test_the_cli_writes_the_vertex_starter_and_says_config_only(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    target = str(tmp_path / "h")
    argv = ["new", "agent", "hello", "--model", "vertex", *ARGV, "--dir", target]
    assert cli.main([*argv, "--dry-run", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["verified"] is False and summary["options"] == OPTIONS
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "vertex is config-only" in out and "not been run against Vertex AI" in out
    assert f"{KEY_NAME}='<the service-account key as one line>'" in out
    assert "jq -c . key.json" in out
    assert "json_schema" not in out  # anthropic's reason, not Vertex's


@pytest.mark.parametrize(
    "argv,code",
    [
        (["--model", "vertex"], "MODEL_OPTION_REQUIRED"),
        (["--model", "vertex", "--vertex-project", PROJECT], "MODEL_OPTION_REQUIRED"),
        (["--model", "gemini", *ARGV], "MODEL_OPTION_UNSUPPORTED"),
        (["--model", "ollama", "--vertex-location", "global"], "MODEL_OPTION_UNSUPPORTED"),
        (["--model", "vertex", "--vertex-project", "", "--vertex-location", "global"],
         "MODEL_OPTION_INVALID"),
    ],
)  # fmt: skip
def test_the_cli_refuses_wrong_vertex_flags(tmp_path, capsys, monkeypatch, argv, code):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    full = ["new", "agent", "hello", *argv, "--dir", str(tmp_path / "h"), "--json"]
    assert cli.main(full) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == code
    assert not (tmp_path / "h").exists()


def test_stack_for_and_from_spec_do_not_offer_vertex(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = tmp_path / "p"
    newagent.create(newagent.plan("hello", "gemini", project, today=TODAY))
    agent = str(project / "agent.yaml")
    base = ["new", "agent", "hello", "--json"]
    assert cli.main([*base, "--model", "vertex", "--stack-for", agent]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "MODEL_UNSUPPORTED" and "vertex" not in error["message"]
    assert cli.main([*base, "--model", "vertex", *ARGV, "--stack-for", agent]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "USAGE"
    spec = tmp_path / "spec.json"
    spec.write_text("{}")
    spec.chmod(0o600)
    assert cli.main([*base, "--from-spec", str(spec), "--vertex-project", PROJECT]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "USAGE"

    from agentbreeder import stackfor
    from agentbreeder.center import onboard

    with pytest.raises(newagent.NewAgentError) as caught:
        stackfor.plan("hello", "vertex", project / "agent.yaml", today=TODAY)
    assert caught.value.code == "MODEL_UNSUPPORTED"
    with pytest.raises(newagent.NewAgentError):
        newagent.check_inputs("hello", "vertex")  # `--from-spec` and `--stack-for` use this
    assert "vertex" not in onboard.PROVIDERS


def test_next_asks_for_the_key(project, tmp_path):
    from test_new_agent_providers import _probe

    from agentbreeder import guide_checks

    def keys(environ=None):
        steps = guide_checks.compute(project, probe=_probe(tmp_path, environ)).steps
        return next(step for step in steps if step.id == "keys")

    step = keys()
    assert step.state != "done" and step.data.missing == [KEY_NAME]
    assert keys({KEY_NAME: key_json()}).state == "done"


# --- services logs: the key's private parts and derived tokens (§5.1) ---------------------------


def test_logs_redact_the_keys_private_parts_not_its_public_ones():
    from agentbreeder import redaction

    redactor = redaction.Redactor([key_json().encode()])
    line = "google.auth: bad key MARKERPRIVATEKEYBODY (key id markerprivatekeyid)"
    assert redactor(line) == "google.auth: bad key [redacted] (key id [redacted])"
    assert "MARKER" not in redactor("credentials=" + key_json())
    assert "MARKER" not in redactor("pem: " + PRIVATE)
    assert "MARKER" not in redactor("escaped: " + json.dumps(PRIVATE))
    public = f"project {PROJECT}, token endpoint https://oauth2.googleapis.com/token"
    assert redactor(public) == public  # an error stays readable


@pytest.mark.parametrize(
    "value",
    [
        '{"private_key": 7}',
        '{"private_key": ["x"]}',
        '["private_key"]',
        '{"private_key"',
        '{"other": 1}',
    ],
)
def test_a_value_that_only_looks_like_a_key_is_redacted_as_before(value):
    from agentbreeder import redaction

    assert redaction.Redactor([value])(f"saw {value} here") == "saw [redacted] here"


def test_logs_redact_derived_credential_shapes_whatever_the_values():
    from agentbreeder import redaction

    none = redaction.Redactor([])
    jwt = "eyJ" + "a" * 20 + ".eyJ" + "b" * 30 + "." + "c" * 40
    assert none(f"assertion={jwt} sent") == "assertion=[redacted] sent"
    assert none("Authorization: Bearer " + "t" * 30) == "Authorization: Bearer [redacted]"
    assert none("authorization: bearer\t" + "t" * 30 + "==") == "authorization: bearer\t[redacted]"
    assert none("AUTHORIZATION: BEARER " + "t" * 30) == "AUTHORIZATION: BEARER [redacted]"
    assert none("auth BeArEr " + "t" * 30) == "auth BeArEr [redacted]"
    assert none("token ya29." + "A" * 40 + " used") == "token [redacted] used"
    assert none("key:\n" + PRIVATE + "end") == "key:\n[redacted]\nend"
    rsa = PRIVATE.replace("PRIVATE KEY", "RSA PRIVATE KEY")
    assert "MARKER" not in none(rsa) and "MARKER" not in none(json.dumps(rsa))
    for unchanged in (
        "the bearer of this message is trusted",
        "Bearer short",
        "eyJhbGciOiJSUzI1NiJ9 alone is a header, not a token",
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
        "-----BEGIN PRIVATE KEY----- with no end",
        "ya29.short",
    ):
        assert none(unchanged) == unchanged


def test_the_shapes_stay_fast_on_hostile_text():
    import time

    from agentbreeder import redaction

    none = redaction.Redactor([])
    for hostile in (
        "eyJ" * 350000,  # 1 MB: quadratic when every `eyJ` started its own scan
        "eyJabcdefgh" * 100000,
        "eyJabcdefgh." * 90000,
        "eyJabcdefgh.eyJabcdefgh." * 45000,
        "-----BEGIN PRIVATE KEY-----" * 40000,
        "-----BEGIN PRIVATE KEY-----" + "A" * 1000000,
        "Bearer " * 150000,
        "bearer " + "a" * 1000000,
        "ya29." * 200000,
    ):
        started = time.monotonic()
        none(hostile)
        assert time.monotonic() - started < 5, hostile[:40]


@pytest.mark.parametrize(
    "changes",
    [
        {"vertex_location": "evil.example/x"},
        {"vertex_location": "us-central1.attacker.example"},
        {"vertex_location": None},
        {"api_base": "https://attacker.example"},
        {"base_url": "https://attacker.example"},
        {"custom_llm_provider": "openai"},
        {"vertex_ai_location": "us-central1"},
        {"vertex_ai_project": "test-project-1"},
        {"vertex_ai_credentials": f"os.environ/{KEY_NAME}"},
        {"api_key": "anything"},
        {"extra_headers": {"x": "y"}},
        {"litellm_credential_name": "other"},
    ],
)
def test_the_launcher_checks_where_a_vertex_entry_is_sent(start, tmp_path, monkeypatch, changes):
    """Defence in depth: the host review already refuses these; the file LiteLLM runs is held to
    the same location rule and may not redirect the entry (review of #1025 V1)."""
    params = {k: v for k, v in {**VERTEX, **changes}.items() if v is not None}
    with pytest.raises(SystemExit, match="vertex"):
        _main(start, tmp_path, monkeypatch, config_text(entry(params)), key_json())


def test_the_launcher_location_rule_matches_the_review(start):
    assert start.VERTEX_LOCATION.pattern == gateway_providers.VERTEX_LOCATION.pattern
    assert start.VERTEX_PARAMS == gateway_providers.PROVIDERS["vertex"].params


def test_the_launcher_accepts_the_tuning_params_the_review_allows(start, tmp_path, monkeypatch):
    params = {**VERTEX, "rpm": 10, "timeout": 30, "max_tokens": 256, "temperature": 0.2}
    _main(start, tmp_path, monkeypatch, config_text(entry(params)), key_json())
