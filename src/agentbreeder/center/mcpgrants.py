"""Grant and revoke an agent's MCP tools from the console (#831, M15 console half, part 2): pure
request checks, the CLI argv and the answer checks. The console never edits `stack.yaml` itself;
it runs `mcp grant|revoke AGENT SERVER TOOL... --project DIR (--dry-run | --expect-base D)
--json` (contract: docs/commands/mcp.md).

Two requests (S-04), as "Move agent to this version": a dry run that returns the diff and the
digest of the `stack.yaml` it read, then the write bound to that digest, which the CLI refuses
when the file changed since the review (`BASE_MISMATCH`). The write is a short write that holds
the project, so it never lands while a job (`services up`, `build`, `release`) reads the stack.

The tools a server's version declares come from its `mcp.yaml`, as `registry show --json` read it
(a CLI child): the text is parsed here, in memory, with the strict YAML loader. The list only
fills the form; the CLI makes the real check (`MCP_TOOL_UNDECLARED`).

Starting services for a stack with MCP servers (#831 part 3) goes through the existing grant
review. A server that is not built in (project or registry-path code) needs the person's consent,
passed as `--allow-project-mcp NAME` (`consent_servers`, `consent_names`): without it `services
up` refuses one whose remote upstream gets a credential (`MCP_PROJECT_CONSENT`), and refuses to pin
any other's first tools without a terminal (`MCP_PINS_NOT_CONFIRMED`), after its relay started. So
the console asks before the job for every such server.

Imports only `registry` and `strict_yaml` (both on the server's allowlist)."""

from __future__ import annotations

import re
from typing import Any

from agentbreeder import registry, strict_yaml
from agentbreeder.center.files import BadRequest

# `services.schema`'s rules (a test keeps them equal; the server does not load `mcpgrant`).
AGENT = re.compile(r"[a-z0-9]([a-z0-9-]{0,51}[a-z0-9])?")
SERVER = re.compile(r"[a-z][a-z0-9_]{0,31}")
TOOL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}")
MAX_TOOLS = 100  # `DemoAgent.mcp_tools`: a grant lists 1-100 unique tools
VERBS = ("grant", "revoke")
EFFECTS = ("read", "write")
KIND = "mcp-grant"
MAX_DIFF = 64 * 1024  # characters; stack.yaml is at most 64 KiB
MAX_IN_USE = 200
INVALID = {
    "code": "MCP_GRANT_INVALID",
    "message": "The mcp grant answer does not match its contract; run `agentbreeder mcp grant` "
    "in a terminal",
}


def grant_request(body: object) -> dict:
    """{verb, agent, server, tools, base|None} from the browser's JSON, or BadRequest. The CLI
    makes the real checks (the agent and server exist, the tools are declared)."""
    if not isinstance(body, dict):
        raise BadRequest("Send a JSON object", "MCP_GRANT_INVALID")
    verb = body.get("verb")
    if verb not in VERBS:
        raise BadRequest("verb is grant or revoke", "MCP_GRANT_INVALID")
    agent, server = body.get("agent"), body.get("server")
    if not isinstance(agent, str) or not AGENT.fullmatch(agent):
        raise BadRequest("Invalid agent name", "MCP_GRANT_INVALID")
    if not isinstance(server, str) or not SERVER.fullmatch(server):
        raise BadRequest("Invalid MCP server name", "MCP_GRANT_INVALID")
    tools = body.get("tools")
    if not isinstance(tools, list) or not 1 <= len(tools) <= MAX_TOOLS:
        raise BadRequest(f"Choose 1 to {MAX_TOOLS} tools", "MCP_TOOL_INVALID")
    if not all(isinstance(tool, str) and TOOL.fullmatch(tool) for tool in tools):
        raise BadRequest("Invalid tool name", "MCP_TOOL_INVALID")
    base = body.get("base")
    if base is not None and base != "":
        if not isinstance(base, str) or not registry.DIGEST.fullmatch(base):
            raise BadRequest("base must be sha256:<64 hex digits>", "DIGEST_INVALID")
    else:
        base = None
    return {
        "verb": verb,
        "agent": agent,
        "server": server,
        "tools": list(dict.fromkeys(tools)),
        "base": base,
    }


def grant_argv(project: dict, request: dict) -> list[str]:
    """`mcp grant|revoke`: a dry run without a base, else the write bound to it. `--` keeps a
    tool name from ever reading as an option."""
    mode = ["--expect-base", request["base"]] if request["base"] else ["--dry-run"]
    return [
        "mcp", request["verb"],
        "--project", project["path"], *mode, "--json",
        "--", request["agent"], request["server"], *request["tools"],
    ]  # fmt: skip


def _names(value: Any, pattern: re.Pattern) -> list[str] | None:
    if not isinstance(value, list) or len(value) > MAX_TOOLS:
        return None
    if not all(isinstance(item, str) and pattern.fullmatch(item) for item in value):
        return None
    return list(value)


def _in_use(value: Any) -> list[dict] | None:
    if not isinstance(value, list) or len(value) > MAX_IN_USE:
        return None
    rows = []
    for item in value:
        if not isinstance(item, dict):
            return None
        deployment, environment = item.get("deployment"), item.get("environment")
        tools = _names(item.get("tools"), TOOL)
        if deployment is not None and not (
            isinstance(deployment, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", deployment)
        ):
            return None
        if not isinstance(environment, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,64}", environment
        ):
            return None
        if tools is None:
            return None
        rows.append({"deployment": deployment, "environment": environment, "tools": tools})
    return rows


