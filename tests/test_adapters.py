"""P1/P3 adapters from recorded fixtures: scoped grants kept, names display-only."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest
from conftest import write_private

from agentbreeder.identity import (
    GroupGrant,
    IdentityProvider,
    ProviderContext,
    ProviderError,
    RoleGrant,
    load_provider,
)
from agentbreeder.identity.aws import AwsProvider
from agentbreeder.identity.transport import JsonTransport, TransportError, check_url

FIXTURES = Path(__file__).parent / "fixtures"
ENTRA_ID = "4f1c0000-0000-4000-8000-000000000001"


class FakeTransport:
    def __init__(self, name: str | None = None, routes: dict | None = None):
        self.routes = dict(routes or {})
        if name:
            data = json.loads((FIXTURES / f"{name}.json").read_text())
            data.pop("_note")
            self.routes.update(data)
        self.calls = []

    def request(self, method, url, *, headers=None, json_body=None, form=None):
        self.calls.append((method, url, headers, json_body, form))
        route = self.routes.get(f"{method} {url}")
        if isinstance(route, Exception):
            raise route
        if route is None:
            return 404, {"error": "not recorded"}
        return route[0], route[1]


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("IDP_SECRET", "client-secret")


def entra(transport, **extra):
    settings = {
        "type": "entra",
        "tenant_id": "tenant-a",
        "client_id": "app",
        "credential": "env:IDP_SECRET",
        **extra,
    }
    return load_provider("entra", {"entra": settings}, transport=transport)


def test_entra_fixture_preserves_scoped_grants(secret):
    transport = FakeTransport("entra")
    principal = entra(transport).resolve(ENTRA_ID)
    assert isinstance(entra(transport), IdentityProvider)
    assert principal.kind == "agent" and principal.enabled and principal.authority == "tenant-a"
    assert principal.roles == {
        RoleGrant("tenant-a", "payments-api-sp", "refunds-role-id"),
        RoleGrant("tenant-a", "crm-api-sp", "crm-read-role-id"),
    }
    assert principal.groups == {GroupGrant("tenant-a", "finance-group-id")}
    assert principal.environment == "prod" and principal.review_by == date(2099, 6, 30)
    assert principal.attributes["acmeGovernance/risk_tier"] == "high"
    assert "acmeGovernance/regions" not in principal.attributes  # multi-valued: ambiguous
    active = [p for p in principal.sponsors if p.active and p.kind == "human"]
    assert [p.id for p in active] == ["alice-oid"]
    assert principal.owners[0].active is False
    assert principal.evidence_digest.startswith("sha256:")
    token_call = transport.calls[0]
    assert token_call[4]["grant_type"] == "client_credentials"
    assert all("Bearer graph-token" == c[2]["Authorization"] for c in transport.calls[1:])
    assert all(c[0] == "GET" for c in transport.calls[1:])  # read-only


def test_entra_display_names_do_not_create_grants(secret):
    principal = entra(FakeTransport("entra")).resolve(ENTRA_ID)
    assert RoleGrant("tenant-a", "Payments API", "refunds-role-id") not in principal.roles
    assert GroupGrant("tenant-a", "Finance") not in principal.groups
    assert RoleGrant("tenant-b", "payments-api-sp", "refunds-role-id") not in principal.roles


def test_entra_non_agent_service_principal_is_workload_without_sponsors(secret):
    base = f"https://graph.microsoft.com/v1.0/servicePrincipals/{ENTRA_ID}"
    transport = FakeTransport("entra")
    select = "$select=id,accountEnabled,servicePrincipalType,customSecurityAttributes"
    del transport.routes[f"GET {base}/microsoft.graph.agentIdentity?{select}"]
    transport.routes[f"GET {base}?{select}"] = [
        200,
        {"id": ENTRA_ID, "accountEnabled": True, "servicePrincipalType": "Application"},
    ]
    principal = entra(transport).resolve(ENTRA_ID)
    assert principal.kind == "workload" and principal.sponsors == ()


@pytest.mark.parametrize(
    "failure,code",
    [
        (TransportError("PROVIDER_TIMEOUT", "Identity provider timed out"), "PROVIDER_TIMEOUT"),
        (TransportError("PROVIDER_ERROR", "unreachable"), "PROVIDER_ERROR"),
        ([403, {"error": {"message": "Insufficient privileges"}}], "PROVIDER_ERROR"),
        ([500, None], "PROVIDER_ERROR"),
    ],
)
def test_entra_failures_fail_closed(secret, failure, code):
    transport = FakeTransport("entra")
    key = next(k for k in transport.routes if "appRoleAssignments" in k)
    transport.routes[key] = failure
    with pytest.raises(ProviderError) as error:
        entra(transport).resolve(ENTRA_ID)
    assert error.value.code == code
    assert "Insufficient privileges" not in str(error.value)


def test_entra_ambiguous_and_cross_origin_paging(secret):
    transport = FakeTransport("entra")
    key = next(k for k in transport.routes if "agentIdentity?$select" in k)
    transport.routes[key] = [200, {"id": "someone-else", "accountEnabled": True}]
    with pytest.raises(ProviderError, match="different principal"):
        entra(transport).resolve(ENTRA_ID)
    transport = FakeTransport("entra")
    key = next(k for k in transport.routes if k.endswith("/appRoleAssignments"))
    transport.routes[key][1]["@odata.nextLink"] = "https://evil.example.com/next"
    with pytest.raises(ProviderError, match="cross-origin"):
        entra(transport).resolve(ENTRA_ID)


def test_entra_unknown_principal(secret):
    with pytest.raises(ProviderError) as error:
        entra(FakeTransport("entra")).resolve("00000000-0000-4000-8000-00000000dead")
    assert error.value.code == "PRINCIPAL_NOT_FOUND"


def test_entra_disable_requires_opt_in(secret):
    transport = FakeTransport("entra")
    with pytest.raises(ProviderError, match="not opted in"):
        entra(transport).disable(ENTRA_ID)
    transport.routes[f"PATCH https://graph.microsoft.com/v1.0/servicePrincipals/{ENTRA_ID}"] = [
        204,
        None,
    ]
    entra(transport, write_enabled=True).disable(ENTRA_ID)
    assert transport.calls[-1][3] == {"accountEnabled": False}


def test_entra_rejects_path_injection(secret):
    with pytest.raises(ProviderError, match="reference format"):
        entra(FakeTransport("entra")).resolve("../users")


def keycloak(transport):
    settings = {
        "type": "keycloak",
        "base_url": "https://kc.example.com",
        "realm": "acme",
        "client_id": "agentbreeder-reader",
        "credential": "env:IDP_SECRET",
    }
    return load_provider("kc", {"kc": settings}, transport=transport)


def test_keycloak_fixture_scoped_realm_and_client_roles(secret):
    principal = keycloak(FakeTransport("keycloak")).resolve("client-uuid-1")
    authority = "https://kc.example.com/realms/acme"
    assert principal.provider == "kc" and principal.authority == authority
    assert principal.kind == "agent" and principal.enabled
    assert principal.roles == {
        RoleGrant(authority, "realm", "realm-role-uuid"),
        RoleGrant(authority, "payments-client-uuid", "refunds-role-uuid"),
    }
    assert principal.groups == {GroupGrant(authority, "finance-group-uuid")}
    assert principal.environment == "prod"
    assert principal.attributes["acme/data_classification"] == "confidential"
    assert "acme/regions" not in principal.attributes
    assert [(p.id, p.active) for p in principal.sponsors] == [
        ("alice-user", True),
        ("gone-user", False),
    ]
    # Same role name in the realm namespace vs the client namespace never collide.
    assert RoleGrant(authority, "realm", "refunds-role-uuid") not in principal.roles


def test_keycloak_client_without_service_account_is_not_workload(secret):
    transport = FakeTransport("keycloak")
    transport.routes["GET https://kc.example.com/admin/realms/acme/clients/client-uuid-1"][1] = {
        "id": "client-uuid-1",
        "enabled": True,
        "serviceAccountsEnabled": False,
    }
    principal = keycloak(transport).resolve("client-uuid-1")
    assert principal.kind == "unknown" and not principal.enabled


def test_keycloak_timeout_fails_closed(secret):
    transport = FakeTransport("keycloak")
    transport.routes[
        "GET https://kc.example.com/admin/realms/acme/users/sa-user-1/role-mappings"
    ] = TransportError("PROVIDER_TIMEOUT", "timed out")
    with pytest.raises(ProviderError) as error:
        keycloak(transport).resolve("client-uuid-1")
    assert error.value.code == "PROVIDER_TIMEOUT"


def test_keycloak_truncated_groups_fail_closed(secret):
    transport = FakeTransport("keycloak")
    key = next(k for k in transport.routes if "/groups?" in k)
    transport.routes[key] = [200, [{"id": f"g{i}"} for i in range(500)]]
    with pytest.raises(ProviderError, match="incomplete"):
        keycloak(transport).resolve("client-uuid-1")


def okta(transport, **extra):
    settings = {
        "type": "okta",
        "org_url": "https://acme.okta.com",
        "token": "env:IDP_SECRET",
        **extra,
    }
    return load_provider("okta", {"okta": settings}, transport=transport)


def test_okta_requires_experimental_opt_in(secret):
    with pytest.raises(ProviderError) as error:
        okta(FakeTransport("okta"))
    assert error.value.code == "PROVIDER_UNSUPPORTED"


def test_okta_fixture(secret):
    transport = FakeTransport("okta")
    principal = okta(transport, experimental=True).resolve("0oa-agent-1")
    assert principal.kind == "agent" and principal.enabled and principal.environment == "prod"
    assert principal.roles == {
        RoleGrant("https://acme.okta.com", "okta-admin-roles", "cr-refunds")
    }
    assert principal.groups == frozenset()  # app assignments are not memberships
    assert principal.attributes["okta/dataClassification"] == "confidential"
    assert principal.sponsors[0].active
    assert transport.calls[0][2]["Authorization"] == "SSWS client-secret"
    with pytest.raises(ProviderError, match="not opted in"):
        okta(transport, experimental=True).disable("0oa-agent-1")
    okta(transport, experimental=True, write_enabled=True).disable("0oa-agent-1")
    assert transport.calls[-1][1].endswith("/lifecycle/deactivate")


class FakeAws:
    class NotFound(Exception):
        response = {"Error": {"Code": "ResourceNotFoundException"}}

    def __init__(self, tags=None, account="111122223333"):
        self.tags = tags or {
            "agentbreeder:environment": "prod",
            "agentbreeder:review-by": "2099-01-01",
            "agentbreeder:sponsors": "user-alice user-gone",
            "data_classification": "confidential",
        }
        self.account = account

    def __call__(self, service):
        return self

    def get_workload_identity(self, name):
        if name != "refund-agent":
            raise self.NotFound()
        arn = f"arn:aws:bedrock-agentcore:us-east-1:{self.account}:workload-identity/{name}"
        return {"name": name, "workloadIdentityArn": arn, "ResponseMetadata": {"RequestId": "x"}}

    def list_tags_for_resource(self, resourceArn):
        return {"tags": self.tags}

    def describe_user(self, IdentityStoreId, UserId):
        if UserId == "user-gone":
            raise self.NotFound()
        return {"UserId": UserId, "UserName": "alice"}


def aws_provider(clients, **extra):
    settings = {
        "trust_identity_store_presence": True,
        "account_id": "111122223333",
        "region": "us-east-1",
        "identity_store_id": "d-1",
        **extra,
    }
    return AwsProvider(ProviderContext("aws", settings, FakeTransport(), None), clients=clients)


def test_aws_workload_identity_facts():
    principal = aws_provider(FakeAws()).resolve("refund-agent")
    assert principal.kind == "agent" and principal.authority == "111122223333"
    assert principal.environment == "prod" and principal.review_by == date(2099, 1, 1)
    assert principal.attributes == {"aws-tag/data_classification": "confidential"}
    assert [(p.id, p.active) for p in principal.sponsors] == [
        ("user-alice", True),
        ("user-gone", False),
    ]
    assert principal.roles == frozenset()  # role verification unavailable: required roles deny


def test_aws_failures():
    with pytest.raises(ProviderError) as error:
        aws_provider(FakeAws()).resolve("missing")
    assert error.value.code == "PRINCIPAL_NOT_FOUND"
    with pytest.raises(ProviderError, match="another account"):
        aws_provider(FakeAws(account="999999999999")).resolve("refund-agent")
    principal = aws_provider(FakeAws(), identity_store_id=None).resolve("refund-agent")
    assert not any(p.active for p in principal.sponsors)
    principal = aws_provider(FakeAws(), trust_identity_store_presence=False).resolve(
        "refund-agent"
    )
    assert not any(p.active for p in principal.sponsors)  # presence alone is not "active"


def test_static_adapter_permissions_and_malformed(identity_env, tmp_path):
    provider = load_provider(
        "static", identity_env.load().model.providers, base=identity_env.directory
    )
    assert provider.resolve("agent-1").id == "agent-1"
    (identity_env.directory / "principals.yaml").chmod(0o644)
    with pytest.raises(ProviderError, match="0600"):
        provider.resolve("agent-1")
    write_private(identity_env.directory / "principals.yaml", "principals: [1, 2]")
    with pytest.raises(ProviderError, match="principals map"):
        provider.resolve("agent-1")
    write_private(
        identity_env.directory / "principals.yaml", "principals: {agent-1: {roles: [{}]}}"
    )
    with pytest.raises(ProviderError, match="Malformed"):
        provider.resolve("agent-1")
    write_private(
        identity_env.directory / "principals.yaml",
        "principals: {agent-1: {review_by: soon}}",
    )
    with pytest.raises(ProviderError, match="review date"):
        provider.resolve("agent-1")


def test_static_ambiguous_reference(identity_env):
    identity_env.principals = {"agent-1": {"kind": "agent"}, "other": {"id": "agent-1"}}
    identity_env.write()
    provider = load_provider(
        "static", identity_env.load().model.providers, base=identity_env.directory
    )
    with pytest.raises(ProviderError) as error:
        provider.resolve("agent-1")
    assert error.value.code == "PRINCIPAL_AMBIGUOUS"


def test_plugins_cannot_shadow_builtins(monkeypatch):
    from agentbreeder import identity

    class Entry:
        name = "static"

        def load(self):
            raise AssertionError("plugin must not load")

    monkeypatch.setattr(identity, "entry_points", lambda group: [Entry()])
    assert identity.available_types()["static"] is identity._builtins()["static"]


def test_plugin_entry_point_loaded_lazily(monkeypatch):
    from agentbreeder import identity

    class Plugin:
        def __init__(self, context):
            self.name = context.name

        def resolve(self, ref):
            raise ProviderError("PRINCIPAL_NOT_FOUND", "none")

    class Entry:
        name = "corp"

        def load(self):
            return Plugin

    monkeypatch.setattr(identity, "entry_points", lambda group: [Entry()])
    provider = load_provider("mine", {"mine": {"type": "corp"}})
    assert provider.name == "mine"
    with pytest.raises(ProviderError) as error:
        load_provider("x", {"x": {"type": "nope"}})
    assert error.value.code == "PROVIDER_UNKNOWN"


def test_transport_url_policy():
    assert check_url("https://graph.microsoft.com/v1.0")
    assert check_url("http://127.0.0.1:8181", allow_loopback_http=True)
    for url in ("http://graph.microsoft.com", "https://user:pw@host/", "file:///etc/passwd"):
        with pytest.raises(TransportError):
            check_url(url, allow_loopback_http=True)
    with pytest.raises(TransportError):
        JsonTransport().request("GET", "http://example.com")


def test_transport_real_http_timeout_and_redirect():
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/slow":
                time.sleep(1)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/x")
                self.end_headers()
                return
            body = b'{"ok": true}' if self.path != "/bad" else b"not json"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        transport = JsonTransport(timeout=0.2, allow_loopback_http=True)
        assert transport.request("GET", base + "/ok") == (200, {"ok": True})
        assert transport.request("GET", base + "/redirect")[0] == 302
        with pytest.raises(TransportError) as error:
            transport.request("GET", base + "/slow")
        assert error.value.code == "PROVIDER_TIMEOUT"
        with pytest.raises(TransportError, match="invalid JSON"):
            transport.request("GET", base + "/bad")
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.entra_live
@pytest.mark.skipif(not os.getenv("AGENTBREEDER_ENTRA_LIVE"), reason="Live Entra tenant test")
def test_entra_live_typed_lookup():
    """Verifies the Graph agentIdentity typed lookup against a real tenant (read-only)."""
    settings = json.loads(os.environ["AGENTBREEDER_ENTRA_LIVE"])
    provider = load_provider("entra", {"entra": {"type": "entra", **settings["connection"]}})
    principal = provider.resolve(settings["agent_id"])
    assert principal.kind == "agent" and principal.authority == settings["connection"]["tenant_id"]


def test_aws_error_translation_and_missing_boto3(monkeypatch):
    import builtins

    from agentbreeder.identity import aws

    class ReadTimeoutError(Exception):
        pass

    assert aws._translate(ReadTimeoutError()).code == "PROVIDER_TIMEOUT"
    assert aws._translate(RuntimeError("x")).code == "PROVIDER_ERROR"
    real_import = builtins.__import__

    def no_boto(name, *args, **kwargs):
        if name in ("boto3", "botocore.config"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_boto)
    provider = AwsProvider(
        ProviderContext("aws", {"account_id": "1", "region": "us-east-1"}, FakeTransport(), None)
    )
    with pytest.raises(ProviderError, match="agentbreeder\\[aws\\]"):
        provider.resolve("refund-agent")
    with pytest.raises(ProviderError, match="reference format"):
        aws_provider(FakeAws()).resolve("bad/name")


def test_aws_sponsor_lookup_error_propagates():
    class Broken(FakeAws):
        def describe_user(self, IdentityStoreId, UserId):
            raise RuntimeError("throttled")

    with pytest.raises(ProviderError) as error:
        aws_provider(Broken()).resolve("refund-agent")
    assert error.value.code == "PROVIDER_ERROR"
