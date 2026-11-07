"""Fail-closed edge cases for deployer verification, identity adapters and operator config.

Every failure must surface a typed deny/exception and never yield a principal or deployer.
"""

from __future__ import annotations

import os
import stat
import sys
import types
from datetime import date, datetime

import pytest
from conftest import principal_entry, v2_manifest_data, write_private
from test_adapters import ENTRA_ID, FakeAws, FakeTransport, aws_provider
from test_deployer import ISSUER, KEY, b64, jwk, token

from agentbreeder import deployer as module
from agentbreeder import identity
from agentbreeder.deployer import (
    DeployerError,
    current_token,
    device_login,
    rsa_verify,
    verify_token,
)
from agentbreeder.deployment import DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.identity import (
    AgentPrincipal,
    ProviderError,
    _common,
    aws,
    load_provider,
    principal_key,
)
from agentbreeder.identity.transport import TransportError
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import (
    MAX_CONFIG_BYTES,
    OperatorConfigError,
    TrustedIssuer,
    check_private_file,
    load_operator_config,
    parse_yaml,
)

# --------------------------------------------------------------------------- deployer


def issuer(**extra) -> TrustedIssuer:
    return TrustedIssuer(issuer=ISSUER, audience="agentbreeder", **extra)


class JwksTransport:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def assert_unverified(error, match: str) -> None:
    assert error.value.code == "DEPLOYER_UNVERIFIED"
    assert match in str(error.value)


def test_rsa_verify_rejects_signature_not_below_modulus():
    n = KEY.public_key().public_numbers().n
    e = KEY.public_key().public_numbers().e
    size = (n.bit_length() + 7) // 8
    assert rsa_verify(n, e, b"m", n.to_bytes(size, "big")) is False
    assert rsa_verify(n, e, b"m", b"\xff" * size) is False


@pytest.mark.parametrize(
    "contents,mode",
    [('{"keys": []}', 0o644), ("not json", 0o600)],
    ids=["world-readable", "invalid-json"],
)
def test_jwks_file_unreadable_or_invalid_fails_closed(tmp_path, contents, mode):
    path = tmp_path / "jwks.json"
    path.write_text(contents)
    path.chmod(mode)
    with pytest.raises(DeployerError) as error:
        verify_token(token(), [issuer(jwks_file="jwks.json")], base=tmp_path)
    assert_unverified(error, "Cannot read trusted JWKS")


def test_jwks_file_symlink_rejected(tmp_path):
    real = write_private(tmp_path / "real.json", '{"keys": []}')
    (tmp_path / "jwks.json").symlink_to(real)
    with pytest.raises(DeployerError) as error:
        verify_token(token(), [issuer(jwks_file=str(tmp_path / "jwks.json"))])
    assert_unverified(error, "Cannot read trusted JWKS")


@pytest.mark.parametrize(
    "outcome",
    [TransportError("PROVIDER_TIMEOUT", "timed out"), (500, {"keys": [jwk(KEY)]})],
    ids=["transport-error", "http-500"],
)
def test_jwks_uri_fetch_failure_fails_closed(outcome):
    transport = JwksTransport(outcome)
    with pytest.raises(DeployerError) as error:
        verify_token(
            token(), [issuer(jwks_uri="https://issuer.example.com/jwks")], transport=transport
        )
    assert_unverified(error, "Cannot fetch trusted JWKS")
    assert transport.calls == [("GET", "https://issuer.example.com/jwks")]


def test_issuer_without_jwks_fails_closed():
    with pytest.raises(DeployerError) as error:
        verify_token(token(), [issuer()], transport=JwksTransport(AssertionError("no fetch")))
    assert_unverified(error, "no JWKS configured")


@pytest.mark.parametrize("document", [{"keys": "nope"}, [jwk(KEY)], {"other": []}])
def test_malformed_jwks_document_fails_closed(document):
    with pytest.raises(DeployerError) as error:
        verify_token(
            token(),
            [issuer(jwks_uri="https://issuer.example.com/jwks")],
            transport=JwksTransport((200, document)),
        )
    assert_unverified(error, "malformed")


