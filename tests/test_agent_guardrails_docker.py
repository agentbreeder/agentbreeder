"""Opt-in real Docker run of #1053: agent-level guardrails on LiteLLM's policy engine.

AGENTBREEDER_DOCKER_TEST=1 pytest tests/test_agent_guardrails_docker.py --no-cov -s

The stack is tests/test_guardrails_docker.py's (postgres, both Presidio services,
service/litellm@1.1.0 and a scripted provider), with one optional guardrail added to the
gateway config. Two deployment keys are minted and set up the way `deploy` does it
(`key_routes`, `mint`, `attach_guardrails`): agent X names the optional guardrail, sibling agent
Y names none. All PII is synthetic.
"""

from __future__ import annotations

import copy
import json
import os

import pytest
import yaml
from conftest import GATEWAY_CAPS
from test_guardrails_docker import CONFIG, EMAIL, IP, SEEDED, SSN, _chat, _post, docker
from test_guardrails_docker import project as g1b_project  # noqa: F401  (fixture)

from agentbreeder import gateway_keys
from agentbreeder.gate import _agent_guardrails_failure
from agentbreeder.operator_config import DeploymentContext, load_operator_config

pytestmark = pytest.mark.skipif(
    os.getenv("AGENTBREEDER_DOCKER_TEST") != "1", reason="Opt-in real Docker test"
)

IP_BLOCK = {
    "guardrail_name": "agent-ip-block",
    "litellm_params": {
        "guardrail": "presidio",
        "mode": "pre_call",
        "default_on": False,
        "pii_entities_config": {"IP_ADDRESS": "BLOCK"},
        "presidio_language": "en",
        "timeout": 30,
    },
}
AGENT_CONFIG = copy.deepcopy(CONFIG)
AGENT_CONFIG["guardrails"].append(IP_BLOCK)
X = gateway_keys.alias("a" * 32)
Y = gateway_keys.alias("b" * 32)


@pytest.fixture
def project(g1b_project):  # noqa: F811
    root, instance, _ = g1b_project
    config = root / "litellm.yaml"
    config.write_text(yaml.safe_dump(AGENT_CONFIG, sort_keys=False))
    approved = gateway_keys.config_review(config, GATEWAY_CAPS).guardrails_sha256
    stack = yaml.safe_load((root / "stack.yaml").read_text())
    stack["environment"]["guardrails"]["approved_sha256"] = approved
    (root / "stack.yaml").write_text(yaml.safe_dump(stack, sort_keys=False))
    return root, instance, approved


def _provider_log(instance) -> str:
    return docker("logs", f"{instance.state.project}-provider-1").stdout


