"""Fail-closed edge cases for the Docker runtime, JSON transport, remote/Cedar PDPs and doctor."""

from __future__ import annotations

import hashlib
import io
import json
import runpy
import subprocess
import sys
import types
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentbreeder import __version__, doctor
from agentbreeder import runtime as runtime_module
from agentbreeder.identity.transport import (
    MAX_RESPONSE_BYTES,
    JsonTransport,
    TransportError,
    check_url,
)
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import PolicySource
from agentbreeder.policy import PolicyError, PolicyRequest
from agentbreeder.policy.cedar import CedarEvaluator
from agentbreeder.policy.remote import AuthZenEvaluator, OpaEvaluator
from agentbreeder.runtime import Docker
from agentbreeder.store import DeploymentError

RECORD = {"container": "owned", "id": "dep-1", "digest": "sha256:spec"}
DIGEST_IMAGE = "sha256:" + "a" * 64


def manifest(image: str = DIGEST_IMAGE) -> Manifest:
    return Manifest(schema_version=1, name="test", image=image)


def policy_request(action: str = "deploy") -> PolicyRequest:
    return PolicyRequest(
        subject={"type": "user", "id": "dana", "properties": {"verified": True, "issuer": "i"}},
        action=action,
        resource={"type": "agent", "id": "sample", "properties": {"risk_tier": "low"}},
        context={},
    )


# -- runtime: Docker.command -------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure", [OSError("/secret/path"), subprocess.TimeoutExpired("docker", 30)]
)
def test_command_spawn_failure_or_timeout_is_deployment_error(monkeypatch, failure):
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(DeploymentError) as error:
        Docker().command("info")
    assert "timed out" in str(error.value)
    assert "/secret/path" not in str(error.value)
    assert error.value.__cause__ is failure


def test_command_nonzero_exit_is_rejected_without_leaking_stderr(monkeypatch):
    result = subprocess.CompletedProcess([], 1, b"partial", b"token=abc123")
    monkeypatch.setattr(subprocess, "run", Mock(return_value=result))
    with pytest.raises(DeploymentError) as error:
        Docker().command("container", "create", "x")
    assert "rejected" in str(error.value)
    assert "abc123" not in str(error.value) and "partial" not in str(error.value)


def test_command_decodes_bytes_output_and_replaces_invalid_utf8(monkeypatch):
    result = subprocess.CompletedProcess([], 0, b"  ok \xff\n", b"")
    monkeypatch.setattr(subprocess, "run", Mock(return_value=result))
    assert Docker().command("info") == "ok �"


# -- runtime: authorize / inspect / create / lifecycle -----------------------------------------


