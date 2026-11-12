"""The checks behind `agentbreeder next`: fill the `guide.Guide` contract from local state.

Pure reads (guided spec §9.2 #4, §14.2). Nothing here builds, starts, deploys or writes:
- local state is opened read-only (`Store.open_readonly`, `StackState.open_readonly`);
- Docker is asked once for this stack's containers (`compose ps`), never `services` preview
  (it binds ports and copies files) and never an MCP probe;
- secret values are never read: stored keys are found by their record in `stack.db`, present
  keys by their name in the environment.

`DIR` is the stack: like `--stack DIR`, the deploy state is the one `services up` wrote under
`~/.agentbreeder/stacks/<id>/envs/<environment>/state`, never the default state directory.
Every external read goes through `Probe`, so tests can pin each one and count calls.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import stat
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agentbreeder import guide, hints, safe_text

MAX_BUILD_RECORD = 1024 * 1024
KEY_URLS = {"GEMINI_API_KEY": "https://aistudio.google.com/apikey"}


class GuideError(Exception):
    """The checklist could not be computed: `next` exits 2 with `[CODE] message`."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code


def _doctor_checks() -> list:
    from agentbreeder import doctor

    return [*doctor.check_docker(), *doctor.check_compose(), *doctor.check_path()]


def _containers(state) -> dict[str, dict]:
    from agentbreeder.services.compose import DockerCLI

    docker = DockerCLI()
    docker.authorize()
    return docker.containers(state)


def _clusters() -> list[str]:
    from agentbreeder import cluster

    return cluster._clusters()


@dataclass
class Probe:
    """Every read outside the project and state files, injectable for tests. Unset readers
    use this module's functions, looked up when the probe is made."""

    doctor: Callable[[], list] | None = None
    containers: Callable[[object], dict[str, dict]] | None = None
    clusters: Callable[[], list[str]] | None = None
    environ: Mapping[str, str] | None = None
    stacks_root: Path | None = None
    now: Callable[[], datetime] | None = None

    def __post_init__(self) -> None:
        self.doctor = self.doctor or _doctor_checks
        self.containers = self.containers or _containers
        self.clusters = self.clusters or _clusters
        self.environ = os.environ if self.environ is None else self.environ
        self.now = self.now or (lambda: datetime.now(UTC))


# -- text --------------------------------------------------------------------------------------

_INVISIBLE = guide._INVISIBLE


def _line(text: object, limit: int = 300) -> str:
    """One printable line for a message or note: refused characters become spaces."""
    flat = "".join(
        " "
        if _INVISIBLE.match(char)
        or (char != " " and unicodedata.category(char) in guide._REFUSED_CATEGORIES)
        else char
        for char in str(text)
    )
    return safe_text.excerpt(flat, limit) or "-"


def printable(text: str) -> bool:
    try:
        guide._clean(text)
    except ValueError:
        return False
    return True


# -- the walk ---------------------------------------------------------------------------------


@dataclass
class _Context:
    project: Path
    environment: str
    output: Path
    probe: Probe
    agent: object | None = None
    stack: object | None = None
    stack_state: object | None = None
    store: object | None = None
    store_problem: str | None = None  # STATE_NEEDS_MIGRATION
    docker_ok: bool = False
    build: dict | None = None
    build_done: bool = False
    production: dict | None = None
    released: bool = False
    services_rows: list[dict] = field(default_factory=list)
    services_seen: bool = False
    kube_approval: bool = False

    def where(self) -> hints.Where:
        return hints.Where(stack=self.project, environment=self.environment)


def _step(step_id, state, reason=None, fix=None, data=None) -> dict:
    return {
        "id": step_id,
        "title": guide.TITLES[step_id],
        "required": step_id not in guide.OPTIONAL_STEPS,
        "state": state,
        "reason": reason,
        "fix": fix,
        "data": data,
    }


def _reason(code: str, message: str) -> dict:
    return {"code": code, "message": _line(message)}


def _fix(action=None, command=None, note=None, url=None) -> dict:
    return {
        "action": action,
        "command": command,
        "note": _line(note) if note else None,
        "url": url,
    }


def _available(subcommand: str) -> bool:
    """Whether this install has `subcommand` (so a printed command works as printed)."""
    from agentbreeder import cli

    return subcommand in cli.COMMAND_MODULES


def _not_checked(step_id: str, message: str) -> dict:
    return _step(step_id, "todo", _reason(guide.NOT_CHECKED, message))


