"""`agentbreeder release agent.yaml`: build, deploy a candidate, run the eval gate, and promote
the exact evaluated image to production only if every gate passes (spec §11, §18.1).

This is orchestration around two ordinary governed deploys (candidate and production), not a
new engine control: someone with deploy rights can still bypass it with plain `deploy`, and the
docs say so. Order of operations:

1. take the release lock (one release per state dir) and `build --locked`
2. preflight the production manifest through the gate (no side effects). A deny exits 2; a
   required approval exits 3. Production is untouched either way.
3. deploy `<name>-candidate` (its own identity and gateway key); pending approval exits 3
4. reset eval fixtures, run the eval set against the candidate, remove the candidate
5. below the threshold: exit 5, production untouched
6. write the release journal (previous deployment + manifest), remove the old production
   (the engine refuses a different spec under an active name), deploy the evaluated image; if
   that fails, redeploy the previous manifest and exit 1
An image already in production is a no-op only when an audited `release/promoted` row matches it
(same image and manifest, threshold and eval set as now). Production running the image without
that evidence (a plain `deploy`) is warned about and evaluated as a candidate; a pass records the
release without replacing production, a fail exits 5 with advice (G-F3). `--rollback` redeploys the
journal's previous manifest.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from agentbreeder import __version__, evals, evidence, hints, package, registry
from agentbreeder.agentfile import load_agent_file
from agentbreeder.build import BuildError, build, docker_build, docker_platform
from agentbreeder.deployer import Deployer
from agentbreeder.deployment import (
    AuditIncomplete,
    AuditUnavailable,
    DeploymentService,
    _linked_package,
)
from agentbreeder.gate import GateDenied
from agentbreeder.lock import Lock
from agentbreeder.manifest import Manifest, load_manifest
from agentbreeder.operator_config import OperatorConfig
from agentbreeder.store import DeploymentError

EXIT_OK, EXIT_RUNTIME, EXIT_DENIED, EXIT_APPROVAL, EXIT_EVAL = 0, 1, 2, 3, 5


@dataclass
class Outcome:
    code: int
    message: str
    details: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        return {"exit_code": self.code, "message": self.message, **self.details}


# Re-exported: callers import ReleaseError from here.
from agentbreeder.evidence import ReleaseError  # noqa: E402


def _digest(manifest: Manifest) -> str:
    return manifest.digest()


def _eval_set(agent, project: Path, where: hints.Where | None = None) -> evals.EvalSet:
    """Resolve the agent's eval ref and verify it against agent.lock (never re-locks)."""
    if agent.evals is None:
        raise ReleaseError("EVALS_MISSING", "agent.yaml has no `evals` ref; release needs one")
    ref = registry.Ref.parse(agent.evals.ref)
    lock = Lock.load(project / "agent.lock")
    locked = lock.entries.get(str(ref))
    if locked is None:
        build_command = (where or hints.Where()).command("build", project / "agent.yaml")
        raise ReleaseError(
            "LOCK_STALE", f"{ref} is not in agent.lock; run `{build_command}` first"
        )
    hit = registry.resolve(ref, registry.roots(project))
    with tempfile.TemporaryDirectory(prefix="agentbreeder-eval-") as temporary:
        copy = Path(temporary) / "eval"
        digest = registry.copy_and_hash(hit.path, copy)
        if digest != locked["sha256"]:
            raise ReleaseError(
                "HASH_MISMATCH", f"{ref}: agent.lock {locked['sha256']}, found {digest}"
            )
        return evals.load_eval_set(copy)


def _journal_path(state: Path, name: str) -> Path:
    """Release journals live in the private (0700) state dir, never in the project tree."""
    directory = state / "releases"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory / f"{name}.json"


