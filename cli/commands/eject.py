"""agentbreeder eject — tier mobility between No Code / Low Code / Full Code.

Two documented invocation modes (per ``website/content/docs/how-to.mdx``):

    agentbreeder eject my-agent --to yaml   # fetch from registry, write agent.yaml
    agentbreeder eject my-agent --to code   # also scaffold agent.py + reqs + README

The first positional argument may also be a path to an existing ``agent.yaml``
file. In that case the registry lookup is skipped and the YAML is read directly
from disk — this preserves the legacy SDK-scaffold flow used by the existing
``_generate_crewai_scaffold`` / ``_generate_google_adk_scaffold`` /
``_generate_claude_sdk_scaffold`` paths (Track J tests rely on it).

Tier mobility rule (see ``CLAUDE.md``): No Code → Low Code (``--to yaml``)
→ Full Code (``--to code``). The deploy pipeline does not know which tier
produced the config — eject just writes files. The deploy command picks
them up like any other agent directory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from cli._http import api_base, auth_headers, get_token

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_class_name(slug: str) -> str:
    """Convert a slug like 'my-agent' or 'customer_support' to 'MyAgent'."""
    parts = re.split(r"[-_]", slug)
    return "".join(part.capitalize() for part in parts if part)


def _looks_like_path(value: str) -> bool:
    """Return True if *value* is a path to an existing file (legacy mode)."""
    if not value:
        return False
    p = Path(value)
    return p.exists() and p.is_file()


def _fetch_agent_from_registry(agent_name: str) -> dict[str, Any]:
    """Fetch an agent by name from the API and return its ``config_snapshot``.

    Lists agents (the only public name-keyed endpoint — ``/agents/{id}``
    requires a UUID) and returns the first match. Exits with code 1 if the
    agent is not found or the API is unreachable.
    """
    base = api_base()
    # Use auth headers only if a token is configured — local dev allows
    # anonymous reads and we don't want to force ``agentbreeder login`` for
    # every eject command.
    headers: dict[str, str] = {}
    if get_token():
        try:
            headers = auth_headers()
        except typer.Exit:
            # No token despite get_token() returning truthy — fall through
            # anonymously. ``require_token`` inside ``auth_headers`` would
            # have called ``typer.Exit`` but ``get_token`` already guarded.
            headers = {}

    url = f"{base}/api/v1/agents"
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=headers, params={"per_page": 100})
    except httpx.HTTPError as exc:
        console.print(
            f"[red]Could not reach the AgentBreeder API at {base}.[/red]\n"
            f"[dim]{type(exc).__name__}: {exc}[/dim]\n"
            "[dim]Is the server running? Try [bold]agentbreeder up[/bold].[/dim]"
        )
        raise typer.Exit(code=1) from exc

    if resp.status_code != 200:
        console.print(
            f"[red]API returned {resp.status_code} when listing agents.[/red]\n"
            f"[dim]{resp.text[:300]}[/dim]"
        )
        raise typer.Exit(code=1)

    try:
        payload = resp.json()
    except (ValueError, json.JSONDecodeError) as exc:
        console.print(f"[red]API returned non-JSON response: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    agents = payload.get("data") or []
    match = next((a for a in agents if a.get("name") == agent_name), None)
    if not match:
        available = ", ".join(a.get("name", "") for a in agents if a.get("name"))
        console.print(f"[red]Agent '{agent_name}' not found in the registry.[/red]")
        if available:
            console.print(f"[dim]Available agents: {available}[/dim]")
        else:
            console.print("[dim]The registry is empty.[/dim]")
        raise typer.Exit(code=1)
    return match


def _agent_to_yaml_dict(agent_record: dict[str, Any]) -> dict[str, Any]:
    """Build a clean human-readable agent.yaml dict from an API response."""
    snapshot = agent_record.get("config_snapshot") or {}
    if isinstance(snapshot, dict) and snapshot:
        # Prefer the round-trippable snapshot stored at registration time.
        return _filter_snapshot(snapshot, agent_record)

    # Fallback: synthesize minimal YAML from the flat columns.
    out: dict[str, Any] = {
        "name": agent_record.get("name", "agent"),
        "version": agent_record.get("version", "0.1.0"),
    }
    if agent_record.get("description"):
        out["description"] = agent_record["description"]
    if agent_record.get("team"):
        out["team"] = agent_record["team"]
    if agent_record.get("owner"):
        out["owner"] = agent_record["owner"]
    if agent_record.get("tags"):
        out["tags"] = agent_record["tags"]
    if agent_record.get("framework"):
        out["framework"] = agent_record["framework"]
    model: dict[str, Any] = {}
    if agent_record.get("model_primary"):
        model["primary"] = agent_record["model_primary"]
    if agent_record.get("model_fallback"):
        model["fallback"] = agent_record["model_fallback"]
    if model:
        out["model"] = model
    return out


def _filter_snapshot(snapshot: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that should never appear in a human-edited agent.yaml."""
    drop = {"id", "created_at", "updated_at", "status", "endpoint_url"}
    cleaned = {k: v for k, v in snapshot.items() if k not in drop}
    # Ensure name matches the API record (snapshot may pre-date renames).
    if "name" not in cleaned and record.get("name"):
        cleaned["name"] = record["name"]
    return cleaned


