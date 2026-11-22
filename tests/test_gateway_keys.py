"""Per-deployment LiteLLM keys (#725 slice 6, spec §10/§18.2): mint, reuse, revoke, reconcile."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from conftest import GATEWAY_CAPS, v2_manifest_data

from agentbreeder import gateway_keys, secret_files
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.gateway_keys import GatewayError, LiteLLM
from agentbreeder.manifest import Manifest
from agentbreeder.store import DeploymentError

MASTER = "master-for-tests"


class FakeGateway:
    """A minimal LiteLLM key API: /key/generate, /key/delete, /key/list."""

    def __init__(self):
        self.keys: dict[str, dict] = {}
        self.down = False
        self.counter = 0
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                if gateway.down:
                    self.reply(503, {"error": "down"})
                    return False
                if self.headers.get("Authorization") != f"Bearer {MASTER}":
                    self.reply(401, {"error": "auth"})
                    return False
                return True

            def do_POST(self):
                if not self.authorized():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/key/generate":
                    gateway.counter += 1
                    value = f"vk-{gateway.counter}"
                    gateway.keys[body["key_alias"]] = {**body, "key": value, "spend": 0.0}
                    self.reply(200, {"key": value, "key_alias": body["key_alias"]})
                elif self.path == "/key/delete":
                    found = [a for a in body["key_aliases"] if gateway.keys.pop(a, None)]
                    self.reply(200 if found else 404, {"deleted_keys": found})
                else:
                    self.reply(404, {})

            def do_GET(self):
                if not self.authorized():
                    return
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                rows = [
                    {"key_alias": a, "spend": k["spend"], "max_budget": k["max_budget"],
                     "rpm_limit": k["rpm_limit"], "models": k["models"]}
                    for a, k in gateway.keys.items()
                ]  # fmt: skip
                if "key_alias" in query:
                    rows = [r for r in rows if r["key_alias"] == query["key_alias"][0]]
                self.reply(200, {"keys": rows, "total_pages": 1})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def gateway():
    fake = FakeGateway()
    yield fake
    fake.server.shutdown()


@pytest.fixture
def minting(identity_env, gateway, tmp_path):
    master = tmp_path / "master"
    master.write_text(MASTER)
    master.chmod(0o600)
    identity_env.config["gateways"] = {
        "litellm-local": {
            "type": "litellm",
            "url": gateway.url,
            "agent_url": "http://host.docker.internal:4000",
            "master_key": f"file:{master}",
        }
    }
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm-local",
            "models": ["support-model", "support-embedding"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
        }
    )
    return identity_env


def manifest(**access):
    return Manifest.model_validate(
        v2_manifest_data(
            model_access={"gateway": "litellm-local", "aliases": ["support-model"], **access}
        )
    )


def running(runtime, creates=True):
    info = {"Id": "c", "State": {"Running": True}}
    runtime.inspect.side_effect = ([None] if creates else []) + [info] * 6


def test_deploy_mints_scoped_key_into_private_file(minting, gateway, state, runtime):
    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(manifest(budget_usd=25, rpm=120), local=True)
    assert result["state"] == "ready"
    key_alias = gateway_keys.alias(result["id"])
    minted = gateway.keys[key_alias]
    assert minted["models"] == ["support-model"]
    assert (minted["max_budget"], minted["rpm_limit"]) == (25.0, 120)
    assert minted["metadata"]["deployment_id"] == result["id"]
    assert "duration" not in minted  # deployment keys live until remove revokes them
    mounts = runtime.create.call_args.kwargs["mounts"]
    key_mount = [m for m in mounts if m.envs == ("LLM_GATEWAY_KEY",)][0]
    assert key_mount.source.read_text() == minted["key"]
    assert key_mount.source.stat().st_mode & 0o777 == 0o444
    rows = repr(state.audit_rows(limit=200))
    assert minted["key"] not in rows and key_alias in rows


def test_budget_defaults_to_environment_ceiling(minting, gateway, state, runtime):
    running(runtime)
    result = DeploymentService(state, runtime, minting.load()).deploy(manifest(), local=True)
    minted = gateway.keys[gateway_keys.alias(result["id"])]
    assert (minted["max_budget"], minted["rpm_limit"]) == (50.0, 300)


def test_healthy_container_keeps_its_key(minting, gateway, state, runtime):
    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    first = service.deploy(manifest(), local=True)
    key = gateway.keys[gateway_keys.alias(first["id"])]["key"]
    running(runtime, creates=False)
    again = DeploymentService(state, runtime, minting.load()).deploy(manifest(), local=True)
    assert again["id"] == first["id"]
    assert gateway.keys[gateway_keys.alias(first["id"])]["key"] == key  # not re-minted


def test_failed_health_revokes_key_and_deletes_file(minting, gateway, state, runtime):
    running(runtime)
    runtime.healthy.return_value = False
    result = DeploymentService(state, runtime, minting.load()).deploy(manifest(), local=True)
    assert result["state"] == "failed"
    assert gateway_keys.alias(result["id"]) not in gateway.keys
    assert not secret_files.deployment_dir(state.directory, result["id"]).exists()


def test_failure_before_minting_still_revokes_an_earlier_key(minting, gateway, state, runtime):
    from conftest import actor

    from agentbreeder.store import DeploymentError

    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(manifest(), local=True)
    key_alias = gateway_keys.alias(result["id"])
    assert key_alias in gateway.keys  # live key from the first generation
    runtime.inspect.side_effect = DeploymentError("docker unavailable")
    record = state.get(result["id"])
    failed = service._run(result["id"], record, manifest(), "attempt-2", actor("dave"), {})
    assert failed["state"] == "failed"
    assert key_alias not in gateway.keys  # revoked even though this attempt minted nothing


def test_gateway_down_fails_deploy_cleanly(minting, gateway, state, runtime):
    running(runtime)
    gateway.down = True
    result = DeploymentService(state, runtime, minting.load()).deploy(manifest(), local=True)
    assert result["state"] == "failed" and "GATEWAY_ERROR" in result["error"]
    runtime.create.assert_not_called()


def test_user_declared_gateway_key_is_reserved(minting, state, runtime):
    data = v2_manifest_data(
        model_access={"gateway": "litellm-local", "aliases": ["support-model"]},
        secrets={"LLM_GATEWAY_KEY": "secret://mine"},
    )
    with pytest.raises(GateDenied) as error:
        DeploymentService(state, runtime, minting.load()).deploy(
            Manifest.model_validate(data), local=True
        )
    assert error.value.code == "SECRET_RESERVED"
    assert runtime.mock_calls == []


def test_remove_revokes_and_reconcile_cleans_orphans(minting, gateway, state, runtime):
    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(manifest(), local=True)
    key_alias = gateway_keys.alias(result["id"])
    status = service.status(result["id"], verify=False)
    assert status["gateway_key"]["max_budget_usd"] == 50.0
    # removal while the gateway is down: the container goes, the key is left for reconcile
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    gateway.down = True
    removed = service.remove(result["id"], local=True)
    assert removed["state"] == "removed" and removed["gateway_key"] == "revoke_failed"
    assert key_alias in gateway.keys
    gateway.down = False
    # keys this store does not own are left alone (another state dir, an eval judge key)
    foreign = gateway_keys.PREFIX + "f" * 32
    judge = gateway_keys.PREFIX + "eval-20261107-abc"
    gateway.keys[foreign] = dict(gateway.keys[key_alias])
    gateway.keys[judge] = dict(gateway.keys[key_alias])
    results = service.reconcile()
    assert key_alias not in gateway.keys
    assert foreign in gateway.keys and judge in gateway.keys
    assert sum(r["compliance"] == "gateway_key_revoked" for r in results) == 1


def test_remove_revokes_when_gateway_up(minting, gateway, state, runtime):
    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(manifest(), local=True)
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    assert service.remove(result["id"], local=True)["gateway_key"] == "revoked"
    assert gateway_keys.alias(result["id"]) not in gateway.keys


def test_disable_revokes_even_if_gateway_down(minting, gateway, state, runtime):
    from conftest import actor

    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(manifest(), local=True)
    gateway.down = True
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    disabled = service.disable("sample", actor("ops"))
    assert disabled["state"] == "disabled" and disabled["stopped"] == [result["id"]]
    gateway.down = False
    runtime.inspect.side_effect = None
    runtime.inspect.return_value = None  # the stopped container stays gone
    service.reconcile()
    assert gateway_keys.alias(result["id"]) not in gateway.keys


def test_reconcile_reports_unreachable_gateway(minting, gateway, state, runtime):
    gateway.down = True
    results = DeploymentService(state, runtime, minting.load()).reconcile()
    assert any(r["compliance"].startswith("gateway_unreachable") for r in results)


def test_status_survives_gateway_errors(minting, gateway, state, runtime):
    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(manifest(), local=True)
    gateway.down = True
    assert "error" in service.status(result["id"], verify=False)["gateway_key"]
    gateway.down = False
    gateway.keys.clear()
    assert service.status(result["id"], verify=False)["gateway_key"]["missing"] is True


def test_client_errors_and_pagination(gateway):
    client = LiteLLM(gateway.url, "wrong")
    with pytest.raises(GatewayError) as error:
        client.revoke("x")
    assert error.value.code == "GATEWAY_ERROR"
    with pytest.raises(GatewayError) as error:
        LiteLLM("http://127.0.0.1:9", MASTER).revoke("x")
    assert error.value.code == "GATEWAY_UNREACHABLE"
    client = LiteLLM(gateway.url, MASTER)
    client.mint(gateway_keys.PREFIX + "a" * 32, ["m"], 1.0, 1, {})
    gateway.keys["someone-else"] = {"spend": 0, "max_budget": 1, "rpm_limit": 1, "models": []}
    assert client.aliases() == [gateway_keys.PREFIX + "a" * 32]
    assert client.describe("nope") is None


def test_no_master_key_means_no_client(identity_env):
    identity_env.config["gateways"] = {
        "g": {"type": "litellm", "url": "http://127.0.0.1:1", "agent_url": "http://x:1"}
    }
    assert gateway_keys.client(identity_env.load(), "g") is None
    assert gateway_keys.client(identity_env.load(), "missing") is None


def test_generated_key_file_name_is_reserved(minting, state, runtime):
    data = v2_manifest_data(
        model_access={"gateway": "litellm-local", "aliases": ["support-model"]},
        secrets={"OTHER": "secret://llm-gateway-key"},
    )
    with pytest.raises(GateDenied) as error:
        DeploymentService(state, runtime, minting.load()).deploy(
            Manifest.model_validate(data), local=True
        )
    assert error.value.code == "SECRET_RESERVED"


# --- config_review: unknown providers (#1031) and prompt logging (#1028) -----------------------

ROOT = Path(__file__).parents[1]
# Every gateway config shipped in the repo keeps its review result byte for byte (#1025 spec AC1).
GOLDEN_CONFIGS = {
    "src/agentbreeder/examples/northwind/stack/litellm/config.yaml": (
        {
            "support-model": "gemini (GEMINI_API_KEY)",
            "support-model-openrouter": "openrouter (OPENROUTER_API_KEY)",
            "support-judge": "gemini (GEMINI_API_KEY)",
            "support-embedding": "gemini (GEMINI_API_KEY)",
        },
        "cce4700401140175cedeebeca3a7bd600bc96bdd2d1559d7d5bf7bb881de114b",
    ),
    "tests/fixtures/mcp_github_project/stack/litellm/config.yaml": (
        {"chat": "gemini (GEMINI_API_KEY)", "judge": "gemini (GEMINI_API_KEY)"},
        "20b7b9543799e509e1c27dd7303cfabb3cc037293152a7784799523504b06f10",
    ),
}


def _config(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def _entry(model, **params):
    return {"model_name": "m", "litellm_params": {"model": model, **params}}


@pytest.mark.parametrize("relative", sorted(GOLDEN_CONFIGS))
def test_shipped_gateway_configs_keep_their_review(relative):
    providers, sha256 = GOLDEN_CONFIGS[relative]
    review = gateway_keys.config_review(ROOT / relative, GATEWAY_CAPS)
    assert review.providers == providers
    assert review.aliases == set(providers)
    assert review.sha256 == sha256


@pytest.mark.parametrize(
    "params",
    [
        {"model": "custom/x"},
        {"model": "Gemini/x"},  # provider prefixes are matched exactly
        {"model": "Vertex_AI/gemini-2.5-flash"},
        {"model": " gemini/x"},
        {"model": "gemini /x"},
        {"model": "gemini/"},
        {"model": "gemini/caf\u00e9"},  # printable ASCII only (#1025)
        {"model": "gemini/a\nb"},
        {"model": "azure_ai/gpt-4o"},  # a separate LiteLLM provider, not azure
        {"model": "azure_text/gpt-4o"},
        {"model": "gpt-4o"},  # bare names: LiteLLM guesses the provider
        {"model": "claude-3-5-sonnet"},
        {"model": "x"},
        {"model": "/x"},
        {"model": ""},
        {"model": 3},
        {"model": None},
        {},  # no model at all
    ],
)
def test_config_review_refuses_unknown_providers_and_bare_names(tmp_path, params):
    path = _config(tmp_path, {"model_list": [{"model_name": "m", "litellm_params": params}]})
    with pytest.raises(GatewayError) as caught:
        gateway_keys.config_review(path, GATEWAY_CAPS)
    assert caught.value.code == "GATEWAY_CONFIG_UNSAFE"
    message = str(caught.value)
    assert "model_list[0].litellm_params.model" in message
    listed = (
        "anthropic, azure, bedrock, gemini, ollama, ollama_chat, openai, openrouter, vertex_ai"
    )
    assert listed in message
    for value in ("custom", "gpt-4o", "claude", "caf", "Vertex_AI", "azure_ai"):
        assert value not in message.replace(listed, "")  # config values are never echoed


@pytest.mark.parametrize(
    "model",
    [
        "vertex_ai/gemini-2.5-flash",  # ADC / metadata server on a cloud VM
        "bedrock/anthropic.claude-3-haiku",  # AWS default credential chain
        "azure/gpt-4o",
        "ollama/llama3",
    ],
)
def test_config_review_refuses_credential_less_new_providers(tmp_path, model):
    # #1031 still holds for the #1025 providers: no explicit credentials (or, for Ollama, no
    # exact api_base) is refused, never routed through a default credential chain.
    path = _config(tmp_path, {"model_list": [_entry(model)]})
    with pytest.raises(GatewayError) as caught:
        gateway_keys.config_review(path, GATEWAY_CAPS)
    assert caught.value.code == "GATEWAY_CONFIG_UNSAFE"
    assert "is required for" in str(caught.value)


def test_config_review_refuses_a_model_list_entry_without_params(tmp_path):
    path = _config(tmp_path, {"model_list": [{"model_name": "m"}]})
    with pytest.raises(GatewayError) as caught:
        gateway_keys.config_review(path, GATEWAY_CAPS)
    assert caught.value.code == "GATEWAY_CONFIG_UNSAFE"


@pytest.mark.parametrize("provider", sorted(gateway_keys.PROVIDER_KEYS))
def test_config_review_accepts_each_known_provider_with_or_without_its_key(tmp_path, provider):
    key = gateway_keys.PROVIDER_KEYS[provider]
    data = {
        "model_list": [
            {
                "model_name": "a",
                "litellm_params": {"model": f"{provider}/x", "api_key": f"os.environ/{key}"},
            },
            {"model_name": "b", "litellm_params": {"model": f"{provider}/vendor/y"}},
        ]
    }
    review = gateway_keys.config_review(_config(tmp_path, data), GATEWAY_CAPS)
    assert review.providers == {"a": f"{provider} ({key})", "b": f"{provider} (no key)"}


@pytest.mark.parametrize(
    "section, key, value",
    [
        ("litellm_settings", "turn_off_message_logging", False),
        ("litellm_settings", "turn_off_message_logging", None),
        ("litellm_settings", "turn_off_message_logging", "true"),  # a string, not a boolean
        ("litellm_settings", "turn_off_message_logging", 1),
        ("general_settings", "store_prompts_in_spend_logs", True),
        ("general_settings", "store_prompts_in_spend_logs", None),
        ("general_settings", "store_prompts_in_spend_logs", "false"),
        ("general_settings", "store_prompts_in_spend_logs", 0),
        ("litellm_settings", "set_verbose", True),  # debug logs print request bodies
        ("litellm_settings", "set_verbose", "yes"),
    ],
)
def test_config_review_refuses_prompt_logging(tmp_path, section, key, value):
    data = {"model_list": [_entry("gemini/x")], section: {key: value}}
    with pytest.raises(GatewayError) as caught:
        gateway_keys.config_review(_config(tmp_path, data), GATEWAY_CAPS)
    assert caught.value.code == "GATEWAY_CONFIG_UNSAFE"
    must = "true" if key == "turn_off_message_logging" else "false"
    assert f"{section}.{key} must be {must}" in str(caught.value)


def test_config_review_accepts_prompt_logging_off_or_unset(tmp_path):
    data = {
        "model_list": [_entry("gemini/x")],
        "litellm_settings": {"turn_off_message_logging": True, "set_verbose": False},
        "general_settings": {"store_prompts_in_spend_logs": False},
    }
    assert gateway_keys.config_review(_config(tmp_path, data), GATEWAY_CAPS).aliases == {"m"}
    unset = {"model_list": [_entry("gemini/x")], "litellm_settings": {"drop_params": True}}
    assert gateway_keys.config_review(_config(tmp_path, unset), GATEWAY_CAPS).aliases == {"m"}


# -- fallbacks (#1033) ---------------------------------------------------------------------


def _models(*names):
    return [{"model_name": n, "litellm_params": {"model": "gemini/x"}} for n in names]


def test_config_review_returns_every_fallback_edge():
    review = gateway_keys.config_review(
        ROOT / "src/agentbreeder/examples/northwind/stack/litellm/config.yaml", GATEWAY_CAPS
    )
    assert review.fallbacks == {"support-model": ["support-model-openrouter"]}


def test_config_review_merges_both_fallback_kinds(tmp_path):
    data = {
        "model_list": _models("a", "b", "c"),
        "litellm_settings": {
            "fallbacks": [{"a": ["b"]}, {"b": ["c"]}],
            "context_window_fallbacks": [{"a": ["c", "b"]}],
        },
    }
    review = gateway_keys.config_review(_config(tmp_path, data), GATEWAY_CAPS)
    assert review.fallbacks == {"a": ["b", "c"], "b": ["c"]}
    plain = gateway_keys.config_review(
        _config(tmp_path, {"model_list": _models("a")}), GATEWAY_CAPS
    )
    assert plain.fallbacks == {}


def test_deploy_mints_granted_fallback_targets_too(minting, gateway, state, runtime):
    dev = minting.config["environments"]["dev"]
    dev["models"] = ["support-model", "support-embedding", "or-model", "third", "judge"]
    # Followed transitively; a fallback of an alias the agent doesn't use adds nothing.
    dev["fallbacks"] = {"support-model": ["or-model"], "or-model": ["third"], "judge": ["third"]}
    running(runtime)
    result = DeploymentService(state, runtime, minting.load()).deploy(manifest(), local=True)
    minted = gateway.keys[gateway_keys.alias(result["id"])]
    assert minted["models"] == ["support-model", "or-model", "third"]
    rows = repr(state.audit_rows(limit=200))
    assert "GATEWAY_KEY_MODELS" in rows and "support-model, or-model, third" in rows
    assert minted["key"] not in rows


def test_unrelated_fallbacks_mint_only_the_manifest_aliases(minting, gateway, state, runtime):
    minting.config["environments"]["dev"]["fallbacks"] = {"support-embedding": ["support-model"]}
    running(runtime)
    result = DeploymentService(state, runtime, minting.load()).deploy(manifest(), local=True)
    assert gateway.keys[gateway_keys.alias(result["id"])]["models"] == ["support-model"]


@pytest.mark.parametrize(
    "fallbacks",
    [
        {"support-model": ["not-granted"]},  # a target outside environment.models
        {"not-granted": ["support-model"]},  # a source outside environment.models
        {"support-model": []},
    ],
)
def test_operator_config_fallbacks_stay_inside_the_grant(minting, fallbacks):
    from agentbreeder.operator_config import OperatorConfigError

    minting.config["environments"]["dev"]["fallbacks"] = fallbacks
    with pytest.raises(OperatorConfigError):
        minting.load()


def test_key_models_follow_fallbacks_in_a_stable_order():
    from agentbreeder.operator_config import Environment

    env = Environment.model_validate(
        {
            "targets": ["local"],
            "models": ["a", "b", "c", "d"],
            "fallbacks": {"a": ["c", "b"], "c": ["a", "d"], "b": ["d"]},
        }
    )
    assert env.key_models(["a"]) == ["a", "c", "b", "d"]
    assert env.key_models(["d", "b"]) == ["d", "b"]
    assert env.key_models(["x"]) == ["x"]  # the gate refuses ungranted aliases, not this helper


# -- #770 §4.3 item 5 (K1): the kill switch revokes the key in `finally` -----------------------


def test_disable_revokes_the_key_when_the_workload_step_fails(minting, gateway, state, runtime):
    """A failed authorize (an expired token, a failed mint, a refused binding) no longer skips
    the revoke: the agent loses its model access while the gateway answers."""
    from conftest import actor

    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(manifest(), local=True)
    runtime.authorize.side_effect = DeploymentError("[KUBERNETES_MINT_FAILED] minting failed")
    disabled = service.disable("sample", actor("ops"))
    assert disabled["state"] == "failed"
    assert disabled["failed"] == [
        {"id": result["id"], "error": "[KUBERNETES_MINT_FAILED] minting failed"}
    ]
    assert gateway_keys.alias(result["id"]) not in gateway.keys
    assert state.get(result["id"])["state"] == "failed"


def test_a_failed_revoke_is_reported_beside_the_original_error(minting, gateway, state, runtime):
    from conftest import actor

    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(manifest(), local=True)
    runtime.authorize.side_effect = DeploymentError("[KUBERNETES_MINT_FAILED] minting failed")
    gateway.down = True
    disabled = service.disable("sample", actor("ops"))
    (failure,) = disabled["failed"]
    assert failure["error"] == (
        "[KUBERNETES_MINT_FAILED] minting failed; the gateway key revoke also failed "
        "(reconcile retries it)"
    )
    assert gateway_keys.alias(result["id"]) in gateway.keys
    gateway.down = False
    runtime.authorize.side_effect = None
    runtime.inspect.side_effect = None
    runtime.inspect.return_value = None
    service.reconcile()
    assert gateway_keys.alias(result["id"]) not in gateway.keys


def test_an_unexpected_error_still_revokes_and_propagates(minting, gateway, state, runtime):
    from conftest import actor

    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(manifest(), local=True)
    runtime.inspect.side_effect = RuntimeError("an unexpected failure")
    with pytest.raises(RuntimeError, match="unexpected"):
        service.disable("sample", actor("ops"))
    assert gateway_keys.alias(result["id"]) not in gateway.keys


def test_the_revoke_runs_once_when_disable_succeeds(minting, gateway, state, runtime, monkeypatch):
    from conftest import actor

    running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    service.deploy(manifest(), local=True)
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    calls = []
    original = service._revoke_key
    monkeypatch.setattr(service, "_revoke_key", lambda *a: calls.append(a) or original(*a))
    assert service.disable("sample", actor("ops"))["state"] == "disabled"
    assert len(calls) == 1
