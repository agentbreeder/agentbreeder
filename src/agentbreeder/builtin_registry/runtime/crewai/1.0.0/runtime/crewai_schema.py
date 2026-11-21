"""A tool's declared JSON Schema as the pydantic model CrewAI's `BaseTool.args_schema` needs
(multi-framework spec §8.5).

CrewAI shows the model `args_schema.model_json_schema()`, made strict: every property listed as
required, and an optional one allowed to be `null`. So an optional argument here is
`T | None = None`, and the adapter drops a `null` the model sends for it (`given`), which is how
"not given" reads in strict mode. The model is only the schema shown to the model: the kit checks
every call against the declared schema itself (§7.3).

What it cannot express exactly, it refuses (`SchemaError`, so the runtime does not start):
`$ref`, `anyOf`/`oneOf`/`allOf`/`not`, `patternProperties`, tuple `items`, a type list other
than `[T, "null"]`, a nested object with declared properties, and property names pydantic cannot
carry as fields. Everything Northwind's and the starters' tools declare is supported: string,
integer, number, boolean, array (of those), free-form object, `enum`, `description`,
`required`, `default`.
"""

from __future__ import annotations

import keyword
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, create_model

SCALARS: dict[str, Any] = {"string": str, "integer": int, "number": float, "boolean": bool}
UNSUPPORTED = ("$ref", "anyOf", "oneOf", "allOf", "not", "patternProperties", "if", "$defs")
FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
# Pydantic reserves these on every model.
RESERVED = {name for name in dir(BaseModel) if not name.startswith("__")} | {"model_config"}


class SchemaError(ValueError):
    pass


def _type(name: str, prop: Any, where: str) -> tuple[Any, bool]:
    """(Python type, nullable) for one property schema."""
    if not isinstance(prop, dict):
        raise SchemaError(f"{where}: a property schema must be an object")
    for key in UNSUPPORTED:
        if key in prop:
            raise SchemaError(f"{where}: `{key}` is not supported by runtime/crewai")
    if "enum" in prop:
        values = prop["enum"]
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(v, (str, int, float, bool)) and v is not None for v in values)
        ):
            raise SchemaError(f"{where}: `enum` must list strings, numbers or booleans")
        return Literal[tuple(values)], False
    kind = prop.get("type")
    nullable = False
    if isinstance(kind, list):
        rest = [k for k in kind if k != "null"]
        if len(rest) != 1 or len(kind) != 2:
            raise SchemaError(f'{where}: a type list must be [T, "null"]')
        kind, nullable = rest[0], True
    if kind in SCALARS:
        return SCALARS[kind], nullable
    if kind == "array":
        items = prop.get("items", {})
        if isinstance(items, list):
            raise SchemaError(f"{where}: tuple `items` is not supported by runtime/crewai")
        if not items:
            return list[Any], nullable
        inner, inner_null = _type(name, items, f"{where}.items")
        return list[Optional[inner] if inner_null else inner], nullable  # noqa: UP045
    if kind == "object":
        if prop.get("properties"):
            raise SchemaError(f"{where}: a nested object with properties is not supported")
        return dict[str, Any], nullable
    raise SchemaError(f"{where}: type {kind!r} is not supported by runtime/crewai")


def args_model(tool: str, parameters: dict) -> type[BaseModel]:
    """The pydantic model for one function's declared parameters."""
    if not isinstance(parameters, dict) or parameters.get("type", "object") != "object":
        raise SchemaError(f"{tool}: parameters must be a JSON Schema object")
    for key in UNSUPPORTED:
        if key in parameters:
            raise SchemaError(f"{tool}: `{key}` is not supported by runtime/crewai")
    properties = parameters.get("properties") or {}
    required = set(parameters.get("required") or [])
    if not isinstance(properties, dict) or not required <= set(properties):
        raise SchemaError(f"{tool}: `required` names a property that is not declared")
    fields: dict[str, Any] = {}
    for name, prop in properties.items():
        if not FIELD.match(name) or keyword.iskeyword(name) or name in RESERVED:
            raise SchemaError(f"{tool}: property name {name!r} is not supported")
        kind, nullable = _type(name, prop, f"{tool}.{name}")
        extra = {"description": prop["description"]} if prop.get("description") else {}
        if name in required:
            fields[name] = (Optional[kind] if nullable else kind, Field(..., **extra))  # noqa: UP045
        else:
            default = prop.get("default")
            fields[name] = (Optional[kind], Field(default, **extra))  # noqa: UP045
    model_name = "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", tool)) or "Args"
    return create_model(f"{model_name}Args", __config__=ConfigDict(extra="forbid"), **fields)


def given(parameters: dict, arguments: dict) -> dict:
    """The arguments the model gave: a `null` for an optional property that does not allow null
    is strict mode's "not given" and is dropped; anything else goes to the kit unchanged."""
    properties = parameters.get("properties") or {}
    required = set(parameters.get("required") or [])
    result = {}
    for name, value in arguments.items():
        prop = properties.get(name)
        if value is None and name not in required and isinstance(prop, dict):
            kind = prop.get("type")
            if not (isinstance(kind, list) and "null" in kind):
                continue
        result[name] = value
    return result