# Preserve insertion order in dumped YAML.
class _OrderedDumper(yaml.SafeDumper):
    pass


def _represent_dict_preserve(dumper: yaml.SafeDumper, data: dict) -> Any:
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


_OrderedDumper.add_representer(dict, _represent_dict_preserve)


def _dump_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(
        data,
        Dumper=_OrderedDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def _resolve_output_dir(
    name: str, output_dir: str | None, output: str | None, force: bool
) -> Path:
    """Compute the eject target dir and bail if it exists without --force."""
    if output_dir:
        target = Path(output_dir)
    elif output:
        target = Path(output)
    else:
        target = Path.cwd() / name
    target = target.resolve()
    if target.exists() and any(target.iterdir()) and not force:
        console.print(
            f"[red]Output directory already exists and is not empty:[/red] {target}\n"
            "[dim]Pass --force to overwrite.[/dim]"
        )
        raise typer.Exit(code=1)
    target.mkdir(parents=True, exist_ok=True)
    return target


# ---------------------------------------------------------------------------
# Full-Code scaffold for `--to code` (delegates to init_cmd templates so we
# stay framework-agnostic with one source of truth).
# ---------------------------------------------------------------------------


def _scaffold_full_code(yaml_data: dict[str, Any], out_dir: Path) -> list[str]:
    """Write agent.py + requirements.txt + README.md alongside agent.yaml.

    Returns the list of created filenames. Raises typer.Exit if the agent's
    framework has no scaffold template.
    """
    from cli.commands.init_cmd import (
        AGENT_PY_GENERATORS,
        _env_example,
        _readme,
        _requirements,
    )

    framework = yaml_data.get("framework", "custom")
    if framework not in AGENT_PY_GENERATORS:
        supported = ", ".join(sorted(AGENT_PY_GENERATORS))
        console.print(
            f"[red]Cannot eject to code: framework '{framework}' has no scaffold "
            f"template.[/red]\n[dim]Supported frameworks: {supported}[/dim]"
        )
        raise typer.Exit(code=1)

    name = yaml_data.get("name", "agent")
    deploy = yaml_data.get("deploy") or {}
    cloud = deploy.get("cloud", "local") if isinstance(deploy, dict) else "local"

    files = {
        "agent.py": AGENT_PY_GENERATORS[framework](name),
        "requirements.txt": _requirements(framework),
        ".env.example": _env_example(framework),
        "README.md": _readme(name, framework, cloud),
    }
    for filename, content in files.items():
        (out_dir / filename).write_text(content, encoding="utf-8")
    return list(files)


# ---------------------------------------------------------------------------
# Legacy framework scaffolds (used by direct-YAML invocations + back-compat
# tests; kept verbatim from the prior implementation).
# ---------------------------------------------------------------------------


def _generate_crewai_scaffold(yaml_content: str, out_dir: Path) -> None:
    """Write a CrewAI project scaffold from agent YAML into *out_dir*."""
    data = yaml.safe_load(yaml_content)
    if not isinstance(data, dict):
        raise ValueError("Invalid YAML: expected a mapping at the top level")

    name: str = data.get("name", "my-agent")
    description: str = data.get("description") or "an AgentBreeder agent"
    class_name = _to_class_name(name)

    crew_py = f'''"""CrewAI agent scaffold generated by AgentBreeder eject."""

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task


@CrewBase
class {class_name}Crew:
    """Crew for {name}."""

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    @agent
    def primary_agent(self) -> Agent:
        return Agent(
            config=self.agents_config["primary_agent"],  # type: ignore[index]
            verbose=True,
        )

    @task
    def primary_task(self) -> Task:
        return Task(
            config=self.tasks_config["primary_task"],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
'''

    agents_yaml = yaml.dump(
        {
            "primary_agent": {
                "role": f"{name} Primary Agent",
                "goal": description,
                "backstory": f"You are a specialized agent responsible for {description}.",
            }
        },
        default_flow_style=False,
        allow_unicode=True,
    )

    tasks_yaml = yaml.dump(
        {
            "primary_task": {
                "description": f"Execute the primary task for {name}.",
                "expected_output": "A complete, accurate response to the task.",
                "agent": "primary_agent",
            }
        },
        default_flow_style=False,
        allow_unicode=True,
    )

    requirements = "crewai>=0.80.0\n"

    out_dir.mkdir(parents=True, exist_ok=True)
    config_dir = out_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "crew.py").write_text(crew_py, encoding="utf-8")
    (config_dir / "agents.yaml").write_text(agents_yaml, encoding="utf-8")
    (config_dir / "tasks.yaml").write_text(tasks_yaml, encoding="utf-8")
    (out_dir / "requirements.txt").write_text(requirements, encoding="utf-8")


