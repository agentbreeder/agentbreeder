"""stack.lock: service versions, file params and imports (#737)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from test_services_schema import LITELLM, PHOENIX, POSTGRES, PROXY, STACK

from agentbreeder import gateway_keys, registry
from agentbreeder.registry import RegistryError
from agentbreeder.services.lock import StackLock, lock_stack
from agentbreeder.services.schema import ServiceError, Stack

LITELLM_CONFIG = {
    "model_list": [
        {"model_name": "support-model", "litellm_params": {"model": "gemini/x"}},
        {"model_name": "support-judge", "litellm_params": {"model": "gemini/x"}},
    ]
}


def publish(root, name, spec, files=None):
    path = root / "service" / name / "1.0.0"
    path.mkdir(parents=True)
    (path / "manifest.yaml").write_text(f"kind: service\nname: {name}\nversion: 1.0.0\n")
    (path / "service.yaml").write_text(yaml.safe_dump(spec))
    for relative, content in (files or {}).items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setattr(registry, "BUILTIN_ROOT", tmp_path / "builtin")
    project = tmp_path / "project"
    root = project / "registry"
    publish(root, "postgres-pgvector", POSTGRES, {"files/init.py": "print('init')\n"})
    publish(root, "litellm", LITELLM)
    publish(root, "phoenix", PHOENIX)
    publish(root, "northwind-proxy", PROXY, {"Dockerfile": "FROM scratch\n"})
    (project / "db").mkdir()
    (project / "db/seed.sql").write_text("CREATE TABLE t (id int);\n")
    (project / "litellm").mkdir()
    (project / "litellm/config.yaml").write_text(yaml.safe_dump(LITELLM_CONFIG))
    return project


def run(project, tmp_path, mode="plain", lock=None, stack_data=None):
    lock = lock if lock is not None else StackLock()
    context = tmp_path / f"ctx-{mode}-{len(list(tmp_path.glob('ctx-*')))}"
    stack = Stack.model_validate(stack_data or STACK)
    locked = lock_stack(
        stack,
        project_dir=project,
        search=registry.roots(project),
        lock=lock,
        context=context,
        mode=mode,
    )
    return locked, lock


def test_lock_records_services_files_and_imports(project, tmp_path):
    locked, lock = run(project, tmp_path)
    assert locked.changed
    assert set(lock.entries) == {
        "service/postgres-pgvector@1.0.0",
        "service/litellm@1.0.0",
        "service/phoenix@1.0.0",
        "service/northwind-proxy@1.0.0",
        "path:db/seed.sql",
        "path:litellm/config.yaml",
    }
    assert lock.imports == {"litellm": ["GEMINI_API_KEY", "OPENROUTER_API_KEY"]}
    assert locked.files["postgres"]["init_sql"].read_text().startswith("CREATE TABLE")
    assert locked.files["litellm"]["config"].name == "config.yaml"
    assert (locked.service_dirs["postgres"] / "files/init.py").exists()
    assert locked.warnings == [
        "gateway config serves aliases the environment doesn't grant: ['support-judge']"
    ]

    path = tmp_path / "stack.lock"
    lock.save(path)
    again, _ = run(project, tmp_path, "locked", StackLock.load(path))
    assert not again.changed
    assert json.loads(path.read_text())["imports"]["litellm"] == [
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
    ]


def test_locked_mode_refuses_changes(project, tmp_path):
    _, lock = run(project, tmp_path)
    path = tmp_path / "stack.lock"
    lock.save(path)

    (project / "db/seed.sql").write_text("DROP TABLE t;\n")
    with pytest.raises(RegistryError) as caught:
        run(project, tmp_path, "locked", StackLock.load(path))
    assert caught.value.code == "HASH_MISMATCH"
    (project / "db/seed.sql").write_text("CREATE TABLE t (id int);\n")

    data = copy.deepcopy(STACK)
    data["services"]["postgres"]["with"]["init_sql"] = "db/other.sql"
    (project / "db/other.sql").write_text("SELECT 1;\n")
    with pytest.raises(RegistryError) as caught:
        run(project, tmp_path, "locked", StackLock.load(path), data)
    assert caught.value.code == "LOCK_STALE"

    stale = StackLock.load(path)
    stale.imports["litellm"] = ["GEMINI_API_KEY"]
    with pytest.raises(RegistryError, match="variables services import changed"):
        run(project, tmp_path, "locked", stale)

    plain, lock2 = run(project, tmp_path, "plain", stale)
    assert plain.changed and lock2.imports["litellm"] == ["GEMINI_API_KEY", "OPENROUTER_API_KEY"]


def test_tampered_service_version_is_refused(project, tmp_path):
    _, lock = run(project, tmp_path)
    service = project / "registry/service/litellm/1.0.0/service.yaml"
    service.write_text(service.read_text().replace("False", "True"))
    with pytest.raises(RegistryError) as caught:
        run(project, tmp_path, "plain", lock)
    assert caught.value.code == "HASH_MISMATCH"


def test_unused_entries_pruned_or_refused(project, tmp_path):
    _, lock = run(project, tmp_path)
    lock.entries["service/gone@1.0.0"] = {"sha256": "x", "source": "registry"}
    path = tmp_path / "stack.lock"
    lock.save(path)
    with pytest.raises(RegistryError) as caught:
        run(project, tmp_path, "locked", StackLock.load(path))
    assert caught.value.code == "LOCK_STALE"
    locked, pruned = run(project, tmp_path, "plain", StackLock.load(path))
    assert locked.changed and "service/gone@1.0.0" not in pruned.entries


def test_file_params_cannot_escape_the_project(project, tmp_path):
    outside = tmp_path / "secret.sql"
    outside.write_text("x")
    (project / "db/link.sql").symlink_to(outside)
    data = copy.deepcopy(STACK)
    data["services"]["postgres"]["with"]["init_sql"] = "db/link.sql"
    with pytest.raises(RegistryError) as caught:
        run(project, tmp_path, stack_data=data)
    assert caught.value.code == "ENTRY_UNSAFE"


def test_gateway_alias_missing_is_an_error(project, tmp_path):
    data = copy.deepcopy(STACK)
    data["environment"]["models"] = ["support-model", "not-served"]
    with pytest.raises(ServiceError) as caught:
        run(project, tmp_path, stack_data=data)
    assert caught.value.code == "GATEWAY_ALIAS_MISSING" and "not-served" in str(caught.value)


def test_gateway_config_must_parse(project, tmp_path):
    (project / "litellm/config.yaml").write_text("model_list: nope\n")
    with pytest.raises(ServiceError) as caught:
        run(project, tmp_path)
    assert caught.value.code == "GATEWAY_CONFIG"


def test_config_aliases_parser(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(LITELLM_CONFIG))
    assert gateway_keys.config_aliases(path) == {"support-model", "support-judge"}
    path.write_text("model_list:\n  - {litellm_params: {}}\n")
    with pytest.raises(gateway_keys.GatewayError, match="model_name"):
        gateway_keys.config_aliases(path)
    path.write_text("a: [unclosed")
    with pytest.raises(gateway_keys.GatewayError) as caught:
        gateway_keys.config_aliases(path)
    assert caught.value.code == "GATEWAY_CONFIG"


def test_stack_lock_rejects_bad_imports(tmp_path):
    path = tmp_path / "stack.lock"
    path.write_text(json.dumps({"lock_version": 1, "entries": {}, "imports": {"a": "x"}}))
    with pytest.raises(RegistryError) as caught:
        StackLock.load(path)
    assert caught.value.code == "LOCK_INVALID"
    assert StackLock.load(tmp_path / "missing.lock").imports == {}


def test_gateway_needs_config_param():
    from agentbreeder.services.schema import ServiceSpec

    data = copy.deepcopy(LITELLM)
    data["params"] = {}
    data["command"] = ["litellm"]
    with pytest.raises(ValueError, match="config"):
        ServiceSpec.model_validate(data)


NORTHWIND_GATEWAY = Path(__file__).parents[1] / "examples/northwind/stack/litellm/config.yaml"


def _review(tmp_path, data):
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(data))
    return gateway_keys.config_review(path)


def test_config_review_accepts_the_northwind_gateway():
    review = gateway_keys.config_review(NORTHWIND_GATEWAY)
    assert "support-model" in review.aliases
    assert review.providers["support-model"] == "gemini (GEMINI_API_KEY)"
    assert review.providers["support-model-openrouter"] == "openrouter (OPENROUTER_API_KEY)"
    assert len(review.sha256) == 64


def _model(**params):
    return {"model_list": [{"model_name": "m", "litellm_params": {"model": "gemini/x", **params}}]}


@pytest.mark.parametrize(
    "data",
    [
        # A repo config can't send a key or the master key anywhere but the provider's endpoint.
        _model(api_key="os.environ/LITELLM_MASTER_KEY"),
        _model(api_key="os.environ/DATABASE_URL"),
        _model(api_key="os.environ/OPENAI_API_KEY"),  # wrong provider for gemini/
        _model(api_key="os.environ/GEMINI_API_KEY", api_base="https://evil.example"),
        _model(base_url="https://evil.example"),
        _model(extra_headers={"x": "os.environ/GEMINI_API_KEY"}),
        {
            "model_list": [
                {
                    "model_name": "m",
                    "litellm_params": {
                        "model": "custom/x",
                        "api_key": "os.environ/GEMINI_API_KEY",
                    },
                }
            ]
        },
        {**_model(), "litellm_settings": {"success_callback": ["langfuse"]}},
        {**_model(), "litellm_settings": {"callbacks": ["x"]}},
        {**_model(), "general_settings": {"master_key": "os.environ/GEMINI_API_KEY"}},
        {**_model(), "general_settings": {"alerting": ["slack"]}},
        {**_model(), "environment_variables": {"X": "y"}},
        {**_model(), "router_settings": {"redis_host": "evil.example"}},
        # Allow-listed settings take plain values: no nested deployment params.
        {
            **_model(),
            "litellm_settings": {"fallbacks": [{"m": [{"model": "x", "api_base": "h"}]}]},
        },
        {**_model(), "litellm_settings": {"fallbacks": [{"m": ["undeclared"]}]}},
        {**_model(), "litellm_settings": {"fallbacks": [{"m": ["m"], "n": ["m"]}]}},
        {**_model(), "litellm_settings": {"fallbacks": {"m": ["m"]}}},
        {**_model(), "litellm_settings": {"drop_params": {"api_base": "h"}}},
        {**_model(), "router_settings": {"timeout": [1]}},
        {**_model(), "general_settings": {"disable_spend_logs": {"x": 1}}},
        _model(timeout={"api_base": "h"}),
        # Model names are printed in the grant: no control characters or spoofed lines.
        {"model_list": [{"model_name": "m\nGateway ok", "litellm_params": {"model": "x"}}]},
        {"model_list": [{"model_name": "m\x1b[2K", "litellm_params": {"model": "x"}}]},
        {"model_list": [{"model_name": "m", "litellm_params": {"model": "x"}, "extra": 1}]},
        {"model_list": [{"model_name": "m", "model_info": {"k": "os.environ/GEMINI_API_KEY"}}]},
    ],
)
def test_config_review_refuses_secret_exfiltration_paths(tmp_path, data):
    with pytest.raises(gateway_keys.GatewayError) as caught:
        _review(tmp_path, data)
    assert caught.value.code == "GATEWAY_CONFIG_UNSAFE"
    assert "evil.example" not in str(caught.value)  # errors never echo config values


@pytest.mark.parametrize("text", ["- a\n", "model_list: []\nrouter_settings: [1]\n"])
def test_config_review_shape_errors(tmp_path, text):
    path = tmp_path / "c.yaml"
    path.write_text(text)
    with pytest.raises(gateway_keys.GatewayError) as caught:
        gateway_keys.config_review(path)
    assert caught.value.code == "GATEWAY_CONFIG"


def test_config_review_accepts_valid_fallbacks(tmp_path):
    data = {
        "model_list": [
            {"model_name": "a", "litellm_params": {"model": "gemini/x"}},
            {"model_name": "b", "litellm_params": {"model": "openrouter/y"}},
        ],
        "litellm_settings": {"fallbacks": [{"a": ["b"]}], "drop_params": True},
        "router_settings": {"num_retries": 1, "routing_strategy": "simple-shuffle"},
    }
    assert _review(tmp_path, data).aliases == {"a", "b"}
