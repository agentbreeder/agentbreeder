"""Opt-in real Docker run of #1019: a deployment key's budget, rpm cap and gateway fallback.

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_gateway_limits_docker.py --no-cov -s

`services up` runs postgres, the built-in gateway and two scripted providers: `provider-ok`
answers every chat call and reports a large token count (so the call has a price), and
`provider-bad` answers 500. No provider key is needed. Keys are minted the way `deploy` mints
them, and each limit is checked twice: with a direct gateway call, and with a real
`runtime/adk@1.4.0` turn, which must answer the limit as a coded error (#1034).

- budget: a key with a tiny `max_budget` is refused once spent; the turn is
  `MODEL_BUDGET_EXCEEDED` (HTTP 402).
- rpm: the call after the cap is 429; the turn is `MODEL_RATE_LIMITED` (HTTP 429) with
  `retry_after_s`.
- fallback: alias `main` (served by `provider-bad`) falls back to `backup` under a key granted
  both; the turn answers and reports `fallbacks` and `served_by` (#1033, #1039).
- grant: a key granted only `chat` is refused when it asks for `backup` by name.

This checks the gateway and the runtime only. Keys are minted directly, so the deploy gate is not
exercised here (tests/test_services_docker.py and the deploy tests cover it).

The one test-only change, as in tests/test_guardrails_docker.py: the config review accepts
`api_base` so the gateway can reach the scripted providers.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pytest
import yaml
from test_guardrails_docker import _post, docker
from test_services_engine import PY

from agentbreeder import gateway_keys, gateway_providers, registry
from agentbreeder.services.engine import Engine
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)

PROVIDER = r"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.environ["PROVIDER_NAME"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self._reply(200, {"ok": True})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        print(f"PROVIDER-{NAME} {self.path}", flush=True)
        data = json.loads(body or b"{}")
        if NAME == "BAD":
            self._reply(500, {"error": {"message": "scripted failure", "type": "server_error"}})
            return
        # 200,000 prompt tokens: the call has a price on the gateway's own price list.
        self._reply(200, {"id": "c1", "object": "chat.completion", "created": 1,
                          "model": data.get("model"), "choices": [{"index": 0,
                          "finish_reason": "stop", "message": {"role": "assistant",
                          "content": "from " + NAME}}],
                          "usage": {"prompt_tokens": 200000, "completion_tokens": 1,
                                    "total_tokens": 200001}})

    def _reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
"""


def _provider_service(name: str) -> dict:
    return {
        "image": PY,
        "command": ["python3", "/files/provider.py"],
        "user": "65532:65532",
        "env": {"PROVIDER_NAME": name},
        "ports": {"http": {"port": 8080}},
        "health": {"http": "/health"},
        "outputs": {"url": {"type": "url", "port": "http"}},
    }


def _model(alias: str, host: str) -> dict:
    return {
        "model_name": alias,
        "litellm_params": {
            "model": "openai/gpt-4o-mini",  # a priced model name; the provider is scripted
            "api_base": f"http://{host}:8080/v1",  # the test-only widening (module docstring)
            "api_key": "os.environ/OPENAI_API_KEY",
        },
    }