def test_agent_guardrails_add_to_the_floor_for_one_key_only(project):
    root, instance, approved = project
    instance.up("dev", confirm=lambda grant: True)
    operator = load_operator_config(instance.state.env_dir("dev") / "operator.yaml")
    environment = operator.model.environments["dev"]
    floor = environment.guardrails
    assert floor.running_sha256 == approved and floor.verified == ["presidio:pre_call"]
    assert floor.optional == ["agent-ip-block"]
    context = DeploymentContext("dev", "local")
    assert _agent_guardrails_failure(environment, ["agent-ip-block"], context) is None
    assert _agent_guardrails_failure(environment, ["pii-block"], context)[0] == (
        "GUARDRAIL_UNDEFINED"
    )  # a default_on guardrail is the floor's, not an agent's

    master = (instance.state.directory / "service-secrets/litellm/master-key").read_text()
    base = f"http://127.0.0.1:{instance.status()['services']['litellm']['host_ports']['http']}"
    client = gateway_keys.LiteLLM(base, master)
    keys = {}
    for alias, agent in ((X, ["agent-ip-block"]), (Y, None)):
        keys[alias] = client.mint(
            alias,
            ["chat", "embed"],
            1.0,
            600,
            {"agentbreeder": True, "deployment_id": alias.removeprefix("agentbreeder-")},
            duration="1h",
            routes=gateway_keys.key_routes(environment, agent),
        )
        if agent:
            client.attach_guardrails(alias, agent)
    x, y = keys[X], keys[Y]
    assert client.guardrail_drift(X, ["agent-ip-block"]) is None
    assert client.guardrail_drift(Y, []) is None

    # 1. The floor applies to both: an email is masked, an SSN blocked.
    for key in (x, y):
        status, body = _post(base, key, "/v1/chat/completions", _chat(f"mail {EMAIL}"))
        assert status == 200, body
        status, body = _post(base, key, "/v1/chat/completions", _chat(f"ssn {SSN}"))
        assert status == 400 and "pii-block" in body
    assert EMAIL not in _provider_log(instance) and SSN not in _provider_log(instance)

    # 2. X's own guardrail blocks X's call; sibling Y is unaffected.
    before = _provider_log(instance)
    status, body = _post(base, x, "/v1/chat/completions", _chat(f"host {IP}"))
    assert status == 400 and "agent-ip-block" in body and IP not in body, body
    assert _provider_log(instance) == before
    status, body = _post(base, y, "/v1/chat/completions", _chat(f"host {IP}"))
    assert status == 200, body
    assert IP in _provider_log(instance)  # Y's call reached the provider unchanged
    status, body = _post(base, x, "/v1/embeddings", {"model": "embed", "input": f"host {IP}"})
    assert status == 400, body
    status, body = _post(base, x, "/v1/chat/completions", _chat(f"host {IP}", stream=True))
    assert status == 400, body

    # 3. A request cannot drop either level.
    attempts = (
        ({"guardrails": []}, {}),
        ({"metadata": {"guardrails": []}}, {}),
        ({"policies": []}, {}),
        ({"metadata": {"policies": []}}, {}),
        ({"disable_global_guardrails": True}, {}),
        ({"metadata": {"opted_out_global_guardrails": ["pii-block", "agent-ip-block"]}}, {}),
        ({"metadata": {"user_api_key_alias": Y}}, {}),
        ({"tags": ["other"]}, {"x-litellm-tags": "other"}),
        ({}, {"x-litellm-disable-guardrails": "true"}),
    )
    statuses = []
    for extra, headers in attempts:
        for text in (f"host {IP}", f"ssn {SSN}"):
            status, _ = _post(base, x, "/v1/chat/completions", _chat(text, **extra), headers)
            statuses.append(status)
    print("override statuses:", json.dumps(statuses))
    assert set(statuses) == {400}, statuses

    # 4. Neither agent key can touch the policy engine.
    refused = {}
    for key, name in ((x, "x"), (y, "y")):
        for method, path, body in (
            ("POST", "/policies", {"policy_name": "w", "guardrails_remove": ["pii-block"]}),
            ("POST", "/policies/attachments", {"policy_name": X, "scope": "*"}),
            ("GET", "/policies/list", None),
            ("GET", "/policies/attachments/list", None),
            ("DELETE", f"/policies/name/{X}/all-versions", None),
        ):
            refused[f"{name} {method} {path}"] = _request(base, key, method, path, body)
    print("policy route statuses:", json.dumps(refused))
    assert all(status in (401, 403) for status in refused.values()), refused
    assert client.guardrail_drift(X, ["agent-ip-block"]) is None

    # 5. The attachment is what blocked X: detached, X's IP passes and drift is reported.
    client.detach_guardrails(X)
    status, body = _post(base, x, "/v1/chat/completions", _chat(f"host {IP}"))
    assert status == 200, body
    assert client.guardrail_drift(X, ["agent-ip-block"]) is not None
    client.revoke(X)
    client.revoke(Y)

    # 6. No seeded PII in the gateway or Presidio logs.
    for name in ("litellm", "analyzer", "anonymizer"):
        logs = docker("logs", f"{instance.state.project}-{name}-1")
        text = logs.stdout + logs.stderr
        assert not [s for s in SEEDED if s in text], name


def _request(base: str, key: str, method: str, path: str, body: dict | None) -> int:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