def _prerequisites(ctx: _Context) -> dict:
    checks = []
    for check in ctx.probe.doctor():
        if getattr(check, "info", False):
            continue
        checks.append(
            {
                "name": _line(check.name, 80),
                "passed": bool(check.passed),
                "required": bool(check.required),
                "message": _line(check.message),
            }
        )
        if check.name == "Docker":
            ctx.docker_ok = bool(check.passed)
    failed = next((c for c in checks if c["required"] and not c["passed"]), None)
    data = {"checks": checks[:50]}
    if failed is None:
        return _step("prerequisites", "done", data=data)
    return _step(
        "prerequisites",
        "blocked",
        _reason("PREREQUISITE_FAILED", f"{failed['name']}: {failed['message']}"),
        _fix(command=hints.command("doctor"), note=failed["message"]),
        data,
    )


def _project(ctx: _Context) -> dict:
    from agentbreeder import agentfile, registry
    from agentbreeder.services.schema import ServiceError, load_stack

    agent_file = ctx.project / "agent.yaml"
    stack_file = ctx.project / "stack.yaml"
    data = {
        "agent": None,
        "agent_file": str(agent_file) if agent_file.is_file() else None,
        "stack_file": str(stack_file) if stack_file.is_file() else None,
        "error_code": None,
        "newer": [],
    }
    if data["agent_file"] is None:
        empty = not any(ctx.project.iterdir())
        name = (
            ctx.project.name
            if empty and re.fullmatch(agentfile.NAME_PATTERN, ctx.project.name)
            else "hello"
        )
        command = (
            hints.command("new", "agent", name, "--model", "gemini", "--dir", ctx.project)
            if empty
            else hints.command("new", "agent", name, "--model", "gemini")
        )
        if _available("new"):
            fix = _fix(
                "new-agent",
                command,
                f"Or copy an example: {hints.command('example', 'northwind')}",
            )
        else:  # until `new agent` ships in this install, the example is the way in
            example = (
                hints.command("example", "northwind", ctx.project)
                if empty
                else hints.command("example", "northwind")
            )
            fix = _fix("example", example)
        return _step(
            "project",
            "next",
            _reason("PROJECT_MISSING", "No agent.yaml in this directory"),
            fix,
            data,
        )
    try:
        agent = agentfile.load_agent_file(agent_file)
    except agentfile.AgentFileError as error:
        return _invalid(data, "PROJECT_INVALID", error.code, "", ctx, "agent.yaml")
    data["agent"] = agent.name
    ctx.agent = agent
    if data["stack_file"] is None:
        return _step(
            "project",
            "next",
            _reason("PROJECT_MISSING", "No stack.yaml in this directory"),
            _fix(note="Add a stack.yaml for the agent's services (see docs/services.md)"),
            data,
        )
    try:
        ctx.stack = load_stack(stack_file)
    except ServiceError as error:
        return _invalid(data, "PROJECT_INVALID", error.code, "", ctx, "stack.yaml")
    search = registry.roots(ctx.project)
    refs = [(text, "agent.yaml") for text in agent.refs()]
    refs += [(entry.ref, "stack.yaml") for entry in ctx.stack.services.values()]
    for text, target in refs:
        try:
            registry.resolve(registry.Ref.parse(text), search)
        except registry.RegistryError as error:
            return _invalid(data, "REF_UNRESOLVED", error.code, _line(text, 120), ctx, target)
    if agent.evals is None:
        # `release` refuses an agent without evals (EVALS_MISSING): no button would work.
        return _invalid(data, "PROJECT_INVALID", "EVALS_MISSING", "", ctx, "agent.yaml")
    data["newer"] = _newer(agent.refs(), search, agent_file)
    return _step("project", "done", data=data)


def _invalid(
    data: dict, code: str, error_code: str, where: str, ctx: _Context, target: str
) -> dict:
    data["error_code"] = error_code if re.fullmatch(r"[A-Z][A-Z0-9_]{2,39}", error_code) else None
    if error_code == "EVALS_MISSING":
        reason = "agent.yaml has no evals ref; release needs one"
        fix = _fix(note="Add `evals: {ref: eval/NAME@X.Y.Z, threshold: 1.0}` to agent.yaml")
    elif code == "REF_UNRESOLVED":
        reason = f"{where} in {target} does not resolve ({error_code})"
        fix = _fix(note=f"Publish {where} with `registry publish`, or fix the ref in {target}")
    else:
        reason = f"{target} is not valid ({error_code})"
        fix = _fix(command=hints.command("validate", ctx.project / target))
    return _step("project", "blocked", _reason(code, reason), fix, data)


def _semver(text: str) -> tuple:
    core, _, pre = text.partition("-")
    return (*(int(part) for part in core.split(".")), pre == "", pre)


