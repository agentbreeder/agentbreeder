"""agentbreeder scan — discover tools and models from connectors."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from connectors.litellm.connector import LiteLLMConnector
from connectors.mcp_scanner.scanner import MCPScanner
from connectors.openrouter.connector import OpenRouterConnector
from engine.providers.models import ProviderConfig, ProviderType
from engine.providers.ollama_provider import OllamaProvider

console = Console()

REGISTRY_DIR = Path.home() / ".agentbreeder" / "registry"


def scan(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Scan for MCP servers and LiteLLM models, register discoveries."""
    asyncio.run(_run_scan(json_output))


async def _run_scan(json_output: bool = False) -> None:
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    all_results: dict[str, list[dict]] = {"tools": [], "models": []}

    # MCP Scanner
    mcp = MCPScanner()
    if await mcp.is_available():
        tools = await mcp.scan()
        all_results["tools"] = tools
        _save_tools(tools)
        if not json_output:
            console.print(f"[green]MCP Scanner:[/green] discovered {len(tools)} tool(s)")
    else:
        if not json_output:
            console.print("[dim]MCP Scanner: not available[/dim]")

    # LiteLLM
    litellm = LiteLLMConnector()
    if await litellm.is_available():
        models = await litellm.scan()
        all_results["models"] = models
        _save_models(models)
        if not json_output:
            console.print(f"[green]LiteLLM:[/green] discovered {len(models)} model(s)")
    else:
        if not json_output:
            console.print("[dim]LiteLLM: not available (set LITELLM_BASE_URL)[/dim]")

    # Ollama
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    if await OllamaProvider.detect(base_url=ollama_url):
        config = ProviderConfig(provider_type=ProviderType.ollama, base_url=ollama_url)
        provider = OllamaProvider(config)
        ollama_models = await provider.list_models()
        model_dicts = [m.model_dump() for m in ollama_models]
        all_results["models"].extend(model_dicts)
        _save_models(model_dicts)
        await provider.close()
        if not json_output:
            console.print(
                f"[green]Ollama:[/green] discovered {len(ollama_models)} model(s) at {ollama_url}"
            )
    else:
        if not json_output:
            console.print(
                "[dim]Ollama: not available (is Ollama running? try: ollama serve)[/dim]"
            )

    # OpenRouter
    openrouter = OpenRouterConnector()
    if await openrouter.is_available():
        or_models = await openrouter.scan()
        for m in or_models:
            m["gateway_prefix"] = "openrouter"
            m["scanned_at"] = datetime.now(UTC).isoformat()
        all_results["models"].extend(or_models)
        _save_models(or_models)
        if not json_output:
            console.print(f"[green]OpenRouter:[/green] discovered {len(or_models)} model(s)")
    else:
        if not json_output:
            console.print("[dim]OpenRouter: not available (set OPENROUTER_API_KEY)[/dim]")

    if json_output:
        sys.stdout.write(json.dumps(all_results, indent=2) + "\n")

    total = len(all_results["tools"]) + len(all_results["models"])
    if not json_output:
        console.print(f"\n[bold]Total discovered: {total} resource(s)[/bold]")


def _save_tools(tools: list[dict]) -> None:
    path = REGISTRY_DIR / "tools.json"
    existing: dict = {}
    if path.exists():
        existing = json.loads(path.read_text())
    for tool in tools:
        existing[tool["name"]] = tool
    path.write_text(json.dumps(existing, indent=2))


def _save_models(models: list[dict]) -> None:
    path = REGISTRY_DIR / "models.json"
    existing: dict = {}
    if path.exists():
        existing = json.loads(path.read_text())
    for model in models:
        existing[model["name"]] = model
    path.write_text(json.dumps(existing, indent=2))
