"""#1025 slice O1: Ollama through `service/litellm@1.2.0`.

The schema's `reaches_host`, its origin check in `services/lock.py`, the host mapping compose
adds only for a reviewed Ollama entry, the grant line, the 1.2.0 launcher (environment
allow-list, forced metadata variables, the fail-closed config parse) and `new agent --model
ollama`. The pinned-image half (the real image, Ollama on the host) is
tests/test_ollama_gateway_docker.py.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
from datetime import date

import pytest
import yaml
from conftest import GATEWAY_CAPS

from agentbreeder import cli, gateway_keys, gateway_providers, newagent, registry, stackfor
from agentbreeder.lock import BUILTIN_LABEL
from agentbreeder.services.lock import check_reaches_host
from agentbreeder.services.schema import ServiceError, ServiceSpec, load_service

V12 = registry.BUILTIN_ROOT / "service/litellm/1.2.0"
OLLAMA = {"model": "ollama_chat/llama3.2:3b", "api_base": "http://host.docker.internal:11434"}
TODAY = date(2026, 11, 16)


def launcher():
    spec = importlib.util.spec_from_file_location("litellm_start_120", V12 / "files/start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gateway_spec(**changes) -> ServiceSpec:
    data = yaml.safe_load((V12 / "service.yaml").read_text())
    data.update(changes)
    return ServiceSpec.model_validate(data)


# --- schema and lock ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [[True], ["11434"], [0], [65536], [11434, 11435], [1.5]])
def test_reaches_host_is_at_most_one_real_port(value):
    with pytest.raises(ValueError):
        gateway_spec(reaches_host=value)


def test_only_1_2_0_reaches_the_host_and_its_caps_say_so():
    for version, ports in (("1.0.0", []), ("1.1.0", []), ("1.2.0", [11434])):
        spec = load_service(registry.BUILTIN_ROOT / f"service/litellm/{version}/service.yaml")
        assert spec.reaches_host == ports
    caps = gateway_providers.builtin_caps("service/litellm@1.2.0")
    assert caps.reaches_host == {11434}
    assert caps.imports == GATEWAY_CAPS.imports  # no gateway-only import: Ollama needs none


def test_the_builtin_gateway_may_reach_the_host():
    check_reaches_host({"litellm": gateway_spec()}, {"litellm": BUILTIN_LABEL}, {})


@pytest.mark.parametrize(
    "origin,relays,changes",
    [
        ("/repo/registry", {}, {}),  # a project version named service/litellm
        (BUILTIN_LABEL, {"litellm": "github"}, {}),  # a relay is recorded as built-in
        (BUILTIN_LABEL, {}, {"provides": None}),  # a built-in service that is not the gateway
    ],
)
def test_no_other_service_may_reach_the_host(origin, relays, changes):
    with pytest.raises(ServiceError) as caught:
        check_reaches_host({"litellm": gateway_spec(**changes)}, {"litellm": origin}, relays)
    assert caught.value.code == "REACHES_HOST_REFUSED"


def test_an_ollama_entry_needs_a_gateway_that_reaches_the_host(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"model_list": [{"model_name": "m", "litellm_params": OLLAMA}]})
    )
    with pytest.raises(gateway_keys.GatewayError) as caught:
        gateway_keys.config_review(path, gateway_providers.builtin_caps("service/litellm@1.1.0"))
    assert caught.value.code == "GATEWAY_PROVIDER_UNSUPPORTED"
    review = gateway_keys.config_review(
        path, gateway_providers.builtin_caps("service/litellm@1.2.0")
    )
    assert review.providers == {"m": "ollama (no key → host port 11434)"}
    assert review.required_env == {"m": ()}


# --- services up: the host mapping and the grant (fake Docker) ---------------------------------


@pytest.fixture
def ollama_stack(tmp_path, monkeypatch):
    """test_services_engine's project, its gateway a fixture built-in declaring
    reaches_host: [11434], serving m1 from Ollama."""
    from test_services_engine import FAKEGW, write_project

    from agentbreeder.operator_config import CONFIG_ENV
    from agentbreeder.services.state import ROOT_ENV

    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("ECHO_CANARY", "canary")
    monkeypatch.delenv(CONFIG_ENV, raising=False)
    spec = copy.deepcopy(FAKEGW)
    spec["reaches_host"] = [11434]
    project = write_project(tmp_path / "project", gateway=spec)
    builtin = tmp_path / "builtin"
    shutil.copytree(registry.BUILTIN_ROOT, builtin)
    shutil.copytree(project / "registry/service/fakegw", builtin / "service/fakegw")
    shutil.rmtree(project / "registry/service/fakegw")
    monkeypatch.setattr(registry, "BUILTIN_ROOT", builtin)
    (project / "gw/config.yaml").write_text(
        yaml.safe_dump({"model_list": [{"model_name": "m1", "litellm_params": OLLAMA}]})
    )
    return project


def _up(project):
    from test_services_engine import FakeDocker, engine

    grants = []
    instance = engine(project, FakeDocker())
    try:
        instance.up("dev", confirm=lambda grant: grants.append(grant) or True)
        compose = yaml.safe_load(instance.state.compose_path.read_text())
    finally:
        instance.close()
    return grants[0], compose["services"]


def test_up_maps_the_host_for_an_ollama_gateway_and_says_so(ollama_stack):
    from agentbreeder.services.plan import grant_text

    grant, services = _up(ollama_stack)
    assert services["gateway"]["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert all("extra_hosts" not in d for name, d in services.items() if name != "gateway")
    assert grant["gateway_config"]["providers"] == {"m1": "ollama (no key → host port 11434)"}
    assert grant["gateway_config"]["routes"] == {"m1": {"provider": "ollama", "host_port": 11434}}
    assert "litellm can reach the host: Ollama at host.docker.internal:11434" in grant_text(grant)


def test_no_host_mapping_without_an_ollama_entry(ollama_stack):
    from agentbreeder.services.plan import grant_text

    (ollama_stack / "gw/config.yaml").write_text(
        yaml.safe_dump(
            {"model_list": [{"model_name": "m1", "litellm_params": {"model": "gemini/x"}}]}
        )
    )
    grant, services = _up(ollama_stack)
    assert "extra_hosts" not in services["gateway"]
    assert set(grant["gateway_config"]) == {"sha256", "providers"}  # digest as before
    assert "can reach the host" not in grant_text(grant)


def test_a_project_gateway_may_not_reach_the_host(ollama_stack):
    from test_services_engine import up

    shutil.copytree(
        registry.BUILTIN_ROOT / "service/fakegw", ollama_stack / "registry/service/fakegw"
    )
    shutil.rmtree(registry.BUILTIN_ROOT / "service/fakegw")
    with pytest.raises(ServiceError) as caught:
        up(ollama_stack)
    assert caught.value.code == "REACHES_HOST_REFUSED"


# --- the 1.2.0 launcher ------------------------------------------------------------------------


def test_the_launcher_lists_match_service_yaml_and_the_provider_table():
    start = launcher()
    service = yaml.safe_load((V12 / "service.yaml").read_text())
    assert set(start.SERVICE_ENV) == set(service["env"]) | {"AGENTBREEDER_SERVICE"}
    assert len(start.SERVICE_ENV) == len(set(start.SERVICE_ENV))
    assert {k: service["env"][k] for k in start.FORCED} == start.FORCED
    assert set(start.GATEWAY_ONLY) == gateway_providers.GATEWAY_ONLY
    imports = {name: s["import"] for name, s in service["secrets"].items() if "import" in s}
    assert {n: v for n, v in start.NAMES.items() if n in imports} == imports
    assert set(start.NAMES) == set(service["secrets"]) | set(service["inputs"]) - {
        "presidio-analyzer",
        "presidio-anonymizer",
    }
    assert not any("*" in name for name in start.IMAGE_ENV)


def test_the_launcher_passes_only_allow_listed_variables():
    start = launcher()
    ambient = {
        "PATH": "/app/.venv/bin:/bin",
        "HOME": "/tmp",
        "LITELLM_LOG": "WARNING",
        "AGENTBREEDER_SERVICE": "litellm",
        "AWS_ACCESS_KEY_ID": "AKIA",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://169.254.170.2/x",
        "AWS_WEB_IDENTITY_TOKEN_FILE": "/var/run/token",
        "AWS_PROFILE": "p",
        "AWS_ENDPOINT_URL": "http://evil",
        "GOOGLE_APPLICATION_CREDENTIALS": "/k.json",
        "CLOUDSDK_CONFIG": "/c",
        "IDENTITY_ENDPOINT": "http://169.254.169.254",
        "MSI_ENDPOINT": "http://x",
        "HTTPS_PROXY": "http://proxy",
        "NO_PROXY": "*",
        "PYTHONPATH": "/evil",
        "HOSTNAME": "abc",
        "AWS_EC2_METADATA_DISABLED": "false",  # forced back to true
        "GCE_METADATA_HOST": "169.254.169.254",
    }
    environment = start.environment_for(ambient, {"LITELLM_MASTER_KEY": "sk-1"})
    assert environment == {
        "PATH": "/app/.venv/bin:/bin",
        "HOME": "/tmp",
        "LITELLM_LOG": "WARNING",
        "AGENTBREEDER_SERVICE": "litellm",
        "LITELLM_MASTER_KEY": "sk-1",
        **start.FORCED,
    }


def _main(tmp_path, monkeypatch, config, secrets=None):
    start = launcher()
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    for name, value in {
        "master-key": "sk-1",
        "database-url": "postgresql://x",
        **(secrets or {}),
    }.items():
        (secret_dir / name).write_text(value + "\n")
    path = tmp_path / "config"
    path.write_text(config)
    monkeypatch.setattr(start, "SECRETS", str(secret_dir))
    monkeypatch.setattr(start, "CONFIG", str(path))
    monkeypatch.setattr(start.load_config, "__defaults__", (str(path),))
    calls = []
    monkeypatch.setattr(start.os, "execve", lambda *args: calls.append(args))
    monkeypatch.setattr(start.os, "environ", {"PATH": "/bin", "AWS_PROFILE": "x"})
    start.main()
    return calls


OLLAMA_CONFIG = yaml.safe_dump({"model_list": [{"model_name": "m", "litellm_params": OLLAMA}]})


def test_the_launcher_execs_litellm_with_the_allow_listed_environment(
    tmp_path, monkeypatch, capsys
):
    calls = _main(tmp_path, monkeypatch, OLLAMA_CONFIG)
    ((binary, argv, environment),) = calls
    assert binary == "/app/.venv/bin/litellm"
    assert argv[1:] == ["--config", str(tmp_path / "config"), "--port", "4000"]
    assert "AWS_PROFILE" not in environment
    assert environment["LITELLM_MASTER_KEY"] == "sk-1"
    # An Ollama entry counts as a provider: no "no provider key" warning.
    assert "no provider key" not in capsys.readouterr().err


def test_the_launcher_warns_without_any_provider(tmp_path, monkeypatch, capsys):
    config = yaml.safe_dump(
        {"model_list": [{"model_name": "m", "litellm_params": {"model": "gemini/x"}}]}
    )
    assert _main(tmp_path, monkeypatch, config)
    assert "no provider key imported" in capsys.readouterr().err
    (tmp_path / "b").mkdir()
    assert _main(tmp_path / "b", monkeypatch, config, {"gemini-api-key": "g"})
    assert "no provider key" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "config",
    [
        "model_list: [unclosed\n",  # not YAML
        "- a list\n",  # not a mapping
        "\0",
    ],
)
def test_the_launcher_fails_closed_on_a_config_it_cannot_parse(tmp_path, monkeypatch, config):
    with pytest.raises(SystemExit) as caught:
        _main(tmp_path, monkeypatch, config)
    assert "gateway config" in str(caught.value)


def test_the_launcher_stops_on_an_unset_gateway_only_credential(tmp_path, monkeypatch):
    nested = {
        "model_list": [
            {
                "model_name": "v",
                "litellm_params": {
                    "model": "vertex_ai/gemini-x",
                    "extra": [{"deep": "os.environ/VERTEX_SERVICE_ACCOUNT_JSON"}],
                },
            }
        ]
    }
    with pytest.raises(SystemExit) as caught:
        _main(tmp_path, monkeypatch, yaml.safe_dump(nested))
    assert "VERTEX_SERVICE_ACCOUNT_JSON" in str(caught.value)
    assert "did not import" in str(caught.value)


def test_the_launcher_keeps_the_secret_file_rules(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="master-key is missing"):
        start = launcher()
        monkeypatch.setattr(start, "SECRETS", str(tmp_path))
        start.main()
    with pytest.raises(SystemExit, match="empty or malformed"):
        _main(tmp_path, monkeypatch, OLLAMA_CONFIG, {"gemini-api-key": "a\nb"})


# --- new agent --model ollama ------------------------------------------------------------------


def test_new_agent_ollama_writes_a_keyless_starter_on_1_2_0(tmp_path):
    plan = newagent.plan("hello", "ollama", tmp_path / "hello", today=TODAY)
    assert plan.model_id == "ollama_chat/llama3.2:3b"
    agent = yaml.safe_load(plan.files["agent.yaml"])
    assert agent["model"]["reasoning"] == "none"  # llama3.2 refuses Ollama's think option
    stack = yaml.safe_load(plan.files["stack.yaml"])
    assert stack["services"]["litellm"]["ref"] == "service/litellm@1.2.0"
    assert "--env-file" not in plan.files["stack.yaml"]
    config = yaml.safe_load(plan.files["stack/litellm/config.yaml"])
    assert [e["litellm_params"] for e in config["model_list"]] == [OLLAMA, OLLAMA]
    summary = plan.summary(dry_run=True)
    assert (summary["key"], summary["keys"]) == (None, [])
    newagent.create(plan)
    caps = gateway_providers.builtin_caps("service/litellm@1.2.0")
    review = gateway_keys.config_review(tmp_path / "hello/stack/litellm/config.yaml", caps)
    assert set(review.providers.values()) == {"ollama (no key → host port 11434)"}


def test_the_key_providers_keep_reasoning_low_and_their_gateway():
    for name, provider in newagent.PROVIDERS.items():
        if name != "ollama":
            assert (provider.reasoning, provider.gateway) == ("low", newagent.GATEWAY)
            plan = newagent.plan("hello", name, today=TODAY)
            assert "reasoning: low," in plan.files["agent.yaml"]


@pytest.mark.parametrize(
    "model_id,ok",
    [
        ("ollama_chat/qwen2.5:7b", True),
        ("ollama/llama3.2:3b", False),  # the chat route is ollama_chat
        ("gemini/x", False),
        ("ollama_chat/", False),
    ],
)
def test_ollama_model_ids(model_id, ok):
    if ok:
        assert newagent.model_id_for("ollama", model_id) == model_id
    else:
        with pytest.raises(newagent.NewAgentError) as caught:
            newagent.model_id_for("ollama", model_id)
        assert caught.value.code == "MODEL_ID_INVALID"
        assert model_id not in str(caught.value) or model_id == "ollama_chat/"


def test_check_gateway_defaults_to_the_models_gateway():
    assert newagent.gateway_for("ollama_chat/x") == "service/litellm@1.2.0"
    assert newagent.gateway_for("gemini/x") == newagent.GATEWAY
    assert newagent.gateway_for("unknown/x") == newagent.GATEWAY


def test_cli_text_for_ollama_names_the_pull_not_a_key(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    assert (
        cli.main(["new", "agent", "hello", "--model", "ollama", "--dir", str(tmp_path / "h")]) == 0
    )
    out = capsys.readouterr().out
    assert "ollama pull llama3.2:3b" in out
    assert "<your key>" not in out


def test_stack_for_ollama_writes_the_keyless_gateway(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = tmp_path / "p"
    newagent.create(newagent.plan("hello", "gemini", project, today=TODAY))
    (project / "stack.yaml").unlink()
    shutil.rmtree(project / "stack")
    plan = stackfor.plan("hello", "ollama", project / "agent.yaml", today=TODAY)
    files = plan.files
    assert "ref: service/litellm@1.2.0\n" in files[stackfor.STACK_FILE]
    assert "--env-file" not in files[stackfor.STACK_FILE]
    assert "api_base: http://host.docker.internal:11434" in files[stackfor.LITELLM_CONFIG]
    stackfor.create(plan)
    assert json.loads(json.dumps(plan.summary(dry_run=False)))["key"] is None


# --- review follow-ups -------------------------------------------------------------------------


def test_stack_for_ollama_cli_names_no_key(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    project = tmp_path / "p"
    newagent.create(newagent.plan("hello", "gemini", project, today=TODAY))
    (project / "stack.yaml").unlink()
    shutil.rmtree(project / "stack")
    agent = str(project / "agent.yaml")
    argv = ["new", "agent", "hello", "--model", "ollama", "--stack-for", agent]
    assert cli.main([*argv, "--dry-run", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert (summary["key"], summary["keys"]) == (None, [])
    assert cli.main(argv) == 0
    out = capsys.readouterr().out
    assert "None=" not in out and "<your key>" not in out
    assert "ollama pull llama3.2:3b" in out


def test_a_priced_call_is_never_hidden_as_local():
    from agentbreeder import llmcost

    rows = [
        {
            "request_id": "a",
            "metadata": {"user_api_key_alias": "agentbreeder-x"},
            "spend": 0.5,  # a provider field cannot move real spend out of the total
            "custom_llm_provider": "ollama_chat",
            "status": "success",
        }
    ]
    total = llmcost.summarize(rows, [])["total"]
    assert (total["local"], total["cost_usd"]) == (0, 0.5)


def test_next_needs_no_key_for_an_ollama_starter(tmp_path):
    from test_new_agent_providers import _probe

    from agentbreeder import guide_checks

    newagent.create(newagent.plan("hello", "ollama", tmp_path / "hello", today=TODAY))
    keys = next(
        s
        for s in guide_checks.compute(tmp_path / "hello", probe=_probe(tmp_path)).steps
        if s.id == "keys"
    )
    assert keys.state == "done" and keys.data.required == []


def test_the_host_mapping_changes_the_compose_label(ollama_stack):
    _, with_ollama = _up(ollama_stack)
    (ollama_stack / "stack.lock").unlink()  # a path param: the edit is a new lock
    (ollama_stack / "gw/config.yaml").write_text(
        yaml.safe_dump(
            {"model_list": [{"model_name": "m1", "litellm_params": {"model": "gemini/x"}}]}
        )
    )
    _, without = _up(ollama_stack)
    label = "io.agentbreeder.config"
    assert with_ollama["gateway"]["labels"][label] != without["gateway"]["labels"][label]


def test_the_launcher_refuses_a_non_utf8_secret_without_quoting_it(tmp_path, monkeypatch):
    start = launcher()
    secret_dir = tmp_path / "s"
    secret_dir.mkdir()
    (secret_dir / "master-key").write_bytes(b"sk-\xff\xfe")
    monkeypatch.setattr(start, "SECRETS", str(secret_dir))
    with pytest.raises(SystemExit) as caught:
        start.main()
    assert str(caught.value) == "litellm launcher: secret master-key is not UTF-8"


@pytest.mark.parametrize(
    "config",
    [
        "a: " + "[" * 5000 + "]" * 5000 + "\n",  # deeper than the recursion limit
        "a: '" + "x" * 300000 + "'\n",  # over the 256 KiB the review reads
    ],
)
def test_the_launcher_fails_closed_without_a_traceback(tmp_path, monkeypatch, config):
    with pytest.raises(SystemExit) as caught:
        _main(tmp_path, monkeypatch, config)
    assert str(caught.value) == "litellm launcher: the gateway config can't be read or parsed"


def test_the_launcher_refuses_a_config_that_is_not_utf8(tmp_path, monkeypatch):
    calls = []
    with pytest.raises(SystemExit) as caught:
        start = launcher()
        path = tmp_path / "config"
        path.write_bytes(b"a: \xff\n")
        monkeypatch.setattr(start.os, "execve", lambda *a: calls.append(a))
        start.load_config(str(path))
    assert str(caught.value) == "litellm launcher: the gateway config can't be read or parsed"
    assert not calls
