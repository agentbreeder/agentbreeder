"""Phase-4 host side: gate wiring, boundary lifecycle in the deployment service, the decision
service (policy, held calls, audit-before-forward, credential broker) and held-call approval."""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from conftest import actor, principal_entry, v2_manifest_data
from egress_support import TOOL_TOKEN, enable_boundary, tool_capabilities

pytest.importorskip("cedarpy")

from agentbreeder.audit import AuditError  # noqa: E402
from agentbreeder.cli import main  # noqa: E402
from agentbreeder.deployment import DeploymentService  # noqa: E402
from agentbreeder.egress import host as host_module  # noqa: E402
from agentbreeder.egress.boundary import BoundaryUnverified  # noqa: E402
from agentbreeder.egress.host import (  # noqa: E402
    Broker,
    DecisionService,
    Denied,
    make_server,
)
from agentbreeder.egress.protocol import (  # noqa: E402
    MAC_HEADER,
    NONCE_HEADER,
    response_authentic,
)
from agentbreeder.gate import GateDenied  # noqa: E402
from agentbreeder.identity.transport import TransportError  # noqa: E402
from agentbreeder.manifest import Manifest  # noqa: E402
from agentbreeder.operator_config import Destination  # noqa: E402
from agentbreeder.store import DeploymentError, Store  # noqa: E402

URL = "https://mcp.example.com/mcp"
PEM = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


class FakeBoundary:
    """Stands in for Docker orchestration; records what the service asked for."""

    def __init__(self, runtime, verify_error=None, start_error=None):
        self.runtime = runtime
        self.verify_error = verify_error
        self.start_error = start_error
        self.pins: dict[str, dict] = {}
        self.calls: list[str] = []
        self.healthy = True

    def names(self, record):
        return {"sidecar": record["container"] + "-pep"}

    def enforced(self, record):
        return record["id"] in self.pins

    def pin(self, record):
        return self.pins[record["id"]]

    def write_pin(self, record, pin):
        self.calls.append("write_pin")
        self.pins[record["id"]] = {**pin, "deployment": record["id"], "token_sha256": "x"}
        return "token"

    def set_serving(self, record, serving):
        self.calls.append(f"serving={serving}")
        self.pins[record["id"]]["serving"] = serving

    def start_service(self, record):
        self.calls.append("start_service")
        if self.start_error:
            raise self.start_error

    def service_healthy(self, record):
        return self.healthy

    def create(self, record, manifest, token):
        self.calls.append("create")
        self.runtime.create(record, manifest, network="int", environment={})

    def start(self, record):
        self.calls.append("start")

    def verify(self, record):
        self.calls.append("verify")
        if self.verify_error:
            raise self.verify_error
        return {}

    def endpoint(self, record):
        return "http://127.0.0.1:4321"

    def teardown(self, record):
        self.calls.append("teardown")


@pytest.fixture
def boundary_env(identity_env, monkeypatch):
    identity_env.principals["agent-1"] = principal_entry(
        sponsors=[
            {"id": "alice-id", "name": "alice@example.com", "active": True},
            {"id": "frank-id", "name": "frank@example.com", "active": True},
        ]
    )
    enable_boundary(identity_env, URL, PEM, monkeypatch)
    return identity_env


def _manifest(**overrides):
    return Manifest.model_validate(
        v2_manifest_data(capabilities=tool_capabilities(URL), **overrides)
    )


def _service(state, runtime, env, **fake):
    service = DeploymentService(state, runtime, env.load())
    service.boundary = FakeBoundary(runtime, **fake)
    return service


def _deploy(state, runtime, env, **fake):
    service = _service(state, runtime, env, **fake)
    result = service.deploy(_manifest(), local=True, deployer=actor("dave"))
    return service, result


def _decisions(state, service, result):
    record = state.get(result["id"])
    return DecisionService(state, service.boundary.pin(record))


def _call(tool="lookup", digest="sha256:" + "1" * 64, method="tools/call", server="tools"):
    return {
        "server": server,
        "method": method,
        "tool": tool,
        "arguments_digest": digest,
        "request_id": "1",
    }


def _tool_rows(state):
    return [
        (r["decision"], r["context"].get("tool"), (r["reasons"] or [{}])[0].get("code"))
        for r in reversed(state.audit_rows(limit=200))
        if r["action"] == "tool_call"
    ]


# -- gate ------------------------------------------------------------------------------------


