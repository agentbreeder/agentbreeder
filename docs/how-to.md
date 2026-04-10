# How-To Guide

Practical recipes for common AgentBreeder workflows. Each section is self-contained — jump to what you need.

---

## Install AgentBreeder

### Option 1: PyPI (recommended)

```bash
# Full CLI + API server + engine
pip install agentbreeder

# Verify
agentbreeder --help
```

### Option 2: Homebrew (macOS / Linux)

```bash
brew tap open-agent-garden/agentbreeder
brew install agentbreeder

# Verify
agentbreeder --help
```

### Option 3: Docker (for CI/CD pipelines)

```bash
# CLI image — use in CI/CD, no Python needed
docker pull rajits/agentbreeder-cli
docker run rajits/agentbreeder-cli --help

# API server — run the platform
docker pull rajits/agentbreeder-api
docker run -p 8000:8000 rajits/agentbreeder-api

# Dashboard — visual agent builder
docker pull rajits/agentbreeder-dashboard
docker run -p 3001:3001 rajits/agentbreeder-dashboard
```

### Option 4: SDK only (for programmatic use)

```bash
# Lightweight SDK — no CLI, no server, just the Python API
pip install agentbreeder-sdk
```

```python
from agenthub import Agent

agent = Agent("my-agent", version="1.0.0", team="eng")
print(agent.to_yaml())
```

### Option 5: From source (for contributors)

```bash
git clone https://github.com/open-agent-garden/agentbreeder.git
cd agentbreeder
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

---

## Build Your First Agent

### Step 1: Scaffold

```bash
agentbreeder init
```

The wizard asks 5 questions and generates a ready-to-run project:

| Question | Example | Notes |
|----------|---------|-------|
| Framework | LangGraph | Choose from 6 frameworks |
| Cloud target | Local | Where to deploy |
| Agent name | `support-agent` | Lowercase, hyphens allowed |
| Team | `engineering` | Your team for RBAC and cost tracking |
| Owner email | `alice@company.com` | Who is responsible |

### Step 2: Review the generated files

```bash
cd support-agent
cat agent.yaml
```

```yaml
name: support-agent
version: 0.1.0
description: "support-agent — powered by langgraph"
team: engineering
owner: alice@company.com
tags:
  - langgraph
  - generated

framework: langgraph

model:
  primary: gpt-4o

deploy:
  cloud: local
```

### Step 3: Customize

Edit `agent.yaml` to match your needs:

```yaml
name: support-agent
version: 1.0.0
description: "Handles tier-1 customer support tickets"
team: customer-success
owner: alice@company.com

framework: langgraph

model:
  primary: claude-sonnet-4       # Change the model
  fallback: gpt-4o               # Add a fallback

tools:                            # Add tools from the registry
  - ref: tools/zendesk-mcp
  - ref: tools/order-lookup

prompts:
  system: "You are a helpful customer support agent for Acme Corp."

guardrails:
  - pii_detection                 # Strip PII from outputs
  - content_filter                # Block harmful content

deploy:
  cloud: local
  scaling:
    min: 1
    max: 5
```

### Step 4: Validate

```bash
agentbreeder validate agent.yaml
```

### Step 5: Deploy

```bash
agentbreeder deploy agent.yaml --target local
```

### Step 6: Test

```bash
# Interactive chat
agentbreeder chat support-agent

# Check status
agentbreeder status

# View logs
agentbreeder logs support-agent --follow
```

---

## Deploy to Different Targets

### Local (Docker Compose)

```bash
agentbreeder deploy agent.yaml --target local
```

No cloud credentials needed. Starts a Docker Compose stack on your machine.

### GCP Cloud Run

```bash
# Prerequisites: gcloud CLI authenticated, project set
gcloud auth login
gcloud config set project my-project

# Deploy
agentbreeder deploy agent.yaml --target cloud-run --region us-central1
```

Your `agent.yaml` should specify GCP:

```yaml
deploy:
  cloud: gcp
  region: us-central1
  scaling:
    min: 0        # Scale to zero when idle
    max: 10
  secrets:
    - OPENAI_API_KEY    # Must exist in GCP Secret Manager
```

### AWS ECS Fargate (planned)

```bash
# Coming soon
agentbreeder deploy agent.yaml --target aws --region us-east-1
```

---

## Use Different Frameworks

AgentBreeder is framework-agnostic. Your `agent.yaml` specifies which framework to use, and the engine builds the right container.

### LangGraph

```yaml
framework: langgraph

model:
  primary: gpt-4o
```

```python
# agent.py
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    messages: Annotated[list, add_messages]

graph = StateGraph(State)
graph.add_node("chatbot", chatbot)
graph.add_edge(START, "chatbot")
graph.add_edge("chatbot", END)
app = graph.compile()
```

### OpenAI Agents

```yaml
framework: openai_agents

