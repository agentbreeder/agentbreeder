"""Repin MCP tools from the console (#964, console half): the read's argv, the strict JSON parse,
the checks the server makes on `services pins DIR --json` before it keeps anything, and the
review text, rendered here in Python (spec `2026-11-14-mcp-repin-expect-pins.md` §7).

The console never computes what a repin accepts. It runs `services pins --json`, checks that the
answer is self-consistent (every definition hashes to its pin, the display part matches the
hashed `review` document, the digest is that document's), keeps the digest per project, and
starts `services up --repin --expect-pins DIGEST`, which pins only when the live definitions
still produce it (`PINS_CHANGED` otherwise). The browser gets ready-made lines and sets them as
text: re-serializing a definition in JavaScript would change `1.0` to `1` and round integers
above 2^53, so a changed hash could show as an empty diff.

`tool_hash`, `canonical`, `review_lines` and `review_diff` are copies of `services.mcp_stack` and
`services.plan` (the server does not load either; a test keeps them equal).

Imports only `center.mcpgrants` (for the server and tool name rules)."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Any

from agentbreeder.center.mcpgrants import SERVER, TOOL

MAX_BYTES = 2 * 1024 * 1024  # stdout cap for the read (S-35 #13)
REVIEW_VERSION = 1  # `mcp_stack.REVIEW_VERSION`
HEX = re.compile(r"[0-9a-f]{64}")
PIN = re.compile(r"sha256:[0-9a-f]{64}")
MAX_SERVERS = 64
INVALID = {
    "code": "PINS_REVIEW_INVALID",
    "message": "The pins review does not match its contract; review the change in a terminal "
    "(`agentbreeder services pins`)",
}
STALE = (
    "This repin review is no longer current (a newer review, a start, or a console restart); "
    "review it again"
)


def timeout(relays: int) -> int:
    """The read's timeout: each relay's probe may take up to 180 s (`services pins`)."""
    return 60 + 180 * max(relays, 0)


def pins_argv(project: dict) -> list[str]:
    return ["services", "pins", project["path"], "--json"]


def terminal_command(project: dict) -> str:
    return f"agentbreeder services pins {project['path']}"


# -- strict JSON, as the relay parses it ----------------------------------------------------------


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _constant(name: str):
    raise ValueError(f"{name} is not JSON")


def loads(text: str) -> Any:
    """`json.loads` refusing duplicate keys, NaN and Infinity (ValueError)."""
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)


# -- copies of the CLI's hashing and review text (a test keeps them equal) ------------------------


def tool_hash(tool: dict) -> str:
    """`mcp_stack.tool_hash`."""
    text = json.dumps(
        tool, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value) -> bytes:
    """`mcp_stack.canonical`."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def review_digest(document: dict) -> str:
    """`mcp_stack.pins_review_digest`."""
    return hashlib.sha256(canonical(document)).hexdigest()


WRAP = 100  # `plan.WRAP`
WRAP_MARK = "↩"  # `plan.WRAP_MARK`
DIFF_LINES = 2000  # `plan.DIFF_LINES`
_TOKEN = re.compile(r"\\u[0-9a-fA-F]{4}|\\.?|.", re.DOTALL)


def _definition(definition) -> list[str]:
    """`plan._definition`: ASCII-escaped, sorted, every field."""
    return json.dumps(definition, indent=2, sort_keys=True, ensure_ascii=True).splitlines()


def review_lines(definition) -> list[str]:
    """`plan.review_lines`: long lines hard-wrapped with a marker, never inside an escape."""
    lines = []
    for line in _definition(definition):
        if len(line) <= WRAP:
            lines.append(line)
            continue
        chunk = ""
        for match in _TOKEN.finditer(line):
            token = match.group()
            if len(chunk) + len(token) > WRAP:
                lines.append(chunk + WRAP_MARK)
                chunk = ""
            chunk += token
        lines.append(chunk)
    return lines


def review_diff(previous, definition) -> list[str]:
    """`plan.review_diff`."""
    after = review_lines(definition)
    if previous is None:
        return ["(previous definition unavailable; the new one in full:)", *after]
    before = review_lines(previous)
    if len(before) > DIFF_LINES or len(after) > DIFF_LINES:
        return ["(diff too large; the new definition in full:)", *after]
    return list(difflib.unified_diff(before, after, "pinned", "upstream now", lineterm=""))


# -- the checks -----------------------------------------------------------------------------------


class _Invalid(Exception):
    pass


def _need(condition: bool) -> None:
    if not condition:
        raise _Invalid


