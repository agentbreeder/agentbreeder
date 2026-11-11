"""Pure planning for a locked stack: addresses, output values, the Compose service definitions
and the grant shown before `services up` runs anything. No Docker calls here.

Addresses are computed by the consumer's position: service to service `internal`
(`<service>:<port>` on the stack network), agent containers `agents` (the same name, on the
stack's agents network), AgentBreeder on the host `host` (`127.0.0.1:<allocated port>`). A port
must be exposed to the scope that uses it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from agentbreeder import gateway_keys
from agentbreeder.lock import BUILTIN_LABEL
from agentbreeder.services import mcp_stack, refs
from agentbreeder.services.lock import LockedStack
from agentbreeder.services.refs import Reference
from agentbreeder.services.schema import ServiceError, ServiceSpec

TMPFS = "/tmp:rw,noexec,nosuid,size=64m"
EXTRA_TMPFS = "rw,noexec,nosuid,size=16m"


def tmpfs_mounts(spec) -> list[str]:
    """The default /tmp plus a service's declared scratch paths, all noexec and size-capped."""
    return [TMPFS, *(f"{path}:{EXTRA_TMPFS}" for path in spec.tmpfs)]


LABEL_STACK = "io.agentbreeder.stack"
LABEL_SERVICE = "io.agentbreeder.service"
LABEL_CONFIG = "io.agentbreeder.config"
SCOPE_LABEL = {"internal": "other services", "agents": "agent containers", "host": "the host"}


