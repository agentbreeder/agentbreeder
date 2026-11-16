"""#1053: agent-level guardrails in `agent.yaml` on top of the environment floor, attached to the
deployment's gateway key through LiteLLM's policy engine (spec §12.8).

The gateway is faked here (keys plus the policy routes, with LiteLLM 1.106.2's rules: an
attachment must name an existing key alias, a policy name is unique). The real-Docker test is in
tests/test_guardrails_docker.py.
"""

from __future__ import annotations

import copy
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

import pytest
import yaml
from conftest import GATEWAY_CAPS, actor, v2_manifest_data
from test_guardrails_g1b import (
    CONFIG,
    PII_MASK,
    STACK,
    _guarded,
    _review,
    _up,
    _write,
    stack,  # noqa: F401  (fixture)
)
from test_services_engine import FakeDocker, engine  # noqa: F401

from agentbreeder import gate as gate_module
from agentbreeder import gateway_keys
from agentbreeder.agentfile import AgentFile
from agentbreeder.build import bind, template
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied, revision_digest
from agentbreeder.gateway_keys import GatewayError, config_review, guardrail_drift, policy_body
from agentbreeder.manifest import IdentityReference, Manifest
from agentbreeder.package import DeployTemplate

MASTER = "master-for-tests"
SHA = "a" * 64
PHONE_BLOCK = {
    "guardrail_name": "agent-phone-block",
    "litellm_params": {
        "guardrail": "presidio",
        "mode": "pre_call",
        "default_on": False,
        "pii_entities_config": {"US_SSN": "MASK", "IP_ADDRESS": "BLOCK"},
    },
}
AGENT_CONFIG = {**CONFIG, "guardrails": [*CONFIG["guardrails"], PHONE_BLOCK]}


# -- a LiteLLM key + policy API ----------------------------------------------------------------


class PolicyGateway:
    def __init__(self):
        self.keys: dict[str, dict] = {}
        self.policies: dict[str, dict] = {}  # policy_id -> row
        self.attachments: dict[str, dict] = {}  # attachment_id -> row
        self.calls: list[tuple[str, str, dict | None]] = []
        self.fail: set[str] = set()  # paths that answer 500
        # guardrails the running gateway defines (`/guardrails/list`)
        self.defined = {"pii-mask", "pii-block", "agent-phone-block", "one", "two"}
        self.unresolved = False  # `/policies/resolve` ignores DB policies (a stale worker)
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

            def authorized(self, method):
                body = None
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    body = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path
                gateway.calls.append((method, path, body))
                if self.headers.get("Authorization") != f"Bearer {MASTER}":
                    self.reply(401, {"error": "auth"})
                    return None
                if path in gateway.fail:
                    self.reply(500, {"error": "boom"})
                    return None
                return path, body

            def do_POST(self):
                found = self.authorized("POST")
                if found is None:
                    return
                path, body = found
                if path == "/key/generate":
                    gateway.keys[body["key_alias"]] = {**body, "key": "vk", "spend": 0.0}
                    self.reply(200, {"key": "vk-" + body["key_alias"]})
                elif path == "/key/delete":
                    hit = [a for a in body["key_aliases"] if gateway.keys.pop(a, None)]
                    self.reply(200 if hit else 404, {"deleted_keys": hit})
                elif path == "/policies":
                    if any(
                        p["policy_name"] == body["policy_name"] for p in gateway.policies.values()
                    ):
                        self.reply(400, {"detail": "exists"})
                        return
                    row = {
                        "policy_id": uuid.uuid4().hex,
                        "version_status": "production",
                        "guardrails_add": [],
                        "guardrails_remove": [],
                        "inherit": None,
                        "condition": None,
                        "pipeline": None,
                        **body,
                    }
                    gateway.policies[row["policy_id"]] = row
                    self.reply(200, row)
                elif path == "/policies/resolve":
                    found = set()
                    for a in gateway.attachments.values():
                        if body["key_alias"] in a.get("keys", []) and not gateway.unresolved:
                            for p in gateway.policies.values():
                                if p["policy_name"] == a["policy_name"]:
                                    found |= set(p.get("guardrails_add") or [])
                    self.reply(200, {"effective_guardrails": sorted(found)})
                elif path == "/policies/attachments":
                    if not any(
                        p["policy_name"] == body["policy_name"] for p in gateway.policies.values()
                    ):
                        self.reply(404, {"detail": "no policy"})
                        return
                    if any(k not in gateway.keys for k in body.get("keys", [])):
                        self.reply(400, {"detail": "no such key"})
                        return
                    row = {"attachment_id": uuid.uuid4().hex, "scope": None, "teams": [],
                           "keys": [], "models": [], "tags": [], **body}  # fmt: skip
                    gateway.attachments[row["attachment_id"]] = row
                    self.reply(200, row)
                else:
                    self.reply(404, {})

            def do_GET(self):
                found = self.authorized("GET")
                if found is None:
                    return
                path, _ = found
                if path == "/guardrails/list":
                    rows = [{"guardrail_name": n} for n in sorted(gateway.defined)]
                    self.reply(200, {"guardrails": rows})
                elif path == "/policies/list":
                    self.reply(200, {"policies": list(gateway.policies.values())})
                elif path == "/policies/attachments/list":
                    self.reply(200, {"attachments": list(gateway.attachments.values())})
                elif path == "/key/list":
                    rows = [{"key_alias": a, **k} for a, k in gateway.keys.items()]
                    self.reply(200, {"keys": rows, "total_pages": 1})
                else:
                    self.reply(404, {})

            def do_DELETE(self):
                found = self.authorized("DELETE")
                if found is None:
                    return
                path, _ = found
                parts = [unquote(p) for p in path.split("/")]
                if path.startswith("/policies/attachments/"):
                    gone = gateway.attachments.pop(parts[3], None)
                    self.reply(200 if gone else 404, {})
                elif path.startswith("/policies/name/") and path.endswith("/all-versions"):
                    name = parts[3]
                    for pid in [
                        i for i, p in gateway.policies.items() if p["policy_name"] == name
                    ]:
                        del gateway.policies[pid]
                    self.reply(200, {"message": "deleted"})
                else:
                    self.reply(404, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def gateway():
    fake = PolicyGateway()
    yield fake
    fake.server.shutdown()


def _floor(optional=("agent-phone-block",), **extra):
    record = {
        "required": True,
        "approved_sha256": SHA,
        "running_sha256": SHA,
        "effective": ["presidio:pre_call"],
        **extra,
    }
    if optional:
        record["optional"] = list(optional)
    return record


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
            "models": ["support-model"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
            "guardrails": _floor(),
        }
    )
    return identity_env


