"""One local lifecycle service; future transports must reuse this boundary.

Every deploy allocates an attempt ID and durably records its start before any IdP/PDP call.
The gate decision is recorded before any Docker operation; deployment state transitions and their
audit events commit in the same SQLite transaction. Audit failure aborts before Docker.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agentbreeder import gateway_keys, secret_files
from agentbreeder import mcp as mcp_relay
from agentbreeder.audit import AuditError, AuditEvent
from agentbreeder.deployer import Deployer, os_deployer
from agentbreeder.egress.boundary import Boundary, BoundaryUnverified
from agentbreeder.gate import GateDecision, GateDenied, IdentityGate, image_digest
from agentbreeder.identity import ProviderError
from agentbreeder.kubernetes import Kubernetes
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import (
    ContextError,
    DeploymentContext,
    OperatorConfig,
    OperatorConfigError,
    load_operator_config,
)
from agentbreeder.policy import PolicyError
from agentbreeder.runtime import Docker
from agentbreeder.secret_files import SecretError
from agentbreeder.store import DeploymentError, Store


class AuditUnavailable(DeploymentError):
    code = "AUDIT_WRITE_FAILED"

    def __init__(self, what: str = "nothing was deployed"):
        super().__init__(
            f"[AUDIT_WRITE_FAILED] Audit log could not be written; {what}. "
            "Check disk space and state permissions"
        )


class AuditIncomplete(DeploymentError):
    code = "AUDIT_WRITE_FAILED"

    def __init__(self, what: str = "after the container was started"):
        super().__init__(
            f"[AUDIT_WRITE_FAILED] Audit log could not be written {what}; "
            "run `agentbreeder reconcile --local` or remove the deployment"
        )


GATEWAY_KEY_ENV = "LLM_GATEWAY_KEY"
GATEWAY_KEY_FILE = "llm-gateway-key"
DEPLOYMENT_ID_ENV = "AGENTBREEDER_DEPLOYMENT_ID"
DEFAULT_BUDGET_USD = 10.0  # when neither the manifest nor the environment sets one
DEFAULT_RPM = 60


def _now() -> datetime:
    return datetime.now(UTC)


class DeploymentService:
    def __init__(
        self,
        store: Store,
        runtime: Docker,
        operator: OperatorConfig | None = None,
        gate: IdentityGate | None = None,
        kubernetes: Kubernetes | None = None,
    ):
        self.store = store
        self.runtime = runtime
        self.operator = operator or load_operator_config()
        self.gate = gate or IdentityGate(self.operator)
        self.kubernetes = kubernetes if kubernetes is not None else Kubernetes(self.operator)
        self.boundary = Boundary(runtime, store.directory, self.operator)
        self.enforcing = self.operator.model.runtime_enforcement.mode == "mcp_http"
        # Secret copies of deployments whose start failed, read just before their cleanup and
        # kept only in this process, so `release` can still redact the failed candidate's log.
        self.retained_secrets: dict = {}

    def _retain_copies(self, identifier: str) -> None:
        from agentbreeder import redaction

        found = redaction.Collected()
        try:
            redaction.deployment_copies(self.store.directory, identifier, found)
        except Exception:  # noqa: BLE001 - never block the cleanup that follows
            found.problems.append("cannot read the failed deployment's secrets")
        self.retained_secrets[identifier] = found

    def _runtime_for(self, target: str):
        return self.kubernetes if target == "kubernetes" else self.runtime

    def _target_of(self, identifier: str) -> str:
        return self.store.manifest(identifier).get("target", "local")

    # -- audit helpers -----------------------------------------------------------------------

    def _event(self, attempt_id: str, action: str, decision: str, actor: Deployer, **kw):
        return AuditEvent(
            attempt_id=attempt_id,
            action=action,
            decision=decision,
            actor=actor.label,
            actor_verified=actor.verified,
            enforcement_mode=self.operator.enforcement,
            operator_config_digest=self.operator.digest,
            **kw,
        )

    def _audit(self, event: AuditEvent) -> None:
        try:
            self.store.audit(event)
        except AuditError as error:
            raise AuditUnavailable() from error

    @staticmethod
    def _gate_fields(decision: GateDecision) -> dict:
        return {
            "checks_evaluated": list(decision.checks_evaluated),
            "checks_unavailable": list(decision.checks_unavailable),
            "principal_snapshot": decision.principal.snapshot() if decision.principal else None,
            "evidence_digest": decision.principal.evidence_digest if decision.principal else None,
            "revision_digest": decision.revision,
            "policy_ids": list(decision.policy_ids),
            "bundle_digests": dict(decision.bundle_digests),
        }

    def record_denial(self, action: str, actor: Deployer, code: str, message: str) -> str:
        """Durably record a denial that happened before an attempt could start."""
        attempt = uuid.uuid4().hex
        self._audit(self._event(attempt, "attempt_started", "started", actor))
        self._audit(
            self._event(
                attempt, action, "deny", actor, reasons=[{"code": code, "message": message}]
            )
        )
        return attempt

    def record(self, action: str, decision: str, actor: Deployer, reasons: list[dict]) -> str:
        """Record an orchestration event (e.g. `release`) in the same audit chain."""
        attempt = uuid.uuid4().hex
        self._audit(self._event(attempt, action, decision, actor, reasons=reasons[:50]))
        return attempt

    def preflight(
        self, manifest: Manifest, environment: str | None, deployer: Deployer
    ) -> GateDecision:
        """Evaluate the gate for a manifest without deploying, auditing or touching Docker.
        Used by `release` to learn, before removing production, whether its deploy would be
        denied or need approval."""
        try:
            context = self.operator.context(environment, manifest.target)
        except ContextError as error:
            return GateDecision(False, error.code, str(error))
        decision = self.gate.check(
            manifest,
            deployer,
            context,
            is_blocked=lambda key, env: self.store.blocker(key, env) is not None,
            is_reference_blocked=self.store.blocked_reference,
        )
        if decision.allowed:
            problem = self._deploy_requirements(manifest, context.environment)
            if problem is not None:
                return GateDecision(False, *problem)
        return decision

    # -- deploy ------------------------------------------------------------------------------

    def deploy(
        self,
        manifest: Manifest,
        *,
        local: bool,
        environment: str | None = None,
        deployer: Deployer | None = None,
        package: tuple[str, str] | None = None,
    ) -> dict:
        """`package` is (ref, tree hash) for `deploy agent/<name>@X.Y.Z` (M13 spec §7.3): the
        allow event carries `DEPLOY_PACKAGE` and the deployment's `package` column names it."""
        if local != (manifest.target == "local"):
            raise DeploymentError(
                "Deployment requires --local and target: local, or --cluster and target: "
                "kubernetes"
            )
        runtime = self._runtime_for(manifest.target)
        deployer = deployer or os_deployer()
        digest = manifest.digest()
        linked = {"ref": package[0], "sha256": package[1]} if package else None
        package_reasons = []
        if package:
            package_reasons = [{"code": "DEPLOY_PACKAGE", "message": f"{package[0]} {package[1]}"}]
        with self.store.lock():
            attempt = uuid.uuid4().hex
            requested = {"environment": environment, "target": manifest.target}
            base = {"image_digest": image_digest(manifest.image), "context": requested}
            self._audit(self._event(attempt, "attempt_started", "started", deployer, **base))
            try:
                context = self.operator.context(environment, manifest.target)
            except ContextError as error:
                self._audit(
                    self._event(
                        attempt,
                        "deploy",
                        "deny",
                        deployer,
                        reasons=[{"code": error.code, "message": str(error)}],
                        checks_evaluated=["context"],
                        **base,
                    )
                )
                raise GateDenied(error.code, str(error), attempt) from error
            base["context"] = {"environment": context.environment, "target": context.target}
            decision = self.gate.check(
                manifest,
                deployer,
                context,
                is_blocked=lambda key, env: self.store.blocker(key, env) is not None,
                is_reference_blocked=self.store.blocked_reference,
            )
            fields = {**base, **self._gate_fields(decision)}
            overridden = False
            if not decision.allowed:
                reason = [{"code": decision.code, "message": decision.message}]
                self._audit(
                    self._event(
                        attempt,
                        "deploy",
                        "deny",
                        deployer,
                        reasons=reason + package_reasons,
                        **fields,
                    )
                )
                if self.operator.enforcement != "audit" or not decision.overridable:
                    raise GateDenied(decision.code, decision.message, attempt)
                self._audit(
                    self._event(
                        attempt,
                        "audit_mode_proceed",
                        "override",
                        deployer,
                        reasons=reason + package_reasons,
                        **fields,
                    )
                )
                overridden = True
                decision.warnings.append(
                    f"AUDIT MODE: evaluated deny [{decision.code}] was not enforced"
                )
            gate_info = {
                "attempt_id": attempt,
                "enforcement": self.operator.enforcement,
                "actor": deployer.label,
                "actor_verified": deployer.verified,
                "checks_evaluated": decision.checks_evaluated,
                "checks_unavailable": decision.checks_unavailable,
                "obligations": list(decision.obligations),
                "warnings": decision.warnings,
            }
            record = self.store.active(manifest.name, manifest.target)
            if record and record["digest"] != digest:
                self._audit(
                    self._event(
                        attempt,
                        "deploy",
                        "aborted",
                        deployer,
                        reasons=[{"code": "NAME_CONFLICT", "message": "different spec"}]
                        + package_reasons,
                        **fields,
                    )
                )
                raise DeploymentError("Agent name already has a different spec; remove it first")
            problem = self._deploy_requirements(manifest, context.environment)
            if problem is not None:
                code, message = problem
                reason = [{"code": code, "message": message}] + package_reasons
                self._audit(
                    self._event(attempt, "deploy", "deny", deployer, reasons=reason, **fields)
                )
                raise GateDenied(code, message, attempt)
            approval = None
            if "require_approval" in decision.obligations:
                approval = self._approval_for(
                    manifest, decision, deployer, context, record, attempt, fields
                )
                if approval is None or approval["state"] == "pending":
                    pending = approval or {}
                    return {
                        "id": record["id"] if record else None,
                        "state": "pending_approval",
                        "approval_id": pending.get("id"),
                        "expires": pending.get("expires"),
                        "endpoint": None,
                        "error": None,
                        "gate": gate_info,
                    }
            if linked and record and record.get("package"):
                current = _linked_package(record["package"])
                if current != linked:
                    # A deploy by ref never replaces another package's link (spec §7.2 step 7).
                    shown = current["ref"] if current else "an unreadable package"
                    decision.warnings.append(
                        f"this deployment stays linked to {shown}, the package it was deployed "
                        "or released as; its spec is the same"
                    )
            if not overridden:
                self._audit(
                    self._event(
                        attempt, "deploy", "allow", deployer, reasons=package_reasons, **fields
                    )
                )
            try:
                if manifest.target == "kubernetes":
                    runtime.select(context.environment)
                    if record and record["context"] != runtime.context:
                        raise DeploymentError(
                            "[KUBERNETES_BINDING_CHANGED] This deployment is on another cluster "
                            "or namespace; remove it first"
                        )
                runtime.authorize(record["context"] if record else None)
            except DeploymentError as error:
                self._audit(
                    self._event(
                        attempt,
                        "deploy",
                        "aborted",
                        deployer,
                        reasons=[{"code": "RUNTIME_UNAVAILABLE", "message": str(error)}],
                        **fields,
                    )
                )
                raise
            principal = decision.principal
            identity_columns = {
                "principal": principal.snapshot() if principal else None,
                "principal_key": principal.key if principal else None,
                "environment": context.environment,
                "revision": decision.revision,
            }
            try:
                with self.store.transaction():
                    if not record:
                        identifier = uuid.uuid4().hex
                        if approval:
                            self._consume(approval, identifier, attempt, deployer, fields)
                        record = self.store.create(
                            identifier,
                            manifest.model_dump(),
                            digest,
                            runtime.context,
                            approval_id=approval["id"] if approval else None,
                            audit=self._event(
                                attempt, "deployment_created", "allow", deployer, **fields
                            ),
                            package=linked,
                            **identity_columns,
                        )
                    else:
                        if approval and approval["state"] == "approved":
                            self._consume(approval, record["id"], attempt, deployer, fields)
                        self.store.update_binding(
                            record["id"],
                            revision=decision.revision,
                            approval_id=approval["id"] if approval else None,
                            principal=identity_columns["principal"],
                            principal_key=identity_columns["principal_key"],
                            environment=identity_columns["environment"],
                            package=linked,
                        )
                    identifier = record["id"]
                    self.store.transition(
                        identifier,
                        "starting",
                        audit=self._event(
                            attempt,
                            "transition",
                            "starting",
                            deployer,
                            deployment_id=identifier,
                            **fields,
                        ),
                    )
            except AuditError as error:
                raise AuditUnavailable() from error
            except GateDenied as error:
                self._audit(
                    self._event(
                        attempt,
                        "deploy",
                        "deny",
                        deployer,
                        reasons=[{"code": error.code, "message": error.args[0]}],
                        **fields,
                    )
                )
                raise
            try:
                if self.enforcing:
                    result = self._run_boundary(
                        identifier, record, manifest, attempt, deployer, fields, decision
                    )
                else:
                    result = self._run(identifier, record, manifest, attempt, deployer, fields)
            except AuditError as error:
                raise AuditIncomplete() from error
            result["gate"] = gate_info
            return result

    def link_package(self, identifier: str, ref: str, sha256: str, actor: Deployer) -> None:
        """Link a deployment to the package `release` recorded for it, audited as
        `deploy/package_linked` in the same transaction (M13 spec §6 step 3, §7.3). A replaced
        link is named in the event (`PREVIOUS_PACKAGE`), so the column always shows production's
        newest release and the log shows what it replaced."""
        linked = {"ref": ref, "sha256": sha256}
        reasons = [{"code": "DEPLOY_PACKAGE", "message": f"{ref} {sha256}"}]
        try:
            with self.store.transaction():
                previous = self.store.get(identifier)["package"]
                if previous and previous != linked:
                    reasons.append(
                        {
                            "code": "PREVIOUS_PACKAGE",
                            "message": f"{previous.get('ref')} {previous.get('sha256')}",
                        }
                    )
                event = self._event(
                    uuid.uuid4().hex,
                    "deploy",
                    "package_linked",
                    actor,
                    deployment_id=identifier,
                    reasons=reasons,
                )
                self.store.link_package(identifier, linked, event)
        except AuditError as error:
            raise AuditUnavailable("the package link was not recorded") from error

    def _run(self, identifier, record, manifest, attempt, deployer, fields) -> dict:
        def event(state):
            return self._event(
                attempt, "transition", state, deployer, deployment_id=identifier, **fields
            )

        runtime = self._runtime_for(manifest.target)
        try:
            info = runtime.inspect(record)
            state = self.store.directory
            minting = self._minting(manifest)
            generated = {GATEWAY_KEY_ENV: "secret://" + GATEWAY_KEY_FILE} if minting else {}
            context_env = (fields.get("context") or {}).get("environment")
            _, servers = self._mcp_servers(manifest, context_env)
            for access, _server, _relay, _tools in servers:
                env, name = _mcp_secret(access.ref)
                generated[env] = "secret://" + name
            if (
                info is not None
                and (manifest.secrets or generated)
                and not secret_files.present(state, identifier, {**manifest.secrets, **generated})
            ):
                # Secret copies belong to one container generation. Without them (a failed
                # attempt cleaned up), recreate the container rather than start it against
                # missing or stale files.
                runtime.remove(info)
                info = None
            if info is None:
                mounts = []
                if manifest.secrets:
                    try:
                        mounts = secret_files.materialise(state, identifier, manifest.secrets)
                    except (SecretError, OSError) as error:
                        code = getattr(error, "code", "SECRET_UNREADABLE")
                        raise DeploymentError(f"[{code}] {error}") from error
                if minting:
                    mounts.append(self._mint_key(manifest, identifier, fields, attempt, deployer))
                environment = self._service_environment(
                    manifest, (fields.get("context") or {}).get("environment")
                )
                if environment is not None:
                    # Runtimes that trace put it on their spans, so `traces ID` can attribute
                    # a trace to this deployment exactly (#907). Not a secret.
                    environment[DEPLOYMENT_ID_ENV] = identifier
                for access, server, relay, tools in servers:
                    mounts.append(
                        self._grant_mcp(
                            manifest, identifier, context_env, access, server, relay, tools
                        )
                    )
                    variable = mcp_relay.env_name(access.ref.split("/")[1].split("@")[0])
                    environment[f"AGENTBREEDER_MCP_{variable}_URL"] = relay.url
                options = {}
                if environment is not None:
                    options = {"environment": environment, "host_gateway": True}
                network = self._agents_network((fields.get("context") or {}).get("environment"))
                if network is not None:
                    options["agents_network"] = network
                runtime.create(record, manifest, mounts=mounts, **options)
                info = runtime.inspect(record)
            if info is None:
                raise DeploymentError(
                    "Container creation could not be verified; retry to reconcile"
                )
            self.store.transition(identifier, "starting", resource=info["Id"])
            if not info["State"]["Running"]:
                runtime.start(info)
            info = runtime.inspect(record)
            if info is None:
                raise DeploymentError("Container disappeared before health verification")
            endpoint = runtime.endpoint(info, manifest)
            if not runtime.healthy(endpoint, manifest.health_path):
                raise DeploymentError(
                    "Health check failed; fix the image or remove the deployment"
                )
            return self.store.transition(
                identifier, "ready", endpoint=endpoint, audit=event("ready")
            )
        except DeploymentError as error:
            self._retain_copies(identifier)
            secret_files.cleanup(self.store.directory, identifier)
            self._revoke_mcp(identifier)  # best effort here; reconcile retries what remains
            # Always revoke on failure: a key from an earlier generation may still be live even
            # when this attempt minted nothing (container gone, or inspect failed). Revoking a
            # key that doesn't exist is a no-op at the gateway.
            self._revoke_key(manifest, identifier, attempt, deployer)
            return self.store.transition(
                identifier, "failed", error=str(error), audit=event("failed")
            )

    # -- per-deployment gateway keys ---------------------------------------------------------

    def _deploy_requirements(
        self, manifest: Manifest, environment: str | None = None
    ) -> tuple[str, str] | None:
        """Checks after the gate that need no side effects: what this installation can run.
        `deploy` and `preflight` share them, so `release --rollback` learns of a deny before it
        removes production."""
        if manifest.target == "kubernetes":
            problem = self._kubernetes_requirements(manifest, environment)
            if problem is not None:
                return problem
        if manifest.mcp_access is not None:
            if self.enforcing:
                return (
                    "MCP_RELAY_UNSUPPORTED",
                    "mcp_access is not yet supported with runtime enforcement",
                )
            problem, _ = self._mcp_servers(manifest, environment)
            if problem is not None:
                return problem
        if self.enforcing and manifest.model_access is not None:
            # The egress boundary runs agents on an internal network with no gateway key,
            # gateway URL or service URLs: the deploy would be "ready" and every call fail.
            return (
                "MODEL_ACCESS_UNSUPPORTED",
                "model_access is not yet supported with runtime enforcement",
            )
        if self._minting(manifest) and (
            GATEWAY_KEY_ENV in manifest.secrets
            or f"secret://{GATEWAY_KEY_FILE}" in manifest.secrets.values()
        ):
            return "SECRET_RESERVED", f"{GATEWAY_KEY_ENV} is minted per deployment; remove it"
        if manifest.secrets:
            try:
                if self.enforcing:
                    raise SecretError(
                        "SECRETS_UNSUPPORTED",
                        "Secret files are not yet supported with runtime enforcement",
                    )
                secret_files.preflight(self.store.directory, manifest.secrets)
            except SecretError as error:
                return error.code, str(error)
        return None

    def _kubernetes_requirements(self, manifest, environment) -> tuple[str, str] | None:
        """What the cluster target cannot do yet is refused, never silently weakened."""
        config = self.operator.model.environments.get(environment or "")
        if config is None or config.kubernetes is None:
            return "KUBERNETES_UNCONFIGURED", f"Environment {environment} has no kubernetes block"
        if self.enforcing:
            return (
                "KUBERNETES_ENFORCEMENT_UNSUPPORTED",
                "Runtime enforcement is not supported on the Kubernetes target",
            )
        if manifest.mcp_access is not None:
            return "KUBERNETES_MCP_UNSUPPORTED", "mcp_access is not supported on Kubernetes yet"
        return None

    def _minting(self, manifest: Manifest) -> bool:
        if manifest.model_access is None:
            return False
        gateway = self.operator.model.gateways.get(manifest.model_access.gateway)
        return gateway is not None and gateway.master_key is not None

    def _gateway(self, manifest_data) -> gateway_keys.LiteLLM | None:
        access = manifest_data.get("model_access") if isinstance(manifest_data, dict) else None
        if not access:
            return None
        return gateway_keys.client(self.operator, access["gateway"])

    def _mint_key(self, manifest, identifier, fields, attempt, actor):
        """Mint this container generation's key: after the gate and approval, inside the
        lifecycle lock. The value only reaches the deployment's private secret file."""
        access = manifest.model_access
        environment = self.operator.model.environments.get(
            (fields.get("context") or {}).get("environment") or ""
        )
        ceilings = environment.ceilings if environment else None
        budget = access.budget_usd or (ceilings.budget_usd if ceilings else DEFAULT_BUDGET_USD)
        rpm = access.rpm or (ceilings.rpm if ceilings else DEFAULT_RPM)
        key_alias = gateway_keys.alias(identifier)
        try:
            client = gateway_keys.client(self.operator, access.gateway)
            key = client.mint(
                key_alias,
                list(access.aliases),
                float(budget),
                int(rpm),
                {
                    "agentbreeder": True,
                    "deployment_id": identifier,
                    "agent": manifest.name,
                    "environment": environment
                    and (fields.get("context") or {}).get("environment"),
                },
            )
            mount = secret_files.write_generated(
                self.store.directory, identifier, GATEWAY_KEY_FILE, GATEWAY_KEY_ENV, key.encode()
            )
        except (gateway_keys.GatewayError, OperatorConfigError, SecretError, OSError) as error:
            code = getattr(error, "code", "GATEWAY_ERROR")
            raise DeploymentError(f"[{code}] gateway key not minted: {error}") from error
        self._audit(
            self._event(
                attempt,
                "gateway_key",
                "minted",
                actor,
                deployment_id=identifier,
                reasons=[{"code": "GATEWAY_KEY", "message": key_alias}],
            )
        )
        return mount

    def _revoke_key(self, manifest_or_data, identifier, attempt, actor) -> str | None:
        """Best-effort revoke; returns None, 'revoked' or 'revoke_failed' (reconcile retries)."""
        data = (
            manifest_or_data.model_dump()
            if isinstance(manifest_or_data, Manifest)
            else manifest_or_data
        )
        try:
            client = self._gateway(data)
            if client is None:
                return None
            client.revoke(gateway_keys.alias(identifier))
            outcome = "revoked"
        except (gateway_keys.GatewayError, OperatorConfigError):
            outcome = "revoke_failed"
        self._audit(
            self._event(
                attempt,
                "gateway_key",
                outcome,
                actor,
                deployment_id=identifier,
                reasons=[{"code": "GATEWAY_KEY", "message": gateway_keys.alias(identifier)}],
            )
        )
        return outcome

    # -- MCP relay grants (spec §7.3, §7.7.3) -----------------------------------------------

    def _mcp_servers(self, manifest: Manifest, environment: str | None):
        """Resolve each `mcp_access` ref to the environment's relay and the agent's grant.
        Returns (problem, [(access, server, relay, tools)]); tools are the requested ones."""
        if not manifest.mcp_access:
            return None, []
        env = self.operator.model.environments.get(environment or "")
        relays = env.mcp_relays if env else {}
        # The grant belongs to the agent whose identity (or candidate identity) this is.
        granted: dict[str, list[str]] = {}
        identity = manifest.identity
        for agent in (env.agents if env else {}).values() if identity is not None else ():
            candidate = agent.candidate_identity
            if identity == agent.identity or (candidate is not None and identity == candidate):
                granted = agent.mcp
                break
        result = []
        for access in manifest.mcp_access:
            matches = sorted(name for name, relay in relays.items() if relay.ref == access.ref)
            if not matches:
                return (
                    "MCP_SERVER_UNAVAILABLE",
                    f"environment {environment} has no MCP relay for {access.ref}",
                ), []
            if len(matches) > 1:
                return (
                    "MCP_SERVER_AMBIGUOUS",
                    f"environment {environment} has {len(matches)} relays for {access.ref}",
                ), []
            server = matches[0]
            missing = sorted(set(access.tools) - set(granted.get(server, [])))
            if missing:
                return (
                    "MCP_TOOL_NOT_GRANTED",
                    f"{access.ref}: {len(missing)} requested tool(s) not granted to this agent "
                    f"in {environment}: {', '.join(missing)}",
                ), []
            result.append((access, server, relays[server], list(access.tools)))
        return None, result

    def _grant_mcp(self, manifest, identifier, environment, access, server, relay, tools):
        """Mint this container generation's relay token. Earlier grants of the deployment go
        first, so a retry or crash never leaves an older token valid; the token reaches only
        the deployment's private secret file, the relay only its sha256."""
        directory = Path(relay.grants_dir)
        env, name = _mcp_secret(access.ref)
        try:
            mcp_relay.revoke(directory, deployment=identifier)
            token = mcp_relay.mint_token()
            mount = secret_files.write_generated(
                self.store.directory, identifier, name, env, token.encode()
            )
            mcp_relay.write_grant(
                directory,
                token,
                {
                    "deployment": identifier,
                    "agent": manifest.name,
                    "environment": environment,
                    "server": server,
                    "tools": sorted(tools),
                },
            )
        except (mcp_relay.McpError, SecretError, OSError) as error:
            code = getattr(error, "code", "MCP_GRANT_FAILED")
            raise DeploymentError(f"[{code}] MCP grant for {server} not written") from error
        return mount

    def _relay_dirs(self) -> list[Path]:
        environments = self.operator.model.environments.values()
        return [Path(r.grants_dir) for env in environments for r in env.mcp_relays.values()]

    def _revoke_mcp(self, identifier: str) -> bool:
        """Delete every grant file of a deployment, in every relay this installation knows.
        Tries every relay even if one fails; False means some grant may remain (reconcile
        retries). Callers finish their other cleanup before acting on it."""
        ok = True
        for directory in self._relay_dirs():
            try:
                mcp_relay.revoke(directory, deployment=identifier)
            except OSError:
                ok = False
        return ok

    def _prune_mcp_grants(self, attempt, actor) -> list[dict]:
        """Delete grant files with no active deployment, or whose hash isn't that deployment's
        current token (its private token file is the record)."""
        results = []
        environments = self.operator.model.environments
        for env_name, env in environments.items():
            for server, relay in env.mcp_relays.items():
                directory = Path(relay.grants_dir)
                for digest, grant in mcp_relay.grants(directory).items():
                    if self._grant_current(digest, grant, env_name, server, relay.ref):
                        continue
                    (directory / f"{digest}.json").unlink(missing_ok=True)
                    self._audit(
                        self._event(
                            attempt,
                            "mcp_grant",
                            "revoked",
                            actor,
                            deployment_id=str(grant.get("deployment", ""))[:64] or None,
                            reasons=[{"code": "MCP_GRANT_ORPHAN", "message": server}],
                        )
                    )
                    results.append(
                        {"id": digest[:16], "name": server, "compliance": "mcp_grant_revoked"}
                    )
        return results

    def _grant_current(self, digest, grant, environment, server, ref) -> bool:
        identifier = grant.get("deployment")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[0-9a-f]{32}", identifier)
            or grant.get("server") != server
            or grant.get("environment") != environment
        ):
            return False
        try:
            record = self.store.get(identifier)
        except DeploymentError:
            return False
        if record["state"] not in ("ready", "starting", "pending"):
            return False
        _, name = _mcp_secret(ref)
        token_file = secret_files.deployment_dir(self.store.directory, identifier) / name
        try:
            return mcp_relay.token_hash(token_file.read_text().strip()) == digest
        except (OSError, mcp_relay.McpError):
            return False

    def _agents_network(self, name: str | None) -> str | None:
        """The operator environment's extra network (a `services up` stack), if any."""
        environment = self.operator.model.environments.get(name or "")
        return environment.network if environment else None

    def _service_environment(self, manifest: Manifest, name: str | None) -> dict[str, str] | None:
        """Non-secret configuration for deployments that request model access (agent.yaml
        builds): the gateway URL agents use and the environment's service URLs."""
        if manifest.model_access is None:
            return None
        model = self.operator.model
        environment = model.environments.get(name or "")
        gateway = model.gateways.get(manifest.model_access.gateway)
        result = {"AGENTBREEDER_GATEWAY_URL": gateway.agent_url} if gateway else {}
        for service, url in sorted((environment.services if environment else {}).items()):
            result[f"AGENTBREEDER_SERVICE_{service.upper()}"] = url
        return result

    def _run_boundary(self, identifier, record, manifest, attempt, deployer, fields, decision):
        """Start the agent behind its egress boundary. Verification failure is a deny (exit 2)
        and leaves nothing running; there is no degraded mode."""
        boundary = self.boundary

        def event(state):
            return self._event(
                attempt, "transition", state, deployer, deployment_id=identifier, **fields
            )

        principal = decision.principal
        payload = {
            "operator_path": str(self.operator.path) if self.operator.path else None,
            "operator_digest": self.operator.digest,
            "revision": decision.revision,
            "deployer_names": sorted({deployer.label, deployer.subject, *deployer.aliases}),
            "risk_tier": decision.risk_tier,
            "image_digest": image_digest(manifest.image),
            # Full, untruncated authorization attributes for runtime call_tool decisions.
            "principal_props": _authorization_props(principal) if principal else None,
        }
        try:
            if self._boundary_current(record, payload):
                endpoint = boundary.endpoint(record)
                if self.runtime.healthy(endpoint, manifest.health_path):
                    return self.store.transition(
                        identifier, "ready", endpoint=endpoint, audit=event("ready")
                    )
            token = boundary.write_pin(record, payload)
            boundary.start_service(record)
            boundary.create(record, manifest, token)
            info = self.runtime.inspect(record)
            if info is None:
                raise DeploymentError("Agent container creation could not be verified")
            self.store.transition(identifier, "starting", resource=info["Id"])
            boundary.start(record)
            try:
                boundary.verify(record)
                boundary.set_serving(record, True)
            except BoundaryUnverified as error:
                self._teardown_quietly(record)
                reason = [{"code": error.code, "message": str(error)}]
                self._audit(
                    self._event(
                        attempt,
                        "deploy",
                        "deny",
                        deployer,
                        deployment_id=identifier,
                        reasons=reason,
                        **fields,
                    )
                )
                self.store.transition(
                    identifier, "failed", error=f"[{error.code}] {error}", audit=event("failed")
                )
                raise GateDenied(error.code, str(error), attempt) from error
            endpoint = boundary.endpoint(record)
            if not self.runtime.healthy(endpoint, manifest.health_path):
                raise DeploymentError(
                    "Health check through the sidecar failed; fix the image or remove it"
                )
            return self.store.transition(
                identifier, "ready", endpoint=endpoint, audit=event("ready")
            )
        except GateDenied:
            raise
        except DeploymentError as error:
            self._teardown_quietly(record)
            return self.store.transition(
                identifier, "failed", error=str(error), audit=event("failed")
            )

    def _boundary_current(self, record, payload) -> bool:
        """An already-verified boundary with an identical pin is reused, not restarted."""
        if not self.boundary.enforced(record):
            return False
        try:
            pin = self.boundary.pin(record)
            if pin.get("serving") is not True or any(
                pin.get(key) != value for key, value in payload.items()
            ):
                return False
            if not self.boundary.service_healthy(record):
                return False
            self.boundary.verify(record)
            return True
        except (DeploymentError, OSError, ValueError):
            return False

    def _teardown_quietly(self, record) -> bool:
        try:
            self.boundary.teardown(record)
            return True
        except DeploymentError:
            return False  # reported by the caller's failed state; reconcile retries

    # -- approvals (maker-checker) -----------------------------------------------------------

    def _approval_for(self, manifest, decision, deployer, context, record, attempt, fields):
        """Return a usable approval (approved or already consumed for this execution) or the
        pending request. Invalidates stale approvals."""
        revision, evidence = decision.revision, decision.principal.evidence_digest
        if (
            record
            and record.get("approval_id")
            and record.get("revision") == revision
            and record["state"] in ("pending", "starting", "ready")
        ):
            bound = self.store.approval(record["approval_id"])
            in_flight = record["state"] in ("pending", "starting")
            unexpired = datetime.fromisoformat(bound["expires"]) > _now()
            if bound["state"] == "consumed" and bound["execution_id"] == record["id"]:
                if bound["evidence"] == evidence and (in_flight or unexpired):
                    return bound  # crash recovery of the same execution; gate already re-ran
        now = _now()
        for existing in self.store.approvals_for_revision(revision):
            if existing["state"] not in ("pending", "approved"):
                continue
            stale = None
            if datetime.fromisoformat(existing["expires"]) <= now:
                stale = ("expired", "APPROVAL_EXPIRED")
            elif existing["evidence"] != evidence:
                stale = ("invalidated", "EVIDENCE_CHANGED")
            elif existing["state"] == "approved" and not self._checker_still_authorized(
                existing, manifest, decision, context, deployer
            ):
                stale = ("invalidated", "CHECKER_REVOKED")
            if stale:
                self.store.decide_approval(
                    existing["id"],
                    expected=("pending", "approved"),
                    state=stale[0],
                    audit=self._event(
                        attempt,
                        "approval_" + stale[0],
                        stale[0],
                        deployer,
                        reasons=[{"code": stale[1], "message": "approval no longer valid"}],
                        **fields,
                    ),
                )
                continue
            if existing["state"] == "pending":
                self._audit(
                    self._event(
                        attempt,
                        "deploy",
                        "pending_approval",
                        deployer,
                        reasons=[{"code": "APPROVAL_REQUIRED", "message": existing["id"]}],
                        **fields,
                    )
                )
            return existing
        sponsors = [{"id": p.id, "name": p.name} for p in _active_sponsors(decision.principal)]
        request = {
            "id": uuid.uuid4().hex,
            "revision": revision,
            "evidence": evidence,
            "principal_key": decision.principal.key,
            "environment": context.environment,
            "agent": json.dumps(
                {
                    "name": manifest.name,
                    "target": context.target,
                    "risk_tier": decision.risk_tier,
                    "principal": decision.principal.snapshot(),
                    "restrictions": list(manifest.policy.restrictions) if manifest.policy else [],
                    "tool_actions": sorted(
                        {a for t in manifest.capabilities.tools for a in t.actions}
                        if manifest.capabilities
                        else ()
                    ),
                    "approval_required": sorted(manifest.capabilities.approval_required)
                    if manifest.capabilities
                    else [],
                    "deployer_names": [deployer.subject, *deployer.aliases],
                    "policy_context": {
                        "image_digest": image_digest(manifest.image),
                        "operator_config_digest": self.operator.digest,
                    },
                }
            ),
            "deployer": deployer.label,
            "deployer_verified": deployer.verified,
            "sponsors": sponsors,
            "obligations": list(decision.obligations),
            "created": now.isoformat(),
            "expires": (
                now + timedelta(seconds=self.operator.model.approval_ttl_seconds)
            ).isoformat(),
        }
        try:
            return self.store.create_approval(
                request,
                self._event(
                    attempt,
                    "deploy",
                    "pending_approval",
                    deployer,
                    reasons=[{"code": "APPROVAL_REQUIRED", "message": request["id"]}],
                    **fields,
                ),
            )
        except AuditError as error:
            raise AuditUnavailable() from error

    def _checker_still_authorized(self, approval, manifest, decision, context, deployer) -> bool:
        claims = approval["checker_claims"] or {}
        checker = Deployer(
            claims.get("subject", ""),
            True,
            claims.get("issuer"),
            "oidc",
            tuple(claims.get("aliases", ())),
            human=claims.get("human") is True,
        )
        if not checker.human:
            return False
        active = _active_sponsors(decision.principal)
        if len(active) == 1 and checker.same_person(active[0].id, active[0].name):
            return False  # checker became the sole sponsor since approving
        if checker.same_person(deployer.subject, *deployer.aliases):
            return False  # the current deployer's identity now overlaps the checker
        try:
            result = self.gate.evaluate(
                self.gate.policy_request(
                    "approve",
                    checker,
                    agent=manifest.name,
                    context=context,
                    principal=decision.principal,
                    risk_tier=decision.risk_tier,
                    manifest=manifest,
                    extra={
                        "image_digest": image_digest(manifest.image),
                        "operator_config_digest": self.operator.digest,
                    },
                ),
                list(manifest.policy.restrictions) if manifest.policy else [],
            )
        except PolicyError:
            return False
        return result.allowed and not result.obligations

    def _consume(self, approval, execution_id, attempt, deployer, fields):
        # Only "approved" can be consumed; anything else (including an already-consumed
        # approval) fails the compare-and-set below and is denied as a replay.
        if not self.store.decide_approval(
            approval["id"],
            expected=("approved",),
            state="consumed",
            execution_id=execution_id,
            audit=self._event(
                attempt,
                "approval_consumed",
                "consumed",
                deployer,
                deployment_id=execution_id,
                **fields,
            ),
        ):
            raise GateDenied("APPROVAL_REPLAYED", "Approval was already used or changed", attempt)

    def decide(self, approval_id: str, checker: Deployer, *, approve: bool) -> dict:
        """Approve or reject a pending request as a separately authenticated checker."""
        with self.store.lock():
            approval = self.store.approval(approval_id)
            attempt = uuid.uuid4().hex
            action = "approval_granted" if approve else "approval_rejected"

            def deny(code, message):
                self._audit(
                    self._event(
                        attempt,
                        action,
                        "deny",
                        checker,
                        reasons=[{"code": code, "message": message}],
                        revision_digest=approval["revision"],
                    )
                )
                raise GateDenied(code, message, attempt)

            if approval["state"] != "pending":
                deny("APPROVAL_NOT_PENDING", "Approval request is not pending")
            if datetime.fromisoformat(approval["expires"]) <= _now():
                self.store.decide_approval(
                    approval_id,
                    expected=("pending",),
                    state="expired",
                    audit=self._event(attempt, "approval_expired", "expired", checker),
                )
                raise GateDenied("APPROVAL_EXPIRED", "Approval request expired", attempt)
            if not checker.verified:
                deny("DEPLOYER_UNVERIFIED", "Checkers must sign in (agentbreeder login)")
            if not checker.human:
                deny("CHECKER_NOT_HUMAN", "Approvals require a human identity issuer")
            agent = json.loads(approval["agent"])
            if (
                checker.label == approval["deployer"]
                or approval["deployer"].endswith("#" + checker.subject)
                or checker.same_person(*agent.get("deployer_names", []))
            ):
                deny("SELF_APPROVAL", "The deployer cannot approve their own request")
            sponsors = approval["sponsors"]
            if len(sponsors) == 1 and checker.same_person(sponsors[0]["id"], sponsors[0]["name"]):
                deny("SOLE_SPONSOR_APPROVAL", "The principal's sole sponsor cannot be the checker")
            if self.store.blocker(approval["principal_key"], approval["environment"]):
                deny("PRINCIPAL_BLOCKED", "Principal is administratively disabled")
            context = DeploymentContext(approval["environment"], agent["target"])
            try:
                result = self.gate.evaluate(
                    self.gate.policy_request(
                        "approve",
                        checker,
                        agent=agent["name"],
                        context=context,
                        principal=_principal_props(agent["principal"]),
                        risk_tier=agent["risk_tier"],
                        extra=agent.get("policy_context") or {},
                        tool_actions=agent.get("tool_actions", []),
                        approval_required=agent.get("approval_required", []),
                    ),
                    agent["restrictions"],
                )
            except PolicyError as error:
                deny(error.code, str(error))
            if not result.allowed:
                deny("CHECKER_NOT_AUTHORIZED", "Policy does not authorize this checker")
            if result.obligations:
                deny("OBLIGATION_UNSUPPORTED", "Obligations are unsupported for approve")
            state = "approved" if approve else "rejected"
            if not self.store.decide_approval(
                approval_id,
                expected=("pending",),
                state=state,
                checker=checker.label,
                checker_claims=checker.claims(),
                audit=self._event(
                    attempt,
                    action,
                    state,
                    checker,
                    revision_digest=approval["revision"],
                    policy_ids=list(result.policy_ids),
                    bundle_digests=dict(result.bundle_digests),
                ),
            ):
                deny("APPROVAL_NOT_PENDING", "Approval request changed concurrently")
            return self.store.approval(approval_id)

    # -- remove ------------------------------------------------------------------------------

    def remove(self, identifier: str, *, local: bool, actor: Deployer | None = None) -> dict:
        target = self._target_of(identifier)
        if local != (target == "local"):
            raise DeploymentError(
                "Removal requires --local for a local deployment, --cluster for a Kubernetes one"
            )
        runtime = self._runtime_for(target)
        actor = actor or os_deployer()
        with self.store.lock():
            record = self.store.get(identifier)
            if record["state"] == "removed":
                return record
            attempt = uuid.uuid4().hex

            def event(state):
                return self._event(attempt, "transition", state, actor, deployment_id=identifier)

            self._audit(
                self._event(attempt, "attempt_started", "started", actor, deployment_id=identifier)
            )
            self._audit(self._event(attempt, "remove", "allow", actor, deployment_id=identifier))
            try:
                runtime.authorize(record["context"])
            except DeploymentError as error:
                self._audit(
                    self._event(
                        attempt,
                        "remove",
                        "aborted",
                        actor,
                        deployment_id=identifier,
                        reasons=[{"code": "RUNTIME_UNAVAILABLE", "message": str(error)}],
                    )
                )
                # Removal must run against the context the container was created in; never
                # fall back to another engine, which could miss (and orphan) the container.
                raise DeploymentError(
                    f"{error}. This deployment was created in "
                    f"{'cluster' if target == 'kubernetes' else 'Docker context'} "
                    f"{record['context']!r}; make that context reachable and retry"
                ) from error
            try:
                self.store.transition(identifier, "removing", audit=event("removing"))
            except AuditError as error:
                raise AuditUnavailable("nothing was removed") from error
            try:
                try:
                    info = runtime.inspect(record)
                    if info:
                        runtime.remove(info)
                    elif target == "kubernetes":
                        runtime.sweep(record)  # a failed create can leave a Secret behind
                    if self.boundary.enforced(record):
                        self.boundary.teardown(record)
                    if runtime.inspect(record) is not None:
                        raise DeploymentError("Container removal could not be verified; retry")
                    grants_ok = self._revoke_mcp(identifier)
                    secret_files.cleanup(self.store.directory, identifier)
                    key = self._revoke_key(
                        self.store.manifest(identifier), identifier, attempt, actor
                    )
                    if not grants_ok:  # never report success while a grant may remain
                        raise DeploymentError(
                            "[MCP_GRANT_CLEANUP] MCP grant files not deleted; retry remove"
                        )
                    removed = self.store.transition(identifier, "removed", audit=event("removed"))
                    return {**removed, "gateway_key": key} if key else removed
                except DeploymentError as error:
                    return self.store.transition(
                        identifier, "failed", error=str(error), audit=event("failed")
                    )
            except AuditError as error:
                raise AuditIncomplete("after the container was removed") from error

    # -- kill switch ------------------------------------------------------------------------

    def _principal_target(self, target: str, environment: str | None, provider, ref):
        """Resolve (principal_key, environment) from a deployment ID/name or a provider ref."""
        if provider and ref:
            if not environment:
                raise DeploymentError("--environment is required with --provider/--ref")
            principal = self.gate.resolve(provider, ref)
            return principal.key, environment, principal
        try:
            record = self.store.get(target)
        except DeploymentError:
            # The kill switch finds every record of the principal in `_affected`; either target
            # is enough to resolve it.
            record = self.store.active(target) or self.store.active(target, "kubernetes")
        if not record or not record.get("principal_key"):
            raise DeploymentError(
                "No identity-bound deployment found; use --provider, --ref and --environment"
            )
        return record["principal_key"], record["environment"], None

    def disable(
        self,
        target: str,
        actor: Deployer,
        *,
        environment: str | None = None,
        provider: str | None = None,
        ref: str | None = None,
        reason: str = "administrative disable",
        idp: bool = False,
    ) -> dict:
        with self.store.lock():
            try:
                key, env, principal = self._principal_target(target, environment, provider, ref)
            except ProviderError as error:
                raise GateDenied(error.code, str(error)) from error
            attempt = uuid.uuid4().hex
            snapshot = {"key": json.loads(key), "environment": env}
            affected = self._affected(key, env, provider, ref)
            try:
                with self.store.transaction():
                    self.store.add_blocker(
                        key,
                        env,
                        actor.label,
                        actor.verified,
                        reason[:200],
                        self._event(attempt, "disable", "blocked", actor, context=snapshot),
                    )
                    # A disabled execution never resumes on its old approval; unbound
                    # (audit-mode) records are bound so reconcile retries their cleanup.
                    for record in affected:
                        self.store.update_binding(
                            record["id"],
                            revision=None,
                            approval_id=None,
                            principal_key=None if record.get("principal_key") else key,
                            environment=None if record.get("environment") else env,
                        )
            except AuditError as error:
                raise AuditUnavailable("nothing was disabled") from error
            for approval in self.store.open_approvals(key, env):
                self.store.decide_approval(
                    approval["id"],
                    expected=("pending", "approved"),
                    state="invalidated",
                    audit=self._event(
                        attempt,
                        "approval_invalidated",
                        "invalidated",
                        actor,
                        reasons=[{"code": "PRINCIPAL_BLOCKED", "message": approval["id"]}],
                    ),
                )
            stopped, failed = self._stop_blocked(key, env, attempt, actor, provider, ref)
            idp_result = None
            if idp:
                idp_result = self._idp_disable(key, principal, attempt, actor)
                if idp_result != "disabled":
                    failed.append({"idp": idp_result})
            return {
                "blocked": snapshot,
                "stopped": stopped,
                "failed": failed,
                "idp": idp_result,
                "state": "failed" if failed else "disabled",
            }

    def _affected(self, key, env, provider=None, ref=None) -> list[dict]:
        """Records bound to the principal, plus unbound records naming the same reference."""
        records = {r["id"]: r for r in self.store.active_for_principal(key, env)}
        connection, _, identifier = json.loads(key)
        references = {(connection, identifier)}
        if provider and ref:
            references.add((provider, ref))
        for record in records.values():
            identity = self.store.manifest(record["id"]).get("identity") or {}
            references.add((identity.get("provider"), identity.get("ref")))
        for record in self.store.active_all():
            if record["id"] in records or record.get("environment") not in (env, None):
                continue
            identity = self.store.manifest(record["id"]).get("identity") or {}
            if (identity.get("provider"), identity.get("ref")) in references:
                records[record["id"]] = record
        return list(records.values())

    def _stop_blocked(self, key, env, attempt, actor, provider=None, ref=None):
        stopped, failed = [], []
        for record in self._affected(key, env, provider, ref):
            identifier = record["id"]
            target = self._target_of(identifier)
            runtime = self._runtime_for(target)
            try:
                runtime.authorize(record["context"])
                info = runtime.inspect(record)
                if info:
                    runtime.remove(info)
                elif target == "kubernetes":
                    runtime.sweep(record)
                if self.boundary.enforced(record):
                    self.boundary.teardown(record)
                if runtime.inspect(record) is not None:
                    raise DeploymentError("Container stop could not be verified; retry reconcile")
                grants_ok = self._revoke_mcp(identifier)
                secret_files.cleanup(self.store.directory, identifier)
                self._revoke_key(self.store.manifest(identifier), identifier, attempt, actor)
                if not grants_ok:
                    raise DeploymentError(
                        "[MCP_GRANT_CLEANUP] MCP grant files not deleted; retry reconcile"
                    )
                self.store.transition(
                    identifier,
                    "disabled",
                    audit=self._event(
                        attempt, "transition", "disabled", actor, deployment_id=identifier
                    ),
                )
                stopped.append(identifier)
            except DeploymentError as error:
                self.store.transition(
                    identifier,
                    "failed",
                    error=f"Disable incomplete: {error}",
                    audit=self._event(
                        attempt, "transition", "failed", actor, deployment_id=identifier
                    ),
                )
                failed.append({"id": identifier, "error": str(error)})
        return stopped, failed

    def _idp_disable(self, key, principal, attempt, actor) -> str:
        provider, _, identifier = json.loads(key)
        try:
            adapter = self.gate.provider(provider)
            disable = getattr(adapter, "disable", None)
            if disable is None:
                raise ProviderError("PROVIDER_UNSUPPORTED", "Adapter cannot disable principals")
            disable(identifier)
            outcome = "disabled"
        except ProviderError as error:
            outcome = error.code
        self._audit(
            self._event(attempt, "idp_disable", outcome, actor, context={"key": [provider]})
        )
        return outcome

    def enable(
        self,
        target: str,
        actor: Deployer,
        *,
        environment: str | None = None,
        provider: str | None = None,
        ref: str | None = None,
    ) -> dict:
        """Clear only the local blocker. Starts nothing and revives no approval."""
        with self.store.lock():
            try:
                key, env, _ = self._principal_target(target, environment, provider, ref)
            except ProviderError as error:
                raise GateDenied(error.code, str(error)) from error
            attempt = uuid.uuid4().hex
            snapshot = {"key": json.loads(key), "environment": env}

            def deny(code, message):
                self._audit(
                    self._event(
                        attempt,
                        "enable",
                        "deny",
                        actor,
                        context=snapshot,
                        reasons=[{"code": code, "message": message}],
                    )
                )
                raise GateDenied(code, message, attempt)

            if not self.store.blocker(key, env):
                deny("NOT_BLOCKED", "Principal is not disabled in this environment")
            if not actor.verified:
                deny(
                    "DEPLOYER_UNVERIFIED", "Enable requires a verified actor (agentbreeder login)"
                )
            if not actor.human:
                deny("CHECKER_NOT_HUMAN", "Enable requires a human identity issuer")
            provider_name, authority, principal_id = json.loads(key)
            try:
                result = self.gate.evaluate(
                    self.gate.policy_request(
                        "enable",
                        actor,
                        agent=target,
                        context=DeploymentContext(env, "local"),
                        principal={
                            "provider": provider_name,
                            "authority": authority,
                            "id": principal_id,
                        },
                        risk_tier=None,
                    ),
                    [],
                )
            except PolicyError as error:
                deny(error.code, str(error))
            if not result.allowed:
                deny("ENABLE_NOT_AUTHORIZED", "Policy does not authorize enable")
            if result.obligations:
                deny("OBLIGATION_UNSUPPORTED", "Obligations are unsupported for enable")
            try:
                self.store.remove_blocker(
                    key,
                    env,
                    self._event(
                        attempt,
                        "enable",
                        "allow",
                        actor,
                        context=snapshot,
                        policy_ids=list(result.policy_ids),
                        bundle_digests=dict(result.bundle_digests),
                    ),
                )
            except AuditError as error:
                raise AuditUnavailable() from error
            return {"enabled": snapshot, "state": "enabled", "started": []}

    # -- re-verification -------------------------------------------------------------------

    def verify(self, record: dict) -> str:
        """Re-resolve the principal and re-run identity checks. Never changes workloads."""
        if not record.get("principal_key"):
            return "unbound"
        if record["state"] == "disabled" or self.store.blocker(
            record["principal_key"], record["environment"]
        ):
            return "noncompliant:PRINCIPAL_BLOCKED"
        manifest = Manifest.model_validate(self.store.manifest(record["id"]))
        context = DeploymentContext(record["environment"], manifest.target)
        try:
            principal = self.gate.resolve(manifest.identity.provider, manifest.identity.ref)
        except ProviderError as error:
            return f"unverifiable:{error.code}"
        except Exception:
            # Mirror the gate: an unexpected adapter failure is reported, never raised.
            return "unverifiable:PROVIDER_ERROR"
        if principal.key != record["principal_key"]:
            return "noncompliant:PRINCIPAL_CHANGED"
        failure = self.gate.identity_failure(manifest, principal, context)
        return f"noncompliant:{failure.code}" if failure else "compliant"

    def status(self, identifier: str, *, verify: bool = True) -> dict:
        record = self.store.get(identifier)
        if verify and record.get("principal_key") and record["state"] != "removed":
            compliance = self.verify(record)
            self.store.set_compliance(identifier, compliance)
            record = self.store.get(identifier)
        if record["state"] != "removed":
            try:
                client = self._gateway(self.store.manifest(record["id"]))
                if client is not None:
                    record = {
                        **record,
                        "gateway_key": client.describe(gateway_keys.alias(record["id"]))
                        or {"alias": gateway_keys.alias(record["id"]), "missing": True},
                    }
            except (gateway_keys.GatewayError, OperatorConfigError) as error:
                record = {**record, "gateway_key": {"error": str(error)}}
        return record

    def reconcile(self, actor: Deployer | None = None) -> list[dict]:
        """Mark compliance and retry cleanup of blocked workloads. Never starts anything."""
        actor = actor or os_deployer()
        results = []
        with self.store.lock():
            attempt = uuid.uuid4().hex
            self._audit(self._event(attempt, "reconcile", "started", actor))
            for record in self.store.active_all():
                if record["state"] in ("failed", "starting") and self.boundary.enforced(record):
                    # Reconcile holds the lifecycle lock, so `starting` here means a deploy was
                    # interrupted before verification: never trust it, tear it down.
                    if record["state"] == "starting":
                        self.store.transition(
                            record["id"],
                            "failed",
                            error="[RUNTIME_BOUNDARY_UNVERIFIED] Interrupted before verification",
                            audit=self._event(
                                attempt,
                                "transition",
                                "failed",
                                actor,
                                deployment_id=record["id"],
                            ),
                        )
                    compliance = self._retry_boundary_cleanup(record, attempt, actor)
                    self.store.set_compliance(record["id"], compliance)
                    results.append(
                        {"id": record["id"], "name": record["name"], "compliance": compliance}
                    )
                    continue
                if not record.get("principal_key"):
                    continue
                key, env = record["principal_key"], record["environment"]
                if self.store.blocker(key, env):
                    _, failed = self._stop_blocked(key, env, attempt, actor)
                    compliance = "noncompliant:PRINCIPAL_BLOCKED"
                    if failed:
                        compliance += ":cleanup_failed"
                else:
                    compliance = self.verify(record)
                    if record["state"] == "ready" and self.boundary.enforced(record):
                        outcome = self._ensure_boundary(record, attempt, actor)
                        if outcome != "verified":
                            compliance = "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"
                            if outcome == "cleanup_failed":
                                compliance += ":cleanup_failed"
                self.store.set_compliance(record["id"], compliance)
                results.append(
                    {"id": record["id"], "name": record["name"], "compliance": compliance}
                )
            results += self._revoke_orphan_keys(attempt, actor)
            results += self._prune_mcp_grants(attempt, actor)
            self._audit(
                self._event(
                    attempt,
                    "reconcile",
                    "allow",
                    actor,
                    reasons=[{"code": r["compliance"], "message": r["id"]} for r in results][:100],
                )
            )
        return results

    def _revoke_orphan_keys(self, attempt, actor) -> list[dict]:
        """Revoke keys of deployments THIS store knows are removed, disabled or failed (e.g. a
        revoke that failed while the gateway was down). Unknown aliases are left alone: another
        state dir or operator may share the gateway, and eval judge keys are short-lived."""
        results = []
        for name in self.operator.model.gateways:
            try:
                client = gateway_keys.client(self.operator, name)
                if client is None:
                    continue
                for key_alias in client.aliases():
                    identifier = key_alias.removeprefix(gateway_keys.PREFIX)
                    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
                        continue  # not a deployment key (e.g. an eval judge key)
                    try:
                        record = self.store.get(identifier)
                    except DeploymentError:
                        continue  # not ours
                    if record["state"] not in ("removed", "disabled", "failed"):
                        continue
                    client.revoke(key_alias)
                    self._audit(
                        self._event(
                            attempt,
                            "gateway_key",
                            "revoked",
                            actor,
                            deployment_id=identifier,
                            reasons=[{"code": "GATEWAY_KEY_ORPHAN", "message": key_alias}],
                        )
                    )
                    results.append(
                        {"id": identifier, "name": key_alias, "compliance": "gateway_key_revoked"}
                    )
            except (gateway_keys.GatewayError, OperatorConfigError) as error:
                results.append(
                    {"id": name, "name": name, "compliance": f"gateway_unreachable:{error.code}"}
                    if isinstance(error, gateway_keys.GatewayError)
                    else {"id": name, "name": name, "compliance": "gateway_credential_error"}
                )
        return results

    def _ensure_boundary(self, record, attempt, actor) -> str:
        """Restore the enforcement point (never the agent) and re-verify before any traffic.
        A boundary that cannot be re-verified is torn down."""
        try:
            self.runtime.authorize(record["context"])
            if not self.boundary.service_healthy(record) or not self._sidecar_running(record):
                self.boundary.set_serving(record, False)  # nothing flows until re-verified
                if not self.boundary.service_healthy(record):
                    self.boundary.start_service(record)
                self.runtime.command("container", "start", self.boundary.names(record)["sidecar"])
            self.boundary.verify(record)
            self.boundary.set_serving(record, True)
            return "verified"
        except DeploymentError as error:
            cleaned = self._teardown_quietly(record)
            self.store.transition(
                record["id"],
                "failed",
                error=f"[RUNTIME_BOUNDARY_UNVERIFIED] {error}",
                audit=self._event(
                    attempt,
                    "transition",
                    "failed",
                    actor,
                    deployment_id=record["id"],
                    reasons=[{"code": "RUNTIME_BOUNDARY_UNVERIFIED", "message": str(error)}],
                ),
            )
            return "unverified" if cleaned else "cleanup_failed"

    def _sidecar_running(self, record) -> bool:
        try:
            output = self.runtime.command(
                "container",
                "inspect",
                "--format",
                "{{.State.Running}}",
                self.boundary.names(record)["sidecar"],
            )
        except DeploymentError:
            return False
        return output == "true"

    def _retry_boundary_cleanup(self, record, attempt, actor) -> str:
        """Failed boundary deployments keep retrying a verified teardown until it succeeds."""
        try:
            self.runtime.authorize(record["context"])
            self.boundary.teardown(record)
        except DeploymentError as error:
            self._audit(
                self._event(
                    attempt,
                    "boundary_cleanup",
                    "failed",
                    actor,
                    deployment_id=record["id"],
                    reasons=[{"code": "CLEANUP_FAILED", "message": str(error)}],
                )
            )
            return "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED:cleanup_failed"
        return "noncompliant:RUNTIME_BOUNDARY_UNVERIFIED"

    # -- held tool calls (phase 4) ---------------------------------------------------------

    def decide_call(self, held_id: str, checker: Deployer, *, approve: bool) -> dict:
        """Approve or reject one held tool call. Same checker rules as deploy approvals: a
        verified human who is neither the deployer nor the principal's sole sponsor and whom
        policy authorizes. An approval releases exactly one identical retry."""
        with self.store.lock():
            held = self.store.held(held_id)
            attempt = uuid.uuid4().hex
            action = "call_approval_granted" if approve else "call_approval_rejected"
            context = {"held_call_id": held_id, "server": held["server"], "tool": held["tool"]}

            def deny(code, message):
                self._audit(
                    self._event(
                        attempt,
                        action,
                        "deny",
                        checker,
                        deployment_id=held["deployment_id"],
                        context=context,
                        reasons=[{"code": code, "message": message}],
                        revision_digest=held["revision"],
                    )
                )
                raise GateDenied(code, message, attempt)

            if held["state"] != "pending":
                deny("HELD_NOT_PENDING", "Held call is not pending")
            if datetime.fromisoformat(held["expires"]) <= _now():
                self.store.decide_held(
                    held_id,
                    expected=("pending",),
                    state="expired",
                    audit=self._event(attempt, "call_approval_expired", "expired", checker),
                )
                raise GateDenied("APPROVAL_EXPIRED", "Held call expired", attempt)
            if not checker.verified:
                deny("DEPLOYER_UNVERIFIED", "Checkers must sign in (agentbreeder login)")
            if not checker.human:
                deny("CHECKER_NOT_HUMAN", "Approvals require a human identity issuer")
            if checker.same_person(*held["deployer_names"]):
                deny("SELF_APPROVAL", "The deployer cannot approve their agent's tool call")
            record = self.store.get(held["deployment_id"])
            if (
                record["state"] not in ("starting", "ready")
                or record["revision"] != held["revision"]
            ):
                deny("DEPLOYMENT_REVISION_CHANGED", "Deployment changed since the call was held")
            if self.store.blocker(held["principal_key"], held["environment"]):
                deny("PRINCIPAL_BLOCKED", "Principal is administratively disabled")
            manifest = Manifest.model_validate(self.store.manifest(held["deployment_id"]))
            try:
                principal = self.gate.resolve(manifest.identity.provider, manifest.identity.ref)
            except ProviderError as error:
                deny(error.code, str(error))
            if principal.key != held["principal_key"]:
                deny("PRINCIPAL_CHANGED", "Principal changed since the call was held")
            # The call executes under the attributes pinned at deploy; judge the checker on the
            # same ones, and refuse when the IdP has drifted since (redeploy first).
            try:
                pin = self.boundary.pin(record)
            except (OSError, ValueError):
                deny("RUNTIME_BOUNDARY_UNVERIFIED", "Deployment has no verified boundary pin")
            if pin.get("principal_props") != _authorization_props(principal) or pin.get(
                "risk_tier"
            ) != self.gate.risk_tier(principal):
                deny("PRINCIPAL_CHANGED", "Principal attributes changed since deploy; redeploy")
            active = _active_sponsors(principal)
            if len(active) == 1 and checker.same_person(active[0].id, active[0].name):
                deny("SOLE_SPONSOR_APPROVAL", "The principal's sole sponsor cannot be the checker")
            try:
                result = self.gate.evaluate(
                    self.gate.policy_request(
                        "approve",
                        checker,
                        agent=manifest.name,
                        context=DeploymentContext(held["environment"], manifest.target),
                        principal=principal,
                        risk_tier=pin.get("risk_tier"),
                        manifest=manifest,
                        extra={
                            "tool": f"{held['server']}/{held['tool']}",
                            "arguments_digest": held["arguments_digest"],
                            "deployment_id": held["deployment_id"],
                        },
                    ),
                    list(manifest.policy.restrictions) if manifest.policy else [],
                )
            except PolicyError as error:
                deny(error.code, str(error))
            if not result.allowed:
                deny("CHECKER_NOT_AUTHORIZED", "Policy does not authorize this checker")
            if result.obligations:
                deny("OBLIGATION_UNSUPPORTED", "Obligations are unsupported for approve")
            state = "approved" if approve else "rejected"
            if not self.store.decide_held(
                held_id,
                expected=("pending",),
                state=state,
                checker=checker.label,
                checker_claims=checker.claims(),
                audit=self._event(
                    attempt,
                    action,
                    state,
                    checker,
                    deployment_id=held["deployment_id"],
                    context=context,
                    revision_digest=held["revision"],
                    policy_ids=list(result.policy_ids),
                    bundle_digests=dict(result.bundle_digests),
                ),
            ):
                deny("HELD_NOT_PENDING", "Held call changed concurrently")
            return _held_view(self.store.held(held_id))


