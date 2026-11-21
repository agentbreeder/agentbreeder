"""Opt-in real Docker run of #1089: the Anthropic Messages route on `service/litellm@1.4.0`.

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_gateway_messages_filter_docker.py --no-cov -s

The harness is tests/test_gateway_request_filter_docker.py's (#1105): `services up` runs postgres,
the gateway and two scripted providers that print each request's path and which imported key
it carried (never the key). Alias `a` is an OpenAI model on provider A, alias `m` an Anthropic
model on provider B. One key is minted the way `deploy` mints it for a
`model_api: anthropic-messages` deployment (chat, embeddings and `/v1/messages`), one with the
chat and embeddings routes only.

On 1.4.0 (and 1.5.0, which carries its filter) a plain Messages request reaches its alias's
provider, on the Anthropic route (`m`) and translated for the OpenAI model (`a`), which LiteLLM
1.106.2 sends to the provider's Responses API (`/v1/responses`), not to chat completions, so
this test's providers also answer that path, plain and streamed (`PROVIDER`). Every request
carrying a refused field, tool, header or content gets the filter's coded 400, and no provider
sees it.
`service/litellm@1.3.0` is the control: the same plain request is refused there
(`GATEWAY_ROUTE_REFUSED`). The test-only widening of the config review (`api_base`) is the
1.3.0 test's.
"""

from __future__ import annotations

import json
import os

import pytest
import yaml
from test_gateway_request_filter_docker import (
    KEY_A,
    KEY_B,
    _log,
    _model,
    _provider_service,
)
from test_gateway_request_filter_docker import PROVIDER as CHAT_PROVIDER
from test_guardrails_docker import _post, docker

from agentbreeder import gateway_keys, gateway_providers, registry
from agentbreeder.services.engine import Engine
from agentbreeder.services.state import ROOT_ENV

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)

# LiteLLM turns a Messages request for an OpenAI model into a Responses API call. #1105's
# providers answer every non-Messages path as chat completions, which LiteLLM cannot parse there.
RESPONSES_BRANCH = r"""
        elif self.path.endswith("/responses"):
            text = "from " + NAME
            item = {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}
            done = {"id": "resp_1", "object": "response", "created_at": 1, "status": "completed",
                    "model": data.get("model"), "output": [item],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
            if not data.get("stream"):
                self._reply(done)
                return
            started = dict(done, status="in_progress", output=[], usage=None)
            part = {"type": "output_text", "text": "", "annotations": []}
            where = {"item_id": "msg_1", "output_index": 0, "content_index": 0}
            events = [
                {"type": "response.created", "response": started},
                {"type": "response.in_progress", "response": started},
                {"type": "response.output_item.added", "output_index": 0,
                 "item": dict(item, status="in_progress", content=[])},
                dict(where, type="response.content_part.added", part=part),
                dict(where, type="response.output_text.delta", delta=text),
                dict(where, type="response.output_text.done", text=text),
                dict(where, type="response.content_part.done", part=dict(part, text=text)),
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": done},
            ]
            payload = "".join(
                "event: " + event["type"] + "\ndata: "
                + json.dumps(dict(event, sequence_number=number)) + "\n\n"
                for number, event in enumerate(events)
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
"""
STREAM_BRANCH = '        elif data.get("stream"):\n'
assert CHAT_PROVIDER.count(STREAM_BRANCH) == 1
PROVIDER = CHAT_PROVIDER.replace(STREAM_BRANCH, RESPONSES_BRANCH.lstrip("\n") + STREAM_BRANCH)

CONFIG = {
    "model_list": [
        _model("a", "openai/fake-a", "provider-a", "OPENAI_API_KEY"),
        _model("m", "anthropic/fake-m", "provider-b", "ANTHROPIC_API_KEY"),
    ],
    "litellm_settings": {"turn_off_message_logging": True, "set_verbose": False},
    "general_settings": {
        "master_key": "os.environ/LITELLM_MASTER_KEY",
        "database_url": "os.environ/DATABASE_URL",
        "store_prompts_in_spend_logs": False,
    },
}
SAY = [{"role": "user", "content": "hello"}]
CLIENT_TOOL = {
    "name": "lookup_order",
    "description": "Look up an order",
    "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}},
}
CLIENT_SECRET = "-".join(["sk", "client", "supplied"])  # must never reach a log