def test_model_access_denied_under_runtime_enforcement(identity_env, state, runtime, monkeypatch):
    enable_boundary(identity_env, URL, PEM, monkeypatch)
    identity_env.config["gateways"] = {
        "litellm-local": {
            "type": "litellm",
            "url": "http://127.0.0.1:4000",
            "agent_url": "http://host.docker.internal:4000",
        }
    }
    identity_env.config["environments"]["dev"].update(
        {
            "gateway": "litellm-local",
            "models": ["support-model"],
            "ceilings": {"budget_usd": 50, "rpm": 300},
        }
    )
    service = _service(state, runtime, identity_env)
    access = {"gateway": "litellm-local", "aliases": ["support-model"]}
    with pytest.raises(GateDenied) as caught:
        service.deploy(_manifest(model_access=access), local=True, deployer=actor("dave"))
    assert caught.value.code == "MODEL_ACCESS_UNSUPPORTED"
    runtime.create.assert_not_called()


def test_gate_requires_pdp_and_approved_destinations(identity_env, state, runtime, monkeypatch):
    identity_env.config["runtime_enforcement"] = {
        "mode": "mcp_http",
        "destinations": {"tools": {"url": URL}},
    }
    service = DeploymentService(state, runtime, identity_env.load())
    with pytest.raises(GateDenied) as caught:
        service.deploy(_manifest(), local=True, deployer=actor("dave"))
    assert caught.value.code == "CONTROL_UNSUPPORTED"
    enable_boundary(identity_env, URL, PEM, monkeypatch)
    service = DeploymentService(state, runtime, identity_env.load())
    other = {"tools": [{"mcp": "https://other.example.com/mcp", "actions": ["x"]}]}
    with pytest.raises(GateDenied) as caught:
        service.deploy(
            Manifest.model_validate(v2_manifest_data(capabilities=other)),
            local=True,
            deployer=actor("dave"),
        )
    assert caught.value.code == "DESTINATION_UNAPPROVED"
    runtime.create.assert_not_called()


def test_destination_config_validation():
    with pytest.raises(ValueError, match="credential"):
        Destination.model_validate(
            {"url": URL, "token_exchange": {"token_endpoint": URL, "audience": "a"}}
        )
    with pytest.raises(ValueError):
        Destination.model_validate({"url": "http://mcp.example.com"})
    with pytest.raises(ValueError):
        Destination.model_validate({"url": URL, "credential": "literal-secret"})


# -- deployment lifecycle --------------------------------------------------------------------