CONFIG = {
    "model_list": [
        _model("chat", "provider-ok"),
        _model("main", "provider-bad"),
        _model("backup", "provider-ok"),
    ],
    "litellm_settings": {
        "turn_off_message_logging": True,
        "set_verbose": False,
        "num_retries": 0,
        "fallbacks": [{"main": ["backup"]}],
    },
    "general_settings": {
        "master_key": "os.environ/LITELLM_MASTER_KEY",
        "database_url": "os.environ/DATABASE_URL",
        "store_prompts_in_spend_logs": False,
    },
}
STACK = {
    "kind": "stack",
    "schema_version": 1,
    "name": "limits",
    "services": {
        "postgres": {
            "ref": "service/postgres-pgvector@1.2.0",
            "with": {"databases": ["litellm", "agent"]},
        },
        "provider-ok": {"ref": "service/provider-ok@1.0.0"},
        "provider-bad": {"ref": "service/provider-bad@1.0.0"},
        "litellm": {
            "ref": "service/litellm@1.3.0",
            "with": {"config": "litellm.yaml"},
            "wire": {"database-url": "postgres.databases.litellm"},
        },
    },
    "environment": {
        "gateway": "litellm",
        "models": ["chat", "main", "backup"],
        "ceilings": {"budget_usd": 5, "rpm": 600},
        "agents": {"limits-agent": {"sponsor": "lead@example.com", "review_by": "2027-06-30"}},
        "bindings": {"DATABASE_URL": {"ref": "postgres.databases.agent"}},
    },
}
INVOKE = r"""
import json, time, urllib.request, urllib.error
for _ in range(120):
    try:
        urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2).read()
        break
    except Exception:
        time.sleep(1)
body = json.dumps({"message": "hello", "session_id": "s1"}).encode()
request = urllib.request.Request("http://127.0.0.1:8080/invoke", body,
                                 {"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(request, timeout=200) as response:
        print(json.dumps({"status": response.status, "body": json.loads(response.read())}))
except urllib.error.HTTPError as error:
    print(json.dumps({"status": error.code, "body": json.loads(error.read() or b"{}")}))
"""


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    # Imported into the gateway as its OpenAI key; the scripted providers ignore it.
    monkeypatch.setenv("OPENAI_API_KEY", "-".join(["sk", "scripted", "provider"]))
    monkeypatch.setattr(
        gateway_providers, "COMMON_PARAMS", gateway_providers.COMMON_PARAMS | {"api_base"}
    )
    root = tmp_path / "project"
    for name in ("ok", "bad"):
        version = root / "registry" / "service" / f"provider-{name}" / "1.0.0"
        (version / "files").mkdir(parents=True)
        (version / "manifest.yaml").write_text(
            f"kind: service\nname: provider-{name}\nversion: 1.0.0\n"
        )
        (version / "service.yaml").write_text(yaml.safe_dump(_provider_service(name.upper())))
        (version / "files" / "provider.py").write_text(PROVIDER)
    (root / "litellm.yaml").write_text(yaml.safe_dump(CONFIG, sort_keys=False))
    (root / "stack.yaml").write_text(yaml.safe_dump(STACK, sort_keys=False))
    instance = Engine(root)
    images: list[str] = []
    # Remove only images this test created: a deterministic build can reuse an existing ID.
    before = set(docker("images", "-q", "--no-trunc").stdout.split())
    yield instance, images
    try:
        for image in images:
            if image not in before:
                docker("image", "rm", image, check=False)
        instance.active_deployments = lambda: []
        instance.down(volumes=True)
    finally:
        instance.close()


def _chat(alias: str) -> dict:
    return {"model": alias, "messages": [{"role": "user", "content": "hello"}]}


def _mint(client, label: str, models: list[str], budget: float, rpm: int) -> str:
    return client.mint(
        gateway_keys.alias(label * 32),
        models,
        budget,
        rpm,
        {"agentbreeder": True, "deployment_id": label * 32},
        duration="1h",
        routes=gateway_keys.key_routes(None),
    )


def _build(instance: Engine, alias: str, images: list[str]) -> tuple[str, str, str]:
    """Build a runtime/adk@1.4.0 agent on `alias`: (image, gateway URL, database URL)."""
    from agentbreeder.build import build
    from agentbreeder.operator_config import load_operator_config
    from agentbreeder.services import operator as operator_writer

    root = instance.project
    agent = {
        "kind": "agent",
        "schema_version": 1,
        "name": "limits-agent",
        "runtime": "runtime/adk@1.4.0",
        "model": {"alias": alias, "reasoning": "none", "max_model_calls": 2},
        "prompt": {"path": "prompt.md"},
        "traces": "none",
    }
    (root / "agent.yaml").write_text(yaml.safe_dump(agent))
    (root / "prompt.md").write_text("You are brief.\n")
    operator_path = Path(instance.state.env_dir("dev") / "operator.yaml")
    operator = yaml.safe_load(operator_path.read_text())
    store = instance.state.deploy_state("dev") / "secrets"
    database_url = (store / operator_writer.binding_secret("DATABASE_URL")).read_text().strip()
    built = build(root / "agent.yaml", load_operator_config(operator_path), "dev")
    if built.image not in images:
        images.append(built.image)
    return built.image, operator["gateways"]["litellm"]["agent_url"], database_url