def _newer(refs: list[str], search: list[Path], agent_file: Path) -> list[dict]:
    from agentbreeder import registry

    try:
        lines = agent_file.read_text(encoding="utf-8").splitlines()
        rows = registry.listing(search)
    except (OSError, UnicodeDecodeError, registry.RegistryError):
        return []
    newer = []
    for text in refs:
        ref = registry.Ref.parse(text)
        if ref.kind == "runtime":
            continue  # runtime upgrades are a separate decision, not a starter edit
        versions = [
            row["ref"].rsplit("@", 1)[1]
            for row in rows
            if not row["symlinked"] and row["ref"].startswith(f"{ref.kind}/{ref.name}@")
        ]
        try:
            latest = max(versions, key=_semver, default=ref.version)
            if _semver(latest) <= _semver(ref.version):
                continue
        except ValueError:
            continue
        line = next((i for i, value in enumerate(lines, 1) if str(ref) in value), None)
        newer.append({"ref": str(ref), "latest": latest, "file": str(agent_file), "line": line})
    return newer[:200]


def _gateway(ctx: _Context) -> tuple[str, str] | None:
    """(stack service name, config path) of the stack's LiteLLM gateway, if it has one."""
    name = ctx.stack.environment.gateway
    entry = ctx.stack.services.get(name)
    if entry is None or not entry.ref.startswith("service/litellm@"):
        return None
    config = entry.with_.get("config")
    return (name, config) if isinstance(config, str) else None


def _keys(ctx: _Context) -> dict:
    from agentbreeder import gateway_keys

    if ctx.stack is None:
        return _not_checked("keys", "Create a project first")
    empty = {"required": [], "present": [], "stored": [], "missing": []}
    gateway = _gateway(ctx)
    if gateway is None:
        return _step("keys", "done", data=empty)
    service, config = gateway
    path = ctx.project / config
    try:
        review = gateway_keys.config_review(path)
    except (gateway_keys.GatewayError, OSError, ValueError) as error:
        code = getattr(error, "code", "GATEWAY_CONFIG_INVALID")
        return _step(
            "keys",
            "blocked",
            _reason("KEYS_CONFIG_INVALID", f"{config} is not allowed ({code})"),
            _fix(note=f"Fix {config}; `services up` refuses it as it is"),
            empty,
        )
    required = sorted(
        {
            match.group(1)
            for alias, text in review.providers.items()
            if alias in set(ctx.stack.environment.models)
            and (match := re.search(r"\(([A-Z_][A-Z0-9_]{0,127})\)$", text))
        }
    )
    present = [k for k in required if ctx.probe.environ.get(k)]
    stored = [k for k in required if k not in present and _stored(ctx, service, k)]
    missing = [k for k in required if k not in present and k not in stored]
    data = {"required": required, "present": present, "stored": stored, "missing": missing}
    if not missing:
        return _step("keys", "done", data=data)
    first = missing[0]
    return _step(
        "keys",
        "next",
        _reason("KEYS_MISSING", f"Set {', '.join(missing)} for the model gateway"),
        _fix(
            "set-keys",
            hints.command(
                "services",
                "up",
                ctx.project,
                "--environment",
                ctx.environment,
                "--env-file",
                "<file>",
                "--yes",
            ),
            f"export {first}=<your key>, or put it in a 0600 file for --env-file",
            KEY_URLS.get(first),
        ),
        data,
    )


def _stored(ctx: _Context, service: str, key: str) -> bool:
    if ctx.stack_state is None:
        return False
    try:
        return ctx.stack_state.owner(service, key.lower().replace("_", "-")) == "import"
    except sqlite3.Error:
        return False


def _services(ctx: _Context) -> dict:
    from agentbreeder.services import mcp_stack

    if ctx.stack is None:
        return _not_checked("services", "Create a project first")
    if not ctx.docker_ok:
        return _not_checked("services", "Docker is not reachable")
    names = [*ctx.stack.services, *(mcp_stack.relay_name(s) for s in sorted(ctx.stack.mcp))]
    kinds = {name: "service" for name in ctx.stack.services} | {
        mcp_stack.relay_name(s): "mcp-relay" for s in ctx.stack.mcp
    }
    state = ctx.stack_state
    containers: dict = {}
    if state is not None:
        try:
            containers = ctx.probe.containers(state)
        except Exception as error:  # noqa: BLE001 - any compose failure is SERVICES_UNREADABLE
            code = getattr(error, "code", type(error).__name__)
            return _step(
                "services",
                "blocked",
                _reason("SERVICES_UNREADABLE", f"docker compose ps failed ({_line(code, 40)})"),
                _fix(
                    command=hints.command("doctor"),
                    note="Check Docker Compose v2 and your Docker context, then run next again",
                ),
                {
                    "services": [],
                    "grant_recorded": state.get("grant") is not None,
                    "environment_written": ctx.environment in state.environments(),
                },
            )
    rows = []
    for name in names:
        info = containers.get(name) or {}
        status = info.get("state") or "absent"
        if status not in (
            "created",
            "running",
            "restarting",
            "paused",
            "exited",
            "removing",
            "dead",
        ):
            status = "absent"
        health = info.get("health") or "none"
        if health not in ("healthy", "unhealthy", "starting"):
            health = "none"
        rows.append({"name": name, "kind": kinds[name], "state": status, "health": health})
    ctx.services_rows = rows
    ctx.services_seen = True
    grant = state is not None and state.get("grant") is not None
    written = state is not None and ctx.environment in state.environments()
    data = {"services": rows[:100], "grant_recorded": grant, "environment_written": written}
    fix = _fix(
        "services-up",
        hints.command("services", "up", ctx.project, "--environment", ctx.environment, "--yes"),
    )
    running = [r for r in rows if r["state"] == "running"]
    if not running:
        return _step(
            "services",
            "next",
            _reason("SERVICES_DOWN", "The stack's services are not running"),
            fix,
            data,
        )
    bad = [r["name"] for r in rows if r["state"] != "running" or r["health"] == "unhealthy"]
    if bad:
        return _step(
            "services",
            "next",
            _reason("SERVICES_UNHEALTHY", f"Not running or unhealthy: {', '.join(bad)}"),
            fix,
            data,
        )
    if not written:
        return _step(
            "services",
            "next",
            _reason("ENVIRONMENT_MISSING", f"services up has not run for {ctx.environment}"),
            fix,
            data,
        )
    return _step("services", "done", data=data)