@contextmanager
def defer_interrupt() -> Iterator[None]:
    """Hold SIGINT across replace-production steps: an interrupt there must not leave nothing
    running. Delivered as KeyboardInterrupt when the block ends."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    pending: list[int] = []
    previous = signal.signal(signal.SIGINT, lambda number, frame: pending.append(number))
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
    if pending:
        raise KeyboardInterrupt


class _ReleaseLock:
    def __init__(self, state: Path):
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = state / "release.lock"

    def __enter__(self):
        self.descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self.descriptor)
            raise ReleaseError(
                "RELEASE_BUSY", "Another release is running; retry later"
            ) from error
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.descriptor, fcntl.LOCK_UN)
        os.close(self.descriptor)


# -- the agent package (M13 spec §5.5, §6) ---------------------------------------------------

_VERSION = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _package_dir(project: Path, name: str) -> Path:
    return project / "registry" / package.PACKAGE_KIND / name


def _reasons(row: dict) -> dict:
    return {item.get("code"): item.get("message") for item in row.get("reasons") or []}


def _taken(store, project: Path, name: str) -> tuple[set[str], set[str]]:
    """The versions of `agent/<name>` that exist as a directory in the project registry, and
    those a `release/started` row in this state reserves (spec §5.5). Both are taken."""
    directories: set[str] = set()
    for directory in (
        project / "registry",
        _package_dir(project, name).parent,
        _package_dir(project, name),
    ):
        if directory.is_symlink():  # publish refuses these too; say so before any eval runs
            raise ReleaseError(
                "ENTRY_UNSAFE", f"{directory.relative_to(project).as_posix()} is a symlink"
            )
    try:
        with os.scandir(_package_dir(project, name)) as entries:
            directories = {entry.name for entry in entries if _VERSION.fullmatch(entry.name)}
    except (FileNotFoundError, NotADirectoryError):
        pass
    except OSError as error:
        raise ReleaseError(
            "RELEASE_REGISTRY_UNREADABLE", f"registry/agent/{name} is unreadable"
        ) from error
    prefix = f"{package.PACKAGE_KIND}/{name}@"
    reserved: set[str] = set()
    for row in store.audit_matching("release", "started"):
        ref = _reasons(row).get("RELEASE_PACKAGE")
        if (
            isinstance(ref, str)
            and ref.startswith(prefix)
            and _VERSION.fullmatch(ref[len(prefix) :])
        ):
            reserved.add(ref[len(prefix) :])
    return directories, reserved


def _released_refs(store) -> set[str]:
    return {
        ref
        for row in store.audit_matching("release", "promoted")
        if isinstance(ref := _reasons(row).get("RELEASE_PACKAGE"), str)
    }


def _next_free(taken: set[str]) -> str:
    if not taken:
        return "0.1.0"
    major, minor, patch = max(_version_key(version) for version in taken)
    return f"{major}.{minor}.{patch + 1}"


def resolve_version(
    store, project: Path, name: str, requested: str | None, where: hints.Where | None = None
) -> str:
    """`--version`, or the next patch above every taken version (`0.1.0` if none is). A taken
    version is `VERSION_EXISTS`, before any eval runs."""
    directories, reserved = _taken(store, project, name)
    taken = directories | reserved
    if requested is None:
        return _next_free(taken)
    if not isinstance(requested, str) or not _VERSION.fullmatch(requested):
        raise ReleaseError("VERSION_INVALID", "--version must be X.Y.Z, for example 1.4.0")
    if requested in taken:
        ref = f"{package.PACKAGE_KIND}/{name}@{requested}"
        note = ""
        if requested in directories and ref not in _released_refs(store):
            note = (
                "; it was never released (an interrupted release): delete "
                f"registry/agent/{name}/{requested} if you do not need it"
            )
        free = _next_free(taken)
        command = (where or hints.Where()).command(
            "release", project / "agent.yaml", "--version", free
        )
        raise ReleaseError("VERSION_EXISTS", f"{ref} is taken{note}. Next: `{command}`")
    return requested


def project_id(project: Path) -> str:
    """`RELEASE_PROJECT`: a hash of the resolved project path; the path itself is never
    recorded (spec §7.1)."""
    return "sha256:" + hashlib.sha256(str(project.resolve()).encode()).hexdigest()


@dataclass
class _Published:
    ref: str
    sha256: str
    path: Path


def publish_package(
    project: Path,
    agent_path: Path,
    built,
    record: package.Package,
    description: str,
    where: hints.Where | None = None,
) -> _Published:
    """Steps 2.1-2.5 of spec §6: check that `agent.yaml` and `agent.lock` are still the bytes
    `build` used, stage the package privately, and publish it into the project registry."""
    lock_path = project / "agent.lock"
    try:
        written = json.loads(built.build_record_path.read_text())
    except (OSError, ValueError) as error:
        raise ReleaseError("PACKAGE_INPUTS_CHANGED", "the build record is unreadable") from error
    inputs = {}
    for path, key, what in (
        (agent_path, "agent_yaml_sha256", "agent.yaml"),
        (lock_path, "lock_sha256", "agent.lock"),
    ):
        try:
            inputs[what] = path.read_bytes()  # read once: these bytes are hashed and packaged
        except OSError:
            inputs[what] = None
        digest = (
            None if inputs[what] is None else "sha256:" + hashlib.sha256(inputs[what]).hexdigest()
        )
        if digest != written.get(key) or written.get(key) != getattr(record.inputs, key):
            raise ReleaseError(
                "PACKAGE_INPUTS_CHANGED",
                f"{what} changed while the evals ran; nothing was published and production is "
                f"unchanged. Next: `{(where or hints.Where()).command('release', agent_path)}`",
            )
    with tempfile.TemporaryDirectory(prefix="agentbreeder-package-") as temporary:
        staging = Path(temporary) / "package"
        try:
            staged = package.assemble(
                staging, record, inputs["agent.yaml"], inputs["agent.lock"], description
            )
        except package.PackageError as error:
            raise ReleaseError(error.code, error.message) from None
        ref, digest, path = registry.publish(staging, project / "registry", package=True)
    if digest != staged:
        with defer_interrupt():
            shutil.rmtree(path, ignore_errors=True)
        raise ReleaseError(
            "PACKAGE_PUBLISH_MISMATCH",
            f"the published copy of {ref} differs from the staged one; it was removed and "
            "production is unchanged",
        )
    return _Published(str(ref), digest, path)


def _unpublish(published: _Published) -> None:
    """Remove a package this run published but never recorded a release for, only while it
    still holds exactly what was published (spec §6 step 4). The version stays reserved."""
    with defer_interrupt():
        try:
            if registry.copy_and_hash(published.path) != published.sha256:
                return
        except (OSError, registry.RegistryError):
            return
        shutil.rmtree(published.path, ignore_errors=True)
        try:
            published.path.parent.rmdir()  # registry/agent/<name>, only when now empty
        except OSError:
            pass


_PUBLISH_RUNTIME_CODES = ("PACKAGE_INPUTS_CHANGED", "PACKAGE_PUBLISH_MISMATCH")


@dataclass
class _Run:
    published: _Published | None = None
    recorded: bool = False


def _package_record(
    agent, version: str, built, platform: str, project: Path, eval_fields: dict, at: str
) -> package.Package:
    """`package.json` for this release (spec §5.3); every value comes from this run."""
    lock_bytes = (project / "agent.lock").read_bytes()
    try:
        lock_version = json.loads(lock_bytes).get("lock_version")
    except (ValueError, AttributeError):
        lock_version = None
    written = json.loads(built.build_record_path.read_text())
    try:
        return package.Package.model_validate(
            {
                "package_version": package.PACKAGE_VERSION,
                "agent": {"name": agent.name, "version": version, "runtime": agent.runtime},
                "image": {"id": built.image, "platform": platform},
                "inputs": {
                    "agent_yaml_sha256": written.get("agent_yaml_sha256"),
                    "lock_sha256": written.get("lock_sha256"),
                    "lock_version": lock_version,
                    "sources": {},
                },
                "evals": eval_fields,
                "deploy": built.deploy,
                "released": {"at": at, "agentbreeder": __version__},
            }
        )
    except ValidationError as error:
        raise ReleaseError(
            "PACKAGE_INVALID", f"the package record is invalid: {package._fields(error)}"
        ) from None


def _package_reasons(published: _Published, project: Path) -> list[dict]:
    return [
        {"code": "RELEASE_PACKAGE", "message": published.ref},
        {"code": "RELEASE_PACKAGE_SHA256", "message": published.sha256},
        {"code": "RELEASE_PROJECT", "message": project_id(project)},
    ]


def _package_details(published: _Published, project: Path) -> dict:
    return {
        "ref": published.ref,
        "sha256": published.sha256,
        "path": published.path.relative_to(project).as_posix(),
    }


def _link(service, deployment_id: str, published: _Published, deployer, where) -> str | None:
    """Link production to the package just recorded (spec §6 step 3). Called right after the
    `promoted` row, inside its `defer_interrupt` block. A crash between the two, or a link that
    could not be written, leaves the old link, which the next release of the same image repairs
    (`PACKAGE_EXISTS`). Returns the problem to report, or None: the release itself stands."""
    try:
        service.link_package(deployment_id, published.ref, published.sha256, deployer)
    except (DeploymentError, sqlite3.Error) as error:  # AuditUnavailable included
        return (
            f"[PACKAGE_LINK_FAILED] production runs {published.ref} but is not linked to it "
            f"({error}); run `{where.command('release', 'agent.yaml')}` again to repair the link"
        )
    return None


def _outcome(code: int, message: str, details: dict, problem: str | None) -> Outcome:
    if problem is None:
        return Outcome(code, message, details)
    details = {**details, "code": "PACKAGE_LINK_FAILED"}
    return Outcome(EXIT_RUNTIME, f"{message}\n{problem}", details)


def _existing_package(project: Path, name: str, reasons: dict) -> _Published | None:
    """The package a matching release row names, if its directory still holds exactly what
    was released (spec §5.5 step 1); None means "not packaged here: release again"."""
    ref_text, digest = reasons.get("RELEASE_PACKAGE"), reasons.get("RELEASE_PACKAGE_SHA256")
    if reasons.get("RELEASE_PROJECT") != project_id(project):
        return None
    try:
        ref = registry.Ref.parse(ref_text)
    except (registry.RegistryError, TypeError, AttributeError):
        return None
    if ref.kind != package.PACKAGE_KIND or ref.name != name:
        return None
    path = project / "registry" / ref.relative()
    try:
        if path.is_symlink() or registry.copy_and_hash(path) != digest:
            return None
    except (OSError, registry.RegistryError):
        return None
    return _Published(str(ref), digest, path)


def release(
    agent_path: Path,
    operator: OperatorConfig,
    service: DeploymentService,
    deployer: Deployer,
    environment: str | None = None,
    *,
    threshold: float | None = None,
    retries: int | None = None,
    output: Path | None = None,
    builder: Callable[[Path, Path], str] = docker_build,
    evaluate: Callable[..., evals.EvalReport] = evals.run,
    log: Callable[[str], None] = lambda message: None,
    where: hints.Where | None = None,
    version: str | None = None,
    platform: Callable[[str], str] | None = None,
) -> Outcome:
    """`where` is the --stack/--environment/--state-dir the caller ran with, so commands in
    messages work as printed. `version` is `--version` (default: the next free patch);
    `platform` reads the image's `os/arch` for the package (default: `docker image inspect`)."""
    where = where or hints.Where()
    if version is not None and not (isinstance(version, str) and _VERSION.fullmatch(version)):
        raise ReleaseError("VERSION_INVALID", "--version must be X.Y.Z, for example 1.4.0")
    agent = load_agent_file(agent_path)
    project = agent_path.resolve().parent
    env = environment or operator.model.default_environment
    output = (output or project / "deploy").resolve()
    state = service.store.directory
    with _ReleaseLock(state):
        log(f"Building {agent.name} (--locked) ...")
        built = build(agent_path, operator, env, mode="locked", output=output, builder=builder)
        if built.candidate_manifest_path is None:
            raise BuildError(
                "CANDIDATE_UNBOUND",
                f"environments.{env}.agents.{agent.name}.candidate_identity is required",
            )
        production = load_manifest(built.manifest_path)
        candidate = load_manifest(built.candidate_manifest_path)
        eval_set = _eval_set(agent, project, where)
        details = {"image": built.image, "agent": agent.name, "environment": env}
        needed = agent.evals.threshold if threshold is None else threshold
        tries = agent.evals.retries if retries is None else retries
        if not 0 <= tries <= 3:
            raise ReleaseError("RETRIES_INVALID", "--retries must be 0-3")

        current = service.store.active(production.name)
        # G-F3: production running this image is "released" only with an audited release
        # of it. A plain `deploy` (or a release under another threshold or eval set) is
        # evaluated now as a candidate; production itself is never replaced or removed here.
        unreleased = None
        if current and current["digest"] == _digest(production) and current["state"] == "ready":
            try:
                evidence_reasons = evidence.released(
                    service.store,
                    agent,
                    project,
                    current,
                    built.image,
                    threshold=needed,
                    require_package=True,
                )
                existing = _existing_package(project, agent.name, evidence_reasons)
                if existing is None:
                    raise ReleaseError(
                        "PACKAGE_MISSING",
                        "its package is missing, changed or from another project",
                    )
                problem = None
                if _linked_package(current.get("package")) != {
                    "ref": existing.ref,
                    "sha256": existing.sha256,
                }:
                    # A crash between the `promoted` row and the link (spec §5.5 step 1).
                    with defer_interrupt():
                        problem = _link(service, current["id"], existing, deployer, where)
                message = (
                    f"{built.image} is already in production. [PACKAGE_EXISTS] This image was "
                    f"released as {existing.ref}."
                )
                if version is not None and existing.ref != f"agent/{agent.name}@{version}":
                    message += f" --version {version} was not used: a released image is not "
                    message += "released again under another version"
                details["package"] = _package_details(existing, project)
                return _outcome(EXIT_OK, message, details, problem)
            except ReleaseError as error:
                unreleased = current
                warning = (
                    f"production ({current['id']}) runs this image but was not released through "
                    f"the eval gate ({error.code}); evaluating the same image now"
                )
                log(f"WARNING: {warning}")
                details["warnings"] = [warning]

        # Resolved once, after build and before the preflight, and reserved by the `started`
        # row below, so a taken version never costs an eval run (spec §5.5).
        package_version = resolve_version(service.store, project, agent.name, version, where)
        package_ref = f"{package.PACKAGE_KIND}/{agent.name}@{package_version}"
        released_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        decision = service.preflight(production, env, deployer)
        if not decision.allowed:
            return Outcome(
                EXIT_DENIED, f"[{decision.code}] {decision.message}; production unchanged", details
            )
        if "require_approval" in decision.obligations:
            return Outcome(
                EXIT_APPROVAL,
                "The production deploy needs a second-person approval. Release does not run "
                "approvals yet: deploy the manifest with "
                f"`{where.command('deploy', built.manifest_path, '--local')}` after approval. "
                "Production unchanged.",
                {**details, "manifest": str(built.manifest_path)},
            )
        service.record(
            "release",
            "started",
            deployer,
            [
                {"code": "RELEASE_IMAGE", "message": built.image},
                {"code": "RELEASE_PACKAGE", "message": package_ref},
            ],
        )

        stale = service.store.active(candidate.name)
        if stale:
            service.remove(stale["id"], local=True, actor=deployer)
        log(f"Deploying candidate {candidate.name} ...")
        deployed = service.deploy(candidate, local=True, environment=env, deployer=deployer)
        if deployed["state"] == "pending_approval":
            return Outcome(
                EXIT_APPROVAL, "The candidate deploy needs approval; production unchanged", details
            )
        capture: Capture | None = None
        try:
            if deployed["state"] != "ready":
                return Outcome(
                    EXIT_RUNTIME,
                    f"Candidate failed to start: {deployed.get('error')}; production unchanged",
                    details,
                )
            log(f"Running {len(eval_set.cases)} evals against {deployed['endpoint']} ...")
            try:
                report = evaluate(
                    eval_set,
                    deployed["endpoint"],
                    operator,
                    env,
                    state,
                    retries=tries,
                )
            except evals.EvalError as error:
                service.record(
                    "release", "failed", deployer, [{"code": error.code, "message": str(error)}]
                )
                raise
        finally:
            if deployed.get("id"):
                # Read before removal, on every run: once removed, its logs and secret copies
                # are gone (§14.9 R3). Capturing never raises, and an interrupt is held until
                # the candidate is removed; redacting and saving happen after the removal, so
                # neither can delay or skip it.
                with defer_interrupt():
                    capture = capture_candidate(service, deployed["id"])
                    try:
                        _removed_or_raise(
                            service, deployed["id"], deployer, "the candidate", where=where
                        )
                    finally:
                        save_candidate_tail(capture, state, agent.name, details)
        summary = report.summary()
        evalset_sha256 = (
            Lock.load(project / "agent.lock")
            .entries.get(str(registry.Ref.parse(agent.evals.ref)), {})
            .get("sha256", "")
        )
        report_path = output / f"{agent.name}.eval-{report.run_id}.json"
        output.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({**summary, "results": report.results}, indent=2) + "\n")
        details |= {"eval": summary, "eval_report": str(report_path)}
        reasons = [
            {
                "code": "RELEASE_EVAL",
                "message": f"{report.run_id} {summary['passed']}/{summary['cases']}",
            },
            {"code": "RELEASE_IMAGE", "message": built.image},
            # What `promote` checks before it accepts this image as released.
            {"code": "RELEASE_MANIFEST", "message": production.digest()},
            {"code": "RELEASE_THRESHOLD", "message": str(needed)},
            {"code": "RELEASE_EVALSET", "message": evalset_sha256},
        ]
        if report.pass_rate < needed:
            service.record("release", "eval_failed", deployer, reasons)
            details["failed_cases"] = _redacted_cases(report.results, capture)
            message = (
                f"Eval gate failed: {summary['passed']}/{summary['cases']} passed, needs "
                f"{needed:.0%}; production unchanged"
            )
            if unreleased:
                # The caller prints the commands, with the --stack/--state-dir it was given.
                message += "; production is running an image that failed the gate"
                details["unreleased_production"] = {
                    "id": unreleased["id"],
                    "rollback": _has_previous(state, agent.name),
                }
            return Outcome(EXIT_EVAL, message, details)

        # The gate passed: the failed-candidate tail of an earlier run is no longer current.
        discard_candidate_tail(state, agent.name)
        details.pop("candidate_log", None)
        record = _package_record(
            agent,
            package_version,
            built,
            (platform or docker_platform)(built.image),
            project,
            {
                "ref": agent.evals.ref,
                "set_sha256": evalset_sha256,
                "threshold": float(needed),
                "retries": tries,
                "run_id": report.run_id,
                "passed": summary["passed"],
                "cases": summary["cases"],
                "environment": env,
            },
            released_at,
        )
        run = _Run()
        try:
            if unreleased:
                # The evaluated image is production's own: record the release, replace nothing.
                now = service.store.active(production.name)
                if (
                    not now
                    or now["id"] != unreleased["id"]
                    or now["state"] != "ready"
                    or now["digest"] != unreleased["digest"]
                ):
                    raise ReleaseError(
                        "PRODUCTION_CHANGED",
                        "Production changed while the evals ran; nothing was recorded. "
                        "Re-run release",
                    )
                with defer_interrupt():  # spec §6: the publish and its row in one block
                    run.published = publish_package(
                        project, agent_path, built, record, agent.description, where
                    )
                    service.record(
                        "release",
                        "promoted",
                        deployer,
                        reasons + _package_reasons(run.published, project),
                    )
                    run.recorded = True
                    problem = _link(service, unreleased["id"], run.published, deployer, where)
                details |= {
                    "production": {"id": unreleased["id"], "endpoint": unreleased.get("endpoint")},
                    "package": _package_details(run.published, project),
                }
                return _outcome(
                    EXIT_OK,
                    f"Released {built.image}: {summary['passed']}/{summary['cases']} evals "
                    "passed; production already ran this image and was not replaced",
                    details,
                    problem,
                )

            journal = _journal_path(state, agent.name)
            # A plain `deploy` does not take the release lock: if production changed while the
            # evals ran, stop rather than replace (or later "restore" over) someone else's deploy.
            # Checked before the journal is written, so an aborted release never records a stale
            # "previous" for rollback.
            now = service.store.active(production.name)
            if (now and now["id"]) != (current and current["id"]):
                raise ReleaseError(
                    "PRODUCTION_CHANGED",
                    "Production changed while the evals ran; nothing was replaced. Re-run release",
                )
            # Published before the journal: a failure here leaves no stale "previous" for rollback.
            with defer_interrupt():  # never a published package that `finally` does not know of
                run.published = publish_package(
                    project, agent_path, built, record, agent.description, where
                )
            details["package"] = _package_details(run.published, project)
            previous = None
            if current:
                previous = {
                    "id": current["id"],
                    "manifest": service.store.manifest(current["id"]),
                    "environment": current.get("environment"),
                    "context": current.get("context"),
                }
            journal.write_text(
                json.dumps(
                    {
                        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "image": built.image,
                        "eval_run": report.run_id,
                        "previous": previous,
                    },
                    indent=2,
                )
                + "\n"
            )
            with defer_interrupt():
                if current:
                    log(f"Replacing production {current['id']} ...")
                    _removed_or_raise(
                        service, current["id"], deployer, "the previous production", where=where
                    )
                denied = False
                try:
                    promoted = service.deploy(
                        production, local=True, environment=env, deployer=deployer
                    )
                except (AuditUnavailable, AuditIncomplete):
                    raise  # never continue (or restore) when the attempt cannot be audited
                except (GateDenied, DeploymentError) as error:
                    denied = isinstance(error, GateDenied)
                    leftover = service.store.active(production.name)
                    ours = leftover and leftover["digest"] == production.digest()
                    promoted = {
                        "state": "failed",
                        "error": str(error),
                        "id": leftover["id"] if ours else None,
                    }
                if promoted["state"] != "ready":
                    rolled = _restore(service, previous, deployer, promoted)
                    service.record("release", "failed", deployer, reasons)
                    return Outcome(
                        EXIT_DENIED if denied else EXIT_RUNTIME,
                        f"Production deploy failed ({promoted.get('error')}); {rolled}",
                        details,
                    )
                # Inside the deferred block: an interrupt must not lose the record `promote` needs.
                service.record(
                    "release",
                    "promoted",
                    deployer,
                    reasons + _package_reasons(run.published, project),
                )
                run.recorded = True
                problem = _link(service, promoted["id"], run.published, deployer, where)
            details |= {"production": {"id": promoted["id"], "endpoint": promoted["endpoint"]}}
            return _outcome(
                EXIT_OK,
                f"Released {built.image}: {summary['passed']}/{summary['cases']} evals passed",
                details,
                problem,
            )

        except ReleaseError as error:
            if error.code in _PUBLISH_RUNTIME_CODES:  # production unchanged; spec §15: exit 1
                service.record(
                    "release", "failed", deployer, [{"code": error.code, "message": str(error)}]
                )
                return Outcome(EXIT_RUNTIME, str(error), {**details, "code": error.code})
            raise
        except (AuditUnavailable, AuditIncomplete) as error:
            if run.published is not None and not run.recorded:
                # The `promoted` row may or may not exist: keep the package (spec §6 step 4).
                # Deliberately for any audit failure after publish, even one before the row was
                # attempted: the version stays reserved either way, and a kept package with no
                # row is never deployable.
                kept = (
                    f"\n[PACKAGE_KEPT_UNVERIFIED] {run.published.ref} was published but the audit "
                    "log could not be confirmed; it is kept. Next: "
                    f"`{where.command('audit', 'verify')}`"
                )
                error.args = (str(error) + kept,)
                error.package_kept = True  # the CLI exits 1 (spec §15), not 2
                run.published = None
            raise
        finally:
            if run.published is not None and not run.recorded:
                _unpublish(run.published)
                details.pop("package", None)  # never point at a package that was removed