def test_authorize_local_unix_context_probes_engine(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    runtime = Docker()
    runtime.command = Mock(
        side_effect=[
            "desktop-linux",
            json.dumps([{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]),
            "27.0.1",
        ]
    )
    runtime.authorize()
    assert runtime.context == "desktop-linux"
    calls = [c.args for c in runtime.command.call_args_list]
    assert calls == [
        ("context", "show"),
        ("context", "inspect", "desktop-linux"),
        ("version", "--format", "{{.Server.Version}}"),
    ]
    assert runtime.command.call_args_list[-1].kwargs == {"timeout": 10}


def test_authorize_engine_unreachable_fails_closed(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    runtime = Docker()
    runtime.command = Mock(
        side_effect=[
            json.dumps([{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]),
            DeploymentError("Docker rejected the operation"),
        ]
    )
    with pytest.raises(DeploymentError):
        runtime.authorize("local")


@pytest.mark.parametrize("payload", ["not json", "[]", json.dumps([{"Endpoints": {}}])])
def test_authorize_unverifiable_context_denied(monkeypatch, payload):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    runtime = Docker()
    runtime.command = Mock(return_value=payload)
    with pytest.raises(DeploymentError, match="Cannot verify"):
        runtime.authorize("local")
    assert runtime.command.call_count == 1  # engine never probed


def test_inspect_returns_info_only_when_labels_match():
    runtime = Docker()
    info = {
        "Id": "abc",
        "Config": {
            "Labels": {
                "io.agentbreeder.deployment": RECORD["id"],
                "io.agentbreeder.spec": RECORD["digest"],
            }
        },
    }
    runtime.command = Mock(side_effect=["abc", json.dumps([info])])
    assert runtime.inspect(RECORD) == info
    assert runtime.command.call_args_list[0].args[4] == "name=^/owned$"


def test_inspect_spec_digest_mismatch_denied():
    runtime = Docker()
    labels = {"io.agentbreeder.deployment": RECORD["id"], "io.agentbreeder.spec": "other"}
    runtime.command = Mock(side_effect=["abc", json.dumps([{"Config": {"Labels": labels}}])])
    with pytest.raises(DeploymentError, match="ownership"):
        runtime.inspect(RECORD)


def test_inspect_malformed_info_denied():
    runtime = Docker()
    runtime.command = Mock(side_effect=["abc", json.dumps([{"NoConfig": {}}])])
    with pytest.raises(DeploymentError, match="Cannot verify container ownership"):
        runtime.inspect(RECORD)


def test_create_pulls_registry_image_then_creates_on_internal_network():
    runtime = Docker()
    runtime.command = Mock(side_effect=["", json.dumps([{"Config": {}}]), "container-id"])
    image = "example.com/agent@sha256:" + "a" * 64
    runtime.create(RECORD, manifest(image), network="ab-net", environment={"MODE": "x"})
    pull, inspect, create = runtime.command.call_args_list
    assert pull.args == ("image", "pull", image) and pull.kwargs == {"timeout": 120}
    assert inspect.args == ("image", "inspect", image)
    args = create.args
    assert args[args.index("--network") + 1] == "ab-net"
    assert "--publish" not in args
    assert "--env=MODE=x" in args and args[-1] == image


def test_create_pull_failure_creates_nothing():
    runtime = Docker()
    runtime.command = Mock(side_effect=DeploymentError("Docker rejected the operation"))
    with pytest.raises(DeploymentError):
        runtime.create(RECORD, manifest("example.com/agent@sha256:" + "a" * 64))
    assert runtime.command.call_count == 1


@pytest.mark.parametrize("payload", ["{bad", "[]", json.dumps([{}])])
def test_create_unverifiable_image_creates_nothing(payload):
    runtime = Docker()
    runtime.command = Mock(return_value=payload)
    with pytest.raises(DeploymentError, match="Cannot verify image"):
        runtime.create(RECORD, manifest())
    assert runtime.command.call_count == 1


def test_start_and_remove_target_container_id():
    runtime = Docker()
    runtime.command = Mock(return_value="")
    runtime.start({"Id": "abc"})
    runtime.remove({"Id": "abc"})
    assert [c.args for c in runtime.command.call_args_list] == [
        ("container", "start", "abc"),
        ("container", "rm", "--force", "--volumes", "abc"),
    ]


def test_start_failure_propagates():
    runtime = Docker()
    runtime.command = Mock(side_effect=DeploymentError("Docker rejected the operation"))
    with pytest.raises(DeploymentError):
        runtime.start({"Id": "abc"})


# -- runtime: endpoint -------------------------------------------------------------------------


def endpoint_info(port: str, bindings: int = 1) -> dict:
    binding = {"HostIp": "127.0.0.1", "HostPort": port}
    return {
        "State": {"Running": True},
        "NetworkSettings": {"Ports": {"8080/tcp": [binding] * bindings}},
    }


def test_endpoint_single_loopback_binding_returns_url():
    assert Docker().endpoint(endpoint_info("49153"), manifest()) == "http://127.0.0.1:49153"


@pytest.mark.parametrize("port", ["0", "65536", "99999", "12a", ""])
def test_endpoint_out_of_range_or_malformed_port_denied(port):
    with pytest.raises(DeploymentError, match="loopback"):
        Docker().endpoint(endpoint_info(port), manifest())


def test_endpoint_multiple_bindings_or_missing_port_denied():
    with pytest.raises(DeploymentError):
        Docker().endpoint(endpoint_info("1234", bindings=2), manifest())
    with pytest.raises(DeploymentError):
        Docker().endpoint({"State": {"Running": True}, "NetworkSettings": {}}, manifest())


# -- runtime: healthy --------------------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.5)


class FakeConnection:
    def __init__(self, clock: FakeClock, chunks, advance: float = 0.0):
        self.clock, self.chunks, self.advance = clock, list(chunks), advance
        self.sent = b""
        self.recv_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def sendall(self, data):
        self.sent += data

    def settimeout(self, value):
        assert value > 0

    def recv(self, size):
        self.recv_calls += 1
        self.clock.now += self.advance
        return self.chunks.pop(0) if self.chunks else b""


def patch_probe(monkeypatch, clock, make_connection):
    connections = []

    def create_connection(address, timeout):
        assert address[0] == "127.0.0.1" and timeout > 0
        connection = make_connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr(runtime_module, "time", clock)
    monkeypatch.setattr(
        runtime_module, "socket", SimpleNamespace(create_connection=create_connection)
    )
    return connections


def test_healthy_truncated_response_before_headers_end_is_not_healthy(monkeypatch):
    clock = FakeClock()
    connections = patch_probe(
        monkeypatch, clock, lambda: FakeConnection(clock, [b"HTTP/1.1 200 OK\r\n"])
    )
    assert Docker().healthy("http://127.0.0.1:5000", "/health", timeout=1) is False
    assert connections and all(c.recv_calls == 2 for c in connections)  # data, then EOF
    assert connections[0].sent.startswith(b"GET /health HTTP/1.1\r\n")


def test_healthy_probe_budget_exhausted_stops_reading(monkeypatch):
    clock = FakeClock()
    connections = patch_probe(
        monkeypatch,
        clock,
        lambda: FakeConnection(clock, [b"HTTP/1.1 200", b" OK\r\n\r\n"], advance=3.0),
    )
    assert Docker().healthy("http://127.0.0.1:5000", "/health", timeout=1) is False
    assert len(connections) == 1 and connections[0].recv_calls == 1


def test_healthy_non_200_is_not_healthy(monkeypatch):
    clock = FakeClock()
    patch_probe(
        monkeypatch,
        clock,
        lambda: FakeConnection(clock, [b"HTTP/1.1 503 Unavailable\r\n\r\n"]),
    )
    assert Docker().healthy("http://127.0.0.1:5000", "/health", timeout=1) is False


def test_healthy_200_with_header_terminator_is_healthy(monkeypatch):
    clock = FakeClock()
    patch_probe(monkeypatch, clock, lambda: FakeConnection(clock, [b"HTTP/1.0 200 OK\r\n\r\n"]))
    assert Docker().healthy("http://127.0.0.1:5000", "/health", timeout=1) is True


# -- identity transport ------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["http://localhost:8181", "http://127.0.0.1:8181", "http://[::1]"])
def test_loopback_http_allowed_only_when_opted_in(url):
    assert check_url(url, allow_loopback_http=True) == url
    with pytest.raises(TransportError) as error:
        check_url(url)
    assert error.value.code == "PROVIDER_ERROR"


@pytest.mark.parametrize("url", ["http://localhost.example.com", "http://10.0.0.1"])
def test_non_loopback_http_denied_even_when_opted_in(url):
    with pytest.raises(TransportError):
        check_url(url, allow_loopback_http=True)


class FakeResponse:
    def __init__(self, status: int, raw: bytes):
        self.status, self.stream = status, io.BytesIO(raw)

    def read(self, size=-1):
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def transport_with(opener_open) -> tuple[JsonTransport, list]:
    sent: list = []
    transport = JsonTransport()

    def open_(request, timeout):
        sent.append((request, timeout))
        return opener_open(request)

    transport._opener = SimpleNamespace(open=open_)
    return transport, sent


def test_form_body_is_urlencoded_with_form_content_type():
    transport, sent = transport_with(lambda r: FakeResponse(200, b'{"access_token": "t"}'))
    status, body = transport.request(
        "POST", "https://idp.example.com/token", form={"grant_type": "x y", "a": "&"}
    )
    assert (status, body) == (200, {"access_token": "t"})
    request, timeout = sent[0]
    assert request.data == b"grant_type=x+y&a=%26"
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert request.get_header("Accept") == "application/json"
    assert timeout == transport.timeout


def test_oversized_success_response_is_rejected():
    raw = b"[" + b" " * MAX_RESPONSE_BYTES + b"]"
    transport, _ = transport_with(lambda r: FakeResponse(200, raw))
    with pytest.raises(TransportError) as error:
        transport.request("GET", "https://idp.example.com/jwks")
    assert error.value.code == "PROVIDER_ERROR" and "too large" in str(error.value)


def test_empty_success_response_is_none():
    transport, _ = transport_with(lambda r: FakeResponse(204, b""))
    assert transport.request("GET", "https://idp.example.com/x") == (204, None)


def http_error(code: int, raw: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://idp.example.com", code, "err", {}, io.BytesIO(raw))


def test_http_error_with_unparseable_body_keeps_status_but_drops_body():
    def raise_(request):
        raise http_error(401, b"<html>secret</html>")

    transport, _ = transport_with(raise_)
    assert transport.request("GET", "https://idp.example.com/x") == (401, None)


def test_http_error_with_oversized_body_keeps_status_but_drops_body():
    def raise_(request):
        raise http_error(500, b"{" * (MAX_RESPONSE_BYTES + 1))

    transport, _ = transport_with(raise_)
    assert transport.request("GET", "https://idp.example.com/x") == (500, None)


def test_http_error_with_json_body_is_returned():
    def raise_(request):
        raise http_error(400, b'{"error": "invalid_grant"}')

    transport, _ = transport_with(raise_)
    assert transport.request("POST", "https://idp.example.com/t", json_body={}) == (
        400,
        {"error": "invalid_grant"},
    )


@pytest.mark.parametrize(
    "failure,code",
    [
        (urllib.error.URLError(TimeoutError()), "PROVIDER_TIMEOUT"),
        (urllib.error.URLError(ConnectionRefusedError("refused 10.0.0.5")), "PROVIDER_ERROR"),
        (ConnectionResetError("reset"), "PROVIDER_ERROR"),
        (TimeoutError(), "PROVIDER_TIMEOUT"),
    ],
)
def test_network_failures_map_to_provider_codes(failure, code):
    def raise_(request):
        raise failure

    transport, _ = transport_with(raise_)
    with pytest.raises(TransportError) as error:
        transport.request("GET", "https://idp.example.com/x")
    assert error.value.code == code
    assert "10.0.0.5" not in str(error.value)


def test_transport_refuses_plain_http_before_any_io():
    transport, sent = transport_with(lambda r: pytest.fail("must not open"))
    with pytest.raises(TransportError):
        transport.request("GET", "http://idp.example.com/x")
    assert sent == []


# -- remote PDPs -------------------------------------------------------------------------------


class Responder:
    def __init__(self, status=200, body=None, error: Exception | None = None):
        self.status, self.body, self.error, self.sent = status, body, error, []

    def request(self, method, url, *, headers=None, json_body=None, **kwargs):
        self.sent.append((method, url, json_body))
        if self.error:
            raise self.error
        return self.status, self.body


def opa(responder, **source) -> OpaEvaluator:
    fields = {"url": "https://opa.example.com/", "revision": "r1", "path": "agentbreeder/allow"}
    return OpaEvaluator(PolicySource(**{**fields, **source}), responder)


def authzen(responder) -> AuthZenEvaluator:
    return AuthZenEvaluator(PolicySource(url="https://pdp.example.com", revision="r1"), responder)


@pytest.mark.parametrize("evaluator", [opa, authzen])
def test_remote_transport_failure_is_policy_error(evaluator):
    responder = Responder(error=TransportError("PROVIDER_TIMEOUT", "timed out at 10.1.2.3"))
    with pytest.raises(PolicyError) as error:
        evaluator(responder).evaluate(policy_request())
    assert error.value.code == "POLICY_ERROR" and "10.1.2.3" not in str(error.value)


@pytest.mark.parametrize("evaluator", [opa, authzen])
@pytest.mark.parametrize("body", [None, [], "allow", True])
def test_remote_non_object_200_is_policy_error(evaluator, body):
    with pytest.raises(PolicyError) as error:
        evaluator(Responder(200, body)).evaluate(policy_request())
    assert error.value.code == "POLICY_ERROR"


def test_opa_unbundled_revision_from_provenance_allows():
    responder = Responder(200, {"provenance": {"revision": "r1"}, "result": {"allow": True}})
    result = opa(responder).evaluate(policy_request())
    assert result.allowed is True and result.obligations == () and result.evidence == "revision:r1"
    method, url, body = responder.sent[0]
    assert method == "POST"
    assert url == "https://opa.example.com/v1/data/agentbreeder/allow?provenance=true"
    assert body["input"]["action"] == {"name": "deploy"}


def test_opa_bundle_revision_used_instead_of_top_level():
    body = {
        "provenance": {"revision": "r1", "bundles": {"main": {"revision": "r0"}}},
        "result": {"allow": True},
    }
    with pytest.raises(PolicyError) as error:
        opa(Responder(200, body), bundle="main").evaluate(policy_request())
    assert error.value.code == "POLICY_REVISION_MISMATCH"


@pytest.mark.parametrize(
    "result", [None, {}, {"allow": "true"}, {"allow": 1}, ["allow"], {"deny": False}]
)
def test_opa_result_without_boolean_allow_is_policy_error(result):
    body = {"provenance": {"revision": "r1"}, "result": result}
    with pytest.raises(PolicyError) as error:
        opa(Responder(200, body)).evaluate(policy_request())
    assert error.value.code == "POLICY_ERROR"


def test_opa_missing_path_denied_before_any_request():
    responder = Responder(200, {})
    with pytest.raises(PolicyError):
        opa(responder, path=None).evaluate(policy_request())
    assert responder.sent == []


def test_opa_require_approval_obligation_accepted_in_both_shapes():
    body = {
        "provenance": {"revision": "r1"},
        "result": {"allow": True, "obligations": [{"name": "require_approval"}, "audit"]},
    }
    assert opa(Responder(200, body)).evaluate(policy_request()).obligations == (
        "require_approval",
        "audit",
    )


@pytest.mark.parametrize("context", [["policy_revision", "r1"], "r1", 7])
def test_authzen_non_object_context_is_policy_error(context):
    with pytest.raises(PolicyError) as error:
        authzen(Responder(200, {"decision": True, "context": context})).evaluate(policy_request())
    assert error.value.code == "POLICY_ERROR" and "context" in str(error.value)


@pytest.mark.parametrize("obligations", [{"name": "require_approval"}, "require_approval", 1])
def test_obligations_not_a_list_fail_closed(obligations):
    body = {"decision": True, "context": {"policy_revision": "r1", "obligations": obligations}}
    with pytest.raises(PolicyError) as error:
        authzen(Responder(200, body)).evaluate(policy_request())
    assert error.value.code == "OBLIGATION_UNSUPPORTED"


@pytest.mark.parametrize("item", [1, None, {"name": 5}, {"type": "require_approval"}, ["x"]])
def test_malformed_obligation_item_fails_closed(item):
    body = {"provenance": {"revision": "r1"}, "result": {"allow": True, "obligations": [item]}}
    with pytest.raises(PolicyError) as error:
        opa(Responder(200, body)).evaluate(policy_request())
    assert error.value.code == "OBLIGATION_UNSUPPORTED"


def test_remote_unresolvable_credential_is_config_error(monkeypatch):
    monkeypatch.delenv("PDP_TOKEN", raising=False)
    with pytest.raises(PolicyError) as error:
        AuthZenEvaluator(
            PolicySource(url="https://pdp.example.com", revision="r1", token="env:PDP_TOKEN"),
            Responder(),
        )
    assert error.value.code == "POLICY_ERROR"


# -- Cedar -------------------------------------------------------------------------------------


def write_policy_bytes(tmp_path, data: bytes, name="p.cedar") -> PolicySource:
    path = tmp_path / name
    path.write_bytes(data)
    path.chmod(0o600)
    return PolicySource(source=name, digest="sha256:" + hashlib.sha256(data).hexdigest())


def fake_cedarpy(monkeypatch, *, errors=(), allowed=True, valid=True) -> types.ModuleType:
    module = types.ModuleType("cedarpy")
    module.validate_policies = Mock(return_value=SimpleNamespace(validation_passed=valid))
    module.is_authorized = Mock(
        return_value=SimpleNamespace(
            allowed=allowed, diagnostics=SimpleNamespace(errors=list(errors))
        )
    )
    monkeypatch.setitem(sys.modules, "cedarpy", module)
    return module


def test_cedar_missing_dependency_is_policy_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "cedarpy", None)  # makes `import cedarpy` raise ImportError
    source = write_policy_bytes(tmp_path, b"permit(principal, action, resource);")
    with pytest.raises(PolicyError) as error:
        CedarEvaluator(source, tmp_path).evaluate(policy_request())
    assert error.value.code == "POLICY_ERROR" and "agentbreeder[cedar]" in str(error.value)


def test_cedar_unsupported_action_denied_before_loading(monkeypatch, tmp_path):
    module = fake_cedarpy(monkeypatch)
    source = PolicySource(source="absent.cedar", digest="sha256:" + "0" * 64)
    with pytest.raises(PolicyError) as error:
        CedarEvaluator(source, tmp_path).evaluate(policy_request(action="delete"))
    assert error.value.code == "POLICY_ERROR"
    module.is_authorized.assert_not_called()


def test_cedar_unreadable_policy_is_policy_error(monkeypatch, tmp_path):
    module = fake_cedarpy(monkeypatch)
    source = PolicySource(source="absent.cedar", digest="sha256:" + "0" * 64)
    with pytest.raises(PolicyError) as error:
        CedarEvaluator(source, tmp_path).evaluate(policy_request())
    assert error.value.code == "POLICY_ERROR" and "Cannot read" in str(error.value)
    module.validate_policies.assert_not_called()


def test_cedar_world_readable_policy_is_policy_error(monkeypatch, tmp_path):
    fake_cedarpy(monkeypatch)
    source = write_policy_bytes(tmp_path, b"permit(principal, action, resource);")
    (tmp_path / "p.cedar").chmod(0o644)
    with pytest.raises(PolicyError) as error:
        CedarEvaluator(source, tmp_path).evaluate(policy_request())
    assert error.value.code == "POLICY_ERROR"


def test_cedar_non_utf8_policy_with_matching_digest_is_policy_error(monkeypatch, tmp_path):
    module = fake_cedarpy(monkeypatch)
    source = write_policy_bytes(tmp_path, b"permit(principal, action, resource); // \xff\xfe")
    with pytest.raises(PolicyError) as error:
        CedarEvaluator(source, tmp_path).evaluate(policy_request())
    assert error.value.code == "POLICY_ERROR" and "UTF-8" in str(error.value)
    module.validate_policies.assert_not_called()


def test_cedar_evaluation_diagnostics_errors_fail_closed_even_if_allowed(monkeypatch, tmp_path):
    module = fake_cedarpy(monkeypatch, errors=["entity attribute type mismatch"], allowed=True)
    source = write_policy_bytes(tmp_path, b"permit(principal, action, resource);")
    with pytest.raises(PolicyError) as error:
        CedarEvaluator(source, tmp_path).evaluate(policy_request())
    assert error.value.code == "POLICY_ERROR" and "mismatch" not in str(error.value)
    assert module.is_authorized.call_count == 1  # no obligation probe after an error


def test_cedar_non_true_allowed_is_deny(monkeypatch, tmp_path):
    fake_cedarpy(monkeypatch, allowed="yes")
    source = write_policy_bytes(tmp_path, b"permit(principal, action, resource);")
    result = CedarEvaluator(source, tmp_path).evaluate(policy_request())
    assert result.allowed is False and result.obligations == ()
    assert result.evidence == source.digest


# -- doctor / __main__ -------------------------------------------------------------------------


def test_doctor_engine_unreachable_fails_without_stderr(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda command, **kw: subprocess.CompletedProcess(command, 1, "", "dial unix /secret"),
    )
    check = doctor.check_docker()[0]
    assert check.passed is False and "Cannot reach the engine" in check.message
    assert "/secret" not in check.message


def test_python_dash_m_entrypoint_reports_version(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["agentbreeder", "--version"])
    monkeypatch.delitem(sys.modules, "agentbreeder.__main__", raising=False)
    with pytest.raises(SystemExit) as exit_:
        runpy.run_module("agentbreeder", run_name="__main__")
    assert exit_.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_authorize_empty_engine_version_fails_closed(monkeypatch):
    # Regression: an unreachable engine must not authorize just because the CLI exited 0.
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    runtime = Docker()
    runtime.command = Mock(
        side_effect=[
            json.dumps([{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}]),
            "",
        ]
    )
    with pytest.raises(DeploymentError, match="Cannot reach the Docker engine"):
        runtime.authorize("local")