model:
  primary: gpt-4o
```

```python
# agent.py
from agents import Agent, Runner

agent = Agent(
    name="support-agent",
    instructions="You are a helpful assistant.",
)

result = Runner.run_sync(agent, "Hello!")
```

### Claude SDK

```yaml
framework: claude_sdk

model:
  primary: claude-sonnet-4-20250514
```

```python
# agent.py
import anthropic

client = anthropic.Anthropic()
message = client.messages.create(
    model="claude-sonnet-4-20250514",
    max_tokens=1024,
    messages=[{"role": "user", "content": "Hello!"}],
)
```

### Custom (bring your own)

```yaml
framework: custom

model:
  primary: any-model
```

```python
# agent.py — whatever you want
def run(user_message: str) -> str:
    # Your custom agent logic here
    return "response"
```

---

## Use Local Models with Ollama

No cloud API keys required. Run everything locally.

### Step 1: Install and start Ollama

```bash
# macOS
brew install ollama

# Start the server
ollama serve &

# Pull a model
ollama pull llama3
```

### Step 2: Configure your agent

```yaml
# agent.yaml
model:
  primary: ollama/llama3
  gateway: ollama
```

### Step 3: Deploy

```bash
agentbreeder deploy agent.yaml --target local
```

The engine routes all LLM calls through your local Ollama instance. No data leaves your machine.

---

## Configure LLM Providers

### Add a provider

```bash
# Add OpenAI
agentbreeder provider add openai --api-key sk-...

# Add Anthropic
agentbreeder provider add anthropic --api-key sk-ant-...

# Add Google
agentbreeder provider add google --credentials-file sa.json

# Add Ollama (local)
agentbreeder provider add ollama --base-url http://localhost:11434
```

### List providers

```bash
agentbreeder provider list
```

### Use fallback chains

If the primary model is unavailable, AgentBreeder automatically falls back:

```yaml
model:
  primary: claude-sonnet-4        # Try this first
  fallback: gpt-4o               # Fall back to this
  gateway: litellm               # Route through LiteLLM for 100+ models
```

### Use LiteLLM gateway

Route all model calls through [LiteLLM](https://docs.litellm.ai/) for unified access to 100+ models:

```yaml
model:
  primary: claude-sonnet-4
  gateway: litellm
```

```bash
# Start LiteLLM proxy
litellm --model claude-sonnet-4

# Or set the base URL
export LITELLM_BASE_URL=http://localhost:4000
```

---

## Manage Secrets

AgentBreeder supports four secrets backends. Your agents reference secrets by name — the backend handles storage.

### Environment variables (default)

```bash
# .env file
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

```yaml
# agent.yaml
deploy:
  secrets:
    - OPENAI_API_KEY
    - ANTHROPIC_API_KEY
```

### AWS Secrets Manager

```bash
# Store a secret
agentbreeder secret set OPENAI_API_KEY --backend aws --value sk-...

# List secrets
agentbreeder secret list --backend aws
```

### GCP Secret Manager

```bash
agentbreeder secret set OPENAI_API_KEY --backend gcp --value sk-...
```

### HashiCorp Vault

```bash
agentbreeder secret set OPENAI_API_KEY --backend vault --value sk-...
```

---

## Orchestrate Multiple Agents

Build multi-agent pipelines with 6 strategies.

### Strategy overview

| Strategy | Use case | How it works |
|----------|----------|-------------|
| `router` | Triage + routing | One agent classifies, routes to specialists |
| `sequential` | Pipeline | Agents execute in order, passing state |
| `parallel` | Fan-out | Multiple agents run simultaneously |
| `hierarchical` | Management | Manager delegates to worker agents |
| `supervisor` | Quality control | Supervisor reviews and corrects |
| `fan_out_fan_in` | Map-reduce | Fan out to workers, aggregate results |

### Example: Router pipeline

```yaml
# orchestration.yaml
name: support-pipeline
version: "1.0.0"
team: customer-success
strategy: router

agents:
  triage:
    ref: agents/triage-agent
    routes:
      - condition: billing
        target: billing
      - condition: technical
        target: technical
      - condition: default
        target: general
  billing:
    ref: agents/billing-agent
  technical:
    ref: agents/technical-agent
  general:
    ref: agents/general-agent

shared_state:
  type: session_context
  backend: redis

deploy:
  target: local
```

```bash
# Validate
agentbreeder orchestration validate orchestration.yaml

# Deploy
agentbreeder orchestration deploy orchestration.yaml

# Chat with the pipeline
agentbreeder orchestration chat support-pipeline
```

### Example: Sequential pipeline

```yaml
strategy: sequential

agents:
  researcher:
    ref: agents/researcher
    order: 1
  writer:
    ref: agents/writer
    order: 2
  editor:
    ref: agents/editor
    order: 3
```