TAIL_LINES = 500


def candidate_tail_path(state: Path, name: str) -> Path:
    """One per agent, beside the release journal in the 0700 state directory."""
    from agentbreeder.agentfile import NAME_PATTERN

    if not re.fullmatch(NAME_PATTERN, name):
        raise ReleaseError("AGENT_NAME", "invalid agent name")
    return state / "releases" / f"{name}.candidate.log"


@dataclass
class Capture:
    """What was read from a candidate before its removal (raw, not yet redacted)."""

    text: str | None = field(default=None, repr=False)
    values: list = field(default_factory=list, repr=False)
    problems: list = field(default_factory=list)


def capture_candidate(service, deployment_id: str) -> Capture:
    """The candidate's last TAIL_LINES log lines and the values needed to redact them, read
    while the container and its secret copies still exist. Never raises."""
    from agentbreeder import redaction

    capture = Capture()
    collected = False
    try:
        state = service.store.directory
        record = service.store.get(deployment_id)
        found = redaction.collect(state, store=service.store)
        # The candidate's own copies (gateway key, MCP tokens, generated secrets). A failed
        # start deletes them from disk, so the deployment service keeps what it read just
        # before that cleanup; otherwise they are read from disk by the deployment's ID.
        retained = getattr(service, "retained_secrets", {}).pop(deployment_id, None)
        if retained is not None and record.get("state") != "ready":
            found.values += retained.values
            found.problems += retained.problems
        else:
            redaction.deployment_copies(state, deployment_id, found)
        capture.values, capture.problems = found.values, found.problems
        collected = True
        service.runtime.authorize(record["context"])
        info = service.runtime.inspect(record)
        if info:
            text, _ = service.runtime.logs(info, TAIL_LINES)
            capture.text = text if isinstance(text, str) else None
    except Exception:  # noqa: BLE001 - a log that cannot be read never fails a release
        capture.text = None
        if not collected:
            # Values are missing: withhold what needs them (the tail, the judges' reasons).
            capture.problems.append("could not collect the values needed for redaction")
    return capture


