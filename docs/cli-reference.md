# CLI Reference

Agent Garden's CLI is the primary interface for managing agents. All commands support `--json` output for scripting and CI.

## Global Usage

```
garden [COMMAND] [OPTIONS]
```

---

## Commands

### `garden init`

Scaffold a new agent project with an interactive wizard.

```
garden init [OUTPUT_DIR] [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `OUTPUT_DIR` | No | Directory to create (defaults to agent name) |

**What it creates:**
- `agent.yaml` — configuration file
- `agent.py` — working example agent code
- `requirements.txt` — Python dependencies
- `.env.example` — environment variable template
- `README.md` — getting started guide

The wizard prompts for:
1. Agent name (slug-friendly, validated)
2. Framework (LangGraph, CrewAI, Claude SDK, OpenAI, ADK, Custom)
3. Cloud target (Local, AWS, GCP, Kubernetes)
4. Team name
5. Owner email (auto-detected from git config)

Automatically runs `garden validate` after scaffolding.

**Examples:**
```bash
garden init                    # Interactive wizard, uses agent name as dir
garden init my-agent           # Create in ./my-agent/
garden init --json             # JSON output (for CI)
```

---

### `garden deploy`

Deploy an agent from an `agent.yaml` configuration file.

```
garden deploy CONFIG_PATH [--target TARGET] [--json]
```

| Argument / Option | Required | Default | Description |
|-------------------|----------|---------|-------------|
| `CONFIG_PATH` | Yes | — | Path to `agent.yaml` |
| `--target`, `-t` | No | `local` | Deploy target: `local`, `kubernetes`, `aws`, `gcp` |

The deploy pipeline executes 8 atomic steps:
1. Parse & validate YAML
2. RBAC check
3. Dependency resolution
4. Container build
5. Infrastructure provision
6. Deploy & health check
7. Auto-register in registry
8. Return endpoint URL

If any step fails, the entire deploy rolls back.

**Examples:**
```bash
garden deploy ./agent.yaml                    # Deploy locally
garden deploy ./agent.yaml --target local     # Same as above
garden deploy ./agent.yaml -t kubernetes      # Deploy to K8s
garden deploy ./agent.yaml --json             # JSON output
```

---

### `garden validate`

Validate an `agent.yaml` without deploying.

```
garden validate CONFIG_PATH [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `CONFIG_PATH` | Yes | Path to `agent.yaml` |

Checks:
- YAML syntax
- JSON Schema validation (all required fields, correct types, valid enums)
- Semantic validation (name format, version format, email format)

**Examples:**
```bash
garden validate ./agent.yaml
garden validate ./agent.yaml --json
```

---

### `garden list`

List entities from the registry.

```
garden list [ENTITY_TYPE] [--team TEAM] [--json]
```

| Argument / Option | Required | Default | Description |
|-------------------|----------|---------|-------------|
| `ENTITY_TYPE` | No | `agents` | One of: `agents`, `tools`, `models`, `prompts` |
| `--team` | No | — | Filter by team name |

**Examples:**
```bash
garden list                           # List all agents
garden list agents                    # Same as above
garden list tools                     # List MCP servers and tools
garden list models                    # List registered models
garden list prompts                   # List prompt templates
garden list agents --team platform    # Filter by team
garden list --json                    # JSON output
```

---

### `garden describe`

Show full details for a registered agent.

```
garden describe NAME [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `NAME` | Yes | Agent name |

Shows: name, version, team, owner, framework, model config, tools, deploy target, status, endpoint URL, and metadata.

**Examples:**
```bash
garden describe my-agent
garden describe my-agent --json
```

---

### `garden search`

Search across all registered agents, tools, models, and prompts.

```
garden search QUERY [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `QUERY` | Yes | Search query (keyword match) |

Searches names, descriptions, tags, and teams across all entity types.

**Examples:**
```bash
garden search "customer support"
garden search zendesk
garden search --json "langgraph"
```

---

### `garden scan`

Scan for MCP servers and LiteLLM models, register discoveries in the registry.

```
garden scan [--json]
```

Discovers:
- Local MCP servers (reads schemas via MCP protocol)
- LiteLLM models (connects to LiteLLM gateway)

Automatically registers discovered tools and models in the registry.

**Examples:**
```bash
garden scan
garden scan --json
```

---

### `garden logs`

Show logs from a deployed agent.

```
garden logs AGENT_NAME [--lines N] [--follow] [--since DURATION] [--json]
```

| Argument / Option | Required | Default | Description |
|-------------------|----------|---------|-------------|
| `AGENT_NAME` | Yes | — | Name of the deployed agent |
| `--lines`, `-n` | No | `50` | Number of recent lines to show |
| `--follow`, `-f` | No | `false` | Stream logs in real time |
| `--since` | No | — | Show logs since duration (e.g., `5m`, `1h`, `2d`) |

**Examples:**
```bash
garden logs my-agent                  # Last 50 lines
garden logs my-agent -n 100           # Last 100 lines
garden logs my-agent --follow         # Stream logs
garden logs my-agent --since 5m       # Logs from last 5 minutes
garden logs my-agent -f --since 1h    # Stream from last hour
```

---

### `garden status`

Show deploy status of agents.

```
garden status [AGENT_NAME] [--json]
```

| Argument | Required | Description |
|----------|----------|-------------|
| `AGENT_NAME` | No | Agent name (omit for all agents summary) |

Without an agent name, shows a summary table of all deployed agents with their status. With an agent name, shows detailed status including deploy pipeline progress.

**Examples:**
```bash
garden status                  # All agents summary
garden status my-agent         # Detailed status for one agent
garden status --json           # JSON output
```

---

### `garden teardown`

Remove a deployed agent and clean up its resources.

```
garden teardown AGENT_NAME [--force] [--json]
```

| Argument / Option | Required | Default | Description |
|-------------------|----------|---------|-------------|
| `AGENT_NAME` | Yes | — | Name of the agent to remove |
| `--force`, `-f` | No | `false` | Skip confirmation prompt |

Removes the agent's containers, infrastructure, and registry entry. Prompts for confirmation unless `--force` is used.

**Examples:**
```bash
garden teardown my-agent              # Prompts for confirmation
garden teardown my-agent --force      # No confirmation
garden teardown my-agent --json       # JSON output
```

---

### `garden provider`

Manage LLM provider connections and API keys.

```
garden provider [SUBCOMMAND] [OPTIONS]
```

#### Subcommands

| Subcommand | Description |
|------------|-------------|
| `list` | List all configured providers with status |
| `add TYPE` | Add a new provider (interactive or with `--api-key`) |
| `test NAME` | Test a provider connection |
| `models NAME` | List available models from a provider |
| `remove NAME` | Remove a provider and its API key |
| `disable NAME` | Disable a provider without removing |
| `enable NAME` | Re-enable a disabled provider |

**Supported provider types:** `openai`, `anthropic`, `google`, `ollama`, `litellm`, `openrouter`

API keys are stored in the project `.env` file (or `~/.garden/.env` if no project).

**Examples:**
```bash
garden provider list                              # List all providers
garden provider add openai                        # Interactive setup
garden provider add openai --api-key sk-proj-...  # Non-interactive
garden provider add ollama                        # Auto-detect local Ollama
garden provider test openai                       # Verify connection + latency
garden provider models openai                     # List available models
garden provider disable openai                    # Disable without removing
garden provider enable openai                     # Re-enable
garden provider remove openai                     # Remove provider + key
garden provider list --json                       # JSON output
```

---

## Global Options

All commands support:

| Option | Description |
|--------|-------------|
| `--json` | Output as JSON (for CI/scripting) |
| `--help` | Show help with examples |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | General error |
| `2` | Invalid arguments |