def _build(ctx: _Context) -> dict:
    from agentbreeder import registry

    agent = ctx.agent
    if agent is None or ctx.stack is None:
        return _not_checked("build", "Create a project first")
    agent_file = ctx.project / "agent.yaml"
    record_path = ctx.output / f"{agent.name}.build.json"
    record = _read_json(record_path)
    ctx.build = record
    lock_path = ctx.project / "agent.lock"
    data = {
        "image": None,
        "build_manifest": None,
        "environment": None,
        "agent_yaml_current": False,
        "lock_current": False,
    }
    build_cmd = hints.command("build", agent_file, stack=ctx.project, environment=ctx.environment)
    if record is not None:
        image = record.get("image")
        data["image"] = (
            image
            if isinstance(image, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image)
            else None
        )
        data["build_manifest"] = str(record_path)
        environment = record.get("environment")
        data["environment"] = (
            environment
            if isinstance(environment, str)
            and re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?", environment)
            else None
        )
        data["agent_yaml_current"] = record.get("agent_yaml_sha256") == _sha(agent_file)
        data["lock_current"] = record.get("lock_sha256") == _sha(lock_path)
    if agent.paths():
        return _step(
            "build",
            "next",
            _reason(
                "REF_UNPINNED", "agent.yaml uses a path: ref; release needs a published version"
            ),
            _fix(
                command=hints.command("registry", "publish", "<directory>"),
                note="Publish it, change the ref in agent.yaml to the version, then build",
            ),
            data,
        )
    try:
        locked = set(_lock_entries(lock_path))
        wanted = {str(registry.Ref.parse(text)) for text in agent.refs()}
    except registry.RegistryError:
        locked, wanted = set(), {"?"}
    if locked != wanted:
        return _step(
            "build",
            "next",
            _reason("LOCK_STALE", "agent.lock does not match agent.yaml's refs"),
            _fix(command=build_cmd, note="Run it from the CLI: the console's build is --locked"),
            data,
        )
    fix = _fix("build", build_cmd)
    if record is None or data["image"] is None:
        return _step(
            "build",
            "next",
            _reason("NOT_BUILT", f"No build manifest at {record_path.name}"),
            fix,
            data,
        )
    if not (
        data["agent_yaml_current"]
        and data["lock_current"]
        and data["environment"] == ctx.environment
    ):
        return _step(
            "build",
            "next",
            _reason(
                "BUILD_STALE", "agent.yaml, agent.lock or the environment changed since the build"
            ),
            fix,
            data,
        )
    ctx.build_done = True
    return _step("build", "done", data=data)


def _read_file(path: Path, limit: int = MAX_BUILD_RECORD) -> bytes | None:
    """A regular file, not through a symlink, without blocking on a FIFO, at most `limit`
    bytes; None for anything else."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        data = handle.read(limit + 1)
    return None if len(data) > limit else data


def _sha(path: Path) -> str | None:
    import hashlib

    data = _read_file(path)
    return None if data is None else "sha256:" + hashlib.sha256(data).hexdigest()


def _lock_entries(path: Path) -> dict[str, dict]:
    """`agent.lock`'s entries, read as `_read_file` reads; empty when missing or invalid."""
    from agentbreeder.lock import LOCK_VERSION

    data = _read_file(path)
    try:
        value = json.loads(data) if data is not None else None
    except ValueError:
        return {}
    if not isinstance(value, dict) or value.get("lock_version") != LOCK_VERSION:
        return {}
    entries = value.get("entries")
    return entries if isinstance(entries, dict) else {}


