"""Per-deployment LiteLLM keys (#725 slice 6, spec §10/§18.2): mint, reuse, revoke, reconcile."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import v2_manifest_data

from agentbreeder import gateway_keys, secret_files
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.gateway_keys import GatewayError, LiteLLM
from agentbreeder.manifest import Manifest

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
