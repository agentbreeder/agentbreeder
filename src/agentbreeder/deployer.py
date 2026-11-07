"""Deployer identity: unverified OS user (phase 0) or a verified OIDC token (phase 1).

Git config identities are never accepted. Only RS256 tokens from operator-trusted issuers are
verified; signature verification uses PKCS#1 v1.5 with full encoded-block comparison.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agentbreeder.identity.transport import JsonTransport, Transport, TransportError
from agentbreeder.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    TrustedIssuer,
    read_private,
)

TOKEN_ENV = "AGENTBREEDER_OIDC_TOKEN"
TOKEN_FILE_ENV = "AGENTBREEDER_OIDC_TOKEN_FILE"
CACHE_PATH = Path.home() / ".agentbreeder" / "oidc-token"
CLOCK_SKEW = 60
_DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")


class DeployerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Deployer:
    subject: str
    verified: bool
    issuer: str | None = None
    method: str = "os"
    aliases: tuple[str, ...] = field(default=())
    human: bool = False

    @property
    def label(self) -> str:
        return f"oidc:{self.issuer}#{self.subject}" if self.verified else f"os:{self.subject}"

    def claims(self) -> dict:
        return {
            "subject": self.subject,
            "issuer": self.issuer,
            "aliases": list(self.aliases),
            "human": self.human,
        }

    def same_person(self, *names: str | None) -> bool:
        mine = {self.subject.lower(), *(a.lower() for a in self.aliases)}
        return any(n and n.lower() in mine for n in names)


def os_deployer() -> Deployer:
    return Deployer(getpass.getuser(), False, None, "os")


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def rsa_verify(n: int, e: int, message: bytes, signature: bytes) -> bool:
    if n.bit_length() < 2048 or e < 3 or e % 2 == 0:
        return False
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= n:
        return False
    decoded = pow(value, e, n).to_bytes(size, "big")
    suffix = _DIGEST_INFO_SHA256 + hashlib.sha256(message).digest()
    expected = b"\x00\x01" + b"\xff" * (size - len(suffix) - 3) + b"\x00" + suffix
    return hmac.compare_digest(decoded, expected)


def _jwks(issuer: TrustedIssuer, transport: Transport, base: Path | None) -> list[dict]:
    if issuer.jwks_file:
        path = Path(issuer.jwks_file)
        path = path if path.is_absolute() or base is None else base / path
        try:
            document = json.loads(read_private(path, "JWKS file", 256 * 1024))
        except (OperatorConfigError, ValueError) as error:
            raise DeployerError("DEPLOYER_UNVERIFIED", "Cannot read trusted JWKS") from error
    elif issuer.jwks_uri:
        try:
            status, document = transport.request("GET", issuer.jwks_uri)
        except TransportError as error:
            raise DeployerError("DEPLOYER_UNVERIFIED", "Cannot fetch trusted JWKS") from error
        if status != 200:
            raise DeployerError("DEPLOYER_UNVERIFIED", "Cannot fetch trusted JWKS")
    else:
        raise DeployerError("DEPLOYER_UNVERIFIED", "Trusted issuer has no JWKS configured")
    keys = document.get("keys") if isinstance(document, dict) else None
    if not isinstance(keys, list):
        raise DeployerError("DEPLOYER_UNVERIFIED", "Trusted JWKS is malformed")
    return [k for k in keys if isinstance(k, dict) and k.get("kty") == "RSA"]


def verify_token(
    token: str,
    issuers: list[TrustedIssuer],
    *,
    transport: Transport | None = None,
    base: Path | None = None,
    now: float | None = None,
) -> Deployer:
    unverified = DeployerError("DEPLOYER_UNVERIFIED", "Deployer token could not be verified")
    try:
        header_b64, payload_b64, signature_b64 = token.strip().split(".")
        header = json.loads(_b64(header_b64))
        claims = json.loads(_b64(payload_b64))
        signature = _b64(signature_b64)
    except (ValueError, TypeError) as error:
        raise unverified from error
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise unverified
    if header.get("alg") != "RS256" or header.get("crit"):
        raise DeployerError("DEPLOYER_UNVERIFIED", "Only RS256 deployer tokens are accepted")
    trusted = next((i for i in issuers if i.issuer == claims.get("iss")), None)
    if trusted is None:
        raise DeployerError("DEPLOYER_UNVERIFIED", "Token issuer is not trusted by the operator")
    keys = _jwks(trusted, transport or JsonTransport(), base)
    kid = header.get("kid")
    candidates = [k for k in keys if k.get("kid") == kid] if kid else keys
    if len(candidates) != 1 or candidates[0].get("use", "sig") != "sig":
        raise unverified
    try:
        n = int.from_bytes(_b64(candidates[0]["n"]), "big")
        e = int.from_bytes(_b64(candidates[0]["e"]), "big")
    except (KeyError, ValueError, TypeError) as error:
        raise unverified from error
    if not rsa_verify(n, e, f"{header_b64}.{payload_b64}".encode(), signature):
        raise unverified
    current = time.time() if now is None else now
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if trusted.audience not in audiences:
        raise DeployerError("DEPLOYER_UNVERIFIED", "Token audience is not accepted")
    exp, nbf, iat = claims.get("exp"), claims.get("nbf"), claims.get("iat")
    if not isinstance(exp, (int, float)) or exp + CLOCK_SKEW < current:
        raise DeployerError("DEPLOYER_UNVERIFIED", "Deployer token is expired")
    for moment in (nbf, iat):
        if moment is not None and (
            not isinstance(moment, (int, float)) or moment > current + CLOCK_SKEW
        ):
            raise DeployerError("DEPLOYER_UNVERIFIED", "Deployer token is not yet valid")
    subject = claims.get(trusted.subject_claim)
    if not isinstance(subject, str) or not subject or len(subject) > 256:
        raise DeployerError("DEPLOYER_UNVERIFIED", "Deployer token has no usable subject")
    aliases = tuple(
        str(claims[c])
        for c in ("sub", "email", "preferred_username", "upn", "oid")
        if isinstance(claims.get(c), str) and claims[c] != subject
    )
    return Deployer(subject, True, trusted.issuer, "oidc", aliases, trusted.kind == "human")


def current_token(cache: Path | None = None) -> str | None:
    cache = cache or CACHE_PATH
    if os.environ.get(TOKEN_ENV):
        return os.environ[TOKEN_ENV]
    if os.environ.get(TOKEN_FILE_ENV):
        path = Path(os.environ[TOKEN_FILE_ENV])
    elif cache.exists():
        path = cache
    else:
        return None
    try:
        return read_private(path, "OIDC token file", 64 * 1024).decode().strip()
    except OperatorConfigError as error:
        raise DeployerError("DEPLOYER_UNVERIFIED", str(error)) from error


def resolve_deployer(
    operator: OperatorConfig, *, transport: Transport | None = None, cache: Path | None = None
) -> Deployer:
    """A verified deployer when a token is present; otherwise the unverified OS user."""
    token = current_token(cache)
    if token is None:
        return os_deployer()
    base = operator.path.parent if operator.path else None
    return verify_token(
        token, list(operator.model.deployer.issuers), transport=transport, base=base
    )


def device_login(
    operator: OperatorConfig,
    *,
    issuer: str | None = None,
    transport: Transport | None = None,
    output: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    cache: Path | None = None,
) -> Deployer:
    """RFC 8628 device authorization; caches the verified ID token privately."""
    transport = transport or JsonTransport()
    choices = [
        i
        for i in operator.model.deployer.issuers
        if i.device_authorization_endpoint
        and i.token_endpoint
        and i.client_id
        and (issuer is None or i.issuer == issuer)
    ]
    if len(choices) != 1:
        raise DeployerError("DEPLOYER_UNVERIFIED", "Configure exactly one device-flow issuer")
    trusted = choices[0]
    try:
        status, grant = transport.request(
            "POST",
            trusted.device_authorization_endpoint,
            form={"client_id": trusted.client_id, "scope": "openid profile email"},
        )
    except TransportError as error:
        raise DeployerError("DEPLOYER_UNVERIFIED", "Device authorization failed") from error
    if status != 200 or not isinstance(grant, dict) or "device_code" not in grant:
        raise DeployerError("DEPLOYER_UNVERIFIED", "Device authorization failed")
    output(
        f"Open {grant.get('verification_uri_complete') or grant.get('verification_uri')} "
        f"and enter code {grant.get('user_code')}"
    )
    interval = max(1, int(grant.get("interval", 5)))
    deadline = time.monotonic() + min(int(grant.get("expires_in", 600)), 1800)
    while time.monotonic() < deadline:
        sleep(interval)
        try:
            status, body = transport.request(
                "POST",
                trusted.token_endpoint,
                form={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": grant["device_code"],
                    "client_id": trusted.client_id,
                },
            )
        except TransportError as error:
            raise DeployerError("DEPLOYER_UNVERIFIED", "Token polling failed") from error
        error_code = body.get("error") if isinstance(body, dict) else None
        if status == 200 and isinstance(body, dict) and body.get("id_token"):
            deployer = verify_token(
                body["id_token"],
                [trusted],
                transport=transport,
                base=operator.path.parent if operator.path else None,
            )
            _save(cache or CACHE_PATH, body["id_token"])
            return deployer
        if error_code == "authorization_pending":
            continue
        if error_code == "slow_down":
            interval += 5
            continue
        raise DeployerError("DEPLOYER_UNVERIFIED", "Device login was denied or expired")
    raise DeployerError("DEPLOYER_UNVERIFIED", "Device login timed out")


def _save(cache: Path, token: str) -> None:
    cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(cache, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, token.encode())
    finally:
        os.close(descriptor)
