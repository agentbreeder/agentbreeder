"""Opt-in real Docker run of #1024 G1b: Presidio guardrails on the built-in LiteLLM gateway.

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_guardrails_docker.py --no-cov -s

`services up` runs postgres, service/presidio-analyzer@1.0.0, service/presidio-anonymizer@1.0.0,
service/litellm@1.1.0 and a scripted OpenAI-compatible provider (a project service that prints
every request body it receives). The one test-only change: the gateway config review is widened
to accept `api_base` so LiteLLM can reach the scripted provider. Everything else (the guardrails
review, the recorded hash, the key mint and its routes) is product code. All PII is synthetic.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request

import pytest
import yaml
from conftest import GATEWAY_CAPS
from test_services_engine import PY

from agentbreeder import gateway_keys, gateway_providers, registry
from agentbreeder.gate import _guardrails_failure
from agentbreeder.operator_config import load_operator_config
from agentbreeder.services.engine import Engine
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)

EMAIL, PHONE, SSN = "jane.doe@example.com", "212-555-0147", "536-22-8174"
CARD, IP = "4111 1111 1111 1111", "203.0.113.7"
SEEDED = (EMAIL, PHONE, SSN, CARD, IP, "536228174")

PROVIDER = r"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CARD = "4111 1111 1111 1111"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self._reply({"ok": True})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        print("PROVIDER " + self.path + " " + body.decode("utf-8", "replace"), flush=True)
        data = json.loads(body or b"{}")
        if "embeddings" in self.path:
            items = data.get("input")
            items = items if isinstance(items, list) else [items]
            self._reply({"object": "list", "model": data.get("model"), "usage": {
                "prompt_tokens": 1, "total_tokens": 1}, "data": [
                {"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3]}
                for i, _ in enumerate(items)]})
            return
        text = json.dumps(data.get("messages", ""))
        reply = "ok"
        if "REPLY_CARD" in text:
            reply = "the card is " + CARD
        elif "REPLY_EMAIL" in text:
            reply = "write to jane.doe@example.com"
        self._reply({"id": "c1", "object": "chat.completion", "created": 1,
                     "model": data.get("model"), "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": reply}}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    def _reply(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
"""

PROVIDER_SERVICE = {
    "image": PY,
    "command": ["python3", "/files/provider.py"],
    "user": "65532:65532",
    "ports": {"http": {"port": 8080}},
    "health": {"http": "/health"},
    "outputs": {"url": {"type": "url", "port": "http"}},
}


def _guardrail(name: str, entities: dict) -> dict:
    return {
        "guardrail_name": name,
        "litellm_params": {
            "guardrail": "presidio",
            "mode": "pre_call",
            "default_on": True,
            "pii_entities_config": entities,
            "presidio_language": "en",
            "timeout": 30,
        },
    }


def _model(name: str, model: str) -> dict:
    return {
        "model_name": name,
        "litellm_params": {
            "model": model,
            "api_base": "http://provider:8080/v1",  # the test-only widening (module docstring)
            "api_key": "os.environ/OPENAI_API_KEY",
        },
    }


CONFIG = {
    "model_list": [_model("chat", "openai/fake-model"), _model("embed", "openai/fake-embed")],
    "litellm_settings": {"turn_off_message_logging": True, "set_verbose": False},
    "general_settings": {
        "master_key": "os.environ/LITELLM_MASTER_KEY",
        "database_url": "os.environ/DATABASE_URL",
        "store_prompts_in_spend_logs": False,
        "disable_error_logs": True,
    },
    "guardrails": [
        _guardrail("pii-mask", {"EMAIL_ADDRESS": "MASK", "PHONE_NUMBER": "MASK"}),
        _guardrail("pii-block", {"US_SSN": "BLOCK", "CREDIT_CARD": "BLOCK"}),
    ],
}


def _stack(approved: str) -> dict:
    return {
        "kind": "stack",
        "schema_version": 1,
        "name": "guarded",
        "services": {
            "postgres": {
                "ref": "service/postgres-pgvector@1.2.0",
                "with": {"databases": ["litellm"]},
            },
            "analyzer": {"ref": "service/presidio-analyzer@1.0.0"},
            "anonymizer": {"ref": "service/presidio-anonymizer@1.0.0"},
            "provider": {"ref": "service/provider@1.0.0"},
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
            "models": ["chat", "embed"],
            "ceilings": {"budget_usd": 5, "rpm": 600},
            "guardrails": {"required": True, "approved_sha256": approved},
            "agents": {
                "guarded-agent": {"sponsor": "lead@example.com", "review_by": "2027-06-30"}
            },
        },
    }


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=600)
    if check and result.returncode:
        raise AssertionError(f"docker {args[0]} failed: {result.stderr[-500:]}")
    return result


