"""Auto-mirror workspace secrets to a cloud secrets manager at deploy time.

When ``agent.yaml`` declares ``deploy.secrets: [...]`` and the deploy target is
``aws`` or ``gcp``, the deployer calls :func:`mirror_secrets_to_cloud` *after*
RBAC validation and *before* the container starts. This:

1. Reads each declared secret from the **workspace** backend (the developer's
   keychain / env file / Vault).
2. Writes the value to the target cloud's secrets manager under a
   deterministic name: ``agentbreeder/<agent-name>/<secret-name>``.
3. Optionally grants the agent's runtime service account
   ``secretAccessor`` (GCP) / Secrets-Manager-read (AWS) permission on that
   specific secret.
4. Returns a :class:`MirrorResult` describing the cloud-native references the
   deployer should plumb into the container's env (e.g. SecretKeyRef).
5. Records ``secret.mirrored`` audit events for each secret.

Idempotency: repeated calls update the existing cloud secret rather than
duplicating it. The cloud-native name is a deterministic function of agent +
secret name so re-deploys land on the same secret version stream.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from engine.secrets.base import SecretsBackend
from engine.secrets.factory import get_workspace_backend

logger = logging.getLogger(__name__)


# ── data shapes ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CloudSecretRef:
    """A reference to a secret stored in a cloud secrets manager."""

    logical_name: str  # e.g. OPENAI_API_KEY
    cloud_name: str  # e.g. agentbreeder/customer-support/OPENAI_API_KEY
    cloud: str  # "aws" | "gcp" | "vault"
    version: str = "latest"


@dataclass
class MirrorResult:
    """Outcome of a mirror operation."""

    refs: list[CloudSecretRef] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # secrets not found in workspace
    errors: dict[str, str] = field(default_factory=dict)


# ── name helpers ────────────────────────────────────────────────────────────


def deterministic_name(agent_name: str, secret_name: str, *, cloud: str) -> str:
    """Compose the cloud-native secret name.

    Format: ``agentbreeder/<agent-name>/<secret-name>``

    GCP secret IDs cannot contain ``/`` so we substitute ``_`` for that target.
    """
    raw = f"agentbreeder/{agent_name}/{secret_name}"
    if cloud == "gcp":
        return raw.replace("/", "_")
    return raw


# ── main entry point ────────────────────────────────────────────────────────


async def mirror_secrets_to_cloud(
    agent_name: str,
    secret_names: list[str],
    *,
    target_cloud: str,
    runtime_service_account: str | None = None,
    workspace: str | None = None,
    target_options: dict[str, Any] | None = None,
    workspace_backend: SecretsBackend | None = None,
) -> MirrorResult:
    """Mirror the named workspace secrets to ``target_cloud``.

    Args:
        agent_name: The deploying agent's slug-friendly name.
        secret_names: The keys listed under ``deploy.secrets`` in agent.yaml.
        target_cloud: ``"aws"``, ``"gcp"``, or ``"vault"``.
        runtime_service_account: When provided (e.g. an IAM role ARN or a GCP
            SA email) the cloud-native IAM grant is applied to that principal
            for the mirrored secret only — never the workspace-wide policy.
        workspace: Override the workspace name (otherwise the workspace config
            decides).
        target_options: kwargs forwarded to the target backend constructor
            (e.g. ``{"region": "us-east-1"}`` for AWS or ``{"project_id": ...}``
            for GCP).
        workspace_backend: Inject a pre-built workspace backend (used by
            tests; production code uses the workspace config).

    Returns:
        :class:`MirrorResult` listing ``CloudSecretRef`` entries the caller
        should hand to the container's env construction code.
    """
    if target_cloud not in ("aws", "gcp", "vault"):
        msg = f"Unsupported mirror target: {target_cloud}"
        raise ValueError(msg)

    if not secret_names:
        return MirrorResult()

    # 1. Resolve the workspace backend (where developers stored the secrets).
    if workspace_backend is not None:
        ws_backend = workspace_backend
        ws_name = workspace or "default"
    else:
        ws_backend, ws_cfg = get_workspace_backend(workspace=workspace)
        ws_name = ws_cfg.workspace

    # 2. Build the target cloud backend lazily (avoid importing boto3/google
    #    SDKs unless we need them).
    target_backend = _build_target_backend(target_cloud, target_options or {})

    result = MirrorResult()

    for logical in secret_names:
        try:
            value = await ws_backend.get(logical)
        except Exception as exc:
            logger.warning(
                "mirror: could not read secret '%s' from workspace backend %s: %s",
                logical,
                ws_backend.backend_name,
                exc,
            )
            result.errors[logical] = f"workspace read failed: {exc}"
            continue

        if value is None:
            logger.warning(
                "mirror: secret '%s' missing from workspace backend %s — "
                "skipping (set it with: agentbreeder secret set %s)",
                logical,
                ws_backend.backend_name,
                logical,
            )
            result.skipped.append(logical)
            continue

        cloud_name = deterministic_name(agent_name, logical, cloud=target_cloud)
        try:
            await target_backend.set(
                cloud_name,
                value,
                tags={
                    "managed-by": "agentbreeder",
                    "agent": agent_name,
                    "workspace": ws_name,
                    "logical-name": logical,
                },
            )
        except Exception as exc:
            logger.error(
                "mirror: failed to write secret '%s' to %s: %s",
                cloud_name,
                target_cloud,
                exc,
            )
            result.errors[logical] = f"target write failed: {exc}"
            continue

        # 3. Grant the runtime SA secret-accessor on this specific secret.
        if runtime_service_account:
            try:
                await _grant_secret_accessor(
                    target_cloud=target_cloud,
                    cloud_name=cloud_name,
                    principal=runtime_service_account,
                    target_options=target_options or {},
                )
            except Exception as exc:  # non-fatal — log and continue
                logger.warning(
                    "mirror: could not grant '%s' on '%s' to %s: %s",
                    target_cloud,
                    cloud_name,
                    runtime_service_account,
                    exc,
                )

        result.refs.append(
            CloudSecretRef(
                logical_name=logical,
                cloud_name=cloud_name,
                cloud=target_cloud,
            )
        )

        # 4. Audit event — best-effort.
        await _emit_mirror_audit(
            agent_name=agent_name,
            logical=logical,
            cloud_name=cloud_name,
            target_cloud=target_cloud,
            workspace=ws_name,
        )

    logger.info(
        "mirror: %d/%d secrets mirrored for agent '%s' → %s",
        len(result.refs),
        len(secret_names),
        agent_name,
        target_cloud,
    )
    return result


# ── helpers ─────────────────────────────────────────────────────────────────


def _build_target_backend(cloud: str, options: dict[str, Any]) -> SecretsBackend:
    """Build the cloud secrets backend without going through the workspace."""
    from engine.secrets.factory import get_backend

    # Force a non-default prefix-less call so deterministic_name fully owns the
    # cloud-side naming. The cloud backends accept ``prefix=""`` to disable
    # their own prefix logic.
    opts = {"prefix": "", **options}
    return get_backend(cloud, **opts)


async def _grant_secret_accessor(
    *,
    target_cloud: str,
    cloud_name: str,
    principal: str,
    target_options: dict[str, Any],
) -> None:
    """Grant the runtime principal access to the mirrored secret only.

    For tests we keep this best-effort: errors are caught upstream so a
    missing IAM permission doesn't abort the deploy. Real production grants
    are still owned by the deployer's identity provisioner
    (``engine/deployers/identity.py``); this is a defence-in-depth call.
    """
    if target_cloud == "gcp":
        await asyncio.to_thread(_gcp_grant, cloud_name, principal, target_options)
    elif target_cloud == "aws":
        await asyncio.to_thread(_aws_grant, cloud_name, principal, target_options)
    elif target_cloud == "vault":
        # Vault uses policies attached to tokens, not per-secret IAM.
        logger.debug("vault: skipping per-secret grant (managed via Vault policies)")
    else:  # pragma: no cover - validated at top of mirror_secrets_to_cloud
        msg = f"Unknown target_cloud: {target_cloud}"
        raise ValueError(msg)


def _gcp_grant(
    secret_name: str, principal: str, options: dict[str, Any]
) -> None:  # pragma: no cover - exercised via integration tests with mocked SDK
    try:
        from google.cloud import secretmanager  # noqa: F401
    except ImportError:
        logger.debug("gcp grant: google-cloud-secret-manager not installed — skipping")
        return

    project_id = options.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project_id:
        logger.debug("gcp grant: no project_id available — skipping")
        return

    from google.cloud import secretmanager as _sm

    client = _sm.SecretManagerServiceClient()
    resource = f"projects/{project_id}/secrets/{secret_name}"
    member = (
        principal
        if principal.startswith(("serviceAccount:", "user:", "group:"))
        else f"serviceAccount:{principal}"
    )
    policy = client.get_iam_policy(request={"resource": resource})
    bindings = list(policy.bindings)
    binding = next((b for b in bindings if b.role == "roles/secretmanager.secretAccessor"), None)
    if binding is None:
        from google.iam.v1 import policy_pb2

        binding = policy_pb2.Binding(role="roles/secretmanager.secretAccessor", members=[member])
        bindings.append(binding)
    elif member not in binding.members:
        binding.members.append(member)
    else:
        return  # already granted
    policy.bindings.clear()
    policy.bindings.extend(bindings)
    client.set_iam_policy(request={"resource": resource, "policy": policy})


def _aws_grant(
    secret_name: str, principal: str, options: dict[str, Any]
) -> None:  # pragma: no cover - exercised via integration tests with mocked SDK
    try:
        import boto3
    except ImportError:
        logger.debug("aws grant: boto3 not installed — skipping")
        return
    import json as _json

    region = options.get("region") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    client = boto3.client("secretsmanager", region_name=region)

    statement = {
        "Sid": "AgentBreederRuntimeRead",
        "Effect": "Allow",
        "Principal": {"AWS": principal},
        "Action": ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
        "Resource": "*",
    }
    policy_doc = {"Version": "2012-10-17", "Statement": [statement]}
    try:
        client.put_resource_policy(
            SecretId=secret_name,
            ResourcePolicy=_json.dumps(policy_doc),
            BlockPublicPolicy=True,
        )
    except Exception as exc:
        logger.debug("aws grant: put_resource_policy failed: %s", exc)


async def _emit_mirror_audit(
    *,
    agent_name: str,
    logical: str,
    cloud_name: str,
    target_cloud: str,
    workspace: str,
) -> None:
    """Best-effort audit event for ``secret.mirrored``."""
    actor = os.environ.get("AGENTBREEDER_USER") or os.environ.get("USER") or "deployer"
    details = {
        "agent": agent_name,
        "secret_name": logical,
        "cloud_name": cloud_name,
        "cloud": target_cloud,
        "workspace": workspace,
    }
    try:
        from api.services.audit_service import AuditService

        await AuditService.log_event(
            actor=actor,
            action="secret.mirrored",
            resource_type="secret",
            resource_name=logical,
            details=details,
        )
    except Exception:  # pragma: no cover - api package may be unavailable
        logger.info("audit_event secret.mirrored %s", details)