def _manifest(guardrails=("agent-phone-block",), name="sample"):
    data = v2_manifest_data(
        name=name, model_access={"gateway": "litellm-local", "aliases": ["support-model"]}
    )
    if guardrails is not None:
        data["guardrails"] = list(guardrails)
    return Manifest.model_validate(data)


def _running(runtime, deploys=1):
    info = {"Id": "c", "State": {"Running": True}}
    runtime.inspect.side_effect = ([None] + [info] * 6) * deploys


def _deploy(minting, state, runtime, manifest=None, deployer=None):
    _running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    return service, service.deploy(manifest or _manifest(), local=True, deployer=deployer)


# -- config review: optional guardrails ---------------------------------------------------------


def test_an_optional_guardrail_is_reviewed_and_never_counts_toward_the_floor(tmp_path):
    review = _review(tmp_path, AGENT_CONFIG)
    assert review.optional() == ["agent-phone-block"]
    assert review.effective() == ["presidio:pre_call"]
    alone = copy.deepcopy(CONFIG)
    alone["guardrails"] = [PHONE_BLOCK]
    review = _review(tmp_path, alone)
    assert review.effective() == [] and review.optional() == ["agent-phone-block"]
    with pytest.raises(GatewayError, match="GUARDRAILS_INEFFECTIVE"):
        review.guardrails_effective(True)


def test_a_logging_only_optional_guardrail_is_not_offered(tmp_path):
    data = copy.deepcopy(AGENT_CONFIG)
    data["guardrails"][2]["litellm_params"]["mode"] = "logging_only"
    assert _review(tmp_path, data).optional() == []


@pytest.mark.parametrize(
    "entities",
    [
        {"EMAIL_ADDRESS": "BLOCK"},  # the floor masks it first (default_on run first)
        {"PHONE_NUMBER": "BLOCK"},
    ],
)
def test_an_optional_guardrail_cannot_block_what_the_floor_masks(tmp_path, entities):
    data = copy.deepcopy(AGENT_CONFIG)
    data["guardrails"][2]["litellm_params"]["pii_entities_config"] = entities
    with pytest.raises(GatewayError, match="blocks an entity another guardrail masks"):
        _review(tmp_path, data)


def test_an_optional_guardrail_cannot_block_what_another_optional_masks(tmp_path):
    data = copy.deepcopy(AGENT_CONFIG)
    other = copy.deepcopy(PHONE_BLOCK)
    other["guardrail_name"] = "agent-ip-mask"
    other["litellm_params"]["pii_entities_config"] = {"IP_ADDRESS": "MASK"}
    data["guardrails"].append(other)
    with pytest.raises(GatewayError, match="another guardrail masks"):
        _review(tmp_path, data)