@dataclass
class Plan:
    """Everything `up` needs per service, built from the locked stack and the stack state."""

    stack_id: str
    project: str  # Compose project name, also the stack network name
    locked: LockedStack
    host_ports: dict[str, dict[str, int]]
    secrets: dict[str, dict[str, bytes]]  # service -> own secret name[.item] -> value
    images: dict[str, str] = field(default_factory=dict)  # service -> image to run
    files: dict[str, dict[str, Path]] = field(default_factory=dict)  # copies of file params
    service_files: dict[str, Path] = field(default_factory=dict)  # copies of files/ dirs
    # Extra read-only binds (relays: grants and pins), service -> [(source, target)].
    binds: dict[str, list[tuple[Path, str]]] = field(default_factory=dict)
    # File modes of /run/secrets copies (relay credentials are 0400, spec §7.6.11).
    secret_modes: dict[str, int] = field(default_factory=dict)

    @property
    def networks(self) -> dict[str, str]:
        networks = {
            "stack": self.project,
            "agents": f"{self.project}-agents",
            "edge": f"{self.project}-edge",
        }
        # A service upstream and its relay share a private network nothing else joins (§7.6.8).
        for server, entry in sorted(self.locked.mcp.items()):
            if not entry.remote:
                key = mcp_stack.network_key(server)
                networks[key] = f"{self.project}-{key}"
        return networks

    def mcp_upstreams(self) -> dict[str, str]:
        """service upstream name -> the server it serves."""
        stack = self.locked.stack
        result = {}
        for server, entry in self.locked.mcp.items():
            name = mcp_stack.upstream_service(stack, entry)
            if name is not None:
                result[name] = server
        return result

    def mcp_upstream_url(self, server: str) -> str:
        entry = self.locked.mcp[server]
        if entry.remote:
            return entry.spec.upstream.remote.url
        service = entry.spec.upstream.service
        name = mcp_stack.upstream_service(self.locked.stack, entry)
        return f"http://{self.address(name, service.port, 'internal')}{service.path}"

    def relay_url(self, server: str) -> str:
        relay = self.locked.mcp[server].relay
        return f"http://{self.address(relay, mcp_stack.RELAY_PORT, 'agents')}/mcp"

    def health_network(self, service: str) -> str:
        return self.networks[self.joins(service)[0]]

    def spec(self, service: str) -> ServiceSpec:
        return self.locked.specs[service]

    # -- addresses and outputs ------------------------------------------------------------------

    def address(self, service: str, port_name: str, scope: str) -> str:
        port = self.spec(service).ports[port_name]
        if scope not in port.scopes:
            raise ServiceError(
                "ADDRESS_SCOPE",
                f"{service}.{port_name} is not exposed to {SCOPE_LABEL[scope]}; "
                f"add `{scope}` to its expose",
            )
        if scope == "host":
            return f"127.0.0.1:{self.host_ports[service][port_name]}"
        return f"{service}:{port.port}"

    def url(self, service: str, output_name: str, scope: str) -> str:
        output = self.spec(service).outputs[output_name]
        port = self.spec(service).ports[output.port]
        prefix = "http://" if port.protocol == "http" else ""
        return prefix + self.address(service, output.port, scope) + output.path

    def output(self, reference: Reference, scope: str) -> bytes:
        """The value of `service.output[.item]` as seen from `scope`. Secret parts are
        percent-encoded inside templates (they become parts of a URL)."""
        service, spec = reference.service, self.spec(reference.service)
        output = spec.outputs[reference.output]
        if output.type == "url":
            return self.url(service, reference.output, scope).encode()
        if output.secret is not None:
            return self._secret(service, output.secret, reference.item)
        where = f"service {service} output {reference.output}"
        values: dict[str, str] = {}
        for placeholder in refs.placeholders(output.template, where):
            if placeholder == "address":
                values[placeholder] = self.address(service, output.port, scope)
            elif placeholder == "item" and reference.item is not None:
                values[placeholder] = reference.item
            elif placeholder.startswith("params."):
                value = self.locked.resolved.params[service].get(placeholder[7:])
                if not isinstance(value, str):
                    raise ServiceError(
                        "TEMPLATE_INVALID", f"{where}: {{{placeholder}}} has no value"
                    )
                values[placeholder] = quote(value, safe="")
            elif placeholder.startswith("secret."):
                data = self._secret(service, placeholder[7:], reference.item)
                values[placeholder] = quote(data.decode("utf-8", "strict"), safe="")
            else:
                raise ServiceError("TEMPLATE_INVALID", f"{where}: unknown {{{placeholder}}}")
        return refs.expand(output.template, values).encode()

    def _secret(self, service: str, name: str, item: str | None) -> bytes:
        own = self.secrets.get(service, {})
        source = self.spec(service).secrets.get(name)
        key = f"{name}.{item}" if source is not None and source.per and item else name
        if key not in own:
            raise ServiceError(
                "SECRET_UNAVAILABLE",
                f"service {service}: secret {name} is not available yet (bootstrap pending?)",
            )
        return own[key]

    # -- what a container receives --------------------------------------------------------------

    def mounted(self, service: str, names: set[str] | None = None) -> dict[str, bytes]:
        """Values bind-mounted at /run/secrets: own secrets (per-item as `name.item`),
        bootstrap-written secrets and secret inputs. `names` limits it (one-shot steps)."""
        spec = self.spec(service)
        wanted = spec.mounted_secrets() if names is None else names
        result: dict[str, bytes] = {}
        for key, value in self.secrets.get(service, {}).items():
            if key.split(".", 1)[0] in wanted:
                result[key] = value
        wires = self.locked.stack.services[service].wire
        for name, spec_input in spec.inputs.items():
            if spec_input.type == "secret" and name in wanted and name in wires:
                result[name] = self.output(Reference.parse(wires[name], name), "internal")
        return result

    def environment(self, service: str) -> dict[str, str]:
        spec = self.spec(service)
        params = self.locked.resolved.params[service]
        wires = self.locked.stack.services[service].wire
        values = {f"params.{k}": v for k, v in params.items() if isinstance(v, str)}
        for name, spec_input in spec.inputs.items():
            if spec_input.type == "url" and name in wires:
                reference = Reference.parse(wires[name], name)
                values[f"inputs.{name}"] = self.output(reference, "internal").decode()
        result = {}
        for key, template in sorted(spec.env.items()):
            # An optional url input left unwired leaves its variable out.
            needed = refs.placeholders(template, f"service {service} env.{key}")
            if all(name in values for name in needed):
                result[key] = refs.expand(template, values)
        # Its own name on the stack network (one-shots reach their service by it; a Host
        # allowlist accepts it). Set by AgentBreeder: service env can't use the prefix.
        result["AGENTBREEDER_SERVICE"] = service
        return result

    def user(self, service: str) -> str | None:
        user = self.spec(service).user
        if user == "image":
            return None
        if user == "host":
            return f"{os.getuid()}:{os.getgid()}"
        return user

    def joins(self, service: str) -> list[str]:
        spec = self.spec(service)
        if service in self.locked.relays:
            server = self.locked.relays[service]
            networks = ["agents"]
            if not self.locked.mcp[server].remote:
                networks.append(mcp_stack.network_key(server))
            return networks + (["edge"] if spec.egress else [])
        upstreams = self.mcp_upstreams()
        if service in upstreams:
            # Never the stack network: only its relay reaches it.
            return [mcp_stack.network_key(upstreams[service])] + (["edge"] if spec.egress else [])
        scopes = set().union(*(p.scopes for p in spec.ports.values())) if spec.ports else set()
        networks = ["stack"]
        if "agents" in scopes:
            networks.append("agents")
        # Docker can't publish ports from internal networks, so a host port also means
        # outbound access. The grant says so.
        if spec.egress or "host" in scopes:
            networks.append("edge")
        return networks

    def upstream(self, service: str) -> list[str]:
        """Services this one is wired to, directly or transitively (sorted)."""
        seen: set[str] = set()
        pending = [service]
        while pending:
            for text in self.locked.stack.services[pending.pop()].wire.values():
                source = Reference.parse(text, "wire").service
                if source not in seen and source != service:
                    seen.add(source)
                    pending.append(source)
        return sorted(seen)

    def volume_name(self, service: str, volume: str) -> str:
        return f"{self.project}-{service}-{volume}"

    def compose_service(self, service: str, mount_dir: Path | None) -> dict:
        spec = self.spec(service)
        definition: dict = {"image": self.images[service]}
        if spec.command:
            # `command` is the whole argv: the image ENTRYPOINT (often a shell script) never
            # wraps it. Without a command the image's own entrypoint and cmd run unchanged.
            definition["entrypoint"] = [spec.command[0]]
            definition["command"] = list(spec.command[1:])
        user = self.user(service)
        if user:
            definition["user"] = user
        environment = self.environment(service)
        if environment:
            definition["environment"] = environment
        definition.update(
            {
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": tmpfs_mounts(spec),
                "pids_limit": 512,
                "restart": "unless-stopped",
                "logging": {"driver": "local", "options": {"max-size": "10m", "max-file": "2"}},
                "networks": self.joins(service),
            }
        )
        ports = [
            {
                "target": port.port,
                "published": str(self.host_ports[service][name]),
                "host_ip": "127.0.0.1",
                "protocol": "tcp",
            }
            for name, port in sorted(spec.ports.items())
            if "host" in port.scopes
        ]
        if ports:
            definition["ports"] = ports
        volumes = []
        if mount_dir is not None:
            volumes.append(_bind(mount_dir, "/run/secrets"))
        if service in self.service_files:
            volumes.append(_bind(self.service_files[service], "/files"))
        for name, path in sorted(self.files.get(service, {}).items()):
            volumes.append(_bind(path, f"/run/params/{name}"))
        for source, target in self.binds.get(service, []):
            volumes.append(_bind(source, target))
        for name, target in sorted(spec.volumes.items()):
            volumes.append({"type": "volume", "source": f"{service}-{name}", "target": target})
        if volumes:
            definition["volumes"] = volumes
        if spec.health is not None and spec.health.command:
            definition["healthcheck"] = {
                "test": ["CMD", *spec.health.command],
                "interval": "3s",
                "timeout": "5s",
                "retries": 40,
                "start_period": "5s",
            }
        definition = _escape(definition)
        digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()
        definition["labels"] = {
            LABEL_STACK: self.stack_id,
            LABEL_SERVICE: service,
            LABEL_CONFIG: digest[:16],
        }
        return definition

    def compose_file(self, services: dict[str, dict]) -> dict:
        labels = {LABEL_STACK: self.stack_id}
        used = {network for definition in services.values() for network in definition["networks"]}
        networks = {}
        for key, name in self.networks.items():
            if key in used or key == "stack":
                networks[key] = {"name": name, "labels": dict(labels)}
                if key != "edge":
                    networks[key]["internal"] = True
        volumes = {
            f"{service}-{name}": {
                "name": self.volume_name(service, name),
                "labels": dict(labels),
            }
            for service in services
            for name in sorted(self.spec(service).volumes)
        }
        document = {"name": self.project, "services": services, "networks": networks}
        if volumes:
            document["volumes"] = volumes
        return document

    # -- the grant --------------------------------------------------------------------------------

    def _gateway_config(self) -> dict | None:
        config = self.files.get(self.locked.stack.environment.gateway, {}).get("config")
        if config is None:
            return None
        try:
            review = gateway_keys.config_review(config)
        except gateway_keys.GatewayError as error:
            raise ServiceError(error.code, str(error)) from None
        return {"sha256": review.sha256, "providers": dict(sorted(review.providers.items()))}

    def grant(self, environment: str) -> dict:
        stack = self.locked.stack
        services = []
        for name in self.locked.resolved.order:
            spec = self.spec(name)
            published = {
                port: self.host_ports[name][port]
                for port, p in sorted(spec.ports.items())
                if "host" in p.scopes
            }
            services.append(
                {
                    "name": name,
                    "ref": stack.services[name].ref,
                    "origin": (
                        "built-in" if self.locked.origins.get(name) == BUILTIN_LABEL else "project"
                    ),
                    "image": spec.image or f"built from {stack.services[name].ref}",
                    "egress": "declared" if spec.egress else "host port" if published else "no",
                    "agents": any("agents" in p.scopes for p in spec.ports.values()),
                    "host_ports": published,
                    "imports": sorted(s.import_ for s in spec.secrets.values() if s.import_),
                }
            )
        env = stack.environment
        grant = {
            "stack": self.stack_id,
            "environment": environment,
            "services": services,
            # Every secret and URL flow is part of what the operator consents to: rewiring a
            # secret to another service or binding changes the grant and needs reconfirmation.
            "wires": {
                f"{name}.{input_name}": reference
                for name in self.locked.resolved.order
                for input_name, reference in sorted(stack.services[name].wire.items())
            },
            "agent_services": dict(sorted(env.services.items())),
            "bindings": {key: value.ref for key, value in sorted(env.bindings.items())},
            "eval_database": env.eval_database.ref if env.eval_database else None,
            "gateway": env.gateway,
            # The gateway config comes from the repo: its digest and where each alias sends which
            # key are part of the consent, so an edit to it needs reconfirmation.
            "gateway_config": self._gateway_config(),
            "models": list(env.models),
            "ceilings": env.ceilings.model_dump(),
            # Per MCP server: origin, upstream, where each credential goes and the tools each
            # agent gets (§7.2, §7.6.1). Pinned definition hashes are shown, but they are
            # consented to in phase 2 and are not part of this digest.
            "mcp": mcp_stack.grant_section(self.locked.mcp, stack, None),
            "demo_identities": [
                {
                    "agent": agent,
                    "sponsor": spec.sponsor,
                    "review_by": spec.review_by,
                    "candidate": spec.candidate,
                }
                for agent, spec in sorted(env.agents.items())
            ],
        }
        if not grant["mcp"]:
            del grant["mcp"]  # a stack without MCP keeps the grant digest it had before #745
        return grant


