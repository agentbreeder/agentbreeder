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
An image already promoted (same manifest digest, ready) is a no-op. `--rollback` redeploys the
journal's previous manifest.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from agentbreeder import evals, hints, registry
from agentbreeder.agentfile import load_agent_file
from agentbreeder.build import BuildError, build, docker_build
from agentbreeder.deployer import Deployer
from agentbreeder.deployment import AuditIncomplete, AuditUnavailable, DeploymentService
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


class ReleaseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


def _digest(manifest: Manifest) -> str:
    return manifest.digest()


def _eval_set(agent, project: Path) -> evals.EvalSet:
    """Resolve the agent's eval ref and verify it against agent.lock (never re-locks)."""
    if agent.evals is None:
        raise ReleaseError("EVALS_MISSING", "agent.yaml has no `evals` ref; release needs one")
    ref = registry.Ref.parse(agent.evals.ref)
    lock = Lock.load(project / "agent.lock")
    locked = lock.entries.get(str(ref))
    if locked is None:
        raise ReleaseError(
            "LOCK_STALE", f"{ref} is not in agent.lock; run `{hints.command('build')}` first"
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
) -> Outcome:
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
        eval_set = _eval_set(agent, project)
        details = {"image": built.image, "agent": agent.name, "environment": env}

        current = service.store.active(production.name)
        if current and current["digest"] == _digest(production) and current["state"] == "ready":
            return Outcome(EXIT_OK, f"{built.image} is already in production", details)

        decision = service.preflight(production, env, deployer)
        if not decision.allowed:
            return Outcome(
                EXIT_DENIED, f"[{decision.code}] {decision.message}; production unchanged", details
            )
        if "require_approval" in decision.obligations:
            return Outcome(
                EXIT_APPROVAL,
                "The production deploy needs a second-person approval. Release does not run "
                "approvals yet: deploy the manifest with `agentbreeder deploy` after approval. "
                "Production unchanged.",
                {**details, "manifest": str(built.manifest_path)},
            )
        service.record(
            "release", "started", deployer, [{"code": "RELEASE_IMAGE", "message": built.image}]
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
                    retries=agent.evals.retries if retries is None else retries,
                )
            except evals.EvalError as error:
                service.record(
                    "release", "failed", deployer, [{"code": error.code, "message": str(error)}]
                )
                raise
        finally:
            if deployed.get("id"):
                _removed_or_raise(service, deployed["id"], deployer, "the candidate")
        summary = report.summary()
        report_path = output / f"{agent.name}.eval-{report.run_id}.json"
        output.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps({**summary, "results": report.results}, indent=2) + "\n")
        details |= {"eval": summary, "eval_report": str(report_path)}
        needed = agent.evals.threshold if threshold is None else threshold
        reasons = [
            {
                "code": "RELEASE_EVAL",
                "message": f"{report.run_id} {summary['passed']}/{summary['cases']}",
            },
            {"code": "RELEASE_IMAGE", "message": built.image},
            # What `promote` checks before it accepts this image as released.
            {"code": "RELEASE_MANIFEST", "message": production.digest()},
            {"code": "RELEASE_THRESHOLD", "message": str(needed)},
            {
                "code": "RELEASE_EVALSET",
                "message": Lock.load(project / "agent.lock")
                .entries.get(str(registry.Ref.parse(agent.evals.ref)), {})
                .get("sha256", ""),
            },
        ]
        if report.pass_rate < needed:
            service.record("release", "eval_failed", deployer, reasons)
            details["failed_cases"] = evals.failed_cases(report.results)
            return Outcome(
                EXIT_EVAL,
                f"Eval gate failed: {summary['passed']}/{summary['cases']} passed, needs "
                f"{needed:.0%}; production unchanged",
                details,
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
                _removed_or_raise(service, current["id"], deployer, "the previous production")
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
            service.record("release", "promoted", deployer, reasons)
        details |= {"production": {"id": promoted["id"], "endpoint": promoted["endpoint"]}}
        return Outcome(
            EXIT_OK,
            f"Released {built.image}: {summary['passed']}/{summary['cases']} evals passed",
            details,
        )


def _removed_or_raise(service, identifier: str, deployer, what: str, *, local=True) -> None:
    """`remove` reports a failed teardown as state `failed` instead of raising; a release must
    not carry on (promote, or exit 0) while a container it meant to remove keeps running."""
    result = service.remove(identifier, local=local, actor=deployer)
    if result.get("state") != "removed":
        raise ReleaseError(
            "REMOVE_FAILED",
            f"Could not remove {what} ({identifier}): {result.get('error')}. Production is "
            f"unchanged where possible; run `agentbreeder remove {identifier} --local` and retry",
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
            _removed_or_raise(service, current["id"], deployer, "the current production")
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
        {"production": {"id": restored.get("id"), "endpoint": restored.get("endpoint")}},
    )
