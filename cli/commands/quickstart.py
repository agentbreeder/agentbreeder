"""agentbreeder quickstart — full local platform bootstrap in one command.

What it does:
  1. Detect / guide install of Docker or Podman + Compose
  2. Start the full stack (Postgres, Redis, ChromaDB, Neo4j, MCP servers,
     API, Studio, LiteLLM)
  3. Check LLM provider availability (Ollama or cloud keys)
  4. Seed ChromaDB with sample documents (RAG ready)
  5. Seed Neo4j with a knowledge graph (GraphRAG ready)
  6. Register sample MCP servers, prompts, and tools
  7. Deploy 5 sample agents (RAG, Graph, Search, A2A, Assistant)
  8. Open Studio and show how to use everything

Usage:
    agentbreeder quickstart
    agentbreeder quickstart --cloud aws     # also deploy to AWS after local
    agentbreeder quickstart --no-browser    # headless
    agentbreeder quickstart --skip-seed     # don't seed sample data
"""

from __future__ import annotations

import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import time
import webbrowser
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from rich.table import Table

console = Console()

# Set by quickstart() when --yes is passed. Consulted by prompt helpers so we
# don't have to thread the flag through every single console.input call site.
_ASSUME_YES = False


def _is_assume_yes() -> bool:
    """Return True if quickstart was invoked with --yes."""
    return _ASSUME_YES


def _set_assume_yes(value: bool) -> None:
    global _ASSUME_YES
    _ASSUME_YES = value


# ── Paths ──────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent.parent
DEPLOY_DIR = REPO_ROOT / "deploy"
QS_COMPOSE = DEPLOY_DIR / "docker-compose.quickstart.yml"
QS_LITELLM = DEPLOY_DIR / "litellm_config.quickstart.yaml"
EXAMPLES_QS = REPO_ROOT / "examples" / "quickstart"
MCP_WORKSPACE = DEPLOY_DIR / "mcp_workspace"

API_BASE = os.environ.get("AGENTBREEDER_API_URL", "http://localhost:8000")
CHROMADB_BASE = "http://localhost:8001"
NEO4J_HTTP = "http://localhost:7474"
NEO4J_USER = "neo4j"
NEO4J_PASS = "agentbreeder"
DASHBOARD_URL = "http://localhost:3001"

CLOUD_DEPLOY_DOCS = {
    "aws": "https://docs.agentbreeder.io/deploy/aws",
    "gcp": "https://docs.agentbreeder.io/deploy/gcp",
    "azure": "https://docs.agentbreeder.io/deploy/azure",
}


# ── (Sample docs and Neo4j Cypher live in deploy/seed/seed.py) ─────────────
# Kept here only so the quickstart step count reference is correct.
_PLACEHOLDER = [
    {
        "id": "intro-1",
        "text": (
            "AgentBreeder is an open-source platform for building, deploying, and governing "
            "enterprise AI agents. The core tagline is: Define Once. Deploy Anywhere. Govern "
            "Automatically. A developer writes one agent.yaml file, runs agentbreeder deploy, "
            "and their agent is live on AWS or GCP with RBAC, cost tracking, audit trail, and "
            "org-wide discoverability automatic and zero extra work."
        ),
        "metadata": {"source": "readme", "topic": "overview"},
    },
    {
        "id": "intro-2",
        "text": (
            "AgentBreeder supports three builder tiers: No Code (visual drag-and-drop UI), "
            "Low Code (YAML config via agent.yaml), and Full Code (Python/TypeScript SDK). "
            "All three compile to the same internal format and share the same deploy pipeline, "
            "governance, and observability. Tier mobility is a first-class feature: start No Code, "
            "eject to YAML, eject to Full Code — no vendor lock-in at any level."
        ),
        "metadata": {"source": "readme", "topic": "builder-tiers"},
    },
    {
        "id": "frameworks",
        "text": (
            "AgentBreeder is framework-agnostic. Supported frameworks include LangGraph, CrewAI, "
            "Claude SDK (Anthropic), OpenAI Agents, Google ADK, and Custom (bring your own). "
            "Framework-specific logic lives in engine/runtimes/ and is abstracted away from the "
            "deploy pipeline. Users specify the framework in agent.yaml under the 'framework' field."
        ),
        "metadata": {"source": "claude-md", "topic": "frameworks"},
    },
    {
        "id": "deploy-targets",
        "text": (
            "AgentBreeder supports multi-cloud deployment as a first-class feature. Supported "
            "deployment targets are: AWS ECS Fargate, AWS App Runner, GCP Cloud Run, GKE, "
            "Azure Container Apps, Kubernetes (EKS/GKE/AKS/self-hosted), Claude Managed Agents, "
            "and local Docker Compose. The deploy.cloud field in agent.yaml selects the target."
        ),
        "metadata": {"source": "claude-md", "topic": "deployment"},
    },
    {
        "id": "governance",
        "text": (
            "Governance is a side effect of deploying in AgentBreeder, not extra configuration. "
            "Every agentbreeder deploy automatically validates RBAC, registers the agent in the "
            "registry, attributes cost to the deploying team, and writes an audit log entry. "
            "There is no quick deploy mode that skips governance — this is intentional."
        ),
        "metadata": {"source": "claude-md", "topic": "governance"},
    },
    {
        "id": "agent-yaml",
        "text": (
            "The agent.yaml file is the canonical configuration format. Required fields: name "
            "(slug-friendly), version (SemVer), team, owner (email), framework, model.primary. "
            "Optional fields: description, tags, tools, knowledge_bases, prompts, guardrails, "
            "deploy configuration, access control settings. The schema is validated by a JSON "
            "Schema at engine/schema/agent.schema.json."
        ),
        "metadata": {"source": "claude-md", "topic": "agent-yaml"},
    },
    {
        "id": "rag-support",
        "text": (
            "AgentBreeder has first-class RAG (Retrieval-Augmented Generation) support. "
            "Knowledge bases are defined in agent.yaml under knowledge_bases. Supported backends "
            "include ChromaDB for vector search. The quickstart stack includes a ChromaDB instance "
            "at http://localhost:8001 pre-seeded with AgentBreeder documentation."
        ),
        "metadata": {"source": "quickstart", "topic": "rag"},
    },
    {
        "id": "graphrag-support",
        "text": (
            "AgentBreeder supports GraphRAG via Neo4j. The quickstart stack includes a Neo4j "
            "Community instance at http://localhost:7474 (browser) and bolt://localhost:7687. "
            "The graph is pre-seeded with nodes for agents, tools, frameworks, and providers, "
            "along with their relationships. Login: neo4j / agentbreeder."
        ),
        "metadata": {"source": "quickstart", "topic": "graphrag"},
    },
    {
        "id": "mcp-support",
        "text": (
            "AgentBreeder has deep MCP (Model Context Protocol) integration. MCP servers can be "
            "registered in the platform registry and referenced from agent.yaml via tools: [{ref: tools/my-mcp}]. "
            "The quickstart stack includes MCP filesystem and memory servers. Agents can also "
            "package and deploy their own MCP servers as sidecars."
        ),
        "metadata": {"source": "quickstart", "topic": "mcp"},
    },
    {
        "id": "a2a-support",
        "text": (
            "Agent-to-Agent (A2A) communication allows agents to call each other over a JSON-RPC "
            "protocol. In agent.yaml, define tools with type: a2a and the target agent name. "
            "The a2a-orchestrator in the quickstart routes questions to rag-agent, graph-agent, "
            "and search-agent automatically based on question type. Access the A2A API at "
            "/api/v1/a2a/ endpoints."
        ),
        "metadata": {"source": "quickstart", "topic": "a2a"},
    },
]  # _PLACEHOLDER — real docs are in deploy/seed/docs/*.md


# ── (Neo4j Cypher lives in deploy/seed/seed.py) ─────────────────────────────
_NEO4J_PLACEHOLDER = """
// Frameworks
CREATE (:Framework:QuickstartNode {name:'langgraph', label:'LangGraph', tool_calling:true})
CREATE (:Framework:QuickstartNode {name:'crewai', label:'CrewAI', tool_calling:true})
CREATE (:Framework:QuickstartNode {name:'claude_sdk', label:'Claude SDK', tool_calling:true})
CREATE (:Framework:QuickstartNode {name:'openai_agents', label:'OpenAI Agents', tool_calling:true})
CREATE (:Framework:QuickstartNode {name:'google_adk', label:'Google ADK', tool_calling:true})
CREATE (:Framework:QuickstartNode {name:'custom', label:'Custom', tool_calling:false})

// Providers
CREATE (:Provider:QuickstartNode {name:'anthropic', label:'Anthropic', local:false, free_tier:false})
CREATE (:Provider:QuickstartNode {name:'openai', label:'OpenAI', local:false, free_tier:false})
CREATE (:Provider:QuickstartNode {name:'google', label:'Google AI', local:false, free_tier:true})
CREATE (:Provider:QuickstartNode {name:'ollama', label:'Ollama', local:true, free_tier:true})
CREATE (:Provider:QuickstartNode {name:'openrouter', label:'OpenRouter', local:false, free_tier:false})

// Tools
CREATE (:Tool:QuickstartNode {name:'rag_search', label:'RAG Search', type:'function', backend:'chromadb'})
CREATE (:Tool:QuickstartNode {name:'graph_query', label:'Graph Query', type:'function', backend:'neo4j'})
CREATE (:Tool:QuickstartNode {name:'web_search', label:'Web Search', type:'function', backend:'http'})
CREATE (:Tool:QuickstartNode {name:'mcp_filesystem', label:'MCP Filesystem', type:'mcp', backend:'filesystem'})
CREATE (:Tool:QuickstartNode {name:'mcp_memory', label:'MCP Memory', type:'mcp', backend:'memory'})
CREATE (:Tool:QuickstartNode {name:'call_rag_agent', label:'Call RAG Agent', type:'a2a', backend:'a2a'})
CREATE (:Tool:QuickstartNode {name:'call_graph_agent', label:'Call Graph Agent', type:'a2a', backend:'a2a'})
CREATE (:Tool:QuickstartNode {name:'call_search_agent', label:'Call Search Agent', type:'a2a', backend:'a2a'})

// Agents
CREATE (:Agent:QuickstartNode {name:'rag-agent', label:'RAG Agent', team:'quickstart', status:'running'})
CREATE (:Agent:QuickstartNode {name:'graph-agent', label:'Graph Agent', team:'quickstart', status:'running'})
CREATE (:Agent:QuickstartNode {name:'search-agent', label:'Search Agent', team:'quickstart', status:'running'})
CREATE (:Agent:QuickstartNode {name:'a2a-orchestrator', label:'A2A Orchestrator', team:'quickstart', status:'running'})
CREATE (:Agent:QuickstartNode {name:'assistant', label:'Assistant', team:'quickstart', status:'running'})

// Deploy Targets
CREATE (:DeployTarget:QuickstartNode {name:'local', label:'Local Docker'})
CREATE (:DeployTarget:QuickstartNode {name:'aws', label:'AWS ECS Fargate'})
CREATE (:DeployTarget:QuickstartNode {name:'gcp', label:'GCP Cloud Run'})
CREATE (:DeployTarget:QuickstartNode {name:'azure', label:'Azure Container Apps'})
CREATE (:DeployTarget:QuickstartNode {name:'kubernetes', label:'Kubernetes'})

// Agent-Framework relationships
MATCH (a:Agent {name:'rag-agent'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f)
MATCH (a:Agent {name:'graph-agent'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f)
MATCH (a:Agent {name:'search-agent'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f)
MATCH (a:Agent {name:'a2a-orchestrator'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f)
MATCH (a:Agent {name:'assistant'}), (f:Framework {name:'langgraph'}) CREATE (a)-[:RUNS_ON]->(f)

// Agent-Tool relationships
MATCH (a:Agent {name:'rag-agent'}), (t:Tool {name:'rag_search'}) CREATE (a)-[:USES_TOOL]->(t)
MATCH (a:Agent {name:'graph-agent'}), (t:Tool {name:'graph_query'}) CREATE (a)-[:USES_TOOL]->(t)
MATCH (a:Agent {name:'search-agent'}), (t:Tool {name:'web_search'}) CREATE (a)-[:USES_TOOL]->(t)
MATCH (a:Agent {name:'search-agent'}), (t:Tool {name:'mcp_filesystem'}) CREATE (a)-[:USES_TOOL]->(t)
MATCH (a:Agent {name:'search-agent'}), (t:Tool {name:'mcp_memory'}) CREATE (a)-[:USES_TOOL]->(t)
MATCH (a:Agent {name:'a2a-orchestrator'}), (t:Tool {name:'call_rag_agent'}) CREATE (a)-[:USES_TOOL]->(t)
MATCH (a:Agent {name:'a2a-orchestrator'}), (t:Tool {name:'call_graph_agent'}) CREATE (a)-[:USES_TOOL]->(t)
MATCH (a:Agent {name:'a2a-orchestrator'}), (t:Tool {name:'call_search_agent'}) CREATE (a)-[:USES_TOOL]->(t)

// Agent-Provider relationships (via LiteLLM)
MATCH (a:Agent), (p:Provider {name:'ollama'}) CREATE (a)-[:CALLS_PROVIDER {role:'primary'}]->(p)
MATCH (a:Agent {name:'rag-agent'}), (p:Provider {name:'anthropic'}) CREATE (a)-[:CALLS_PROVIDER {role:'fallback'}]->(p)
MATCH (a:Agent {name:'graph-agent'}), (p:Provider {name:'anthropic'}) CREATE (a)-[:CALLS_PROVIDER {role:'fallback'}]->(p)

// A2A relationships
MATCH (o:Agent {name:'a2a-orchestrator'}), (r:Agent {name:'rag-agent'}) CREATE (o)-[:CALLS_AGENT]->(r)
MATCH (o:Agent {name:'a2a-orchestrator'}), (g:Agent {name:'graph-agent'}) CREATE (o)-[:CALLS_AGENT]->(g)
MATCH (o:Agent {name:'a2a-orchestrator'}), (s:Agent {name:'search-agent'}) CREATE (o)-[:CALLS_AGENT]->(s)

// All agents deploy to local
MATCH (a:Agent), (d:DeployTarget {name:'local'}) CREATE (a)-[:DEPLOYED_ON]->(d)
"""  # _NEO4J_PLACEHOLDER — real Cypher is in deploy/seed/seed.py


