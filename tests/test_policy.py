"""P2 (#704): PDP contract, operator baseline + approved restrictions, Cedar/OPA/AuthZEN."""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import BASELINE_POLICY, actor, enable_cedar, principal_entry, v2_manifest_data

from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import PolicyConfig, PolicySource
from agentbreeder.policy import PolicyEngine, PolicyError, PolicyRequest
from agentbreeder.policy.cedar import CedarEvaluator
from agentbreeder.policy.remote import AuthZenEvaluator, OpaEvaluator

pytest.importorskip("cedarpy")


def request(verified=True, kind="agent", risk="low", action="deploy") -> PolicyRequest:
    return PolicyRequest(
        subject={
            "type": "user",
            "id": "dana",
            "properties": {"verified": verified, "issuer": "i"},
        },
        action=action,
        resource={
            "type": "agent",
            "id": "sample",
            "properties": {
                "environment": "dev",
                "target": "local",
                "risk_tier": risk,
                "principal": {"provider": "static", "authority": "t", "id": "a", "kind": kind},
            },
        },
        context={"image_digest": "sha256:" + "a" * 64},
    )


# Shared decision fixtures: (request kwargs, allowed, obligations).
DECISIONS = [
    ({}, True, ()),
    ({"risk": "high"}, True, ("require_approval",)),
    ({"verified": False}, False, ()),
    ({"kind": "workload"}, False, ()),
]


class FakeOpa(BaseHTTPRequestHandler):
    """Implements OPA's Data API + provenance for the Rego fixture's rules."""

    revision = "baseline-v1"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data = body["input"]
        props = data["resource"]["properties"]
        allow = (
            data["subject"]["properties"]["verified"] is True
            and props["principal"]["kind"] == "agent"
        )
        obligations = ["require_approval"] if props["risk_tier"] == "high" else []
        response = {
            "result": {"allow": allow, "obligations": obligations},
            "provenance": {"bundles": {"baseline": {"revision": self.revision}}},
        }
        payload = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def opa_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpa)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def cedar_engine(identity_env, restrictions=None, selected=()):
    enable_cedar(identity_env, restrictions=restrictions)
    config = identity_env.load()
    return PolicyEngine(config.model.pdp, list(selected), identity_env.directory)


@pytest.mark.parametrize("kwargs,allowed,obligations", DECISIONS)
def test_cedar_and_opa_decision_parity(identity_env, opa_url, kwargs, allowed, obligations):
    cedar = cedar_engine(identity_env).evaluate(request(**kwargs))
    opa_config = PolicyConfig(
        engine="opa",
        baseline=PolicySource(
            url=opa_url,
            path="agentbreeder/deploy/decision",
            bundle="baseline",
            revision="baseline-v1",
        ),
    )
    opa = PolicyEngine(opa_config, [], None).evaluate(request(**kwargs))
    for decision in (cedar, opa):
        assert decision.allowed is allowed
        assert decision.obligations == (obligations if allowed else ())
    assert cedar.bundle_digests["baseline"].startswith("sha256:")
    assert opa.bundle_digests["baseline"] == "revision:baseline-v1"


@pytest.mark.skipif(not os.getenv("AGENTBREEDER_OPA_URL"), reason="Opt-in real OPA server")
@pytest.mark.parametrize("kwargs,allowed,obligations", DECISIONS)
def test_real_opa_parity(kwargs, allowed, obligations):
    """Run against a real OPA, e.g.:
    docker run -p 127.0.0.1:8181:8181 -v $PWD/tests/fixtures/policy:/policy:ro \
      openpolicyagent/opa:1.4.2 run --server --addr 0.0.0.0:8181 --bundle /policy
    """
    config = PolicyConfig(
        engine="opa",
        baseline=PolicySource(
            url=os.environ["AGENTBREEDER_OPA_URL"],
            path="agentbreeder/deploy/decision",
            bundle=os.environ.get("AGENTBREEDER_OPA_BUNDLE", "/policy"),
            revision="baseline-v1",
        ),
    )
    decision = PolicyEngine(config, [], None).evaluate(request(**kwargs))
    assert decision.allowed is allowed
    assert decision.obligations == (obligations if allowed else ())
    assert decision.bundle_digests["baseline"] == "revision:baseline-v1"


@pytest.mark.skipif(not os.getenv("AGENTBREEDER_OPA_URL"), reason="Opt-in real OPA server")
def test_real_opa_unverified_revision_denies():
    config = PolicyConfig(
        engine="opa",
        baseline=PolicySource(
            url=os.environ["AGENTBREEDER_OPA_URL"],
            path="agentbreeder/deploy/decision",
            bundle=os.environ.get("AGENTBREEDER_OPA_BUNDLE", "/policy"),
            revision="baseline-v2",
        ),
    )
    assert PolicyEngine(config, [], None).evaluate(request()).code == "POLICY_REVISION_MISMATCH"


