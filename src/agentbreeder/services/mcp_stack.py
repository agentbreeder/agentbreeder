"""stack.yaml `mcp:` entries: one built-in relay container per entry (spec §7.2, §7.6, §7.7).

- The relay version (`service/mcp-relay@1.0.1`) resolves from the built-in root only; stack.yaml
  can't name it (RELAY_RESERVED) and no project or AGENTBREEDER_REGISTRY_PATH root can shadow it.
- Each entry's `mcp` version is locked like a service version, and `stack.lock` records its
  origin, its upstream origin and its `token_url` host. Any change needs consent again.
- A relay instance is derived from the relay's service.yaml: the mcp version's secrets (0400
  files), egress only for a remote upstream, and a generated config with no secret values.
- Tool definitions are pinned in two phases (§7.7.1): the grant first, then a probe inside the
  running relay. `stack.lock` holds the hashes; the relay reads the definitions from its pins
  file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from agentbreeder import mcp as mcp_kind
from agentbreeder import registry
from agentbreeder.lock import BUILTIN_LABEL, Lock, materialise
from agentbreeder.registry import Ref, RegistryError
from agentbreeder.services.schema import SecretSource, ServiceError, ServiceSpec, Stack

RELAY_REF = "service/mcp-relay@1.0.1"
RELAY_PORT = "http"
ADMIN_SECRET = "admin-token"
CONFIG_NAME = "config"


def tool_hash(tool: dict) -> str:
    """The relay's hash: sha256 of the whole canonical tool object (relay/relay.py)."""
    text = json.dumps(
        tool, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def relay_name(server: str) -> str:
    """The relay's Compose service name: `mcp-<server>` (`_` becomes `-`)."""
    return "mcp-" + server.replace("_", "-")


def network_key(server: str) -> str:
    return "mcp-" + server.replace("_", "-")


def origin_of(source: str, project_dir: Path) -> str:
    """built-in, project (./registry) or registry-path (AGENTBREEDER_REGISTRY_PATH)."""
    if source == BUILTIN_LABEL:
        return "built-in"
    if source == "registry":
        return "project"
    return "registry-path"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}:{parts.port or 443}"


@dataclass
class McpEntry:
    server: str
    ref: str
    spec: mcp_kind.McpSpec
    origin: str  # built-in | project | registry-path
    relay: str  # its relay service name

    @property
    def remote(self) -> bool:
        return self.spec.upstream.remote is not None

    @property
    def upstream_origin(self) -> str:
        if self.remote:
            return _origin(self.spec.upstream.remote.url)
        service = self.spec.upstream.service
        return f"service {service.ref} port {service.port}"

    @property
    def token_url_host(self) -> str | None:
        oauth = self.spec.auth.oauth_refresh
        return _origin(oauth.token_url) if oauth is not None else None

    def identity(self) -> dict:
        """What stack.lock records besides the pins; a change needs consent again."""
        return {
            "ref": self.ref,
            "origin": self.origin,
            "upstream": self.upstream_origin,
            "token_url": self.token_url_host,
        }

    def credentials(self) -> list[dict]:
        """`credential VAR will be sent to <scheme://host:port>` for each imported secret."""
        names = self.spec.auth.secret_names()
        oauth = self.spec.auth.oauth_refresh
        result = []
        for name in sorted(names):
            source = self.spec.secrets[name]
            label = source.import_ or f"generated {name}"
            destination = self.token_url_host if oauth is not None else self.upstream_origin
            result.append({"variable": label, "destination": destination})
        return result


@dataclass
class LockedMcp:
    entries: dict[str, McpEntry] = field(default_factory=dict)  # server -> entry
    relay_dir: Path | None = None
    relay_spec: ServiceSpec | None = None
    changed: bool = False
    keys: set[str] = field(default_factory=set)  # lock entry keys in use


