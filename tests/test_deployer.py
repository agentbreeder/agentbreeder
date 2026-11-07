"""P1 (#703): verified deployer via operator-trusted OIDC (CI token and device flow)."""

from __future__ import annotations

import base64
import json
import time

import pytest
from conftest import write_private
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from agentbreeder import cli
from agentbreeder import deployer as module
from agentbreeder.deployer import DeployerError, device_login, resolve_deployer, verify_token
from agentbreeder.operator_config import TrustedIssuer

ISSUER = "https://token.actions.example.com"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwk(key, kid="k1") -> dict:
    numbers = key.public_key().public_numbers()
    size = lambda v: (v.bit_length() + 7) // 8  # noqa: E731
    return {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "n": b64(numbers.n.to_bytes(size(numbers.n), "big")),
        "e": b64(numbers.e.to_bytes(size(numbers.e), "big")),
    }


def token(claims=None, *, key=KEY, header=None) -> str:
    now = int(time.time())
    body = {
        "iss": ISSUER,
        "aud": "agentbreeder",
        "sub": "repo:acme/agent:ref:main",
        "exp": now + 300,
        "iat": now,
    }
    body.update(claims or {})
    head = {"alg": "RS256", "kid": "k1", **(header or {})}
    signing = f"{b64(json.dumps(head).encode())}.{b64(json.dumps(body).encode())}"
    signature = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{b64(signature)}"


@pytest.fixture
def trusted(identity_env):
    write_private(identity_env.directory / "jwks.json", json.dumps({"keys": [jwk(KEY)]}))
    issuer = {"issuer": ISSUER, "audience": "agentbreeder", "jwks_file": "jwks.json"}
    identity_env.config["deployer"] = {"issuers": [issuer]}
    return identity_env


def issuers(env):
    return list(env.load().model.deployer.issuers)


def test_valid_ci_token_is_verified(trusted):
    deployer = verify_token(
        token({"email": "ci@acme.com"}), issuers(trusted), base=trusted.directory
    )
    assert deployer.verified and deployer.subject == "repo:acme/agent:ref:main"
    assert deployer.label == f"oidc:{ISSUER}#repo:acme/agent:ref:main"
    assert deployer.same_person("CI@acme.com")


@pytest.mark.parametrize(
    "make,message",
    [
        (lambda: token(key=OTHER_KEY), "could not be verified"),
        (lambda: token({"iss": "https://evil.example.com"}), "not trusted"),
        (lambda: token({"aud": "other"}), "audience"),
        (lambda: token({"exp": int(time.time()) - 3600}), "expired"),
        (lambda: token({"nbf": int(time.time()) + 3600}), "not yet valid"),
        (lambda: token(header={"alg": "none"}), "RS256"),
        (lambda: token(header={"alg": "HS256"}), "RS256"),
        (lambda: token(header={"kid": "unknown"}), "could not be verified"),
        (lambda: token({"sub": ""}), "subject"),
        (lambda: "not-a-jwt", "could not be verified"),
        (lambda: token()[:-8] + "AAAAAAAA", "could not be verified"),
    ],
)
def test_invalid_tokens_rejected(trusted, make, message):
    with pytest.raises(DeployerError, match=message) as error:
        verify_token(make(), issuers(trusted), base=trusted.directory)
    assert error.value.code == "DEPLOYER_UNVERIFIED"


def test_rsa_verify_rejects_weak_and_malformed_keys():
    assert not module.rsa_verify(3233, 17, b"m", b"\x00")  # tiny modulus
    numbers = KEY.public_key().public_numbers()
    assert not module.rsa_verify(numbers.n, 4, b"m", b"\x00" * 256)  # even exponent
    assert not module.rsa_verify(numbers.n, numbers.e, b"m", b"\x00" * 10)


def test_jwks_uri_fetch(trusted):
    class Transport:
        def request(self, method, url, **kwargs):
            assert url == "https://issuer.example.com/jwks"
            return 200, {"keys": [jwk(KEY)]}

    issuer = TrustedIssuer(
        issuer=ISSUER, audience="agentbreeder", jwks_uri="https://issuer.example.com/jwks"
    )
    assert verify_token(token(), [issuer], transport=Transport()).verified


def test_resolution_order_env_file_cache_os(trusted, tmp_path, monkeypatch):
    operator = trusted.load()
    assert resolve_deployer(operator).verified is False  # no token -> OS user, unverified
    cache = tmp_path / "cache"
    write_private(cache, token())
    assert resolve_deployer(operator, cache=cache).verified
    monkeypatch.setenv(module.TOKEN_ENV, "garbage")
    with pytest.raises(DeployerError):
        resolve_deployer(operator, cache=cache)


def test_git_config_identity_never_used(trusted, monkeypatch):
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("git must not be consulted for identity")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "ceo@acme.com")
    deployer = resolve_deployer(trusted.load())
    assert deployer.verified is False and "ceo@acme.com" not in deployer.label


class DeviceTransport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.forms = []

    def request(self, method, url, *, form=None, **kwargs):
        self.forms.append((url, form))
        if url.endswith("/device"):
            return 200, {
                "device_code": "dev-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://login.example.com/device",
                "interval": 1,
                "expires_in": 60,
            }
        return self.outcomes.pop(0)


@pytest.fixture
def device(trusted):
    trusted.config["deployer"]["issuers"][0].update(
        {
            "device_authorization_endpoint": "https://login.example.com/device",
            "token_endpoint": "https://login.example.com/token",
            "client_id": "agentbreeder-cli",
        }
    )
    return trusted


def test_device_flow_success_caches_privately(device, tmp_path):
    transport = DeviceTransport(
        [
            (400, {"error": "authorization_pending"}),
            (400, {"error": "slow_down"}),
            (200, {"id_token": token()}),
        ]
    )
    printed, slept = [], []
    cache = tmp_path / "cache" / "token"
    deployer = device_login(
        device.load(), transport=transport, output=printed.append, sleep=slept.append, cache=cache
    )
    assert deployer.verified
    assert "ABCD-EFGH" in printed[0]
    assert slept == [1, 1, 6]
    assert cache.stat().st_mode & 0o777 == 0o600
    assert transport.forms[-1][1]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"


@pytest.mark.parametrize(
    "outcome", [(400, {"error": "access_denied"}), (400, {"error": "expired_token"})]
)
def test_device_flow_denied(device, tmp_path, outcome):
    with pytest.raises(DeployerError, match="denied or expired"):
        device_login(
            device.load(),
            transport=DeviceTransport([outcome]),
            output=lambda _: None,
            sleep=lambda _: None,
            cache=tmp_path / "t",
        )


def test_device_flow_requires_configured_issuer(trusted):
    with pytest.raises(DeployerError, match="exactly one"):
        device_login(trusted.load(), transport=DeviceTransport([]))


def test_cli_invalid_token_denial_is_audited(trusted, tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "agent.yaml"
    manifest.write_text(
        "schema_version: 2\nname: x\nimage: sha256:" + "a" * 64 + "\n"
        "identity: {provider: static, ref: agent-1, environment: dev}\n"
    )
    monkeypatch.setenv(module.TOKEN_ENV, token(key=OTHER_KEY))
    state = tmp_path / "state"
    assert cli.main(["deploy", str(manifest), "--local", "--state-dir", str(state)]) == 2
    assert "[DEPLOYER_UNVERIFIED]" in capsys.readouterr().err
    assert cli.main(["audit", "list", "--state-dir", str(state)]) == 0
    assert "DEPLOYER_UNVERIFIED" in capsys.readouterr().out
