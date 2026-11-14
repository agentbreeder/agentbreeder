"""`agentbreeder mcp grant|revoke AGENT SERVER TOOL...`: edit one agent's MCP tool grant in the
project's `stack.yaml` (M15 #831; console spec §4.3).

The grant is `environment.agents.<AGENT>.mcp.<SERVER>: [tools]`. Only that value changes: its
span in the file is found from the YAML node marks and replaced with a flow mapping
(`{github: [get_me, list_issues]}`), or one `mcp:` line is added to the agent's entry. The rest of
the file, comments included, is kept byte for byte; comments inside the old `mcp:` value are
dropped (the diff shows them). The rewritten text must parse to exactly the old document with only
that grant changed, and validate as a stack, or nothing is written (`STACK_EDIT_AMBIGUOUS`).

`grant` refuses tools the server's `mcp/<name>@X.Y.Z` version doesn't declare
(`MCP_TOOL_UNDECLARED`, the check `services up` makes too). Both verbs are idempotent: nothing
to change is `changed: false` and no write. Like `agent set-ref`, `--dry-run` shows the diff and
`--expect-base` (S-04) refuses unless the file is still the bytes that were reviewed; the write is
an atomic replace. The record of the change is the file itself and git (S-30), not an audit chain.

Nothing here talks to a relay or a deployment. A grant reaches an agent after `services up` (the
relay's config) and a deploy that requests the tool; deploy refuses tools the stack doesn't grant
(`MCP_TOOL_NOT_GRANTED`). A revoke of a tool an active deployment holds is written, and reported in
`in_use` from the stack's grant files (read only): `services up` refuses to narrow it
(`MCP_GRANT_IN_USE`) until that deployment is removed.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from agentbreeder import mcp as mcp_kind
from agentbreeder import registry
from agentbreeder.registry import Ref, RegistryError, read_project_file
from agentbreeder.services.schema import MAX_BYTES, MCP_TOOL, ServiceError, Stack, _problems
from agentbreeder.setref import _replace, _unified, digest
from agentbreeder.strict_yaml import StrictYamlError, load_text

STACK_FILE = "stack.yaml"
SUMMARY_VERSION = 1


class GrantError(ValueError):
    def __init__(self, code: str, message: str):
        message = registry.clean(message)[0]
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def change(
    project_dir: Path,
    verb: str,
    agent: str,
    server: str,
    tools: list[str],
    *,
    expect_base: str | None = None,
    dry_run: bool = False,
    state_root: Path | None = None,
) -> dict:
    """Grant (`verb="grant"`) or revoke TOOLS of SERVER for AGENT in `<project_dir>/stack.yaml`.
    Returns the summary `mcp grant|revoke --json` prints."""
    if verb not in ("grant", "revoke"):
        raise ValueError(verb)
    if expect_base is not None and not registry.DIGEST.fullmatch(expect_base):
        raise GrantError("DIGEST_INVALID", "--expect-base must be sha256:<64 hex digits>")
    if not tools:
        raise GrantError("MCP_TOOL_INVALID", "name at least one tool")
    for tool in tools:
        if not re.fullmatch(MCP_TOOL.strip("^$"), tool):
            raise GrantError("MCP_TOOL_INVALID", f"{registry._shown(tool)!r} is not a tool name")
    tools = list(dict.fromkeys(tools))
    path = project_dir / STACK_FILE
    try:
        raw = read_project_file(path, MAX_BYTES)
    except RegistryError as error:
        raise GrantError(error.code, str(error).split("] ", 1)[-1]) from None
    if raw is None:
        raise GrantError("STACK_MISSING", f"no {STACK_FILE} in {registry._shown(project_dir)}")
    base = digest(raw)
    if expect_base is not None and base != expect_base:
        raise GrantError(
            "BASE_MISMATCH",
            f"{STACK_FILE} is {base}, not the reviewed {expect_base}: it changed after the "
            "preview; review it again",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise GrantError("STACK_INVALID", f"{STACK_FILE} is not UTF-8 text") from None
    data, stack = _parse(text, STACK_FILE)
    if agent not in stack.environment.agents:
        raise GrantError(
            "MCP_AGENT_UNKNOWN",
            f"{STACK_FILE} has no environment.agents.{registry._shown(agent)} (add the agent's "
            "entry, with its sponsor and review_by, by hand first)",
        )
    if server not in stack.mcp:
        raise GrantError(
            "MCP_UNKNOWN", f"{STACK_FILE} has no mcp.{registry._shown(server)} server entry"
        )
    declared = _declared(project_dir, stack.mcp[server].ref)
    before = dict(stack.environment.agents[agent].mcp)
    current = list(before.get(server, []))
    if verb == "grant":
        undeclared = [tool for tool in tools if tool not in declared]
        if undeclared:
            raise GrantError(
                "MCP_TOOL_UNDECLARED",
                f"{stack.mcp[server].ref} doesn't declare {len(undeclared)} tool(s): "
                f"{', '.join(undeclared)}",
            )
        moved = [tool for tool in tools if tool not in current]
        updated = current + moved
    else:
        moved = [tool for tool in tools if tool in current]
        updated = [tool for tool in current if tool not in moved]
    after = dict(before)
    if updated:
        after[server] = updated
    else:
        after.pop(server, None)
    result = {
        "schema_version": SUMMARY_VERSION,
        "file": registry._absolute(path),
        "verb": verb,
        "agent": agent,
        "server": server,
        "ref": stack.mcp[server].ref,
        "added" if verb == "grant" else "removed": moved,
        "tools": [{"name": tool, "effect": _effect(declared, tool)} for tool in updated],
        "changed": bool(moved),
        "base_sha256": base,
        "sha256": base,
        "diff": "",
        "dry_run": dry_run,
        "in_use": [],
    }
    if not moved:
        return result
    edited = _edit(text, data, agent, after)
    expected = copy.deepcopy(data)
    expected["environment"]["agents"][agent]["mcp"] = after
    check, _ = _parse(edited, f"{STACK_FILE} after the change", code="STACK_EDIT_AMBIGUOUS")
    if check != expected:
        raise GrantError(
            "STACK_EDIT_AMBIGUOUS",
            f"rewriting environment.agents.{agent}.mcp would change more than that grant; "
            "edit stack.yaml by hand",
        )
    new = edited.encode("utf-8")
    if len(new) > MAX_BYTES:
        raise GrantError(
            "STACK_TOO_LARGE",
            f"{STACK_FILE} would be {len(new)} bytes, over the {MAX_BYTES // 1024} KiB limit "
            "every command reads; shorten it first",
        )
    result["sha256"] = digest(new)
    result["diff"] = registry.clean(_unified(text, edited, STACK_FILE))[0]
    if verb == "revoke":
        result["in_use"] = in_use(project_dir, stack.name, agent, server, moved, state_root)
    if not dry_run:
        try:
            _replace(path, raw, new, MAX_BYTES)
        except RegistryError as error:
            raise GrantError(error.code, str(error).split("] ", 1)[-1]) from None
    return result


def _parse(text: str, where: str, code: str = "STACK_INVALID") -> tuple[dict, Stack]:
    try:
        data = load_text(text)
    except StrictYamlError as error:
        raise GrantError(code, f"{where}: {error}") from None
    try:
        return data, Stack.model_validate(data)
    except ValidationError as error:
        raise GrantError(code, f"{where}: {_problems(error)}") from None


def _declared(project_dir: Path, ref_text: str) -> dict:
    """The tools the server's version declares: name -> effect."""
    try:
        hit = registry.resolve(Ref.parse(ref_text), registry.roots(project_dir))
        spec = mcp_kind.load_mcp(hit.path)
    except (RegistryError, ServiceError) as error:
        raise GrantError(error.code, str(error).split("] ", 1)[-1]) from None
    return {name: tool.effect for name, tool in spec.tools.items()}