def _post(base: str, key: str, path: str, body: dict, headers: dict | None = None):
    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        | (headers or {}),
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def _chat(text: str, **extra) -> dict:
    return {"model": "chat", "messages": [{"role": "user", "content": text}], **extra}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    # Imported into the gateway as its OpenAI key (the scripted provider ignores it). It also
    # makes LiteLLM's `/openai/*` passthrough live, which the key's routes must refuse.
    monkeypatch.setenv("OPENAI_API_KEY", "-".join(["sk", "scripted", "provider"]))
    # The scripted provider's address (#1025: params are per provider in gateway_providers).
    monkeypatch.setattr(
        gateway_providers, "COMMON_PARAMS", gateway_providers.COMMON_PARAMS | {"api_base"}
    )
    root = tmp_path / "project"
    version = root / "registry" / "service" / "provider" / "1.0.0"
    (version / "files").mkdir(parents=True)
    (version / "manifest.yaml").write_text("kind: service\nname: provider\nversion: 1.0.0\n")
    (version / "service.yaml").write_text(yaml.safe_dump(PROVIDER_SERVICE))
    (version / "files" / "provider.py").write_text(PROVIDER)
    config = root / "litellm.yaml"
    config.write_text(yaml.safe_dump(CONFIG, sort_keys=False))
    approved = gateway_keys.config_review(config, GATEWAY_CAPS).guardrails_sha256
    (root / "stack.yaml").write_text(yaml.safe_dump(_stack(approved), sort_keys=False))
    instance = Engine(root)
    yield root, instance, approved
    try:
        instance.active_deployments = lambda: []
        instance.down(volumes=True)
    finally:
        instance.close()


def _provider_log(instance: Engine) -> str:
    return docker("logs", f"{instance.state.project}-provider-1").stdout


