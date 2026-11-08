"""`agentbreeder services up/status/logs/env/down` and `--stack` resolution.

`up` (spec §5): lock and validate; show and confirm the grant; import and generate secrets;
start each service in dependency order (Compose, health, `init`, `bootstrap`); write the demo
operator config. Every value stays in the stack state dir; nothing is written to the repo except
`stack.lock`.
"""

from __future__ import annotations

import re
import shlex
import socket
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentbreeder import env_import, registry, secret_files
from agentbreeder.services import operator as operator_writer
from agentbreeder.services.compose import PROBE_IMAGE, DockerCLI
from agentbreeder.services.lock import LockedStack, StackLock, lock_stack
from agentbreeder.services.plan import Plan, grant_digest
from agentbreeder.services.schema import ServiceError, Stack, load_stack
from agentbreeder.services.state import StackState, stacks_root

STACK_FILE = "stack.yaml"
LOCK_FILE = "stack.lock"


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def load(project: Path) -> Stack:
    path = project / STACK_FILE
    if not path.is_file():
        raise ServiceError("STACK_MISSING", f"no {STACK_FILE} in {project}")
    return load_stack(path)


def open_state(project: Path, root: Path | None = None) -> tuple[Stack, StackState]:
    project = project.resolve()
    stack = load(project)
    return stack, StackState(stack.name, project, root)


@dataclass(frozen=True)
class StackContext:
    stack_id: str
    environment: str
    operator_config: Path
    state_dir: Path


def context(project: Path, environment: str | None, root: Path | None = None) -> StackContext:
    """What `--stack DIR` means: that stack's generated operator config and deploy state."""
    stack, state = open_state(project, root)
    try:
        written = state.environments()
        if environment is None:
            if len(written) != 1:
                raise ServiceError(
                    "STACK_ENVIRONMENT",
                    "pass --environment"
                    + (f" (one of {', '.join(written)})" if written else "")
                    + "; run `agentbreeder services up` first if none is listed",
                )
            environment = written[0]
        if environment not in written:
            raise ServiceError(
                "STACK_NOT_UP",
                f"stack {state.id} has no operator config for {environment}; run "
                f"`agentbreeder services up --environment {environment}`",
            )
        return StackContext(
            state.id,
            environment,
            state.env_dir(environment) / "operator.yaml",
            state.deploy_state(environment),
        )
    finally:
        state.close()


