"""agentbreeder registry — push and list registry entities (prompts, tools, agents).

Talks to the AgentBreeder API at $AGENTBREEDER_API_URL (default
http://localhost:8000). All API calls require a JWT in $AGENTBREEDER_API_TOKEN
since the platform now gates 247/247 routes.

Examples:

    agentbreeder registry prompt push prompts/microlearning-system.md
    agentbreeder registry prompt list

    agentbreeder registry tool push engine.tools.standard.web_search
    agentbreeder registry tool list

    agentbreeder registry agent push agent.yaml
    agentbreeder registry agent list
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import httpx
import typer
import yaml
from rich.console import Console
from rich.table import Table

console = Console()

registry_app = typer.Typer(no_args_is_help=True, help="Push and list AgentBreeder registry entities")
prompt_app = typer.Typer(no_args_is_help=True, help="Manage prompts in the registry")
tool_app = typer.Typer(no_args_is_help=True, help="Manage tools in the registry")
agent_app = typer.Typer(no_args_is_help=True, help="Manage agents in the registry")
registry_app.add_typer(prompt_app, name="prompt")
registry_app.add_typer(tool_app, name="tool")
registry_app.add_typer(agent_app, name="agent")


def _api_base() -> str:
    return os.getenv("AGENTBREEDER_API_URL", "http://localhost:8000").rstrip("/")


def _auth_headers() -> dict[str, str]:
    token = os.getenv("AGENTBREEDER_API_TOKEN", "").strip()
    if not token:
        console.print(
            "[red]AGENTBREEDER_API_TOKEN is not set.[/red] "
            "Log in via /api/v1/auth/login and export the token first."
        )
        raise typer.Exit(code=1)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _post(path: str, body: dict) -> dict:
    url = f"{_api_base()}{path}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=_auth_headers(), json=body)
        if resp.status_code >= 400:
            console.print(f"[red]POST {path} -> {resp.status_code}[/red]\n{resp.text}")
            raise typer.Exit(code=1)
        return resp.json()


def _get(path: str) -> dict:
    url = f"{_api_base()}{path}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=_auth_headers())
        if resp.status_code >= 400:
            console.print(f"[red]GET {path} -> {resp.status_code}[/red]\n{resp.text}")
            raise typer.Exit(code=1)
        return resp.json()


# ── prompts ────────────────────────────────────────────────────────────────


@prompt_app.command("push")
def prompt_push(
    file: Path = typer.Argument(..., help="Path to the prompt markdown file (e.g. prompts/foo.md)"),
    name: str | None = typer.Option(None, "--name", help="Override prompt name (defaults to filename without extension)"),
    version: str = typer.Option("1.0.0", "--version", help="Semver version"),
    team: str = typer.Option("engineering", "--team", help="Owning team"),
    description: str = typer.Option("", "--description", help="One-line description"),
) -> None:
    """Push a prompt markdown file to the registry as POST /api/v1/registry/prompts."""
    if not file.is_file():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(code=1)
    body = {
        "name": name or file.stem,
        "version": version,
        "content": file.read_text(encoding="utf-8"),
        "description": description,
        "team": team,
    }
    payload = _post("/api/v1/registry/prompts", body)
    data = payload["data"]
    console.print(f"[green]✓[/green] {data['name']} v{data['version']}  id={data['id']}")


@prompt_app.command("list")
def prompt_list(team: str | None = typer.Option(None, "--team")) -> None:
    """List prompts in the registry."""
    path = "/api/v1/registry/prompts" + (f"?team={team}" if team else "")
    payload = _get(path)
    table = Table(title=f"Prompts ({payload['meta']['total']})")
    table.add_column("Name")
    table.add_column("Version")
    table.add_column("Team")
    table.add_column("Bytes", justify="right")
    table.add_column("ID")
    for p in payload["data"]:
        table.add_row(p["name"], p["version"], p["team"], str(len(p["content"])), p["id"][:8])
    console.print(table)


@prompt_app.command("try")
def prompt_try(
    name: str = typer.Argument(..., help="Prompt name (kebab-case) to render"),
    user_message: str = typer.Option("", "--input", help="User message to send"),
    model: str = typer.Option("gemini-2.5-flash", "--model"),
    temperature: float = typer.Option(0.4, "--temperature"),
) -> None:
    """Render a registered prompt by calling a real LLM with it as the system instruction."""
    listing = _get("/api/v1/registry/prompts")
    match = next((p for p in listing["data"] if p["name"] == name), None)
    if not match:
        console.print(f"[red]Prompt '{name}' not found.[/red]")
        raise typer.Exit(code=1)

    payload = _post(
        f"/api/v1/registry/prompts/{match['id']}/render",
        {"user_message": user_message, "model": model, "temperature": temperature},
    )
    data = payload["data"]
    if data.get("error"):
        console.print(f"[red]error[/red]: {data['error']}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] {name} via {data['model']} ({data['duration_ms']} ms)")
    console.print()
    console.print(data["output"])


# ── tools ──────────────────────────────────────────────────────────────────


def _import_tool_module(target: str) -> object:
    """Resolve `package.module.path` or `path/to/file.py` to a module object.

    For Python file paths, the file's grand-parent directory is prepended to
    ``sys.path`` so sibling-package imports inside the loaded module
    (e.g. ``from tools.impl import foo`` from a file at ``tools/foo.py``)
    resolve correctly. TS/JS files are NOT importable from Python — callers
    must use ``_extract_node_metadata`` for those.
    """
    if target.endswith(".py") or ("/" in target and not target.endswith((".ts", ".js", ".mjs"))):
        path = Path(target).resolve()
        project_root = path.parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load module from {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return importlib.import_module(target)


def _extract_node_metadata(target: str) -> dict:
    """Spawn a small `node`/`npx tsx` child process to read SCHEMA + docstring.

    Returns ``{'name', 'docstring', 'schema'}``. Used by ``tool push`` for TS/JS
    files (which Python cannot import directly).
    """
    import subprocess

    abs_path = Path(target).resolve()
    if not abs_path.is_file():
        raise FileNotFoundError(f"Node tool file not found: {abs_path}")

    inspector = (
        f"const {{ pathToFileURL }} = require('node:url');"
        f"(async () => {{"
        f"  const m = await import(pathToFileURL({json.dumps(str(abs_path))}).href);"
        f"  const out = {{ name: Object.keys(m).find(k => typeof m[k] === 'function'),"
        f"                 schema: m.SCHEMA ?? null }};"
        f"  process.stdout.write(JSON.stringify(out));"
        f"}})().catch((e) => {{ console.error(e); process.exit(1); }});"
    )
    result = subprocess.run(  # noqa: S603 — argv list, no shell
        ["npx", "--yes", "tsx", "--eval", inspector],
        capture_output=True, text=True, timeout=60.0, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"node metadata read failed: {result.stderr[:500]}")
    return json.loads(result.stdout)


@tool_app.command("push")
def tool_push(
    target: str = typer.Argument(..., help="Tool module to register (e.g. 'engine.tools.standard.web_search' or 'tools/foo.py')"),
    name: str | None = typer.Option(None, "--name", help="Override tool name (defaults to module basename, kebab-cased)"),
    description: str = typer.Option("", "--description", help="One-line description"),
    tool_type: str = typer.Option("function", "--type", help="Tool type: function | mcp_server | http"),
) -> None:
    """Push tool metadata + JSON schema to the registry from a Python module.

    The module must expose a ``SCHEMA`` dict (OpenAPI subset). The function
    name (snake_case) becomes the tool name in kebab-case unless overridden.
    """
    is_node = target.endswith((".ts", ".js", ".mjs"))
    if is_node:
        meta = _extract_node_metadata(target)
        if not meta.get("name"):
            console.print(f"[red]{target} does not export a function[/red]")
            raise typer.Exit(code=1)
        if not isinstance(meta.get("schema"), dict):
            console.print(f"[yellow]warning:[/yellow] {target} has no SCHEMA export — using empty schema")
        func_name = meta["name"]
        schema = meta.get("schema") or {"type": "object", "properties": {}, "required": []}
        docstring = ""  # TS doesn't surface docstrings the same way; rely on --description
    else:
        mod = _import_tool_module(target)
        schema = getattr(mod, "SCHEMA", None)
        if not isinstance(schema, dict):
            console.print(f"[red]{target} does not export SCHEMA dict[/red]")
            raise typer.Exit(code=1)
        func_name = mod.__name__.split(".")[-1]
        docstring = (mod.__doc__ or "").strip().split("\n")[0]

    final_name = name or func_name.replace("_", "-")

    # Endpoint inference:
    #   - .py file        -> python:<abs_path>   (subprocess via tool_runner)
    #   - .ts/.js/.mjs    -> node:<abs_path>     (subprocess via npx tsx)
    #   - module path     -> kept as-is          (in-process Python import)
    if target.endswith(".py") or ("/" in target and not is_node):
        endpoint = f"python:{Path(target).resolve()}"
    elif is_node:
        endpoint = f"node:{Path(target).resolve()}"
    else:
        endpoint = target

    body = {
        "name": final_name,
        "description": description or docstring,
        "tool_type": tool_type,
        "schema_definition": schema,
        "endpoint": endpoint,
        "source": "manual",
    }
    payload = _post("/api/v1/registry/tools", body)
    data = payload["data"]
    console.print(f"[green]✓[/green] {data['name']}  id={data['id']}  endpoint={data['endpoint']}")


@tool_app.command("run")
def tool_run(
    name: str = typer.Argument(..., help="Tool name (kebab-case) to invoke"),
    args_json: str = typer.Option("{}", "--args", help="JSON-encoded args object"),
) -> None:
    """Invoke a registered tool with structured args; print output + duration."""
    try:
        args = json.loads(args_json)
        if not isinstance(args, dict):
            raise ValueError("--args must be a JSON object")
    except (ValueError, json.JSONDecodeError) as exc:
        console.print(f"[red]Bad --args:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    listing = _get("/api/v1/registry/tools")
    match = next((t for t in listing["data"] if t["name"] == name), None)
    if not match:
        console.print(f"[red]Tool '{name}' not found in registry.[/red]")
        raise typer.Exit(code=1)

    payload = _post(f"/api/v1/registry/tools/{match['id']}/execute", {"args": args})
    data = payload["data"]
    if data.get("error"):
        console.print(f"[red]error[/red] (exit={data['exit_code']}): {data['error']}")
        if data.get("stderr"):
            console.print(f"[dim]{data['stderr']}[/dim]")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] {name} ({data['duration_ms']} ms)")
    console.print(json.dumps(data["output"], indent=2, default=str))


@tool_app.command("list")
def tool_list() -> None:
    """List tools in the registry."""
    payload = _get("/api/v1/registry/tools")
    table = Table(title=f"Tools ({payload['meta']['total']})")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Endpoint")
    table.add_column("ID")
    for t in payload["data"]:
        table.add_row(t["name"], t["tool_type"], t.get("endpoint") or "-", t["id"][:8])
    console.print(table)


# ── agents ─────────────────────────────────────────────────────────────────


@agent_app.command("push")
def agent_push(
    yaml_file: Path = typer.Argument(Path("agent.yaml"), help="Path to agent.yaml (default: ./agent.yaml)"),
) -> None:
    """Push an agent definition to the registry from agent.yaml.

    Uses POST /api/v1/agents/from-yaml so the server-side parser handles the
    full schema (tools, prompts, deploy config, etc).
    """
    if not yaml_file.is_file():
        console.print(f"[red]agent.yaml not found at {yaml_file}[/red]")
        raise typer.Exit(code=1)

    yaml_text = yaml_file.read_text(encoding="utf-8")
    payload = _post("/api/v1/agents/from-yaml", {"yaml_content": yaml_text})
    data = payload["data"]
    console.print(f"[green]✓[/green] {data['name']} v{data.get('version','?')}  id={data['id']}")


@agent_app.command("invoke")
def agent_invoke(
    name: str = typer.Argument(..., help="Agent name to invoke"),
    user_input: str = typer.Option(..., "--input", help="User message"),
    endpoint: str | None = typer.Option(None, "--endpoint", help="Override agent endpoint URL"),
    token: str | None = typer.Option(None, "--token", help="Bearer token for the agent runtime"),
    session_id: str | None = typer.Option(None, "--session", help="Continue an existing session"),
) -> None:
    """Send a message to a registered agent's deployed runtime via the API proxy."""
    listing = _get("/api/v1/agents")
    match = next((a for a in listing["data"] if a["name"] == name), None)
    if not match:
        console.print(f"[red]Agent '{name}' not found.[/red]")
        raise typer.Exit(code=1)

    body: dict = {"input": user_input}
    if endpoint:
        body["endpoint_url"] = endpoint
    if token:
        body["auth_token"] = token
    if session_id:
        body["session_id"] = session_id

    payload = _post(f"/api/v1/agents/{match['id']}/invoke", body)
    data = payload["data"]
    if data.get("error"):
        console.print(f"[red]error[/red] (status={data['status_code']}): {data['error'][:500]}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]✓[/green] {name}  session={data.get('session_id', '?')[:8] if data.get('session_id') else '?'}  ({data['duration_ms']} ms)"
    )
    console.print()
    console.print(data["output"])


@agent_app.command("list")
def agent_list(team: str | None = typer.Option(None, "--team")) -> None:
    """List agents in the registry."""
    path = "/api/v1/agents" + (f"?team={team}" if team else "")
    payload = _get(path)
    table = Table(title=f"Agents ({payload['meta']['total']})")
    table.add_column("Name")
    table.add_column("Framework")
    table.add_column("Team")
    table.add_column("Status")
    table.add_column("ID")
    for a in payload["data"]:
        table.add_row(
            a["name"],
            a.get("framework", "?"),
            a.get("team", "-"),
            a.get("status", "-"),
            a["id"][:8],
        )
    console.print(table)
