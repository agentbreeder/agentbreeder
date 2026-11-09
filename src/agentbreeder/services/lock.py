"""`stack.lock`: the exact service versions, file params and imported variable names a stack
was started from. Same format and modes as `agent.lock` (see lock.py), plus `imports`.

- Service refs are hashed like any registry version. A service's image digest is part of its
  `service.yaml`, so the version hash covers it.
- File params (`type: file`) are `path:` entries. Unlike agent path refs, `--locked` accepts them
  when their hash matches: a stack's config files are part of the project, not work in progress.
- Imported variable names are recorded per service, so a new service version can't start
  reading another variable from the operator's environment unnoticed (`--locked` refuses it).
- `mcp` records, per stack.yaml `mcp:` entry, its ref, origin, upstream origin, `token_url` host
  and the pinned tool definition hashes (spec §7.6.1, §7.6.5). The built-in relay version is an
  ordinary entry.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from agentbreeder import gateway_keys
from agentbreeder.lock import BUILTIN_LABEL, LOCK_VERSION, Lock, materialise
from agentbreeder.registry import Ref, RegistryError
from agentbreeder.services import mcp_stack, refs
from agentbreeder.services.schema import (
    ServiceError,
    ServiceSpec,
    Stack,
    StackService,
    load_service,
)


@dataclass
class StackLock(Lock):
    imports: dict[str, list[str]] = field(default_factory=dict)
    mcp: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> StackLock:
        base = Lock.load(path)
        imports: dict[str, list[str]] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            raw = data.get("imports", {})
            if not isinstance(raw, dict) or not all(
                isinstance(v, list) and all(isinstance(n, str) for n in v) for v in raw.values()
            ):
                raise RegistryError("LOCK_INVALID", f"{path}: imports must map services to names")
            imports = raw
            mcp = data.get("mcp", {})
            if not isinstance(mcp, dict) or not all(
                isinstance(v, dict)
                and isinstance(v.get("tools", {}), dict)
                and all(
                    isinstance(h, str) and h.startswith("sha256:")
                    for h in v.get("tools", {}).values()
                )
                for v in mcp.values()
            ):
                raise RegistryError("LOCK_INVALID", f"{path}: mcp must map servers to pins")
            return cls(base.entries, imports, mcp)
        return cls(base.entries, imports)

    def save(self, path: Path) -> None:
        body = {
            "lock_version": LOCK_VERSION,
            "entries": dict(sorted(self.entries.items())),
            "imports": {k: sorted(v) for k, v in sorted(self.imports.items())},
        }
        if self.mcp:
            body["mcp"] = {
                server: {**entry, "tools": dict(sorted(entry.get("tools", {}).items()))}
                for server, entry in sorted(self.mcp.items())
            }
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)


@dataclass
class LockedStack:
    stack: Stack
    specs: dict[str, ServiceSpec]  # service name -> its service.yaml
    service_dirs: dict[str, Path]  # service name -> hashed copy of its registry version
    files: dict[str, dict[str, Path]]  # service name -> file param -> hashed copy
    resolved: refs.Resolved
    hashes: dict[str, str]  # lock key -> sha256
    warnings: list[str]
    changed: bool
    # service name -> "builtin" or the registry root it came from (lock.Materialised.source)
    origins: dict[str, str] = field(default_factory=dict)
    mcp: dict = field(default_factory=dict)  # server -> mcp_stack.McpEntry
    relays: dict[str, str] = field(default_factory=dict)  # relay service -> server


def _imports(spec: ServiceSpec) -> list[str]:
    return sorted(s.import_ for s in spec.secrets.values() if s.import_)


def lock_stack(
    stack: Stack,
    *,
    project_dir: Path,
    search: list[Path],
    lock: StackLock,
    context: Path,
    mode: str = "plain",
) -> LockedStack:
    """Copy and hash every service version and file param into `context`, validate the stack
    against the hashed copies, and reconcile with `lock` (mode as for agent.lock)."""
    mcp_stack.check_reserved(stack)
    service_refs = {name: entry.ref for name, entry in stack.services.items()}
    entries, changed = materialise(
        refs=sorted(set(service_refs.values())),
        paths=[],
        project_dir=project_dir,
        search=search,
        lock=lock,
        context=context / "services",
        mode=mode,
        prune=False,
        lock_file="stack.lock",
        update_command="services up --update",
    )
    by_key = {entry.key: entry for entry in entries}
    service_dirs = {name: by_key[str(Ref.parse(ref))].path for name, ref in service_refs.items()}
    specs = {name: load_service(path / "service.yaml") for name, path in service_dirs.items()}
    resolved = refs.check(stack, specs)
    locked_mcp = mcp_stack.lock_mcp(
        stack, project_dir=project_dir, search=search, lock=lock, context=context, mode=mode
    )
    changed |= locked_mcp.changed
    mcp_stack.check_grants(stack, locked_mcp.entries)
    mcp_stack.check_service_upstreams(stack, specs, locked_mcp.entries)

    file_params = {
        name: {
            key: str(resolved.params[name][key])
            for key, param in specs[name].params.items()
            if param.type == "file" and key in resolved.params[name]
        }
        for name in stack.services
    }
    paths = sorted({value for params in file_params.values() for value in params.values()})
    path_entries, path_changed = materialise(
        refs=[],
        paths=paths,
        project_dir=project_dir,
        search=search,
        lock=lock,
        context=context / "files",
        mode=mode,
        prune=False,
        lock_file="stack.lock",
        update_command="services up --update",
        locked_paths=True,
    )
    changed |= path_changed
    by_path = {entry.key.removeprefix("path:"): entry for entry in path_entries}
    files = {
        name: {key: _single_file(by_path[Path(value).as_posix()].path) for key, value in p.items()}
        for name, p in file_params.items()
    }

    used = set(by_key) | {entry.key for entry in path_entries} | locked_mcp.keys
    unused = sorted(set(lock.entries) - used)
    if unused:
        if mode == "locked":
            raise RegistryError(
                "LOCK_STALE", f"stack.lock has entries stack.yaml no longer uses: {unused}"
            )
        for key in unused:
            del lock.entries[key]
        changed = True

    origins = {name: by_key[str(Ref.parse(ref))].source for name, ref in service_refs.items()}
    # Relays import the mcp versions' credentials: recorded like any service's imports.
    relays = _add_relays(stack, locked_mcp, specs, service_dirs, files, resolved, origins)
    imports = {name: _imports(spec) for name, spec in specs.items() if _imports(spec)}
    if imports != lock.imports:
        if mode == "locked":
            raise RegistryError(
                "LOCK_STALE",
                "the variables services import changed; review them and run without --locked",
            )
        lock.imports = imports
        changed = True

    warnings = _gateway_aliases(stack, specs, files)
    hashes = {key: value["sha256"] for key, value in lock.entries.items()}
    stack = stack.model_copy(
        update={
            "services": {
                **stack.services,
                **{name: StackService(ref=mcp_stack.RELAY_REF) for name in relays},
            }
        }
    )
    return LockedStack(
        stack,
        specs,
        service_dirs,
        files,
        resolved,
        hashes,
        warnings,
        changed,
        origins,
        locked_mcp.entries,
        relays,
    )


def _add_relays(stack, locked_mcp, specs, service_dirs, files, resolved, origins) -> dict:
    """One relay service per `mcp:` entry, after its service upstream in the start order."""
    relays = {}
    for server, entry in sorted(locked_mcp.entries.items()):
        name = entry.relay
        relays[name] = server
        specs[name] = mcp_stack.relay_spec(locked_mcp.relay_spec, entry)
        service_dirs[name] = locked_mcp.relay_dir
        files[name] = {}
        resolved.params[name] = {}
        resolved.order.append(name)
        origins[name] = BUILTIN_LABEL
    return relays


def _single_file(directory: Path) -> Path:
    """A file param is copied as a one-file directory (lock._copy_file); return the file."""
    if directory.is_file():
        return directory
    children = [child for child in directory.iterdir()]
    if len(children) != 1 or not children[0].is_file():
        raise ServiceError("PARAM_INVALID", "file params must name a single file")
    return children[0]


def _gateway_aliases(stack: Stack, specs, files) -> list[str]:
    """Every alias the environment grants must be served by the gateway's config (error);
    extra aliases in the config are only a warning."""
    gateway = stack.environment.gateway
    config = files.get(gateway, {}).get("config")
    if config is None:
        raise ServiceError("GATEWAY_CONTRACT", "the gateway service needs a `config` file param")
    try:
        served = gateway_keys.config_aliases(config)
    except gateway_keys.GatewayError as error:
        raise ServiceError(error.code, str(error)) from None
    missing = sorted(set(stack.environment.models) - served)
    if missing:
        raise ServiceError(
            "GATEWAY_ALIAS_MISSING",
            f"environment.models lists aliases the gateway config doesn't serve: {missing}",
        )
    extra = sorted(served - set(stack.environment.models))
    return (
        [f"gateway config serves aliases the environment doesn't grant: {extra}"] if extra else []
    )