### Programmatic orchestration (Full Code SDK)

```python
from agenthub import Orchestration

pipeline = (
    Orchestration("support-pipeline", strategy="router", team="eng")
    .add_agent("triage",  ref="agents/triage-agent")
    .add_agent("billing", ref="agents/billing-agent")
    .add_agent("general", ref="agents/general-agent")
    .with_route("triage", condition="billing", target="billing")
    .with_route("triage", condition="default", target="general")
    .with_shared_state(state_type="session_context", backend="redis")
)
pipeline.deploy()
```

---

## Use the Python SDK

The SDK is for programmatic agent definitions — the "Full Code" tier.

### Install

```bash
pip install agentbreeder-sdk
```

### Define an agent

```python
from agenthub import Agent

agent = (
    Agent("support-agent", version="1.0.0", team="engineering")
    .with_model(primary="claude-sonnet-4", fallback="gpt-4o")
    .with_tools(["tools/zendesk-mcp", "tools/order-lookup"])
    .with_prompt(system="You are a helpful customer support agent.")
    .with_deploy(cloud="gcp", min_scale=1, max_scale=10)
)
```

### Export to YAML

```python
# Generate agent.yaml
yaml_str = agent.to_yaml()
print(yaml_str)

# Save to file
agent.save("agent.yaml")
```

### Load from YAML

```python
agent = Agent.from_yaml_file("agent.yaml")
print(agent.config.name)   # support-agent
print(agent.config.team)   # engineering
```

### Deploy programmatically

```python
agent.deploy()
```

---

## Migrate from Another Framework

Already have agents built with another framework? Wrap them in `agent.yaml` without rewriting your code.

### From LangGraph

```yaml
framework: langgraph
```

Your existing `agent.py` with `StateGraph` works as-is. See [full migration guide](migrations/FROM_LANGGRAPH.md).

### From OpenAI Agents

```yaml
framework: openai_agents
```

Your existing `Agent` + `Runner` code works as-is. See [full migration guide](migrations/FROM_OPENAI_AGENTS.md).

### From CrewAI

```yaml
framework: crewai
```

See [full migration guide](migrations/FROM_CREWAI.md).

### From AutoGen

See [full migration guide](migrations/FROM_AUTOGEN.md).

### From custom code

```yaml
framework: custom
```

See [full migration guide](migrations/FROM_CUSTOM.md).

---

## Eject from YAML to Full Code

Start with YAML config, eject to SDK code when you need more control.

```bash
# Generate Python SDK code from your agent.yaml
agentbreeder eject agent.yaml --language python

# Generate TypeScript SDK code
agentbreeder eject agent.yaml --language typescript
```

This creates an `agent.py` (or `agent.ts`) that uses the SDK and can be customized freely. Your original `agent.yaml` is preserved.

**Tier mobility:** No Code (visual builder) → Low Code (YAML) → Full Code (SDK). Move freely between tiers — no lock-in at any level.

---

## Use MCP Servers

AgentBreeder has native MCP (Model Context Protocol) support. MCP servers are injected as sidecar containers alongside your agent.

### Reference MCP servers from the registry

```yaml
# agent.yaml
tools:
  - ref: tools/zendesk-mcp       # MCP server from org registry
  - ref: tools/slack-mcp
```

### Discover available MCP servers

```bash
# Scan for MCP servers on your network
agentbreeder scan

# List registered MCP servers
agentbreeder list tools
```

### Register a custom MCP server

```bash
agentbreeder submit tools/my-custom-mcp --type mcp
```

---

## Manage Teams and RBAC

Every deploy is governed by RBAC. Agents belong to teams, and teams control who can deploy what.

### Configure access in agent.yaml

```yaml
# agent.yaml
team: customer-success            # Required — who owns this agent
owner: alice@company.com          # Required — individual responsible

access:
  visibility: team                # public | team | private
  allowed_callers:                # Who can invoke this agent
    - team:engineering
    - team:customer-success
  require_approval: false         # If true, deploys need admin approval
```

### What happens at deploy time

1. AgentBreeder checks if the deploying user belongs to the agent's team
2. If `require_approval: true`, the deploy is queued for admin review
3. Cost is attributed to the team
4. An audit entry is written with who, what, when, where

There is no way to bypass this. Governance is structural.

---

## Track Costs

Every deploy tracks cost attribution automatically.

### View costs

```bash
# Costs by team
agentbreeder list costs --group-by team

# Costs by agent
agentbreeder list costs --group-by agent

# Costs by model
agentbreeder list costs --group-by model
```

### How cost tracking works

Every LLM call made by a deployed agent is logged with:

- Token count (input + output)
- Model used (including fallback)
- Team and agent attribution
- Timestamp

No configuration needed — this happens automatically for every deployed agent.