def _read_json(path: Path) -> dict | None:
    data = _read_file(path)
    if data is None:
        return None
    try:
        value = json.loads(data)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _manifest_digest(path: Path) -> str | None:
    from agentbreeder.manifest import ManifestError, load_manifest

    try:
        return load_manifest(path).digest()
    except (ManifestError, OSError, ValueError):
        return None


def _reasons(row: dict) -> dict[str, str]:
    items = row.get("reasons")
    if not isinstance(items, list):
        return {}
    return {
        item.get("code"): item.get("message")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("message"), str)
    }


def _manifest_image(row: dict | None) -> str | None:
    """The image a recorded deployment runs, from its stored manifest JSON."""
    try:
        value = json.loads((row or {}).get("manifest") or "{}")
    except (TypeError, ValueError):
        return None
    image = value.get("image") if isinstance(value, dict) else None
    return image if isinstance(image, str) and image else None


def _latest_release(store, image: str, evalset: str | None, threshold: float) -> str | None:
    """The decision of the newest release row for this image, eval set and threshold."""
    best = None
    for decision in ("promoted", "eval_failed"):
        for row in store.audit_matching("release", decision):
            reasons = _reasons(row)
            try:
                same = (
                    reasons.get("RELEASE_IMAGE") == image
                    and reasons.get("RELEASE_EVALSET") == evalset
                    and float(reasons.get("RELEASE_THRESHOLD", "nan")) == threshold
                )
            except ValueError:
                same = False
            if same and (best is None or row["seq"] > best[0]):
                best = (row["seq"], decision)
            if same:
                break  # newest first
    return best[1] if best else None


def _pending_approvals(ctx: _Context) -> set[str]:
    """Agent names (NAME, NAME-candidate) with an unexpired pending approval here. The rows
    carry the gate's revision digest, not a manifest digest, so they are matched by name."""
    if ctx.store is None or ctx.agent is None:
        return set()
    now = ctx.probe.now().isoformat()
    names = (ctx.agent.name, f"{ctx.agent.name}-candidate")
    rows = ctx.store.db.execute(
        "SELECT agent, expires FROM approvals WHERE state='pending' AND environment=? "
        "AND agent IN (?, ?)",
        (ctx.environment, *names),
    ).fetchall()
    return {row[0] for row in rows if str(row[1]) > now}


