"""agentbreeder quickstart — full local platform bootstrap in one command.

What it does:
  1. Detect / guide install of Docker or Podman + Compose
  2. Start the full stack (Postgres, Redis, ChromaDB, Neo4j, MCP servers,
     API, Dashboard, LiteLLM)
  3. Check LLM provider availability (Ollama or cloud keys)
  4. Seed ChromaDB with sample documents (RAG ready)
  5. Seed Neo4j with a knowledge graph (GraphRAG ready)
  6. Register sample MCP servers, prompts, and tools
  7. Deploy 5 sample agents (RAG, Graph, Search, A2A, Assistant)
  8. Open the dashboard and show how to use everything

Usage:
    agentbreeder quickstart
    agentbreeder quickstart --cloud aws     # also deploy to AWS after local
    agentbreeder quickstart --no-browser    # headless
    agentbreeder quickstart --skip-seed     # don't seed sample data
"""

from __future__ import annotations

import os
import platform
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
    """
    # Docker
    if shutil.which("docker"):
        result = subprocess.run(["docker", "compose", "version"], capture_output=True, text=True)
        if result.returncode == 0:
            return ("docker", "docker compose")
        # Docker without compose plugin
        if shutil.which("docker-compose"):
            return ("docker", "docker-compose")

    # Podman
    if shutil.which("podman"):
        result = subprocess.run(["podman", "compose", "version"], capture_output=True, text=True)
        if result.returncode == 0:
            return ("podman", "podman compose")
        if shutil.which("podman-compose"):
            return ("podman", "podman-compose")

    return None


def _runtime_is_running(binary: str) -> bool:
    result = subprocess.run([binary, "info"], capture_output=True, text=True)
    return result.returncode == 0


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


def _seed_chromadb() -> bool:
    """Seed ChromaDB via deploy/seed/seed.py. Returns True on success."""
    mod = _load_seed_module()
    if mod is None:
        return False
    result = mod.seed_chromadb()
    return result.get("ok", False)


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


def _guide_cloud_deploy(target: str) -> None:
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
    3001: "Dashboard",
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
            "Dashboard",
            DASHBOARD_URL,
            services_ok.get("dashboard", False),
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
            f"  [bold]Dashboard:[/bold] [cyan]{DASHBOARD_URL}[/cyan]\n"
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
        help="Build API/Dashboard images from source instead of using Docker Hub",
    ),
) -> None:
    """Full local platform bootstrap — one command to go from zero to running agents.

    Sets up Docker/Podman, starts the full stack (Postgres, Redis, ChromaDB, Neo4j,
    MCP servers, API, Dashboard, LiteLLM), seeds sample data, deploys 5 sample agents,
    and opens the dashboard. Takes ~3 minutes on first run.

    All agents are usable via CLI and the visual dashboard.

    Examples:
        agentbreeder quickstart                     # local only
        agentbreeder quickstart --cloud aws         # local + deploy to AWS
        agentbreeder quickstart --cloud gcp         # local + deploy to GCP
        agentbreeder quickstart --skip-seed         # restart without re-seeding
    """
    total_steps = 9 if cloud else 8

    console.print()
    console.print(
        Panel(
            "[bold cyan]AgentBreeder Quickstart[/bold cyan]\n\n"
            "[dim]Full local AI agent platform in one command.\n"
            "Sets up: Docker stack · ChromaDB RAG · Neo4j GraphRAG · MCP servers ·\n"
            "         5 sample agents · Visual dashboard · CLI tools\n\n"
            "Estimated time: 3–5 minutes (longer on first run for image pulls)[/dim]",
            border_style="cyan",
            padding=(1, 3),
        )
    )

    # ── Step 1: Container runtime ────────────────────────────────────────────
    _step("Container Runtime", 1, total_steps)

    runtime = _detect_runtime()
    if not runtime:
        console.print(
            Panel(
                "[yellow]No container runtime found.[/yellow]\n\n"
                + "\n".join(_install_instructions())
                + "\n\n[dim]Re-run [bold cyan]agentbreeder quickstart[/bold cyan] after installing.[/dim]",
                title="Install Docker or Podman",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        raise typer.Exit(code=1)

    binary, compose_cmd = runtime
    _ok(f"Found {binary} ({compose_cmd})")

    if not _runtime_is_running(binary):
        console.print(f"\n  [red]{binary} daemon is not running.[/red]")
        if binary == "docker":
            system = platform.system()
            if system == "Darwin":
                console.print("  [dim]Open Docker Desktop from your Applications folder.[/dim]")
            elif system == "Linux":
                console.print("  [dim]Run: sudo systemctl start docker[/dim]")
            else:
                console.print("  [dim]Start Docker Desktop from the system tray.[/dim]")
        else:
            console.print("  [dim]Run: podman machine start[/dim]")
        console.print()
        console.input(
            "  [bold]Press Enter once the daemon is running (or Ctrl+C to cancel): [/bold]"
        )
        if not _runtime_is_running(binary):
            console.print(f"  [red]Still can't reach {binary}. Exiting.[/red]")
            raise typer.Exit(code=1)

    _ok(f"{binary.capitalize()} daemon is running")

    # ── Step 2: LLM providers check ──────────────────────────────────────────
    _step("LLM Providers", 2, total_steps)

    has_ollama = False
    has_cloud_key = any(
        os.environ.get(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY")
    )

    try:
        resp = httpx.get("http://localhost:11434/", timeout=3.0)
        has_ollama = resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    if has_ollama:
        _ok("Ollama is running — local models will be used as primary")
    else:
        _warn("Ollama not running at http://localhost:11434")
        _info("Agents will use cloud providers if API keys are set")
        _info("For free local inference: ollama serve  (then: ollama pull llama3.2)")

    if has_cloud_key:
        keys_found = [
            k
            for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY")
            if os.environ.get(k)
        ]
        _ok(f"Cloud keys found: {', '.join(keys_found)}")
    else:
        _warn("No cloud API keys in environment")
        _info("Set them in .env or run: agentbreeder setup --providers-only")

    if not has_ollama and not has_cloud_key:
        console.print()
        console.print(
            Panel(
                "[yellow]No LLM provider available.[/yellow]\n\n"
                "Agents need at least one of:\n"
                "  • [cyan]ollama serve[/cyan] + [cyan]ollama pull llama3.2[/cyan]  (free, local)\n"
                "  • [cyan]OPENAI_API_KEY[/cyan] / [cyan]ANTHROPIC_API_KEY[/cyan] in .env\n\n"
                "Continue to set up the stack anyway?",
                border_style="yellow",
                padding=(1, 2),
            )
        )
        proceed = (
            console.input("  [bold]Continue without a provider? (y/N): [/bold]").strip().lower()
        )
        if proceed != "y":
            console.print("  [dim]Run: agentbreeder setup  — then come back[/dim]")
            raise typer.Exit(code=0)

    # ── Step 3: Compose environment ──────────────────────────────────────────
    _step("Environment", 3, total_steps)

    env_path = DEPLOY_DIR.parent / ".env"
    compose_env: dict[str, str] = {}

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                compose_env[k.strip()] = v.strip()
        _ok(f"Using existing .env: {env_path}")
    else:
        # Generate minimal .env
        compose_env.update(
            {
                "SECRET_KEY": secrets.token_hex(32),
                "JWT_SECRET_KEY": secrets.token_hex(32),
                "LITELLM_MASTER_KEY": f"sk-agentbreeder-{secrets.token_hex(16)}",
            }
        )
        lines = [f"{k}={v}" for k, v in compose_env.items()]
        env_path.write_text("\n".join(lines) + "\n")
        _ok(f"Created .env at {env_path}")

    # Ensure MCP workspace dir exists
    MCP_WORKSPACE.mkdir(parents=True, exist_ok=True)
    _info(f"MCP workspace: {MCP_WORKSPACE}")

    # ── Step 4: Start the stack ──────────────────────────────────────────────
    _step("Starting Services", 4, total_steps)

    # Pre-flight: check for port conflicts before pulling images.
    # Skip if the ports are already owned by our own quickstart stack
    # (e.g., re-running after a partial failure that left containers up).
    qs_running = subprocess.run(
        ["docker", "ps", "--filter", "name=agentbreeder-qs", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not qs_running:
        taken_ports = _check_ports()
        if taken_ports:
            conflict_lines = "\n".join(
                f"  • [bold]:{port}[/bold]  {name}  [dim](already in use)[/dim]"
                for port, name in taken_ports
            )
            console.print(
                Panel(
                    "[yellow]Port conflicts detected.[/yellow]\n\n"
                    "These ports are already in use on your machine:\n\n"
                    + conflict_lines
                    + "\n\n"
                    "[bold]To fix:[/bold]\n"
                    "  Stop the conflicting services, then re-run [cyan]agentbreeder quickstart[/cyan].\n\n"
                    "  If you have the [bold]agentbreeder dev stack[/bold] running:\n"
                    "  [cyan]docker compose -f deploy/docker-compose.yml down[/cyan]\n\n"
                    "  To find and stop any process on a port:\n"
                    "  [cyan]lsof -ti :<port> | xargs kill -9[/cyan]",
                    title="[bold yellow]Port Conflict[/bold yellow]",
                    border_style="yellow",
                    padding=(1, 2),
                )
            )
            raise typer.Exit(code=1)

    if qs_running:
        # Stack already up — skip pull/up to avoid re-running migrate and wiping seed data
        _info("Stack already running — skipping pull/up")
        result = subprocess.CompletedProcess(args=[], returncode=0)
    else:
        pull_args = ["pull", "--quiet"]
        console.print("  [dim]Pulling images (first run may take a few minutes)...[/dim]")
        _compose_run(compose_cmd, pull_args, env=compose_env, capture=True)

        up_args = ["up", "-d"]
        if dev:
            up_args.append("--build")
            _info("Building from source (--dev mode)")

        result = _compose_run(compose_cmd, up_args, env=compose_env)
    if result.returncode != 0:
        # Identify which containers from OUR stack are running (not external conflicts)
        qs_containers = subprocess.run(
            ["docker", "ps", "--filter", "name=agentbreeder-qs", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Check for unhealthy containers to give a specific message
        unhealthy = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=agentbreeder-qs",
             "--filter", "health=unhealthy", "--format", "{{.Names}}: {{.Status}}"],
            capture_output=True,
            text=True,
        ).stdout.strip()

        exited = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=agentbreeder-qs",
             "--filter", "status=exited", "--format", "{{.Names}}: {{.Status}}"],
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
                "[red]Failed to start services.[/red]"
                + detail
                + "\n"
                "Diagnose:\n"
                "  [cyan]docker compose -f deploy/docker-compose.quickstart.yml --project-name agentbreeder-qs logs[/cyan]\n\n"
                "Common fixes:\n"
                "  • [bold]Disk space:[/bold]  [cyan]docker system prune -f[/cyan]\n"
                "  • [bold]Port conflict:[/bold] stop any service using ports 5432/6379/8000/3001/8001/7474/7687\n"
                "    Dev stack: [cyan]docker compose -f deploy/docker-compose.yml down[/cyan]",
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
        ("Dashboard", DASHBOARD_URL, False),  # non-blocking
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

    # ── Step 6: Seed data ────────────────────────────────────────────────────
    _step("Seeding Sample Data", 6, total_steps)

    if skip_seed:
        _info("Skipping seed (--skip-seed)")
    else:
        # ChromaDB
        console.print("  [dim]Seeding ChromaDB knowledge base...[/dim]")
        if services_ok.get("chromadb"):
            chroma_ok = _seed_chromadb()
            if chroma_ok:
                _ok("ChromaDB seeded from deploy/seed/docs/ (collection: agentbreeder_knowledge)")
            else:
                _warn("ChromaDB seed failed — RAG agent may not have knowledge base data")
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
        _guide_cloud_deploy(cloud)

    # ── Final summary ─────────────────────────────────────────────────────────
    _print_final_summary(services_ok, [p.stem for p in sorted(EXAMPLES_QS.glob("*.yaml"))])

    # Open browser
    if not no_browser and services_ok.get("dashboard"):
        webbrowser.open(DASHBOARD_URL)
    elif not no_browser:
        webbrowser.open("http://localhost:8000/docs")  # fallback to API docs