def test_an_optional_guardrail_placed_first_is_still_checked_against_the_floor(tmp_path):
    # Config order does not matter: optional guardrails run after every default_on one.
    data = copy.deepcopy(AGENT_CONFIG)
    data["guardrails"][2]["litellm_params"]["pii_entities_config"] = {"EMAIL_ADDRESS": "BLOCK"}
    data["guardrails"] = [data["guardrails"][2], *data["guardrails"][:2]]
    with pytest.raises(GatewayError, match="another guardrail masks"):
        _review(tmp_path, data)


@pytest.mark.parametrize("value", [None, "false", 0])
def test_default_on_must_be_a_boolean(tmp_path, value):
    data = copy.deepcopy(AGENT_CONFIG)
    data["guardrails"][2]["litellm_params"]["default_on"] = value
    with pytest.raises(GatewayError, match="default_on"):
        _review(tmp_path, data)


def test_policy_sections_stay_refused_in_the_config(tmp_path):
    # Agents get policies through the API only; the config can't define or attach any.
    for section in ("policies", "policy_attachments"):
        data = {**AGENT_CONFIG, section: {}}
        with pytest.raises(GatewayError, match="GATEWAY_CONFIG_UNSAFE"):
            _review(tmp_path, data)


# -- services up: the record --------------------------------------------------------------------


def test_up_records_the_optional_guardrails(stack):  # noqa: F811
    from agentbreeder.services.plan import grant_text

    grant, operator, _ = _up(stack, _guarded(required=False), AGENT_CONFIG)
    record = operator["environments"]["dev"]["guardrails"]
    assert record["optional"] == ["agent-phone-block"]
    assert record["effective"] == ["presidio:pre_call"]
    text = grant_text(grant)
    assert "agent-phone-block (pre_call, agents that name it): IP_ADDRESS BLOCK" in text
    shown = {g["name"]: g for g in grant["gateway_config"]["guardrails"]}
    assert shown["agent-phone-block"]["default_on"] is False
    assert "default_on" not in shown["pii-mask"]  # G1b stacks keep their grant digest


def test_up_offers_no_optional_guardrail_that_would_not_run(stack):  # noqa: F811
    config = copy.deepcopy(AGENT_CONFIG)
    config["general_settings"].pop("disable_error_logs")
    _, operator, _ = _up(stack, _guarded(required=False), config)
    assert "optional" not in operator["environments"]["dev"]["guardrails"]


def test_up_without_optional_guardrails_writes_no_optional_key(stack):  # noqa: F811
    _, operator, _ = _up(stack, _guarded(required=False))
    assert "optional" not in operator["environments"]["dev"]["guardrails"]


def test_up_records_optional_guardrails_without_a_default_on_floor(stack):  # noqa: F811
    config = copy.deepcopy(CONFIG)
    config["guardrails"] = [PHONE_BLOCK]
    _, operator, _ = _up(stack, _guarded(required=False), config)
    record = operator["environments"]["dev"]["guardrails"]
    assert record["optional"] == ["agent-phone-block"] and record["effective"] == []


def test_the_operator_record_needs_the_running_hash(identity_env):
    identity_env.config["environments"]["dev"]["guardrails"] = {
        "required": False,
        "optional": ["agent-phone-block"],
    }
    with pytest.raises(Exception, match="environments"):
        identity_env.load()


# -- the manifest, agent.yaml and the package ---------------------------------------------------


def test_guardrails_need_model_access():
    with pytest.raises(ValueError, match="model_access"):
        Manifest.model_validate(v2_manifest_data(guardrails=["agent-phone-block"]))


@pytest.mark.parametrize("names", [[], ["a", "a"], ["-bad"], ["x" * 129], ["a b"]])
def test_guardrail_names_are_checked(names):
    with pytest.raises(ValueError):
        _manifest(names)


def test_the_digest_and_revision_are_unchanged_without_guardrails(identity_env):
    without = _manifest(None)
    assert "guardrails" not in without.model_dump_json(exclude={"guardrails"})
    dumped = without.revision_dump()
    assert "guardrails" not in dumped
    assert without.digest() != _manifest().digest()
    # The digest of a manifest without the field is computed as before the field existed.
    import hashlib

    legacy = without.model_dump_json(exclude={"mcp_access", "guardrails"})
    assert without.digest() == hashlib.sha256(legacy.encode()).hexdigest()
    operator = identity_env.load()
    context = gate_module.DeploymentContext("dev", "local")
    decision = gate_module.GateDecision(True)
    assert revision_digest(_manifest(), None, actor("a"), context, operator, decision) != (
        revision_digest(without, None, actor("a"), context, operator, decision)
    )


