"""Pure planning for a locked stack: addresses, output values, the Compose service definitions
and the grant shown before `services up` runs anything. No Docker calls here.

Addresses are computed by the consumer's position: service to service `internal`
(`<service>:<port>` on the stack network), agent containers `agents` (the same name, on the
stack's agents network), AgentBreeder on the host `host` (`127.0.0.1:<allocated port>`). A port
must be exposed to the scope that uses it.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from agentbreeder.services import refs
from agentbreeder.services.lock import LockedStack
from agentbreeder.services.refs import Reference
from agentbreeder.services.schema import ServiceError, ServiceSpec

TMPFS = "/tmp:rw,noexec,nosuid,size=64m"
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

    @property
    def networks(self) -> dict[str, str]:
        return {
            "stack": self.project,
            "agents": f"{self.project}-agents",
            "edge": f"{self.project}-edge",
        }

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
        scopes = set().union(*(p.scopes for p in spec.ports.values())) if spec.ports else set()
        networks = ["stack"]
        if "agents" in scopes:
            networks.append("agents")
        # Docker can't publish ports from internal networks, so a host port also means
        # outbound access. The grant says so.
        if spec.egress or "host" in scopes:
            networks.append("edge")
        return networks

    def volume_name(self, service: str, volume: str) -> str:
        return f"{self.project}-{service}-{volume}"

    def compose_service(self, service: str, mount_dir: Path | None) -> dict:
        spec = self.spec(service)
        definition: dict = {"image": self.images[service]}
        if spec.command:
            definition["command"] = list(spec.command)
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
                "tmpfs": [TMPFS],
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
                    "image": spec.image or f"built from {stack.services[name].ref}",
                    "egress": "declared" if spec.egress else "host port" if published else "no",
                    "agents": any("agents" in p.scopes for p in spec.ports.values()),
                    "host_ports": published,
                    "imports": sorted(s.import_ for s in spec.secrets.values() if s.import_),
                }
            )
        env = stack.environment
        return {
            "stack": self.stack_id,
            "environment": environment,
            "services": services,
            "gateway": env.gateway,
            "models": list(env.models),
            "ceilings": env.ceilings.model_dump(),
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


def grant_text(grant: dict) -> str:
    lines = [
        f"Stack {grant['stack']}, environment {grant['environment']} (local demo only)",
        "Services:",
    ]
    for service in grant["services"]:
        ports = ", ".join(f"{k} 127.0.0.1:{v}" for k, v in service["host_ports"].items())
        lines.append(
            f"  {service['name']:<14} {service['image']}\n"
            f"  {'':<14} egress: {service['egress']}; agents: "
            f"{'yes' if service['agents'] else 'no'}; host: {ports or 'no'}; imports: "
            f"{', '.join(service['imports']) or 'none'}"
        )
    ceilings = grant["ceilings"]
    lines += [
        f"Gateway {grant['gateway']}: models {', '.join(grant['models'])}; "
        f"ceilings ${ceilings['budget_usd']:g}, {ceilings['rpm']} rpm",
        "Demo identities (static provider, written outside the repo):",
        *(
            f"  {i['agent']}{' (+ candidate)' if i['candidate'] else ''}  sponsor "
            f"{i['sponsor']}  review by {i['review_by']}"
            for i in grant["demo_identities"]
        ),
    ]
    return "\n".join(lines)


def grant_digest(grant: dict) -> str:
    return hashlib.sha256(json.dumps(grant, sort_keys=True).encode()).hexdigest()


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