@pytest.mark.parametrize(
    "raw",
    [f"{b64(b'[]')}.{b64(b'{}')}.AA", f"{b64(b'{}')}.{b64(b'[1]')}.AA"],
    ids=["list-header", "list-claims"],
)
def test_non_object_header_or_claims_rejected(raw):
    with pytest.raises(DeployerError) as error:
        verify_token(raw, [issuer(jwks_uri="https://issuer.example.com/jwks")])
    assert_unverified(error, "could not be verified")


@pytest.mark.parametrize(
    "key",
    [
        {k: v for k, v in jwk(KEY).items() if k != "n"},
        {**jwk(KEY), "e": 12345},
    ],
    ids=["missing-modulus", "non-string-exponent"],
)
def test_jwk_with_unusable_numbers_rejected(key):
    with pytest.raises(DeployerError) as error:
        verify_token(
            token(),
            [issuer(jwks_uri="https://issuer.example.com/jwks")],
            transport=JwksTransport((200, {"keys": [key]})),
        )
    assert_unverified(error, "could not be verified")


def test_token_file_env_is_read_privately_and_beats_cache(tmp_path, monkeypatch):
    cache = write_private(tmp_path / "cache", "cached-token")
    path = write_private(tmp_path / "ci-token", "  file-token\n")
    monkeypatch.setenv(module.TOKEN_FILE_ENV, str(path))
    assert current_token(cache) == "file-token"
    path.chmod(0o644)
    with pytest.raises(DeployerError) as error:
        current_token(cache)
    assert error.value.code == "DEPLOYER_UNVERIFIED"
    assert "file-token" not in str(error.value)


def test_cache_symlink_is_rejected(tmp_path):
    real = write_private(tmp_path / "real", "cached-token")
    cache = tmp_path / "cache"
    cache.symlink_to(real)
    with pytest.raises(DeployerError) as error:
        current_token(cache)
    assert error.value.code == "DEPLOYER_UNVERIFIED"


