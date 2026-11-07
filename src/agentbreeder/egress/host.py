"""Host-side decision service for one boundary-enforced deployment.

The sidecar asks this service before forwarding anything. It re-checks the operator config pin,
the deployment revision and the kill switch on every request, evaluates `call_tool` with the same
PolicyEngine (baseline AND restrictions), persists held calls and writes the audit row before it
answers. It also acts as the credential broker: long-lived tool credentials are resolved here and
only a per-call `Authorization` value is returned to the sidecar, never to the agent.

Run as `python -m agentbreeder.egress.host --state-dir DIR --deployment ID` (started by deploy).
The service is single-threaded on purpose: decisions are serialized through one SQLite handle.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socketserver
import sqlite3
import sys
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from agentbreeder.audit import AuditError, AuditEvent
from agentbreeder.egress.protocol import (
    MAC_HEADER,
    METHODS,
    NONCE_HEADER,
    canonical,
    response_mac,
    token_matches,
    valid_nonce,
)
from agentbreeder.identity.transport import JsonTransport, TransportError
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import (
    Destination,
    OperatorConfigError,
    load_operator_config,
    resolve_credential,
)
from agentbreeder.policy import PolicyEngine, PolicyError, PolicyRequest
from agentbreeder.store import DeploymentError, Store

ACTIVE = ("starting", "ready")
RETRY_AFTER = 30
GRANT_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
TOKEN_TYPE_ACCESS = "urn:ietf:params:oauth:token-type:" + "access" + "_token"


class Denied(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Unavailable(Exception):
    """Audit persistence failed: the sidecar must not forward (HTTP 503)."""


def egress_dir(state_dir: Path, deployment_id: str) -> Path:
    return state_dir / "egress" / deployment_id


def binding_digest(**fields) -> str:
    return "sha256:" + hashlib.sha256(canonical(fields).encode()).hexdigest()


class Broker:
    """Resolve a destination credential on the host; optionally exchange it (RFC 8693)."""

    def __init__(self, transport=None):
        self.transport = transport or JsonTransport()

    def authorization(self, destination: Destination, base: Path | None) -> str | None:
        try:
            subject = resolve_credential(destination.credential, base)
            exchange = destination.token_exchange
            if not exchange or subject is None:
                return f"Bearer {subject}" if subject else None
            headers = {}
            if exchange.client_id and exchange.client_secret:
                client = resolve_credential(exchange.client_secret, base)
                pair = f"{exchange.client_id}:{client}".encode()
                headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
            form = {
                "grant_type": GRANT_TOKEN_EXCHANGE,
                "subject_token": subject,
                "subject_token_type": TOKEN_TYPE_ACCESS,
                "audience": exchange.audience,
            }
            if exchange.scope:
                form["scope"] = exchange.scope
            status, body = self.transport.request(
                "POST", exchange.token_endpoint, headers=headers, form=form
            )
        except (OperatorConfigError, TransportError) as error:
            raise Denied("CREDENTIAL_UNAVAILABLE", "Tool credential unavailable") from error
        body = body if isinstance(body, dict) else {}
        issued = body.get("access" + "_token")
        kind = str(body.get("token_type", "Bearer")).lower()
        if status != 200 or not isinstance(issued, str) or not issued or kind != "bearer":
            raise Denied("CREDENTIAL_UNAVAILABLE", "Token exchange failed")
        return f"Bearer {issued}"


class DecisionService:
    def __init__(
        self,
        store: Store,
        pin: dict,
        broker: Broker | None = None,
        clock=None,
        pin_path: Path | None = None,
    ):
        self.store = store
        self.pin = pin
        self.pin_path = pin_path
        self.deployment_id = pin["deployment"]
        self.broker = broker or Broker()
        self.clock = clock or (lambda: datetime.now(UTC))

    def _serving(self) -> bool:
        """The serving latch is set only after boundary verification passes (and cleared before
        any restore), so no call is forwarded through an unverified boundary."""
        if self.pin_path is None:
            return self.pin.get("serving") is True
        try:
            return json.loads(self.pin_path.read_text()).get("serving") is True
        except (OSError, ValueError):
            return False

    # -- checks shared by every request ------------------------------------------------------

    def _operator(self):
        try:
            operator = load_operator_config(Path(self.pin["operator_path"]))
        except OperatorConfigError as error:
            raise Denied("RUNTIME_CONFIG_CHANGED", "Operator config is unreadable") from error
        if operator.digest != self.pin["operator_digest"]:
            raise Denied(
                "RUNTIME_CONFIG_CHANGED", "Operator config changed since deploy; redeploy"
            )
        if operator.model.runtime_enforcement.mode != "mcp_http" or operator.model.pdp is None:
            raise Denied("RUNTIME_CONFIG_CHANGED", "Runtime enforcement is no longer configured")
        return operator

    def _record(self) -> dict:
        try:
            record = self.store.get(self.deployment_id)
        except DeploymentError as error:
            raise Denied("DEPLOYMENT_INACTIVE", "Deployment not found") from error
        if record["state"] not in ACTIVE:
            raise Denied("DEPLOYMENT_INACTIVE", "Deployment is not active")
        if not record.get("revision") or record["revision"] != self.pin["revision"]:
            raise Denied("DEPLOYMENT_REVISION_CHANGED", "Deployment revision changed")
        if record.get("principal_key") and self.store.blocker(
            record["principal_key"], record["environment"]
        ):
            raise Denied("PRINCIPAL_BLOCKED", "Principal is administratively disabled")
        return record

    def active(self) -> bool:
        try:
            return self.store.get(self.deployment_id)["state"] in ACTIVE
        except DeploymentError:
            return False
        except sqlite3.Error:
            # Transient (e.g. "database is locked"): keep serving; decisions re-read the store
            # and fail closed on their own. Exiting would leave the deployment at 503.
            return True

    def health(self) -> dict:
        operator = self._operator()
        record = self._record()
        return {
            "deployment": self.deployment_id,
            "operator_config_digest": operator.digest,
            "revision": record["revision"],
        }

    # -- decisions -------------------------------------------------------------------------

    def decide(self, request: dict) -> dict:
        method, server, tool = request.get("method"), request.get("server"), request.get("tool")
        if method not in METHODS or not isinstance(server, str):
            return {"decision": "deny", "code": "MALFORMED_RPC", "message": "Bad request"}
        operator = record = None
        try:
            operator = self._operator()
            record = self._record()
            if not self._serving():
                raise Denied("RUNTIME_BOUNDARY_UNVERIFIED", "Boundary is not verified yet")
            destination = operator.model.runtime_enforcement.destinations.get(server)
            if destination is None:
                raise Denied("DESTINATION_UNAPPROVED", "Destination is not operator-approved")
            manifest = Manifest.model_validate(self.store.manifest(self.deployment_id))
            capabilities = manifest.capabilities
            actions = {
                action
                for item in (capabilities.tools if capabilities else [])
                if item.mcp == destination.url
                for action in item.actions
            }
            if not actions:
                raise Denied("DESTINATION_UNDECLARED", "Manifest does not declare this server")
            if method != "tools/call":
                # No tool effect: forwarded after validation, kill-switch and pin checks.
                authorization = self._credential(destination, operator)
                self._record()  # a disable during the exchange still wins
                return {"decision": "allow", "authorization": authorization}
            if not isinstance(tool, str) or not isinstance(request.get("arguments_digest"), str):
                raise Denied("MALFORMED_RPC", "tools/call needs a tool and arguments digest")
            if tool not in actions:
                raise Denied("CAPABILITY_UNDECLARED", "Tool is not a declared capability")
            policy = self._evaluate(operator, record, manifest, server, destination, request)
            if not policy.allowed:
                raise Denied(policy.code or "POLICY_DENIED", "Policy denied this tool call")
            needs_approval = "require_approval" in policy.obligations or tool in (
                capabilities.approval_required if capabilities else []
            )
            if needs_approval:
                return self._held(operator, record, server, request, policy)
            authorization = self._credential(destination, operator)
            record = self._record()  # a disable during evaluation/exchange still wins
            self._audit(self._event(operator, record, request, "allow", policy=policy))
            return {"decision": "allow", "authorization": authorization}
        except Denied as denial:
            if record is not None or method == "tools/call":
                reason = {"code": denial.code, "message": str(denial)}
                try:
                    self.store.audit(self._event(operator, record, request, "deny", reason=reason))
                except AuditError:
                    pass  # denied either way
            return {"decision": "deny", "code": denial.code, "message": str(denial)}

    def _credential(self, destination, operator) -> str | None:
        return self.broker.authorization(
            destination, operator.path.parent if operator.path else None
        )

    def _evaluate(self, operator, record, manifest, server, destination, request):
        # Full authorization attributes pinned at deploy time; the record's audit snapshot is
        # truncated (64 roles/groups, attribute keys only) and must never drive an allow.
        principal = self.pin.get("principal_props")
        if not isinstance(principal, dict):
            raise Denied("RUNTIME_CONFIG_CHANGED", "Pinned principal attributes are missing")
        capabilities = manifest.capabilities
        try:
            engine = PolicyEngine(
                operator.model.pdp,
                list(manifest.policy.restrictions) if manifest.policy else [],
                operator.path.parent if operator.path else None,
            )
        except PolicyError as error:
            raise Denied(error.code, str(error)) from error
        return engine.evaluate(
            PolicyRequest(
                subject={
                    "type": "agent",
                    "id": principal.get("id", ""),
                    "properties": {
                        "name": manifest.name,
                        "environment": record.get("environment") or "",
                        "target": manifest.target,
                        "risk_tier": self.pin.get("risk_tier") or "",
                        "image_digest": self.pin.get("image_digest") or "",
                        "provider": principal.get("provider"),
                        "authority": principal.get("authority"),
                        "kind": principal.get("kind"),
                        "groups": list(principal.get("groups", [])),
                        "roles": list(principal.get("roles", [])),
                        "tool_actions": sorted(
                            {a for t in capabilities.tools for a in t.actions}
                            if capabilities
                            else set()
                        ),
                        "approval_required": sorted(
                            capabilities.approval_required if capabilities else []
                        ),
                    },
                },
                action="call_tool",
                resource={
                    "type": "tool",
                    "id": f"{server}/{request['tool']}",
                    "properties": {
                        "server": server,
                        "name": request["tool"],
                        "url": destination.url,
                        "environment": record.get("environment") or "",
                    },
                },
                context={
                    "arguments_digest": request["arguments_digest"],
                    "deployment_id": self.deployment_id,
                    "revision": record["revision"],
                    "operator_config_digest": operator.digest,
                },
            )
        )

    def _held(self, operator, record, server, request, policy) -> dict:
        binding = binding_digest(
            deployment=self.deployment_id,
            server=server,
            tool=request["tool"],
            arguments_digest=request["arguments_digest"],
            principal_key=record.get("principal_key"),
            revision=record["revision"],
            operator_digest=operator.digest,
            bundle_digests=policy.bundle_digests,
            # Attribute changes that keep the revision (risk tier, groups, roles) still void a
            # pending or approved release; so does replaying it in another MCP session.
            principal_props=self.pin.get("principal_props"),
            risk_tier=self.pin.get("risk_tier"),
            session=request.get("session"),
        )
        now = self.clock()
        destination = operator.model.runtime_enforcement.destinations[server]
        for held in self.store.held_for_binding(self.deployment_id, binding):
            if datetime.fromisoformat(held["expires"]) <= now:
                continue
            if held["state"] == "approved":
                authorization = self._credential(destination, operator)
                record = self._record()  # a disable during the exchange still wins
                event = self._event(
                    operator, record, request, "allow", policy=policy, held=held["id"]
                )
                try:
                    consumed = self.store.decide_held(
                        held["id"],
                        expected=("approved",),
                        state="consumed",
                        audit=event,
                        require_unexpired=True,
                    )
                except AuditError as error:
                    raise Unavailable() from error
                if not consumed:
                    raise Denied("APPROVAL_REPLAYED", "Release was already used")
                return {"decision": "allow", "authorization": authorization}
            if held["state"] == "pending":
                return {"decision": "held", "held_call_id": held["id"], "retry_after": RETRY_AFTER}
            if held["state"] == "rejected":
                raise Denied("CALL_REJECTED", "A checker rejected this call")
        identifier = uuid.uuid4().hex
        held = {
            "id": identifier,
            "deployment_id": self.deployment_id,
            "binding": binding,
            "server": server,
            "tool": request["tool"],
            "arguments_digest": request["arguments_digest"],
            "revision": record["revision"],
            "principal_key": record.get("principal_key") or "",
            "environment": record.get("environment") or "",
            "deployer_names": self.pin.get("deployer_names", []),
            "created": now.isoformat(),
            "expires": (now + timedelta(seconds=operator.model.approval_ttl_seconds)).isoformat(),
        }
        event = self._event(operator, record, request, "held", policy=policy, held=identifier)
        try:
            self.store.create_held(held, event)
        except AuditError as error:
            raise Unavailable() from error
        return {"decision": "held", "held_call_id": identifier, "retry_after": RETRY_AFTER}

    # -- audit -------------------------------------------------------------------------------

    def _event(self, operator, record, request, decision, *, policy=None, held=None, reason=None):
        principal = (record or {}).get("principal") or {}
        return AuditEvent(
            attempt_id=uuid.uuid4().hex,
            action="tool_call",
            decision=decision,
            actor=f"agent:{principal.get('provider', '?')}/{principal.get('id', '?')}",
            actor_verified=False,
            enforcement_mode="enforce",
            deployment_id=self.deployment_id,
            reasons=[reason] if reason else [],
            principal_snapshot=principal or None,
            context={
                "server": request.get("server"),
                "method": request.get("method"),
                "tool": request.get("tool"),
                "arguments_digest": request.get("arguments_digest"),
                "request_id": request.get("request_id"),
                "held_call_id": held,
            },
            revision_digest=(record or {}).get("revision"),
            policy_ids=list(policy.policy_ids) if policy else [],
            bundle_digests=dict(policy.bundle_digests) if policy else {},
            operator_config_digest=operator.digest if operator else self.pin["operator_digest"],
            image_digest=self.pin.get("image_digest"),
        )

    def _audit(self, event: AuditEvent) -> None:
        try:
            self.store.audit(event)
        except AuditError as error:
            raise Unavailable() from error


HANDLER_TIMEOUT = 5  # seconds per decision-service connection


def handler_for(service: DecisionService, token_sha256: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "agentbreeder-decision"
        sys_version = ""
        # The server is single-threaded: bound every connection so an idle or slow client
        # (any local process, or another container via host.docker.internal) cannot stall
        # every decision for the deployment.
        timeout = HANDLER_TIMEOUT

        def log_message(self, format, *args):  # noqa: A002
            pass

        def _reply(self, status: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            nonce = self.headers.get(NONCE_HEADER)
            if valid_nonce(nonce):
                # Proves this reply came from the holder of the pin, not a port squatter.
                self.send_header(MAC_HEADER, response_mac(token_sha256, nonce, status, data))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path != "/healthz":
                return self._reply(404, {})
            # Unauthenticated liveness for reconcile: the revision only, MAC'd to the caller's
            # nonce. Authenticated callers use POST /health for the full status.
            try:
                return self._reply(200, {"revision": service.health()["revision"]})
            except Denied as denial:
                return self._reply(409, {"code": denial.code})

        def do_POST(self):  # noqa: N802
            presented = self.headers.get("Authorization", "")
            if not token_matches(presented.removeprefix("Bearer "), token_sha256):
                return self._reply(401, {})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 <= length <= 64 * 1024:
                    raise ValueError
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._reply(400, {})
            try:
                if self.path == "/health":
                    return self._reply(200, service.health())
                if self.path == "/decide":
                    return self._reply(200, service.decide(body))
            except Denied as denial:
                return self._reply(409, {"code": denial.code})
            except Unavailable:
                return self._reply(503, {"code": "AUDIT_UNAVAILABLE"})
            return self._reply(404, {})

    return Handler


class _UnixHTTPServer(socketserver.UnixStreamServer):
    def get_request(self):
        request, _ = super().get_request()
        return request, ("unix", 0)


def make_server(service: DecisionService, pin: dict, directory: Path):
    handler = handler_for(service, pin["token_sha256"])
    if pin.get("listen") == "unix":
        # Mounted read-only into the sidecar only (it runs as another uid); the bearer token,
        # not file permissions, authenticates callers.
        path = directory / "sock" / "pep.sock"
        path.unlink(missing_ok=True)
        server = _UnixHTTPServer(str(path), handler)
        # Reviewed exception: both parents (egress/, egress/<id>/) are 0700 so no other host
        # user can traverse to it; the sidecar's uid reaches it only via its bind mount.
        os.chmod(path, 0o666)  # nosec B103
        return server, f"unix:{path}"
    # A stable port so a restarted service is reachable at the address the sidecar holds.
    server = HTTPServer(("127.0.0.1", int(pin.get("port", 0))), handler)
    return server, f"tcp:{server.server_address[1]}"


def write_private_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(value, handle)
    os.replace(temporary, path)


def run(state_dir: Path, deployment: str, max_idle_checks: int = 2, poll: float = 5) -> int:
    directory = egress_dir(state_dir, deployment)
    pin = json.loads((directory / "pin.json").read_text())
    store = Store(state_dir)
    service = DecisionService(store, pin, pin_path=directory / "pin.json")
    server, address = make_server(service, pin, directory)
    server.timeout = poll
    write_private_json(directory / "endpoint.json", {"pid": os.getpid(), "address": address})
    idle_checks = 0
    try:
        while True:
            server.handle_request()
            # Exit once the deployment is no longer active (removed, disabled, failed).
            idle_checks = 0 if service.active() else idle_checks + 1
            if idle_checks >= max_idle_checks:
                return 0
    finally:
        server.server_close()
        store.close()
        sys.stderr.flush()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - process entry point
    parser = argparse.ArgumentParser(prog="agentbreeder-decision-service")
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--deployment", required=True)
    args = parser.parse_args(argv)
    return run(args.state_dir, args.deployment)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