def test_opa_revision_mismatch_denies(opa_url):
    config = PolicyConfig(
        engine="opa",
        baseline=PolicySource(
            url=opa_url, path="agentbreeder/deploy/decision", bundle="baseline", revision="v2"
        ),
    )
    decision = PolicyEngine(config, [], None).evaluate(request())
    assert not decision.allowed and decision.code == "POLICY_REVISION_MISMATCH"


def test_author_allow_all_restriction_cannot_override_baseline_deny(identity_env):
    allow_all = "permit(principal, action, resource);"
    engine = cedar_engine(identity_env, {"author-allow-all": allow_all}, ["author-allow-all"])
    decision = engine.evaluate(request(verified=False))
    assert not decision.allowed and decision.code == "POLICY_DENIED"
    assert set(decision.bundle_digests) == {"baseline", "author-allow-all"}


def test_restriction_can_add_but_not_remove_obligations(identity_env):
    strict = (
        'permit(principal, action == AgentBreeder::Action::"deploy", resource);\n'
        'permit(principal, action == AgentBreeder::Action::"RequireApproval", resource);'
    )
    engine = cedar_engine(identity_env, {"strict": strict}, ["strict"])
    assert engine.evaluate(request()).obligations == ("require_approval",)
    lenient = 'permit(principal, action == AgentBreeder::Action::"deploy", resource);'
    engine = cedar_engine(identity_env, {"lenient": lenient}, ["lenient"])
    assert engine.evaluate(request(risk="high")).obligations == ("require_approval",)


def test_restriction_that_denies_wins(identity_env):
    forbid = 'forbid(principal, action, resource) when { resource.environment == "dev" };'
    engine = cedar_engine(identity_env, {"no-dev": forbid}, ["no-dev"])
    assert engine.evaluate(request()).code == "POLICY_DENIED"


def test_unapproved_restriction_and_digest_mismatch(identity_env):
    enable_cedar(identity_env)
    config = identity_env.load()
    with pytest.raises(PolicyError) as error:
        PolicyEngine(config.model.pdp, ["not-approved"], identity_env.directory)
    assert error.value.code == "RESTRICTION_UNAPPROVED"
    (identity_env.directory / "baseline.cedar").write_text(BASELINE_POLICY + "\n// edited")
    decision = PolicyEngine(config.model.pdp, [], identity_env.directory).evaluate(request())
    assert decision.code == "POLICY_DIGEST_MISMATCH"


def test_cedar_schema_validation_rejects_invalid_policy(identity_env):
    enable_cedar(
        identity_env, baseline="permit(principal, action, resource) when { resource.nope == 1 };"
    )
    config = identity_env.load()
    decision = PolicyEngine(config.model.pdp, [], identity_env.directory).evaluate(request())
    assert decision.code == "POLICY_INVALID"


def test_cedar_requires_pinned_digest(tmp_path):
    with pytest.raises(PolicyError):
        CedarEvaluator(PolicySource(source="p.cedar"), tmp_path)


def test_unknown_obligation_fails_closed():
    class Weird:
        def evaluate(self, request):
            from agentbreeder.policy import SourceResult

            return SourceResult(True, ("notify_ciso",), "sha256:x")

    config = PolicyConfig(
        engine="cedar", baseline=PolicySource(source="x", digest="sha256:" + "0" * 64)
    )
    engine = PolicyEngine(config, [], None, factory=lambda *a: Weird())
    assert engine.evaluate(request()).code == "OBLIGATION_UNSUPPORTED"


def test_evaluator_exception_is_policy_error():
    class Boom:
        def evaluate(self, request):
            raise RuntimeError("secret detail")

    config = PolicyConfig(
        engine="cedar", baseline=PolicySource(source="x", digest="sha256:" + "0" * 64)
    )
    decision = PolicyEngine(config, [], None, factory=lambda *a: Boom()).evaluate(request())
    assert decision.code == "POLICY_ERROR" and "secret" not in decision.message


class Responder:
    def __init__(self, status, body):
        self.status, self.body, self.sent = status, body, []

    def request(self, method, url, *, headers=None, json_body=None, **kwargs):
        self.sent.append((url, headers, json_body))
        return self.status, self.body