AGENT_YAML = {
    "kind": "agent",
    "schema_version": 1,
    "name": "support",
    "runtime": "runtime/adk@1.3.0",
    "model": {"alias": "support-model"},
    "prompt": {"path": "prompt.md"},
}


def test_agent_yaml_guardrails_reach_the_deploy_manifest(minting):
    agent = AgentFile.model_validate({**AGENT_YAML, "guardrails": ["agent-phone-block"]})
    serving = {"port": 8080, "health_path": "/health"}
    deploy = template(agent, serving, ["support-model"])
    assert deploy.guardrails == ("agent-phone-block",)
    data = bind(
        deploy,
        "sha256:" + "1" * 64,
        minting.load(),
        "dev",
        IdentityReference(provider="static", ref="agent-1", environment="dev"),
        "support",
    )
    assert data["guardrails"] == ["agent-phone-block"]
    plain = template(AgentFile.model_validate(AGENT_YAML), serving, ["support-model"])
    assert plain.guardrails is None
    assert "guardrails" not in bind(
        plain,
        "sha256:" + "1" * 64,
        minting.load(),
        "dev",
        IdentityReference(provider="static", ref="agent-1", environment="dev"),
        "support",
    )


@pytest.mark.parametrize("names", [["a", "a"], ["no spaces"], ["x"] * 11])
def test_agent_yaml_refuses_bad_guardrail_lists(names):
    with pytest.raises(ValueError):
        AgentFile.model_validate({**AGENT_YAML, "guardrails": names})


def test_a_package_without_guardrails_keeps_its_bytes():
    fields = {"port": 8080, "health_path": "/health", "aliases": ["m"], "budget_usd": None,
              "rpm": None, "mcp_access": []}  # fmt: skip
    from test_package import record as package_record

    record = package_record()
    assert record.deploy.guardrails is None
    assert '"guardrails"' not in record.to_bytes().decode()
    guarded = record.model_copy(
        update={"deploy": DeployTemplate.model_validate({**fields, "guardrails": ["g"]})}
    )
    assert json.loads(guarded.to_bytes())["deploy"]["guardrails"] == ["g"]
    with pytest.raises(ValueError):
        DeployTemplate.model_validate({**fields, "guardrails": ["g", "g"]})


# -- the gate -----------------------------------------------------------------------------------


@pytest.mark.parametrize("enforcement", ["enforce", "audit"])
@pytest.mark.parametrize(
    "floor, shown",
    [
        (_floor(), "does not define not-defined"),
        (_floor(optional=()), "defines no agent guardrails"),
        (None, "defines no agent guardrails"),
    ],
)
def test_gate_refuses_a_guardrail_the_gateway_does_not_define(
    minting, state, runtime, floor, shown, enforcement
):
    minting.config["enforcement"] = enforcement
    if floor is None:
        minting.config["environments"]["dev"].pop("guardrails")
    else:
        minting.config["environments"]["dev"]["guardrails"] = floor
    with pytest.raises(GateDenied) as error:
        _deploy(minting, state, runtime, _manifest(["not-defined"]))
    assert error.value.code == "GUARDRAIL_UNDEFINED"
    assert shown in str(error.value)
    assert "GUARDRAIL_UNDEFINED" not in gate_module.OVERRIDABLE
    assert runtime.mock_calls == []
    rows = state.audit_rows(attempt_id=error.value.attempt_id)
    assert not any(r["action"] == "audit_mode_proceed" for r in rows)


def test_cedar_sees_the_floor_and_the_agent_guardrails(minting):
    gate = gate_module.IdentityGate(minting.load())
    request = gate.policy_request(
        "deploy",
        actor("alice"),
        agent="sample",
        context=gate_module.DeploymentContext("dev", "local"),
        principal=None,
        risk_tier=None,
        manifest=_manifest(),
    )
    assert request.resource["properties"]["guardrails"] == [
        "agent:agent-phone-block",
        "presidio:pre_call",
    ]


AGENT_GUARDED = """
permit(principal, action == AgentBreeder::Action::"deploy", resource)
when { principal.verified && resource.guardrails.contains("agent:agent-phone-block") };
permit(principal, action == AgentBreeder::Action::"call_tool", resource);
"""