# ── Container runtime detection ─────────────────────────────────────────────


def _detect_runtime() -> tuple[str, str] | None:
    """Return (binary, compose_cmd) or None if nothing found.
    compose_cmd is e.g. 'docker compose' or 'podman compose'.

    Prefers a runtime whose daemon is actually running so that, e.g., a user
    with Docker installed but not started and Podman running falls through
    to Podman instead of dead-ending on Docker Desktop.
    """
    candidates: list[tuple[str, str]] = []

    # Docker
    if shutil.which("docker"):
        result = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        if result.returncode == 0:
            candidates.append(("docker", "docker compose"))
        elif shutil.which("docker-compose"):
            candidates.append(("docker", "docker-compose"))

    # Podman
    if shutil.which("podman"):
        result = subprocess.run(["podman", "compose", "version"], capture_output=True, text=True)
        if result.returncode == 0:
            candidates.append(("podman", "podman compose"))
        elif shutil.which("podman-compose"):
            candidates.append(("podman", "podman-compose"))

    # Nerdctl (containerd / Rancher Desktop / Lima)
    if shutil.which("nerdctl"):
        result = subprocess.run(["nerdctl", "compose", "version"], capture_output=True, text=True)
        if result.returncode == 0:
            candidates.append(("nerdctl", "nerdctl compose"))

    if not candidates:
        return None

    # Prefer a runtime whose daemon is actually reachable
    for binary, compose in candidates:
        if _runtime_is_running(binary):
            return (binary, compose)

    # Fall back to first installed candidate; caller will guide the user to start it
    return candidates[0]


def _runtime_is_running(binary: str) -> bool:
    result = subprocess.run([binary, "info"], capture_output=True, text=True)
    return result.returncode == 0


# ── Container-runtime diagnostics (issue #467) ─────────────────────────────
#
# When `docker info` (or equivalent) fails, the user gets a generic "cannot
# connect to daemon" without knowing *why*. These helpers turn the failure
# into a specific cause + fix command.

_DEFAULT_DOCKER_SOCKETS = (
    "/var/run/docker.sock",
    # Common rootless/Snap/Flatpak alternates (XDG_RUNTIME_DIR varies by user,
    # so we resolve $UID lazily in _docker_socket_candidates).
)


def _docker_host_socket_path() -> str | None:
    """Return the filesystem path encoded in $DOCKER_HOST (unix:// only), else None."""
    raw = os.environ.get("DOCKER_HOST", "").strip()
    if raw.startswith("unix://"):
        return raw[len("unix://") :]
    return None


def _docker_socket_candidates() -> list[str]:
    """Sockets to probe when looking for a reachable Docker daemon."""
    paths: list[str] = []
    host_sock = _docker_host_socket_path()
    if host_sock:
        paths.append(host_sock)
    paths.extend(_DEFAULT_DOCKER_SOCKETS)
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        paths.append(f"{xdg}/docker.sock")
    uid = os.environ.get("UID") or (str(os.getuid()) if hasattr(os, "getuid") else "")
    if uid:
        paths.append(f"/run/user/{uid}/docker.sock")
    # De-dupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _docker_socket_status() -> tuple[str | None, str | None]:
    """Return (path, status) where status is one of:

    - "reachable"          — socket exists and we can stat it
    - "permission_denied"  — socket exists but we lack rwx access
    - None (path also None) — no socket found

    The first matching socket wins; we prefer $DOCKER_HOST over defaults.
    """
    for path in _docker_socket_candidates():
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue
        except PermissionError:
            return (path, "permission_denied")
        except OSError:
            continue
        # On Linux a Unix-domain socket has S_IFSOCK; treat anything that stats
        # as "present" — distinguishing the file type matters less than whether
        # we can connect, which os.access covers.
        del st
        if not os.access(path, os.R_OK | os.W_OK):
            return (path, "permission_denied")
        return (path, "reachable")
    return (None, None)


