> Extracted from CLAUDE.md (thin-router refactor). Read on demand — see CLAUDE.md router for when.

## 🌐 API Conventions

```
# Registry
GET    /api/v1/agents                 # List agents (paginated, filterable)
GET    /api/v1/agents/{id}            # Get agent detail
POST   /api/v1/agents                 # Create/register agent
PUT    /api/v1/agents/{id}            # Update agent
DELETE /api/v1/agents/{id}            # Soft-delete (archive)
GET    /api/v1/registry/search        # Cross-entity registry search

# Deploy
POST   /api/v1/deploys               # Trigger a deployment
GET    /api/v1/deploys/{job_id}       # Poll deploy status

# Builders (visual agent/tool/prompt builders)
POST   /api/v1/builders/...          # Visual builder endpoints

# Providers
GET    /api/v1/providers              # List configured providers
POST   /api/v1/providers              # Add provider

# RAG & Memory
GET/POST /api/v1/rag/*                # RAG indexes, file ingestion, search
GET/POST /api/v1/memory/*             # Memory configs, conversation storage

# Git Workflow
GET/POST /api/v1/git/*                # Git operations, PR review workflow

# Governance
GET    /api/v1/teams                  # Team management
GET    /api/v1/costs                  # Cost data (filterable by team/agent/model)
GET    /api/v1/audit                  # Audit trail
GET    /api/v1/tracing                # Distributed tracing

# Tools
POST   /api/v1/tools/sandbox/execute  # Tool sandbox execution
GET    /api/v1/prompts/test           # Test prompt with model
GET    /api/v1/playground             # Chat playground
GET    /api/v1/evals                  # Agent evaluation

# Agent-to-Agent (A2A)
GET/POST /api/v1/a2a/*               # A2A protocol, agent cards, inter-agent calls

# Fleet Operations
GET    /api/v1/agentops               # Fleet dashboard, multi-agent monitoring

# Model Gateway
GET    /api/v1/gateway                # Gateway status, provider health, model proxy

# Marketplace
GET    /api/v1/marketplace            # Browse community templates + agents
POST   /api/v1/marketplace/publish    # Publish template to marketplace

# MCP Servers
GET/POST /api/v1/mcp_servers/*        # MCP server registry CRUD

# Templates
GET/POST /api/v1/templates/*          # Agent template management

# API v2 (versioned endpoints — see api/versioning.py)
GET    /api/v2/agents                 # v2 agents endpoint (enhanced filtering)
```

All responses follow:
```json
{
  "data": { ... },
  "meta": { "page": 1, "total": 42 },
  "errors": []
}
```

---