@pytest.mark.parametrize(
    "body,code",
    [
        ({"decision": True, "context": {"policy_revision": "r1"}}, None),
        ({"decision": True, "context": {"policy_revision": "r0"}}, "POLICY_REVISION_MISMATCH"),
        ({"decision": True}, "POLICY_REVISION_MISMATCH"),
        ({"decision": "yes", "context": {"policy_revision": "r1"}}, "POLICY_ERROR"),
        (
            {
                "decision": True,
                "context": {"policy_revision": "r1", "obligations": [{"name": "x"}]},
            },
            "OBLIGATION_UNSUPPORTED",
        ),
    ],
)
def test_authzen_adapter(body, code, monkeypatch):
    monkeypatch.setenv("PDP_TOKEN", "pdp-secret")
    source = PolicySource(url="https://pdp.example.com", revision="r1", token="env:PDP_TOKEN")
    responder = Responder(200, body)
    config = PolicyConfig(engine="authzen", baseline=source)
    engine = PolicyEngine(config, [], None, factory=lambda e, s, b: AuthZenEvaluator(s, responder))
    decision = engine.evaluate(request())
    assert decision.code == code
    url, headers, sent = responder.sent[0]
    assert url == "https://pdp.example.com/access/v1/evaluation"
    assert headers["Authorization"] == "Bearer pdp-secret"
    assert sent["action"] == {"name": "deploy"} and sent["resource"]["type"] == "agent"


def test_remote_sources_require_revision_and_safe_url():
    with pytest.raises(PolicyError):
        OpaEvaluator(PolicySource(url="https://opa.example.com"))
    with pytest.raises(PolicyError):
        OpaEvaluator(PolicySource(url="http://opa.example.com", revision="r"))
    decision = PolicyEngine(
        PolicyConfig(engine="opa", baseline=PolicySource(url="https://o", revision="r")),
        [],
        None,
        factory=lambda e, s, b: OpaEvaluator(s, Responder(503, None)),
    ).evaluate(request())
    assert decision.code == "POLICY_ERROR"


# -- gate integration ---------------------------------------------------------------------


def gated_deploy(identity_env, state, runtime, deployer, data=None):
    service = DeploymentService(state, runtime, identity_env.load())
    return service.deploy(
        Manifest.model_validate(data or v2_manifest_data()), local=True, deployer=deployer
    )


def test_gate_with_pdp_requires_verified_deployer(identity_env, state, runtime):
    enable_cedar(identity_env)
    with pytest.raises(GateDenied, match="DEPLOYER_UNVERIFIED"):
        gated_deploy(identity_env, state, runtime, actor("dana", verified=False))
    result = gated_deploy(identity_env, state, runtime, actor("dana"))
    assert result["state"] == "ready"
    assert result["gate"]["checks_unavailable"] == []
    row = next(r for r in state.audit_rows(limit=50) if r["action"] == "deploy")
    assert row["policy_ids"] == ["baseline"]
    assert row["bundle_digests"]["baseline"].startswith("sha256:")
    assert row["operator_config_digest"] == identity_env.load().digest


def test_gate_policy_deny_audited_without_docker(identity_env, state, runtime):
    enable_cedar(identity_env)
    identity_env.principals["agent-1"] = principal_entry(kind="workload")
    with pytest.raises(GateDenied, match="POLICY_DENIED"):
        gated_deploy(identity_env, state, runtime, actor("dana"))
    assert runtime.mock_calls == []


def test_manifest_restriction_paths_rejected_and_unapproved_ids_deny(identity_env, state, runtime):
    enable_cedar(identity_env)
    with pytest.raises(ValueError):
        Manifest.model_validate(v2_manifest_data(policy={"restrictions": ["./allow.cedar"]}))
    with pytest.raises(GateDenied, match="RESTRICTION_UNAPPROVED"):
        gated_deploy(
            identity_env,
            state,
            runtime,
            actor("dana"),
            v2_manifest_data(policy={"restrictions": ["mine"]}),
        )


def test_author_attributes_cannot_lower_verified_risk(identity_env, state, runtime):
    """Risk comes from the verified principal; the manifest cannot declare a lower tier."""
    enable_cedar(identity_env)
    identity_env.principals["agent-1"] = principal_entry(
        attributes={"acme/data_classification": "confidential", "acme/risk_tier": "high"}
    )
    data = v2_manifest_data()
    data["requires"]["attributes"]["acme/risk_tier"] = "low"
    with pytest.raises(GateDenied, match="ATTRIBUTE_MISMATCH"):
        gated_deploy(identity_env, state, runtime, actor("dana"), data)
    result = gated_deploy(identity_env, state, runtime, actor("dana"))
    assert result["state"] == "pending_approval"
    runtime.authorize.assert_not_called()