def _generate_google_adk_scaffold(yaml_content: str, out_dir: Path) -> None:
    """Write a Google ADK project scaffold from agent YAML into *out_dir*."""
    data = yaml.safe_load(yaml_content)
    if not isinstance(data, dict):
        raise ValueError("Invalid YAML: expected a mapping at the top level")

    name: str = data.get("name", "my-agent")
    description: str = data.get("description") or "A helpful agent"
    model_cfg = data.get("model") or {}
    model: str = (
        model_cfg.get("primary", "gemini-2.0-flash")
        if isinstance(model_cfg, dict)
        else "gemini-2.0-flash"
    )
    has_subagents = bool(data.get("subagents"))

    r_name = repr(name)
    r_model = repr(model)
    r_description = repr(description)
    r_name_step1 = repr(f"{name}_step1")
    r_name_step2 = repr(f"{name}_step2")
    r_first_step_desc = repr(f"First step of {name}")
    r_second_step_desc = repr(f"Second step of {name}")

    if has_subagents:
        agent_py = f'''"""Google ADK agent scaffold generated by AgentBreeder eject."""

import os

from google.adk.agents import LlmAgent, SequentialAgent

_sub1 = LlmAgent(
    name={r_name_step1},
    model={r_model},
    description={r_first_step_desc},
    instruction="Complete the first step of the task.",
)

_sub2 = LlmAgent(
    name={r_name_step2},
    model={r_model},
    description={r_second_step_desc},
    instruction="Complete the second step of the task.",
)

root_agent = SequentialAgent(
    name={r_name},
    description={r_description},
    sub_agents=[_sub1, _sub2],
)
'''
    else:
        agent_py = f'''"""Google ADK agent scaffold generated by AgentBreeder eject."""

import os

from google.adk.agents import LlmAgent

root_agent = LlmAgent(
    name={r_name},
    model={r_model},
    description={r_description},
    instruction=(
        "You are a helpful agent. Complete the user\'s request accurately and concisely."
    ),
)
'''

    requirements = "google-adk>=1.29.0\n"

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "agent.py").write_text(agent_py, encoding="utf-8")
    (out_dir / "requirements.txt").write_text(requirements, encoding="utf-8")


