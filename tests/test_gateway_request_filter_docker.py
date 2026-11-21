"""Opt-in real Docker run of #1105: request-side provider routing on the built-in gateway.

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_gateway_request_filter_docker.py --no-cov -s

`services up` runs postgres, the gateway and two scripted providers that print each request's
path and which imported key it carried (never the key). Alias `a` (and `a-embed`) is served by
provider A on the gateway's OpenAI key; alias `b` by provider B on its Anthropic key. A
deployment key is minted for `a` and `a-embed` only, the way `deploy` mints it, then sends every
client-side parameter of spec §4.2 (more-providers), in the body, nested, as fallbacks and as
headers, and calls the routes that name a provider without an alias.

On `service/litellm@1.3.0` every attempt is refused before any provider call, and B receives
nothing. `service/litellm@1.2.0` is the control: the same harness must see `custom_llm_provider`
honoured there, so the check is shown to catch what it looks for. The one test-only change, as
in tests/test_guardrails_docker.py: the config review accepts `api_base` so the gateway can reach
the scripted providers.
"""

from __future__ import annotations

import json
import os

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

# Stand-ins for the operator's provider keys: the providers print which one they saw.
KEY_A = "-".join(["sk", "provider", "a", "marker"])
KEY_B = "-".join(["sk", "ant", "provider", "b", "marker"])
B_URL = "http://provider-b:8080/v1"

