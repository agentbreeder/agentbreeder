"""`agentbreeder services up/status/logs/env/down` and `--stack` resolution.

`up` (spec §5): lock and validate; show and confirm the grant; import and generate secrets;
start each service in dependency order (Compose, health, `init`, `bootstrap`); pin MCP tool
definitions through each relay (phase 2, spec §7.7.1); write the demo operator config. Every
value stays in the stack state dir; nothing is written to the repo except `stack.lock`.
"""

from __future__ import annotations

import json
import shlex
import socket
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agentbreeder import env_import, hints, registry, secret_files
from agentbreeder import mcp as mcp_kind
from agentbreeder.registry import RegistryError
from agentbreeder.services import mcp_stack
from agentbreeder.services import operator as operator_writer
from agentbreeder.services.compose import PROBE_IMAGE, DockerCLI
from agentbreeder.services.dockerfile import check as _check_dockerfile
from agentbreeder.services.lock import LockedStack, StackLock, lock_stack
from agentbreeder.services.plan import Plan, grant_digest, grant_text
from agentbreeder.services.schema import ServiceError, Stack, load_stack
from agentbreeder.services.state import (
    StackState,
    stack_id,
    stacks_root,
    write_private,
    written_environments,
)

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
    """What `--stack DIR` means: that stack's generated operator config and deploy state.
    Creates nothing: a stack that was never brought up is reported, not made."""
    project = project.resolve()
    stack = load(project)
    directory = StackState.locate(stack.name, project, root)
    written = written_environments(directory) if directory else []
    name = stack_id(stack.name, project)
    if environment is None:
        if len(written) != 1:
            raise ServiceError(
                "STACK_ENVIRONMENT",
                "pass --environment"
                + (f" (one of {', '.join(written)})" if written else "")
                + f"; run `{hints.command('services', 'up', project, '--environment', 'ENV')}` "
                "first if none is listed",
            )
        environment = written[0]
    if environment not in written:
        raise ServiceError(
            "STACK_NOT_UP",
            f"stack {name} has no operator config for {environment}; run "
            f"`{hints.command('services', 'up', project, environment=environment)}`",
        )
    env_dir = directory / "envs" / environment
    return StackContext(name, environment, env_dir / "operator.yaml", env_dir / "state")


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
        self._lock_state = None
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
        self._lock_state = (lock, lock_path)
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
            ref_hash = locked.hashes[str(registry.Ref.parse(locked.stack.services[name].ref))]
            # A Docker tag can't hold the hash's `sha256:` prefix: use its hex.
            digest = ref_hash.rpartition(":")[2]
            # Every relay of a stack runs the same built image.
            image = "mcp-relay" if name in locked.relays else name
            plan.images[name] = spec.image or (
                f"agentbreeder-svc/{self.state.id}-{image}:{digest[:16]}"
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
        data = (source.prefix or "").encode() + secret_files.generate_token()
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
        confirm_pins: Callable[[dict], bool] = lambda report: False,
        repin: bool = False,
        allow_project_mcp: frozenset[str] = frozenset(),
        expect_grant: str | None = None,
    ) -> dict:
        self.state.env_dir(environment)  # validates the name before anything runs
        plan = self.plan(mode)
        unknown = sorted(set(allow_project_mcp) - set(plan.locked.mcp))
        if unknown:
            raise ServiceError(
                "MCP_UNKNOWN",
                f"--allow-project-mcp names no mcp: entry in stack.yaml: {', '.join(unknown)}",
            )
        for server, entry in sorted(plan.locked.mcp.items()):
            if mcp_stack.needs_project_consent(entry) and server not in allow_project_mcp:
                raise ServiceError(
                    "MCP_PROJECT_CONSENT",
                    f"mcp.{server} is {entry.origin} code that sends a credential to "
                    f"{entry.upstream_origin}; review it and pass --allow-project-mcp {server} "
                    "(--yes alone never allows it)",
                )
        self._check_in_use(plan)
        grant = self._grant(plan, environment)
        digest = grant_digest(grant)
        if expect_grant is not None and digest != expect_grant:
            raise ServiceError(
                "GRANT_CHANGED", "the grant differs from the one reviewed; review it again"
            )
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
        self._relays(plan, environment)
        before = self.docker.containers(self.state)
        removed = self._remove_dropped_relays(plan, before)
        rendered: dict[str, dict] = {}
        mounts: set[Path] = set()
        for name in plan.locked.resolved.order:
            self._image(plan, name)
            self._start(plan, name, rendered, mounts)
            self._steps(plan, name, rendered, mounts)
        self.state.prune_mounts(mounts)
        self.state.put("grant", digest)
        self._attach_cluster(plan)
        mcp = self._pin(plan, mode, repin, confirm_pins, allow_project_mcp)
        config = operator_writer.write(plan, self.state, environment)
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
            "mcp": mcp,
            "removed": removed,
        }

    def _grant(self, plan: Plan, environment: str) -> dict:
        grant = plan.grant(environment)
        grant["mcp_pins"] = {
            server: mcp_stack.lock_tools(self._lock_state[0], server) for server in plan.locked.mcp
        }
        cluster = json.loads(self.state.get("kubernetes") or "null")
        if cluster:
            grant["kubernetes"] = {
                key: cluster[key] for key in ("context", "cluster_uid", "namespace", "registry")
            }
        return grant

    def preview(self, environment: str) -> dict:
        """What `up` would ask to confirm. Records the chosen host ports so the digest holds;
        copies content-addressed files into the state; starts nothing."""
        self.state.env_dir(environment)
        plan = self.plan("plain")
        for service, ports in plan.host_ports.items():
            for port_name, number in ports.items():
                self.state.record_port(service, port_name, number)
        grant = self._grant(plan, environment)
        digest = grant_digest(grant)
        needed: dict[str, dict] = {}
        for name in plan.locked.resolved.order:
            for secret, source in sorted(plan.spec(name).secrets.items()):
                if source.import_:
                    key = needed.setdefault(
                        source.import_,
                        {"name": source.import_, "optional": True, "stored": True},
                    )
                    key["optional"] = key["optional"] and bool(source.optional)
                    key["stored"] = (
                        key["stored"] and self.state.read_secret(name, secret) is not None
                    )
        return {
            "digest": digest,
            "text": grant_text(grant),
            "grant": grant,
            "confirmed": self.state.get("grant") == digest,
            "keys": sorted(needed.values(), key=lambda key: key["name"]),
            "mcp": sorted(plan.locked.mcp),
        }

    def _attach_cluster(self, plan: Plan) -> None:
        """Compose recreates the agents network, so the kind node is attached again here."""
        cluster = json.loads(self.state.get("kubernetes") or "null")
        if not cluster:
            return
        from agentbreeder import cluster as local_cluster

        network = plan.networks["agents"]
        local_cluster.connect(local_cluster.NODE, network)
        cluster["blocked_cidrs"] = local_cluster.blocked_cidrs(network)
        self.state.put("kubernetes", json.dumps(cluster))

    def active_cluster_deployments(self) -> list[str]:
        from agentbreeder.store import Store

        active = []
        for environment in self.state.environments():
            directory = self.state.deploy_state(environment)
            if not (directory / "deployments.sqlite3").exists():
                continue
            store = Store(directory)
            try:
                active += [
                    f"{environment}/{row['name']}"
                    for row in store.active_all()
                    if store.manifest(row["id"]).get("target") == "kubernetes"
                ]
            finally:
                store.close()
        return active

    # -- MCP relays (spec §7) ---------------------------------------------------------------------

    def _check_in_use(self, plan: Plan) -> None:
        """Refuse to drop an `mcp:` entry, or narrow a grant, that an active deployment uses.
        Grant files exist only for active deployments (deploy writes, remove and reconcile
        delete them)."""
        root = self.state.directory / "grants"
        if not root.is_dir():
            return
        stack = plan.locked.stack
        for env_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
            for server_dir in sorted(p for p in env_dir.iterdir() if p.is_dir()):
                server = server_dir.name
                entry = plan.locked.mcp.get(server)
                for grant in mcp_kind.grants(server_dir).values():
                    agent = grant.get("agent")
                    demo = stack.environment.agents.get(agent) if isinstance(agent, str) else None
                    allowed = set(demo.mcp.get(server, [])) if demo else set()
                    allowed &= set(entry.spec.tools) if entry else set()
                    tools = grant.get("tools") if isinstance(grant.get("tools"), list) else [None]
                    if entry is None or not set(tools) <= allowed:
                        raise ServiceError(
                            "MCP_GRANT_IN_USE",
                            f"an active deployment in {env_dir.name} uses mcp.{server} tools this "
                            "stack.yaml drops; remove that deployment first (agentbreeder remove "
                            "ID --local --stack .), or run `agentbreeder reconcile` if it is gone",
                        )

    def _remove_dropped_relays(self, plan: Plan, containers: dict) -> list[str]:
        """A relay holds a third-party credential, so it doesn't outlive its `mcp:` entry
        (other dropped services keep running until `services down`). `containers` is this
        stack's only; `_check_in_use` already refused dropping an entry a deployment uses."""
        keep = set(plan.locked.resolved.order)
        # The relays the last `up` started (by record, not by name: a user service may be
        # called mcp-something).
        started = set(json.loads(self.state.get("mcp_relays") or "[]"))
        removed = []
        for name, info in sorted(containers.items()):
            if name in started and name not in keep:
                self.log(f"Removing {name}: its mcp: entry is gone")
                self.docker.remove_container(info["id"])
                removed.append(name)
        # Recorded before anything starts, so a relay a failed `up` leaves running is known.
        self.state.put("mcp_relays", json.dumps(sorted(set(plan.locked.relays))))
        return removed

    def _admin_token(self, relay: str, create: bool = True) -> bytes:
        token = self.state.read_secret(relay, mcp_stack.ADMIN_SECRET)
        if token is None:
            if not create:  # `status` is read-only: a new token would recreate the relay
                raise ServiceError(
                    "MCP_ADMIN_TOKEN_MISSING", f"no admin token recorded for {relay}"
                )
            token = secret_files.generate_token()
            self.state.write_secret(relay, mcp_stack.ADMIN_SECRET, token, "generate")
        self.docker.redactions.append(token)
        return token

    def _relays(self, plan: Plan, environment: str) -> None:
        """Each relay's generated config, grants and pins mounts, and 0400 credentials."""
        grants = self.state.shared_dir("grants")
        for server, entry in sorted(plan.locked.mcp.items()):
            relay = entry.relay
            self.state.shared_dir("grants", environment, server)
            data = mcp_stack.relay_config(
                entry,
                plan.locked.stack,
                upstream_url=plan.mcp_upstream_url(server),
                admin_token=self._admin_token(relay),
            )
            with tempfile.TemporaryDirectory(dir=self.state.directory, prefix=".cfg-") as scratch:
                source = Path(scratch) / mcp_stack.CONFIG_NAME
                source.write_bytes(data)
                plan.files[relay] = {mcp_stack.CONFIG_NAME: self.state.copy_file(source)}
            # Before the relay (re)starts, its pins file holds only what stack.lock still pins:
            # after an identity change that is nothing until phase 2 confirms new pins.
            pins = self.state.shared_dir("pins", server)
            document = mcp_stack.pins_document(
                server,
                mcp_stack.lock_tools(self._lock_state[0], server),
                {},
                mcp_stack.read_pins(pins / "pins.json"),
            )
            write_private(pins / "pins.json", document, mode=0o444)
            plan.binds[relay] = [(grants, "/run/grants"), (pins, "/run/pins")]
            plan.secret_modes[relay] = 0o400

    def _probe(self, entry, declared=None, create: bool = True) -> dict:
        token = self._admin_token(entry.relay, create)
        output = self.docker.relay_probe(self.state, entry.relay, token)
        return mcp_stack.valid_probe(entry.server, output, declared)

    def _pin(self, plan: Plan, mode: str, repin: bool, confirm_pins, allowed) -> dict:
        """Phase 2: probe each relay and pin its tool definitions with consent. A changed or
        missing pinned tool stays hidden (the relay refuses it) until `--repin` accepts it."""
        lock, lock_path = self._lock_state
        reports = {}
        for server, entry in sorted(plan.locked.mcp.items()):
            pinned = mcp_stack.lock_tools(lock, server)
            try:
                probe = self._probe(entry, set(entry.spec.tools))
            except ServiceError as error:
                if not pinned or repin or mode == "locked":
                    raise
                # Pinned before: the relay keeps serving its last pins file; nothing new is
                # trusted until a probe succeeds.
                reports[server] = {"state": "unverified", "error": error.code}
                plan.locked.warnings.append(
                    f"mcp.{server}: couldn't check tool definitions upstream ({error.code}); "
                    "the relay keeps its existing pins"
                )
                continue
            report = mcp_stack.compare(pinned, probe)
            pins = self.state.shared_dir("pins", server) / "pins.json"
            previous = mcp_stack.read_pins(pins)
            for name, item in report["changed"].items():
                old = previous.get(name)
                if (
                    isinstance(old, dict)
                    and old.get("sha256") == pinned.get(name)
                    and isinstance(old.get("definition"), dict)
                    and mcp_stack.tool_hash(old["definition"]) == pinned.get(name)
                ):
                    item["previous"] = old["definition"]  # so the prompt can show a diff
            drift = sorted(report["changed"]) + [m for m in report["missing"] if m in pinned]
            accept = dict(report["new"]) | (
                {name: report["changed"][name] for name in report["changed"]} if repin else {}
            )
            if accept or (repin and drift):
                if mode == "locked":
                    raise RegistryError(
                        "LOCK_STALE",
                        f"mcp.{server}: tool definitions need pinning; run without --locked",
                    )
                details = {
                    "server": server,
                    "origin": entry.origin,
                    # --yes covers first pins and new tools only; --repin (accepting a
                    # changed definition or dropping a pin) always needs a person.
                    "auto": (entry.origin == "built-in" or server in allowed) and not repin,
                    "repin": repin,
                    "first": not pinned,
                    **report,
                }
                if not confirm_pins(details):
                    raise ServiceError(
                        "MCP_PINS_NOT_CONFIRMED",
                        f"mcp.{server}: review the tool definitions and confirm them",
                    )
                if repin:
                    pinned = {k: v for k, v in pinned.items() if k not in report["missing"]}
                pinned.update({name: item["sha256"] for name, item in accept.items()})
                lock.mcp[server]["tools"] = pinned
                # Locked before its pins file is served: a later server failing can't leave
                # this relay serving pins stack.lock never recorded.
                lock.save(lock_path)
            elif drift and mode == "locked":
                raise RegistryError(
                    "LOCK_STALE",
                    f"mcp.{server}: pinned tool definitions changed upstream ({', '.join(drift)})",
                )
            document = mcp_stack.pins_document(server, pinned, probe, previous)
            write_private(pins, document, mode=0o444)
            hidden = [] if repin else drift
            reports[server] = {
                "state": "drift" if hidden else "ok",
                "pinned": sorted(pinned),
                "changed": [] if repin else sorted(report["changed"]),
                "missing": report["missing"],
            }
        return reports

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
        mode = plan.secret_modes.get(name, 0o444)
        mount = self.state.mount_dir(name, values, mode) if values else None
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
            if not self.docker.probe(self.state, plan.health_network(name), url):
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
            network=plan.health_network(name),
            image=plan.images[name],
            command=list(step.command),
            user=plan.user(name),
            mounts=binds,
            # Set last, so it always wins; the schema also reserves the AGENTBREEDER_ prefix.
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
        # The minted value lives in data this service or anything it is wired to keeps (Phoenix's
        # ingest key is in postgres): a new volume anywhere there means minting again.
        identities = sorted(
            self.docker.volume_identity(plan.volume_name(owner, volume)) or ""
            for owner in [name, *plan.upstream(name)]
            for volume in plan.spec(owner).volumes
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
        relays = [mcp_stack.relay_name(server) for server in sorted(self.stack.mcp)]
        for name in [*self.stack.services, *relays]:
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
            "mcp": self._mcp_status(containers),
        }

    def _mcp_status(self, containers: dict) -> dict:
        """Per relay: tools whose live definition changed, and pinned tools missing upstream."""
        lock = StackLock.load(self.project / LOCK_FILE)
        result = {}
        for server in sorted(self.stack.mcp):
            relay = mcp_stack.relay_name(server)
            if (containers.get(relay) or {}).get("state") != "running":
                result[server] = {"state": "absent"}
                continue
            entry = mcp_stack.McpEntry(server, self.stack.mcp[server].ref, None, "", relay)
            pinned = mcp_stack.lock_tools(lock, server)
            try:
                report = mcp_stack.compare(pinned, self._probe(entry, create=False))
            except ServiceError as error:
                result[server] = {"state": "unverified", "error": error.code}
                continue
            missing = [m for m in report["missing"] if m in pinned]
            result[server] = {
                "state": "drift" if report["changed"] or missing else "ok",
                "pinned": len(pinned),
                "changed": sorted(report["changed"]),
                "missing": missing,
                "unpinned": sorted(report["new"]),
            }
        return result

    def logs(self, service: str, tail: int) -> str:
        from agentbreeder.services.compose import redact

        relays = {mcp_stack.relay_name(server) for server in self.stack.mcp}
        if service not in self.stack.services and service not in relays:
            raise ServiceError("SERVICE_UNKNOWN", "no such service in stack.yaml")
        from agentbreeder.redaction import redaction_values

        self.docker.authorize()
        self.docker.redactions.extend(self.state.known_secrets())
        # Agents' secret copies too: a service can log what an agent sent it (S-09).
        self.docker.redactions.extend(redaction_values(None, self.state.directory))
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

    def down(self, *, volumes: bool = False, purge: bool = False) -> dict:
        """Stop the stack. `volumes` also deletes its data volumes; `purge` deletes everything
        `up` made for it (volumes, built service images and the state dir: generated secrets,
        operator config, deploy history), so the next `up` starts from scratch."""
        volumes = volumes or purge
        active = self.active_deployments()
        if active:
            raise ServiceError(
                "STACK_IN_USE",
                f"remove these deployments first (agentbreeder remove ID --local or --cluster "
                f"--stack .): "
                f"{', '.join(active)}",
            )
        self.docker.authorize()
        if self.state.get("kubernetes"):
            from agentbreeder import cluster as local_cluster

            try:  # compose cannot remove a network with the kind node still attached
                self.docker.run(
                    "network",
                    "disconnect",
                    "--force",
                    self.state.project + "-agents",
                    local_cluster.NODE,
                    what="detaching the kind node",
                )
            except ServiceError:
                self.log("The kind node was not attached to the agents network")
        self.docker.down(self.state)
        removed = []
        if volumes:
            for name in self.docker.stack_volumes(self.state):
                self.docker.remove_volume(name)
                removed.append(name)
        images: list[str] = []
        state_removed = None
        if purge:
            for tag in self.docker.stack_images(self.state):
                self.docker.remove_image(tag)
                images.append(tag)
            state_removed = str(self.state.directory)
            self.state.purge()
        return {
            "stack": self.state.id,
            "volumes_removed": removed,
            "images_removed": images,
            "state_removed": state_removed,
        }

    def env_lines(self, environment: str | None) -> list[str]:
        resolved = context(self.project, environment, self.state.directory.parent)
        return [
            f"export AGENTBREEDER_OPERATOR_CONFIG={shlex.quote(str(resolved.operator_config))}",
            f"export AGENTBREEDER_STATE_DIR={shlex.quote(str(resolved.state_dir))}",
        ]