def _generate_claude_sdk_scaffold(yaml_content: str, out_dir: Path) -> None:
    """Write a Claude SDK project scaffold from agent YAML into *out_dir*."""
    data = yaml.safe_load(yaml_content)
    if not isinstance(data, dict):
        raise ValueError("Invalid YAML: expected a mapping at the top level")

    model_cfg = data.get("model") or {}
    model: str = (
        model_cfg.get("primary", "claude-sonnet-4-6")
        if isinstance(model_cfg, dict)
        else "claude-sonnet-4-6"
    )

    prompts_cfg = data.get("prompts") or {}
    system_prompt: str = (
        prompts_cfg.get("system", "You are a helpful assistant.")
        if isinstance(prompts_cfg, dict)
        else "You are a helpful assistant."
    )
    if not system_prompt:
        system_prompt = "You are a helpful assistant."

    agent_py = f'''"""Claude SDK agent scaffold generated by AgentBreeder eject."""

from __future__ import annotations

import anthropic
from anthropic.types import ToolParam

client = anthropic.AsyncAnthropic()

MODEL = {repr(model)}
SYSTEM_PROMPT = {repr(system_prompt)}

TOOLS: list[ToolParam] = [
    # Add your tools here. Example:
    # {{
    #     "name": "search",
    #     "description": "Search for information",
    #     "input_schema": {{
    #         "type": "object",
    #         "properties": {{"query": {{"type": "string"}}}},
    #         "required": ["query"],
    #     }},
    # }},
]


async def run_agent(user_input: str) -> str:
    """Run the agent with a tool-use loop."""
    messages: list[dict] = [{{"role": "user", "content": user_input}}]

    while True:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            return "".join(
                block.text for block in response.content if hasattr(block, "text")
            )

        if response.stop_reason == "tool_use":
            messages.append({{"role": "assistant", "content": response.content}})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = f"Tool {{block.name}} called with {{block.input}}"
                    tool_results.append(
                        {{
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }}
                    )
            messages.append({{"role": "user", "content": tool_results}})
            continue

        break

    return ""
'''

    requirements = "anthropic>=0.50.0\n"

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "agent.py").write_text(agent_py, encoding="utf-8")
    (out_dir / "requirements.txt").write_text(requirements, encoding="utf-8")


# ---------------------------------------------------------------------------
# Legacy Python / TypeScript SDK code generation (used by `--sdk python|typescript`)
# ---------------------------------------------------------------------------


def _generate_python_sdk(yaml_content: str) -> str:
    """Generate Python SDK code that recreates the agent from YAML."""
    data = yaml.safe_load(yaml_content)
    if not isinstance(data, dict):
        raise typer.BadParameter("Invalid YAML: expected a mapping at the top level")

    lines: list[str] = [
        '"""Agent definition generated by `agentbreeder eject`.',
        "",
        "Edit this file to add custom routing, middleware, hooks, and state logic.",
        "Then deploy with: python agent_sdk.py",
        '"""',
        "",
        "from agenthub import Agent, Tool, Model, Memory",
        "",
    ]

    name = data.get("name", "my-agent")
    version_str = data.get("version", "1.0.0")
    team = data.get("team", "default")
    desc = data.get("description", "")

    chain_parts: list[str] = []

    init_args = f'"{name}", version="{version_str}", team="{team}"'
    if desc:
        init_args += f', description="{desc}"'
    owner = data.get("owner", "")
    if owner:
        init_args += f', owner="{owner}"'
    framework = data.get("framework", "custom")
    if framework != "custom":
        init_args += f', framework="{framework}"'

    chain_parts.append(f"    Agent({init_args})")

    model = data.get("model")
    if isinstance(model, dict):
        primary = model.get("primary", "")
        fallback = model.get("fallback")
        model_args = f'primary="{primary}"'
        if fallback:
            model_args += f', fallback="{fallback}"'
        temp = model.get("temperature")
        if temp is not None and temp != 0.7:
            model_args += f", temperature={temp}"
        max_tok = model.get("max_tokens")
        if max_tok is not None and max_tok != 4096:
            model_args += f", max_tokens={max_tok}"
        chain_parts.append(f"    .with_model({model_args})")

    prompts = data.get("prompts")
    if isinstance(prompts, dict) and "system" in prompts:
        system = prompts["system"]
        chain_parts.append(f'    .with_prompt(system="{system}")')

    tools = data.get("tools", [])
    for tool in tools:
        if isinstance(tool, dict):
            if "ref" in tool:
                chain_parts.append(f'    .with_tool(Tool.from_ref("{tool["ref"]}"))')
            elif "name" in tool:
                desc = tool.get("description", "")
                desc_part = f', description="{desc}"' if desc else ""
                chain_parts.append(f'    .with_tool(Tool(name="{tool["name"]}"{desc_part}))')

    memory = data.get("memory")
    if isinstance(memory, dict):
        backend = memory.get("backend", "in_memory")
        mem_args = f'backend="{backend}"'
        max_msg = memory.get("max_messages")
        if max_msg and max_msg != 100:
            mem_args += f", max_messages={max_msg}"
        chain_parts.append(f"    .with_memory({mem_args})")

    for g in data.get("guardrails", []):
        if isinstance(g, str):
            chain_parts.append(f'    .with_guardrail("{g}")')

    deploy = data.get("deploy")
    if isinstance(deploy, dict):
        cloud = deploy.get("cloud", "local")
        deploy_args = f'cloud="{cloud}"'
        runtime = deploy.get("runtime")
        if runtime:
            deploy_args += f', runtime="{runtime}"'
        region = deploy.get("region")
        if region:
            deploy_args += f', region="{region}"'
        chain_parts.append(f"    .with_deploy({deploy_args})")

    tags = data.get("tags", [])
    if tags:
        tag_str = ", ".join(f'"{t}"' for t in tags)
        chain_parts.append(f"    .tag({tag_str})")

    lines.append("agent = (")
    lines.append("\n".join(chain_parts))
    lines.append(")")
    lines.append("")
    lines.append("")
    lines.append("# --- Customize below this line ---")
    lines.append("")
    lines.append('if __name__ == "__main__":')
    lines.append("    errors = agent.validate()")
    lines.append("    if errors:")
    lines.append('        print("Validation errors:", errors)')
    lines.append("    else:")
    lines.append('        print("Agent is valid!")')
    lines.append("        print(agent.to_yaml())")
    lines.append("")

    return "\n".join(lines)