def _gateway_line(review: dict | None) -> list[str]:
    if review is None:
        return []
    return [
        f"  config sha256 {review['sha256'][:16]}…; keys go only to their provider:",
        *(f"    {alias}: {route}" for alias, route in review["providers"].items()),
    ]


def _project_gateway(grant: dict) -> list[str]:
    origin = {s["name"]: s.get("origin") for s in grant["services"]}.get(grant["gateway"])
    if origin != "project":
        return []
    return [
        "  WARNING: this gateway is project code, not a built-in. The config review can't bind "
        "what its image does with the keys it imports."
    ]


def _mcp_lines(grant: dict) -> list[str]:
    servers = grant.get("mcp") or []
    if not servers:
        return []
    pins = grant.get("mcp_pins") or {}
    lines = ["MCP servers (each behind its own relay; agents never hold the credential):"]
    for server in servers:
        lines.append(
            f"  {server['server']:<14} {server['ref']} ({server['origin']}); upstream "
            f"{server['upstream']}; auth {server['auth']}; egress: {server['egress']}"
        )
        lines += [
            f"  {'':<14} credential {c['variable']} will be sent to {c['destination']}"
            for c in server["credentials"]
        ]
        for agent, tools in server["agents"].items():
            hashes = pins.get(server["server"], {})
            listed = ", ".join(
                f"{tool} ({effect}, {hashes[tool][7:23] if tool in hashes else 'unpinned'})"
                for tool, effect in tools.items()
            )
            lines.append(f"  {'':<14} {agent}: {listed}")
        if server["origin"] != "built-in":
            lines.append(
                f"  {'':<14} WARNING: {server['origin']} code; its `effect` labels are untrusted"
            )
    return lines