def _release(ctx: _Context) -> dict:
    from agentbreeder import evidence, registry

    agent = ctx.agent
    if agent is None or ctx.stack is None:
        return _not_checked("release", "Create a project first")
    release_cmd = hints.command(
        "release", ctx.project / "agent.yaml", stack=ctx.project, environment=ctx.environment
    )
    if ctx.store_problem:
        return _step(
            "release",
            "blocked",
            _reason(
                "STATE_NEEDS_MIGRATION", "The local state needs `migrate` before it can be read"
            ),
            _fix(command=hints.command("migrate", stack=ctx.project, environment=ctx.environment)),
            None,
        )
    store = ctx.store
    production = store.active(agent.name, "local") if store is not None else None
    ctx.production = production
    build_image = (ctx.build or {}).get("image") if ctx.build_done else None
    manifest_path = ctx.output / f"{agent.name}.deploy.yaml"
    production_digest = _manifest_digest(manifest_path)
    data = {
        "production": None,
        "released": False,
        "blocker": False,
        "gate_failed": False,
        "failed_tail": False,
    }
    try:
        from agentbreeder.release import candidate_tail_path

        data["failed_tail"] = (
            store is not None and candidate_tail_path(store.directory, agent.name).is_file()
        )
    except Exception:  # noqa: BLE001 - a missing or odd tail is "no tail"
        data["failed_tail"] = False
    threshold = agent.evals.threshold if agent.evals else 1.0
    try:
        entry = (
            _lock_entries(ctx.project / "agent.lock").get(str(registry.Ref.parse(agent.evals.ref)))
            if agent.evals
            else None
        )
        evalset = entry.get("sha256") if isinstance(entry, dict) else None
    except registry.RegistryError:
        evalset = None
    if store is not None and build_image:
        data["gate_failed"] = (
            _latest_release(store, build_image, evalset, threshold) == "eval_failed"
        )
    if production is not None:
        image = _manifest_image(production) or ""
        endpoint = production.get("endpoint")
        endpoint = (
            endpoint
            if isinstance(endpoint, str)
            and re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", endpoint)
            else None
        )
        data["production"] = {
            "id": production["id"],
            "image": image,
            "state": production["state"],
            "endpoint": endpoint,
            "manifest_current": production_digest is not None
            and production["digest"] == production_digest,
        }
        for row in store.audit_matching("release", "promoted"):
            reasons = _reasons(row)
            if (
                reasons.get("RELEASE_IMAGE") == image
                and reasons.get("RELEASE_MANIFEST") == production["digest"]
            ):
                data["released"] = True
                break
        if production.get("principal_key"):
            data["blocker"] = (
                store.blocker(
                    production["principal_key"], production.get("environment") or ctx.environment
                )
                is not None
            )
    ctx.released = data["released"]
    code = None
    pending = _pending_approvals(ctx)
    approval = _blocked_release(
        "APPROVAL_PENDING",
        "A deploy of this agent is waiting for a second person's approval",
        _fix(note=_approve_note(ctx)),
        data,
    )
    if f"{agent.name}-candidate" in pending:
        return approval
    if agent.name in pending and not _cluster_attached(ctx):
        return approval
    if data["blocker"]:
        if agent.name in pending:
            return approval
        return _blocked_release(
            "PRODUCTION_DISABLED",
            "The kill switch disabled production",
            _fix(
                command=hints.command(
                    "enable",
                    data["production"]["id"],
                    "--environment",
                    ctx.environment,
                    state_dir=store.directory,
                ),
                note=_config_hint(ctx) + ": with it, only an authorised operator can re-enable it",
            ),
            data,
        )
    prod = data["production"]
    if data["gate_failed"]:
        command = (
            hints.command(
                "logs", "--failed", agent.name, stack=ctx.project, environment=ctx.environment
            )
            if data["failed_tail"]
            else release_cmd
        )
        code = (
            "RELEASE_GATE_FAILED",
            "The last release of this build failed the eval gate",
            command,
            "Edit the prompt or evals, publish a new version, change the ref, build and release",
        )
    elif prod is None:
        code = ("NOT_RELEASED", "No production deployment", release_cmd, None)
    elif not build_image or prod["image"] != build_image or not prod["manifest_current"]:
        code = ("PRODUCTION_STALE", "Production does not run the current build", release_cmd, None)
    elif not data["released"]:
        code = (
            "NO_RELEASE_EVIDENCE",
            "Production was deployed without the eval gate",
            release_cmd,
            None,
        )
    elif agent.evals is None:
        code = _unproven(data, ctx, release_cmd)
    else:
        try:
            evidence.released(
                store, agent, ctx.project, {"digest": production["digest"]}, prod["image"]
            )
        except evidence.ReleaseError as error:
            if error.code in ("RELEASE_THRESHOLD_LOWERED", "RELEASE_EVALSET_CHANGED"):
                message = (
                    "That release ran below the threshold agent.yaml asks now"
                    if error.code == "RELEASE_THRESHOLD_LOWERED"
                    else "That release used another eval set"
                )
                code = (error.code, message, release_cmd, None)
            else:
                code = _unproven(data, ctx, release_cmd)
        except (registry.RegistryError, ValueError):
            # An unreadable lock or eval ref: fail closed.
            code = _unproven(data, ctx, release_cmd)
        if code is None and prod["state"] != "ready":
            code = (
                "PRODUCTION_NOT_READY",
                f"Production is recorded {prod['state']}",
                release_cmd,
                None,
            )
    if agent.name in pending and code is not None:
        return approval  # a NAME approval is the release's unless release is otherwise done
    if code is None:
        ctx.kube_approval = agent.name in pending
        return _step("release", "done", data=data)
    return _step(
        "release", "next", _reason(code[0], code[1]), _fix("release", code[2], code[3]), data
    )


def _cluster_attached(ctx: _Context) -> bool:
    return ctx.stack_state is not None and bool(ctx.stack_state.get("kubernetes"))


def _config_hint(ctx: _Context) -> str:
    from agentbreeder.operator_config import CONFIG_ENV

    path = ctx.stack_state.env_dir(ctx.environment) / "operator.yaml" if ctx.stack_state else None
    hint = f"{CONFIG_ENV}={shlex.quote(str(path))}" if path else ""
    if not hint or len(hint) > 200:  # never let the note cap cut a path in half
        return f"set {CONFIG_ENV} to this stack's operator.yaml"
    return hint


def _approve_note(ctx: _Context) -> str:
    return (
        _config_hint(ctx) + ": with it, a second authorised person runs `agentbreeder approve ID` "
        "with --state-dir set to this stack's state"
    )


def _unproven(data: dict, ctx: _Context, release_cmd: str) -> tuple:
    """Evidence that `evidence.released` itself rejects is no evidence."""
    data["released"] = False
    ctx.released = False
    return ("NO_RELEASE_EVIDENCE", "Production's release evidence is not valid", release_cmd, None)


def _blocked_release(code: str, message: str, fix: dict, data: dict) -> dict:
    return _step("release", "blocked", _reason(code, message), fix, data)