def lock_mcp(
    stack: Stack,
    *,
    project_dir: Path,
    search: list[Path],
    lock,
    context: Path,
    mode: str,
) -> LockedMcp:
    """Lock every `mcp:` version and the built-in relay version; record each entry's identity
    in `lock.mcp` (dropping pins when the identity changes)."""
    from agentbreeder.services.schema import load_service

    result = LockedMcp()
    if not stack.mcp:
        if lock.mcp:
            if mode == "locked":
                raise RegistryError("LOCK_STALE", "stack.lock pins MCP servers stack.yaml dropped")
            lock.mcp = {}
            result.changed = True
        return result
    names = {"lock_file": "stack.lock", "update_command": "services up --update"}
    entries, changed = materialise(
        refs=sorted({entry.ref for entry in stack.mcp.values()}),
        paths=[],
        project_dir=project_dir,
        search=search,
        lock=lock,
        context=context / "mcp",
        mode=mode,
        prune=False,
        **names,
    )
    # The relay itself: built-in root only, whatever ./registry, the env path or a lock entry
    # say. --locked loads a ref from the lock's recorded source, so that must be built-in too.
    recorded_relay = lock.entries.get(RELAY_REF)
    if recorded_relay is not None and recorded_relay.get("source") != BUILTIN_LABEL:
        raise ServiceError(
            "RELAY_RESERVED",
            f"stack.lock records {RELAY_REF} from {recorded_relay.get('source')!r}; the relay "
            "only comes from the built-in registry. Remove that entry from stack.lock",
        )
    relay_entries, relay_changed = materialise(
        refs=[RELAY_REF],
        paths=[],
        project_dir=project_dir,
        search=[registry.BUILTIN_ROOT],
        lock=lock,
        context=context / "relay",
        mode=mode,
        prune=False,
        **names,
    )
    result.changed = changed or relay_changed
    result.keys = {e.key for e in entries} | {e.key for e in relay_entries}
    result.relay_dir = relay_entries[0].path
    result.relay_spec = load_service(result.relay_dir / "service.yaml")
    by_key = {entry.key: entry for entry in entries}
    taken = set(stack.services)
    for server, entry in sorted(stack.mcp.items()):
        materialised = by_key[str(Ref.parse(entry.ref))]
        spec = mcp_kind.load_mcp(materialised.path)
        relay = relay_name(server)
        if relay in taken:
            raise ServiceError(
                "MCP_NAME_CLASH", f"mcp.{server}: its relay {relay} clashes with a service name"
            )
        taken.add(relay)
        if ADMIN_SECRET in spec.secrets:
            raise ServiceError(
                "MCP_INVALID", f"{entry.ref}: secret name {ADMIN_SECRET} is reserved"
            )
        item = McpEntry(
            server, entry.ref, spec, origin_of(materialised.source, project_dir), relay
        )
        _check_upstream(stack, item)
        result.entries[server] = item
        recorded = lock.mcp.get(server)
        identity = item.identity()
        if recorded is None or {k: recorded.get(k) for k in identity} != identity:
            if mode == "locked":
                raise RegistryError(
                    "LOCK_STALE",
                    f"mcp.{server}: its version, origin or upstream changed; review it and run "
                    "without --locked",
                )
            lock.mcp[server] = {**identity, "tools": {}}
            result.changed = True
    for server in sorted(set(lock.mcp) - set(stack.mcp)):
        if mode == "locked":
            raise RegistryError("LOCK_STALE", f"stack.lock pins mcp.{server}, now gone")
        del lock.mcp[server]
        result.changed = True
    return result


def check_reserved(stack: Stack) -> None:
    """stack.yaml can't name the relay: it starts only from `mcp:` entries (§7.6.2)."""
    for name, entry in stack.services.items():
        if Ref.parse(entry.ref).name == "mcp-relay":
            raise ServiceError(
                "RELAY_RESERVED", f"services.{name}: the MCP relay is started from `mcp:` entries"
            )


def _check_upstream(stack: Stack, entry: McpEntry) -> None:
    remote = entry.spec.upstream.remote
    if remote is not None:
        for url in [remote.url] + (
            [entry.spec.auth.oauth_refresh.token_url] if entry.spec.auth.oauth_refresh else []
        ):
            if (urlsplit(url).port or 443) != 443:
                raise ServiceError(
                    "MCP_PORT", f"mcp.{entry.server}: remote upstreams use https on port 443"
                )
        return
    upstream = entry.spec.upstream.service
    matches = [name for name, s in stack.services.items() if s.ref == upstream.ref]
    if len(matches) != 1:
        raise ServiceError(
            "MCP_UPSTREAM_SERVICE",
            f"mcp.{entry.server}: stack.yaml needs exactly one service with ref {upstream.ref}",
        )


def upstream_service(stack: Stack, entry: McpEntry) -> str | None:
    if entry.remote:
        return None
    ref = entry.spec.upstream.service.ref
    return next(name for name, s in stack.services.items() if s.ref == ref)