def _definition(definition) -> list[str]:
    """A whole tool object, as hashed: every field, nothing truncated. ASCII-escaped, so
    upstream text can't put control sequences on the operator's terminal."""
    return json.dumps(definition, indent=2, sort_keys=True, ensure_ascii=True).splitlines()


def pins_text(report: dict) -> str:
    """Phase 2: the tool definitions found upstream, for the operator to pin (§7.7.1). The
    whole definition is shown (the pin covers all of it); a changed one as a diff."""
    pad = " " * 11
    lines = [
        f"Pin these tool definitions for mcp.{report['server']} ({report['origin']})? The first "
        "pin trusts what the upstream serves now (trust on first use)."
    ]
    for name, item in sorted(report["new"].items()):
        lines.append(f"  new      {name}  {item['sha256'][7:23]}")
        lines.extend(pad + line for line in _definition(item["definition"]))
    for name, item in sorted(report["changed"].items()):
        lines.append(f"  changed  {name}  {item['pinned'][7:23]} -> {item['sha256'][7:23]}")
        if not report.get("repin"):
            lines.append(f"{pad}(hidden; not accepted without --repin)")
        if item.get("previous") is None:
            lines.append(f"{pad}(previous definition unavailable; the new one in full:)")
            lines.extend(pad + line for line in _definition(item["definition"]))
        else:
            diff = difflib.unified_diff(
                _definition(item["previous"]),
                _definition(item["definition"]),
                "pinned",
                "upstream now",
                lineterm="",
            )
            lines.extend(pad + line for line in diff)
    for name in report["missing"]:
        lines.append(f"  missing upstream  {name}")
    if report["missing"] and report.get("repin"):
        lines.append("  Tools missing upstream: answering yes drops these pins.")
    return "\n".join(lines)