def _try(ctx: _Context, release: dict) -> dict:
    if release["state"] != "done":
        return _not_checked("try", "Release first")
    production = release["data"]["production"]
    fix = (
        _fix(
            "invoke",
            hints.command(
                "invoke", ctx.agent.name, "Hello", stack=ctx.project, environment=ctx.environment
            ),
        )
        if _available("invoke")
        else None
    )
    return _step(
        "try",
        "done",
        fix=fix,
        data={"deployment": production["id"], "endpoint": production["endpoint"]},
    )


def _kubernetes(ctx: _Context) -> dict | None:
    from agentbreeder import cluster

    state = ctx.stack_state
    if state is None or not state.get("kubernetes"):
        return None
    if ctx.agent is None or ctx.store_problem:
        return _not_checked("kubernetes", "Release first")
    if not ctx.docker_ok:
        return _not_checked("kubernetes", "Docker is not reachable")
    try:
        up = cluster.NAME in ctx.probe.clusters()
    except Exception:  # noqa: BLE001 - kind missing or failing: the cluster is not up
        up = False
    deployment = ctx.store.active(ctx.agent.name, "kubernetes") if ctx.store else None
    reference = _manifest_image(deployment)
    promoted = None
    if deployment and ctx.store is not None:
        for row in ctx.store.audit_matching("promote", "promoted"):
            reasons = _reasons(row)
            if reference and reasons.get("PROMOTE_IMAGE") == reference:
                promoted = reasons.get("RELEASE_IMAGE")
                break
    production_image = _manifest_image(ctx.production)
    data = {
        "cluster": cluster.NAME,
        "cluster_up": up,
        "deployment": deployment["id"] if deployment else None,
        "promoted_image": promoted,
        "production_image": production_image,
    }
    where = {"stack": ctx.project, "environment": ctx.environment}
    agent_file = ctx.project / "agent.yaml"
    if ctx.kube_approval:
        return _step(
            "kubernetes",
            "blocked",
            _reason(
                "APPROVAL_PENDING",
                "A deploy of this agent is waiting for a second person's approval",
            ),
            _fix(note=_approve_note(ctx)),
            data,
        )
    promote = _fix("promote", hints.command("promote", agent_file, "--to", "kubernetes", **where))
    if not up:
        return _step(
            "kubernetes",
            "next",
            _reason("CLUSTER_DOWN", "The local kind cluster is not running"),
            _fix("cluster-up", hints.command("cluster", "up", **where)),
            data,
        )
    if deployment is None or deployment["state"] != "ready" or promoted is None:
        return _step(
            "kubernetes",
            "next",
            _reason("NOT_PROMOTED", "Production is not on the cluster"),
            promote,
            data,
        )
    if promoted != production_image:
        return _step(
            "kubernetes",
            "next",
            _reason("PROMOTION_STALE", "The cluster runs an older release than production"),
            promote,
            data,
        )
    return _step("kubernetes", "done", data=data)


def _order(steps: list[dict]) -> tuple[list[dict], str | None, bool]:
    """Set next/blocked/todo by the contract's rule, from each check's own result."""
    required = [s for s in steps if s["required"]]
    optional = [s for s in steps if not s["required"]]
    pending = [s for s in (*required, *optional) if s["state"] != "done"]
    for index, step in enumerate(pending):
        code = step["reason"]["code"]
        if index == 0:
            step["state"] = "blocked" if code in guide.BLOCKING else "next"
        else:
            step["state"] = "todo"
            if code == guide.NOT_CHECKED:
                step["fix"] = None
        if step["state"] == "blocked" and step["fix"]:
            step["fix"]["action"] = None
    first = pending[0] if pending else None
    complete = all(s["state"] == "done" for s in required)
    return steps, (first["id"] if first and first["state"] == "next" else None), complete


def _conditions(ctx: _Context, steps: list[dict]) -> list[dict]:
    conditions = []
    for step in steps:
        if step["state"] == "blocked":
            conditions.append(
                {
                    "code": "STEP_BLOCKED",
                    "message": _line(f"{step['title']}: {step['reason']['message']}"),
                    "step": step["id"],
                    "deployment": None,
                    "service": None,
                }
            )
    if ctx.store is not None and ctx.agent is not None:
        for target in ("local", "kubernetes"):
            row = ctx.store.active(ctx.agent.name, target)
            if row is None:
                continue
            if row["state"] == "failed":
                conditions.append(
                    _condition("DEPLOYMENT_FAILED", f"A {target} deployment failed", row)
                )
            compliance = row.get("compliance")
            if isinstance(compliance, str) and compliance.startswith("noncompliant"):
                conditions.append(
                    _condition(
                        "DEPLOYMENT_NONCOMPLIANT",
                        f"A {target} deployment is not compliant as of the last reconcile",
                        row,
                    )
                )
    services = next((s for s in steps if s["id"] == "services"), None)
    if (
        ctx.services_seen
        and services
        and services["data"]
        and services["data"]["grant_recorded"]
        and any(r["state"] != "absent" for r in ctx.services_rows)
    ):
        for row in ctx.services_rows:
            if row["state"] != "running" or row["health"] == "unhealthy":
                conditions.append(
                    {
                        "code": "SERVICES_UNHEALTHY",
                        "message": _line(f"{row['name']} is {row['state']} ({row['health']})"),
                        "step": "services",
                        "deployment": None,
                        "service": row["name"],
                    }
                )
    release = next(s for s in steps if s["id"] == "release")
    production = (release["data"] or {}).get("production")
    if production is not None and not release["data"]["released"]:
        conditions.append(
            {
                "code": "PRODUCTION_UNGATED",
                "message": "Production runs an image no release promoted",
                "step": "release",
                "deployment": production["id"],
                "service": None,
            }
        )
    return conditions[:100]