def save_candidate_tail(capture: Capture, state: Path, name: str, details: dict) -> None:
    """Redact, keep the last TAIL_LINES lines, save (0600) and note the path in `details`; or
    note why nothing was saved. Any earlier tail is replaced or deleted. Never raises."""
    from agentbreeder import redaction

    try:
        discard_candidate_tail(state, name)
        if capture.text is None:
            return
        if capture.problems:
            details["candidate_log_skipped"] = (
                "the candidate log was not saved because it could not be fully redacted: "
                + "; ".join(sorted(set(capture.problems)))
            )
            return
        redacted = redaction.redact(capture.text, capture.values)
        lines = redacted.splitlines()[-TAIL_LINES:]
        path = candidate_tail_path(state, name)
        _journal_path(state, name)  # creates releases/ with 0700
        _write_private(path, ("\n".join(lines) + "\n").encode())
        details["candidate_log"] = str(path)
    except Exception:  # noqa: BLE001 - best effort, after the candidate is gone
        details.pop("candidate_log", None)


def _redacted_cases(results: list[dict], capture: Capture | None) -> list[dict]:
    """Failed cases for printing and JSON. Ids, check names and the judge's reason are redacted
    whole (with the values read before the candidate was removed, its own keys included) and
    only then cut to an excerpt, so a value across the cap leaves no prefix. The reason is
    withheld when redaction is incomplete."""
    from agentbreeder import redaction

    hidden = "(not shown: could not be redacted)"
    try:
        values = capture.values if capture else []
        complete = bool(capture) and not capture.problems
        cases = evals.failed_cases(results, redact=lambda text: redaction.redact(text, values))
        if not complete:
            for case in cases:
                case["reason"] = hidden if case["reason"] else ""
        return cases
    except Exception:  # noqa: BLE001 - never show what could not be redacted
        return [{"id": hidden, "failed_checks": [], "reason": hidden} for _ in results]