def test_a_cedar_bundle_can_require_an_agent_guardrail(minting, gateway, state, runtime):
    pytest.importorskip("cedarpy")
    from conftest import enable_cedar

    enable_cedar(minting, baseline=AGENT_GUARDED)
    with pytest.raises(GateDenied) as error:
        _deploy(minting, state, runtime, _manifest(None), actor("alice"))
    assert error.value.code == "POLICY_DENIED"
    _, result = _deploy(minting, state, runtime, _manifest(name="other"), actor("alice"))
    assert result["state"] == "ready"


# -- deploy: the policy, the key, the audit -----------------------------------------------------


def test_deploy_attaches_an_add_only_policy_to_the_key_alias(minting, gateway, state, runtime):
    _, result = _deploy(minting, state, runtime)
    assert result["state"] == "ready"
    alias = gateway_keys.alias(result["id"])
    (policy,) = gateway.policies.values()
    (attachment,) = gateway.attachments.values()
    posted = [c for c in gateway.calls if c[0] == "POST" and c[1].startswith("/policies")]
    assert posted[0][2] == {
        "policy_name": alias,
        "description": gateway_keys.POLICY_DESCRIPTION,
        "guardrails_add": ["agent-phone-block"],
    }
    assert posted[1][2] == {"policy_name": alias, "keys": [alias]}
    assert policy["guardrails_remove"] == [] and attachment["keys"] == [alias]
    assert gateway.keys[alias]["allowed_routes"] == list(gateway_keys.GUARDED_ROUTES)
    order = [c[1] for c in gateway.calls if c[0] == "POST"]
    assert order.index("/key/generate") < order.index("/policies/attachments")
    rows = state.audit_rows(limit=200)
    minted = [
        r
        for r in rows
        if r["action"] == "gateway_key"
        and r["decision"] == "minted"
        and r["deployment_id"] == result["id"]
    ]
    reasons = {r["code"]: r["message"] for r in minted[0]["reasons"]}
    assert reasons["GUARDRAILS_EFFECTIVE"] == "agent:agent-phone-block, presidio:pre_call"


def test_agent_guardrails_limit_the_key_routes_without_a_floor(minting, gateway, state, runtime):
    minting.config["environments"]["dev"]["guardrails"] = _floor(required=False)
    _, result = _deploy(minting, state, runtime)
    alias = gateway_keys.alias(result["id"])
    assert gateway.keys[alias]["allowed_routes"] == list(gateway_keys.GUARDED_ROUTES)


def test_a_deploy_without_agent_guardrails_creates_no_policy(minting, gateway, state, runtime):
    _, result = _deploy(minting, state, runtime, _manifest(None))
    assert result["state"] == "ready" and gateway.policies == {} and gateway.attachments == {}


@pytest.mark.parametrize("path", ["/policies", "/policies/attachments"])
def test_a_failed_attach_revokes_the_key_and_writes_no_key_file(
    minting, gateway, state, runtime, path
):
    gateway.fail.add(path)
    _running(runtime)
    service = DeploymentService(state, runtime, minting.load())
    result = service.deploy(_manifest(), local=True)
    assert result["state"] == "failed" and "gateway key not minted" in result["error"]
    runtime.create.assert_not_called()
    assert gateway.keys == {}
    assert not list(state.directory.glob("deployments/*/secrets/llm-gateway-key"))


def test_a_retry_replaces_an_earlier_attempts_policy(minting, gateway, state, runtime):
    client = gateway_keys.LiteLLM(gateway.url, MASTER)
    alias = gateway_keys.alias("0" * 32)
    gateway.keys[alias] = {}
    client.attach_guardrails(alias, ["one"])
    client.attach_guardrails(alias, ["two"])
    assert [p["guardrails_add"] for p in gateway.policies.values()] == [["two"]]
    assert len(gateway.attachments) == 1


def test_remove_revokes_the_key_then_deletes_the_policy(minting, gateway, state, runtime):
    service, result = _deploy(minting, state, runtime)
    runtime.inspect.side_effect = None
    runtime.inspect.return_value = None
    service.remove(result["id"], local=True)
    assert gateway.keys == {} and gateway.policies == {} and gateway.attachments == {}
    deletes = [c for c in gateway.calls if c[1] in ("/key/delete",) or c[0] == "DELETE"]
    assert deletes[-3][1] == "/key/delete"


# -- status and reconcile -----------------------------------------------------------------------


def test_status_shows_the_effective_set(minting, gateway, state, runtime):
    service, result = _deploy(minting, state, runtime)
    status = service.status(result["id"], verify=False)
    assert status["guardrails"] == {
        "floor": ["presidio:pre_call"],
        "agent": ["agent-phone-block"],
        "effective": ["agent:agent-phone-block", "presidio:pre_call"],
        "drift": None,
    }