def _messages(model: str = "m", **extra) -> dict:
    return {"model": model, "max_tokens": 64, "messages": SAY, **extra}


def _stack(version: str) -> dict:
    return {
        "kind": "stack",
        "schema_version": 1,
        "name": "messages",
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
            "models": ["a", "m"],
            "ceilings": {"budget_usd": 5, "rpm": 600},
            "agents": {"claude-agent": {"sponsor": "lead@example.com", "review_by": "2027-06-30"}},
            "bindings": {"DATABASE_URL": {"ref": "postgres.databases.agent"}},
        },
    }


@pytest.fixture(params=["1.5.0", "1.4.0", "1.3.0"])
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


def _refused_attempts():
    """(label, body, headers, field): each must get the filter's GATEWAY_PARAM_REFUSED."""
    mcp = [{"type": "url", "url": "https://mcp.example/sse", "name": "x"}]
    file_image = {"type": "image", "source": {"type": "file", "file_id": "file_x"}}
    yield "mcp_servers", _messages(mcp_servers=mcp), {}, "mcp_servers"
    yield "container", _messages(container={"skills": []}), {}, "container"
    yield "service_tier", _messages(service_tier="priority"), {}, "service_tier"
    yield "inference_geo", _messages(inference_geo="us"), {}, "inference_geo"
    yield "speed", _messages(speed="fast"), {}, "speed"
    yield "betas", _messages(betas=["mcp-client-2025-04-04"]), {}, "betas"
    yield "anthropic_beta", _messages(anthropic_beta=["x"]), {}, "anthropic_beta"
    for tool in (
        {"type": "web_search_20250305", "name": "web_search"},
        {"type": "code_execution_20250825", "name": "code_execution"},
        {"type": "bash_20250124", "name": "bash"},
        {"type": "mcp", "server_url": "litellm_proxy", "server_label": "x"},
    ):
        yield f"tool {tool['type']}", _messages(tools=[CLIENT_TOOL, tool]), {}, "tools"
    yield (
        "header anthropic-beta",
        _messages(),
        {"anthropic-beta": "code-execution-2025-08-25"},
        "provider_specific_header",
    )
    yield (
        "body provider_specific_header",
        _messages(
            provider_specific_header={
                "custom_llm_provider": "anthropic",
                "extra_headers": {"x-api-key": CLIENT_SECRET},
            }
        ),
        {},
        "provider_specific_header",
    )
    files = [{"role": "user", "content": [file_image]}]
    yield "files api source", _messages(messages=files), {}, "messages"
    url_image = {"type": "image", "source": {"type": "url", "url": "https://example.com/a.png"}}
    urls = [{"role": "user", "content": [url_image]}]
    yield "url source", _messages(messages=urls), {}, "messages"
    yield "compaction", _messages(compaction={"type": "auto"}), {}, "compaction"
    yield "custom_llm_provider", _messages(custom_llm_provider="openai"), {}, "custom_llm_provider"
    yield "extra_body api_key", _messages(extra_body={"api_key": CLIENT_SECRET}), {}, "api_key"


def files_request() -> list:
    block = {"type": "image", "source": {"type": "file", "file_id": "file_x"}}
    return [{"role": "user", "content": [block]}]