---

## Use the Git Workflow

AgentBreeder has a built-in review workflow for changes to agents, tools, and prompts.

### Submit a change for review

```bash
# Create a PR for your agent changes
agentbreeder submit agent.yaml --title "Update support agent prompt"
```

### Review submissions

```bash
# List pending reviews
agentbreeder review list

# Show a specific PR
agentbreeder review show 42

# Approve
agentbreeder review approve 42

# Reject with feedback
agentbreeder review reject 42 --comment "Needs guardrails for PII"
```

### Publish approved changes

```bash
agentbreeder publish 42
```

This merges the PR, bumps the version, and updates the registry.

---

## Run Evaluations

Test your agents against golden datasets before deploying to production.

```bash
# Run evals
agentbreeder eval run --agent support-agent --dataset golden-test-cases.json

# View results
agentbreeder eval results --agent support-agent
```

---

## Use Agent Templates

Start from pre-built templates instead of blank scaffolds.

```bash
# List available templates
agentbreeder template list

# Create from a template
agentbreeder template use customer-support --name my-support-agent

# Publish your agent as a template
agentbreeder template create --from agent.yaml --name "My Template"
```

---

## Teardown a Deployed Agent

```bash
# Remove with confirmation prompt
agentbreeder teardown support-agent

# Force remove (no confirmation)
agentbreeder teardown support-agent --force
```

This stops the agent, removes the container, and archives the registry entry. The audit log is preserved.

---

## Use AgentBreeder in CI/CD

### GitHub Actions

```yaml
# .github/workflows/deploy-agent.yml
name: Deploy Agent
on:
  push:
    paths: ['agents/support-agent/**']

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Validate
        run: |
          docker run --rm -v $PWD:/work -w /work \
            rajits/agentbreeder-cli validate agents/support-agent/agent.yaml

      - name: Deploy
        run: |
          docker run --rm -v $PWD:/work -w /work \
            -e GOOGLE_APPLICATION_CREDENTIALS=/work/sa.json \
            rajits/agentbreeder-cli deploy agents/support-agent/agent.yaml --target cloud-run
```

### GitLab CI

```yaml
# .gitlab-ci.yml
deploy-agent:
  image: rajits/agentbreeder-cli:latest
  script:
    - agentbreeder validate agents/support-agent/agent.yaml
    - agentbreeder deploy agents/support-agent/agent.yaml --target cloud-run
```

---

## Run the Platform with Docker Compose

Run the full AgentBreeder stack (API + Dashboard + Database) with Docker:

```bash
# Using pre-built images
docker compose -f deploy/docker-compose.yml up -d
```

Or create a custom compose file:

```yaml
# docker-compose.yml
services:
  api:
    image: rajits/agentbreeder-api:latest
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://agentbreeder:agentbreeder@db:5432/agentbreeder
      REDIS_URL: redis://redis:6379
      SECRET_KEY: change-me-in-production
    depends_on:
      - db
      - redis

  dashboard:
    image: rajits/agentbreeder-dashboard:latest
    ports:
      - "3001:3001"
    depends_on:
      - api

  db:
    image: postgres:16
    environment:
      POSTGRES_USER: agentbreeder
      POSTGRES_PASSWORD: agentbreeder
      POSTGRES_DB: agentbreeder
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7

volumes:
  pgdata:
```

```bash
docker compose up -d

# Dashboard: http://localhost:3001
# API:       http://localhost:8000
# API Docs:  http://localhost:8000/docs
```

---

## Troubleshooting

### "agentbreeder: command not found"

```bash
# Check if it's installed
pip show agentbreeder

# If installed but not in PATH, use python -m
python -m cli.main --help

# Or reinstall
pip install agentbreeder
```

### "Validation failed: unknown framework"

Supported frameworks: `langgraph`, `openai_agents`, `claude_sdk`, `crewai`, `google_adk`, `custom`

Check your `agent.yaml`:
```yaml
framework: langgraph   # Must be one of the above
```

### "RBAC check failed"

The deploying user must belong to the team specified in `agent.yaml`:

```yaml
team: engineering   # Your user must be a member of this team
```

### "Container build failed"

```bash
# Check Docker is running
docker info

# Try building manually
docker build -t my-agent .

# Check the generated Dockerfile
agentbreeder deploy agent.yaml --target local --dry-run
```

### "Deploy rolled back"

The pipeline is atomic — if any of the 8 steps fails, everything rolls back. Check which step failed:

```bash
agentbreeder logs my-agent
agentbreeder status my-agent
```

### Dashboard won't start

The dashboard container needs the API to be running:

```bash
# Start API first
docker run -d -p 8000:8000 rajits/agentbreeder-api

# Then dashboard
docker run -d -p 3001:3001 rajits/agentbreeder-dashboard
```