class ScriptedDevice:
    """Device-flow transport: first answer is the device grant, the rest are polls."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.urls = []

    def request(self, method, url, **kwargs):
        self.urls.append(url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


GRANT = (200, {"device_code": "d", "user_code": "U", "interval": 1, "expires_in": 60})


@pytest.fixture
def device_operator(identity_env):
    write_private(identity_env.directory / "jwks.json", '{"keys": []}')
    identity_env.config["deployer"] = {
        "issuers": [
            {
                "issuer": ISSUER,
                "audience": "agentbreeder",
                "jwks_file": "jwks.json",
                "device_authorization_endpoint": "https://login.example.com/device",
                "token_endpoint": "https://login.example.com/token",
                "client_id": "agentbreeder-cli",
            }
        ]
    }
    return identity_env.load()


def run_device(operator, transport, cache):
    return device_login(
        operator, transport=transport, output=lambda _: None, sleep=lambda _: None, cache=cache
    )


@pytest.mark.parametrize(
    "grant",
    [
        TransportError("PROVIDER_ERROR", "unreachable"),
        (500, {"device_code": "d"}),
        (200, {"user_code": "U"}),
        (200, ["device_code"]),
    ],
    ids=["transport-error", "http-500", "no-device-code", "non-object"],
)
def test_device_authorization_failure_fails_closed(device_operator, tmp_path, grant):
    transport = ScriptedDevice(grant)
    cache = tmp_path / "token"
    with pytest.raises(DeployerError) as error:
        run_device(device_operator, transport, cache)
    assert_unverified(error, "Device authorization failed")
    assert transport.urls == ["https://login.example.com/device"]  # never polled
    assert not cache.exists()


def test_device_token_polling_transport_error_fails_closed(device_operator, tmp_path):
    cache = tmp_path / "token"
    with pytest.raises(DeployerError) as error:
        run_device(
            device_operator,
            ScriptedDevice(GRANT, TransportError("PROVIDER_TIMEOUT", "timed out")),
            cache,
        )
    assert_unverified(error, "Token polling failed")
    assert not cache.exists()


def test_device_login_times_out_without_caching(device_operator, tmp_path):
    expired = (200, {"device_code": "d", "user_code": "U", "expires_in": 0})
    transport = ScriptedDevice(expired)
    cache = tmp_path / "token"
    with pytest.raises(DeployerError) as error:
        run_device(device_operator, transport, cache)
    assert_unverified(error, "timed out")
    assert transport.urls == ["https://login.example.com/device"]
    assert not cache.exists()


def test_device_login_unverifiable_id_token_is_not_cached(device_operator, tmp_path):
    cache = tmp_path / "token"
    with pytest.raises(DeployerError) as error:
        run_device(device_operator, ScriptedDevice(GRANT, (200, {"id_token": token()})), cache)
    assert error.value.code == "DEPLOYER_UNVERIFIED"  # JWKS has no matching key
    assert not cache.exists()


# --------------------------------------------------------------------------- _common


class PagedTransport:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        return 200, self.pages(url)


@pytest.mark.parametrize("body", [[], {"value": {}}, {"items": []}, None])
def test_paged_rejects_unexpected_shape(body):
    with pytest.raises(ProviderError) as error:
        list(_common.paged(PagedTransport(lambda url: body), "https://idp.example.com/x", {}))
    assert error.value.code == "PROVIDER_ERROR"
    assert "response shape" in str(error.value)


def test_paged_is_bounded_on_endless_same_origin_links():
    transport = PagedTransport(
        lambda url: {"value": [1], "@odata.nextLink": "https://idp.example.com/next"}
    )
    with pytest.raises(ProviderError) as error:
        list(_common.paged(transport, "https://idp.example.com/x", {}))
    assert error.value.code == "PROVIDER_ERROR" and "paging limit" in str(error.value)
    assert transport.calls == _common.MAX_PAGES


def test_parse_date_accepts_dates_and_fails_closed_on_other_types():
    assert _common.parse_date(datetime(2099, 1, 2, 3, 4)) == date(2099, 1, 2)
    assert _common.parse_date(date(2099, 1, 2)) == date(2099, 1, 2)
    assert _common.parse_date("") is None and _common.parse_date(None) is None
    for value in (20990101, 1.5, ["2099-01-01"], {"date": "2099-01-01"}, True):
        with pytest.raises(ProviderError) as error:
            _common.parse_date(value)
        assert error.value.code == "PROVIDER_ERROR"


@pytest.mark.parametrize(
    "settings",
    [
        {"type": "keycloak", "base_url": "https://kc.example.com", "realm": "acme"},
        {"type": "entra", "tenant_id": "t", "client_id": "", "credential": "env:X"},
        {"type": "static", "authority": None, "file": "p.yaml"},
    ],
)
def test_missing_provider_setting_fails_closed(settings):
    with pytest.raises(ProviderError) as error:
        load_provider("conn", {"conn": settings}, transport=FakeTransport())
    assert error.value.code == "PROVIDER_ERROR"
    assert "is required" in str(error.value)


# --------------------------------------------------------------------------- __init__


def test_principal_key_matches_principal_key_property():
    principal = AgentPrincipal("conn", "tenant-a", "id-1", "agent", True)
    expected = '["conn","tenant-a","id-1"]'
    assert principal_key("conn", "tenant-a", "id-1") == principal.key == expected
    assert principal_key("conn", "tenant-b", "id-1") != principal.key


def test_plugin_init_failure_is_wrapped_without_leaking_detail(monkeypatch):
    class Plugin:
        def __init__(self, context):
            raise RuntimeError("password=hunter2")

    class Entry:
        name = "corp"

        def load(self):
            return Plugin

    monkeypatch.setattr(identity, "entry_points", lambda group: [Entry()])
    with pytest.raises(ProviderError) as error:
        load_provider("mine", {"mine": {"type": "corp"}})
    assert error.value.code == "PROVIDER_ERROR"
    assert "hunter2" not in str(error.value) and "mine" in str(error.value)


def test_unconfigured_connection_is_unknown():
    with pytest.raises(ProviderError) as error:
        load_provider("missing", {})
    assert error.value.code == "PROVIDER_UNKNOWN"


# --------------------------------------------------------------------------- keycloak

KC_ADMIN = "https://kc.example.com/admin/realms/acme"
KC_TOKEN = "POST https://kc.example.com/realms/acme/protocol/openid-connect/token"


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("IDP_SECRET", "client-secret")


def keycloak(transport):
    settings = {
        "type": "keycloak",
        "base_url": "https://kc.example.com",
        "realm": "acme",
        "client_id": "agentbreeder-reader",
        "credential": "env:IDP_SECRET",
    }
    return load_provider("kc", {"kc": settings}, transport=transport)


def resolve_error(provider, ref) -> ProviderError:
    with pytest.raises(ProviderError) as error:
        provider.resolve(ref)
    return error.value


@pytest.mark.parametrize("body", [{"token_type": "Bearer"}, {"access_token": ""}, ["kc-token"]])
def test_keycloak_token_failure_stops_before_admin_reads(secret, body):
    transport = FakeTransport("keycloak")
    transport.routes[KC_TOKEN] = [200, body]
    error = resolve_error(keycloak(transport), "client-uuid-1")
    assert error.code == "PROVIDER_ERROR" and "token request failed" in str(error)
    assert [c[0] for c in transport.calls] == ["POST"]


def test_keycloak_unset_credential_fails_closed(monkeypatch):
    monkeypatch.delenv("IDP_SECRET", raising=False)
    transport = FakeTransport("keycloak")
    with pytest.raises(OperatorConfigError, match="unset"):
        keycloak(transport).resolve("client-uuid-1")
    assert transport.calls == []


def test_keycloak_different_client_is_ambiguous(secret):
    transport = FakeTransport("keycloak")
    transport.routes[f"GET {KC_ADMIN}/clients/client-uuid-1"][1]["id"] = "client-uuid-2"
    assert resolve_error(keycloak(transport), "client-uuid-1").code == "PRINCIPAL_AMBIGUOUS"


@pytest.mark.parametrize("body", [{"username": "sa"}, {"id": ""}, ["sa-user-1"]])
def test_keycloak_unexpected_service_account_fails_closed(secret, body):
    transport = FakeTransport("keycloak")
    transport.routes[f"GET {KC_ADMIN}/clients/client-uuid-1/service-account-user"] = [200, body]
    error = resolve_error(keycloak(transport), "client-uuid-1")
    assert error.code == "PROVIDER_ERROR" and "service account" in str(error)


def test_keycloak_non_list_sponsor_attribute_yields_no_sponsors(secret):
    transport = FakeTransport("keycloak")
    user = transport.routes[f"GET {KC_ADMIN}/clients/client-uuid-1/service-account-user"][1]
    user["attributes"]["agentbreeder.sponsors"] = "alice-user"
    principal = keycloak(transport).resolve("client-uuid-1")
    assert principal.sponsors == ()  # gate then denies SPONSOR_MISSING
    assert not any(c[1].endswith("/users/alice-user") for c in transport.calls)


@pytest.mark.parametrize(
    "failure,code",
    [
        ([500, None], "PROVIDER_ERROR"),
        ([403, None], "PROVIDER_ERROR"),
        (TransportError("PROVIDER_TIMEOUT", "timed out"), "PROVIDER_TIMEOUT"),
    ],
)
def test_keycloak_sponsor_lookup_failure_propagates(secret, failure, code):
    transport = FakeTransport("keycloak")
    transport.routes[f"GET {KC_ADMIN}/users/alice-user"] = failure
    assert resolve_error(keycloak(transport), "client-uuid-1").code == code


@pytest.mark.parametrize("body", [["alice"], "alice", None], ids=["list", "string", "null"])
def test_keycloak_non_object_sponsor_fails_closed(secret, body):
    # Regression: a non-dict sponsor body raised AttributeError instead of a typed error.
    transport = FakeTransport("keycloak")
    transport.routes[f"GET {KC_ADMIN}/users/alice-user"] = [200, body]
    error = resolve_error(keycloak(transport), "client-uuid-1")
    assert error.code == "PROVIDER_ERROR" and "sponsor shape" in str(error)


@pytest.mark.parametrize(
    "mappings",
    [
        {"realmMappings": [{"name": "deployer"}]},
        {"clientMappings": {"payments-api": {"mappings": [{"id": "r"}]}}},
        {"clientMappings": ["payments-api"]},
        {"realmMappings": ["deployer"]},
    ],
    ids=["realm-no-id", "client-no-id", "client-list", "realm-strings"],
)
def test_keycloak_unexpected_role_shape_fails_closed(secret, mappings):
    transport = FakeTransport("keycloak")
    transport.routes[f"GET {KC_ADMIN}/users/sa-user-1/role-mappings"] = [200, mappings]
    error = resolve_error(keycloak(transport), "client-uuid-1")
    assert error.code == "PROVIDER_ERROR" and "grant shape" in str(error)


def test_keycloak_group_without_id_fails_closed(secret):
    transport = FakeTransport("keycloak")
    key = next(k for k in transport.routes if "/groups?" in k)
    transport.routes[key] = [200, [{"name": "finance", "path": "/finance"}]]
    assert resolve_error(keycloak(transport), "client-uuid-1").code == "PROVIDER_ERROR"


# --------------------------------------------------------------------------- okta

OKTA_APP = "GET https://acme.okta.com/api/v1/apps/0oa-agent-1"
OKTA_ROLES = "GET https://acme.okta.com/oauth2/v1/clients/client-abc/roles"


def okta(transport):
    settings = {
        "type": "okta",
        "org_url": "https://acme.okta.com",
        "token": "env:IDP_SECRET",
        "experimental": True,
    }
    return load_provider("okta", {"okta": settings}, transport=transport)


def test_okta_different_application_is_ambiguous(secret):
    transport = FakeTransport("okta")
    transport.routes[OKTA_APP][1]["id"] = "0oa-other"
    assert resolve_error(okta(transport), "0oa-agent-1").code == "PRINCIPAL_AMBIGUOUS"
    transport.routes[OKTA_APP] = [200, ["0oa-agent-1"]]
    assert resolve_error(okta(transport), "0oa-agent-1").code == "PRINCIPAL_AMBIGUOUS"


def test_okta_app_without_client_id_reports_no_roles(secret):
    transport = FakeTransport("okta")
    del transport.routes[OKTA_APP][1]["credentials"]
    principal = okta(transport).resolve("0oa-agent-1")
    assert principal.roles == frozenset()  # required roles then deny ROLE_NOT_GRANTED
    assert not any("/oauth2/" in c[1] for c in transport.calls)


def test_okta_non_service_app_is_unknown_kind(secret):
    transport = FakeTransport("okta")
    transport.routes[OKTA_APP][1]["settings"] = "not-a-dict"
    assert okta(transport).resolve("0oa-agent-1").kind == "unknown"


def test_okta_missing_sponsor_is_inactive(secret):
    transport = FakeTransport("okta")
    transport.routes[OKTA_APP][1]["profile"]["agentbreederSponsors"] = ["00u-alice", "00u-gone"]
    principal = okta(transport).resolve("0oa-agent-1")
    assert [(p.id, p.active) for p in principal.sponsors] == [
        ("00u-alice", True),
        ("00u-gone", False),
    ]


def test_okta_suspended_sponsor_is_inactive(secret):
    transport = FakeTransport("okta")
    transport.routes["GET https://acme.okta.com/api/v1/users/00u-alice"][1]["status"] = "SUSPENDED"
    assert [p.active for p in okta(transport).resolve("0oa-agent-1").sponsors] == [False]


@pytest.mark.parametrize(
    "failure,code",
    [
        ([500, None], "PROVIDER_ERROR"),
        ([401, None], "PROVIDER_ERROR"),
        (TransportError("PROVIDER_TIMEOUT", "timed out"), "PROVIDER_TIMEOUT"),
    ],
)
def test_okta_sponsor_lookup_failure_propagates(secret, failure, code):
    transport = FakeTransport("okta")
    transport.routes["GET https://acme.okta.com/api/v1/users/00u-alice"] = failure
    assert resolve_error(okta(transport), "0oa-agent-1").code == code


@pytest.mark.parametrize("body", [["00u-alice"], "x", None], ids=["list", "string", "null"])
def test_okta_non_object_sponsor_fails_closed(secret, body):
    # Regression: a non-dict sponsor body raised AttributeError instead of a typed error.
    transport = FakeTransport("okta")
    transport.routes["GET https://acme.okta.com/api/v1/users/00u-alice"] = [200, body]
    error = resolve_error(okta(transport), "0oa-agent-1")
    assert error.code == "PROVIDER_ERROR" and "sponsor shape" in str(error)


def test_okta_non_object_sponsor_profile_has_no_login(secret):
    transport = FakeTransport("okta")
    transport.routes["GET https://acme.okta.com/api/v1/users/00u-alice"][1]["profile"] = "x"
    sponsors = okta(transport).resolve("0oa-agent-1").sponsors
    assert sponsors and sponsors[0].name is None


@pytest.mark.parametrize(
    "roles",
    [[{"status": "ACTIVE", "label": "x"}], ["SUPER_ADMIN"], [None]],
    ids=["no-role-or-type", "string-entry", "null-entry"],
)
def test_okta_unexpected_role_shape_fails_closed(secret, roles):
    transport = FakeTransport("okta")
    transport.routes[OKTA_ROLES] = [200, roles]
    error = resolve_error(okta(transport), "0oa-agent-1")
    assert error.code == "PROVIDER_ERROR" and "grant shape" in str(error)


# --------------------------------------------------------------------------- entra

ENTRA_TOKEN = "POST https://login.microsoftonline.com/tenant-a/oauth2/v2.0/token"
ENTRA_BASE = f"https://graph.microsoft.com/v1.0/servicePrincipals/{ENTRA_ID}"
ENTRA_SELECT = "$select=id,accountEnabled,servicePrincipalType,customSecurityAttributes"
ENTRA_AGENT = f"GET {ENTRA_BASE}/microsoft.graph.agentIdentity?{ENTRA_SELECT}"


def entra(transport):
    settings = {
        "type": "entra",
        "tenant_id": "tenant-a",
        "client_id": "app",
        "credential": "env:IDP_SECRET",
    }
    return load_provider("entra", {"entra": settings}, transport=transport)


@pytest.mark.parametrize("body", [{"token_type": "Bearer"}, {"access_token": 7}, None])
def test_entra_token_failure_stops_before_graph_reads(secret, body):
    transport = FakeTransport("entra")
    transport.routes[ENTRA_TOKEN] = [200, body]
    error = resolve_error(entra(transport), ENTRA_ID)
    assert error.code == "PROVIDER_ERROR" and "token request failed" in str(error)
    assert [c[0] for c in transport.calls] == ["POST"]


@pytest.mark.parametrize(
    "failure,code",
    [
        ([403, {"error": "Insufficient privileges"}], "PROVIDER_ERROR"),
        (TransportError("PROVIDER_TIMEOUT", "timed out"), "PROVIDER_TIMEOUT"),
    ],
)
def test_entra_typed_lookup_failure_does_not_fall_back(secret, failure, code):
    transport = FakeTransport("entra")
    transport.routes[ENTRA_AGENT] = failure
    assert resolve_error(entra(transport), ENTRA_ID).code == code
    assert not any(c[1] == f"{ENTRA_BASE}?{ENTRA_SELECT}" for c in transport.calls)


@pytest.mark.parametrize(
    "assignment",
    [{"principalId": ENTRA_ID, "appRoleId": "r"}, {"principalId": ENTRA_ID, "resourceId": "s"}],
    ids=["no-resource", "no-role"],
)
def test_entra_unexpected_assignment_shape_fails_closed(secret, assignment):
    transport = FakeTransport("entra")
    transport.routes[f"GET {ENTRA_BASE}/appRoleAssignments"] = [200, {"value": [assignment]}]
    error = resolve_error(entra(transport), ENTRA_ID)
    assert error.code == "PROVIDER_ERROR" and "grant shape" in str(error)


def test_entra_group_without_id_fails_closed(secret):
    transport = FakeTransport("entra")
    key = next(k for k in transport.routes if "transitiveMemberOf" in k)
    transport.routes[key] = [200, {"value": [{"displayName": "Finance"}]}]
    assert resolve_error(entra(transport), ENTRA_ID).code == "PROVIDER_ERROR"


def test_entra_other_principal_assignments_are_not_granted(secret):
    transport = FakeTransport("entra")
    transport.routes[f"GET {ENTRA_BASE}/appRoleAssignments"] = [
        200,
        {"value": [{"principalId": "someone-else", "resourceId": "s", "appRoleId": "r"}]},
    ]
    assert entra(transport).resolve(ENTRA_ID).roles == frozenset()


def test_entra_non_object_attribute_sets_are_ignored(secret):
    transport = FakeTransport("entra")
    transport.routes[ENTRA_AGENT][1]["customSecurityAttributes"] = {
        "AgentBreeder": "environment=prod",
        "acmeGovernance": {"risk_tier": "low", "@odata.type": "#x"},
    }
    principal = entra(transport).resolve(ENTRA_ID)
    assert dict(principal.attributes) == {"acmeGovernance/risk_tier": "low"}
    assert principal.environment is None and principal.review_by is None


# --------------------------------------------------------------------------- aws


def test_boto3_clients_use_bounded_config(monkeypatch):
    created = []

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_boto3 = types.SimpleNamespace(
        client=lambda service, **kwargs: created.append((service, kwargs)) or "client"
    )
    fake_config = types.ModuleType("botocore.config")
    fake_config.Config = Config
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", types.ModuleType("botocore"))
    monkeypatch.setitem(sys.modules, "botocore.config", fake_config)
    factory = aws._boto3_clients("eu-west-1")
    assert factory("identitystore") == "client"
    service, kwargs = created[0]
    assert service == "identitystore" and kwargs["region_name"] == "eu-west-1"
    assert kwargs["config"].kwargs == {
        "connect_timeout": 5,
        "read_timeout": 10,
        "retries": {"max_attempts": 1},
    }


def test_aws_provider_error_from_client_passes_through_unchanged():
    original = ProviderError("PROVIDER_TIMEOUT", "client-side timeout")

    class Raising(FakeAws):
        def get_workload_identity(self, name):
            raise original

    with pytest.raises(ProviderError) as error:
        aws_provider(Raising()).resolve("refund-agent")
    assert error.value is original


def test_aws_tag_lookup_failure_fails_closed():
    class NoTags(FakeAws):
        def list_tags_for_resource(self, resourceArn):
            raise RuntimeError("AccessDenied: arn:aws:iam::111122223333:role/x")

    error = resolve_error(aws_provider(NoTags()), "refund-agent")
    assert error.code == "PROVIDER_ERROR" and "111122223333" not in str(error)


# --------------------------------------------------------------------------- static (via gate)


def gate_deploy(identity_env, state, runtime):
    service = DeploymentService(state, runtime, identity_env.load())
    return service.deploy(Manifest.model_validate(v2_manifest_data()), local=True)


@pytest.mark.parametrize(
    "entry,code",
    [
        ("agent-1 is an agent", "PROVIDER_ERROR"),
        (principal_entry(attributes={"acme/risk_tier": 3}), "PROVIDER_ERROR"),
        (principal_entry(attributes={7: "x"}), "PROVIDER_ERROR"),
        (principal_entry(kind="robot"), "PRINCIPAL_KIND_REJECTED"),
    ],
    ids=["non-mapping-entry", "non-string-value", "non-string-key", "unknown-kind"],
)
def test_static_malformed_entries_deny_without_docker(identity_env, state, runtime, entry, code):
    identity_env.principals["agent-1"] = entry
    with pytest.raises(GateDenied) as error:
        gate_deploy(identity_env, state, runtime)
    assert error.value.code == code
    assert runtime.mock_calls == []
    assert state.active("sample") is None


def test_static_unknown_kind_maps_to_unknown(identity_env):
    identity_env.principals["agent-1"] = principal_entry(kind="robot")
    provider = load_provider(
        "static", identity_env.load().model.providers, base=identity_env.directory
    )
    assert provider.resolve("agent-1").kind == "unknown"


# --------------------------------------------------------------------------- operator config


def assert_config_rejected(identity_env, field: str, leaked: str | None = None) -> None:
    with pytest.raises(OperatorConfigError) as error:
        identity_env.load()
    assert field in str(error.value)
    if leaked:
        assert leaked not in str(error.value)


def test_invalid_restriction_bundle_id_rejected(identity_env):
    identity_env.config["pdp"] = {
        "engine": "cedar",
        "baseline": {"source": "baseline.cedar", "digest": "sha256:" + "0" * 64},
        "approved_restrictions": {"Bad Bundle!": {"source": "x.cedar"}},
    }
    assert_config_rejected(identity_env, "pdp", "Bad Bundle!")


@pytest.mark.parametrize(
    "destinations",
    [
        {"Bad Name": {"url": "https://mcp.example.com/mcp"}},
        {
            "one": {"url": "https://mcp.example.com/mcp"},
            "two": {"url": "https://mcp.example.com/mcp"},
        },
    ],
    ids=["bad-name", "duplicate-url"],
)
def test_invalid_runtime_destinations_rejected(identity_env, destinations):
    identity_env.config["runtime_enforcement"] = {
        "mode": "mcp_http",
        "destinations": destinations,
    }
    assert_config_rejected(identity_env, "runtime_enforcement", "mcp.example.com")


def test_invalid_provider_connection_name_rejected(identity_env):
    identity_env.config["providers"]["Static Prod"] = {"type": "static"}
    assert_config_rejected(identity_env, "providers", "Static Prod")


def test_oversized_operator_config_rejected(identity_env):
    write_private(identity_env.path, "#" * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(OperatorConfigError, match="too large"):
        load_operator_config(identity_env.path)


@pytest.mark.parametrize(
    "data,message",
    [
        (b"base: &b {enforcement: audit}\nother: *b\n", "anchors or aliases"),
        (b"enforcement: \xff\xfe\n", "not valid UTF-8 YAML"),
        (b"enforcement: [audit\n", "not valid UTF-8 YAML"),
        (b"enforcement: enforce\nenforcement: audit\n", "not valid UTF-8 YAML"),
    ],
    ids=["anchor", "bad-utf8", "bad-yaml", "duplicate-key"],
)
def test_unsafe_or_invalid_yaml_rejected(identity_env, data, message):
    with pytest.raises(OperatorConfigError, match=message):
        parse_yaml(data, "Operator config")
    identity_env.path.write_bytes(data)
    with pytest.raises(OperatorConfigError, match=message):
        load_operator_config(identity_env.path)


class FakePath:
    def __init__(self, mode: int, uid: int):
        self.result = os.stat_result((mode, 0, 0, 1, uid, 0, 0, 0, 0, 0))

    def lstat(self):
        return self.result


@pytest.mark.parametrize(
    "mode,uid,allowed",
    [
        (stat.S_IFREG | 0o644, 0, True),  # root-owned, not group/world writable
        (stat.S_IFREG | 0o664, 0, False),  # root-owned but group writable
        (stat.S_IFREG | 0o646, 0, False),  # root-owned but world writable
        (stat.S_IFREG | 0o600, os.getuid() + 1, False),  # someone else's private file
        (stat.S_IFREG | 0o640, os.getuid(), False),  # mine but group readable
        (stat.S_IFLNK | 0o600, os.getuid(), False),  # symlink
    ],
)
def test_private_file_ownership_rules(mode, uid, allowed):
    path = FakePath(mode, uid)
    if allowed:
        assert check_private_file(path, "Operator config") is None
    else:
        with pytest.raises(OperatorConfigError):
            check_private_file(path, "Operator config")
