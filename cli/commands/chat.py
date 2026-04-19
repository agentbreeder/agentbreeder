"""agentbreeder chat — interactive terminal chat with a deployed agent.

Usage:
    agentbreeder chat my-agent
    agentbreeder chat my-agent --verbose
    agentbreeder chat my-agent --model gpt-4o --env staging
"""

from __future__ import annotations

import json
import os
import signal
import sys
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

console = Console()

API_BASE = os.environ.get("AGENTBREEDER_API_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# Claude Managed Agents helpers
# ---------------------------------------------------------------------------


def _is_managed_agent_endpoint(endpoint_url: str) -> bool:
    """Return True if the endpoint is a Claude Managed Agent (anthropic:// scheme)."""
    return endpoint_url.startswith("anthropic://agents/")


def _parse_managed_endpoint(endpoint_url: str) -> tuple[str, str]:
    """Parse anthropic://agents/{agent_id}?env={env_id} → (agent_id, env_id)."""
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(endpoint_url)
    agent_id = parsed.netloc + parsed.path  # "agents/agent_abc123"
    # Strip leading "agents/" if present
    if agent_id.startswith("agents/"):
        agent_id = agent_id[len("agents/") :]
    env_id = parse_qs(parsed.query).get("env", [""])[0]
    return agent_id, env_id


def _get_agent_endpoint(agent_name: str) -> str | None:
    """Look up the deployed endpoint URL for an agent from the registry API."""
    try:
        with _get_client() as client:
            response = client.get(f"/api/v1/agents/{agent_name}")
            if response.status_code == 200:
                data = response.json().get("data", {})
                return data.get("endpoint_url") or data.get("endpoint")
    except Exception:
        pass
    return None


async def _chat_via_managed_agent(
    agent_id: str,
    environment_id: str,
    message: str,
    verbose: bool = False,
) -> str:
    """Create a session and stream events for a Claude Managed Agent.

    Returns the full assistant response text.
    """
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic SDK not installed. Run: pip install anthropic") from exc

    client = Anthropic()

    session = client.beta.sessions.create(
        agent=agent_id,
        environment_id=environment_id,
        title="agentbreeder chat session",
    )

    response_text = ""
    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": message}],
                }
            ],
        )
        for event in stream:
            if event.type == "agent.message":
                for block in event.content:
                    if hasattr(block, "text"):
                        response_text += block.text
                        if verbose:
                            print(block.text, end="", flush=True)
            elif event.type == "session.status_idle":
                break

    return response_text


def _get_client() -> httpx.Client:
    """Create an httpx client with the configured base URL."""
    return httpx.Client(base_url=API_BASE, timeout=120.0)


def chat(
    agent_name: str = typer.Argument(
        ...,
        help="Name of the deployed agent to chat with",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-V",
        help="Show tool calls, token counts, and latency",
    ),
    model: str = typer.Option(
        None,
        "--model",
        "-m",
        help="Override the agent's configured model",
    ),
    env: str = typer.Option(
        "production",
        "--env",
        "-e",
        help="Target environment: staging, production",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output each turn as JSON (non-interactive, reads from stdin)",
    ),
) -> None:
    """Start an interactive chat session with a deployed agent.

    Sends messages to the agent via the playground API and displays responses
    in the terminal. Use --verbose to see tool calls and token usage.

    Press Ctrl+C to end the session and see a usage summary.

    Examples:
        agentbreeder chat my-agent
        agentbreeder chat my-agent --verbose --model gpt-4o
        echo "hello" | agentbreeder chat my-agent --json
    """
    if json_output:
        _run_json_mode(agent_name, model)
        return

    _run_interactive(agent_name, verbose, model, env)


