# Migrate from LangGraph to AgentBreeder

> **Time to migrate:** ~15 minutes
> **Difficulty:** Easy
> **What changes:** You add an `agent.yaml` file. Your LangGraph code stays exactly the same.

---

## Before You Start

- [ ] You have an existing LangGraph agent with a compiled graph
- [ ] Your agent code is in a directory with `agent.py` and `requirements.txt` (or `pyproject.toml`)
- [ ] Python 3.11+ is installed
- [ ] Docker is installed and running (for local deploys)
- [ ] You have installed AgentBreeder: `pip install agentbreeder`

---

## The Big Picture

**Before:** You write LangGraph code, figure out how to containerize it, manually deploy it, and hope someone documents where it lives.

**After:** You write the same LangGraph code, add a 10-line YAML file, run `agentbreeder deploy`, and get a production-ready container with RBAC, cost tracking, health checks, and registry entry -- automatically.

Your LangGraph graph, state, nodes, edges, and tools are unchanged. AgentBreeder wraps your graph in a FastAPI server and handles everything else.

---

## Before & After

### Before: Raw LangGraph

```
my-langgraph-agent/
  agent.py
  tools.py
  requirements.txt
  Dockerfile              # you wrote this
  deploy.sh               # you wrote this
  terraform/              # you wrote this
    main.tf
    variables.tf
```

You had to:
1. Write a Dockerfile manually
2. Build and push to a container registry
3. Write Terraform/Pulumi for cloud infrastructure
4. Set up health checks
5. Configure autoscaling
6. Remember to tell your team the agent exists
7. Track costs in a spreadsheet

### After: LangGraph + AgentBreeder

```
my-langgraph-agent/
  agent.py                # UNCHANGED
  tools.py                # UNCHANGED
  requirements.txt        # UNCHANGED
  agent.yaml              # NEW -- 10 lines
```

You run `agentbreeder deploy agent.yaml` and get all of the above for free.

---

## Step-by-Step Migration

### Step 1: Verify your LangGraph agent structure

AgentBreeder looks for a compiled graph exported from `agent.py`. Your file should export a variable named `graph` or `app`:

```python
# agent.py
from langgraph.graph import StateGraph

class AgentState(TypedDict):
    messages: list
    response: str

def process(state: AgentState) -> AgentState:
    # your logic here
    ...

builder = StateGraph(AgentState)
builder.add_node("process", process)
builder.set_entry_point("process")
builder.set_finish_point("process")

# Export as 'graph' -- AgentBreeder looks for this variable
graph = builder.compile()
```

If your graph variable is named something else (e.g., `workflow`, `chain`), rename the export or add an alias:

```python
workflow = builder.compile()
graph = workflow  # alias for AgentBreeder
```

### Step 2: Verify your requirements.txt

Make sure your `requirements.txt` includes your LangGraph dependencies:

```
langgraph>=0.2.0
langchain-core>=0.3.0
langchain-openai>=0.2.0    # if using OpenAI
langchain-anthropic>=0.3.0  # if using Anthropic
```

AgentBreeder will automatically add framework dependencies if they are missing (`langgraph`, `langchain-core`, `fastapi`, `uvicorn`, `httpx`, `pydantic`), but it is better to be explicit.

### Step 3: Create agent.yaml

In the same directory as your `agent.py`, create `agent.yaml`:

```yaml
name: my-langgraph-agent
version: 1.0.0
description: "Customer support agent with tool use"
team: engineering
owner: you@company.com
tags: [support, langgraph, production]

framework: langgraph

model:
  primary: gpt-4o
  fallback: gpt-4o-mini
  temperature: 0.7
  max_tokens: 4096

deploy:
  cloud: local
```

That is the minimal config. Every field maps to something AgentBreeder handles automatically.

### Step 4: Validate

```bash
agentbreeder validate agent.yaml
```

This checks your YAML against the JSON Schema and verifies that your `agent.py` exists with the expected exports. Fix any errors before proceeding.

### Step 5: Deploy locally