def _docker_is_rootless() -> bool:
    """Heuristic: True if `docker info` advertises the rootless SecurityOption."""
    if not shutil.which("docker"):
        return False
    result = subprocess.run(
        ["docker", "info", "--format", "{{.SecurityOptions}}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    return "rootless" in result.stdout.lower()


def _docker_info_error() -> str:
    """Return the stderr of `docker info` lowercased — empty string if docker is missing."""
    if not shutil.which("docker"):
        return ""
    result = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if result.returncode == 0:
        return ""
    return (result.stderr + result.stdout).lower()


def _diagnose_missing_runtime() -> list[str] | None:
    """When `_detect_runtime()` returns None, check whether a daemon socket is
    nonetheless present — that's the "CLI missing but daemon up" case from #467.

    Returns Rich-formatted hint lines, or None if no socket is reachable
    (in which case the caller falls back to the generic install panel).
    """
    sock, status = _docker_socket_status()
    if not sock:
        return None
    if status == "reachable":
        return [
            f"[yellow]Docker socket reachable at[/yellow] [cyan]{sock}[/cyan] "
            "[yellow]but no `docker` CLI on PATH.[/yellow]",
            "",
            "Looks like a partial install — daemon up, CLI shim missing. Install the CLI:",
            "  [cyan]brew install docker[/cyan]   "
            "[dim]# macOS — pairs with Docker Desktop/Colima/OrbStack[/dim]",
            "  [cyan]sudo apt-get install docker-ce-cli[/cyan]   [dim]# Debian/Ubuntu[/dim]",
            "  [cyan]sudo dnf install docker-ce-cli[/cyan]       [dim]# Fedora/RHEL[/dim]",
            "",
            "[dim]Then re-run [bold cyan]agentbreeder quickstart[/bold cyan].[/dim]",
        ]
    if status == "permission_denied":
        return [
            f"[red]Cannot access Docker socket at[/red] [cyan]{sock}[/cyan] "
            "[red](permission denied).[/red]",
            "",
            "Add your user to the [bold]docker[/bold] group, then start a new login session:",
            "  [cyan]sudo usermod -aG docker $USER[/cyan]",
            "  [cyan]newgrp docker[/cyan]",
            "",
            "[dim]Then re-run [bold cyan]agentbreeder quickstart[/bold cyan].[/dim]",
        ]
    return None


def _diagnose_runtime_failure(binary: str) -> list[str]:
    """Specific cause + fix lines for a runtime that's installed but unreachable.

    Each return value is a list of Rich-markup lines for the *cause* of failure
    — never the generic "could not connect to daemon" string. Falls back to
    the original "daemon not running" hint when nothing specific matches.
    """
    # Stale $DOCKER_HOST takes priority — even other runtimes get tripped by it.
    host_sock = _docker_host_socket_path()
    if host_sock and not Path(host_sock).exists():
        return [
            f"[red]$DOCKER_HOST points at a missing socket:[/red] [cyan]{host_sock}[/cyan]",
            "",
            "Unset it (or point it at a live socket) and re-run:",
            "  [cyan]unset DOCKER_HOST[/cyan]",
        ]

    if binary == "docker":
        err = _docker_info_error()
        sock, status = _docker_socket_status()

        if "permission denied" in err or status == "permission_denied":
            shown = sock or "/var/run/docker.sock"
            return [
                f"[red]Permission denied on Docker socket[/red] [cyan]{shown}[/cyan]",
                "",
                "Your user isn't in the [bold]docker[/bold] group. Fix:",
                "  [cyan]sudo usermod -aG docker $USER[/cyan]",
                "  [cyan]newgrp docker[/cyan]   [dim]# or log out + back in[/dim]",
            ]

        if status == "reachable" and "cannot connect" in err:
            # Socket file is present but the daemon behind it isn't speaking —
            # typical with Flatpak/Snap Docker where the CLI talks to the wrong
            # socket path.
            return [
                "[red]Docker CLI can't talk to the daemon — socket path mismatch.[/red]",
                "",
                f"A socket exists at [cyan]{sock}[/cyan] but the CLI is looking elsewhere.",
                "Override the CLI's target with $DOCKER_HOST:",
                f"  [cyan]export DOCKER_HOST=unix://{sock}[/cyan]",
                "",
                "[dim]This is the usual fix for Snap/Flatpak Docker installs.[/dim]",
            ]

    # Per-runtime "start it" hints (preserved from the previous behavior).
    system = platform.system()
    if binary == "docker":
        if system == "Darwin":
            return [
                "[yellow]Docker daemon is not running.[/yellow] Start one of:",
                "  [dim]• Docker Desktop  (Applications → Docker)[/dim]",
                "  [dim]• OrbStack        (Applications → OrbStack)[/dim]",
                "  [dim]• Colima          [cyan]colima start[/cyan][/dim]",
                "  [dim]• Podman          "
                "[cyan]podman machine init && podman machine start[/cyan][/dim]",
            ]
        if system == "Linux":
            return [
                "[yellow]Docker daemon is not running.[/yellow] Run one of:",
                "  [cyan]sudo systemctl start docker[/cyan]",
                "  [cyan]systemctl --user start podman.socket[/cyan]   "
                "[dim]# rootless podman[/dim]",
            ]
        return [
            "[yellow]Docker daemon is not running.[/yellow] "
            "Start Docker Desktop, Podman Desktop, or Rancher Desktop.",
        ]
    if binary == "podman":
        return [
            "[yellow]Podman machine is not running.[/yellow] Run:",
            "  [cyan]podman machine start[/cyan]",
        ]
    return [
        f"[yellow]{binary} daemon is not running.[/yellow] Start the {binary} daemon and re-run.",
    ]


def _runtime_install_command() -> tuple[list[str], str] | None:
    """Return (cmd_parts, human_readable) for an auto-install of a container runtime.

    Defaults: Docker Desktop on macOS w/ brew, Docker Engine via the official
    convenience script on Linux, manual download on Windows.
    """
    system = platform.system()
    if system == "Darwin":
        if shutil.which("brew"):
            return (
                ["brew", "install", "--cask", "docker"],
                "brew install --cask docker",
            )
        # Brew missing — point the user at the cask install page; no auto-install.
        return None
    if system == "Linux":
        return (
            ["sh", "-c", "curl -fsSL https://get.docker.com | sh"],
            "curl -fsSL https://get.docker.com | sh",
        )
    return None


def _install_instructions() -> list[str]:
    system = platform.system()
    if system == "Darwin":
        return [
            "[bold]Option A — Docker Desktop (recommended, includes Compose):[/bold]",
            "  [cyan]brew install --cask docker[/cyan]",
            "  Then open Docker from Applications and wait for the whale icon.",
            "",
            "[bold]Option B — OrbStack (faster, lighter):[/bold]",
            "  [cyan]brew install --cask orbstack[/cyan]",
            "",
            "[bold]Option C — Podman Desktop:[/bold]",
            "  [cyan]brew install podman[/cyan]",
            "  [cyan]brew install podman-compose[/cyan]",
            "  [cyan]podman machine init && podman machine start[/cyan]",
        ]
    elif system == "Linux":
        return [
            "[bold]Docker Engine (recommended):[/bold]",
            "  [cyan]curl -fsSL https://get.docker.com | sh[/cyan]",
            "  [cyan]sudo usermod -aG docker $USER && newgrp docker[/cyan]",
            "  [cyan]sudo apt-get install docker-compose-plugin[/cyan]",
            "",
            "[bold]Podman (rootless alternative):[/bold]",
            "  [cyan]sudo apt-get install podman podman-compose[/cyan]  # Debian/Ubuntu",
            "  [cyan]sudo dnf install podman podman-compose[/cyan]      # Fedora/RHEL",
        ]
    else:
        return [
            "[bold]Docker Desktop for Windows:[/bold]",
            "  [link=https://docker.com/products/docker-desktop]https://docker.com/products/docker-desktop[/link]",
            "",
            "[bold]Or via winget:[/bold]",
            "  [cyan]winget install Docker.DockerDesktop[/cyan]",
        ]


# ── Health-checking ─────────────────────────────────────────────────────────


def _wait_http(url: str, timeout: int = 120, interval: float = 3.0) -> bool:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            resp = httpx.get(url, timeout=4.0)
            if resp.status_code < 500:
                return True
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ReadError):
            pass
        time.sleep(interval)
    return False


def _dashboard_smoke_check(url: str) -> tuple[bool, str | None]:
    """Validate Studio is serving a usable Vite-built bundle.

    Returns (ok, error_reason). A failure here typically means the published
    Docker image is stale (e.g. shipped with mismatched react/react-dom
    versions causing a runtime crash, leaving the page blank). When that
    happens, rebuilding from source via `--dev` (or pulling a newer image)
    is the fix.
    """
    try:
        html_resp = httpx.get(url, timeout=5.0)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as exc:
        return False, f"unreachable: {exc}"
    if html_resp.status_code != 200:
        return False, f"returned HTTP {html_resp.status_code}"
    html = html_resp.text
    if '<div id="root"' not in html and '<div id="root"' not in html:
        return False, "HTML missing React mount point"
    # Pull out the Vite bundle path: /assets/index-XXXXXXXX.js
    match = re.search(r'src="(/assets/index-[A-Za-z0-9_-]+\.js)"', html)
    if not match:
        # Older or differently-built bundles may not match — accept silently
        return True, None
    bundle_path = match.group(1)
    try:
        bundle_resp = httpx.get(url.rstrip("/") + bundle_path, timeout=10.0)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as exc:
        return False, f"bundle fetch failed: {exc}"
    if bundle_resp.status_code != 200 or len(bundle_resp.content) < 1024:
        return False, f"bundle suspiciously small ({len(bundle_resp.content)} bytes)"
    return True, None


# ── Docker / Compose helpers ────────────────────────────────────────────────


def _compose_run(
    compose_cmd: str,
    args: list[str],
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    parts = compose_cmd.split()
    cmd = [*parts, "-f", str(QS_COMPOSE), "--project-name", "agentbreeder-qs", *args]
    run_env = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd,
        cwd=str(DEPLOY_DIR),
        env=run_env,
        capture_output=capture,
        text=True,
    )


def _podman_socket() -> str | None:
    """Return the path to podman's Unix socket, or None if podman isn't running.

    Used to redirect a hijacking docker-compose shim (Rancher Desktop) at
    podman's daemon when podman is the chosen runtime.
    """
    if not shutil.which("podman"):
        return None
    try:
        result = subprocess.run(
            ["podman", "info", "--format", "{{.Host.RemoteSocket.Path}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    path = (result.stdout or "").strip()
    if result.returncode != 0 or not path:
        return None
    # Path may already start with unix:// — strip it. Verify the socket exists.
    if path.startswith("unix://"):
        path = path[len("unix://") :]
    if not Path(path).exists():
        return None
    return path


def _compose_preflight(compose_cmd: str, env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Verify the compose subcommand can talk to a working engine.

    Uses `compose ls` because it round-trips to the daemon (unlike `version`,
    which only prints the binary's own version without contacting anything).
    Catches cases like podman delegating to a Rancher Desktop docker-compose
    shim whose socket is dead.

    Returns (ok, combined_stderr_stdout).
    """
    parts = compose_cmd.split()
    run_env = {**os.environ, **(env or {})}
    try:
        result = subprocess.run(
            [*parts, "ls", "-q"],
            env=run_env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)
    blob = (result.stdout or "") + "\n" + (result.stderr or "")
    return result.returncode == 0, blob


# ── Seeding — delegate to deploy/seed/seed.py ──────────────────────────────


def _load_seed_module():
    """Load deploy/seed/seed.py as a module."""
    import importlib.util

    seed_path = DEPLOY_DIR / "seed" / "seed.py"
    spec = importlib.util.spec_from_file_location("qs_seed", seed_path)
    if spec is None or spec.loader is None:
        return None
    # module_from_spec sets __file__, __spec__, etc. so seed.py can use Path(__file__)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _seed_chromadb() -> dict:
    """Seed ChromaDB via deploy/seed/seed.py.

    Returns the full status dict from seed.seed_chromadb() so callers can
    distinguish "not installed" from "not reachable" and print precise hints.
    """
    mod = _load_seed_module()
    if mod is None:
        return {"ok": False, "error": "could not load deploy/seed/seed.py"}
    return mod.seed_chromadb()


def _seed_neo4j() -> bool:
    """Seed Neo4j via deploy/seed/seed.py. Returns True on success."""
    mod = _load_seed_module()
    if mod is None:
        return False
    # Neo4j's transaction API can lag behind the HTTP discovery endpoint;
    # retry a few times with backoff to let it fully initialise.
    for attempt in range(4):
        result = mod.seed_neo4j()
        if result.get("ok", False):
            return True
        if attempt < 3:
            time.sleep(8)
    return False


# ── AgentBreeder API registration ───────────────────────────────────────────

_API_TOKEN: str | None = None


def _get_api_token() -> str | None:
    """Log in as the default admin and return a JWT token (cached)."""
    global _API_TOKEN
    if _API_TOKEN:
        return _API_TOKEN
    try:
        resp = httpx.post(
            f"{API_BASE}/api/v1/auth/login",
            json={"email": "admin@agentbreeder.local", "password": "plant"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            _API_TOKEN = resp.json()["data"]["access_token"]
            return _API_TOKEN
    except Exception:
        pass
    return None


def _api_post(path: str, payload: dict) -> dict | None:
    token = _get_api_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        resp = httpx.post(f"{API_BASE}{path}", json=payload, headers=headers, timeout=15.0)
        if resp.status_code in (200, 201):
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return None


def _register_mcp_servers() -> int:
    """Register the MCP servers that quickstart spun up. Returns number registered."""
    servers = [
        {
            "name": "mcp-filesystem",
            "description": "MCP server exposing read/write access to /workspace",
            "transport": "stdio",
            "command": "npx @modelcontextprotocol/server-filesystem /workspace",
            "tags": ["filesystem", "mcp", "quickstart"],
            "team": "quickstart",
        },
        {
            "name": "mcp-memory",
            "description": "MCP server for persistent key-value memory across conversations",
            "transport": "stdio",
            "command": "npx @modelcontextprotocol/server-memory",
            "tags": ["memory", "mcp", "quickstart"],
            "team": "quickstart",
        },
    ]
    count = 0
    for s in servers:
        result = _api_post("/api/v1/mcp-servers", s)
        if result:
            count += 1
    return count


def _register_prompts() -> int:
    """Register sample prompt templates. Returns number registered."""
    prompts = [
        {
            "name": "rag-system-prompt",
            "version": "1.0.0",
            "description": "System prompt for RAG agents — instructs to use vector search before answering",
            "content": (
                "You are a helpful AI assistant with access to a knowledge base. "
                "Always use the rag_search tool to find relevant information before answering. "
                "Cite the document source and confidence level in your response."
            ),
            "tags": ["rag", "system", "quickstart"],
            "team": "quickstart",
        },
        {
            "name": "a2a-orchestrator-prompt",
            "version": "1.0.0",
            "description": "System prompt for A2A orchestrators — explains routing logic",
            "content": (
                "You are an intelligent orchestrator. Route questions to the right specialist:\n"
                "- rag-agent: factual/documentation questions\n"
                "- graph-agent: relationship/connection questions\n"
                "- search-agent: current events or file access\n"
                "For complex questions, consult multiple agents and synthesize results."
            ),
            "tags": ["a2a", "orchestration", "system", "quickstart"],
            "team": "quickstart",
        },
    ]
    count = 0
    for p in prompts:
        result = _api_post("/api/v1/registry/prompts", p)
        if result:
            count += 1
    return count


def _register_agents() -> list[dict]:
    """Register sample agents in the platform registry."""
    agents = []
    yamls = sorted(EXAMPLES_QS.glob("*.yaml"))
    for yaml_path in yamls:
        try:
            content = yaml_path.read_text()
            result = _api_post("/api/v1/agents/from-yaml", {"yaml_content": content})
            if result:
                agents.append(result.get("data", {}))
        except Exception:
            pass
    return agents


def _deploy_agents_local(compose_cmd: str, env: dict[str, str]) -> bool:
    """Trigger local deployment for each quickstart agent via the API."""
    yaml_files = sorted(EXAMPLES_QS.glob("*.yaml"))
    ok = 0
    for yaml_path in yaml_files:
        result = _api_post(
            "/api/v1/deploys",
            {
                "agent_yaml": yaml_path.read_text(),
                "target": "local",
                "dry_run": False,
            },
        )
        if result:
            ok += 1
    return ok == len(yaml_files)


# ── Cloud deployment helper ─────────────────────────────────────────────────


def _check_cloud_prerequisites(target: str) -> bool:
    """Check that the cloud CLI is installed and authenticated. Returns True if all checks pass."""
    cli_checks = {
        "aws": ("aws", "brew install awscli"),
        "gcp": ("gcloud", "brew install --cask google-cloud-sdk"),
        "azure": ("az", "brew install azure-cli"),
    }
    auth_checks = {
        "aws": (
            ["aws", "sts", "get-caller-identity", "--output", "text"],
            "Run: aws configure",
        ),
        "gcp": (
            ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
            "Run: gcloud auth login",
        ),
        "azure": (
            ["az", "account", "show", "--output", "none"],
            "Run: az login",
        ),
    }

    if target not in cli_checks:
        return True

    cli_binary, install_cmd = cli_checks[target]
    if not shutil.which(cli_binary):
        console.print(
            f"  [red]{cli_binary} CLI not found.[/red] Install with: [bold cyan]{install_cmd}[/bold cyan]"
        )
        return False

    auth_cmd, auth_hint = auth_checks[target]
    result = subprocess.run(auth_cmd, capture_output=True, text=True)
    if result.returncode != 0 or (target == "gcp" and not result.stdout.strip()):
        console.print(f"  [red]{target.upper()} authentication not active.[/red] {auth_hint}")
        return False

    return True


def _guide_cloud_deploy(target: str, assume_yes: bool = False) -> None:
    """Interactive guide for deploying to AWS / GCP / Azure."""
    docs_url = CLOUD_DEPLOY_DOCS.get(target, "https://docs.agentbreeder.io")

    cloud_info: dict[str, dict] = {
        "aws": {
            "prereqs": [
                "AWS CLI installed + configured",
                "IAM permissions: ECS, ECR, CloudFormation",
            ],
            "cmd": "agentbreeder deploy --target aws --region us-east-1",
            "env_keys": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"],
        },
        "gcp": {
            "prereqs": ["gcloud CLI installed + authenticated", "Cloud Run API enabled"],
            "cmd": "agentbreeder deploy --target gcp --region us-central1",
            "env_keys": ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"],
        },
        "azure": {
            "prereqs": ["Azure CLI installed + logged in (az login)", "Container Apps extension"],
            "cmd": "agentbreeder deploy --target azure --region eastus",
            "env_keys": ["AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP"],
        },
    }

    info = cloud_info.get(target)
    if not info:
        console.print(f"  [red]Unknown cloud target: {target}[/red]")
        return

    console.print()
    console.print(Rule(f"[bold]Deploy to {target.upper()}[/bold]", style="blue"))
    console.print()

    # Check prereqs
    console.print("  [bold]Prerequisites:[/bold]")
    for prereq in info["prereqs"]:
        console.print(f"    [dim]•[/dim] {prereq}")
    console.print()

    # Check env keys
    missing_keys = [k for k in info["env_keys"] if not os.environ.get(k)]
    if missing_keys:
        console.print("  [yellow]Missing environment variables:[/yellow]")
        if _is_assume_yes():
            for k in missing_keys:
                console.print(f"    [dim]{k}[/dim] [dim](--yes: skipped)[/dim]")
        else:
            for k in missing_keys:
                val = console.input(f"    [bold]{k}[/bold] (or Enter to skip): ").strip()
                if val:
                    os.environ[k] = val
                    _write_env_key(k, val)
        console.print()

    console.print("  [bold]Deploy command:[/bold]")
    console.print(f"    [bold cyan]{info['cmd']}[/bold cyan]")
    console.print()
    console.print(f"  [dim]Docs: {docs_url}[/dim]")
    console.print()

    if assume_yes:
        console.print(
            "  [dim]--yes: skipping interactive cloud deploy. Run "
            f"[cyan]{info['cmd']} <agent.yaml>[/cyan] when you're ready.[/dim]"
        )
        proceed = "n"
    else:
        proceed = console.input("  [bold]Deploy now? (y/N): [/bold]").strip().lower()
    if proceed == "y":
        yaml_files = sorted(EXAMPLES_QS.glob("*.yaml"))
        if yaml_files:
            target_yaml = yaml_files[0]  # deploy assistant agent as first example
            cmd_parts = info["cmd"].split() + [str(target_yaml)]
            console.print(f"\n  [dim]Running: {' '.join(cmd_parts)}[/dim]\n")
            subprocess.run(cmd_parts)
        else:
            console.print(
                Panel(
                    f"  Run this from any agent directory:\n\n"
                    f"  [bold cyan]{info['cmd']}[/bold cyan]\n\n"
                    f"  Or create a new agent first:\n"
                    f"  [bold cyan]agentbreeder init[/bold cyan]",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )


_CLOUD_PROVIDERS: list[tuple[str, str, str, str]] = [
    # (display_name,          env_key,               placeholder,   models)
    ("OpenAI", "OPENAI_API_KEY", "sk-...", "gpt-4o, gpt-4o-mini"),
    ("Anthropic (Claude)", "ANTHROPIC_API_KEY", "sk-ant-...", "claude-sonnet-4, claude-haiku-4"),
    ("Google Gemini", "GOOGLE_API_KEY", "AIza...", "gemini-2.0-flash"),
    ("OpenRouter", "OPENROUTER_API_KEY", "sk-or-...", "100+ models, pay-per-use"),
]


# ── Ollama bootstrap ────────────────────────────────────────────────────────
# Default local model — Google's latest small open-weights model (~2 GB),
# fast on most laptops and a strong general-purpose choice.
DEFAULT_LOCAL_MODEL = "gemma3"


def _ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def _ollama_running() -> bool:
    try:
        resp = httpx.get("http://localhost:11434/", timeout=3.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException, httpx.ConnectTimeout, httpx.ReadError):
        return False


def _ollama_models() -> list[str]:
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
        if resp.status_code == 200:
            return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        pass
    return []


def _ollama_bind_is_localhost_only() -> bool | None:
    """Detect whether Ollama is bound only to 127.0.0.1 on macOS.

    When bound to 127.0.0.1, Docker/Podman containers cannot reach it via
    host.docker.internal:11434 — the API container will fall back to cloud
    providers or fail. Returns:
      True  → bound only to 127.0.0.1 (needs rebind to 0.0.0.0)
      False → bound to 0.0.0.0 / *  (good)
      None  → could not determine (lsof unavailable, not macOS, etc.)
    """
    if platform.system() != "Darwin":
        return None
    if not shutil.which("lsof"):
        return None
    try:
        r = subprocess.run(
            ["lsof", "-nP", "-iTCP:11434", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    # lsof output column for the address looks like "127.0.0.1:11434" or
    # "*:11434" or "[::1]:11434". We want True only if EVERY listening
    # address is 127.0.0.1 / [::1] (loopback-only).
    listening_addrs: list[str] = []
    for line in r.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 9:
            continue
        # Address is the 9th column ("NAME"); may include "->" for ESTABLISHED
        addr = parts[8]
        if "->" in addr:
            continue
        listening_addrs.append(addr)
    if not listening_addrs:
        return None
    loopback_prefixes = ("127.", "[::1]:", "localhost:")
    return all(a.startswith(loopback_prefixes) for a in listening_addrs)


def _rebind_ollama_all_interfaces() -> bool:
    """Rebind macOS Ollama daemon to 0.0.0.0:11434 so containers can reach it.

    Sets the launchd-wide OLLAMA_HOST env var, then restarts whichever Ollama
    process is currently running (Ollama.app or `ollama serve`). Returns True
    if the daemon comes back up bound to a non-loopback interface.
    """
    if platform.system() != "Darwin":
        return False
    # Set the env var for any future launchd-launched processes (Ollama.app)
    subprocess.run(
        ["launchctl", "setenv", "OLLAMA_HOST", "0.0.0.0:11434"],
        capture_output=True,
    )
    # Try to restart the Ollama service. Approach in priority order:
    #  1. brew services restart ollama  (if installed via brew)
    #  2. quit Ollama.app via osascript + relaunch
    #  3. pkill ollama + spawn `ollama serve` with OLLAMA_HOST set
    restarted = False
    if shutil.which("brew"):
        r = subprocess.run(["brew", "services", "restart", "ollama"], capture_output=True)
        restarted = r.returncode == 0
    if not restarted:
        # Quit Ollama.app gracefully if running
        subprocess.run(
            ["osascript", "-e", 'tell application "Ollama" to quit'],
            capture_output=True,
        )
        # Hard-kill the daemon if still around
        subprocess.run(["pkill", "-f", "ollama"], capture_output=True)
        time.sleep(2)
        # Relaunch the app if available, else fall back to `ollama serve`
        if os.path.isdir("/Applications/Ollama.app"):
            subprocess.run(["open", "-a", "Ollama"], capture_output=True)
        elif shutil.which("ollama"):
            env = os.environ.copy()
            env["OLLAMA_HOST"] = "0.0.0.0:11434"
            try:
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                )
            except Exception:
                return False
        restarted = True
    # Wait for the daemon to come back up, then verify the bind
    for _ in range(15):
        time.sleep(1)
        if _ollama_running():
            bind_localhost = _ollama_bind_is_localhost_only()
            if bind_localhost is False:
                return True
            if bind_localhost is None:
                # Couldn't verify — assume rebind worked since daemon is up
                return True
    return False


def _ollama_install_command() -> tuple[list[str], str] | None:
    """Return (cmd_parts, human_description) or None if no auto-install path."""
    system = platform.system()
    if system == "Darwin":
        if shutil.which("brew"):
            return (["brew", "install", "ollama"], "brew install ollama")
        return (
            ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            "curl -fsSL https://ollama.com/install.sh | sh",
        )
    if system == "Linux":
        return (
            ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            "curl -fsSL https://ollama.com/install.sh | sh",
        )
    return None  # Windows: no portable one-liner — point user to download page


def _start_ollama() -> bool:
    """Best-effort start of the Ollama daemon. Returns True if it ends up running."""
    system = platform.system()

    # macOS: prefer brew services (leaves a launchd unit for persistence)
    if system == "Darwin" and shutil.which("brew"):
        subprocess.run(["brew", "services", "start", "ollama"], capture_output=True)
        for _ in range(8):
            time.sleep(1)
            if _ollama_running():
                return True

    # Linux: try systemd user/system unit (Ollama installer registers one)
    if system == "Linux" and shutil.which("systemctl"):
        for unit_args in (["--user"], []):
            r = subprocess.run(["systemctl", *unit_args, "start", "ollama"], capture_output=True)
            if r.returncode == 0:
                for _ in range(8):
                    time.sleep(1)
                    if _ollama_running():
                        return True

    # Fallback: launch `ollama serve` detached so it survives this script
    if shutil.which("ollama"):
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            return False
        for _ in range(10):
            time.sleep(1)
            if _ollama_running():
                return True

    return _ollama_running()


def _ask_model_source(no_ollama_flag: bool, assume_yes: bool = False) -> tuple[bool, bool]:
    """Ask up front whether to set up local models, cloud providers, or both.

    Returns ``(skip_ollama, skip_cloud_keys)``.

    Short-circuits when the user has already declared their intent:
      - ``--no-ollama`` on the CLI → ``(True, False)``  (cloud path)
      - ``--yes`` / non-TTY         → ``(no_ollama_flag, False)``  (legacy default)

    Choices:
      1. Local   — install Ollama, pull a model. Skip cloud key prompts.
      2. Cloud   — skip Ollama entirely. Prompt for cloud provider keys.
      3. Both    — local Ollama plus cloud keys.

    When Ollama is already running with models, option 1 (Local) becomes the
    default so that pressing Enter takes the free, no-key path.  When Ollama is
    not detected, option 3 (Both) remains the default.
    """
    if no_ollama_flag:
        return (True, False)
    import sys as _sys

    if assume_yes or not _sys.stdin.isatty():
        return (False, False)

    # Detect whether Ollama is already up with at least one model.
    ollama_ready = _ollama_running() and bool(_ollama_models())

    if ollama_ready:
        panel_body = (
            "[bold cyan]How do you want to run models?[/bold cyan]\n\n"
            "  [bold]1.[/bold] [bold green]Local[/bold green] — use your local Ollama models "
            "[dim](free, no key — press Enter)[/dim]  ← default\n"
            "  [bold]2.[/bold] Cloud — OpenAI · Anthropic · Google · OpenRouter "
            "[dim](BYO API key, no download)[/dim]\n"
            "  [bold]3.[/bold] Both — local Ollama plus cloud keys"
        )
        default_prompt = "  Choice [1/2/3, default=1]: "
        default_choice = "1"
    else:
        panel_body = (
            "[bold cyan]How do you want to run models?[/bold cyan]\n\n"
            "  [bold]1.[/bold] Local — install Ollama and pull a model "
            "[dim](~3 GB; offline / privacy)[/dim]\n"
            "  [bold]2.[/bold] Cloud — OpenAI · Anthropic · Google · OpenRouter "
            "[dim](BYO API key, no download)[/dim]\n"
            "  [bold]3.[/bold] Both — local Ollama plus cloud keys "
            "[dim](default)[/dim]"
        )
        default_prompt = "  Choice [1/2/3, default=3]: "
        default_choice = "3"

    console.print()
    console.print(
        Panel(
            panel_body,
            border_style="cyan",
            padding=(1, 3),
        )
    )

    while True:
        answer = console.input(default_prompt).strip()
        effective = answer if answer else default_choice
        if effective == "1":
            return (False, True)
        if effective == "2":
            console.print(
                "  [dim]Skipping Ollama install. Equivalent flag: [bold]--no-ollama[/bold].[/dim]"
            )
            return (True, False)
        if effective == "3":
            return (False, False)
        console.print("  [red]Enter 1, 2, or 3.[/red]")


def _ensure_ollama(skip: bool, default_model: str) -> bool:
    """Interactive bootstrap: install Ollama → start daemon → pull a model.

    Returns True if Ollama ends up running with at least one usable model.
    Each step is opt-in (default Yes) so the user can decline anywhere.
    """
    if skip:
        if _ollama_running() and _ollama_models():
            _ok("Ollama already running with model(s) — using existing setup")
            return True
        _info("Skipping Ollama setup (--no-ollama)")
        return False

    # ── Install ──
    if not _ollama_installed():
        console.print(
            "  [bold]Ollama[/bold] runs AI models entirely on your laptop — "
            "[bold green]free, private, no API key.[/bold green]"
        )
        install = _ollama_install_command()
        if install is None:
            console.print(
                "  [dim]Auto-install isn't supported on this OS.[/dim]\n"
                "  [dim]Download manually: [cyan]https://ollama.com/download[/cyan][/dim]"
            )
            if _is_assume_yes():
                _info("--yes: skipping Ollama (manual install required on this OS)")
                return False
            ans = (
                console.input(
                    "  [bold]Press Enter once installed, or type [cyan]skip[/cyan]: [/bold]"
                )
                .strip()
                .lower()
            )
            if ans == "skip" or not _ollama_installed():
                _info("Skipping Ollama (not installed)")
                return False
        else:
            cmd_parts, human = install
            if _is_assume_yes():
                console.print(f"  [dim]--yes: installing Ollama ([cyan]{human}[/cyan])[/dim]")
                ans = "y"
            else:
                ans = (
                    console.input(
                        f"  [bold]Install Ollama now?[/bold] "
                        f"[dim](runs: [cyan]{human}[/cyan])[/dim] [Y/n]: "
                    )
                    .strip()
                    .lower()
                )
            if ans in ("n", "no", "skip"):
                _info("Skipping Ollama install")
                return False
            console.print()
            r = subprocess.run(cmd_parts)
            if r.returncode != 0 or not _ollama_installed():
                _warn("Ollama install failed — install manually from https://ollama.com")
                return False
            _ok("Ollama installed")

    # ── Start ──
    if not _ollama_running():
        console.print("  [dim]Starting Ollama daemon...[/dim]")
        if not _start_ollama():
            _warn(
                "Ollama is installed but the daemon isn't responding. "
                "Run [cyan]ollama serve[/cyan] in another terminal, then re-run quickstart."
            )
            return False
    _ok("Ollama running at [cyan]http://localhost:11434[/cyan]")

    # ── Verify bind is reachable from Docker containers ──
    # On macOS, Ollama.app defaults to 127.0.0.1 only. Containers reach the
    # host via host.docker.internal, which routes to a non-loopback IP — so a
    # 127.0.0.1-only bind is invisible to the API container. Detect and
    # offer to rebind to 0.0.0.0:11434 in one click.
    bind_localhost = _ollama_bind_is_localhost_only()
    if bind_localhost is True:
        console.print()
        _warn(
            "Ollama is listening on [cyan]127.0.0.1[/cyan] only — Docker "
            "containers cannot reach it via [cyan]host.docker.internal[/cyan]."
        )
        if _is_assume_yes():
            console.print("  [dim]--yes: rebinding Ollama to 0.0.0.0:11434[/dim]")
            ans = "y"
        else:
            ans = (
                console.input(
                    "  [bold]Rebind Ollama to 0.0.0.0:11434 so containers can "
                    "reach it?[/bold] [dim](runs: [cyan]launchctl setenv "
                    "OLLAMA_HOST 0.0.0.0:11434[/cyan] + restarts Ollama)[/dim] "
                    "[Y/n]: "
                )
                .strip()
                .lower()
            )
        if ans not in ("n", "no", "skip"):
            console.print("  [dim]Rebinding Ollama daemon...[/dim]")
            if _rebind_ollama_all_interfaces():
                _ok("Ollama rebound to 0.0.0.0:11434 — reachable from containers")
            else:
                _warn(
                    "Could not auto-rebind. Run manually:\n"
                    "    [cyan]launchctl setenv OLLAMA_HOST 0.0.0.0:11434[/cyan]\n"
                    '    [cyan]osascript -e \'tell application "Ollama" to '
                    "quit'[/cyan] && [cyan]open -a Ollama[/cyan]"
                )
        else:
            _info(
                "Skipping rebind — local Ollama models will be unreachable "
                "from API container; agents will use cloud providers."
            )

    # ── Pull a model ──
    models = _ollama_models()
    if models:
        _ok(f"Local model(s) available: [cyan]{', '.join(models[:3])}[/cyan]")
        return True

    console.print()
    console.print("  [bold]No local models installed yet.[/bold]")
    console.print(
        f"  [dim]Default: [cyan]{default_model}[/cyan] — Google's latest small "
        "open-weights model (~2 GB).[/dim]"
    )
    console.print(
        "  [dim]Other good picks: [cyan]llama3.2[/cyan] · [cyan]phi4-mini[/cyan] · "
        "[cyan]qwen2.5[/cyan] · [cyan]mistral[/cyan][/dim]"
    )
    if _is_assume_yes():
        console.print(f"  [dim]--yes: pulling [cyan]{default_model}[/cyan][/dim]")
        ans = ""
    else:
        ans = console.input(
            "  [bold]Pull a model now?[/bold] [Y/n / type a model name to override]: "
        ).strip()
    if ans.lower() in ("n", "no", "skip"):
        _warn("No model pulled — agents will need a model before they can run")
        return False
    model = ans if ans and ans.lower() not in ("y", "yes") else default_model

    console.print(f"\n  [dim]Running: [cyan]ollama pull {model}[/cyan][/dim]\n")
    r = subprocess.run(["ollama", "pull", model])
    if r.returncode == 0:
        _ok(f"Pulled [cyan]{model}[/cyan] — ready for local inference")
        return True
    _warn(f"Failed to pull {model}. You can retry with [cyan]ollama pull {model}[/cyan].")
    return False


def _collect_provider_keys(existing_env: dict, assume_yes: bool = False) -> tuple[dict, bool]:
    """Interactively prompt for cloud API keys. Returns (new_keys_dict, has_ollama).

    Under ``--yes`` the prompts are skipped — the caller is expected to have
    already exported provider keys via the environment.
    """
    has_ollama = False
    try:
        resp = httpx.get("http://localhost:11434/", timeout=3.0)
        has_ollama = resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    console.print()
    console.print("  [bold]Provider status:[/bold]")
    console.print()
    if has_ollama:
        console.print("  [green]●[/green] Ollama  [dim](running — free local inference)[/dim]")
    else:
        console.print(
            "  [dim]○[/dim] Ollama  [dim](not running — "
            "start with: [cyan]ollama serve[/cyan])[/dim]"
        )

    for name, env_key, _ph, models in _CLOUD_PROVIDERS:
        existing = existing_env.get(env_key) or os.environ.get(env_key, "")
        if existing:
            masked = existing[:10] + "..." if len(existing) > 10 else "***"
            console.print(f"  [green]●[/green] {name}  [dim]({masked} — already set)[/dim]")
        else:
            console.print(f"  [dim]○[/dim] {name}  [dim]({models})[/dim]")

    if assume_yes:
        console.print()
        console.print(
            "  [dim]--yes: skipping key prompts. Set provider keys via env vars "
            "(e.g. [cyan]OPENAI_API_KEY[/cyan]) before re-running if any are missing.[/dim]"
        )
        return {}, has_ollama

    console.print()
    console.print("  [bold]Add or update API keys[/bold]  [dim](press Enter to skip any)[/dim]")
    console.print()

    collected: dict[str, str] = {}
    for name, env_key, placeholder, _models in _CLOUD_PROVIDERS:
        existing = existing_env.get(env_key) or os.environ.get(env_key, "")
        if existing:
            masked = existing[:10] + "..." if len(existing) > 10 else "***"
            prompt = f"  {name} [dim]({masked}, Enter to keep)[/dim]: "
        else:
            prompt = f"  {name} [dim]({placeholder}, Enter to skip)[/dim]: "
        val = console.input(prompt).strip()
        if val:
            collected[env_key] = val

    return collected, has_ollama


def _write_env_key(key: str, value: str) -> None:
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={value}")
        env_path.write_text("\n".join(lines) + "\n")
    else:
        env_path.write_text(f"{key}={value}\n")


# ── Print helpers ───────────────────────────────────────────────────────────


_QS_PORTS: dict[int, str] = {
    5432: "PostgreSQL",
    6379: "Redis",
    8000: "AgentBreeder API",
    3001: "Studio",
    4000: "LiteLLM",
    8001: "ChromaDB",
    7474: "Neo4j HTTP",
    7687: "Neo4j Bolt",
}


def _check_ports() -> list[tuple[int, str]]:
    """Return list of (port, service) that are already in use."""
    taken = []
    for port, name in _QS_PORTS.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                taken.append((port, name))
    return taken


def _port_owners(port: int) -> list[tuple[int, str]]:
    """Return [(pid, command_name), ...] for processes listening on a TCP port.

    Uses lsof when available (macOS / most Linux). Returns [] if lsof is absent
    or returns no PIDs.
    """
    if not shutil.which("lsof"):
        return []
    result = subprocess.run(
        ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
    )
    pids: list[int] = []
    for tok in result.stdout.split():
        tok = tok.strip()
        if tok.isdigit():
            pids.append(int(tok))
    owners: list[tuple[int, str]] = []
    seen: set[int] = set()
    for pid in pids:
        if pid in seen:
            continue
        seen.add(pid)
        ps = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
        )
        cmd = (ps.stdout.strip() or "?").split("/")[-1]
        owners.append((pid, cmd))
    return owners


def _kill_port_owners(pids: list[int]) -> list[int]:
    """SIGTERM, then SIGKILL stragglers. Returns PIDs that are still alive."""
    if not pids:
        return []
    subprocess.run(["kill", *(str(p) for p in pids)], capture_output=True)
    time.sleep(2.0)
    # Recheck which are still alive
    alive: list[int] = []
    for pid in pids:
        check = subprocess.run(["kill", "-0", str(pid)], capture_output=True)
        if check.returncode == 0:
            alive.append(pid)
    if alive:
        subprocess.run(["kill", "-9", *(str(p) for p in alive)], capture_output=True)
        time.sleep(1.0)
        still: list[int] = []
        for pid in alive:
            check = subprocess.run(["kill", "-0", str(pid)], capture_output=True)
            if check.returncode == 0:
                still.append(pid)
        return still
    return []


def _service_stop_hint(cmd: str) -> str | None:
    """Best-guess command to stop a service supervisor that's respawning a process.

    Returns None for unrecognized commands so the caller can fall back to a
    generic `lsof` discovery hint.
    """
    cmd_l = cmd.lower()
    is_macos = platform.system() == "Darwin"
    if "postgres" in cmd_l:
        return (
            "brew services list | grep -i postgres   # then: brew services stop <name>"
            if is_macos
            else "sudo systemctl stop postgresql"
        )
    if "redis" in cmd_l:
        return "brew services stop redis" if is_macos else "sudo systemctl stop redis"
    if "mysql" in cmd_l or "mariadb" in cmd_l:
        return (
            "brew services stop mysql   # or: brew services stop mariadb"
            if is_macos
            else "sudo systemctl stop mysql"
        )
    if "mongod" in cmd_l:
        return "brew services stop mongodb-community" if is_macos else "sudo systemctl stop mongod"
    if "neo4j" in cmd_l:
        return "brew services stop neo4j" if is_macos else "sudo systemctl stop neo4j"
    return None


def _step(title: str, n: int, total: int = 8) -> None:
    console.print()
    console.print(Rule(f"[bold]Step {n}/{total} — {title}[/bold]", style="blue"))
    console.print()


def _ok(msg: str) -> None:
    console.print(f"  [green]✓[/green] {msg}")


def _warn(msg: str) -> None:
    console.print(f"  [yellow]⚠[/yellow] {msg}")


def _info(msg: str) -> None:
    console.print(f"  [dim]{msg}[/dim]")


# ── Final summary ───────────────────────────────────────────────────────────


def _print_final_summary(services_ok: dict[str, bool], agents: list[str]) -> None:
    console.print()
    console.print(Rule("[bold green]Quickstart Complete[/bold green]", style="green"))
    console.print()

    # Service table
    svc_table = Table(title="Running Services", show_header=True, header_style="bold")
    svc_table.add_column("Service")
    svc_table.add_column("URL")
    svc_table.add_column("Status")
    svc_table.add_column("Purpose")

    svc_rows = [
        (
            "Studio",
            DASHBOARD_URL,
            services_ok.get("studio", False),
            "Build agents visually, manage registry",
        ),
        (
            "API",
            "http://localhost:8000",
            services_ok.get("api", False),
            "REST API + OpenAPI docs (/docs)",
        ),
        (
            "LiteLLM",
            "http://localhost:4000",
            services_ok.get("litellm", False),
            "Model gateway (OpenAI-compat)",
        ),
        (
            "ChromaDB",
            f"{CHROMADB_BASE}/docs",
            services_ok.get("chromadb", False),
            "Vector store for RAG",
        ),
        (
            "Neo4j",
            "http://localhost:7474",
            services_ok.get("neo4j", False),
            "Graph DB browser (neo4j/agentbreeder)",
        ),
    ]
    for name, url, ok, purpose in svc_rows:
        status = "[green]● running[/green]" if ok else "[yellow]● starting[/yellow]"
        svc_table.add_row(name, f"[cyan]{url}[/cyan]", status, f"[dim]{purpose}[/dim]")

    console.print(svc_table)
    console.print()

    # Agents table
    agent_table = Table(title="Sample Agents", show_header=True, header_style="bold")
    agent_table.add_column("Agent")
    agent_table.add_column("Superpower")
    agent_table.add_column("Chat (CLI)")
    agent_table.add_column("UI")

    agent_rows = [
        (
            "assistant",
            "General conversation",
            "agentbreeder chat assistant --local",
            f"{DASHBOARD_URL}/chat/assistant",
        ),
        (
            "rag-agent",
            "Searches knowledge base (ChromaDB)",
            "agentbreeder chat rag-agent --local",
            f"{DASHBOARD_URL}/chat/rag-agent",
        ),
        (
            "graph-agent",
            "Queries knowledge graph (Neo4j)",
            "agentbreeder chat graph-agent --local",
            f"{DASHBOARD_URL}/chat/graph-agent",
        ),
        (
            "search-agent",
            "Web search + filesystem (MCP)",
            "agentbreeder chat search-agent --local",
            f"{DASHBOARD_URL}/chat/search-agent",
        ),
        (
            "a2a-orchestrator",
            "Routes to all above agents (A2A)",
            "agentbreeder chat a2a-orchestrator --local",
            f"{DASHBOARD_URL}/chat/a2a-orchestrator",
        ),
    ]
    for name, power, cli_cmd, ui_url in agent_rows:
        agent_table.add_row(
            f"[bold]{name}[/bold]",
            power,
            f"[cyan]{cli_cmd}[/cyan]",
            f"[dim]{ui_url}[/dim]",
        )

    console.print(agent_table)
    console.print()

    # What you can do next
    console.print(
        Panel(
            "[bold]Try these next:[/bold]\n\n"
            "  [bold cyan]agentbreeder chat assistant[/bold cyan]"
            "                 Chat with the assistant agent\n"
            "  [bold cyan]agentbreeder chat rag-agent[/bold cyan]"
            "                 Ask questions about AgentBreeder docs\n"
            "  [bold cyan]agentbreeder chat a2a-orchestrator[/bold cyan]"
            "         Let the orchestrator route your question\n"
            "  [bold cyan]agentbreeder list agents[/bold cyan]"
            "                   See all registered agents\n"
            "  [bold cyan]agentbreeder init[/bold cyan]"
            "                          Scaffold a new agent project\n"
            "  [bold cyan]agentbreeder deploy --target local[/bold cyan]"
            "         Deploy your agent to Docker\n"
            "  [bold cyan]agentbreeder deploy --target aws[/bold cyan]"
            "           Deploy to AWS ECS Fargate\n"
            "  [bold cyan]agentbreeder deploy --target gcp[/bold cyan]"
            "           Deploy to GCP Cloud Run\n"
            "  [bold cyan]agentbreeder deploy --target azure[/bold cyan]"
            "         Deploy to Azure Container Apps\n"
            "\n"
            f"  [bold]Studio:[/bold] [cyan]{DASHBOARD_URL}[/cyan]\n"
            f"  [dim]Build agents visually, manage providers, view audit logs, and more.[/dim]\n"
            "\n"
            "  [dim]Stop everything:[/dim]  [bold cyan]agentbreeder down[/bold cyan]",
            title="[bold green]You're all set![/bold green]",
            border_style="green",
            padding=(1, 3),
        )
    )
    console.print()


# ── Main command ────────────────────────────────────────────────────────────


def quickstart(
    cloud: str = typer.Option(
        None,
        "--cloud",
        "-c",
        help="Also deploy to cloud after local: aws | gcp | azure",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Don't open browser after startup",
    ),
    skip_seed: bool = typer.Option(
        False,
        "--skip-seed",
        help="Skip sample data seeding (faster restart)",
    ),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Build API/Studio images from source instead of using Docker Hub",
    ),
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Tear down all volumes and restart from scratch",
    ),
    no_ollama: bool = typer.Option(
        False,
        "--no-ollama",
        help=(
            "Cloud-only setup: skip the Ollama install, daemon start, and model pull "
            "(saves ~1–2 min and a ~3 GB download). Use this when you already have "
            "an OpenAI / Anthropic / Google / OpenRouter API key. Equivalent to "
            "choosing 'Cloud' at the interactive model-source prompt."
        ),
    ),
    ollama_model: str = typer.Option(
        DEFAULT_LOCAL_MODEL,
        "--ollama-model",
        help=f"Default local model to pull when none are installed (default: {DEFAULT_LOCAL_MODEL})",
    ),
    assume_yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help=(
            "Non-interactive mode for CI / scripted runs. Skip every yes/no prompt "
            "and use the safe default for each. Pair with --no-ollama and "
            "--no-browser for a fully scripted bootstrap. Provider keys must be "
            "supplied via env vars (OPENAI_API_KEY etc.) — --yes does not invent them."
        ),
    ),
    skip_doctor: bool = typer.Option(
        False,
        "--skip-doctor",
        help="Skip the prerequisite preflight (advanced; CI environments)",
    ),
) -> None:
    """Full local platform bootstrap — one command to go from zero to running agents.

    Sets up Docker/Podman, starts the full stack (Postgres, Redis, ChromaDB, Neo4j,
    MCP servers, API, Studio, LiteLLM), seeds sample data, deploys 5 sample agents,
    and opens Studio. Takes ~3 minutes on first run.

    All agents are usable via CLI and Studio.

    Examples:
        agentbreeder quickstart                                  # local only (interactive)
        agentbreeder quickstart --no-ollama                      # cloud-only (skip ~3 GB download)
        agentbreeder quickstart --yes --no-ollama --no-browser   # fully scripted (CI)
        agentbreeder quickstart --cloud aws                      # local + deploy to AWS
        agentbreeder quickstart --cloud gcp                      # local + deploy to GCP
        agentbreeder quickstart --skip-seed                      # restart without re-seeding
    """
    _set_assume_yes(assume_yes)
    if assume_yes:
        console.print(
            "  [dim]--yes: non-interactive mode. Yes/no prompts will use safe defaults; "
            "blocking prompts (e.g. 'press Enter once daemon is up') will exit instead "
            "of waiting.[/dim]"
        )
    total_steps = 9 if cloud else 8

    if reset:
        console.print(
            Panel(
                "[bold yellow]Resetting quickstart — all local data will be erased[/bold yellow]",
                border_style="yellow",
            )
        )
        # Detect a runtime so we can route `compose down` to docker OR podman.
        _reset_runtime = _detect_runtime()
        _reset_compose = (_reset_runtime[1] if _reset_runtime else "docker compose").split()
        subprocess.run(
            [
                *_reset_compose,
                "-f",
                str(QS_COMPOSE),
                "--project-name",
                "agentbreeder-qs",
                "down",
                "-v",
                "--remove-orphans",
            ],
            check=False,
            capture_output=True,
        )
        console.print("  [green]✓[/green] Volumes cleared — starting fresh")

    console.print()
    console.print(
        Panel(
            "[bold cyan]AgentBreeder Quickstart[/bold cyan]\n\n"
            "[dim]Full local AI agent platform in one command.\n"
            "Sets up: Docker stack · ChromaDB RAG · Neo4j GraphRAG · MCP servers ·\n"
            "         5 sample agents · Studio · CLI tools\n\n"
            "Estimated time: 3–5 minutes (longer on first run for image pulls)[/dim]",
            border_style="cyan",
            padding=(1, 3),
        )
    )

    # Prerequisite preflight — fail fast before any image pulls happen.
    if not skip_doctor:
        from cli.commands.doctor import has_blocker, render_report, run_all_checks

        doctor_results = run_all_checks()
        if has_blocker(doctor_results):
            render_report(doctor_results)
            console.print(
                "[dim]Re-run after fixing the issues above, or use "
                "[bold]--skip-doctor[/bold] to bypass (not recommended).[/dim]\n"
            )
            raise typer.Exit(code=1)

    # Light pyenv recommendation (does not block — agentbreeder runs on any 3.11+)
    try:
        from cli.main import _print_pyenv_warning as _qs_pyenv_warn

        _qs_pyenv_warn()
    except Exception:
        pass

    # ── Step 1: Container runtime ────────────────────────────────────────────
    _step("Container Runtime", 1, total_steps)

    runtime = _detect_runtime()
    if not runtime:
        # CLI missing but a daemon socket is reachable → partial install.
        # Also catches EACCES on /var/run/docker.sock when docker isn't on PATH.
        partial = _diagnose_missing_runtime()
        if partial:
            console.print(
                Panel(
                    "\n".join(partial),
                    title="Container runtime — partial install detected",
                    border_style="yellow",
                    padding=(1, 2),
                )
            )
            raise typer.Exit(code=1)
        console.print(
            Panel(
                "[yellow]No container runtime found.[/yellow]\n\n"
                + "\n".join(_install_instructions()),
                title="Install Docker or Podman",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        # Offer to run an auto-install for the user when we know how.
        # --yes intentionally does *not* trigger Docker install — it's a heavy
        # side effect (sudo, downloads, daemon spawn) we don't want in CI.
        install = _runtime_install_command()
        import sys as _sys

        if install and _sys.stdin.isatty() and not assume_yes:
            cmd_parts, human = install
            console.print()
            ans = (
                console.input(
                    f"  [bold]Install Docker for you now?[/bold] "
                    f"[dim](runs: [cyan]{human}[/cyan])[/dim] [Y/n]: "
                )
                .strip()
                .lower()
            )
            if ans not in ("n", "no", "skip"):
                console.print()
                r = subprocess.run(cmd_parts)
                if r.returncode == 0:
                    runtime = _detect_runtime()
                    if runtime:
                        _ok(f"Installed {runtime[0]}")
                else:
                    _warn("Auto-install failed — install manually and re-run quickstart")

        if not runtime:
            console.print(
                "\n  [dim]Re-run [bold cyan]agentbreeder quickstart[/bold cyan] "
                "once a runtime is installed.[/dim]"
            )
            raise typer.Exit(code=1)

    binary, compose_cmd = runtime
    host_sock = _docker_host_socket_path()
    if host_sock and binary == "docker":
        _ok(f"Found {binary} ({compose_cmd}) via $DOCKER_HOST → [cyan]{host_sock}[/cyan]")
    else:
        _ok(f"Found {binary} ({compose_cmd})")
        if binary == "docker":
            _info("Override the daemon target with [cyan]export DOCKER_HOST=unix://<path>[/cyan]")

    if not _runtime_is_running(binary):
        for line in _diagnose_runtime_failure(binary):
            console.print(f"  {line}" if line else "")
        console.print()
        if assume_yes:
            console.print(
                "  [red]--yes: cannot wait for a daemon to start. "
                "Start the runtime and re-run.[/red]"
            )
            raise typer.Exit(code=1)
        console.input(
            "  [bold]Press Enter once any container runtime is running "
            "(or Ctrl+C to cancel): [/bold]"
        )
        # Re-detect — user may have started a *different* runtime than what we
        # first picked (e.g. they had Docker installed but started Podman).
        rerun = _detect_runtime()
        if rerun and _runtime_is_running(rerun[0]):
            binary, compose_cmd = rerun
        elif not _runtime_is_running(binary):
            console.print(
                f"  [red]Still can't reach a container runtime. Exiting.[/red]\n"
                f"  [dim]Tried: {binary}. Install/start docker, podman, or nerdctl and re-run.[/dim]"
            )
            raise typer.Exit(code=1)

    _ok(f"{binary.capitalize()} daemon is running")
    if binary == "docker" and _docker_is_rootless():
        _info(
            "Rootless Docker detected — bind-mounted volumes will be owned by your user. "
            "If you see permission errors on mounts, that's why."
        )

    # ── Step 2: LLM providers ────────────────────────────────────────────────
    _step("LLM Providers", 2, total_steps)

    # Ask the user how they want to run models so we can short-circuit either
    # the Ollama install or the cloud-key prompts.
    skip_ollama, skip_cloud_keys = _ask_model_source(
        no_ollama_flag=no_ollama, assume_yes=assume_yes
    )

    # First: offer to install/start Ollama and pull a default local model so the
    # platform has a free, no-API-key inference backend out of the box.
    _ensure_ollama(skip=skip_ollama, default_model=ollama_model)

    # Read any existing .env from CWD first so we can show masked existing keys.
    _cwd_env: dict[str, str] = {}
    _cwd_env_path = Path.cwd() / ".env"
    if _cwd_env_path.exists():
        for _line in _cwd_env_path.read_text().splitlines():
            if "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                _cwd_env[_k.strip()] = _v.strip()

    if skip_cloud_keys:
        new_provider_keys: dict[str, str] = {}
        has_ollama = _ollama_running()
    else:
        new_provider_keys, has_ollama = _collect_provider_keys(_cwd_env, assume_yes=assume_yes)

    # Merge new keys into process env so the compose step picks them up
    os.environ.update(new_provider_keys)

    has_cloud_key = any(
        os.environ.get(k)
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY")
    )

    console.print()
    if has_ollama:
        _ok("Ollama ready — local models available")
    if has_cloud_key:
        active = [
            k
            for k in (
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GOOGLE_API_KEY",
                "OPENROUTER_API_KEY",
            )
            if os.environ.get(k)
        ]
        _ok(f"Cloud providers configured: {', '.join(active)}")
    if new_provider_keys:
        _ok(f"Keys saved: {', '.join(new_provider_keys)}")

    if not has_ollama and not has_cloud_key:
        console.print()
        console.print(
            Panel(
                "[yellow]No LLM provider configured.[/yellow]\n\n"
                "Agents need at least one of:\n"
                "  • [cyan]ollama serve[/cyan] + [cyan]ollama pull llama3.2[/cyan]  (free, local)\n"
                "  • An API key entered above\n\n"
                "Continue to set up the stack anyway?",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        if assume_yes:
            console.print(
                "  [dim]--yes: continuing without a provider — "
                "set OPENAI_API_KEY (or equivalent) in the environment before re-running.[/dim]"
            )
            proceed = "y"
        else:
            proceed = (
                console.input("  [bold]Continue without a provider? (y/N): [/bold]")
                .strip()
                .lower()
            )
        if proceed != "y":
            console.print(
                "  [dim]Re-run agentbreeder quickstart and enter an API key above.[/dim]"
            )
            raise typer.Exit(code=0)

    # ── Step 3: Compose environment ──────────────────────────────────────────
    _step("Environment", 3, total_steps)

    # Primary .env lives where the user ran the command from.
    env_path = Path.cwd() / ".env"
    # Docker compose reads from DEPLOY_DIR; keep a copy there too if different.
    deploy_env_path = DEPLOY_DIR.parent / ".env"

    compose_env: dict[str, str] = {}

    # Load existing .env (CWD wins, fall back to deploy dir)
    source_path = (
        env_path if env_path.exists() else (deploy_env_path if deploy_env_path.exists() else None)
    )
    if source_path:
        for line in source_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                compose_env[k.strip()] = v.strip()

    # Inject generated secrets if not already present
    compose_env.setdefault("SECRET_KEY", secrets.token_hex(32))
    compose_env.setdefault("JWT_SECRET_KEY", secrets.token_hex(32))
    compose_env.setdefault("LITELLM_MASTER_KEY", f"sk-agentbreeder-{secrets.token_hex(16)}")

    # Merge provider keys collected in Step 2
    compose_env.update(new_provider_keys)

    # Also pull any cloud keys already in the environment (e.g. exported in shell)
    for _env_key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        if os.environ.get(_env_key) and _env_key not in compose_env:
            compose_env[_env_key] = os.environ[_env_key]

    def _write_env_file(path: Path) -> None:
        lines = [f"{k}={v}" for k, v in compose_env.items()]
        path.write_text("\n".join(lines) + "\n")

    _write_env_file(env_path)
    if deploy_env_path.resolve() != env_path.resolve():
        _write_env_file(deploy_env_path)

    if source_path:
        _ok(f".env updated: {env_path}")
    else:
        _ok(f".env created: {env_path}")

    # Ensure MCP workspace dir exists
    MCP_WORKSPACE.mkdir(parents=True, exist_ok=True)
    _info(f"MCP workspace: {MCP_WORKSPACE}")

    # ── Step 4: Start the stack ──────────────────────────────────────────────
    _step("Starting Services", 4, total_steps)

    # Pre-flight: check for port conflicts before pulling images.
    # Skip if the ports are already owned by our own quickstart stack
    # (e.g., re-running after a partial failure that left containers up).
    qs_running = subprocess.run(
        [binary, "ps", "--filter", "name=agentbreeder-qs", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not qs_running:
        taken_ports = _check_ports()
        if taken_ports:
            # Gather owner info per port so we can offer to kill interactively.
            conflicts: list[tuple[int, str, list[tuple[int, str]]]] = []
            for port, name in taken_ports:
                conflicts.append((port, name, _port_owners(port)))

            conflict_table = Table(show_header=True, header_style="bold yellow")
            conflict_table.add_column("Port")
            conflict_table.add_column("Needed by")
            conflict_table.add_column("Currently held by")
            for port, name, owners in conflicts:
                if owners:
                    held = ", ".join(f"{c} [dim](pid {p})[/dim]" for p, c in owners)
                else:
                    held = "[dim]unknown — install lsof for details[/dim]"
                conflict_table.add_row(f"[bold]:{port}[/bold]", name, held)

            console.print()
            console.print(Rule("[bold yellow]Port Conflict[/bold yellow]", style="yellow"))
            console.print()
            console.print("  These ports are needed by quickstart but already in use:")
            console.print()
            console.print(conflict_table)
            console.print()

            all_pids = sorted({p for _, _, owners in conflicts for p, _ in owners})
            if all_pids:
                console.print("  [bold]Free these ports automatically and continue?[/bold]")
                console.print(
                    "  [dim]y = SIGTERM (then SIGKILL) the listed PIDs and proceed[/dim]"
                )
                console.print(
                    "  [dim]N = exit so you can stop them yourself "
                    "(safer if any of these are services you care about)[/dim]"
                )
                if assume_yes:
                    console.print("  [dim]--yes: auto-killing conflicting processes[/dim]")
                    ans = "y"
                else:
                    ans = console.input("  > ").strip().lower()
                if ans == "y":
                    still = _kill_port_owners(all_pids)
                    time.sleep(1.0)
                    new_taken = _check_ports()
                    if not still and not new_taken:
                        _ok("Ports freed — continuing")
                    elif still:
                        # SIGKILL didn't take — almost always a permission issue.
                        console.print(
                            f"\n  [red]Could not kill {len(still)} process(es).[/red] "
                            "They may need elevated permissions."
                        )
                        for port, name, owners in conflicts:
                            for pid, cmd in owners:
                                if pid in still:
                                    console.print(
                                        f"    [cyan]sudo kill -9 {pid}[/cyan]  "
                                        f"[dim]# {cmd} on :{port} ({name})[/dim]"
                                    )
                        raise typer.Exit(code=1)
                    else:
                        # Kill succeeded but ports are still in use — a service
                        # supervisor (brew services / launchd / systemd / Postgres.app)
                        # respawned the process under a new PID. Stop it at the source.
                        console.print(
                            "\n  [yellow]Killed the listed PIDs, but the ports are "
                            "still in use.[/yellow]"
                        )
                        console.print(
                            "  [dim]A service supervisor (brew services, launchd, "
                            "systemd, or Postgres.app) is restarting them. Stop it at "
                            "the source:[/dim]"
                        )
                        console.print()
                        seen_cmds: set[str] = set()
                        new_owner_lines: list[tuple[int, str, list[tuple[int, str]]]] = []
                        for port, name in new_taken:
                            new_owner_lines.append((port, name, _port_owners(port)))
                        for port, name, owners in new_owner_lines:
                            for _pid, cmd in owners:
                                if cmd in seen_cmds:
                                    continue
                                seen_cmds.add(cmd)
                                hint = _service_stop_hint(cmd)
                                if hint:
                                    console.print(
                                        f"    [cyan]{hint}[/cyan]  "
                                        f"[dim]# stops {cmd} (port {port})[/dim]"
                                    )
                                else:
                                    console.print(
                                        f"    [dim]# {cmd} on :{port} ({name}) — "
                                        f"find its supervisor:[/dim]\n"
                                        f"    [cyan]lsof -i :{port}[/cyan]"
                                    )
                        if not seen_cmds:
                            # No new owner detected (race) — generic hint.
                            console.print(
                                f"    [cyan]lsof -i :{new_taken[0][0]}[/cyan]  "
                                "[dim]# find what's holding the port[/dim]"
                            )
                        console.print()
                        console.print(
                            "  [dim]After stopping the service, re-run "
                            "[cyan]agentbreeder quickstart[/cyan].[/dim]"
                        )
                        raise typer.Exit(code=1)
                else:
                    console.print()
                    console.print("  [bold]To free the ports manually, run:[/bold]")
                    console.print()
                    for port, name, owners in conflicts:
                        if owners:
                            pids_str = " ".join(str(p) for p, _ in owners)
                            console.print(
                                f"    [cyan]kill -9 {pids_str}[/cyan]  "
                                f"[dim]# port {port} ({name})[/dim]"
                            )
                        else:
                            console.print(
                                f"    [cyan]lsof -ti :{port} | xargs kill[/cyan]  "
                                f"[dim]# port {port} ({name})[/dim]"
                            )
                    console.print()
                    console.print(
                        "  Or stop the AgentBreeder dev stack if it's running:\n"
                        f"  [cyan]{compose_cmd} -f deploy/docker-compose.yml down[/cyan]"
                    )
                    raise typer.Exit(code=1)
            else:
                # lsof unavailable or returned nothing — show manual fix
                console.print(
                    "  [dim]Install lsof for automatic detection, or stop the "
                    "conflicting services manually.[/dim]"
                )
                console.print()
                console.print("  [bold]Manual fix:[/bold]")
                for port, name in taken_ports:
                    console.print(
                        f"    [cyan]lsof -ti :{port} | xargs kill -9[/cyan]  [dim]# {name}[/dim]"
                    )
                console.print()
                console.print(
                    "  Or stop the AgentBreeder dev stack if it's running:\n"
                    f"  [cyan]{compose_cmd} -f deploy/docker-compose.yml down[/cyan]"
                )
                raise typer.Exit(code=1)

    if qs_running:
        # Stack already up — skip pull/up to avoid re-running migrate and wiping seed data
        _info("Stack already running — skipping pull/up")
        result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
            args=[], returncode=0
        )
    else:
        # Pre-flight: make sure the compose command can actually reach an engine.
        # Catches the common case where podman delegates to a Rancher Desktop
        # docker-compose shim whose socket is dead.
        ok, blob = _compose_preflight(compose_cmd, env=compose_env)
        if not ok:
            blob_l = blob.lower()
            rancher_hijack = (
                ".rd/docker.sock" in blob_l
                or "/users/" in blob_l
                and ".rd/" in blob_l
                and "docker-compose" in blob_l
            )
            cannot_connect = "cannot connect to the docker daemon" in blob_l

            # Auto-recovery #1: stale DOCKER_HOST. If the user's shell exports
            # DOCKER_HOST=unix:///path/that/no/longer/exists (e.g. a removed
            # Rancher socket), every docker-compose call obeys it and fails.
            # Strip it from the subprocess env (NOT the user's shell) and retry.
            stale_docker_host = os.environ.get("DOCKER_HOST", "")
            if (
                cannot_connect
                and stale_docker_host.startswith("unix://")
                and not Path(stale_docker_host[len("unix://") :]).exists()
            ):
                compose_env["DOCKER_HOST"] = ""  # subprocess sees empty → uses default
                _info(f"Ignoring stale DOCKER_HOST → {stale_docker_host} (socket does not exist)")
                ok, blob = _compose_preflight(compose_cmd, env=compose_env)
                if ok:
                    _ok("Compose now reaching the default engine socket")
                else:
                    blob_l = blob.lower()
                    rancher_hijack = (
                        ".rd/docker.sock" in blob_l
                        or "/users/" in blob_l
                        and ".rd/" in blob_l
                        and "docker-compose" in blob_l
                    )
                    cannot_connect = "cannot connect to the docker daemon" in blob_l

            # Auto-recovery #2: if podman is running and a Rancher (or any other)
            # docker-compose shim is hijacking the call, redirect it at podman's
            # socket via DOCKER_HOST and retry the pre-flight.
            if not ok and (rancher_hijack or cannot_connect) and binary == "podman":
                sock = _podman_socket()
                if sock and "DOCKER_HOST" not in compose_env:
                    compose_env["DOCKER_HOST"] = f"unix://{sock}"
                    _info("Detected docker-compose shim hijack — auto-routing to podman's socket")
                    ok, blob = _compose_preflight(compose_cmd, env=compose_env)
                    if ok:
                        _ok(f"Compose now reaching podman at {sock}")
                    else:
                        blob_l = blob.lower()
                        rancher_hijack = (
                            ".rd/docker.sock" in blob_l
                            or "/users/" in blob_l
                            and ".rd/" in blob_l
                            and "docker-compose" in blob_l
                        )
                        cannot_connect = "cannot connect to the docker daemon" in blob_l

        if not ok:
            console.print()
            if rancher_hijack:
                console.print(
                    Panel(
                        "[red]Compose is being hijacked by a docker-compose shim.[/red]\n\n"
                        f"[dim]{binary} compose[/dim] is delegating to "
                        "[bold]~/.rd/bin/docker-compose[/bold] (Rancher Desktop),\n"
                        "and even routing it at podman's socket didn't work.\n\n"
                        "[bold]Pick one to unblock:[/bold]\n"
                        "  • [cyan]open -a 'Rancher Desktop'[/cyan]   "
                        "[dim]# start Rancher; its docker socket comes back up[/dim]\n"
                        f"  • [cyan]brew install podman-compose[/cyan]   "
                        f"[dim]# native compose for {binary}, removes the shim entirely[/dim]\n"
                        "  • [cyan]rm ~/.rd/bin/docker-compose[/cyan]   "
                        "[dim]# remove the Rancher shim if you don't use Rancher[/dim]\n\n"
                        "Then re-run [cyan]agentbreeder quickstart[/cyan].",
                        title="[bold red]Compose Engine Not Reachable[/bold red]",
                        border_style="red",
                        padding=(1, 2),
                    )
                )
            elif cannot_connect:
                console.print(
                    Panel(
                        "[red]Compose can't reach the container daemon.[/red]\n\n"
                        f"[dim]{binary} compose version[/dim] failed with:\n"
                        f"[dim]{blob.strip().splitlines()[-1] if blob.strip() else 'unknown error'}[/dim]\n\n"
                        f"Start your container runtime and re-run "
                        f"[cyan]agentbreeder quickstart[/cyan].",
                        title="[bold red]Container Daemon Unreachable[/bold red]",
                        border_style="red",
                        padding=(1, 2),
                    )
                )
            else:
                console.print(
                    Panel(
                        "[red]Compose pre-flight failed.[/red]\n\n"
                        f"[dim]{blob.strip()[-500:] if blob.strip() else '(no output)'}[/dim]",
                        title="[bold red]Compose Pre-Flight Failed[/bold red]",
                        border_style="red",
                        padding=(1, 2),
                    )
                )
            raise typer.Exit(code=1)

        pull_args = ["pull", "--quiet"]
        console.print("  [dim]Pulling images (first run may take a few minutes)...[/dim]")
        _compose_run(compose_cmd, pull_args, env=compose_env, capture=True)

        up_args = ["up", "-d"]
        if dev:
            up_args.append("--build")
            _info("Building from source (--dev mode)")

        result = _compose_run(compose_cmd, up_args, env=compose_env)
    if result.returncode != 0:
        # Check for unhealthy containers to give a specific message
        unhealthy = subprocess.run(
            [
                binary,
                "ps",
                "-a",
                "--filter",
                "name=agentbreeder-qs",
                "--filter",
                "health=unhealthy",
                "--format",
                "{{.Names}}: {{.Status}}",
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()

        exited = subprocess.run(
            [
                binary,
                "ps",
                "-a",
                "--filter",
                "name=agentbreeder-qs",
                "--filter",
                "status=exited",
                "--format",
                "{{.Names}}: {{.Status}}",
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()

        detail_lines = []
        if unhealthy:
            for line in unhealthy.splitlines():
                detail_lines.append(f"  • [yellow]unhealthy[/yellow]  {line}")
        if exited:
            for line in exited.splitlines():
                detail_lines.append(f"  • [red]exited[/red]      {line}")

        detail = ("\n" + "\n".join(detail_lines) + "\n") if detail_lines else ""

        console.print(
            Panel(
                "[red]Failed to start services.[/red]" + detail + "\n"
                "Diagnose:\n"
                f"  [cyan]{compose_cmd} -f deploy/docker-compose.quickstart.yml "
                "--project-name agentbreeder-qs logs[/cyan]\n\n"
                "Common fixes:\n"
                f"  • [bold]Disk space:[/bold]  [cyan]{binary} system prune -f[/cyan]\n"
                "  • [bold]Port conflict:[/bold] stop any service using ports 5432/6379/8000/3001/8001/7474/7687\n"
                f"    Dev stack: [cyan]{compose_cmd} -f deploy/docker-compose.yml down[/cyan]",
                border_style="red",
                padding=(1, 2),
            )
        )
        raise typer.Exit(code=1)

    # ── Step 5: Wait for services ────────────────────────────────────────────
    _step("Waiting for Services", 5, total_steps)

    service_checks = [
        ("PostgreSQL", None, True),  # no HTTP, just timing
        ("Redis", None, True),
        ("ChromaDB", f"{CHROMADB_BASE}/api/v1/heartbeat", True),
        ("Neo4j", f"{NEO4J_HTTP}", True),
        ("API", "http://localhost:8000/health", True),
        ("Studio", DASHBOARD_URL, False),  # non-blocking
        ("LiteLLM", "http://localhost:4000/health", False),
    ]

    services_ok: dict[str, bool] = {}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        for svc_name, url, blocking in service_checks:
            task = progress.add_task(f"  {svc_name}...", total=None)
            if url:
                ok = _wait_http(url, timeout=120 if blocking else 30)
            else:
                time.sleep(5)  # give infra services time
                ok = True
            progress.update(task, completed=1, total=1)
            svc_key = svc_name.lower().replace(" ", "")
            services_ok[svc_key] = ok
            if ok:
                progress.console.print(f"  [green]✓[/green] {svc_name} ready")
            elif blocking:
                progress.console.print(f"  [yellow]⚠[/yellow] {svc_name} not ready — continuing")
            else:
                progress.console.print(f"  [dim]○[/dim] {svc_name} still starting")

    # ── Studio content smoke check ───────────────────────────────────────────
    # The Studio HTTP can respond 200 even when the published image ships a
    # broken Vite bundle (e.g. mismatched react/react-dom versions cause a
    # runtime crash → blank page). Validate the bundle and offer to rebuild
    # from source if anything is off and we have a build context available.
    if services_ok.get("studio"):
        smoke_ok, smoke_err = _dashboard_smoke_check(DASHBOARD_URL)
        if not smoke_ok:
            _warn(f"Studio smoke check failed — {smoke_err}")
            # If we're running from a source checkout (build context exists),
            # offer to rebuild the Studio image locally.
            dashboard_src = REPO_ROOT / "dashboard" / "package.json"
            if dashboard_src.exists():
                if _is_assume_yes():
                    console.print("  [dim]--yes: rebuilding Studio from source[/dim]")
                    ans = "y"
                else:
                    ans = (
                        console.input(
                            "  [bold]Rebuild Studio from source now?[/bold] "
                            "[dim](runs: compose up -d --build dashboard)[/dim] "
                            "[Y/n]: "
                        )
                        .strip()
                        .lower()
                    )
                if ans not in ("n", "no", "skip"):
                    console.print("  [dim]Rebuilding Studio image (~30s)...[/dim]")
                    rebuild = _compose_run(
                        compose_cmd,
                        ["up", "-d", "--build", "dashboard"],
                        env=compose_env,
                        capture=True,
                    )
                    if rebuild.returncode == 0 and _wait_http(DASHBOARD_URL, timeout=60):
                        retry_ok, retry_err = _dashboard_smoke_check(DASHBOARD_URL)
                        if retry_ok:
                            _ok("Studio rebuilt — bundle now valid")
                        else:
                            _warn(f"Studio still failing after rebuild: {retry_err}")
                    else:
                        _warn("Studio rebuild failed — check compose logs")
            else:
                _info(
                    "If Studio appears blank, pull a fresh image:\n"
                    f"    [cyan]{compose_cmd} -f deploy/docker-compose.quickstart.yml "
                    "pull dashboard && "
                    f"{compose_cmd} -f deploy/docker-compose.quickstart.yml "
                    "up -d dashboard[/cyan]"
                )

    # ── Step 6: Seed data ────────────────────────────────────────────────────
    _step("Seeding Sample Data", 6, total_steps)

    if skip_seed:
        _info("Skipping seed (--skip-seed)")
    else:
        # ChromaDB
        console.print("  [dim]Seeding ChromaDB knowledge base...[/dim]")
        if services_ok.get("chromadb"):
            chroma_result = _seed_chromadb()
            if chroma_result.get("ok"):
                _ok("ChromaDB seeded from deploy/seed/docs/ (collection: agentbreeder_knowledge)")
            elif chroma_result.get("code") == "chromadb-package-not-installed":
                _warn(
                    "ChromaDB seed skipped — chromadb python client not installed.\n"
                    "    Upgrade with: [bold cyan]pip install --upgrade agentbreeder[/bold cyan]\n"
                    "    Then re-run: [bold cyan]agentbreeder seed --chromadb[/bold cyan]"
                )
            else:
                _warn(
                    f"ChromaDB seed failed — {chroma_result.get('error', 'unknown error')}\n"
                    "    RAG agent may not have knowledge base data."
                )
        else:
            _warn("ChromaDB not ready — skipping vector seed")

        # Neo4j
        console.print("  [dim]Seeding Neo4j knowledge graph...[/dim]")
        if services_ok.get("neo4j"):
            # Neo4j HTTP responds before the transaction API is ready; give it more time.
            time.sleep(15)
            neo4j_ok = _seed_neo4j()
            if neo4j_ok:
                _ok("Neo4j seeded with agent/tool/provider knowledge graph")
            else:
                _warn("Neo4j seed failed — graph agent may return empty results")
        else:
            _warn("Neo4j not ready — skipping graph seed")

        # API resources
        if services_ok.get("api"):
            console.print("  [dim]Registering MCP servers...[/dim]")
            n_mcp = _register_mcp_servers()
            _ok(f"Registered {n_mcp} MCP server(s)")

            console.print("  [dim]Registering sample prompts...[/dim]")
            n_prompts = _register_prompts()
            _ok(f"Registered {n_prompts} sample prompt(s)")

    # ── Step 7: Register & deploy agents ─────────────────────────────────────
    _step("Deploying Sample Agents", 7, total_steps)

    if services_ok.get("api"):
        console.print("  [dim]Registering agents in the platform...[/dim]")
        registered = _register_agents()
        if registered:
            _ok(f"Registered {len(registered)} agent(s) in the registry")
        else:
            _warn("Agent registration via API failed — agents listed in examples/quickstart/")

        console.print("  [dim]Deploying agents locally...[/dim]")
        for yaml_path in sorted(EXAMPLES_QS.glob("*.yaml")):
            name = yaml_path.stem
            deploy_result = _api_post(
                "/api/v1/deploys",
                {"agent_yaml": yaml_path.read_text(), "target": "local", "dry_run": False},
            )
            if deploy_result:
                _ok(f"Deployed: {name}")
            else:
                _info(
                    f"Queued: {name}  (deploy: agentbreeder deploy {yaml_path.name} --target local)"
                )
    else:
        _warn("API not ready — deploy agents manually:")
        for yaml_path in sorted(EXAMPLES_QS.glob("*.yaml")):
            _info(f"  agentbreeder deploy {yaml_path} --target local")

    # ── Step 8: Cloud deployment (optional) ──────────────────────────────────
    if cloud:
        _step(f"Cloud Deployment → {cloud.upper()}", 8, total_steps)
        if not _check_cloud_prerequisites(cloud):
            console.print(
                Panel(
                    "[yellow]Cloud pre-flight checks failed — skipping cloud deploy.\n"
                    "Local deployment succeeded. Fix the issues above and re-run with [bold cyan]--cloud "
                    f"{cloud}[/bold cyan].[/yellow]",
                    border_style="yellow",
                )
            )
        else:
            _guide_cloud_deploy(cloud, assume_yes=assume_yes)

    # ── Final summary ─────────────────────────────────────────────────────────
    _print_final_summary(services_ok, [p.stem for p in sorted(EXAMPLES_QS.glob("*.yaml"))])

    # Open browser
    if not no_browser and services_ok.get("studio"):
        webbrowser.open(DASHBOARD_URL)
    elif not no_browser:
        webbrowser.open("http://localhost:8000/docs")  # fallback to API docs
