"""A strict, offline check of Kubernetes' homogeneous aggregate literal rule (#1204).

The API server compiles a ValidatingAdmissionPolicy's CEL with cel-go's
`HomogeneousAggregateLiterals` validator: in a list literal every element, and in a map literal
every key and every value, must have a type equivalent to the first one's. cel-python accepts
mixed literals, which is how three of K2's six policies passed the in-process tests and were
refused by a real API server. No faithful offline cel-go checker is available to the test suite,
so this module is stricter than the API server instead:

- a literal's type is known only for `null`, `true`/`false`, integer and string literals, list and
  map literals built from those, and `dyn(...)` (type `dyn`);
- any other expression (a field, a variable, a call, `'a' + b`) has a type unique to it;
- an empty list or map has a type unique to it;
- a list or map literal with two or more elements (or values) passes only when all of them have
  one and the same known type. Keys are checked the same way.

Anything this accepts, cel-go's validator accepts; it may refuse a literal the API server would
take (two fields of one type, say), which the policy then has to wrap in `dyn()`. The opt-in
`kind` test remains the measurement.
"""

from __future__ import annotations

import itertools
import re

from celpy.celparser import CELParser
from lark import Token, Tree

_PARSER = CELParser()
_COUNTER = itertools.count()
_PASS_THROUGH = {
    "expr",
    "conditionalor",
    "conditionaland",
    "relation",
    "addition",
    "multiplication",
    "unary",
    "member",
    "primary",
}


def _unique(what: str) -> str:
    return f"?{what}{next(_COUNTER)}"


def _literal_type(token: Token) -> str:
    text = str(token)
    if text in ("true", "false"):
        return "bool"
    if text == "null":
        return "null"
    if re.fullmatch(r"-?[0-9]+|0x[0-9a-fA-F]+", text):
        return "int"
    if re.fullmatch(r"[0-9]+[uU]", text):
        return "uint"
    if text[:1] in ("'", '"') or text[:2].lower() in ("r'", 'r"'):
        return "string"
    if text[:2].lower() in ("b'", 'b"'):
        return "bytes"
    return _unique("literal")


def _known(kind: str) -> bool:
    return "?" not in kind


def _join(kinds: list[str], where: str, errors: list[str]) -> str:
    if not kinds:
        return _unique("empty")
    if len(kinds) == 1:
        return kinds[0]
    first = kinds[0]
    for index, kind in enumerate(kinds[1:], start=1):
        if kind != first or not _known(kind):
            errors.append(f"{where}: element {index} has type {kind}, the first has {first}")
            return _unique("mixed")
    return first


def _type(node, errors: list[str]) -> str:
    """The static type of `node`, recording every heterogeneous literal inside it."""
    if isinstance(node, Token):
        return _unique("token")
    while node.data in _PASS_THROUGH and len(node.children) == 1:
        child = node.children[0]
        if isinstance(child, Token):
            return _literal_type(child) if node.data in ("primary", "literal") else _unique("t")
        node = child
    if node.data == "literal":
        return _literal_type(node.children[0])
    if node.data == "list_lit":
        items = node.children[0].children if node.children else []
        element = _join([_type(item, errors) for item in items], "list literal", errors)
        return f"list({element})" if items else _unique("emptylist")
    if node.data == "map_lit":
        entries = node.children[0].children if node.children else []
        keys = [_type(item, errors) for item in entries[0::2]]
        values = [_type(item, errors) for item in entries[1::2]]
        if not entries:
            return _unique("emptymap")
        key = _join(keys, "map literal keys", errors)
        value = _join(values, "map literal values", errors)
        return f"map({key}, {value})"
    if node.data == "ident_arg" and str(node.children[0]) == "dyn":
        args = node.children[1].children if len(node.children) > 1 else []
        if len(args) != 1:
            errors.append("dyn() takes exactly one argument")
        for arg in args:
            _type(arg, errors)
        return "dyn"
    for child in node.children:  # an operator, call, macro or selection: check inside it
        if isinstance(child, Tree):
            _type(child, errors)
    return _unique(node.data)


def heterogeneous_literals(expression: str) -> list[str]:
    """Every list or map literal in `expression` that Kubernetes might refuse (see above)."""
    errors: list[str] = []
    _type(_PARSER.parse(expression), errors)
    return errors


def policy_expressions(item: dict) -> list[tuple[str, str]]:
    """`(where, expression)` for every CEL expression of a rendered ValidatingAdmissionPolicy."""
    spec = item["spec"]
    name = item["metadata"]["name"]
    found = []
    for condition in spec.get("matchConditions") or []:
        found.append((f"{name} matchCondition {condition['name']}", condition["expression"]))
    for variable in spec.get("variables") or []:
        found.append((f"{name} variable {variable['name']}", variable["expression"]))
    for index, validation in enumerate(spec.get("validations") or []):
        found.append((f"{name} validation {index}", validation["expression"]))
        if "messageExpression" in validation:
            found.append((f"{name} messageExpression {index}", validation["messageExpression"]))
    return found
