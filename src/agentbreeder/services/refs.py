"""References between a stack's services, checked before anything runs.

Grammar (single pass, no nesting; `{{` and `}}` are literal braces):
  {params.NAME}            a service's own string/db_name param   (service env only)
  {inputs.NAME}            a url input wired by the stack          (service env only)
  {SERVICE.OUTPUT}         another service's url output            (stack environment.services)
A wire or binding is a bare reference `SERVICE.OUTPUT` or `SERVICE.OUTPUT.ITEM` (for outputs
declared `per:` a list param).

Taint: a secret output, a secret input or a secret never appears inside a template. Secret
outputs may only be wired to secret inputs, or used as a stack `bindings` / `eval_database`
value, as a whole. This keeps values out of env, `docker inspect`, labels and generated files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from agentbreeder.services.schema import (
    DB_NAME,
    RESERVED,
    ServiceError,
    ServiceSpec,
    Stack,
)

PLACEHOLDER = re.compile(r"\{\{|\}\}|\{([^{}]*)\}")
REFERENCE = re.compile(r"[a-z][a-z0-9-]{0,30}\.[a-z][a-z0-9_-]{0,30}(\.[a-z][a-z0-9_]{0,62})?")


@dataclass(frozen=True)
class Reference:
    service: str
    output: str
    item: str | None = None

    @classmethod
    def parse(cls, text: str, where: str) -> Reference:
        if not REFERENCE.fullmatch(text or ""):
            raise ServiceError("REFERENCE_INVALID", f"{where}: expected service.output[.item]")
        parts = text.split(".")
        return cls(parts[0], parts[1], parts[2] if len(parts) == 3 else None)


@dataclass
class Resolved:
    """A validated stack: each service's params, and the dependency order."""

    params: dict[str, dict[str, str | list[str]]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)


def placeholders(template: str, where: str) -> list[str]:
    """The `{...}` names in a template, in order. Unbalanced braces are an error."""
    names = []
    position = 0
    for match in PLACEHOLDER.finditer(template):
        between = template[position : match.start()]
        if "{" in between or "}" in between:
            raise ServiceError("TEMPLATE_INVALID", f"{where}: unbalanced brace")
        if match.group(1) is not None:
            if not match.group(1):
                raise ServiceError("TEMPLATE_INVALID", f"{where}: empty placeholder")
            names.append(match.group(1))
        position = match.end()
    if "{" in template[position:] or "}" in template[position:]:
        raise ServiceError("TEMPLATE_INVALID", f"{where}: unbalanced brace")
    return names


def expand(template: str, values: dict[str, str]) -> str:
    """Substitute placeholders in one pass. Substituted text is never re-scanned."""

    def replace(match: re.Match) -> str:
        if match.group(0) == "{{":
            return "{"
        if match.group(0) == "}}":
            return "}"
        return values[match.group(1)]

    return PLACEHOLDER.sub(replace, template)


def _params(name: str, spec: ServiceSpec, given: dict) -> dict[str, str | list[str]]:
    where = f"services.{name}.with"
    unknown = set(given) - set(spec.params)
    if unknown:
        raise ServiceError("PARAM_UNKNOWN", f"{where}: unknown params {sorted(unknown)}")
    values: dict[str, str | list[str]] = {}
    for key, param in spec.params.items():
        value = given.get(key, param.default)
        if value is None:
            if param.required:
                raise ServiceError("PARAM_MISSING", f"{where}.{key} is required")
            continue
        kind = param.item if param.type == "list" else param.type
        items = value if param.type == "list" else [value]
        if (param.type == "list") != isinstance(value, list):
            raise ServiceError("PARAM_INVALID", f"{where}.{key}: expected a {param.type}")
        for item in items:
            if not isinstance(item, str) or len(item) > 512:
                raise ServiceError("PARAM_INVALID", f"{where}.{key}: expected text")
            if kind == "db_name" and not re.fullmatch(DB_NAME.strip("^$"), item):
                raise ServiceError("PARAM_INVALID", f"{where}.{key}: not a valid database name")
            if kind == "file":
                path = PurePosixPath(item)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise ServiceError(
                        "PARAM_INVALID", f"{where}.{key}: file params are paths inside the project"
                    )
        if param.type == "list" and len(set(items)) != len(items):
            raise ServiceError("PARAM_INVALID", f"{where}.{key}: duplicate items")
        values[key] = value
    return values