def _turn(instance: Engine, tmp_path, built: tuple[str, str, str], key: str) -> dict:
    """One real runtime turn through the gateway with `key`: {"status", "body"}."""
    image, gateway_url, database_url = built
    secrets = tmp_path / f"agent-secrets-{uuid.uuid4().hex[:8]}"
    secrets.mkdir()
    for name, value in {"DATABASE_URL": database_url, "LLM_GATEWAY_KEY": key}.items():
        (secrets / name).write_text(value + "\n")
        os.chmod(secrets / name, 0o444)
    os.chmod(secrets, 0o755)
    name = f"ab-1019-{uuid.uuid4().hex[:8]}"
    docker("run", "-d", "--name", name, "--network", f"{instance.state.project}-agents",
           "-v", f"{secrets}:/run/secrets:ro",
           "-e", "DATABASE_URL_FILE=/run/secrets/DATABASE_URL",
           "-e", "LLM_GATEWAY_KEY_FILE=/run/secrets/LLM_GATEWAY_KEY",
           "-e", f"AGENTBREEDER_GATEWAY_URL={gateway_url}",
           image)  # fmt: skip
    try:
        out = docker("exec", name, "python", "-c", INVOKE).stdout.strip().splitlines()[-1]
        return json.loads(out)
    finally:
        docker("rm", "-f", name, check=False)


def _provider_log(instance: Engine, name: str) -> list[str]:
    result = docker("logs", f"{instance.state.project}-{name}-1")
    return [line for line in result.stdout.splitlines() if line.startswith("PROVIDER-")]


def test_budget_rpm_and_fallback_reach_the_caller_as_coded_results(stack, tmp_path):
    instance, images = stack
    instance.up("dev", confirm=lambda grant: True)
    master = (instance.state.directory / "service-secrets/litellm/master-key").read_text()
    base = f"http://127.0.0.1:{instance.status()['services']['litellm']['host_ports']['http']}"
    client = gateway_keys.LiteLLM(base, master)
    on_chat = _build(instance, "chat", images)

    # 1. Budget: the first call is served and overspends; later calls are refused.
    budget_key = _mint(client, "b", ["chat"], 0.001, 600)
    status, text = _post(base, budget_key, "/v1/chat/completions", _chat("chat"))
    assert status == 200 and "from OK" in text, (status, text[:300])
    refused = (0, "")
    deadline = time.monotonic() + 60  # LiteLLM books the spend just after it answers
    while time.monotonic() < deadline:
        refused = _post(base, budget_key, "/v1/chat/completions", _chat("chat"))
        if refused[0] != 200:
            break
        time.sleep(1)
    print("\nbudget: direct call after the spend:", refused[0], refused[1][:160])
    assert 400 <= refused[0] < 500 and "budget" in refused[1].lower(), refused
    turn = _turn(instance, tmp_path, on_chat, budget_key)
    print("budget: runtime turn:", json.dumps(turn)[:400])
    assert turn["status"] == 402 and turn["body"]["code"] == "MODEL_BUDGET_EXCEEDED", turn
    body = json.dumps(turn["body"])
    # LiteLLM's own text names the key as `sk-...` plus its last 4 characters; none of it passes.
    assert "Budget has been exceeded" not in body and "sk-" not in body, body

    # 2. rpm: two calls a minute; the third is 429, and a turn at the cap says so. The key is new,
    # so its window starts at the first call; the turn follows within about 20 s.
    rpm_key = _mint(client, "r", ["chat"], 5.0, 2)
    statuses = [_post(base, rpm_key, "/v1/chat/completions", _chat("chat"))[0] for _ in range(3)]
    print("rpm: direct statuses:", statuses)
    assert statuses == [200, 200, 429], statuses
    started = time.monotonic()
    turn = _turn(instance, tmp_path, on_chat, rpm_key)
    print("rpm: runtime turn:", json.dumps(turn)[:400], f"{time.monotonic() - started:.1f}s")
    assert turn["status"] == 429 and turn["body"]["code"] == "MODEL_RATE_LIMITED", turn
    assert isinstance(turn["body"]["retry_after_s"], int) and turn["body"]["retry_after_s"] > 0

    # 3. Fallback: `main` fails at its provider and `backup` answers, under a key granted both.
    fallback_key = _mint(client, "f", ["main", "backup"], 5.0, 600)
    before = len(_provider_log(instance, "provider-bad"))
    status, text = _post(base, fallback_key, "/v1/chat/completions", _chat("main"))
    assert status == 200 and "from OK" in text, (status, text[:300])
    assert len(_provider_log(instance, "provider-bad")) > before  # the primary was tried
    turn = _turn(instance, tmp_path, _build(instance, "main", images), fallback_key)
    print("fallback: runtime turn:", json.dumps(turn)[:500])
    assert turn["status"] == 200, turn
    assert turn["body"]["fallbacks"] >= 1 and "backup" in turn["body"]["served_by"], turn
    # A key that is not granted `backup` cannot ask for it by name.
    narrow_key = _mint(client, "n", ["chat"], 5.0, 600)
    status, text = _post(base, narrow_key, "/v1/chat/completions", _chat("backup"))
    assert status in (401, 403), (status, text[:300])