def check_service_upstreams(stack: Stack, specs: dict, entries: dict[str, McpEntry]) -> None:
    """A service upstream is reachable only by its relay (§7.1, §7.6.8): its port is
    `expose: internal`, it has no wires, nothing is wired to it and no agent URL, binding or
    eval database names it, and it serves one relay."""
    owners: dict[str, str] = {}
    env = stack.environment
    for entry in entries.values():
        name = upstream_service(stack, entry)
        if name is None:
            continue
        where = f"mcp.{entry.server}"
        if name in owners:
            raise ServiceError("MCP_UPSTREAM_SERVICE", f"{where}: {name} already serves a relay")
        owners[name] = entry.server
        spec = specs[name]
        port = spec.ports.get(entry.spec.upstream.service.port)
        if port is None or port.protocol != "http" or port.scopes != {"internal"}:
            raise ServiceError(
                "MCP_UPSTREAM_EXPOSED",
                f"{where}: {name}'s {entry.spec.upstream.service.port} port must be an HTTP port "
                "with `expose: internal`",
            )
        if any(p.scopes != {"internal"} for p in spec.ports.values()):
            raise ServiceError(
                "MCP_UPSTREAM_EXPOSED", f"{where}: every port of {name} must be internal"
            )
        if stack.services[name].wire:
            raise ServiceError("MCP_UPSTREAM_WIRED", f"{where}: {name} can't take wired inputs")
        referenced = [
            *(text for s in stack.services.values() for text in s.wire.values()),
            *env.services.values(),
            *(b.ref for b in env.bindings.values()),
            *([env.eval_database.ref] if env.eval_database else []),
        ]
        if name == env.gateway or any(
            text.startswith(f"{name}.") or f"{{{name}." in text for text in referenced
        ):
            raise ServiceError("MCP_UPSTREAM_WIRED", f"{where}: only its relay may reach {name}")


def relay_spec(base: ServiceSpec, entry: McpEntry) -> ServiceSpec:
    """This entry's relay: the mcp version's secrets, and egress only for remote upstreams."""
    secrets = {
        name: SecretSource.model_validate(s.model_dump(by_alias=True, exclude_none=True))
        for name, s in entry.spec.secrets.items()
    }
    return base.model_copy(update={"egress": entry.remote, "secrets": secrets})


def relay_config(
    entry: McpEntry,
    stack: Stack,
    *,
    upstream_url: str,
    admin_token: bytes,
) -> bytes:
    """The relay's /run/params/config: no secret values, only names and the admin hash."""
    auth = entry.spec.auth
    config_auth: dict = {"type": auth.type}
    if auth.type == "bearer":
        config_auth["secret"] = auth.secret
    elif auth.type == "header":
        config_auth.update(header=auth.header.name, secret=auth.header.secret)
    elif auth.type == "oauth_refresh":
        oauth = auth.oauth_refresh
        config_auth["oauth"] = {
            "token_url": oauth.token_url,
            "client_id": oauth.client_id,
            "client_secret": oauth.client_secret,
            "refresh_token": oauth.refresh_token,
            "scopes": list(oauth.scopes),
        }
    agents = {
        agent: sorted(demo.mcp[entry.server])
        for agent, demo in sorted(stack.environment.agents.items())
        if entry.server in demo.mcp
    }
    document = {
        "server": entry.server,
        "upstream": {"kind": "remote" if entry.remote else "service", "url": upstream_url},
        "auth": config_auth,
        "tools": {name: tool.effect for name, tool in sorted(entry.spec.tools.items())},
        "agents": agents,
        "allowed_ports": [443],
        "admin_token_sha256": hashlib.sha256(admin_token.strip()).hexdigest(),
    }
    return json.dumps(document, sort_keys=True, indent=2).encode() + b"\n"


def grant_section(entries: dict[str, McpEntry], stack: Stack, lock=None) -> list[dict]:
    """What the operator consents to in phase 1, per server (§7.2, §7.6.1)."""
    result = []
    for server, entry in sorted(entries.items()):
        agents = {}
        for agent, demo in sorted(stack.environment.agents.items()):
            if server in demo.mcp:
                agents[agent] = {
                    tool: entry.spec.tools[tool].effect
                    for tool in sorted(demo.mcp[server])
                    if tool in entry.spec.tools
                }
        result.append(
            {
                "server": server,
                "ref": entry.ref,
                "origin": entry.origin,
                "upstream": entry.upstream_origin,
                "auth": entry.spec.auth.type,
                "credentials": entry.credentials(),
                "egress": "yes" if entry.remote else "no (private network with its upstream)",
                "agents": agents,
            }
        )
    return result