def _effect(declared: dict, tool: str) -> str | None:
    return declared.get(tool)  # None: granted before, no longer declared (up refuses it)


# -- the text edit ------------------------------------------------------------------------------


def _scalar(value: str) -> str:
    """A plain scalar when YAML reads it back as that same string, else JSON-quoted. Names here
    already match the server/tool patterns, so the load itself cannot fail."""
    return value if yaml.safe_load(value) == value else json.dumps(value)


def flow(grant: dict[str, list[str]]) -> str:
    items = (
        f"{_scalar(server)}: [{', '.join(_scalar(t) for t in tools)}]"
        for server, tools in grant.items()
    )
    return "{" + ", ".join(items) + "}"


def _child(node: yaml.Node, key: str, where: str) -> tuple[yaml.Node, yaml.Node]:
    if not isinstance(node, yaml.MappingNode):
        raise GrantError("STACK_EDIT_AMBIGUOUS", f"{where} is not a mapping")
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == key:
            return key_node, value_node
    raise GrantError("STACK_EDIT_AMBIGUOUS", f"{where} has no {key}")


def _content_end(node: yaml.Node) -> int:
    """Where a node's own text ends: a block collection's end mark runs on to the next token, so
    use its last child's end instead."""
    if isinstance(node, yaml.ScalarNode) or node.flow_style or not node.value:
        return node.end_mark.index
    last = node.value[-1]
    return _content_end(last[1] if isinstance(last, tuple) else last)