def test_status_without_guardrails_has_no_block(identity_env, state, runtime):
    _running(runtime)
    service = DeploymentService(state, runtime, identity_env.load())
    result = service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)
    assert "guardrails" not in service.status(result["id"], verify=False)


def _tamper_remove_attachment(gateway, alias):
    gateway.attachments.clear()


def _tamper_change_policy(gateway, alias):
    for policy in gateway.policies.values():
        policy["guardrails_add"] = ["something-else"]


def _tamper_weakening_policy(gateway, alias):
    gateway.policies["w"] = {
        "policy_id": "w",
        "policy_name": "weaken",
        "guardrails_remove": ["pii-block"],
        "version_status": "production",
    }
    gateway.attachments["w"] = {"attachment_id": "w", "policy_name": "weaken", "scope": "*",
                                "keys": [], "teams": [], "models": [], "tags": []}  # fmt: skip


def _tamper_prefix_attachment(gateway, alias):
    gateway.policies["w"] = {"policy_id": "w", "policy_name": "weaken", "inherit": "x"}
    gateway.attachments["w"] = {"attachment_id": "w", "policy_name": "weaken",
                                "keys": ["agentbreeder-*"]}  # fmt: skip


def _tamper_drop_policy(gateway, alias):
    gateway.policies.clear()


@pytest.mark.parametrize(
    "tamper",
    [
        _tamper_remove_attachment,
        _tamper_change_policy,
        _tamper_weakening_policy,
        _tamper_prefix_attachment,
        _tamper_drop_policy,
    ],
)
def test_reconcile_reports_drift(minting, gateway, state, runtime, tamper):
    service, result = _deploy(minting, state, runtime)
    tamper(gateway, gateway_keys.alias(result["id"]))
    results = {r["id"]: r["compliance"] for r in service.reconcile()}
    assert results[result["id"]] == "noncompliant:DRIFT_GUARDRAILS"
    assert service.status(result["id"], verify=False)["guardrails"]["drift"]


def test_reconcile_is_compliant_when_the_policy_matches(minting, gateway, state, runtime):
    service, result = _deploy(minting, state, runtime)
    results = {r["id"]: r["compliance"] for r in service.reconcile()}
    assert results[result["id"]] == "compliant"


def test_reconcile_ignores_an_add_only_policy_on_a_deployment_without_agent_guardrails(
    minting, gateway, state, runtime
):
    service, result = _deploy(minting, state, runtime, _manifest(None))
    alias = gateway_keys.alias(result["id"])
    gateway_keys.LiteLLM(gateway.url, MASTER).attach_guardrails(alias, ["agent-phone-block"])
    results = {r["id"]: r["compliance"] for r in service.reconcile()}
    assert results[result["id"]] == "compliant"  # only adds checks


def test_reconcile_unreachable_gateway_is_unverifiable(minting, gateway, state, runtime):
    service, result = _deploy(minting, state, runtime)
    gateway.fail.add("/policies/list")
    results = {r["id"]: r["compliance"] for r in service.reconcile()}
    assert results[result["id"]] == "unverifiable:GATEWAY_ERROR"


def test_reconcile_deletes_the_policy_of_a_removed_deployment(minting, gateway, state, runtime):
    service, result = _deploy(minting, state, runtime)
    gateway.fail.add("/policies/attachments/list")  # the detach after the revoke fails
    runtime.inspect.side_effect = None
    runtime.inspect.return_value = None
    service.remove(result["id"], local=True)
    assert gateway.keys == {} and len(gateway.policies) == 1
    gateway.fail.clear()
    results = service.reconcile()
    assert {"id": result["id"], "name": gateway_keys.alias(result["id"]),
            "compliance": "gateway_policy_removed"} in results  # fmt: skip
    assert gateway.policies == {} and gateway.attachments == {}


def test_reconcile_leaves_policies_it_does_not_own(minting, gateway, state, runtime):
    service, _ = _deploy(minting, state, runtime)
    gateway.policies["o"] = {"policy_id": "o", "policy_name": "agentbreeder-" + "f" * 32}
    service.reconcile()
    assert "o" in gateway.policies


# -- the policy bodies and the drift rule ------------------------------------------------------


@pytest.mark.parametrize("alias", ["agentbreeder-*", "other-key", "agentbreeder-a*"])
def test_a_policy_is_for_one_deployment_key(alias):
    with pytest.raises(GatewayError, match="GATEWAY_POLICY"):
        policy_body(alias, ["g"])