def check_grants(stack: Stack, entries: dict[str, McpEntry]) -> None:
    """Per-agent grants name declared tools only."""
    for agent, demo in stack.environment.agents.items():
        for server, tools in demo.mcp.items():
            unknown = sorted(set(tools) - set(entries[server].spec.tools))
            if unknown:
                raise ServiceError(
                    "MCP_TOOL_UNDECLARED",
                    f"environment.agents.{agent}.mcp.{server}: {len(unknown)} tool(s) the "
                    f"{entries[server].ref} version doesn't declare: {', '.join(unknown)}",
                )


def needs_project_consent(entry: McpEntry) -> bool:
    """A non-built-in entry whose remote upstream carries a credential (§7.6.1)."""
    return entry.origin != "built-in" and entry.remote and entry.spec.auth.type != "none"


# -- pins ---------------------------------------------------------------------------------------


def compare(pinned: dict[str, str], probe: dict) -> dict:
    """new: live, never pinned; changed: pinned, live hash differs; missing: pinned or declared,
    absent upstream; same: unchanged."""
    live = probe.get("tools") or {}
    report = {"new": {}, "changed": {}, "missing": sorted(probe.get("missing") or []), "same": []}
    for name, item in sorted(live.items()):
        if name not in pinned:
            report["new"][name] = item
        elif pinned[name] != item["sha256"]:
            report["changed"][name] = {"pinned": pinned[name], **item}
        else:
            report["same"].append(name)
    return report


def pins_document(
    server: str, pinned: dict[str, str], probe: dict, previous: dict | None = None
) -> bytes:
    """The relay's pins file: for each locked tool, its definition from the probe or else from
    the previous pins file, only where that definition hashes to the locked hash. Keeping a
    changed or missing tool's pinned definition lets the relay name the drift
    (TOOL_DEFINITION_CHANGED, TOOL_MISSING_UPSTREAM) instead of treating it as unpinned."""
    tools = {}
    for name, digest in sorted(pinned.items()):
        for source in ((probe.get("tools") or {}), (previous or {})):
            item = source.get(name)
            if (
                isinstance(item, dict)
                and item.get("sha256") == digest
                and isinstance(item.get("definition"), dict)
                and item["definition"].get("name") == name
                and tool_hash(item["definition"]) == digest
            ):
                tools[name] = {"sha256": digest, "definition": item["definition"]}
                break
    return json.dumps({"server": server, "tools": tools}, sort_keys=True).encode() + b"\n"


def read_pins(path: Path) -> dict:
    """The tools of an existing pins file; anything unreadable counts as none."""
    try:
        document = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return {}
    tools = document.get("tools") if isinstance(document, dict) else None
    return tools if isinstance(tools, dict) else {}


def valid_probe(server: str, output: str, declared) -> dict:
    """Parse the relay's probe output; anything unexpected is refused, never echoed."""
    try:
        document = json.loads(output)
    except ValueError:
        document = None
    if not isinstance(document, dict) or document.get("server") != server:
        error = document.get("error") if isinstance(document, dict) else None
        code = error if isinstance(error, str) and error.isupper() and len(error) < 40 else ""
        raise ServiceError(
            "MCP_PROBE_FAILED",
            f"the {server} relay couldn't list upstream tools{f' ({code})' if code else ''}; "
            f"see `agentbreeder services logs {relay_name(server)}`",
        )
    tools = document.get("tools")
    missing = document.get("missing")
    if not isinstance(tools, dict) or not isinstance(missing, list):
        raise ServiceError("MCP_PROBE_FAILED", f"the {server} relay sent an invalid probe")
    for name, item in tools.items():
        if (
            (declared is not None and name not in declared)
            or not isinstance(item, dict)
            or not isinstance(item.get("definition"), dict)
            or item["definition"].get("name") != name
            or tool_hash(item["definition"]) != item.get("sha256")
        ):
            raise ServiceError("MCP_PROBE_FAILED", f"the {server} relay sent an invalid probe")
    names = [m for m in missing if isinstance(m, str)]
    return {
        "tools": tools,
        "missing": sorted(m for m in names if declared is None or m in declared),
    }


def lock_tools(lock: Lock, server: str) -> dict[str, str]:
    return dict((lock.mcp.get(server) or {}).get("tools") or {})