def _generate_typescript_sdk(yaml_content: str) -> str:
    """Generate TypeScript SDK code that recreates the agent from YAML."""
    data = yaml.safe_load(yaml_content)
    if not isinstance(data, dict):
        raise typer.BadParameter("Invalid YAML: expected a mapping at the top level")

    lines: list[str] = [
        "/** Agent definition generated by `agentbreeder eject --sdk typescript`.",
        " *",
        " * Edit this file to add custom logic, then deploy with: npx ts-node agent.ts",
        " */",
        "",
        'import { Agent, Tool } from "@agentbreeder/sdk";',
        "",
    ]

    name = data.get("name", "my-agent")
    version_str = data.get("version", "1.0.0")
    team = data.get("team", "default")
    desc = data.get("description", "")
    owner = data.get("owner", "")
    framework = data.get("framework", "custom")

    opts_parts: list[str] = [f'  version: "{version_str}"']
    opts_parts.append(f'  team: "{team}"')
    if desc:
        opts_parts.append(f'  description: "{desc}"')
    if owner:
        opts_parts.append(f'  owner: "{owner}"')
    if framework != "custom":
        opts_parts.append(f'  framework: "{framework}"')

    lines.append(f'const agent = new Agent("{name}", {{')
    lines.append(",\n".join(opts_parts))
    lines.append("})")

    model = data.get("model")
    if isinstance(model, dict):
        primary = model.get("primary", "")
        fallback = model.get("fallback")
        opts = []
        if fallback:
            opts.append(f'fallback: "{fallback}"')
        temp = model.get("temperature")
        if temp is not None and temp != 0.7:
            opts.append(f"temperature: {temp}")
        opts_str = ", { " + ", ".join(opts) + " }" if opts else ""
        lines.append(f'  .withModel("{primary}"{opts_str})')

    prompts = data.get("prompts")
    if isinstance(prompts, dict) and "system" in prompts:
        lines.append(f'  .withPrompt("{prompts["system"]}")')

    for tool in data.get("tools", []):
        if isinstance(tool, dict):
            if "ref" in tool:
                lines.append(f'  .withTool(Tool.fromRef("{tool["ref"]}"))')
            elif "name" in tool:
                lines.append(f'  .withTool(new Tool({{ name: "{tool["name"]}" }}))')

    for sub in data.get("subagents", []):
        if isinstance(sub, dict) and "ref" in sub:
            sub_desc = sub.get("description", "")
            desc_part = f', {{ description: "{sub_desc}" }}' if sub_desc else ""
            lines.append(f'  .withSubagent("{sub["ref"]}"{desc_part})')

    for g in data.get("guardrails", []):
        if isinstance(g, str):
            lines.append(f'  .withGuardrail("{g}")')

    deploy = data.get("deploy")
    if isinstance(deploy, dict):
        cloud = deploy.get("cloud", "local")
        lines.append(f'  .withDeploy("{cloud}")')

    tags = data.get("tags", [])
    if tags:
        tag_str = ", ".join(f'"{t}"' for t in tags)
        lines.append(f"  .tag({tag_str})")

    lines.append(";")
    lines.append("")
    lines.append("// Validate and output YAML")
    lines.append("const errors = agent.validate();")
    lines.append("if (errors.length > 0) {")
    lines.append('  console.error("Validation errors:", errors);')
    lines.append("} else {")
    lines.append('  console.log("Agent is valid!");')
    lines.append("  console.log(agent.toYaml());")
    lines.append("}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


def eject(
    target: str | None = typer.Argument(
        None,
        help=(
            "Agent name to fetch from the registry, OR path to an existing "
            "agent.yaml file (legacy mode)."
        ),
    ),
    to: str = typer.Option(
        "yaml",
        "--to",
        help="Eject target: 'yaml' (write agent.yaml only) or 'code' (scaffold full project).",
    ),
    output_dir: str | None = typer.Option(
        None,
        "--output-dir",
        help="Directory to write to. Defaults to ./<agent-name>/.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing non-empty output directory.",
    ),
    # ── Legacy flags (preserved for the prior `eject agent.yaml --sdk ...` flow) ──
    config_path: Path | None = typer.Option(
        None,
        "--config",
        help="(Legacy) Explicit path to an agent.yaml file.",
    ),
    sdk: str = typer.Option(
        "python",
        "--sdk",
        help="(Legacy) Target SDK language when ejecting a YAML file directly: python | typescript.",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="(Legacy) Output file path for --sdk-style invocations.",
    ),
) -> None:
    """Tier mobility — eject an agent from Low Code to YAML or Full Code.

    Examples:

      agentbreeder eject my-agent --to yaml      # fetch from registry, emit agent.yaml
      agentbreeder eject my-agent --to code      # also scaffold agent.py + requirements
      agentbreeder eject ./agent.yaml --sdk python  # legacy: SDK code from a YAML file
    """
    # ── Path 1: legacy YAML-file dispatch ────────────────────────────
    # Triggered when `config_path` is explicit, or when the positional arg
    # is a real file on disk. This preserves the pre-existing scaffold
    # behavior (and its tests) for crewai / google_adk / claude_sdk.
    yaml_file: Path | None = None
    if config_path is not None:
        yaml_file = config_path
    elif target and _looks_like_path(target):
        yaml_file = Path(target)

    if yaml_file is not None:
        return _eject_from_yaml_file(yaml_file, output=output, sdk=sdk)

    # ── Path 2: documented mode — fetch agent by name and write files ──
    if not target:
        console.print(
            "[red]Missing argument:[/red] provide an agent name "
            "([bold]agentbreeder eject my-agent --to yaml[/bold]) "
            "or a path to an existing agent.yaml file."
        )
        raise typer.Exit(code=1)

    if to not in ("yaml", "code"):
        console.print(f"[red]--to must be 'yaml' or 'code' (got '{to}').[/red]")
        raise typer.Exit(code=1)

    agent_record = _fetch_agent_from_registry(target)
    yaml_data = _agent_to_yaml_dict(agent_record)
    name = yaml_data.get("name") or target

    out_dir = _resolve_output_dir(name, output_dir, output, force)

    # Always write agent.yaml.
    yaml_text = _dump_yaml(yaml_data)
    (out_dir / "agent.yaml").write_text(yaml_text, encoding="utf-8")
    created = ["agent.yaml"]

    if to == "code":
        created.extend(_scaffold_full_code(yaml_data, out_dir))

    _print_summary(name, to, out_dir, created)


def _eject_from_yaml_file(yaml_file: Path, output: str | None, sdk: str) -> None:
    """Original behavior: read agent.yaml from disk and dispatch by framework."""
    yaml_content = yaml_file.read_text(encoding="utf-8")

    parsed: dict | None = None
    try:
        parsed = yaml.safe_load(yaml_content)
        framework = parsed.get("framework", "") if isinstance(parsed, dict) else ""
    except Exception:
        framework = ""

    if output:
        out_dir = Path(output)
    else:
        data_for_name = parsed if isinstance(parsed, dict) else {}
        agent_name = data_for_name.get("name", "agent")
        out_dir = Path("agents") / agent_name

    if framework == "crewai":
        try:
            _generate_crewai_scaffold(yaml_content, out_dir)
        except Exception as e:
            console.print(f"[red]Failed to generate CrewAI scaffold: {e}[/red]")
            raise typer.Exit(1) from e
        typer.echo(f"CrewAI scaffold written to {out_dir}")
        return

    if framework == "google_adk":
        try:
            _generate_google_adk_scaffold(yaml_content, out_dir)
        except Exception as e:
            console.print(f"[red]Failed to generate Google ADK scaffold: {e}[/red]")
            raise typer.Exit(1) from e
        typer.echo(f"Google ADK scaffold written to {out_dir}")
        return

    if framework == "claude_sdk":
        try:
            _generate_claude_sdk_scaffold(yaml_content, out_dir)
        except Exception as e:
            console.print(f"[red]Failed to generate Claude SDK scaffold: {e}[/red]")
            raise typer.Exit(1) from e
        typer.echo(f"Claude SDK scaffold written to {out_dir}")
        return

    # Fall through to legacy python/typescript SDK generation.
    if sdk not in ("python", "typescript"):
        console.print(f"[red]Unsupported SDK: {sdk}. Use 'python' or 'typescript'.[/red]")
        raise typer.Exit(1)

    try:
        if sdk == "python":
            code = _generate_python_sdk(yaml_content)
            lang = "python"
            ext = "py"
        else:
            code = _generate_typescript_sdk(yaml_content)
            lang = "typescript"
            ext = "ts"
    except Exception as e:
        console.print(f"[red]Failed to generate SDK code: {e}[/red]")
        raise typer.Exit(1) from e

    if output:
        out_path = Path(output)
    else:
        data = yaml.safe_load(yaml_content)
        name = data.get("name", "agent") if isinstance(data, dict) else "agent"
        out_path = Path("agents") / name / f"agent_sdk.{ext}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(code, encoding="utf-8")

    console.print(
        Panel(
            Syntax(code, lang, theme="monokai", line_numbers=True),
            title=f"[green]Ejected to {out_path}[/green]",
            border_style="green",
        )
    )
    console.print(f"\n[green]SDK scaffold written to[/green] [bold]{out_path}[/bold]")


def _print_summary(name: str, mode: str, out_dir: Path, created: list[str]) -> None:
    """Friendly summary + next-steps panel."""
    files_block = "\n".join(f"  [green]✓[/green] {f}" for f in created)
    if mode == "yaml":
        next_steps = (
            f"  [dim]$[/dim] cd {out_dir.name}\n"
            "  [dim]$[/dim] agentbreeder validate agent.yaml\n"
            "  [dim]$[/dim] agentbreeder deploy --target local"
        )
        tier_note = "[dim]Tier moved: registry/No Code → Low Code (YAML).[/dim]"
    else:
        next_steps = (
            f"  [dim]$[/dim] cd {out_dir.name}\n"
            "  [dim]$[/dim] pip install -r requirements.txt\n"
            "  [dim]$[/dim] python agent.py\n"
            "  [dim]$[/dim] agentbreeder deploy --target local"
        )
        tier_note = "[dim]Tier moved: registry/No Code → Full Code (SDK).[/dim]"

    console.print(
        Panel(
            f"[bold green]Ejected '{name}' to {out_dir}[/bold green]\n\n"
            f"{files_block}\n\n"
            f"  [bold]Next steps:[/bold]\n\n"
            f"{next_steps}\n\n"
            f"{tier_note}",
            title="[bold]Eject complete[/bold]",
            border_style="green",
            padding=(1, 2),
        )
    )
