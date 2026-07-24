> Extracted from CLAUDE.md (thin-router refactor). Read on demand — see CLAUDE.md router for when.

## 🔌 MCP Servers in Use

AgentBreeder uses MCP servers for development tooling. These are configured in `.mcp.json` at the repo root.

### Active MCP Servers

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/agentbreeder"],
      "description": "Read/write project files directly"
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "<token>" },
      "description": "Create issues, PRs, search code"
    },
    "postgres": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://agentbreeder:agentbreeder@localhost:5432/agentbreeder"],
      "description": "Query registry database directly during development"
    },
    "docker": {
      "command": "npx",
      "args": ["-y", "mcp-server-docker"],
      "description": "Manage local Docker containers and images"
    },
    "fetch": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-fetch"],
      "description": "Fetch external URLs (docs, APIs)"
    },
    "sequential-thinking": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
      "description": "Use for multi-step planning before implementing complex features"
    },
    "memory": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"],
      "description": "Persist context across sessions (architecture decisions, etc.)"
    },
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp"],
      "description": "E2E test the dashboard UI and CLI output"
    }
  }
}
```

### How to Use MCP in Development

When working on a feature, use MCPs in this order:
1. `sequential-thinking` — plan the implementation approach before coding
2. `filesystem` — read existing code before modifying
3. `postgres` — validate schema before writing migration
4. `github` — create issues or PRs after implementation
5. `playwright` — verify UI changes work end-to-end

---