def _pins(value: Any) -> dict[str, str]:
    _need(isinstance(value, dict))
    for name, pin in value.items():
        _need(isinstance(name, str) and bool(TOOL.fullmatch(name)))
        _need(isinstance(pin, str) and bool(PIN.fullmatch(pin)))
    return value


def _names(value: Any) -> list[str]:
    _need(isinstance(value, list))
    _need(all(isinstance(name, str) and TOOL.fullmatch(name) for name in value))
    _need(len(set(value)) == len(value))
    return value


def _server(name: str, document: Any, shown: Any) -> dict:
    """One server, checked against its part of the hashed document, then rendered."""
    _need(isinstance(document, dict) and set(document) == {"identity", "pinned", "accept", "drop"})
    identity = document["identity"]
    _need(isinstance(identity, dict) and isinstance(identity.get("origin"), str))
    pinned, accept = _pins(document["pinned"]), _pins(document["accept"])
    drop = _names(document["drop"])
    _need(set(drop) <= set(pinned) and not set(drop) & set(accept))
    _need(isinstance(shown, dict))
    _need(shown.get("origin") == identity["origin"])
    new, changed = shown.get("new"), shown.get("changed")
    _need(isinstance(new, dict) and isinstance(changed, dict))
    _need(not set(new) & set(changed) and set(new) | set(changed) == set(accept))
    _need(shown.get("drop") == drop)
    missing = _names(shown.get("missing"))
    _need(set(drop) <= set(missing))
    tools = []
    for tool, item in sorted(new.items()):
        _need(isinstance(item, dict) and isinstance(item.get("definition"), dict))
        _need(tool not in pinned and item.get("sha256") == accept[tool])
        _need(tool_hash(item["definition"]) == accept[tool])
        tools.append(
            {
                "name": tool,
                "change": "new",
                "pinned": "",
                "sha256": accept[tool][7:23],
                "lines": review_lines(item["definition"]),
            }
        )
    for tool, item in sorted(changed.items()):
        _need(isinstance(item, dict) and isinstance(item.get("definition"), dict))
        _need(tool in pinned and item.get("pinned") == pinned[tool])
        _need(item.get("sha256") == accept[tool] != pinned[tool])  # else not changed
        _need(tool_hash(item["definition"]) == accept[tool])
        previous = item.get("previous")
        if previous is not None:  # display only, but it must be what `pinned` binds
            _need(isinstance(previous, dict) and tool_hash(previous) == pinned[tool])
        tools.append(
            {
                "name": tool,
                "change": "changed",
                "pinned": pinned[tool][7:23],
                "sha256": accept[tool][7:23],
                "lines": review_diff(previous, item["definition"]),
            }
        )
    # `state` and `same` are derived from the hashed document; `missing` is display only.
    return {
        "name": name,
        "origin": identity["origin"],
        "built_in": identity["origin"] == "built-in",
        "state": "drift" if changed or drop else "ok",
        "tools": tools,
        "drop": list(drop),
        "missing": [tool for tool in missing if tool not in drop],
        "same": sorted(set(pinned) - set(accept) - set(drop)),
    }


def verify(data: object) -> tuple[dict | None, dict | None]:
    """`(summary, None)` for a `services pins --json` answer that is consistent with its own
    hashed review and digest, else `(None, INVALID)`; nothing else from the answer goes on. The
    summary is `{stack, digest, changes, servers: [{name, origin, built_in, state, tools: [{name,
    change, pinned, sha256, lines}], drop, missing, same}]}`."""
    try:
        return _verify(data), None
    except (_Invalid, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None, INVALID


def _verify(data: object) -> dict:
    _need(isinstance(data, dict) and type(data.get("schema_version")) is int)
    _need(data["schema_version"] == 1)
    digest, stack = data.get("digest"), data.get("stack")
    review, servers = data.get("review"), data.get("servers")
    _need(isinstance(digest, str) and bool(HEX.fullmatch(digest)))
    _need(isinstance(stack, str) and 0 < len(stack) <= 200)
    _need(isinstance(review, dict) and set(review) == {"v", "stack", "servers"})
    _need(type(review["v"]) is int and review["v"] == REVIEW_VERSION)
    _need(review["stack"] == stack)
    documents = review["servers"]
    _need(isinstance(documents, dict) and isinstance(servers, dict))
    _need(set(documents) == set(servers) and len(servers) <= MAX_SERVERS)
    _need(all(isinstance(name, str) and SERVER.fullmatch(name) for name in servers))
    rows = [_server(name, documents[name], servers[name]) for name in sorted(servers)]
    _need(review_digest(review) == digest)
    return {
        "stack": stack,
        "digest": digest,
        "changes": any(row["tools"] or row["drop"] for row in rows),
        "servers": rows,
    }
