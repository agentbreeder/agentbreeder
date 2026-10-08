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

# Deploy (deploys run in-process via `agentbreeder deploy`; no deploy-job API)
GET    /api/v1/deployments/cloud-requirements/{cloud}  # Required cloud fields
POST   /api/v1/deployments/validate-infra              # Read-only infra check

# Builders
POST   /api/v1/builders/...          # YAML import/export, recommend, chat builder
GET/POST /api/v1/builder/sessions/*   # Chat builder sessions (produce agent.yaml)

# Providers
GET    /api/v1/providers              # List configured providers
POST   /api/v1/providers              # Add provider

# Memory
GET/POST /api/v1/memory/*             # Memory configs, conversation storage

# Governance
GET    /api/v1/teams                  # Team management
GET/POST /api/v1/secrets/*            # Secrets backends
GET/POST /api/v1/approvals/*          # HITL approval queue for agent tool calls
# Audit events are emitted as structured `audit_event` log lines (no audit API)

# Tools
POST   /api/v1/tools/sandbox/execute  # Tool sandbox execution
GET    /api/v1/playground             # Chat playground

# Agent-to-Agent (A2A)
GET/POST /api/v1/a2a/agents           # A2A agent registry
POST   /api/v1/a2a/invoke             # Invoke a registered agent by name

# Fleet Operations
GET    /api/v1/agentops               # Fleet health, incidents, compliance scans

# Model Gateway
GET    /api/v1/gateway                # LiteLLM status, models, providers, spend, logs

# Marketplace
GET    /api/v1/marketplace            # Browse community templates + agents
POST   /api/v1/marketplace/publish    # Publish template to marketplace

# MCP Servers
GET/POST /api/v1/mcp-servers/*        # MCP server registry CRUD

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