def grant_answer(data: object, request: dict) -> tuple[dict | None, dict | None]:
    """`(summary, None)`, or `(None, INVALID)` when the answer does not match its contract (the
    CLI's coded refusals come back as the envelope's error, from its `[CODE]` line). Only the
    known keys go on."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        return None, INVALID
    moved_key = "added" if request["verb"] == "grant" else "removed"
    moved = _names(data.get(moved_key), TOOL)
    tools = data.get("tools")
    in_use = _in_use(data.get("in_use"))
    diff = data.get("diff")
    if (
        data.get("verb") != request["verb"]
        or data.get("agent") != request["agent"]
        or data.get("server") != request["server"]
        or data.get("dry_run") is not (request["base"] is None)
        or not isinstance(data.get("changed"), bool)
        or not isinstance(data.get("ref"), str)
        or moved is None
        or in_use is None
        or not isinstance(diff, str)
        or len(diff) > MAX_DIFF
        or not isinstance(tools, list)
        or len(tools) > MAX_TOOLS
        or any(
            not isinstance(tool, dict)
            or not isinstance(tool.get("name"), str)
            or not TOOL.fullmatch(tool["name"])
            or tool.get("effect") not in (*EFFECTS, None)
            for tool in tools
        )
    ):
        return None, INVALID
    for key in ("base_sha256", "sha256"):
        if not isinstance(data.get(key), str) or not registry.DIGEST.fullmatch(data[key]):
            return None, INVALID
    try:
        ref = registry.Ref.parse(data["ref"])
    except registry.RegistryError:
        return None, INVALID
    if ref.kind != "mcp":
        return None, INVALID
    return {
        "verb": request["verb"],
        "agent": request["agent"],
        "server": request["server"],
        "ref": str(ref),
        "moved": moved,
        "tools": [{"name": tool["name"], "effect": tool.get("effect")} for tool in tools],
        "changed": data["changed"],
        "base_sha256": data["base_sha256"],
        "sha256": data["sha256"],
        "diff": diff,
        "dry_run": data["dry_run"],
        "in_use": in_use,
    }, None


def declared_tools(show: object) -> tuple[list[dict] | None, dict | None]:
    """The tools an `mcp/` version declares, `[{name, effect}]` sorted, from `registry show
    --json`'s `mcp.yaml` text; or `(None, problem)` when it is missing, cut or unreadable."""
    unreadable = {
        "code": "MCP_TOOLS_UNREADABLE",
        "message": "Could not read the tools this version declares; name them in a terminal "
        "(`agentbreeder mcp grant`)",
    }
    if not isinstance(show, dict) or show.get("kind") != "mcp":
        return None, unreadable
    files = show.get("files")
    entry = next(
        (
            item
            for item in (files if isinstance(files, list) else [])
            if isinstance(item, dict) and item.get("path") == "mcp.yaml"
        ),
        None,
    )
    if entry is None or entry.get("truncated") or not isinstance(entry.get("text"), str):
        return None, unreadable
    try:
        spec = strict_yaml.load_text(entry["text"])
    except (strict_yaml.StrictYamlError, ValueError, RecursionError):
        return None, unreadable
    tools = spec.get("tools") if isinstance(spec, dict) else None
    if not isinstance(tools, dict) or len(tools) > 1000:
        return None, unreadable
    rows = []
    for name, item in tools.items():
        effect = item.get("effect") if isinstance(item, dict) else None
        if not isinstance(name, str) or not TOOL.fullmatch(name) or effect not in EFFECTS:
            return None, unreadable
        rows.append({"name": name, "effect": effect})
    return sorted(rows, key=lambda row: row["name"]), None


def consent_servers(preview: object) -> list[dict]:
    """The `mcp` servers of a `services plan --json` answer that are not built in, each
    `{server, ref, origin, upstream, credentials: [{variable, destination}], sends_credential}`.
    `sends_credential` is `mcp_stack.needs_project_consent`, from the grant's own fields (a test
    keeps them equal): a remote upstream (`egress: yes`) with auth other than none."""
    grant = preview.get("grant") if isinstance(preview, dict) else None
    servers = grant.get("mcp") if isinstance(grant, dict) else None
    rows = []
    for item in servers if isinstance(servers, list) else []:
        if not isinstance(item, dict) or item.get("origin") == "built-in":
            continue
        name = item.get("server")
        if not isinstance(name, str) or not SERVER.fullmatch(name):
            continue
        credentials = [
            {"variable": str(c.get("variable", "")), "destination": str(c.get("destination", ""))}
            for c in (item.get("credentials") if isinstance(item.get("credentials"), list) else [])
            if isinstance(c, dict)
        ]
        rows.append(
            {
                "server": name,
                "ref": str(item.get("ref", "")),
                "origin": str(item.get("origin", "")),
                "upstream": str(item.get("upstream", "")),
                "credentials": credentials,
                "sends_credential": item.get("egress") == "yes" and item.get("auth") != "none",
            }
        )
    return rows


def consent_names(value: object, preview: object) -> list[str]:
    """The `allow_project_mcp` job option checked against the fresh plan: exactly the servers
    that are not built in, each named once (sorted), or BadRequest. Refused before the job, so
    no relay starts on a consent that `services up` would then refuse."""
    if value is None:
        value = []
    if not isinstance(value, list) or not all(
        isinstance(name, str) and SERVER.fullmatch(name) for name in value
    ):
        raise BadRequest("Invalid MCP server consent", "MCP_CONSENT_INVALID")
    needed = {row["server"] for row in consent_servers(preview)}
    given = set(value)
    if given - needed:
        raise BadRequest(
            "Consent names a server that needs none; review it again", "MCP_CONSENT_INVALID"
        )
    missing = sorted(needed - given)
    if missing:
        raise BadRequest(
            f"Allow mcp.{missing[0]} (project code) before starting, or start from a terminal",
            "MCP_PROJECT_CONSENT",
        )
    return sorted(given)