def _check_templates(name: str, spec: ServiceSpec, params: dict) -> None:
    allowed = {f"params.{k}" for k, p in spec.params.items() if p.type in ("string", "db_name")}
    allowed |= {f"inputs.{k}" for k, i in spec.inputs.items() if i.type == "url"}
    secret_like = {f"inputs.{k}" for k, i in spec.inputs.items() if i.type == "secret"}
    secret_like |= {f"secrets.{k}" for k in spec.mounted_secrets()}
    # Arguments are never templated: a value placed in argv can be re-parsed by the program
    # (an interpreter's -c, a wrapper), which no denylist can rule out. Values travel in env.
    argv = [(f"command[{i}]", v) for i, v in enumerate(spec.command or [])]
    for step_name, step in (("init", spec.init), ("bootstrap", spec.bootstrap)):
        if step is not None:
            argv += [(f"{step_name}.command[{i}]", v) for i, v in enumerate(step.command)]
    if spec.health is not None and spec.health.command:
        argv += [(f"health.command[{i}]", v) for i, v in enumerate(spec.health.command)]
    for where, text in argv:
        if placeholders(text, f"service {name} {where}"):
            raise ServiceError(
                "TEMPLATE_IN_ARGV",
                f"service {name} {where}: pass values through env, not command arguments",
            )
    texts = [(f"env.{k}", v) for k, v in spec.env.items()]
    for where, text in texts:
        for placeholder in placeholders(text, f"service {name} {where}"):
            if placeholder in secret_like:
                raise ServiceError(
                    "SECRET_IN_TEMPLATE",
                    f"service {name} {where}: secrets are mounted as files, never interpolated",
                )
            if placeholder not in allowed:
                raise ServiceError(
                    "REFERENCE_UNKNOWN", f"service {name} {where}: unknown {{{placeholder}}}"
                )
            # A declared optional param with no value leaves the variable out (plan.environment),
            # like an unwired optional input; a required one fails at resolution.


def _output(stack_specs, params, reference: Reference, where: str):
    if reference.service in RESERVED or reference.service not in stack_specs:
        raise ServiceError("REFERENCE_UNKNOWN", f"{where}: unknown service {reference.service}")
    spec = stack_specs[reference.service]
    output = spec.outputs.get(reference.output)
    if output is None:
        raise ServiceError(
            "REFERENCE_UNKNOWN", f"{where}: {reference.service} has no output {reference.output}"
        )
    if output.operator_only:
        raise ServiceError(
            "OUTPUT_OPERATOR_ONLY",
            f"{where}: {reference.service}.{reference.output} is for AgentBreeder only",
        )
    if output.per is None and reference.item is not None:
        raise ServiceError("REFERENCE_INVALID", f"{where}: {reference.output} takes no item")
    if output.per is not None:
        items = params[reference.service].get(output.per) or []
        if reference.item is None or reference.item not in items:
            raise ServiceError(
                "REFERENCE_INVALID",
                f"{where}: {reference.service}.{reference.output} needs one of its "
                f"{output.per} items",
            )
    return output


def _order(edges: dict[str, set[str]]) -> list[str]:
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycle = path[path.index(node) :] + [node]
            raise ServiceError(
                "STACK_CYCLE", "services depend on each other: " + " -> ".join(cycle)
            )
        state[node] = 1
        for dependency in sorted(edges[node]):
            visit(dependency, path + [node])
        state[node] = 2
        order.append(node)

    for node in sorted(edges):
        visit(node, [])
    return order


def check(stack: Stack, specs: dict[str, ServiceSpec]) -> Resolved:
    """Validate params, templates, wiring, taint and the dependency graph of a stack whose
    service refs have been resolved to `specs` (service name -> its service.yaml)."""
    resolved = Resolved()
    for name, entry in stack.services.items():
        resolved.params[name] = _params(name, specs[name], dict(entry.with_))
    edges: dict[str, set[str]] = {name: set() for name in stack.services}
    for name, entry in stack.services.items():
        spec = specs[name]
        _check_templates(name, spec, resolved.params[name])
        unknown = set(entry.wire) - set(spec.inputs)
        if unknown:
            raise ServiceError(
                "INPUT_UNKNOWN", f"services.{name}.wire: {name} has no inputs {sorted(unknown)}"
            )
        for input_name, input_spec in spec.inputs.items():
            where = f"services.{name}.wire.{input_name}"
            text = entry.wire.get(input_name)
            if text is None:
                if not input_spec.optional:
                    raise ServiceError("INPUT_MISSING", f"{where} must be wired")
                continue
            reference = Reference.parse(text, where)
            if reference.service == name:
                raise ServiceError("STACK_CYCLE", f"{where}: a service can't depend on itself")
            output = _output(specs, resolved.params, reference, where)
            if output.type != input_spec.type:
                raise ServiceError(
                    "WIRE_TYPE",
                    f"{where}: a {input_spec.type} input needs a {input_spec.type} output",
                )
            edges[name].add(reference.service)
    resolved.order = _order(edges)

    env = stack.environment
    gateway = specs[env.gateway]
    if gateway.provides != "gateway/litellm":
        raise ServiceError(
            "GATEWAY_CONTRACT",
            "environment.gateway must name a service that provides gateway/litellm",
        )
    for key, text in env.services.items():
        where = f"environment.services.{key}"
        for placeholder in placeholders(text, where):
            output = _output(specs, resolved.params, Reference.parse(placeholder, where), where)
            if output.type != "url":
                raise ServiceError(
                    "SECRET_IN_TEMPLATE", f"{where}: only url outputs may appear in a template"
                )
    bound = [(f"environment.bindings.{k}", v.ref) for k, v in env.bindings.items()]
    if env.eval_database is not None:
        bound.append(("environment.eval_database", env.eval_database.ref))
    for where, text in bound:
        output = _output(specs, resolved.params, Reference.parse(text, where), where)
        if output.type != "secret":
            raise ServiceError("BINDING_TYPE", f"{where}: bindings take a secret output")
    return resolved
