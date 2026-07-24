> Extracted from CLAUDE.md (thin-router refactor). Read on demand — see CLAUDE.md router for when.

## 📁 Project Structure

```
agentbreeder/
├── api/                        # FastAPI backend server
│   ├── main.py                 # App entry, middleware, routers
│   ├── auth.py                 # Auth dependencies
│   ├── config.py               # Settings (pydantic-settings)
│   ├── database.py             # Async SQLAlchemy setup
│   ├── versioning.py           # API versioning middleware + deprecation headers
│   ├── middleware/              # RBAC middleware
│   ├── routes/                 # REST endpoints
│   │   ├── agents.py           # Agent CRUD
│   │   ├── deploys.py          # Deploy from dashboard
│   │   ├── prompts.py          # Prompts + test panel
│   │   ├── providers.py        # Provider config
│   │   ├── rag.py              # RAG indexes, search
│   │   ├── memory.py           # Memory configs
│   │   ├── git.py              # Git workflow + PR review
│   │   ├── sandbox.py          # Tool sandbox execution
│   │   ├── teams.py            # Team management
│   │   ├── costs.py            # Cost tracking
│   │   ├── audit.py            # Audit log
│   │   ├── tracing.py          # Distributed tracing
│   │   ├── builders.py         # Visual builder endpoints
│   │   ├── orchestrations.py   # Orchestration management
│   │   ├── evals.py            # Agent evaluation
│   │   ├── playground.py       # Chat playground
│   │   ├── registry.py         # Cross-entity registry search
│   │   ├── a2a.py              # Agent-to-agent (A2A) communication endpoints
│   │   ├── agentops.py         # Fleet operations dashboard endpoints
│   │   ├── gateway.py          # Model gateway status + proxy endpoints
│   │   ├── marketplace.py      # Community marketplace browsing + publishing
│   │   ├── mcp_servers.py      # MCP server registry endpoints
│   │   ├── templates.py        # Agent template endpoints
│   │   └── v2/
│   │       └── agents.py       # API v2 agents endpoints
│   ├── services/               # Business logic layer
│   ├── models/                 # SQLAlchemy DB models + Pydantic schemas
│   └── tasks/                  # Background tasks (provider health)
├── cli/                        # CLI tool (built with Typer)
│   ├── main.py                 # Command registration
│   └── commands/
│       ├── init_cmd.py         # agentbreeder init
│       ├── deploy.py           # agentbreeder deploy (the core command)
│       ├── validate.py         # agentbreeder validate
│       ├── search.py           # agentbreeder search
│       ├── list_cmd.py         # agentbreeder list
│       ├── describe.py         # agentbreeder describe
│       ├── scan.py             # agentbreeder scan (MCP/LiteLLM/Ollama/OpenRouter discovery)
│       ├── schedule.py         # agentbreeder schedule (cron-based agent runs)
│       ├── logs.py             # agentbreeder logs
│       ├── status.py           # agentbreeder status
│       ├── teardown.py         # agentbreeder teardown
│       ├── submit.py           # agentbreeder submit (create PR)
│       ├── review.py           # agentbreeder review (PR review)
│       ├── publish.py          # agentbreeder publish (merge PR)
│       ├── chat.py             # agentbreeder chat
│       ├── eval.py             # agentbreeder eval
│       ├── eject.py            # agentbreeder eject (tier mobility)
│       ├── orchestration.py    # agentbreeder orchestration
│       ├── provider.py         # agentbreeder provider (subcommand)
│       ├── secret.py           # agentbreeder secret (manage secrets across backends)
│       └── template.py         # agentbreeder template (manage agent templates)
├── sdk/
│   └── python/                 # pip install agentbreeder-sdk
│       └── agenthub/           # SDK package (agent, deploy, model, tool, memory, mcp)
├── engine/                     # Core deployment pipeline
│   ├── config_parser.py        # YAML parsing + JSON Schema validation
│   ├── resolver.py             # Dependency resolution from registry
│   ├── builder.py              # Container image builder (per framework)
│   ├── governance.py           # RBAC validation at deploy time
│   ├── orchestrator.py         # Multi-agent orchestration engine
│   ├── orchestration_parser.py # Orchestration YAML parser
│   ├── providers/              # LLM provider abstraction
│   │   ├── base.py             # Provider interface
│   │   ├── openai_provider.py  # OpenAI provider
│   │   ├── anthropic_provider.py # Anthropic (Claude) provider
│   │   ├── google_provider.py  # Google (Gemini) provider
│   │   ├── ollama_provider.py  # Ollama (local) provider
│   │   ├── registry.py         # Provider registry + fallback chains
│   │   └── models.py           # Provider data models
│   ├── deployers/
│   │   ├── base.py                  # Abstract deployer interface
│   │   ├── docker_compose.py        # Local Docker Compose deployer
│   │   ├── gcp_cloudrun.py          # GCP Cloud Run deployer
│   │   ├── aws_ecs.py               # AWS ECS Fargate deployer
│   │   ├── aws_app_runner.py        # AWS App Runner deployer
│   │   ├── azure_container_apps.py  # Azure Container Apps deployer
│   │   ├── kubernetes.py            # Kubernetes deployer (EKS/GKE/AKS/self-hosted)
│   │   ├── claude_managed.py        # Claude Managed Agents deployer
│   │   ├── identity.py              # Per-agent cloud IAM identity provisioner
│   │   └── mcp_sidecar.py           # MCP sidecar container injection
│   ├── runtimes/               # Framework-specific container builders
│   │   ├── base.py             # Runtime builder interface
│   │   ├── langgraph.py        # LangGraph runtime
│   │   ├── openai_agents.py    # OpenAI Agents runtime
│   │   ├── crewai.py           # CrewAI runtime
│   │   ├── claude_sdk.py       # Claude SDK (Anthropic) runtime
│   │   ├── google_adk.py       # Google ADK runtime
│   │   ├── custom.py           # Custom (bring your own) runtime
│   │   └── templates/          # Server templates per runtime
│   ├── secrets/                # Pluggable secrets backend system
│   │   ├── base.py             # Secrets backend interface
│   │   ├── env_backend.py      # .env / environment variable backend
│   │   ├── aws_backend.py      # AWS Secrets Manager backend
│   │   ├── gcp_backend.py      # GCP Secret Manager backend
│   │   └── vault_backend.py    # HashiCorp Vault backend
│   ├── a2a/                    # Agent-to-agent (A2A) communication protocol
│   │   ├── protocol.py         # JSON-RPC A2A protocol implementation
│   │   ├── client.py           # A2A client for calling remote agents
│   │   ├── server.py           # A2A server for exposing agents
│   │   └── auth.py             # A2A authentication + agent cards
│   ├── mcp/                    # MCP packaging utilities
│   │   └── packager.py         # Package MCP servers for deployment
│   └── schema/                 # JSON Schemas
│       ├── agent.schema.json
│       ├── orchestration.schema.json
│       ├── prompt.schema.json
│       ├── tool.schema.json
│       ├── rag.schema.json
│       ├── memory.schema.json
│       └── template.schema.json
├── connectors/                 # Integration plugins (pluggable)
│   ├── base.py
│   ├── litellm/                # LiteLLM gateway connector
│   ├── mcp_scanner/            # MCP server scanner
│   ├── openrouter/             # OpenRouter model gateway connector
│   ├── email/
│   │   └── smtp.py             # SMTP email connector (send/send_async)
│   └── news/
│       ├── base.py             # NewsItem dataclass
│       ├── hackernews.py       # HackerNews via Algolia API
│       ├── arxiv.py            # ArXiv Atom feed connector
│       └── rss.py              # Generic RSS/Atom via feedparser (optional)
├── registry/                   # Catalog service
│   ├── agents.py
│   ├── prompts.py
│   ├── tools.py
│   ├── models.py
│   ├── providers.py
│   ├── deploys.py
│   ├── mcp_servers.py
│   ├── a2a_agents.py           # A2A-enabled agent registry
│   └── templates.py            # Agent template registry
├── dashboard/                  # React + TypeScript web UI
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── hooks/
│   │   └── lib/
│   └── package.json
├── sidecar/                    # Track J — Go cross-cutting-concerns sidecar
│   ├── cmd/sidecar/            # main entrypoint
│   ├── internal/
│   │   ├── auth/               # bearer-token middleware
│   │   ├── guardrails/         # PII / content filter rule engine
│   │   ├── a2a/                # JSON-RPC 2.0 A2A client
│   │   ├── mcp/                # MCP HTTP/SSE passthrough
│   │   ├── otelx/              # OTLP/HTTP span exporter
│   │   ├── cost/               # /api/v1/costs + /api/v1/audit emitter
│   │   ├── proxy/              # reverse-proxy w/ guardrail egress
│   │   ├── server/             # chi router assembly
│   │   └── config/             # env + YAML loader
│   ├── Dockerfile              # multi-arch distroless image
│   └── README.md
├── deploy/
│   └── docker-compose.yml      # Local development
├── alembic/                    # Database migrations
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── examples/
    ├── langgraph-agent/
    ├── openai-agents-agent/
    ├── mcp-server/
    ├── orchestration/          # Multi-agent orchestration examples
    ├── sdk-basic/
    └── sdk-advanced/
```

---
