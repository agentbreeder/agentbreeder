"""`deploy agent/<name>@X.Y.Z`: deploy a released agent package (M13 #763, slice 3; spec
`docs/superpowers/specs/2026-11-13-agent-package-design.md` §7).

A package is a record, not a credential (spec §12.1). It deploys only when this state
directory's audit log has a `release/promoted` row binding this ref, this project, this tree
hash, this image ID and this eval evidence. The identity gate, policy and approvals then apply to
the bound manifest as for any deploy. Steps (§7.2):

1. resolve the ref in the project's own `./registry` only;
2. copy and hash the version into a private temp directory, and load it from that copy;
3. verify the audit chain, then check the release row against the copy;
4. check the released image is still in the local Docker engine;
5. bind the deploy template to the environment (`build.bind`);
6. gate and deploy (`DeploymentService.deploy(..., package=(ref, hash))`).

Nothing here writes the registry or the store; `DeploymentService.deploy` does the writes.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from agentbreeder import hints, package, registry
from agentbreeder.build import BuildError, bind
from agentbreeder.manifest import Manifest
from agentbreeder.release import project_id
from agentbreeder.store import DeploymentError

# Exit 1 rather than 2 (spec §15): the state or the engine, not the request, is the problem.
RUNTIME_CODES = ("AUDIT_CORRUPT", "PACKAGE_IMAGE_MISSING")


class PackageDeployError(DeploymentError):
    """`[CODE] message`; `exit` is 2 unless the code is in RUNTIME_CODES."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {registry.clean(message)[0]}")
        self.code = code
        self.exit = 1 if code in RUNTIME_CODES else 2


def parse(text: str) -> registry.Ref | None:
    """The package ref when `text` fully matches `<kind>/<name>@X.Y.Z`, else None (a manifest
    path). Only `agent/` refs deploy: any other kind is `REF_NOT_DEPLOYABLE`. A file literally
    named like a ref is deployed as `./agent/…` (spec §7.1)."""
    match = registry.REF.fullmatch(text or "")
    if not match or not registry.VERSION.fullmatch(match["version"]):
        return None
    ref = registry.Ref(match["kind"], match["name"], match["version"])
    if ref.kind != package.PACKAGE_KIND:
        raise PackageDeployError(
            "REF_NOT_DEPLOYABLE", f"{ref}: only agent/ versions can be deployed by ref"
        )
    return ref


@dataclass(frozen=True)
class Prepared:
    ref: str
    sha256: str
    record: package.Package
    manifest: Manifest
    environment: str
    notes: tuple[str, ...]


def _shown(path: Path) -> str:
    return registry.clean(str(path), name=True)[0]


def _locate(ref: registry.Ref, project: Path) -> Path:
    """Step 1: the version directory in `<project>/registry` only. The built-in registry,
    AGENTBREEDER_REGISTRY_PATH and remote sources are never searched for `agent/` refs, so a
    package's origin is fixed by construction (spec §7.2 step 1)."""
    root = project / "registry"
    path = root / ref.relative()
    for part in (root, root / ref.kind, root / ref.kind / ref.name, path):
        if part.is_symlink():
            raise PackageDeployError("PACKAGE_INVALID", f"{part.name} must not be a symlink")
    if path.is_dir():
        return path
    for other in registry.roots(project)[1:]:
        if (other / ref.relative()).is_dir():
            raise PackageDeployError(
                "PACKAGE_NOT_HERE",
                f"{ref} is not in this project's ./registry (found in {_shown(other)}); deploy "
                "works only on packages released from this project",
            )
    raise PackageDeployError(
        "PACKAGE_NOT_FOUND",
        f"{ref} is not in {_shown(root)}; check --project, or restore the package from git",
    )


def _reasons(row: dict) -> dict:
    return {item.get("code"): item.get("message") for item in row.get("reasons") or []}


_EVIDENCE = (
    "RELEASE_PACKAGE_SHA256",
    "RELEASE_IMAGE",
    "RELEASE_THRESHOLD",
    "RELEASE_EVALSET",
    "RELEASE_EVAL",
)