def mcp_status_lines(mcp: dict) -> list[str]:
    lines = []
    for server, info in sorted(mcp.items()):
        state = info.get("state", "")
        detail = []
        if info.get("changed"):
            detail.append(f"changed (hidden): {', '.join(info['changed'])}")
        if info.get("missing"):
            detail.append(f"missing upstream: {', '.join(info['missing'])}")
        if info.get("error"):
            detail.append(info["error"])
        suffix = f"  {'; '.join(detail)}" if detail else ""
        lines.append(f"mcp.{server:<12} {state}{suffix}")
        if info.get("changed"):
            lines.append("  review with: agentbreeder services up --environment ENV --repin")
    return lines


def _cluster_lines(grant: dict) -> list[str]:
    cluster = grant.get("kubernetes")
    if not cluster:
        return []
    return [
        f"Kubernetes target: context {cluster['context']}, namespace {cluster['namespace']}, "
        f"cluster {cluster['cluster_uid']}, images pushed to {cluster['registry']}"
    ]


def grant_text(grant: dict) -> str:
    lines = [
        f"Stack {grant['stack']}, environment {grant['environment']} (local demo only)",
        "Services:",
    ]
    for service in grant["services"]:
        ports = ", ".join(f"{k} 127.0.0.1:{v}" for k, v in service["host_ports"].items())
        lines.append(
            f"  {service['name']:<14} {service['image']} ({service.get('origin', 'project')})\n"
            f"  {'':<14} egress: {service['egress']}; agents: "
            f"{'yes' if service['agents'] else 'no'}; host: {ports or 'no'}; imports: "
            f"{', '.join(service['imports']) or 'none'}"
        )
    if grant["wires"]:
        lines.append("Wiring (input <- output):")
        lines += [f"  {target} <- {source}" for target, source in grant["wires"].items()]
    lines.append("Given to agents:")
    lines += [f"  service {key}: {value}" for key, value in grant["agent_services"].items()]
    lines += [f"  secret {key} <- {value}" for key, value in grant["bindings"].items()]
    if grant["eval_database"]:
        lines.append(f"Eval database (host) <- {grant['eval_database']}")
    ceilings = grant["ceilings"]
    lines += [
        f"Gateway {grant['gateway']}: models {', '.join(grant['models'])}; "
        f"ceilings ${ceilings['budget_usd']:g}, {ceilings['rpm']} rpm",
        *_gateway_line(grant.get("gateway_config")),
        *_project_gateway(grant),
        *_mcp_lines(grant),
        *_cluster_lines(grant),
        "Demo identities (static provider, written outside the repo):",
        *(
            f"  {i['agent']}{' (+ candidate)' if i['candidate'] else ''}  sponsor "
            f"{i['sponsor']}  review by {i['review_by']}"
            for i in grant["demo_identities"]
        ),
    ]
    return "\n".join(lines)


def grant_digest(grant: dict) -> str:
    consented = {key: value for key, value in grant.items() if key != "mcp_pins"}
    return hashlib.sha256(json.dumps(consented, sort_keys=True).encode()).hexdigest()


def _bind(source: Path, target: str) -> dict:
    return {"type": "bind", "source": str(source), "target": target, "read_only": True}


def _escape(value):
    """Compose interpolates `$`; every generated string is literal."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, list):
        return [_escape(item) for item in value]
    if isinstance(value, dict):
        return {key: _escape(item) for key, item in value.items()}
    return value