@pytest.mark.parametrize("names", [[], ["bad name"], [""]])
def test_a_policy_names_gateway_guardrails(names):
    with pytest.raises(GatewayError, match="GATEWAY_POLICY"):
        policy_body("agentbreeder-" + "0" * 32, names)


def test_policy_bodies_hold_only_allow_listed_fields():
    bodies = policy_body("agentbreeder-" + "0" * 32, ["b", "a", "a"])
    assert set(bodies["policy"]) == gateway_keys.POLICY_FIELDS
    assert set(bodies["attachment"]) == gateway_keys.ATTACHMENT_FIELDS
    assert bodies["policy"]["guardrails_add"] == ["a", "b"]


ALIAS = "agentbreeder-" + "0" * 32
OURS = {"policy_name": ALIAS, "guardrails_add": ["g"], "version_status": "production"}
ATTACHED = {"policy_name": ALIAS, "keys": [ALIAS]}


@pytest.mark.parametrize(
    "policies, attachments, drift",
    [
        ([OURS], [ATTACHED], None),
        ([OURS], [], "not attached"),
        ([OURS], [ATTACHED, ATTACHED], "not attached"),
        ([OURS], [{**ATTACHED, "keys": [ALIAS, "agentbreeder-other"]}], "not attached"),
        ([OURS], [{**ATTACHED, "models": ["m"]}], "not attached"),
        ([{**OURS, "version_status": "draft"}], [ATTACHED], "missing"),
        ([{**OURS, "condition": {"model": "x"}}], [ATTACHED], "does not add"),
        ([{**OURS, "guardrails_remove": ["pii-block"]}], [ATTACHED], "can remove"),
        (
            [OURS, {"policy_name": "w", "pipeline": {"mode": "x"}}],
            [ATTACHED, {"policy_name": "w", "tags": ["t"]}],
            "can remove",
        ),
        (
            [OURS, {"policy_name": "w", "guardrails_remove": ["x"]}],
            [ATTACHED, {"policy_name": "w", "keys": ["other"]}],
            None,  # attached to another key only
        ),
    ],
)
def test_guardrail_drift(policies, attachments, drift):
    found = guardrail_drift(ALIAS, ["g"], policies, attachments)
    assert (found is None) if drift is None else (drift in found)


def test_no_expected_guardrails_and_no_policy_is_no_drift():
    assert guardrail_drift(ALIAS, [], [], []) is None
    assert "attached" in guardrail_drift(ALIAS, [], [OURS], [ATTACHED])


def test_a_weakening_policy_name_is_cut_in_the_message():
    name = "w" * 500
    found = guardrail_drift(
        ALIAS, ["g"], [OURS, {"policy_name": name, "guardrails_remove": ["x"]}],
        [ATTACHED, {"policy_name": name, "scope": "*"}],
    )  # fmt: skip
    assert len(found) < 140


def test_the_config_and_stack_fixtures_are_consistent(tmp_path):
    # The real-Docker test uses AGENT_CONFIG through the same review.
    path = _write(tmp_path, AGENT_CONFIG)
    assert config_review(path, GATEWAY_CAPS).optional() == ["agent-phone-block"]
    assert yaml.safe_load(yaml.safe_dump(STACK))["environment"]["gateway"] == "litellm"
    assert PII_MASK["litellm_params"]["default_on"] is True


# -- review fixes ---------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "GEMINI_API_KEY"])
def test_agent_guardrails_reserve_provider_keys_without_a_required_floor(
    minting, state, runtime, name
):
    minting.config["environments"]["dev"]["guardrails"] = _floor(required=False)
    data = _manifest().model_dump(mode="json", exclude_none=True)
    data["secrets"] = {name: "secret://provider"}
    with pytest.raises(GateDenied) as error:
        _deploy(minting, state, runtime, Manifest.model_validate(data))
    assert error.value.code == "SECRET_RESERVED"
    # Without agent guardrails and without a required floor, the name is not reserved.
    data.pop("guardrails")
    data["name"] = "plain"
    with pytest.raises(GateDenied) as error:  # it gets as far as the secret file check
        _deploy(minting, state, runtime, Manifest.model_validate(data))
    assert error.value.code == "SECRET_MISSING"


def test_floor_only_deployments_are_not_checked_for_policies(minting, gateway, state, runtime):
    service, result = _deploy(minting, state, runtime, _manifest(None))
    _tamper_weakening_policy(gateway, gateway_keys.alias(result["id"]))
    gateway.fail.add("/policies/list")
    results = {r["id"]: r["compliance"] for r in service.reconcile()}
    assert results[result["id"]] == "compliant"
    assert service.status(result["id"], verify=False)["guardrails"]["drift"] is None