class Engine:
    def __init__(
        self,
        project: Path,
        *,
        docker: DockerCLI | None = None,
        root: Path | None = None,
        log: Callable[[str], None] = lambda message: None,
        allocate: Callable[[], int] = allocate_port,
    ):
        self.project = project.resolve()
        self.stack, self.state = open_state(self.project, root or stacks_root())
        self.docker = docker or DockerCLI()
        self.log = log
        self.allocate = allocate
        self._pending_lock = None
        self._probe_pulled = False

    def close(self) -> None:
        self.state.close()

    # -- planning -------------------------------------------------------------------------------

    def _lock(self, mode: str) -> LockedStack:
        lock_path = self.project / LOCK_FILE
        lock = StackLock.load(lock_path)
        with tempfile.TemporaryDirectory(dir=self.state.directory, prefix=".lock-") as scratch:
            locked = lock_stack(
                self.stack,
                project_dir=self.project,
                search=registry.roots(self.project),
                lock=lock,
                context=Path(scratch),
                mode=mode,
            )
            # Content-addressed copies outlive the scratch dir and keep paths stable.
            locked.service_dirs = {
                name: self.state.copy_file(path) for name, path in locked.service_dirs.items()
            }
            locked.files = {
                name: {key: self.state.copy_file(path) for key, path in files.items()}
                for name, files in locked.files.items()
            }
        # Saved only once the grant is confirmed (up), never by a declined or failed plan.
        self._pending_lock = (lock, lock_path) if locked.changed else None
        return locked

    def plan(self, mode: str = "plain") -> Plan:
        locked = self._lock(mode)
        host_ports: dict[str, dict[str, int]] = {}
        chosen: set[int] = set()
        for name in locked.resolved.order:
            for port_name, port in sorted(locked.specs[name].ports.items()):
                if "host" in port.scopes:
                    number = self.state.host_port(name, port_name, self.allocate, chosen)
                    chosen.add(number)
                    host_ports.setdefault(name, {})[port_name] = number
        plan = Plan(self.state.id, self.state.project, locked, host_ports, secrets={})
        plan.files = locked.files
        for name, directory in locked.service_dirs.items():
            files = directory / "files"
            if files.is_dir():
                plan.service_files[name] = files
            spec = locked.specs[name]
            ref_hash = locked.hashes[str(registry.Ref.parse(self.stack.services[name].ref))]
            plan.images[name] = spec.image or (
                f"agentbreeder-svc/{self.state.id}-{name}:{ref_hash[:16]}"
            )
        return plan

    # -- secrets ----------------------------------------------------------------------------------

    def _volumes_exist(self, plan: Plan, service: str) -> bool:
        return any(
            self.docker.volume_identity(plan.volume_name(service, volume)) is not None
            for volume in plan.spec(service).volumes
        )

    def secrets(self, plan: Plan, env_file: Path | None) -> None:
        """Import and generate every service's own secrets; reuse only what up recorded."""
        for name in plan.locked.resolved.order:
            spec = plan.spec(name)
            own: dict[str, bytes] = {}
            params = plan.locked.resolved.params[name]
            for secret, source in sorted(spec.secrets.items()):
                keys = (
                    [f"{secret}.{item}" for item in params.get(source.per) or []]
                    if source.per
                    else [secret]
                )
                for key in keys:
                    value = self._secret(plan, name, key, source, env_file)
                    if value is not None:
                        own[key] = value
            if spec.bootstrap is not None:
                written = self.state.read_secret(name, spec.bootstrap.writes_secret)
                if written is not None:
                    own[spec.bootstrap.writes_secret] = written
            plan.secrets[name] = own
            self.docker.redactions.extend(own.values())

    def _secret(self, plan, service, key, source, env_file) -> bytes | None:
        current = self.state.read_secret(service, key)
        if source.import_:
            origin = self.state.owner(service, key)
            if current is not None and origin != "import":
                raise ServiceError("SECRET_NOT_OWNED", f"service {service}: secret {key}")
            try:
                value = env_import.lookup(source.import_, env_file)
            except env_import.EnvImportError as error:
                raise ServiceError(error.code, str(error)) from None
            if value:
                data = value.encode()
                if data != current:
                    self.state.write_secret(service, key, data, "import")
                return data
            if current is not None:
                return current  # imported on an earlier run
            if source.optional:
                return None
            raise ServiceError(
                "SECRET_SOURCE_MISSING",
                f"service {service} imports {source.import_}; set it in the environment or "
                "pass --env-file",
            )
        if current is not None:
            if self.state.owner(service, key) != "generate":
                raise ServiceError("SECRET_NOT_OWNED", f"service {service}: secret {key}")
            return current
        # Lost, not new: up recorded it once and its data volume still exists.
        if self.state.owner(service, key) is not None and self._volumes_exist(plan, service):
            raise ServiceError(
                "SECRET_LOST",
                f"service {service}: secret {key} is gone but its data volume exists; restore "
                "it or remove the data with `services down --volumes --yes`",
            )
        data = secret_files.generate_token()
        self.state.write_secret(service, key, data, "generate")
        return data

    # -- up ---------------------------------------------------------------------------------------

    def up(
        self,
        environment: str,
        *,
        mode: str = "plain",
        env_file: Path | None = None,
        confirm: Callable[[dict], bool],
    ) -> dict:
        self.state.env_dir(environment)  # validates the name before anything runs
        plan = self.plan(mode)
        grant = plan.grant(environment)
        digest = grant_digest(grant)
        if self.state.get("grant") != digest and not confirm(grant):
            raise ServiceError(
                "GRANT_NOT_CONFIRMED",
                "the stack's grant is new or changed; review it and pass --yes to confirm",
            )
        if self._pending_lock is not None:
            lock, path = self._pending_lock
            lock.save(path)
        for service, ports in plan.host_ports.items():
            for port_name, number in ports.items():
                self.state.record_port(service, port_name, number)
        self.docker.authorize()
        self.secrets(plan, env_file)
        for name in plan.locked.resolved.order:
            self.docker.redactions.extend(plan.environment(name).values())
        before = self.docker.containers(self.state)
        rendered: dict[str, dict] = {}
        mounts: set[Path] = set()
        for name in plan.locked.resolved.order:
            self._image(plan, name)
            self._start(plan, name, rendered, mounts)
            self._steps(plan, name, rendered, mounts)
        self.state.prune_mounts(mounts)
        config = operator_writer.write(plan, self.state, environment)
        self.state.put("grant", digest)
        after = self.docker.containers(self.state)
        actions = {}
        for name in plan.locked.resolved.order:
            old, new = before.get(name), after.get(name, {})
            actions[name] = (
                "created"
                if old is None
                else "unchanged"
                if old["id"] == new.get("id")
                else "recreated"
            )
        return {
            "stack": self.state.id,
            "environment": environment,
            "services": actions,
            "operator_config": str(config),
            "state_dir": str(self.state.deploy_state(environment)),
            "warnings": plan.locked.warnings,
            "lock_changed": plan.locked.changed,
        }

    def _image(self, plan: Plan, name: str) -> None:
        spec = plan.spec(name)
        if spec.image is not None:
            return
        tag = plan.images[name]
        if not self.docker.image_exists(tag):
            context = plan.locked.service_dirs[name]
            _check_dockerfile(context / "Dockerfile")
            self.log(f"Building {name} ({tag})")
            self.docker.build(context, tag)

    def _start(self, plan: Plan, name: str, rendered: dict, mounts: set[Path]) -> None:
        values = plan.mounted(name)
        mount = self.state.mount_dir(name, values) if values else None
        if mount is not None:
            mounts.add(mount)
        rendered[name] = plan.compose_service(name, mount)
        self.docker.write_compose(self.state, plan.compose_file(rendered))
        self.log(f"Starting {name}")
        self.docker.up(self.state, name)
        health = plan.spec(name).health
        if health is not None and health.http is not None:
            port = health.port or next(iter(plan.spec(name).ports))
            url = f"http://{plan.address(name, port, 'internal')}{health.http}"
            if not self._probe_pulled:
                self.docker.pull(PROBE_IMAGE)
                self._probe_pulled = True
            if not self.docker.probe(self.state, plan.networks["stack"], url):
                raise ServiceError(
                    "SERVICE_UNHEALTHY",
                    f"service {name} did not become healthy; see `agentbreeder services logs "
                    f"{name}`",
                )

    def _step_run(self, plan: Plan, name: str, step, label: str, mounts: set[Path], capture):
        values = plan.mounted(name, set(step.secrets))
        mount = self.state.mount_dir(f"{name}-{label}", values) if values else None
        if mount is not None:
            mounts.add(mount)
        binds = [(mount, "/run/secrets")] if mount is not None else []
        if name in plan.service_files:
            binds.append((plan.service_files[name], "/files"))
        binds += [(path, f"/run/params/{key}") for key, path in sorted(plan.files[name].items())]
        return self.docker.oneshot(
            self.state,
            label=f"{name}.{label}",
            network=plan.networks["stack"],
            image=plan.images[name],
            command=list(step.command),
            user=plan.user(name),
            mounts=binds,
            env=plan.environment(name),
            capture=capture,
        )

    def _steps(self, plan: Plan, name: str, rendered: dict, mounts: set[Path]) -> None:
        spec = plan.spec(name)
        if spec.init is not None:
            self.log(f"Running {name} init")
            self._step_run(plan, name, spec.init, "init", mounts, capture=False)
        if spec.bootstrap is None:
            return
        identities = sorted(
            self.docker.volume_identity(plan.volume_name(name, volume)) or ""
            for volume in spec.volumes
        )
        marker = "|".join(identities) or "no-volume"
        target = spec.bootstrap.writes_secret
        if self.state.get(f"bootstrap:{name}") == marker and target in plan.secrets[name]:
            return
        self.log(f"Running {name} bootstrap")
        value = self._step_run(plan, name, spec.bootstrap, "bootstrap", mounts, capture=True)
        self.state.write_secret(name, target, value, "bootstrap")
        self.docker.redactions.append(value)
        changed = plan.secrets[name].get(target) != value
        plan.secrets[name][target] = value
        self.state.put(f"bootstrap:{name}", marker)
        if changed and target in spec.mounted_secrets():
            self._start(plan, name, rendered, mounts)  # the service mounts what it minted

    # -- status / logs / down ---------------------------------------------------------------------

    def status(self) -> dict:
        self.docker.authorize()  # the same Docker context as up and down
        containers = self.docker.containers(self.state)
        ports = self.state.ports()
        services = {}
        for name in self.stack.services:
            info = containers.get(name)
            services[name] = {
                "state": info["state"] if info else "absent",
                "health": (info or {}).get("health") or "",
                "host_ports": ports.get(name, {}),
            }
        return {
            "stack": self.state.id,
            "environments": self.state.environments(),
            "services": services,
        }

    def logs(self, service: str, tail: int) -> str:
        from agentbreeder.services.compose import redact

        if service not in self.stack.services:
            raise ServiceError("SERVICE_UNKNOWN", "no such service in stack.yaml")
        self.docker.authorize()
        self.docker.redactions.extend(self.state.known_secrets())
        output = self.docker.logs(self.state, service, max(1, min(tail, 10000)))
        return redact(output, self.docker.redactions)

    def active_deployments(self) -> list[str]:
        from agentbreeder.store import Store

        active = []
        for environment in self.state.environments():
            directory = self.state.deploy_state(environment)
            if not (directory / "deployments.sqlite3").exists():
                continue
            store = Store(directory)
            try:
                active += [f"{environment}/{row['name']}" for row in store.active_all()]
            finally:
                store.close()
        return active

    def down(self, *, volumes: bool = False) -> dict:
        active = self.active_deployments()
        if active:
            raise ServiceError(
                "STACK_IN_USE",
                f"remove these deployments first (agentbreeder remove ID --local --stack .): "
                f"{', '.join(active)}",
            )
        self.docker.authorize()
        self.docker.down(self.state)
        removed = []
        if volumes:
            for name in self.docker.stack_volumes(self.state):
                self.docker.remove_volume(name)
                removed.append(name)
        return {"stack": self.state.id, "volumes_removed": removed}

    def env_lines(self, environment: str | None) -> list[str]:
        resolved = context(self.project, environment, self.state.directory.parent)
        return [
            f"export AGENTBREEDER_OPERATOR_CONFIG={shlex.quote(str(resolved.operator_config))}",
            f"export AGENTBREEDER_STATE_DIR={shlex.quote(str(resolved.state_dir))}",
        ]