def _evidence(store, ref: str, project: Path, sha256: str, record: package.Package, where) -> None:
    """Step 3: the release rows for `ref` must bind this project, tree, image and evals."""
    if not store.verify_audit().get("ok"):
        raise PackageDeployError(
            "AUDIT_CORRUPT",
            "the audit log failed verification; packages cannot be checked. Next: "
            f"`{where.command('audit', 'verify')}`",
        )
    rows = [
        reasons
        for row in store.audit_matching("release", "promoted")
        if (reasons := _reasons(row)).get("RELEASE_PACKAGE") == ref
    ]
    if not rows:
        raise PackageDeployError(
            "PACKAGE_UNRELEASED_HERE",
            f"{ref} was not released from this state directory (--state-dir / --stack?). "
            "Packages from elsewhere cannot be deployed yet. Next: "
            f"`{where.command('release', 'agent.yaml')}`",
        )
    ours = [reasons for reasons in rows if reasons.get("RELEASE_PROJECT") == project_id(project)]
    if not ours:
        raise PackageDeployError(
            "PACKAGE_OTHER_PROJECT",
            f"{ref} in this state was released from a different project with the same agent "
            "name (or this project moved). Next: deploy from that project, use --stack, or "
            "release a new version from here",
        )
    if len({tuple(reasons.get(key) for key in _EVIDENCE) for reasons in ours}) > 1:
        raise PackageDeployError(
            "PACKAGE_AMBIGUOUS",
            f"{ref} has more than one different release row; the audit log may be corrupt. "
            f"Next: `{where.command('audit', 'verify')}`",
        )
    row = ours[0]
    if row.get("RELEASE_PACKAGE_SHA256") != sha256:
        recorded = str(row.get("RELEASE_PACKAGE_SHA256"))
        raise PackageDeployError(
            "PACKAGE_CHANGED",
            f"{ref} changed after it was released ({recorded[:13]}… → {sha256[:13]}…). A "
            "released package must not change: restore it from git, or release a new version",
        )
    evals = record.evals
    try:
        threshold_matches = float(row.get("RELEASE_THRESHOLD")) == evals.threshold
    except (TypeError, ValueError):
        threshold_matches = False
    checks = (
        ("image", row.get("RELEASE_IMAGE") == record.image.id),
        ("threshold", threshold_matches),
        ("eval set", row.get("RELEASE_EVALSET") == evals.set_sha256),
        ("eval run", row.get("RELEASE_EVAL") == f"{evals.run_id} {evals.passed}/{evals.cases}"),
    )
    differing = [name for name, ok in checks if not ok]
    if differing:
        raise PackageDeployError(
            "PACKAGE_EVIDENCE_MISMATCH",
            f"{ref}: the release row and package.json differ in {', '.join(differing)}; the "
            f"audit log may be corrupt. Next: `{where.command('audit', 'verify')}`",
        )


def _bind(record: package.Package, operator, environment: str) -> Manifest:
    """Step 5: identity, secrets and the gateway from the operator config only (spec §5.4)."""
    name = record.agent.name
    env = operator.model.environments.get(environment)
    if env is None:
        raise PackageDeployError("CONTEXT_ENVIRONMENT_UNKNOWN", "Unknown or missing --environment")
    if env.gateway is None:
        raise PackageDeployError(
            "MODEL_ACCESS_UNCONFIGURED", f"Environment {environment} has no gateway for agents"
        )
    identities = env.agents.get(name)
    if identities is None:
        raise PackageDeployError(
            "IDENTITY_UNBOUND",
            f"Environment {environment} has no identity for agent {name}; add "
            f"environments.{environment}.agents.{name}.identity to the operator config",
        )
    try:
        data = bind(
            record.deploy, record.image.id, operator, environment, identities.identity, name
        )
    except BuildError as error:
        raise PackageDeployError(error.code, str(error).split("] ", 1)[-1]) from None
    return Manifest.model_validate(data)


def prepare(
    ref: registry.Ref,
    project: Path,
    service,
    operator,
    environment: str | None,
    *,
    cluster: bool = False,
    where: hints.Where | None = None,
) -> Prepared:
    """Steps 1-5 of spec §7.2; the caller runs step 6 with `service.deploy`."""
    where = where or hints.Where()
    if cluster:
        raise PackageDeployError(
            "PACKAGE_TARGET_UNSUPPORTED",
            "deploy by ref is local only in this version; use --local",
        )
    project = project.resolve()
    path = _locate(ref, project)
    with tempfile.TemporaryDirectory(prefix="agentbreeder-package-") as temporary:
        copy = Path(temporary) / "package"
        try:
            sha256 = registry.copy_and_hash(path, copy)
        except (registry.RegistryError, OSError):
            raise PackageDeployError(
                "PACKAGE_INVALID", f"{ref} could not be copied: it has unsafe or unreadable files"
            ) from None
        try:
            record = package.load(copy, ref.name, ref.version)
        except package.PackageError as error:
            raise PackageDeployError(error.code, f"{ref}: {error.message}") from None
    _evidence(service.store, str(ref), project, sha256, record, where)
    if not service.runtime.image_present(record.image.id):
        raise PackageDeployError(
            "PACKAGE_IMAGE_MISSING",
            f"image {record.image.id[:19]}… of {ref} is not in this Docker engine (pruned?). "
            "Released images cannot be rebuilt bit for bit. Next: "
            f"`{where.command('release', 'agent.yaml')}`",
        )
    env = environment or operator.model.default_environment
    if not env:
        raise PackageDeployError("CONTEXT_ENVIRONMENT_UNKNOWN", "Unknown or missing --environment")
    manifest = _bind(record, operator, env)
    notes = []
    if env != record.evals.environment:
        notes.append(
            f"{ref} was evaluated in {record.evals.environment}, not {env}: this deploy passes "
            f"{env}'s identity gate, policy and approvals, but its evals did not run there"
        )
    return Prepared(str(ref), sha256, record, manifest, env, tuple(notes))