PROVIDER = r"""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NAME = os.environ["PROVIDER_NAME"]
MARKERS = {"A": "-".join(["sk", "provider", "a", "marker"]),
           "B": "-".join(["sk", "ant", "provider", "b", "marker"])}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self._reply({"ok": True})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        headers = " ".join(f"{k}: {v}" for k, v in self.headers.items())
        keys = [n for n, m in MARKERS.items() if m in headers]
        print(f"PROVIDER-{NAME} {self.path} keys={','.join(keys) or '-'}", flush=True)
        data = json.loads(body or b"{}")
        if "embeddings" in self.path:
            self._reply({"object": "list", "model": data.get("model"), "usage": {
                "prompt_tokens": 1, "total_tokens": 1}, "data": [
                {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}]})
        elif "messages" in self.path:
            self._reply({"id": "m1", "type": "message", "role": "assistant", "model": "x",
                         "content": [{"type": "text", "text": "from " + NAME}],
                         "stop_reason": "end_turn",
                         "usage": {"input_tokens": 1, "output_tokens": 1}})
        elif data.get("stream"):
            chunk = {"id": "c1", "object": "chat.completion.chunk", "created": 1,
                     "model": data.get("model"), "choices": [{"index": 0,
                     "delta": {"role": "assistant", "content": "from " + NAME},
                     "finish_reason": "stop"}]}
            payload = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            content = "from " + NAME
            if "response_format" in data:  # the eval judge's strict json_schema
                content = json.dumps({"score": 1, "explanation": "from " + NAME})
            self._reply({"id": "c1", "object": "chat.completion", "created": 1,
                         "model": data.get("model"), "choices": [{"index": 0,
                         "finish_reason": "stop", "message": {"role": "assistant",
                         "content": content}}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                   "total_tokens": 2}})

    def _reply(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
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


def _model(alias: str, model: str, host: str, key: str) -> dict:
    return {
        "model_name": alias,
        "litellm_params": {
            "model": model,
            "api_base": f"http://{host}:8080/v1",  # the test-only widening (module docstring)
            "api_key": f"os.environ/{key}",
        },
    }


CONFIG = {
    "model_list": [
        _model("a", "openai/fake-a", "provider-a", "OPENAI_API_KEY"),
        _model("a-embed", "openai/fake-embed", "provider-a", "OPENAI_API_KEY"),
        _model("b", "anthropic/fake-b", "provider-b", "ANTHROPIC_API_KEY"),
    ],
    "litellm_settings": {"turn_off_message_logging": True, "set_verbose": False},
    "general_settings": {
        "master_key": "os.environ/LITELLM_MASTER_KEY",
        "database_url": "os.environ/DATABASE_URL",
        "store_prompts_in_spend_logs": False,
    },
}


def _stack(version: str) -> dict:
    return {
        "kind": "stack",
        "schema_version": 1,
        "name": "routing",
        "services": {
            "postgres": {
                "ref": "service/postgres-pgvector@1.2.0",
                "with": {"databases": ["litellm", "agent"]},
            },
            "provider-a": {"ref": "service/provider-a@1.0.0"},
            "provider-b": {"ref": "service/provider-b@1.0.0"},
            "litellm": {
                "ref": f"service/litellm@{version}",
                "with": {"config": "litellm.yaml"},
                "wire": {"database-url": "postgres.databases.litellm"},
            },
        },
        "environment": {
            "gateway": "litellm",
            "models": ["a", "a-embed", "b"],
            "ceilings": {"budget_usd": 5, "rpm": 600},
            "agents": {"router-agent": {"sponsor": "lead@example.com", "review_by": "2027-06-30"}},
            "bindings": {"DATABASE_URL": {"ref": "postgres.databases.agent"}},
        },
    }


@pytest.fixture(params=["1.5.0", "1.4.0", "1.3.0", "1.2.0"])
def stack(request, tmp_path, monkeypatch):
    monkeypatch.delenv(registry.PATH_ENV, raising=False)
    monkeypatch.setenv(ROOT_ENV, str(tmp_path / "stacks"))
    monkeypatch.setenv("OPENAI_API_KEY", KEY_A)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY_B)
    monkeypatch.setattr(
        gateway_providers, "COMMON_PARAMS", gateway_providers.COMMON_PARAMS | {"api_base"}
    )
    root = tmp_path / "project"
    for name in ("A", "B"):
        version = root / "registry" / "service" / f"provider-{name.lower()}" / "1.0.0"
        (version / "files").mkdir(parents=True)
        (version / "manifest.yaml").write_text(
            f"kind: service\nname: provider-{name.lower()}\nversion: 1.0.0\n"
        )
        (version / "service.yaml").write_text(yaml.safe_dump(_provider_service(name)))
        (version / "files" / "provider.py").write_text(PROVIDER)
    (root / "litellm.yaml").write_text(yaml.safe_dump(CONFIG, sort_keys=False))
    (root / "stack.yaml").write_text(yaml.safe_dump(_stack(request.param), sort_keys=False))
    instance = Engine(root)
    yield request.param, instance
    try:
        instance.active_deployments = lambda: []
        instance.down(volumes=True)
    finally:
        instance.close()


def _log(instance: Engine, name: str) -> list[str]:
    result = docker("logs", f"{instance.state.project}-{name}-1")
    return [line for line in result.stdout.splitlines() if line.startswith("PROVIDER-")]


def _chat(**extra) -> dict:
    return {"model": "a", "messages": [{"role": "user", "content": "hello"}], **extra}


# Spec §4.2's list, each with a value that would point somewhere other than alias a's route.
FIELDS = {
    "api_base": B_URL,
    "base_url": B_URL,
    "aws_bedrock_runtime_endpoint": B_URL,
    "aws_sts_endpoint": B_URL,
    "aws_region_name": "us-west-2",
    "aws_role_name": "arn:aws:iam::000000000000:role/x",
    "aws_web_identity_token": "t",
    "vertex_credentials": "{}",
    "vertex_project": "other-project",
    "vertex_location": "us-central1",
    "vertex_ai_project": "other-project",
    "vertex_ai_location": "us-central1",
    "vertex_ai_credentials": "{}",
    "azure_ad_token": "t",
    "azure_ad_token_provider": "x",
    "api_version": "2024-01-01",
    "api_key": "sk-client-supplied",
    "organization": "org-other",
    "aws_session_name": "s",
    "litellm_credential_name": "b",
    "model_id": "b",
    "extra_headers": {"x-api-key": "sk-client-supplied"},
    "user_config": {"model_list": [{"model_name": "a", "litellm_params": {"model": "openai/x"}}]},
    "specific_deployment": True,
}
PROVIDERS = ("anthropic", "openai", "gemini", "openrouter", "ollama_chat", "azure", "bedrock")


def _attempts():
    """(label, path, body, headers) for every override attempt."""
    for provider in PROVIDERS:
        yield (
            f"custom_llm_provider={provider}",
            "/v1/chat/completions",
            _chat(custom_llm_provider=provider),
            {},
        )
        yield (
            f"stream custom_llm_provider={provider}",
            "/v1/chat/completions",
            _chat(custom_llm_provider=provider, stream=True),
            {},
        )
    yield (
        "embed custom_llm_provider",
        "/v1/embeddings",
        {
            "model": "a-embed",
            "input": "x",
            "custom_llm_provider": "anthropic",
        },
        {},
    )
    for field, value in FIELDS.items():
        yield f"body {field}", "/v1/chat/completions", _chat(**{field: value}), {}
    for where in ("extra_body", "litellm_params"):
        yield (
            f"{where}.custom_llm_provider",
            "/v1/chat/completions",
            _chat(**{where: {"custom_llm_provider": "anthropic"}}),
            {},
        )
    yield "fallbacks to b", "/v1/chat/completions", _chat(fallbacks=["b"]), {}
    yield (
        "fallbacks dict",
        "/v1/chat/completions",
        _chat(fallbacks=[{"model": "a", "custom_llm_provider": "anthropic"}]),
        {},
    )
    yield "model b", "/v1/chat/completions", {**_chat(), "model": "b"}, {}
    yield "query api-version", "/v1/chat/completions?api-version=2024-01-01", _chat(), {}


def _inert():
    """Attempts LiteLLM ignores (the filter lets them through): they must reach provider A on
    alias a's own path with A's key, and never B."""
    for where in ("metadata", "litellm_metadata"):
        yield (
            f"{where}.custom_llm_provider",
            "/v1/chat/completions",
            _chat(**{where: {"custom_llm_provider": "anthropic"}}),
            {},
        )
    for header in ("x-litellm-api-base", "x-litellm-base-url", "api_base", "x-api-key"):
        yield f"header {header}", "/v1/chat/completions", _chat(), {header: B_URL}
    yield (
        "header openai-organization",
        "/v1/chat/completions",
        _chat(),
        {"OpenAI-Organization": "org-other"},
    )
    # Sub-agent attribution tags (#1058 S0) and tracing metadata pass through.
    tags = {"tags": ["agentbreeder-subagent:billing"], "trace_id": "t-1"}
    yield (
        "extra_body.metadata tags",
        "/v1/chat/completions",
        _chat(extra_body={"metadata": tags}),
        {},
    )
    yield "metadata tags", "/v1/chat/completions", _chat(metadata=tags), {}


SAY = [{"role": "user", "content": "x"}]
# Routes that name a provider without an alias, POST (they reach the filter's hook).
ROUTES = (
    ("POST", "/anthropic/v1/messages", {"model": "fake-b", "max_tokens": 5, "messages": SAY}),
    ("POST", "/openai/v1/chat/completions", {"model": "fake-a", "messages": SAY}),
    ("POST", "/v1/messages", {"model": "a", "max_tokens": 5, "messages": SAY}),
    ("POST", "/v1/responses", {"model": "a", "input": "x"}),
    ("POST", "/v1/completions", {"model": "a", "prompt": "x"}),
    ("POST", "/v1/moderations", {"input": "x"}),
    ("POST", "/v1/batches", {"input_file_id": "f", "endpoint": "/v1/chat/completions",
                             "completion_window": "24h", "custom_llm_provider": "anthropic"}),
)  # fmt: skip
# Versions whose filter serves the Anthropic Messages route (#1089).
MESSAGES_VERSIONS = frozenset({"1.4.0", "1.5.0"})
# Routes that choose a provider from a query parameter or header and never reach a pre-call
# hook: only the key's `allowed_routes` closes them (sent with the deploy-shaped key only).
UNHOOKED = (
    ("GET", "/v1/files", None, {}),
    ("GET", "/v1/files?custom_llm_provider=anthropic", None, {}),
    ("GET", "/v1/files/file-1?custom_llm_provider=anthropic", None, {}),
    ("GET", "/v1/files/file-1/content", None, {"custom-llm-provider": "anthropic"}),
    ("DELETE", "/v1/files/file-1?custom_llm_provider=openai", None, {}),
    ("GET", "/v1/batches?custom_llm_provider=anthropic", None, {}),
    ("GET", "/v1/batches/batch-1?custom_llm_provider=anthropic", None, {}),
    ("POST", "/v1/batches/batch-1/cancel?custom_llm_provider=anthropic", {}, {}),
)
STANDARD = ("/v1/chat/completions", "/chat/completions", "/v1/embeddings", "/embeddings")

AGENT_YAML = {
    "kind": "agent",
    "schema_version": 1,
    "name": "router-agent",
    "runtime": "runtime/adk@1.4.0",
    "model": {"alias": "a", "reasoning": "none", "max_model_calls": 2},
    "prompt": {"path": "prompt.md"},
    "traces": "none",
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
    print(json.dumps({"status": error.code, "body": error.read().decode()[:500]}))
"""