def test_reconcile_reads_the_policy_lists_once_per_gateway(minting, gateway, state, runtime):
    service, _ = _deploy(minting, state, runtime)
    _deploy(minting, state, runtime, _manifest(name="second"))
    gateway.calls.clear()
    service.reconcile()
    listed = [c for c in gateway.calls if c[1] == "/policies/attachments/list"]
    assert len(listed) == 1


def test_an_unreadable_stored_manifest_does_not_stop_reconcile(minting, gateway, state, runtime):
    service, result = _deploy(minting, state, runtime)
    state.db.execute(
        "UPDATE deployments SET manifest=? WHERE id=?",
        (json.dumps({"name": "sample", "bad": True}), result["id"]),
    )
    assert service._guardrail_compliance(state.get(result["id"])) == (
        "unverifiable:MANIFEST_INVALID"
    )


def test_disable_revokes_the_key_and_deletes_the_policy(minting, gateway, state, runtime):
    service, result = _deploy(minting, state, runtime)
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    disabled = service.disable("sample", actor("ops"))
    assert disabled["stopped"] == [result["id"]]
    assert gateway.keys == {} and gateway.policies == {} and gateway.attachments == {}


# -- security review fixes ------------------------------------------------------------------------


def test_a_blank_attachment_reaches_every_key():
    weaken = {"policy_name": "w", "guardrails_remove": ["g"]}
    found = guardrail_drift(ALIAS, ["g"], [OURS, weaken], [ATTACHED, {"policy_name": "w"}])
    assert found and "can remove" in found
    assert "w" not in found.replace("remove", "").split()  # gateway text is never printed


def test_a_default_attachment_of_ours_is_drift():
    assert "not attached" in guardrail_drift(ALIAS, ["g"], [OURS], [{**ATTACHED, "default": True}])


@pytest.mark.parametrize("break_it", ["undefined", "unresolved"])
def test_attach_verifies_the_gateway_runs_the_guardrails(
    minting, gateway, state, runtime, break_it
):
    if break_it == "undefined":
        gateway.defined.discard("agent-phone-block")
    else:
        gateway.unresolved = True
    _running(runtime)
    result = DeploymentService(state, runtime, minting.load()).deploy(_manifest(), local=True)
    assert result["state"] == "failed" and "GATEWAY_POLICY" in result["error"]
    assert gateway.keys == {}


def test_reconcile_reports_a_guardrail_the_gateway_stopped_defining(
    minting, gateway, state, runtime
):
    service, result = _deploy(minting, state, runtime)
    gateway.defined.discard("agent-phone-block")  # e.g. the config changed without `up`
    results = {r["id"]: r["compliance"] for r in service.reconcile()}
    assert results[result["id"]] == "noncompliant:DRIFT_GUARDRAILS"
    assert "does not define" in service.status(result["id"], verify=False)["guardrails"]["drift"]


def test_agent_guardrails_need_the_approved_config_even_when_not_required(minting, state, runtime):
    minting.config["environments"]["dev"]["guardrails"] = _floor(
        required=False, running_sha256="c" * 64
    )
    with pytest.raises(GateDenied) as error:
        _deploy(minting, state, runtime)
    assert error.value.code == "GUARDRAILS_CHANGED"
    # Without an approved hash there is nothing to compare: the recorded names decide.
    floor = _floor(required=False, running_sha256="c" * 64)
    floor.pop("approved_sha256")
    minting.config["environments"]["dev"]["guardrails"] = floor
    _, result = _deploy(minting, state, runtime, _manifest(name="other"))
    assert result["state"] == "ready"


def test_a_gateway_without_the_policy_api_has_no_policies(gateway):
    client = gateway_keys.LiteLLM(gateway.url + "/nothing-here", MASTER)
    assert client.policies() == [] and client.policy_attachments() == []


def test_reconcile_asks_a_failing_gateway_once(minting, gateway, state, runtime):
    service, first = _deploy(minting, state, runtime)
    _, second = _deploy(minting, state, runtime, _manifest(name="second"))
    gateway.fail.add("/policies/list")
    gateway.calls.clear()
    results = {r["id"]: r["compliance"] for r in service.reconcile()}
    assert results[first["id"]] == results[second["id"]] == "unverifiable:GATEWAY_ERROR"
    listed = [c for c in gateway.calls if c[1] == "/policies/list"]
    assert len(listed) == 2  # once for the compliance checks, once for the orphan pass
