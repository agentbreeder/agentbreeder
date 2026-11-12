"""`agentbreeder promote`: deploy the released image to Kubernetes, without rebuilding it (§8).

`release` is what proves an image: this only accepts an image with an audited `release/promoted`
event for the current local production manifest, at no lower a threshold than `agent.yaml` asks
and against the locked eval set. The image is pushed, its digest is read back and compared with
the registry's copy, and the same manifest (target and image changed, nothing else) goes through
the ordinary gated deploy. The eval set then runs against the cluster deployment; a failure removes
it and restores the previous one.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import yaml

from agentbreeder import evals, evidence
from agentbreeder.agentfile import load_agent_file
from agentbreeder.deployer import Deployer
from agentbreeder.deployment import AuditIncomplete, AuditUnavailable, DeploymentService
from agentbreeder.gate import GateDenied
from agentbreeder.manifest import Manifest
from agentbreeder.operator_config import OperatorConfig
from agentbreeder.release import (
    EXIT_APPROVAL,
    EXIT_DENIED,
    EXIT_EVAL,
    EXIT_OK,
    EXIT_RUNTIME,
    Outcome,
    ReleaseError,
    _eval_set,
    _journal_path,
    _ReleaseLock,
    _removed_or_raise,
    _restore,
    defer_interrupt,
)
from agentbreeder.store import DeploymentError

_MANIFESTS = ", ".join(
    (
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    )
)


def _fetch(url: str, timeout: int = 30) -> dict:
    request = Request(url, headers={"Accept": _MANIFESTS})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            return json.load(response)
    except (URLError, OSError, ValueError) as error:
        raise ReleaseError(
            "REGISTRY_UNREADABLE", "Cannot read the image from the registry"
        ) from error


def registry_image_id(reference: str) -> str:
    """The image ID the registry holds for `host/path@sha256:…`: the config digest of the
    manifest (of the first image of an index). A loopback registry only, read over HTTP."""
    name, _, digest = reference.partition("@")
    host, _, path = name.partition("/")
    if urlsplit(f"http://{host}").hostname not in ("localhost", "127.0.0.1"):
        raise ReleaseError("REGISTRY_NOT_LOCAL", "Only a local registry is supported")
    base = f"http://{host}/v2/{path}/manifests/"
    manifest = _fetch(base + digest)
    if "manifests" in manifest:
        images = [m for m in manifest["manifests"] if m.get("platform", {}).get("os") != "unknown"]
        if not images:
            raise ReleaseError("REGISTRY_UNREADABLE", "The registry index has no image")
        digest = images[0]["digest"]
        manifest = _fetch(base + digest)
    try:
        return manifest["config"]["digest"]
    except (KeyError, TypeError) as error:
        raise ReleaseError("REGISTRY_UNREADABLE", "The registry manifest has no config") from error


def push_image(docker, image: str, repository: str, tag: str) -> str:
    """Push the evaluated image ID and return `repository@sha256:…`, verified at the registry."""
    docker.authorize()
    docker.command("image", "tag", image, f"{repository}:{tag}")
    docker.command("image", "push", f"{repository}:{tag}", timeout=900)
    try:
        digests = json.loads(
            docker.command("image", "inspect", "--format", "{{json .RepoDigests}}", image)
        )
        reference = next(
            entry for entry in reversed(digests) if entry.startswith(repository + "@")
        )
    except (ValueError, TypeError, StopIteration) as error:
        raise ReleaseError("REGISTRY_DIGEST", "Docker did not report the pushed digest") from error
    held = registry_image_id(reference)
    pushed = reference.partition("@")[2]
    if image not in (held, pushed):  # classic image store / containerd image store
        raise ReleaseError("REGISTRY_MISMATCH", "The registry holds a different image")
    return reference


def _evidence(service: DeploymentService, agent, project: Path, current: dict, image: str) -> dict:
    """The audited release that promoted this image, or a refusal explaining what is missing."""
    return evidence.released(service.store, agent, project, current, image)


def _already_promoted(service: DeploymentService, reference: str, skipping: bool) -> bool:
    """A deployment that is merely `ready` may have been interrupted before its evals ran. A
    promote that skipped them only counts for another run that skips them too."""
    for row in service.store.audit_matching("promote", "promoted"):
        reasons = row.get("reasons") or []
        skipped = any(item["code"] == "CLUSTER_EVALS_SKIPPED" for item in reasons)
        if {"code": "PROMOTE_IMAGE", "message": reference} in reasons and (
            skipping or not skipped
        ):
            return True
    return False


def promote(
    agent_path: Path,
    operator: OperatorConfig,
    service: DeploymentService,
    deployer: Deployer,
    environment: str | None = None,
    *,
    skip_cluster_evals: bool = False,
    output: Path | None = None,
    pusher: Callable[..., str] = push_image,
    evaluate: Callable[..., evals.EvalReport] = evals.run,
    log: Callable[[str], None] = lambda message: None,
) -> Outcome:
    agent = load_agent_file(agent_path)
    if agent.evals is None:
        raise ReleaseError("EVALS_MISSING", "agent.yaml has no `evals` ref; promote needs one")
    project = agent_path.resolve().parent
    env = environment or operator.model.default_environment
    config = operator.model.environments.get(env or "")
    if config is None or config.kubernetes is None:
        raise ReleaseError("KUBERNETES_UNCONFIGURED", f"Environment {env} has no kubernetes block")
    output = (output or project / "deploy").resolve()
    state = service.store.directory
    with _ReleaseLock(state):
        local = service.store.active(agent.name)
        if local is None or local["state"] != "ready":
            raise ReleaseError("NO_LOCAL_RELEASE", "No ready local release to promote")
        data = service.store.manifest(local["id"])
        image = data["image"]
        reasons = _evidence(service, agent, project, local, image)
        eval_set = _eval_set(agent, project)  # before anything changes: a stale lock stops here
        repository = f"{config.kubernetes.registry}/{agent.name}"
        tag = re.sub(r"[^A-Za-z0-9_.-]", "-", reasons.get("RELEASE_EVAL", "release").split()[0])
        log(f"Pushing {image} to {repository} ...")
        reference = pusher(service.runtime, image, repository, tag)
        manifest = Manifest.model_validate({**data, "target": "kubernetes", "image": reference})
        output.mkdir(parents=True, exist_ok=True)
        manifest_path = output / f"{agent.name}.kubernetes.deploy.yaml"
        manifest_path.write_text(yaml.safe_dump(manifest.model_dump(exclude_none=True)))
        details = {
            "agent": agent.name,
            "environment": env,
            "image": reference,
            "manifest": str(manifest_path),
        }
        audit = [
            {"code": "RELEASE_IMAGE", "message": image},
            {"code": "PROMOTE_IMAGE", "message": reference},
            {"code": "RELEASE_EVAL", "message": reasons.get("RELEASE_EVAL", "")},
            *(
                [{"code": "CLUSTER_EVALS_SKIPPED", "message": "operator flag"}]
                if skip_cluster_evals
                else []
            ),
        ]
        current = service.store.active(agent.name, "kubernetes")
        resuming = bool(
            current and current["digest"] == manifest.digest() and current["state"] == "ready"
        )
        if resuming and _already_promoted(service, reference, skip_cluster_evals):
            return Outcome(EXIT_OK, f"{reference} is already on the cluster", details)
        decision = service.preflight(manifest, env, deployer)
        if not decision.allowed:
            return Outcome(
                EXIT_DENIED, f"[{decision.code}] {decision.message}; cluster unchanged", details
            )
        if "require_approval" in decision.obligations:
            return Outcome(
                EXIT_APPROVAL,
                "The cluster deploy needs a second-person approval: deploy the manifest with "
                "`agentbreeder deploy --cluster` after approval. Cluster unchanged.",
                details,
            )
        service.record("promote", "started", deployer, audit)
        previous = None
        if current and not resuming:
            previous = {
                "id": current["id"],
                "manifest": service.store.manifest(current["id"]),
                "environment": current.get("environment"),
            }
        journal = _journal_path(state, f"{agent.name}.kubernetes")
        recorded = json.loads(journal.read_text()) if journal.exists() else {}
        if resuming and recorded.get("image") == reference:
            previous = recorded.get("previous")  # what the interrupted run replaced
        else:
            journal.write_text(
                json.dumps(
                    {
                        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "image": reference,
                        "previous": previous,
                    },
                    indent=2,
                )
                + "\n"
            )
        deployed = None
        try:
            outcome = _replace(
                service, manifest, env, deployer, current, resuming, previous, audit, details, log
            )
        except KeyboardInterrupt:
            leftover = service.store.active(agent.name, "kubernetes")
            if leftover and leftover["digest"] == manifest.digest():
                _reject(service, previous, deployer, leftover, audit, details, "interrupted")
            raise
        if isinstance(outcome, Outcome):
            return outcome
        deployed = outcome
        details["cluster"] = {"id": deployed["id"], "endpoint": deployed["endpoint"]}
        if not skip_cluster_evals:
            log(f"Running {len(eval_set.cases)} evals against the cluster deployment ...")
            try:
                with service.kubernetes.forward(deployed["endpoint"]) as port:
                    report = evaluate(
                        eval_set,
                        f"http://127.0.0.1:{port}",
                        operator,
                        env,
                        state,
                        retries=agent.evals.retries,
                    )
            except (evals.EvalError, DeploymentError) as error:
                return _reject(service, previous, deployer, deployed, audit, details, str(error))
            except KeyboardInterrupt:
                _reject(service, previous, deployer, deployed, audit, details, "interrupted")
                raise
            summary = report.summary()
            details["eval"] = summary
            audit.append(
                {
                    "code": "PROMOTE_EVAL",
                    "message": f"{report.run_id} {summary['passed']}/{summary['cases']}",
                }
            )
            if report.pass_rate < agent.evals.threshold:
                return _reject(
                    service,
                    previous,
                    deployer,
                    deployed,
                    audit,
                    details,
                    f"{summary['passed']}/{summary['cases']} passed",
                )
        service.record("promote", "promoted", deployer, audit)
        result = details.get("eval")
        passed = f": {result['passed']}/{result['cases']} evals passed" if result else ""
        return Outcome(EXIT_OK, f"Promoted {reference} to the cluster{passed}", details)


def _replace(service, manifest, env, deployer, current, resuming, previous, audit, details, log):
    """Swap the cluster deployment with interrupts held. Returns the ready deployment, or the
    Outcome that ends the promote."""
    with defer_interrupt():
        if current and not resuming:
            log(f"Replacing the cluster deployment {current['id']} ...")
            _removed_or_raise(
                service, current["id"], deployer, "the previous deployment", local=False
            )
        denied = False
        log("Deploying to the cluster ...")
        try:
            deployed = service.deploy(manifest, local=False, environment=env, deployer=deployer)
        except (AuditUnavailable, AuditIncomplete):
            raise
        except (GateDenied, DeploymentError) as error:
            denied = isinstance(error, GateDenied)
            leftover = service.store.active(manifest.name, "kubernetes")
            ours = leftover and leftover["digest"] == manifest.digest()
            deployed = {
                "state": "failed",
                "error": str(error),
                "id": leftover["id"] if ours else None,
            }
        if deployed["state"] != "ready":
            restored = _restore(service, previous, deployer, deployed, local=False)
            service.record("promote", "failed", deployer, audit)
            return Outcome(
                EXIT_DENIED if denied else EXIT_RUNTIME,
                f"Cluster deploy failed ({deployed.get('error')}); {restored}",
                details,
            )
    return deployed


def _reject(service, previous, deployer, deployed, audit, details, reason: str) -> Outcome:
    """The evals failed on the cluster: take this deployment out and restore the previous one."""
    with defer_interrupt():
        restored = _restore(service, previous, deployer, deployed, local=False)
    service.record("promote", "eval_failed", deployer, audit)
    return Outcome(
        EXIT_EVAL,
        f"Cluster evals failed ({reason}); the cluster deployment was replaced and {restored}",
        details,
    )