def discard_candidate_tail(state: Path, name: str) -> None:
    try:
        candidate_tail_path(state, name).unlink(missing_ok=True)
    except (OSError, ReleaseError):
        pass


def _write_private(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _has_previous(state: Path, name: str) -> bool:
    """Whether `release --rollback` can work: a journal with a previous production."""
    journal = state / "releases" / f"{name}.json"
    try:
        return bool(json.loads(journal.read_text()).get("previous"))
    except (OSError, ValueError, AttributeError):
        return False


def _removed_or_raise(
    service, identifier: str, deployer, what: str, *, local=True, where: hints.Where | None = None
) -> None:
    """`remove` reports a failed teardown as state `failed` instead of raising; a release must
    not carry on (promote, or exit 0) while a container it meant to remove keeps running."""
    result = service.remove(identifier, local=local, actor=deployer)
    if result.get("state") != "removed":
        raise ReleaseError(
            "REMOVE_FAILED",
            f"Could not remove {what} ({identifier}): {result.get('error')}. Production is "
            "unchanged where possible; run "
            f"`{(where or hints.Where()).command('remove', identifier, '--local')}` "
            "and retry",
        )


def _restore(service, previous, deployer, failed, *, local=True) -> str:
    try:
        if failed.get("id"):
            service.remove(failed["id"], local=local, actor=deployer)
        if previous is None:
            return "there was no previous production to restore"
        manifest = Manifest.model_validate(previous["manifest"])
        restored = service.deploy(
            manifest, local=local, environment=previous["environment"], deployer=deployer
        )
        return f"previous production restored ({restored['state']})"
    except (AuditUnavailable, AuditIncomplete):
        raise
    except (GateDenied, DeploymentError) as error:
        return f"restoring the previous production failed ({error}); run `release --rollback`"


def rollback(
    agent_path: Path,
    service: DeploymentService,
    deployer: Deployer,
    environment: str | None = None,
    *,
    output: Path | None = None,
    where: hints.Where | None = None,
) -> Outcome:
    """Redeploy the previous production recorded by the last release. The journal is only a
    pointer: the manifest must name this agent, the environment comes from the caller or the
    operator default, and the gate and the previous image are checked before anything is
    removed."""
    agent = load_agent_file(agent_path)
    journal = _journal_path(service.store.directory, agent.name)
    if not journal.exists():
        raise ReleaseError("NO_RELEASE_JOURNAL", f"No release journal at {journal}")
    previous = json.loads(journal.read_text()).get("previous")
    if not previous:
        raise ReleaseError("NO_PREVIOUS", "The last release had no previous production")
    manifest = Manifest.model_validate(previous["manifest"])
    if manifest.name != agent.name:
        raise ReleaseError("JOURNAL_MISMATCH", "The release journal is for a different agent")
    env = environment or service.operator.model.default_environment
    decision = service.preflight(manifest, env, deployer)
    if not decision.allowed:
        return Outcome(EXIT_DENIED, f"[{decision.code}] {decision.message}; production unchanged")
    if "require_approval" in decision.obligations:
        return Outcome(EXIT_APPROVAL, "The rollback deploy needs approval; production unchanged")
    with _ReleaseLock(service.store.directory):
        current = service.store.active(agent.name)
        service.runtime.authorize(current["context"] if current else None)
        if not service.runtime.image_present(manifest.image):
            # Checked before anything is removed: a pruned local image can't be pulled back.
            return Outcome(
                EXIT_RUNTIME,
                f"The previous image {manifest.image} is no longer available locally; "
                "production unchanged. Rebuild and release instead",
            )
        replaced = None
        if current:
            replaced = {
                "id": current["id"],
                "manifest": service.store.manifest(current["id"]),
                "environment": current.get("environment"),
            }
            _removed_or_raise(
                service, current["id"], deployer, "the current production", where=where
            )
        try:
            restored = service.deploy(manifest, local=True, environment=env, deployer=deployer)
        except (AuditUnavailable, AuditIncomplete):
            raise
        except (GateDenied, DeploymentError) as error:
            restored = {"state": "failed", "error": str(error)}
        if restored["state"] != "ready":
            # Put back what was running, so a failed rollback never leaves nothing running.
            note = _restore(service, replaced, deployer, restored)
            service.record(
                "release",
                "rollback_failed",
                deployer,
                [{"code": "RELEASE_IMAGE", "message": manifest.image}],
            )
            reason = restored.get("error") or restored["state"]
            return Outcome(EXIT_RUNTIME, f"Rollback to {manifest.image} failed ({reason}); {note}")
    service.record(
        "release", "rolled_back", deployer, [{"code": "RELEASE_IMAGE", "message": manifest.image}]
    )
    return Outcome(
        EXIT_OK,
        f"Rolled back to {manifest.image} (ready)",
        {
            "agent": agent.name,
            "production": {"id": restored.get("id"), "endpoint": restored.get("endpoint")},
        },
    )