def _send(base: str, key: str, method: str, path: str, body, headers: dict) -> tuple[int, str]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode()[:200]
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()[:200]


def _mint(client, label: str, routes):
    alias = gateway_keys.alias(label * 32)
    key = client.mint(
        alias,
        ["a", "a-embed"],
        1.0,
        600,
        {"agentbreeder": True, "deployment_id": label * 32},
        duration="1h",
        routes=routes,
    )
    return alias, key


def _runtime_turn(instance: Engine, tmp_path, key: str) -> dict:
    """One real runtime/adk@1.4.0 turn on alias `a`, through the gateway, with the key."""
    import uuid
    from pathlib import Path

    from agentbreeder.build import build
    from agentbreeder.operator_config import load_operator_config
    from agentbreeder.services import operator as operator_writer

    root = instance.project
    (root / "agent.yaml").write_text(yaml.safe_dump(AGENT_YAML))
    (root / "prompt.md").write_text("You are brief.\n")
    operator_path = Path(instance.state.env_dir("dev") / "operator.yaml")
    operator = yaml.safe_load(operator_path.read_text())
    gateway_url = operator["gateways"]["litellm"]["agent_url"]
    store = instance.state.deploy_state("dev") / "secrets"
    database_url = (store / operator_writer.binding_secret("DATABASE_URL")).read_text().strip()
    built = build(root / "agent.yaml", load_operator_config(operator_path), "dev")
    secrets = tmp_path / "agent-secrets"
    secrets.mkdir()
    for name, value in {"DATABASE_URL": database_url, "LLM_GATEWAY_KEY": key}.items():
        (secrets / name).write_text(value + "\n")
        os.chmod(secrets / name, 0o444)
    os.chmod(secrets, 0o755)
    name = f"ab-1105-{uuid.uuid4().hex[:8]}"
    docker("run", "-d", "--name", name, "--network", f"{instance.state.project}-agents",
           "-v", f"{secrets}:/run/secrets:ro",
           "-e", "DATABASE_URL_FILE=/run/secrets/DATABASE_URL",
           "-e", "LLM_GATEWAY_KEY_FILE=/run/secrets/LLM_GATEWAY_KEY",
           "-e", f"AGENTBREEDER_GATEWAY_URL={gateway_url}",
           built.image)  # fmt: skip
    try:
        out = docker("exec", name, "python", "-c", INVOKE).stdout.strip().splitlines()[-1]
        return json.loads(out)
    finally:
        docker("rm", "-f", name, check=False)
        docker("image", "rm", "-f", built.image, check=False)