def test_presidio_guardrails_on_the_built_in_gateway(project):
    root, instance, approved = project
    result = instance.up("dev", confirm=lambda grant: True)
    assert result["services"]["litellm"] == "created"

    # The operator config `up` wrote verifies at the gate.
    operator = load_operator_config(instance.state.env_dir("dev") / "operator.yaml")
    floor = operator.model.environments["dev"].guardrails
    assert floor.running_sha256 == approved and floor.verified == ["presidio:pre_call"]
    assert _guardrails_failure(floor, "dev") is None

    # Presidio: internal only, read-only root, no capabilities, never on the agents network.
    for name in ("analyzer", "anonymizer"):
        inspect = json.loads(docker("inspect", f"{instance.state.project}-{name}-1").stdout)[0]
        assert inspect["HostConfig"]["ReadonlyRootfs"] is True
        assert inspect["HostConfig"]["CapDrop"] == ["ALL"]
        assert set(inspect["NetworkSettings"]["Networks"]) == {instance.state.project}
        assert not inspect["HostConfig"]["PortBindings"]

    master = (instance.state.directory / "service-secrets/litellm/master-key").read_text()
    base = f"http://127.0.0.1:{instance.status()['services']['litellm']['host_ports']['http']}"
    client = gateway_keys.LiteLLM(base, master)
    key = client.mint(
        "agentbreeder-g1b-test",
        ["chat", "embed"],
        1.0,
        600,
        {"agentbreeder": True, "deployment_id": "g1b-test"},
        duration="1h",
        routes=gateway_keys.key_routes(operator.model.environments["dev"]),
    )

    # 1. MASK on input: the provider sees tags, the turn succeeds.
    status, body = _post(base, key, "/v1/chat/completions", _chat(f"mail {EMAIL} or {PHONE}"))
    assert status == 200, body
    log = _provider_log(instance)
    assert "<EMAIL_ADDRESS>" in log and "<PHONE_NUMBER>" in log
    assert EMAIL not in log and PHONE not in log

    # 2. BLOCK on input: 400 naming the entity type, the provider receives nothing.
    before = _provider_log(instance)
    status, body = _post(base, key, "/v1/chat/completions", _chat(f"my ssn is {SSN}"))
    assert status == 400 and "Blocked entity detected" in body and SSN not in body
    assert _provider_log(instance) == before

    # 3. Output: a pre_call guardrail also checks the reply on this digest (spec §12.2).
    status, body = _post(base, key, "/v1/chat/completions", _chat("REPLY_EMAIL please"))
    assert status == 200 and EMAIL not in body and "<EMAIL_ADDRESS>" in body, body
    status, body = _post(base, key, "/v1/chat/completions", _chat("REPLY_CARD please"))
    assert status == 400 and "Blocked entity detected" in body and CARD not in body

    # 4. Embeddings are covered.
    status, body = _post(base, key, "/v1/embeddings", {"model": "embed", "input": f"to {EMAIL}"})
    assert status == 200, body
    assert EMAIL not in _provider_log(instance)

    # 5. Request-side overrides still get checked (a sample of G0 §12.3).
    for extra, headers in (
        ({"guardrails": []}, {}),
        ({"metadata": {"guardrails": [], "disable_global_guardrails": True}}, {}),
        ({"disable_global_guardrails": True}, {}),
        ({"metadata": {"opted_out_global_guardrails": ["pii-block"]}}, {}),
        ({}, {"x-litellm-disable-guardrails": "true"}),
        ({"stream": True}, {}),
    ):
        status, body = _post(
            base, key, "/v1/chat/completions", _chat(f"ssn {SSN}", **extra), headers
        )
        assert status == 400, (extra, headers, status)
    assert SSN not in _provider_log(instance)

    # 6. The key's routes: chat + embeddings only (spec §12.4; records exact vs prefix).
    refused = {}
    for path, body in (
        ("/openai/v1/chat/completions", _chat(f"mail {EMAIL}")),
        ("/openai_passthrough/v1/chat/completions", _chat(f"mail {EMAIL}")),
        ("/v1/moderations", {"input": f"ssn {SSN}"}),
        ("/v1/responses", {"model": "chat", "input": f"mail {EMAIL}"}),
        (
            "/v1/messages",
            {
                "model": "chat",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": f"mail {EMAIL}"}],
            },
        ),  # fmt: skip
        ("/v1/completions", {"model": "chat", "prompt": f"mail {EMAIL}"}),
        ("/key/generate", {"models": ["chat"], "metadata": {"disable_global_guardrails": True}}),
        ("/key/update", {"key": key, "metadata": {"disable_global_guardrails": True}}),
        ("/v1/chat/completions/extra", _chat("hello")),
    ):
        refused[path] = _post(base, key, path, body)[0]
    print("route statuses:", json.dumps(refused))
    assert all(status in (401, 403, 404) for status in refused.values()), refused
    assert EMAIL not in _provider_log(instance) and SSN not in _provider_log(instance)

    # 7. An agent container cannot reach Presidio (the agents network only): by name, and by
    # each Presidio container's stack-network address.
    agents = f"{instance.state.project}-agents"
    addresses = [
        json.loads(docker("inspect", f"{instance.state.project}-{name}-1").stdout)[0][
            "NetworkSettings"
        ]["Networks"][instance.state.project]["IPAddress"]
        for name in ("analyzer", "anonymizer")
    ]
    assert all(addresses)
    probe = docker(
        "run", "--rm", "--network", agents, "--pull", "never", "--user", "65532:65532",
        PY, "python3", "-c",
        "import socket,sys\nfor h in ['analyzer','anonymizer']+sys.argv[1:]:\n try:\n"
        "  socket.create_connection((h,3000),timeout=5); sys.exit('reached '+h)\n"
        " except OSError: pass",
        *addresses,
        check=False,
    )  # fmt: skip
    assert probe.returncode == 0, probe.stdout + probe.stderr

    # 8. No seeded PII in the gateway or Presidio logs.
    for name in ("litellm", "analyzer", "anonymizer"):
        logs = docker("logs", f"{instance.state.project}-{name}-1")
        text = logs.stdout + logs.stderr
        assert not [s for s in SEEDED if s in text], name

    # 9. Fail closed: with the analyzer stopped, the call fails and the provider gets nothing.
    docker("stop", f"{instance.state.project}-analyzer-1")
    before = _provider_log(instance)
    status, body = _post(base, key, "/v1/chat/completions", _chat("hello, no pii here"))
    assert status >= 400 and "Presidio" in body, (status, body[:300])
    assert _provider_log(instance) == before

    # 10. Editing the guardrails changes the recorded hash: the gate then denies.
    client.revoke("agentbreeder-g1b-test")
    data = yaml.safe_load((root / "litellm.yaml").read_text())
    data["guardrails"][0]["litellm_params"]["pii_entities_config"].pop("PHONE_NUMBER")
    (root / "litellm.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    (root / "stack.lock").unlink(missing_ok=True)
    docker("start", f"{instance.state.project}-analyzer-1")
    instance.up("dev", confirm=lambda grant: True)
    operator = load_operator_config(instance.state.env_dir("dev") / "operator.yaml")
    floor = operator.model.environments["dev"].guardrails
    assert floor.running_sha256 != approved
    assert _guardrails_failure(floor, "dev")[0] == "GUARDRAILS_CHANGED"
