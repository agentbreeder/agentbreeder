"""The operator config `services up` writes for a local demo environment (spec §3).

It lives in `~/.agentbreeder/stacks/<stack-id>/<env>/`, never in the repo, and can only express:
`enforcement: enforce`, `targets: [local]`, one `static` provider (`demo`) whose principals are
the stack's demo agents (enabled, sponsored, with a review date), the stack's gateway (master key
as a `file:` ref into the stack's service secrets), models, ceilings, service URLs, bindings,
`eval_database`, the agents network, MCP relays (`mcp_relays`) and the agents' identity
references and MCP grants. Never `pdp`, `deployer`,
`maker_checker`, `runtime_enforcement`, `mcp_egress` or another provider type.

Digest rule: `up` records the digest of what it wrote. It overwrites a file only while its digest
still matches; a hand-edited or pre-seeded file stops `up` with OPERATOR_CONFIG_MODIFIED.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from agentbreeder import secret_files
from agentbreeder.operator_config import OperatorModel
from agentbreeder.services.plan import Plan
from agentbreeder.services.refs import Reference, expand, placeholders
from agentbreeder.services.schema import ServiceError
from agentbreeder.services.state import StackState, private_dir, write_private

PROVIDER = "demo"
EVAL_SECRET = "eval-database"
# Host-form database URLs for CLI children only (M12 delta §3.5): never bound into an agent;
# `secret_files.RESERVED` makes deploy refuse a manifest that references them.
AGENT_SECRET = "agent-database"
CONSOLE_SECRET = "console-database"
DATABASE_BINDING = "DATABASE_URL"
CONSOLE_OUTPUT = "console"
CONSOLE_PARAM = "console_database"


def grants_dir(state: StackState, environment: str, server: str) -> Path:
    """~/.agentbreeder/stacks/<id>/grants/<env>/<server>/ (spec §7.7.2)."""
    state.env_dir(environment)  # validates the name
    return state.directory / "grants" / environment / server


def binding_secret(env_name: str) -> str:
    return "binding-" + env_name.lower().replace("_", "-")


def host_database_secrets(plan: Plan) -> dict[str, bytes]:
    """`agent-database`: the `DATABASE_URL` binding seen from the host. `console-database`: the
    `console` output of that binding's service, when its `console_database` param is set
    (`service/postgres-pgvector@1.2.0`). A binding whose port is not exposed to the host gets
    neither: CLI children cannot reach it."""
    binding = plan.locked.stack.environment.bindings.get(DATABASE_BINDING)
    if binding is None:
        return {}
    reference = Reference.parse(binding.ref, DATABASE_BINDING)
    spec = plan.spec(reference.service)
    port = spec.outputs[reference.output].port
    if port is None or "host" not in spec.ports[port].scopes:
        return {}
    values = {AGENT_SECRET: plan.output(reference, "host")}
    console = spec.outputs.get(CONSOLE_OUTPUT)
    configured = plan.locked.resolved.params[reference.service].get(CONSOLE_PARAM)
    if console is not None and console.operator_only and isinstance(configured, str):
        values[CONSOLE_SECRET] = plan.output(Reference(reference.service, CONSOLE_OUTPUT), "host")
    return values


def documents(plan: Plan, state: StackState, environment: str) -> tuple[dict, dict]:
    stack = plan.locked.stack
    env = stack.environment
    gateway = env.gateway
    gateway_spec = plan.spec(gateway)
    master = gateway_spec.outputs["master_key"].secret
    master_path = state.directory / "service-secrets" / gateway / master
    services = {}
    for key, template in sorted(env.services.items()):
        where = f"environment.services.{key}"
        values = {
            name: plan.output(Reference.parse(name, where), "agents").decode()
            for name in placeholders(template, where)
        }
        services[key] = expand(template, values)
    agents = {}
    principals = {}
    for agent, demo in sorted(env.agents.items()):
        entry = {
            "kind": "agent",
            "enabled": True,
            "environment": environment,
            "review_by": demo.review_by,
            "sponsors": [{"id": demo.sponsor, "name": demo.sponsor, "active": True}],
        }
        identity = {"provider": PROVIDER, "ref": agent, "environment": environment}
        agents[agent] = {"identity": identity}
        if demo.mcp:
            agents[agent]["mcp"] = {server: sorted(tools) for server, tools in demo.mcp.items()}
        principals[agent] = dict(entry)
        if demo.candidate:
            agents[agent]["candidate_identity"] = {**identity, "ref": f"{agent}-candidate"}
            principals[f"{agent}-candidate"] = dict(entry)
    environment_doc = {
        "targets": ["local"],
        "gateway": gateway,
        "models": list(env.models),
        "ceilings": env.ceilings.model_dump(),
        "network": plan.networks["agents"],
        "agents": agents,
    }
    if services:
        environment_doc["services"] = services
    cluster = json.loads(state.get("kubernetes") or "null")
    if cluster:
        environment_doc["targets"] = ["local", "kubernetes"]
        environment_doc["kubernetes"] = cluster
    if env.bindings:
        environment_doc["bindings"] = {
            name: "secret://" + binding_secret(name) for name in sorted(env.bindings)
        }
    if env.eval_database is not None:
        environment_doc["eval_database"] = "secret://" + EVAL_SECRET
    if plan.locked.mcp:
        environment_doc["mcp_relays"] = {
            server: {
                "ref": entry.ref,
                "url": plan.relay_url(server),
                "grants_dir": str(grants_dir(state, environment, server)),
            }
            for server, entry in sorted(plan.locked.mcp.items())
        }
    operator = {
        "enforcement": "enforce",
        "default_environment": environment,
        "require_sponsor": True,
        "providers": {
            PROVIDER: {"type": "static", "authority": PROVIDER, "file": "principals.yaml"}
        },
        "gateways": {
            gateway: {
                "type": "litellm",
                "url": plan.url(gateway, "url", "host"),
                "agent_url": plan.url(gateway, "url", "agents"),
                "master_key": f"file:{master_path}",
            }
        },
        "environments": {environment: environment_doc},
    }
    try:
        OperatorModel.model_validate(operator)
    except ValidationError as error:
        fields = sorted({".".join(str(p) for p in e["loc"][:3]) for e in error.errors()})
        raise ServiceError(
            "OPERATOR_CONFIG_INVALID",
            f"the generated operator config is invalid at {', '.join(fields)}",
        ) from None
    return operator, {"principals": principals}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(plan: Plan, state: StackState, environment: str) -> Path:
    """Write bindings, operator.yaml and principals.yaml for `environment`; return the config."""
    operator, principals = documents(plan, state, environment)
    directory = private_dir(state.env_dir(environment))
    header = (
        f"# Generated by `agentbreeder services up` for stack {state.id}, environment "
        f"{environment}.\n# Local demo identities only. Do not edit: up refuses to overwrite a "
        "changed file.\n"
    )
    files = {
        "operator.yaml": (header + _dump(operator)).encode(),
        "principals.yaml": (header + _dump(principals)).encode(),
    }
    for name in files:
        path = directory / name
        recorded = state.get(f"written:{environment}:{name}")
        if path.exists() or path.is_symlink():
            current = path.read_bytes() if path.is_file() and not path.is_symlink() else b""
            if recorded is None or _digest(current) != recorded:
                raise ServiceError(
                    "OPERATOR_CONFIG_MODIFIED",
                    f"{path} was changed or not written by services up; move it away to "
                    "let up regenerate it",
                )
    deploy_state = private_dir(state.deploy_state(environment))
    env = plan.locked.stack.environment
    try:
        for name, binding in sorted(env.bindings.items()):
            value = plan.output(Reference.parse(binding.ref, name), "agents")
            secret_files.set_secret(deploy_state, binding_secret(name), value)
        if env.eval_database is not None:
            value = plan.output(Reference.parse(env.eval_database.ref, "eval_database"), "host")
            secret_files.set_secret(deploy_state, EVAL_SECRET, value)
        host = host_database_secrets(plan)
        for name in (AGENT_SECRET, CONSOLE_SECRET):
            if name in host:
                secret_files.set_secret(deploy_state, name, host[name])
            else:  # a removed binding or console role: no stale credential for CLI children
                secret_files.delete_secret(deploy_state, name)
    except secret_files.SecretError as error:
        raise ServiceError(error.code, str(error)) from None
    for name, data in files.items():
        write_private(directory / name, data)
        state.put(f"written:{environment}:{name}", _digest(data))
    return directory / "operator.yaml"


class _NoAliases(yaml.SafeDumper):
    """The operator config loader refuses anchors and aliases, which safe_dump emits for an
    object used twice (an agent and its candidate share sponsors)."""

    def ignore_aliases(self, data) -> bool:
        return True


def _dump(document: dict) -> str:
    return yaml.dump(document, Dumper=_NoAliases, sort_keys=False)