def _run_interactive(
    agent_name: str,
    verbose: bool,
    model: str | None,
    env: str,
) -> None:
    """Run the interactive chat loop with Rich output."""
    conversation: list[dict[str, str]] = []
    total_tokens = 0
    total_cost = 0.0
    turn_count = 0

    # Detect Claude Managed Agents endpoint
    managed_agent_id: str | None = None
    managed_env_id: str | None = None
    endpoint_url = _get_agent_endpoint(agent_name)
    if endpoint_url and _is_managed_agent_endpoint(endpoint_url):
        managed_agent_id, managed_env_id = _parse_managed_endpoint(endpoint_url)

    console.print()
    console.print(
        Panel(
            f"  Chatting with [bold cyan]{agent_name}[/bold cyan]\n"
            f"  Environment: [dim]{env}[/dim]"
            + (f"\n  Model:       [dim]{model}[/dim]" if model else "")
            + "\n\n  [dim]Type your message and press Enter. Ctrl+C to quit.[/dim]",
            title="AgentBreeder Chat",
            border_style="blue",
        )
    )
    console.print()

    # Graceful exit on Ctrl+C
    def _on_exit(signum: int, frame: Any) -> None:
        _print_session_summary(turn_count, total_tokens, total_cost)
        raise typer.Exit(code=0)

    signal.signal(signal.SIGINT, _on_exit)

    while True:
        try:
            user_input = console.input("[bold green]You:[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            _print_session_summary(turn_count, total_tokens, total_cost)
            raise typer.Exit(code=0) from None

        if not user_input:
            continue

        # Special commands
        if user_input.lower() in ("/quit", "/exit", "/q"):
            _print_session_summary(turn_count, total_tokens, total_cost)
            raise typer.Exit(code=0)

        if user_input.lower() == "/clear":
            conversation.clear()
            total_tokens = 0
            total_cost = 0.0
            turn_count = 0
            console.clear()
            console.print("  [dim]Conversation cleared.[/dim]\n")
            continue

        if user_input.lower() == "/help":
            _print_chat_help()
            continue

        # Add to conversation history
        conversation.append({"role": "user", "content": user_input})

        # Call the agent — Claude Managed Agents use the Anthropic sessions API;
        # all other targets use the local playground API.
        tool_calls: list[Any] = []
        token_count = 0
        cost_estimate = 0.0
        latency_ms = 0
        model_used = ""

        if managed_agent_id and managed_env_id:
            try:
                import asyncio

                assistant_msg = asyncio.run(
                    _chat_via_managed_agent(managed_agent_id, managed_env_id, user_input, verbose)
                )
            except RuntimeError as exc:
                console.print(f"\n  [red]Managed Agent error: {exc}[/red]\n")
                continue
        else:
            try:
                with _get_client() as client:
                    response = client.post(
                        "/api/v1/playground/chat",
                        json={
                            "agent_id": agent_name,
                            "message": user_input,
                            "model_override": model,
                            "conversation_history": conversation[:-1],
                        },
                    )
                    response.raise_for_status()
                    data = response.json()["data"]

            except httpx.ConnectError:
                console.print()
                console.print(f"  [red]Cannot connect to API at {API_BASE}.[/red]")
                console.print(
                    "  [dim]Ensure the server is running: uvicorn api.main:app --port 8000[/dim]"
                )
                console.print()
                raise typer.Exit(code=1) from None

            except httpx.HTTPStatusError as exc:
                detail = ""
                try:
                    detail = exc.response.json().get("detail", str(exc))
                except Exception:
                    detail = str(exc)
                console.print(f"\n  [red]Error: {detail}[/red]\n")
                continue

            assistant_msg = data.get("response", "")
            tool_calls = data.get("tool_calls", [])
            token_count = data.get("token_count", 0)
            cost_estimate = data.get("cost_estimate", 0.0)
            latency_ms = data.get("latency_ms", 0)
            model_used = data.get("model_used", "")

        # Track totals
        total_tokens += token_count
        total_cost += cost_estimate
        turn_count += 1

        # Display tool calls (verbose mode)
        if verbose and tool_calls:
            console.print()
            for tc in tool_calls:
                tool_name = tc.get("tool_name", "unknown")
                tool_input = tc.get("tool_input", {})
                tool_output = tc.get("tool_output", {})
                duration = tc.get("duration_ms", 0)

                tool_lines = [
                    f"  [bold]{tool_name}[/bold] [dim]({duration}ms)[/dim]",
                ]
                if tool_input:
                    tool_lines.append(
                        f"  Input:  [dim]{json.dumps(tool_input, default=str)}[/dim]"
                    )
                if tool_output:
                    output_str = json.dumps(tool_output, default=str)
                    if len(output_str) > 200:
                        output_str = output_str[:200] + "..."
                    tool_lines.append(f"  Output: [dim]{output_str}[/dim]")

                console.print(
                    Panel(
                        "\n".join(tool_lines),
                        title="[dim]Tool Call[/dim]",
                        border_style="dim",
                        padding=(0, 1),
                    )
                )

        # Display assistant response
        console.print()
        console.print(f"[bold blue]{agent_name}:[/bold blue]")
        console.print(Markdown(assistant_msg))

        # Verbose metadata
        if verbose:
            meta_parts = []
            if model_used:
                meta_parts.append(f"model={model_used}")
            meta_parts.append(f"tokens={token_count}")
            meta_parts.append(f"cost=${cost_estimate:.6f}")
            meta_parts.append(f"latency={latency_ms}ms")
            console.print(f"  [dim]{' | '.join(meta_parts)}[/dim]")

        console.print()

        # Update conversation history
        conversation.append({"role": "assistant", "content": assistant_msg})


def _run_json_mode(agent_name: str, model: str | None) -> None:
    """Non-interactive JSON mode: read from stdin, write JSON to stdout."""
    conversation: list[dict[str, str]] = []

    for line in sys.stdin:
        user_input = line.strip()
        if not user_input:
            continue

        conversation.append({"role": "user", "content": user_input})

        try:
            with _get_client() as client:
                response = client.post(
                    "/api/v1/playground/chat",
                    json={
                        "agent_id": agent_name,
                        "message": user_input,
                        "model_override": model,
                        "conversation_history": conversation[:-1],
                    },
                )
                response.raise_for_status()
                data = response.json()["data"]

        except (httpx.ConnectError, httpx.HTTPStatusError) as exc:
            detail = str(exc)
            try:
                if hasattr(exc, "response"):
                    detail = exc.response.json().get("detail", detail)
            except Exception:
                pass
            sys.stdout.write(json.dumps({"error": detail}) + "\n")
            sys.stdout.flush()
            continue

        sys.stdout.write(json.dumps(data, default=str) + "\n")
        sys.stdout.flush()

        conversation.append({"role": "assistant", "content": data.get("response", "")})


def _print_session_summary(turns: int, tokens: int, cost: float) -> None:
    """Print a session summary on exit."""
    console.print()
    if turns == 0:
        console.print("  [dim]No messages exchanged.[/dim]")
        console.print()
        return

    summary_lines = [
        f"  Turns:  {turns}",
        f"  Tokens: {tokens:,}",
        f"  Cost:   ${cost:.6f}",
    ]

    console.print(
        Panel(
            "\n".join(summary_lines),
            title="Session Summary",
            border_style="blue",
        )
    )
    console.print()


def _print_chat_help() -> None:
    """Print help for in-chat commands."""
    console.print()
    table = Table(title="Chat Commands", show_header=False)
    table.add_column("Command", style="cyan")
    table.add_column("Description")

    table.add_row("/help", "Show this help message")
    table.add_row("/clear", "Clear conversation history and counters")
    table.add_row("/quit", "End the chat session")
    table.add_row("Ctrl+C", "End the chat session")

    console.print(table)
    console.print()