def _linked_package(text) -> dict | None:
    """A raw `package` column value as {ref, sha256}, or None when it cannot be read."""
    from agentbreeder.registry import clean

    try:
        value = json.loads(text) if isinstance(text, str) else None
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("ref"), str):
        return None
    return {"ref": clean(value["ref"], name=True)[0], "sha256": value.get("sha256")}


def _mcp_secret(ref: str) -> tuple[str, str]:
    """(ENV, file name) of a deployment's relay token for `mcp/<name>@X.Y.Z`."""
    name = ref.split("/", 1)[1].split("@", 1)[0]
    return f"AGENTBREEDER_MCP_{mcp_relay.env_name(name)}_TOKEN", f"mcp-{name}-token"


def _authorization_props(principal) -> dict:
    """Full, untruncated authorization attributes (pinned for runtime call_tool decisions)."""
    return {
        "provider": principal.provider,
        "authority": principal.authority,
        "id": principal.id,
        "kind": principal.kind,
        "groups": sorted(f"{g.authority}|{g.group_id}" for g in principal.groups),
        "roles": sorted(f"{r.authority}|{r.resource}|{r.role_id}" for r in principal.roles),
    }


def _held_view(held: dict) -> dict:
    return {
        key: held[key]
        for key in ("id", "deployment_id", "server", "tool", "state", "checker", "expires")
    }


def _active_sponsors(principal) -> list:
    """Distinct active human sponsors (case-insensitive ID) so duplicates never count twice."""
    seen, result = set(), []
    for person in principal.sponsors:
        key = person.id.lower()
        if person.active and person.kind == "human" and key not in seen:
            seen.add(key)
            result.append(person)
    return result


def _principal_props(snapshot: dict) -> dict:
    return {
        "provider": snapshot.get("provider"),
        "authority": snapshot.get("authority"),
        "id": snapshot.get("id"),
        "kind": snapshot.get("kind"),
        "groups": ["|".join(g) for g in snapshot.get("groups", [])],
        "roles": ["|".join(r) for r in snapshot.get("roles", [])],
    }