_DIRECTIVE = re.compile(r"#\s*[A-Za-z]+\s*=")


def _instructions(text: str) -> list[tuple[int, str]]:
    """Logical lines as BuildKit reads them (backslash escape): continuations joined, comments
    skipped. Parser directives (`# syntax=`, `# escape=`, `# check=`) are refused, so a custom
    frontend or escape character can't change what the build runs."""
    result: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    leading = True
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if leading and _DIRECTIVE.match(stripped):
            raise ServiceError(
                "BUILD_INVALID", f"Dockerfile line {number}: parser directives are refused"
            )
        if stripped.startswith("#") or (not stripped and not pending):
            continue
        leading = False
        if not pending:
            start = number
        if stripped.endswith("\\"):
            pending.append(stripped[:-1])
            continue
        pending.append(stripped)
        result.append((start, " ".join(part for part in pending if part)))
        pending = []
    if pending:
        result.append((start, " ".join(pending)))
    return result


def _check_dockerfile(path: Path) -> None:
    """Built services: every base image is digest-pinned (FROM, COPY --from, RUN --mount from=),
    and no build args, secrets, SSH, ONBUILD or parser directives."""
    if not path.is_file() or path.is_symlink():
        raise ServiceError("BUILD_INVALID", "a built service needs a regular Dockerfile")
    stages: list[str] = []

    def pinned(image: str) -> bool:
        image = image.lower()
        return (
            "@sha256:" in image
            or image in stages
            or image == "scratch"
            or (image.isdigit() and int(image) < len(stages))
        )

    def refuse(number: int, why: str):
        raise ServiceError("BUILD_INVALID", f"Dockerfile line {number}: {why}")

    for number, line in _instructions(path.read_text(encoding="utf-8")):
        words = line.split()
        instruction = words[0].upper()
        if instruction in ("ARG", "ONBUILD"):
            refuse(number, f"{instruction} is refused")
        options = [w for w in words[1:] if w.startswith("--")]
        if instruction == "FROM":
            images = [w for w in words[1:] if not w.startswith("--")]
            # A later stage may build FROM an earlier stage's name.
            if not images or not pinned(images[0]):
                refuse(number, "FROM must be digest-pinned")
            stage = (
                images[2] if len(images) == 3 and images[1].upper() == "AS" else str(len(stages))
            )
            stages.append(stage.lower())
        for option in options:
            name, _, value = option[2:].partition("=")
            if name == "from" and not pinned(value):
                refuse(number, "--from must name an earlier stage or a digest-pinned image")
            if name == "mount":
                fields = dict(part.partition("=")[::2] for part in value.split(",") if "=" in part)
                if fields.get("type") in ("secret", "ssh"):
                    refuse(number, "build secrets and SSH are refused")
                if "from" in fields and not pinned(fields["from"]):
                    refuse(number, "a mount's from must be an earlier stage or pinned")
