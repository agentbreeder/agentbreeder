"""Tool definition for AgentBreeder agents.

Supports creating tools from Python functions (with automatic schema
generation from type hints) or from registry references.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

# Type-hint to JSON Schema mapping (reuses the same logic as mcp.py)
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json_schema(py_type: type) -> str:
    """Convert a Python type annotation to a JSON Schema type string."""
    return _TYPE_MAP.get(py_type, "string")


def _build_input_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Auto-generate a JSON Schema object from a function's type hints."""
    hints = get_type_hints(func)
    sig = inspect.signature(func)

    properties: dict[str, dict[str, str]] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name == "return":
            continue
        py_type = hints.get(param_name, str)
        json_type = _python_type_to_json_schema(py_type)
        properties[param_name] = {"type": json_type}

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


@dataclass
class ToolConfig:
    """Serializable tool configuration."""

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML output."""
        if self.ref:
            return {"ref": self.ref}
        d: dict[str, Any] = {"name": self.name}
        if self.description:
            d["description"] = self.description
            d["type"] = "function"
        if self.input_schema:
            d["schema"] = self.input_schema
        return d


class Tool:
    """Define a tool as a Python function with automatic schema generation."""

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        fn: Callable[..., Any] | None = None,
        ref: str | None = None,
    ) -> None:
        self.name = name or ""
        self.description = description or ""
        self.fn = fn
        self.ref = ref
        self.input_schema: dict[str, Any] = {}
        self.output_schema: dict[str, Any] = {}

        if fn is not None:
            if not self.name:
                self.name = fn.__name__
            if not self.description:
                self.description = (fn.__doc__ or "").strip()
            self.input_schema = _build_input_schema(fn)

    @staticmethod
    def from_function(
        fn: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
    ) -> Tool:
        """Create a Tool from a Python function. Extracts schema from type hints."""
        return Tool(name=name, description=description, fn=fn)

    @staticmethod
    def from_ref(ref: str) -> Tool:
        """Create a Tool from a registry reference (e.g. 'tools/my-tool')."""
        # Extract a short name from the ref for display
        short_name = ref.rsplit("/", 1)[-1] if "/" in ref else ref
        return Tool(name=short_name, ref=ref)

    def to_config(self) -> ToolConfig:
        """Convert to a ToolConfig dataclass."""
        return ToolConfig(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            ref=self.ref,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for YAML output."""
        return self.to_config().to_dict()