def _condition(code: str, message: str, row: dict) -> dict:
    return {
        "code": code,
        "message": message,
        "step": None,
        "deployment": row["id"],
        "service": None,
    }


def compute(
    project: Path, environment: str = "dev", output: Path | None = None, probe: Probe | None = None
) -> guide.Guide:
    """The checklist for `project`, validated against the contract."""
    from agentbreeder.services.schema import ServiceError
    from agentbreeder.services.state import StackState
    from agentbreeder.store import DeploymentError, StateBusy, StateNeedsMigration, Store

    probe = probe or Probe()
    if not project.is_dir():
        raise GuideError("PROJECT_DIR_MISSING", "DIR is not a directory")
    project = Path(os.path.normpath(project.absolute()))
    if not printable(str(project)) or project == Path("/"):
        raise GuideError(
            "PROJECT_PATH_UNSUPPORTED",
            "The project path has characters next cannot print; rename the directory",
        )
    output = Path(os.path.normpath((output or project / "deploy").absolute()))
    inside = str(output).startswith(str(project) + "/")
    try:
        inside = inside and output.resolve().is_relative_to(project.resolve())
    except (OSError, RuntimeError):
        inside = False
    if not inside:
        raise GuideError("OUTPUT_OUTSIDE_PROJECT", "--output must be inside DIR")
    ctx = _Context(project, environment, output, probe)
    try:
        steps = [_prerequisites(ctx), _project(ctx)]
        if ctx.stack is not None:
            ctx.stack_state = StackState.open_readonly(ctx.stack.name, project, probe.stacks_root)
            if ctx.stack_state is not None and environment in ctx.stack_state.environments():
                state_dir = ctx.stack_state.env_dir(environment) / "state"
                if (state_dir / "deployments.sqlite3").exists():
                    try:
                        ctx.store = Store.open_readonly(state_dir)
                    except StateNeedsMigration:
                        ctx.store_problem = "STATE_NEEDS_MIGRATION"
                    except StateBusy as error:
                        raise GuideError(
                            "STATE_BUSY", "A running job holds the local state"
                        ) from error
                    except DeploymentError as error:
                        raise GuideError(
                            "STATE_UNREADABLE", "The local state cannot be read"
                        ) from error
        steps.append(_keys(ctx))
        steps.append(_services(ctx))
        steps.append(_build(ctx))
        release = _release(ctx)
        steps.append(release)
        steps.append(_try(ctx, release))
        kube = _kubernetes(ctx)
        if kube is not None:
            steps.append(kube)
        steps, next_id, complete = _order(steps)
        conditions = _conditions(ctx, steps)
    except sqlite3.OperationalError as error:
        if "locked" in str(error):
            raise GuideError("STATE_BUSY", "A running job holds the local state") from None
        raise GuideError("STATE_UNREADABLE", "The local state cannot be read") from None
    except (sqlite3.Error, ServiceError, DeploymentError) as error:
        raise GuideError("STATE_UNREADABLE", "The local state cannot be read") from error
    except OSError as error:
        raise GuideError("PROJECT_UNREADABLE", "A project or state file cannot be read") from error
    finally:
        for handle in (ctx.store, ctx.stack_state):
            if handle is not None:
                handle.close()
    document = {
        "schema_version": guide.SCHEMA_VERSION,
        "project": str(project),
        "environment": environment,
        "complete": complete,
        "next": next_id,
        "steps": steps,
        "conditions": conditions,
    }
    try:
        return guide.Guide.model_validate(document)
    except Exception as error:  # pydantic ValidationError: name the fields, never the values
        locations = []
        for item in getattr(error, "errors", lambda: [])()[:5]:
            locations.append(".".join(str(part) for part in item.get("loc", ())))
        raise GuideError(
            "GUIDE_OUTPUT_INVALID",
            "next computed a checklist that breaks its contract at "
            + (", ".join(locations) or "an unknown field"),
        ) from None