def _edit(text: str, data: dict, agent: str, after: dict[str, list[str]]) -> str:
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:  # pragma: no cover - load_text already parsed this text
        raise GrantError("STACK_EDIT_AMBIGUOUS", f"{STACK_FILE} can't be parsed again") from None
    _, environment = _child(root, "environment", "the document")
    _, agents = _child(environment, "agents", "environment")
    _, entry = _child(agents, agent, "environment.agents")
    value = flow(after)
    if "mcp" in data["environment"]["agents"][agent]:
        key, old = _child(entry, "mcp", f"environment.agents.{agent}")
        start, end = key.end_mark.index, _text_end(text, old)
        return text[:start] + ": " + value + text[end:]
    if not isinstance(entry, yaml.MappingNode) or not entry.value:  # pragma: no cover - schema
        raise GrantError(
            "STACK_EDIT_AMBIGUOUS", f"environment.agents.{agent} is not a mapping to add mcp to"
        )
    if entry.flow_style:
        close = entry.end_mark.index - 1
        if text[close] != "}":  # pragma: no cover - a flow mapping ends with its brace
            raise GrantError("STACK_EDIT_AMBIGUOUS", f"environment.agents.{agent} is unusual")
        return text[:close].rstrip() + f", mcp: {value}" + text[close:]
    indent = entry.value[0][0].start_mark.column
    end = _text_end(text, entry)
    at, eol = _line_end(text, end)
    return text[:at] + eol + " " * indent + f"mcp: {value}" + text[at:]


def _line_end(text: str, start: int) -> tuple[int, str]:
    """Where the line holding `start` ends, and its own line break (LF, CRLF or CR); at the end
    of the file, the break the file uses most."""
    newline, cr = text.find("\n", start), text.find("\r", start)
    if cr >= 0 and (newline < 0 or cr < newline):
        return cr, "\r\n" if text[cr + 1 : cr + 2] == "\n" else "\r"
    if newline >= 0:
        return newline, "\n"
    crlf = text.count("\r\n")
    common = max(
        (("\r\n", crlf), ("\r", text.count("\r") - crlf), ("\n", text.count("\n") - crlf)),
        key=lambda item: item[1],
    )
    return len(text), common[0] if common[1] else "\n"


def _text_end(text: str, node: yaml.Node) -> int:
    """`_content_end` without the line break(s) a block scalar (`|`, `>`) ends with."""
    return len(text[: _content_end(node)].rstrip())


# -- deployments holding a revoked tool ---------------------------------------------------------


def in_use(
    project_dir: Path,
    stack_name: str,
    agent: str,
    server: str,
    revoked: list[str],
    state_root: Path | None = None,
) -> list[dict]:
    """Active deployments of AGENT whose grant file for SERVER holds a revoked tool: [{deployment,
    environment, tools}]. Read only; nothing when the stack was never brought up."""
    from agentbreeder.services.state import StackState

    directory = StackState.locate(stack_name, project_dir.resolve(), state_root)
    root = directory / "grants" if directory is not None else None
    if root is None or not root.is_dir() or root.is_symlink():
        return []
    found = []
    for env_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink()):
        server_dir = env_dir / server
        if server_dir.is_symlink():
            continue
        for grant in mcp_kind.grants(server_dir).values():
            held = grant.get("tools")
            deployment = grant.get("deployment")
            malformed = not isinstance(held, list)  # the engine refuses on these alone
            if malformed:
                # `services up` refuses any change while such a file exists (engine
                # `_check_in_use`); `reconcile` or `remove` clears it.
                hit = list(revoked)
            elif grant.get("agent") != agent:
                continue
            else:
                hit = [t for t in revoked if t in held]
            if hit:
                found.append(
                    {
                        "deployment": registry._shown(deployment)
                        if isinstance(deployment, str)
                        else None,
                        "environment": registry._shown(env_dir.name),
                        "tools": hit,
                    }
                )
    return found