```bash
agentbreeder deploy agent.yaml --target local
```

AgentBreeder will:
1. Parse and validate your `agent.yaml`
2. Check RBAC permissions
3. Resolve any registry references (tools, knowledge bases)
4. Generate a Dockerfile with a FastAPI server wrapper
5. Build a Docker container with your agent code
6. Start the container with Docker Compose
7. Run health checks
8. Register the agent in the local registry
9. Return the endpoint URL

You will see output like:

```
  AgentBreeder
  Deploying agent.yaml -> local

    Parse & validate YAML
    RBAC check
    Resolve dependencies
    Build container
    Provision infrastructure
    Deploy & health check
    Register in registry
    Return endpoint

  Deployed
  Agent:    my-langgraph-agent
  Version:  1.0.0
  Endpoint: http://localhost:8080

  Invoke:   curl -X POST http://localhost:8080/invoke \
            -d '{"input": {"message": "hello"}}' \
            -H 'Content-Type: application/json'
```

### Step 6: Test the endpoint

```bash
curl -X POST http://localhost:8080/invoke \
  -d '{"input": {"messages": [{"role": "user", "content": "Hello!"}]}}' \
  -H 'Content-Type: application/json'
```

### Step 7: Deploy to cloud (when ready)

Change your deploy target:

```yaml
# For AWS ECS Fargate
deploy:
  cloud: aws
  runtime: ecs-fargate
  region: us-east-1
  scaling:
    min: 1
    max: 10
    target_cpu: 70
  resources:
    cpu: "1"
    memory: "2Gi"
  secrets:
    - OPENAI_API_KEY
```

```bash
agentbreeder deploy agent.yaml --target aws
```

Or for GCP:

```yaml
deploy:
  cloud: gcp
  runtime: cloud-run
  region: us-central1
```

```bash
agentbreeder deploy agent.yaml --target gcp
```

---

## Concept Mapping: LangGraph to AgentBreeder

| LangGraph Concept | AgentBreeder Equivalent | Notes |
|-------------------|------------------------|-------|
| `StateGraph` | Your `agent.py` (unchanged) | AG wraps it; does not replace it |
| `graph.compile()` | Export as `graph` variable | AG server wrapper imports `graph` from `agent.py` |
| Nodes / Edges | Your code (unchanged) | AG does not inspect graph internals |
| `ToolNode` / tools | `tools:` in `agent.yaml` | Registry refs or inline definitions |
| LLM model selection | `model.primary` / `model.fallback` | Declarative, with automatic fallback |
| Subgraphs | `subagents:` in `agent.yaml` | Cross-agent calls via A2A protocol |
| `MemorySaver` | `knowledge_bases:` in `agent.yaml` | Registry-managed, shared across agents |
| Human-in-the-loop | `access.require_approval: true` | Deploy-level approval gates |
| `langgraph serve` | `agentbreeder deploy` | AG replaces LangGraph's own serve command |
| LangSmith tracing | AG tracing (OpenTelemetry) | Automatic, no code changes |
| Environment variables | `deploy.env_vars` / `deploy.secrets` | Secrets use cloud-native secret managers |

---

## Adding Governance Features

Once migrated, you can incrementally add AgentBreeder features:

### Guardrails

```yaml
guardrails:
  - pii_detection
  - hallucination_check
  - content_filter
```

### Access control

```yaml
access:
  visibility: team
  allowed_callers:
    - team:engineering
    - team:support
```

### MCP servers (tool sidecar injection)

```yaml
mcp_servers:
  - ref: mcp/zendesk
    transport: stdio
  - ref: mcp/slack
```

### Multi-agent orchestration

If you have multiple LangGraph agents, you can orchestrate them with `orchestration.yaml`:

```yaml
name: support-pipeline
version: 1.0.0
team: support
owner: you@company.com
strategy: sequential

agents:
  classifier:
    ref: agents/ticket-classifier
  resolver:
    ref: agents/ticket-resolver
  responder:
    ref: agents/response-writer

deploy:
  target: local
```