def test_boundary_deploy_pins_revision_and_reports_runtime_boundary(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    assert result["state"] == "ready" and result["endpoint"] == "http://127.0.0.1:4321"
    assert "runtime_boundary" in result["gate"]["checks_evaluated"]
    assert "runtime_tool_enforcement" not in result["gate"]["checks_unavailable"]
    pin = service.boundary.pin(state.get(result["id"]))
    record = state.get(result["id"])
    assert pin["revision"] == record["revision"]
    assert pin["operator_digest"] == service.operator.digest
    assert "dave" in pin["deployer_names"]
    assert runtime.create.call_args.kwargs["network"] == "int"
    assert service.boundary.calls == [
        "write_pin",
        "start_service",
        "create",
        "start",
        "verify",
        "serving=True",
    ]
    assert pin["principal_props"]["groups"] == ["tenant-a|finance-group"]
    again = service.deploy(_manifest(), local=True, deployer=actor("dave"))
    assert again["state"] == "ready" and service.boundary.calls.count("write_pin") == 1


def test_unverified_boundary_is_a_deny_and_leaves_nothing(boundary_env, state, runtime):
    error = BoundaryUnverified("Agent reached a network address outside its boundary")
    service = _service(state, runtime, boundary_env, verify_error=error)
    with pytest.raises(GateDenied) as caught:
        service.deploy(_manifest(), local=True, deployer=actor("dave"))
    assert caught.value.code == "RUNTIME_BOUNDARY_UNVERIFIED"
    assert "teardown" in service.boundary.calls
    [record] = state.active_all()
    assert record["state"] == "failed" and "RUNTIME_BOUNDARY_UNVERIFIED" in record["error"]
    denies = [r for r in state.audit_rows(limit=50) if r["decision"] == "deny"]
    assert denies[0]["reasons"][0]["code"] == "RUNTIME_BOUNDARY_UNVERIFIED"
    assert state.verify_audit()["incomplete_attempts"] == []


def test_service_start_failure_fails_closed(boundary_env, state, runtime):
    service, result = _deploy(
        state, runtime, boundary_env, start_error=DeploymentError("no service")
    )
    assert result["state"] == "failed" and "teardown" in service.boundary.calls
    runtime.create.assert_not_called()


def test_health_failure_through_sidecar_tears_down(boundary_env, state, runtime):
    runtime.healthy.return_value = False
    service, result = _deploy(state, runtime, boundary_env)
    assert result["state"] == "failed" and "sidecar" in result["error"]
    assert "teardown" in service.boundary.calls


def test_remove_and_disable_tear_down_the_boundary(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    assert service.remove(result["id"], local=True, actor=actor("dave"))["state"] == "removed"
    assert service.boundary.calls.count("teardown") == 1
    runtime.inspect.side_effect = None
    service, result = _deploy(state, runtime, boundary_env)
    runtime.inspect.side_effect = [{"Id": "c", "State": {"Running": True}}, None]
    disabled = service.disable(result["id"], actor("admin"))
    assert disabled["state"] == "disabled" and "teardown" in service.boundary.calls


def test_reconcile_restores_or_fails_closed(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    service.boundary.healthy = False
    [item] = service.reconcile(actor("dave"))
    assert item["compliance"] == "compliant"
    assert service.boundary.calls[-4:] == [
        "serving=False",
        "start_service",
        "verify",
        "serving=True",
    ]
    runtime.command.assert_called_with(
        "container", "start", state.get(result["id"])["container"] + "-pep"
    )
    service.boundary.verify_error = BoundaryUnverified("Agent publishes ports")
    [item] = service.reconcile(actor("dave"))
    assert item["compliance"] == "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"
    assert state.get(result["id"])["state"] == "failed"
    assert service.boundary.calls[-1] == "teardown"


# -- decision service ------------------------------------------------------------------------


def test_allowed_call_audited_before_credential_returned(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    reply = decisions.decide(_call())
    assert reply == {"decision": "allow", "authorization": f"Bearer {TOOL_TOKEN}"}
    row = next(r for r in state.audit_rows(limit=10) if r["action"] == "tool_call")
    assert row["decision"] == "allow" and row["deployment_id"] == result["id"]
    assert row["actor"] == "agent:static/agent-1" and row["policy_ids"] == ["baseline"]
    assert TOOL_TOKEN not in json.dumps(row)
    assert decisions.health()["revision"] == state.get(result["id"])["revision"]


def test_denials_are_audited_and_never_return_a_credential(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    cases = {
        "POLICY_DENIED": _call("delete"),
        "CAPABILITY_UNDECLARED": _call("undeclared"),
        "DESTINATION_UNAPPROVED": _call(server="other"),
        "MALFORMED_RPC": _call(digest=None),
    }
    for code, request in cases.items():
        reply = decisions.decide(request)
        assert (
            reply["decision"] == "deny" and reply["code"] == code and "authorization" not in reply
        )
    assert decisions.decide({"method": "bogus", "server": "tools"})["code"] == "MALFORMED_RPC"
    assert [c for _, _, c in _tool_rows(state)] == list(cases)


def test_metadata_methods_forwarded_without_policy_or_audit(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    reply = _decisions(state, service, result).decide(_call(tool=None, method="tools/list"))
    assert reply["decision"] == "allow" and _tool_rows(state) == []


def test_kill_switch_revision_and_config_pin_deny_every_call(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    record = state.get(result["id"])
    state.add_blocker(
        record["principal_key"],
        "dev",
        "admin",
        True,
        "x",
        service._event("a", "disable", "blocked", actor("admin")),
    )
    assert decisions.decide(_call(method="ping", tool=None))["code"] == "PRINCIPAL_BLOCKED"
    with pytest.raises(Denied):
        decisions.health()
    state.db.execute("DELETE FROM blockers")
    boundary_env.config["approval_ttl_seconds"] = 1200
    boundary_env.write()
    assert decisions.decide(_call())["code"] == "RUNTIME_CONFIG_CHANGED"
    del boundary_env.config["approval_ttl_seconds"]
    boundary_env.write()
    state.update_binding(result["id"], revision="other", approval_id=None)
    assert decisions.decide(_call())["code"] == "DEPLOYMENT_REVISION_CHANGED"
    state.transition(result["id"], "removed")
    assert decisions.decide(_call())["code"] == "DEPLOYMENT_INACTIVE"
    assert not decisions.active()
    gone = DecisionService(state, {**decisions.pin, "deployment": "missing"})
    assert gone.decide(_call())["code"] == "DEPLOYMENT_INACTIVE" and not gone.active()


def test_unreadable_or_disabled_operator_config_denies(boundary_env, state, runtime, tmp_path):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    decisions.pin = {**decisions.pin, "operator_path": str(tmp_path / "missing.yaml")}
    assert decisions.decide(_call())["code"] == "RUNTIME_CONFIG_CHANGED"
    boundary_env.config["runtime_enforcement"]["mode"] = "none"
    operator = boundary_env.load()
    decisions.pin = {
        **decisions.pin,
        "operator_path": str(boundary_env.path),
        "operator_digest": operator.digest,
    }
    assert decisions.decide(_call())["code"] == "RUNTIME_CONFIG_CHANGED"


def test_audit_failure_blocks_the_call(boundary_env, state, runtime, monkeypatch):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)

    def broken(event):
        raise AuditError("disk full")

    monkeypatch.setattr(state, "audit", broken)
    with pytest.raises(host_module.Unavailable):
        decisions.decide(_call())
    monkeypatch.setattr(state, "create_held", lambda *a: broken(None))
    with pytest.raises(host_module.Unavailable):
        decisions.decide(_call("refund"))


def test_held_call_released_once_after_second_person_approval(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    first = decisions.decide(_call("refund"))
    assert first["decision"] == "held"
    assert decisions.decide(_call("refund")) == first  # same pending request, not a new one
    other_arguments = decisions.decide(_call("refund", digest="sha256:" + "2" * 64))
    assert other_arguments["held_call_id"] != first["held_call_id"]

    held_id = first["held_call_id"]
    for checker, code in (
        (actor("dave"), "SELF_APPROVAL"),
        (actor("carol", verified=False), "DEPLOYER_UNVERIFIED"),
        (actor("eve"), "CHECKER_NOT_AUTHORIZED"),
    ):
        with pytest.raises(GateDenied) as caught:
            service.decide_call(held_id, checker, approve=True)
        assert caught.value.code == code
    approved = service.decide_call(held_id, actor("carol"), approve=True)
    assert approved["state"] == "approved" and approved["checker"].endswith("carol")
    with pytest.raises(GateDenied, match="not pending"):
        service.decide_call(held_id, actor("carol"), approve=True)

    released = decisions.decide(_call("refund"))
    assert released["decision"] == "allow" and released["authorization"]
    assert state.held(held_id)["state"] == "consumed"
    assert decisions.decide(_call("refund"))["decision"] == "held"  # single use
    decisions_seen = [d for d, tool, _ in _tool_rows(state) if tool == "refund"]
    assert decisions_seen == ["held", "held", "allow", "held"]


def test_rejected_and_expired_held_calls(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    held = decisions.decide(_call("refund"))["held_call_id"]
    assert service.decide_call(held, actor("carol"), approve=False)["state"] == "rejected"
    assert decisions.decide(_call("refund"))["code"] == "CALL_REJECTED"

    later = DecisionService(
        state, decisions.pin, clock=lambda: datetime.now(UTC) + timedelta(hours=2)
    )
    fresh = later.decide(_call("refund"))  # the rejection expired; a new request is created
    assert fresh["decision"] == "held" and fresh["held_call_id"] != held
    state.db.execute(
        "UPDATE held_calls SET expires=? WHERE id=?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), fresh["held_call_id"]),
    )
    with pytest.raises(GateDenied) as caught:
        service.decide_call(fresh["held_call_id"], actor("carol"), approve=True)
    assert caught.value.code == "APPROVAL_EXPIRED"
    assert state.held(fresh["held_call_id"])["state"] == "expired"


def test_held_call_checker_rules_follow_live_principal(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    held = decisions.decide(_call("refund"))["held_call_id"]
    boundary_env.principals["agent-1"] = principal_entry()  # alice becomes the sole sponsor
    boundary_env.write()
    with pytest.raises(GateDenied) as caught:
        service.decide_call(held, actor("alice-id"), approve=True)
    assert caught.value.code == "SOLE_SPONSOR_APPROVAL"
    nonhuman = actor("carol")
    object.__setattr__(nonhuman, "human", False)
    with pytest.raises(GateDenied, match="human"):
        service.decide_call(held, nonhuman, approve=True)
    record = state.get(result["id"])
    state.add_blocker(
        record["principal_key"],
        "dev",
        "admin",
        True,
        "x",
        service._event("a", "disable", "blocked", actor("admin")),
    )
    with pytest.raises(GateDenied) as caught:
        service.decide_call(held, actor("carol"), approve=True)
    assert caught.value.code == "PRINCIPAL_BLOCKED"
    state.db.execute("DELETE FROM blockers")
    del boundary_env.principals["agent-1"]
    boundary_env.write()
    with pytest.raises(GateDenied) as caught:
        service.decide_call(held, actor("carol"), approve=True)
    assert caught.value.code == "PRINCIPAL_NOT_FOUND"
    state.update_binding(result["id"], revision="changed", approval_id=None)
    with pytest.raises(GateDenied) as caught:
        service.decide_call(held, actor("carol"), approve=True)
    assert caught.value.code == "DEPLOYMENT_REVISION_CHANGED"


def test_replayed_release_is_refused(boundary_env, state, runtime, monkeypatch):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    held = decisions.decide(_call("refund"))["held_call_id"]
    service.decide_call(held, actor("carol"), approve=True)
    monkeypatch.setattr(state, "decide_held", lambda *a, **k: False)
    assert decisions.decide(_call("refund"))["code"] == "APPROVAL_REPLAYED"


# -- credential broker -----------------------------------------------------------------------


def test_broker_static_and_token_exchange(monkeypatch, tmp_path):
    monkeypatch.setenv("SUBJECT", "subject-token")
    monkeypatch.setenv("CLIENT", "client-pw")
    assert Broker().authorization(Destination(url=URL), None) is None
    assert Broker().authorization(Destination(url=URL, credential="env:SUBJECT"), None) == (
        "Bearer subject-token"
    )
    transport = Mock()
    transport.request.return_value = (200, {"access_token": "short", "token_type": "Bearer"})
    destination = Destination.model_validate(
        {
            "url": URL,
            "credential": "env:SUBJECT",
            "token_exchange": {
                "token_endpoint": "https://as.example.com/token",
                "audience": "mcp",
                "scope": "tools",
                "client_id": "pep",
                "client_secret": "env:CLIENT",
            },
        }
    )
    assert Broker(transport).authorization(destination, None) == "Bearer short"
    form = transport.request.call_args.kwargs["form"]
    assert (
        form["grant_type"].endswith("token-exchange") and form["subject_token"] == "subject-token"
    )
    assert form["audience"] == "mcp" and form["scope"] == "tools"
    assert transport.request.call_args.kwargs["headers"]["Authorization"].startswith("Basic ")
    for reply in ((401, {}), (200, {"access_token": "x", "token_type": "mac"}), (200, None)):
        transport.request.return_value = reply
        with pytest.raises(Denied, match="exchange"):
            Broker(transport).authorization(destination, None)
    transport.request.side_effect = TransportError("PROVIDER_ERROR", "down")
    with pytest.raises(Denied, match="unavailable"):
        Broker(transport).authorization(destination, None)
    monkeypatch.delenv("SUBJECT")
    with pytest.raises(Denied):
        Broker().authorization(Destination(url=URL, credential="env:SUBJECT"), None)


# -- HTTP surface ----------------------------------------------------------------------------


def _request(server, method, path, body=None, token=None, headers=None, raw=None):
    """Serve on this (the service's own) thread; the client runs on a helper thread."""
    result = {}

    def client():
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        sent = {"Authorization": f"Bearer {token}"} if token else {}
        connection.request(method, path, body=body, headers={**sent, **(headers or {})})
        response = connection.getresponse()
        data = response.read()
        if raw is not None:
            raw.update(status=response.status, data=data, mac=response.getheader(MAC_HEADER))
        result["reply"] = (response.status, json.loads(data or b"{}"))
        connection.close()

    thread = threading.Thread(target=client)
    thread.start()
    server.handle_request()
    thread.join(5)
    return result["reply"]


def test_decision_http_handler(boundary_env, state, runtime, tmp_path):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    token = "t0k"
    pin = {**decisions.pin, "token_sha256": hashlib.sha256(token.encode()).hexdigest()}
    server, address = make_server(decisions, pin, tmp_path)
    server.timeout = 5
    assert address.startswith("tcp:")
    try:
        assert _request(server, "GET", "/healthz")[0] == 200
        assert _request(server, "GET", "/other")[0] == 404
        assert _request(server, "POST", "/decide", b"{}", "wrong")[0] == 401
        assert _request(server, "POST", "/decide", b"[1]", token)[0] == 400
        assert _request(server, "POST", "/decide", b"not json", token)[0] == 400
        status, reply = _request(server, "POST", "/decide", json.dumps(_call()).encode(), token)
        assert status == 200 and reply["decision"] == "allow"
        assert _request(server, "POST", "/health", b"{}", token)[0] == 200
        assert _request(server, "POST", "/nope", b"{}", token)[0] == 404
        state.update_binding(result["id"], revision="changed", approval_id=None)
        assert _request(server, "GET", "/healthz")[0] == 409
        assert _request(server, "POST", "/health", b"{}", token)[0] == 409
        decisions.decide = Mock(side_effect=host_module.Unavailable())
        assert _request(server, "POST", "/decide", b"{}", token)[0] == 503
    finally:
        server.server_close()


def test_unix_socket_server_mode(boundary_env, state, runtime):
    import shutil
    import tempfile
    from pathlib import Path

    service, result = _deploy(state, runtime, boundary_env)
    short = Path(tempfile.mkdtemp(dir="/tmp"))
    (short / "sock").mkdir()
    try:
        server, address = make_server(
            _decisions(state, service, result), {"token_sha256": "x", "listen": "unix"}, short
        )
        assert address == f"unix:{short}/sock/pep.sock"
        assert oct((short / "sock" / "pep.sock").stat().st_mode & 0o777) == "0o666"
        server.server_close()
    finally:
        shutil.rmtree(short)


def test_service_loop_writes_endpoint_and_exits_when_inactive(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    record = state.get(result["id"])
    directory = host_module.egress_dir(state.directory, result["id"])
    directory.mkdir(parents=True, mode=0o700)
    host_module.write_private_json(
        directory / "pin.json", {**service.boundary.pin(record), "port": 0}
    )
    state.transition(result["id"], "removed")
    code = host_module.run(state.directory, result["id"], max_idle_checks=1, poll=0.05)
    endpoint = json.loads((directory / "endpoint.json").read_text())
    assert code == 0 and endpoint["address"].startswith("tcp:")
    assert oct((directory / "endpoint.json").stat().st_mode & 0o777) == "0o600"


# -- CLI -------------------------------------------------------------------------------------


def test_cli_approve_call(boundary_env, tmp_path, runtime, monkeypatch, capsys):
    store = Store(tmp_path / "cli-state")
    service, result = _deploy(store, runtime, boundary_env)
    held = _decisions(store, service, result).decide(_call("refund"))["held_call_id"]
    directory = host_module.egress_dir(store.directory, result["id"])
    directory.mkdir(parents=True, mode=0o700)
    host_module.write_private_json(
        directory / "pin.json", service.boundary.pin(store.get(result["id"]))
    )
    store.close()
    monkeypatch.setattr("agentbreeder.cli.resolve_deployer", lambda operator: actor("carol"))
    monkeypatch.setattr("agentbreeder.cli.Docker", lambda: runtime)
    code = main(["approve-call", held, "--state-dir", str(tmp_path / "cli-state")])
    assert code == 0 and "approved by" in capsys.readouterr().out
    code = main(["reject-call", held, "--state-dir", str(tmp_path / "cli-state")])
    assert code == 2 and "HELD_NOT_PENDING" in capsys.readouterr().err


# -- Codex review regressions ----------------------------------------------------------------


def test_no_call_flows_before_the_serving_latch_opens(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    record = state.get(result["id"])
    service.boundary.pins[record["id"]]["serving"] = False
    decisions = _decisions(state, service, result)
    assert decisions.decide(_call())["code"] == "RUNTIME_BOUNDARY_UNVERIFIED"
    assert decisions.decide(_call(method="ping", tool=None))["decision"] == "deny"
    assert decisions.health()["revision"]  # verification probes still see health


def test_serving_latch_is_read_from_the_pin_file(boundary_env, state, runtime, tmp_path):
    service, result = _deploy(state, runtime, boundary_env)
    pin = service.boundary.pin(state.get(result["id"]))
    path = tmp_path / "pin.json"
    host_module.write_private_json(path, {**pin, "serving": False})
    decisions = DecisionService(state, pin, pin_path=path)
    assert decisions.decide(_call())["code"] == "RUNTIME_BOUNDARY_UNVERIFIED"
    host_module.write_private_json(path, {**pin, "serving": True})
    assert decisions.decide(_call())["decision"] == "allow"
    path.unlink()
    assert decisions.decide(_call())["code"] == "RUNTIME_BOUNDARY_UNVERIFIED"


def test_runtime_policy_uses_full_principal_not_truncated_snapshot(
    boundary_env, state, runtime, monkeypatch
):
    from conftest import BASELINE_POLICY
    from egress_support import CALL_POLICY

    groups = [{"group_id": f"g{i:03d}", "name": f"G{i}"} for i in range(70)]
    groups.append({"group_id": "zz-blocked", "name": "Blocked"})
    boundary_env.principals["agent-1"] = principal_entry(
        groups=[{"group_id": "finance-group", "name": "Finance"}, *groups]
    )
    forbid = """
forbid(principal, action == AgentBreeder::Action::"call_tool", resource)
when { principal.groups.contains("tenant-a|zz-blocked") };
"""
    enable_boundary(boundary_env, URL, PEM, monkeypatch, CALL_POLICY + forbid)
    assert BASELINE_POLICY  # deploy itself stays allowed
    service, result = _deploy(state, runtime, boundary_env)
    record = state.get(result["id"])
    assert len(record["principal"]["groups"]) == 64  # the audit snapshot is truncated
    assert "tenant-a|zz-blocked" in service.boundary.pin(record)["principal_props"]["groups"]
    assert _decisions(state, service, result).decide(_call())["code"] == "POLICY_DENIED"
    pin = dict(service.boundary.pin(record))
    del pin["principal_props"]
    assert DecisionService(state, pin).decide(_call())["code"] == "RUNTIME_CONFIG_CHANGED"


def test_disable_during_credential_exchange_still_denies(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    record = state.get(result["id"])

    class BlockingBroker:
        def authorization(self, destination, base):
            state.add_blocker(
                record["principal_key"],
                "dev",
                "admin",
                True,
                "x",
                service._event("a", "disable", "blocked", actor("admin")),
            )
            return "Bearer short"

    decisions = DecisionService(state, service.boundary.pin(record), broker=BlockingBroker())
    reply = decisions.decide(_call())
    assert reply["code"] == "PRINCIPAL_BLOCKED" and "authorization" not in reply
    assert [d for d, _, _ in _tool_rows(state)] == ["deny"]


def test_expired_release_cannot_be_consumed(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    held = decisions.decide(_call("refund"))["held_call_id"]
    service.decide_call(held, actor("carol"), approve=True)
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    state.db.execute("UPDATE held_calls SET expires=? WHERE id=?", (past, held))
    event = service._event("a", "tool_call", "allow", actor("carol"))
    assert not state.decide_held(
        held, expected=("approved",), state="consumed", audit=event, require_unexpired=True
    )
    assert state.held(held)["state"] == "approved"


def test_changed_risk_tier_rebuilds_instead_of_reusing_pin(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    record = state.get(result["id"])
    service.boundary.pins[record["id"]]["risk_tier"] = "stale"
    service.deploy(_manifest(), local=True, deployer=actor("dave"))
    assert service.boundary.calls.count("write_pin") == 2
    assert service.boundary.pin(record)["risk_tier"] == "low"


def test_reconcile_retries_failed_boundary_cleanup(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    state.transition(result["id"], "failed", error="x")
    service.boundary.teardown = Mock(side_effect=DeploymentError("stuck"))
    [item] = service.reconcile(actor("dave"))
    assert item["compliance"] == "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED:cleanup_failed"
    assert any(r["action"] == "boundary_cleanup" for r in state.audit_rows(limit=20))
    service.boundary.teardown = Mock()
    [item] = service.reconcile(actor("dave"))
    assert item["compliance"] == "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"
    service.boundary.teardown.assert_called_once()


def test_running_sidecar_is_not_restarted_by_reconcile(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    runtime.command.return_value = "true"
    [item] = service.reconcile(actor("dave"))
    assert item["compliance"] == "compliant"
    assert "serving=False" not in service.boundary.calls
    assert not any(c.args[:2] == ("container", "start") for c in runtime.command.call_args_list)


def test_release_is_void_after_attribute_change_or_in_another_session(
    boundary_env, state, runtime
):
    service, result = _deploy(state, runtime, boundary_env)
    record = state.get(result["id"])
    decisions = _decisions(state, service, result)
    held = decisions.decide({**_call("refund"), "session": "A"})["held_call_id"]
    service.decide_call(held, actor("carol"), approve=True)
    other_session = decisions.decide({**_call("refund"), "session": "B"})
    assert other_session["decision"] == "held" and other_session["held_call_id"] != held
    service.boundary.pins[record["id"]]["risk_tier"] = "high"
    changed = DecisionService(state, service.boundary.pin(record)).decide(
        {**_call("refund"), "session": "A"}
    )
    assert changed["decision"] == "held" and changed["held_call_id"] != held
    assert state.held(held)["state"] == "approved"  # never consumed by a different binding


def test_reconcile_reports_cleanup_failure_in_the_same_run(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    service.boundary.verify_error = BoundaryUnverified("Agent publishes ports")
    service.boundary.teardown = Mock(side_effect=DeploymentError("stuck"))
    [item] = service.reconcile(actor("dave"))
    assert item["compliance"] == "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED:cleanup_failed"


def test_metadata_call_rechecks_kill_switch_after_exchange(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    record = state.get(result["id"])

    class BlockingBroker:
        def authorization(self, destination, base):
            state.add_blocker(
                record["principal_key"],
                "dev",
                "admin",
                True,
                "x",
                service._event("a", "disable", "blocked", actor("admin")),
            )
            return "Bearer short"

    decisions = DecisionService(state, service.boundary.pin(record), broker=BlockingBroker())
    reply = decisions.decide(_call(method="tools/list", tool=None))
    assert reply["code"] == "PRINCIPAL_BLOCKED" and "authorization" not in reply


def test_checker_judged_on_pinned_attributes_and_drift_denied(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    held = decisions.decide(_call("refund"))["held_call_id"]
    entry = boundary_env.principals["agent-1"]
    entry["attributes"] = {**entry["attributes"], "acme/risk_tier": "high"}
    boundary_env.write()
    with pytest.raises(GateDenied) as caught:
        service.decide_call(held, actor("carol"), approve=True)
    assert caught.value.code == "PRINCIPAL_CHANGED"
    entry["attributes"]["acme/risk_tier"] = "low"
    entry["groups"] = [*entry["groups"], {"group_id": "new-group", "name": "New"}]
    boundary_env.write()
    with pytest.raises(GateDenied) as caught:
        service.decide_call(held, actor("carol"), approve=True)
    assert caught.value.code == "PRINCIPAL_CHANGED"
    record = state.get(result["id"])
    del service.boundary.pins[record["id"]]
    service.boundary.pin = Mock(side_effect=OSError)
    with pytest.raises(GateDenied) as caught:
        service.decide_call(held, actor("carol"), approve=True)
    assert caught.value.code in ("PRINCIPAL_CHANGED", "RUNTIME_BOUNDARY_UNVERIFIED")


def test_reconcile_tears_down_boundary_interrupted_in_starting(boundary_env, state, runtime):
    service, result = _deploy(state, runtime, boundary_env)
    state.transition(result["id"], "starting")
    service.boundary.teardown = Mock()
    [item] = service.reconcile(actor("dave"))
    assert item["compliance"] == "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"
    assert state.get(result["id"])["state"] == "failed"
    service.boundary.teardown.assert_called_once()


# -- independent review (2026-11-06) ------------------------------------------------------------


def test_healthz_is_minimal_and_mac_bound_to_nonce(boundary_env, state, runtime, tmp_path):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    key = hashlib.sha256(b"t0k").hexdigest()
    server, _ = make_server(decisions, {**decisions.pin, "token_sha256": key}, tmp_path)
    server.timeout = 5
    try:
        raw: dict = {}
        nonce = "ab" * 16
        status, body = _request(server, "GET", "/healthz", headers={NONCE_HEADER: nonce}, raw=raw)
        # Only the revision: no deployment id or operator digest to an unauthenticated caller.
        assert status == 200 and body == {"revision": decisions.pin["revision"]}
        assert response_authentic(key, nonce, 200, raw["data"], raw["mac"])
        assert not response_authentic(key, "cd" * 16, 200, raw["data"], raw["mac"])
        raw.clear()
        _request(server, "GET", "/healthz", headers={NONCE_HEADER: "not-hex"}, raw=raw)
        assert raw["mac"] is None
    finally:
        server.server_close()


def test_idle_connection_cannot_stall_decision_service(
    boundary_env, state, runtime, tmp_path, monkeypatch
):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    monkeypatch.setattr(host_module, "HANDLER_TIMEOUT", 0.3)
    server, _ = make_server(decisions, {**decisions.pin, "token_sha256": "x"}, tmp_path)
    server.timeout = 5
    idle = socket.create_connection(("127.0.0.1", server.server_address[1]))
    try:
        threading.Thread(target=server.handle_request, daemon=True).start()  # the idle client
        time.sleep(0.05)
        started = time.monotonic()
        assert _request(server, "GET", "/healthz")[0] == 200
        assert time.monotonic() - started < 3
    finally:
        idle.close()
        server.server_close()


def test_transient_store_error_keeps_service_alive(boundary_env, state, runtime, monkeypatch):
    service, result = _deploy(state, runtime, boundary_env)
    decisions = _decisions(state, service, result)
    monkeypatch.setattr(
        decisions.store, "get", Mock(side_effect=sqlite3.OperationalError("database is locked"))
    )
    assert decisions.active() is True