def test_a_key_reaches_only_its_aliases_provider(stack, tmp_path):
    from agentbreeder.evals import Judge

    version, instance = stack
    instance.up("dev", confirm=lambda grant: True)
    master = (instance.state.directory / "service-secrets/litellm/master-key").read_text()
    base = f"http://127.0.0.1:{instance.status()['services']['litellm']['host_ports']['http']}"
    client = gateway_keys.LiteLLM(base, master)
    # As `deploy` mints it (chat + embeddings routes), and with no route limit, so the filter
    # is shown on its own.
    deploy_alias, deploy_key = _mint(client, "d", gateway_keys.key_routes(None))
    open_alias, open_key = _mint(client, "o", None)

    # 1. Both keys work for their own aliases: chat, streamed chat, the judge, embeddings.
    for key in (deploy_key, open_key):
        for body in (_chat(), _chat(stream=True)):
            status, text = _post(base, key, "/v1/chat/completions", body)
            assert status == 200 and "from A" in text, (status, text[:300])
        # LiteLLM refuses `dimensions` for an `openai/` embedding model before any call.
        status, text = _post(base, key, "/v1/embeddings", {"model": "a-embed", "input": ["x"]})
        assert status == 200, text[:300]
        assert Judge(base, key, "a").grade("grade this") == (1.0, "from A")
    assert {line.split()[1] for line in _log(instance, "provider-a")} <= set(STANDARD)
    baseline_a = len(_log(instance, "provider-a"))

    # 2. The no-limit key: every override attempt, and the routes that reach the hook.
    results = {}
    for label, path, body, headers in _attempts():
        results[label] = _post(base, open_key, path, body, headers)
    # 1.4.0 and 1.5.0 serve `/v1/messages` (#1089): tests/test_gateway_messages_filter_docker.py
    # checks it there, so this harness sends the other routes only.
    routes = [r for r in ROUTES if version not in MESSAGES_VERSIONS or r[1] != "/v1/messages"]
    if version != "1.2.0":  # on 1.2.0 these would leave for the providers' real endpoints
        for method, path, body in routes:
            results[f"{method} {path}"] = _send(base, open_key, method, path, body, {})
    seen_a = _log(instance, "provider-a")[baseline_a:]
    seen_b = _log(instance, "provider-b")
    print(f"\n[{version}] statuses:", json.dumps({k: v[0] for k, v in results.items()}))
    print(f"[{version}] provider A after the baseline:", json.dumps(seen_a))
    print(f"[{version}] provider B:", json.dumps(seen_b))
    if version == "1.2.0":
        # The control: on the version without the filter the same attempts change the route
        # (another provider's API on A's endpoint), so this harness catches the gap.
        honoured = [line for line in seen_a if line.split()[1] not in STANDARD]
        assert honoured, (seen_a, results)
        print(f"[{version}] honoured (control):", json.dumps(honoured))
        return

    # 3. 1.3.0: refused before any provider call; neither provider saw anything.
    assert seen_a == [] and seen_b == [], (seen_a, seen_b)
    inert = {label: _post(base, open_key, path, body, headers)
             for label, path, body, headers in _inert()}  # fmt: skip
    print(f"[{version}] ignored by LiteLLM:", json.dumps({k: v[0] for k, v in inert.items()}))
    assert all(s == 200 and "from A" in t for s, t in inert.values()), inert
    served = _log(instance, "provider-a")[baseline_a:]
    assert served == ["PROVIDER-A /v1/chat/completions keys=A"] * len(inert), served
    assert _log(instance, "provider-b") == []
    baseline_a = len(_log(instance, "provider-a"))
    assert all(400 <= status < 500 for status, _ in results.values()), results
    for provider in PROVIDERS:
        status, text = results[f"custom_llm_provider={provider}"]
        assert status == 400 and "GATEWAY_PARAM_REFUSED" in text, (provider, status, text)
    for method, path, _ in routes:
        status, text = results[f"{method} {path}"]
        assert status in (400, 401, 403, 404) and ("GATEWAY_" in text or status != 400), path
    # These reach the filter's hook in the recorded run: its own refusal, not LiteLLM's.
    for path in ("/v1/messages", "/v1/responses", "/v1/completions", "/v1/moderations"):
        if f"POST {path}" not in results:  # /v1/messages on 1.4.0 and 1.5.0
            continue
        status, text = results[f"POST {path}"]
        assert status == 400 and "GATEWAY_ROUTE_REFUSED" in text, (path, status, text)
    filtered = sorted(k for k, (s, t) in results.items() if s == 400 and "GATEWAY_" in t)
    print(f"[{version}] refused by the filter: {len(filtered)} of {len(results)}")

    # 4. The deploy-shaped key: also the routes that never reach the hook, and the overrides.
    unhooked = {}
    for method, path, body, headers in UNHOOKED:
        unhooked[f"{method} {path}"] = _send(base, deploy_key, method, path, body, headers)[0]
    for method, path, body in ROUTES:
        unhooked[f"{method} {path}"] = _send(base, deploy_key, method, path, body, {})[0]
    for label, path, body, headers in _attempts():
        unhooked[label] = _post(base, deploy_key, path, body, headers)[0]
    print(f"[{version}] deploy key statuses:", json.dumps(unhooked))
    assert all(400 <= status < 500 for status in unhooked.values()), unhooked
    assert _log(instance, "provider-a")[baseline_a:] == [] and _log(instance, "provider-b") == []
    for label, path, body, headers in _inert():
        status, text = _post(base, deploy_key, path, body, headers)
        assert status == 200 and "from A" in text, (label, status, text)
    served = _log(instance, "provider-a")[baseline_a:]
    assert set(served) == {"PROVIDER-A /v1/chat/completions keys=A"}, served
    assert _log(instance, "provider-b") == []
    baseline_a = len(_log(instance, "provider-a"))

    # 5. A real runtime/adk@1.4.0 turn through the filtered gateway on the deploy-shaped key.
    turn = _runtime_turn(instance, tmp_path, deploy_key)
    print(f"[{version}] runtime turn:", json.dumps({"status": turn["status"]}))
    assert turn["status"] == 200, turn
    assert "from A" in json.dumps(turn["body"]), turn
    runtime_paths = {line.split()[1] for line in _log(instance, "provider-a")[baseline_a:]}
    print(f"[{version}] runtime paths at provider A:", sorted(runtime_paths))
    assert runtime_paths and runtime_paths <= set(STANDARD)

    # 6. The log lines are there, with no key material.
    logs = docker("logs", f"{instance.state.project}-litellm-1")
    text = logs.stdout + logs.stderr
    assert "refused GATEWAY_PARAM_REFUSED field=custom_llm_provider in=body" in text
    # Provider pass-through got LiteLLM's own 403 before any hook in the recorded run; the
    # other routes reached the filter.
    route_calls = sorted(
        {
            line.split(" call=")[1].split()[0]
            for line in text.splitlines()
            if "refused GATEWAY_ROUTE_REFUSED" in line
        }
    )
    print(f"[{version}] route refusals by call type:", route_calls)
    assert route_calls
    assert f"key_alias={open_alias}" in text
    # Neither key material nor the values the refused requests carried.
    refused_values = ("sk-client-supplied", "org-other", "other-project")
    for secret in (KEY_A, KEY_B, master, deploy_key, open_key, *refused_values):
        assert secret not in text
    filter_lines = [line for line in text.splitlines() if "agentbreeder gateway: refused" in line]
    print(f"[{version}] filter log lines: {len(filter_lines)}; first: {filter_lines[0]}")
    client.revoke(deploy_alias)
    client.revoke(open_alias)


def test_the_filter_lists_every_credential_param_of_the_pinned_image(monkeypatch):
    """CredentialLiteLLMParams as the pinned gateway image defines it, against the filter and
    the recorded list (a LiteLLM bump cannot open a gap silently)."""
    from test_gateway_request_filter import CREDENTIAL_PARAMS, V13, _load

    image = yaml.safe_load((V13 / "service.yaml").read_text())["image"]
    script = (
        "import json\n"
        "from litellm.types.router import CredentialLiteLLMParams as C\n"
        "print(json.dumps(sorted(C.model_fields)))"
    )
    out = docker(
        "run", "--rm", "--network", "none", "--entrypoint", "/app/.venv/bin/python",
        image, "-c", script,
    ).stdout  # fmt: skip
    fields = set(json.loads(out.strip().splitlines()[-1]))
    rules = _load(
        "agentbreeder_request_filter", V13 / "files/agentbreeder_request_filter.py", monkeypatch
    )
    print("\nCredentialLiteLLMParams fields in the pinned image:", len(fields))
    assert fields == set(CREDENTIAL_PARAMS), sorted(fields ^ set(CREDENTIAL_PARAMS))
    assert fields <= rules.ROUTING_FIELDS, sorted(fields - rules.ROUTING_FIELDS)