This chains agents: classifier output feeds into resolver, then responder. Each agent is independently deployed and managed.

---

## What You Gain

| Feature | Before (LangGraph only) | After (LangGraph + AG) |
|---------|------------------------|------------------------|
| Deploy | Manual Docker + Terraform | `agentbreeder deploy agent.yaml` |
| Multi-cloud | Rewrite per provider | Change `cloud: aws` to `cloud: gcp` |
| RBAC | Not available | Automatic on every deploy |
| Cost tracking | Manual / LangSmith | Per-agent, per-team, per-model |
| Audit trail | Not available | Every deploy and invocation logged |
| Agent registry | Not available | `agentbreeder search` finds any agent |
| Health checks | Manual | Automatic with configurable intervals |
| Autoscaling | Manual cloud config | Declarative in `agent.yaml` |
| Model fallback | Try/except in code | `fallback: gpt-4o-mini` |
| Guardrails | Build from scratch | `guardrails: [pii_detection]` |

## What Stays the Same

- Your LangGraph graph definition
- Your node functions and edge logic
- Your tools and tool implementations
- Your state schema
- Your LangChain integrations
- Your ability to run standalone (`python agent.py`)

---

## Troubleshooting

### "Missing agent.py" error

AgentBreeder expects a file named `agent.py` in the same directory as `agent.yaml`. If your main file is named differently:

```bash
# Option 1: Rename it
mv my_agent.py agent.py

# Option 2: Create an agent.py that re-exports
# agent.py
from my_agent import workflow as graph
```

### "No 'graph' or 'app' variable found"

The AG server wrapper imports your compiled graph. Make sure you export it:

```python
# Must be at module level, not inside a function
graph = builder.compile()
```

### Container build fails on dependencies

Check that your `requirements.txt` does not pin versions that conflict with AG's requirements. AG adds these automatically:
- `langgraph>=0.2.0`
- `langchain-core>=0.3.0`
- `fastapi>=0.110.0`
- `uvicorn[standard]>=0.27.0`
- `httpx>=0.27.0`
- `pydantic>=2.0.0`

If you have conflicting pins, widen your version ranges.

### Health check fails

The generated container exposes `/health` on port 8080. If your agent takes a long time to initialize (loading large models, connecting to databases), the default 5-second timeout may not be enough. Check your agent's startup time and ensure any heavy initialization is lazy.

### "RBAC check failed"

Every deploy requires a valid team. Make sure the `team` field in your `agent.yaml` matches a team registered in AgentBreeder. For local development, the default `examples` team works:

```yaml
team: examples
```

### Agent works standalone but fails in container

The most common cause is missing environment variables. Add them to your config:

```yaml
deploy:
  env_vars:
    LANGCHAIN_TRACING_V2: "false"
  secrets:
    - OPENAI_API_KEY
```

---

## Full Example

Here is a complete, working LangGraph agent migrated to AgentBreeder:

**agent.py:**

```python
from __future__ import annotations
from typing import TypedDict
from langgraph.graph import StateGraph

class AgentState(TypedDict):
    message: str
    response: str

def respond(state: AgentState) -> AgentState:
    message = state.get("message", "")
    return {
        "message": message,
        "response": f"Hello from AgentBreeder! You said: {message}",
    }

builder = StateGraph(AgentState)
builder.add_node("respond", respond)
builder.set_entry_point("respond")
builder.set_finish_point("respond")

graph = builder.compile()
```

**requirements.txt:**

```
langgraph>=0.2.0
langchain-core>=0.3.0
```

**agent.yaml:**

```yaml
name: hello-world-agent
version: 0.1.0
description: "A simple hello world LangGraph agent"
team: examples
owner: dev@agentbreeder.com
tags: [example, langgraph, hello-world]

framework: langgraph

model:
  primary: gpt-4o-mini

deploy:
  cloud: local
```

**Deploy:**

```bash
agentbreeder deploy agent.yaml
```

That is it. Your LangGraph agent is now deployed with governance, health checks, and registry entry.