def test_the_messages_route_is_filtered(stack):
    version, instance = stack
    instance.up("dev", confirm=lambda grant: True)
    master = (instance.state.directory / "service-secrets/litellm/master-key").read_text()
    base = f"http://127.0.0.1:{instance.status()['services']['litellm']['host_ports']['http']}"
    client = gateway_keys.LiteLLM(base, master)
    messages_alias = gateway_keys.alias("c" * 32)
    messages_key = client.mint(
        messages_alias,
        ["a", "m"],
        1.0,
        600,
        {"agentbreeder": True, "deployment_id": "c" * 32},
        duration="1h",
        routes=gateway_keys.key_routes(None, None, model_api="anthropic-messages"),
    )
    chat_alias = gateway_keys.alias("g" * 32)
    chat_key = client.mint(
        chat_alias,
        ["a", "m"],
        1.0,
        600,
        {"agentbreeder": True, "deployment_id": "g" * 32},
        duration="1h",
        routes=gateway_keys.key_routes(None),
    )

    # 1. Chat is served on both keys, on both versions.
    for key in (messages_key, chat_key):
        status, text = _post(base, key, "/v1/chat/completions", {"model": "a", "messages": SAY})
        assert status == 200 and "from A" in text, (status, text[:300])
    # A key without `/v1/messages` is refused by LiteLLM's route check (F0, D17).
    status, text = _post(base, chat_key, "/v1/messages", _messages())
    print(f"\n[{version}] chat-only key on /v1/messages: {status}")
    assert status in (401, 403), (status, text[:300])
    before_a, before_b = len(_log(instance, "provider-a")), len(_log(instance, "provider-b"))

    plain = {
        "anthropic": _post(base, messages_key, "/v1/messages", _messages(tools=[CLIENT_TOOL])),
        "translated": _post(base, messages_key, "/v1/messages", _messages("a")),
        "streamed": _post(base, messages_key, "/v1/messages", _messages("a", stream=True)),
        "api version": _post(
            base, messages_key, "/v1/messages", _messages(), {"anthropic-version": "2023-06-01"}
        ),
    }
    print(f"[{version}] plain statuses:", json.dumps({k: v[0] for k, v in plain.items()}))
    if version == "1.3.0":
        # The control: the 1.3.0 filter refuses the call type before any provider call.
        for status, text in plain.values():
            assert status == 400 and "GATEWAY_ROUTE_REFUSED" in text, (status, text[:300])
        assert len(_log(instance, "provider-a")) == before_a
        assert len(_log(instance, "provider-b")) == before_b
        return

    # 2. 1.4.0: a plain Messages request reaches its alias's provider with that provider's key.
    for label, (status, text) in plain.items():
        assert status == 200, (label, status, text[:300])
    assert "from B" in plain["anthropic"][1] and "from A" in plain["translated"][1]
    # A failed stream still answers 200, with an error event in the body.
    assert "from A" in plain["streamed"][1] and "event: error" not in plain["streamed"][1]
    seen_a = _log(instance, "provider-a")[before_a:]
    seen_b = _log(instance, "provider-b")[before_b:]
    print(f"[{version}] provider A:", json.dumps(seen_a), "provider B:", json.dumps(seen_b))
    assert seen_b and all("messages" in line and line.endswith("keys=B") for line in seen_b)
    assert seen_a and all(line.endswith("keys=A") for line in seen_a)
    before_a, before_b = len(_log(instance, "provider-a")), len(_log(instance, "provider-b"))

    # 3. Every refused field, tool, header and content: the filter's 400, and no provider call.
    results = {}
    for label, body, headers, field in _refused_attempts():
        status, text = _post(base, messages_key, "/v1/messages", body, headers)
        results[label] = status
        assert status == 400 and "GATEWAY_PARAM_REFUSED" in text, (label, status, text[:300])
        assert f"({field})" in text, (label, text[:300])
    print(f"[{version}] refused:", json.dumps(results))
    assert _log(instance, "provider-a")[before_a:] == []
    assert _log(instance, "provider-b")[before_b:] == []

    # 4. Recorded, not asserted: `/v1/messages/count_tokens` is reachable with a key allowed
    # `/v1/messages` (LiteLLM's prefix match) and calls no pre-call hook (a known residual).
    counted = _post(
        base, messages_key, "/v1/messages/count_tokens", _messages(messages=files_request())
    )
    print(f"[{version}] count_tokens with a Files API source: {counted[0]} {counted[1][:120]}")

    # 5. The log lines are there, with no key material and no client values.
    logs = docker("logs", f"{instance.state.project}-litellm-1")
    text = logs.stdout + logs.stderr
    lines = [line for line in text.splitlines() if "agentbreeder gateway: refused" in line]
    print(f"[{version}] filter log lines: {len(lines)}; first: {lines[0] if lines else '-'}")
    assert f"field=mcp_servers in=body call=anthropic_messages key_alias={messages_alias}" in text
    for secret in (KEY_A, KEY_B, master, messages_key, chat_key, CLIENT_SECRET):
        assert secret not in text
    client.revoke(messages_alias)
    client.revoke(chat_alias)
