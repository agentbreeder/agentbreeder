# Migrate from CrewAI to AgentBreeder

> **Time to migrate:** ~20 minutes
> **Difficulty:** Easy
> **What changes:** You add an `agent.yaml` file and optionally an `orchestration.yaml` for multi-agent workflows. Your CrewAI code stays exactly the same.

---

## Before You Start

- [ ] You have an existing CrewAI agent or crew
- [ ] Your agent code is in a directory with `agent.py` and `requirements.txt`
- [ ] Python 3.11+ is installed
- [ ] Docker is installed and running
- [ ] You have installed AgentBreeder: `pip install agentbreeder`

---

## The Big Picture

CrewAI excels at multi-agent orchestration with its Crew, Agent, Task, and Process abstractions. AgentBreeder does not replace any of that. It wraps your CrewAI code in a production-ready container with governance, multi-cloud deploy, and org-wide discoverability.

**Your CrewAI code runs as-is.** AgentBreeder adds the deployment and governance layer.

---

## Before & After

### Before: Raw CrewAI

```
my-crew/
  agent.py            # Crew definition
  tools.py            # Custom tools
  requirements.txt
  Dockerfile          # you wrote this
  docker-compose.yml  # you wrote this
  deploy.sh           # you wrote this
```

### After: CrewAI + AgentBreeder

```
my-crew/
  agent.py            # UNCHANGED
  tools.py            # UNCHANGED
  requirements.txt    # UNCHANGED
  agent.yaml          # NEW -- the AG config
```

---

## Step-by-Step Migration

### Step 1: Structure your CrewAI code

AgentBreeder expects your crew to be importable from `agent.py`. Your file should export a `crew` variable (a `Crew` instance) or an `agent` variable:

```python
# agent.py
from crewai import Agent, Task, Crew, Process

# Define agents
researcher = Agent(
    role="Senior Research Analyst",
    goal="Find and analyze cutting-edge AI developments",
    backstory="You are an expert AI researcher...",
    tools=[search_tool, scrape_tool],
    verbose=True,
)

writer = Agent(
    role="Tech Content Writer",
    goal="Write engaging articles about AI",
    backstory="You are a skilled technical writer...",
    verbose=True,
)

# Define tasks
research_task = Task(
    description="Research the latest developments in {topic}",
    expected_output="A detailed research report",
    agent=researcher,
)

write_task = Task(
    description="Write an article based on the research report",
    expected_output="A publishable article",
    agent=writer,
)

# Export as 'crew' -- AgentBreeder looks for this
crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, write_task],
    process=Process.sequential,
    verbose=True,
)
```

### Step 2: Create agent.yaml

```yaml
name: research-crew
version: 1.0.0
description: "Research and writing crew for AI content"
team: content
owner: you@company.com
tags: [crewai, research, content]

framework: crewai

model:
  primary: gpt-4o
  fallback: gpt-4o-mini
  temperature: 0.7

tools:
  - name: search_tool
    type: function
    description: "Search the web for information"
  - name: scrape_tool
    type: function
    description: "Scrape web page content"

deploy:
  cloud: local
  resources:
    cpu: "1"
    memory: "2Gi"
  secrets:
    - OPENAI_API_KEY
```

### Step 3: Validate

```bash
agentbreeder validate agent.yaml
```

### Step 4: Deploy

```bash
agentbreeder deploy agent.yaml --target local
```

### Step 5: Test

```bash
curl -X POST http://localhost:8080/invoke \
  -d '{"input": {"topic": "AI agents in 2026"}}' \
  -H 'Content-Type: application/json'
```

### Step 6: Deploy to cloud (when ready)

```yaml
deploy:
  cloud: aws
  runtime: ecs-fargate
  region: us-east-1
  scaling:
    min: 1
    max: 5
  resources:
    cpu: "2"
    memory: "4Gi"
  secrets:
    - OPENAI_API_KEY
    - SERPER_API_KEY
```

---

## Concept Mapping: CrewAI to AgentBreeder

| CrewAI Concept | AgentBreeder Equivalent | Notes |
|---------------|------------------------|-------|
| `Agent` | Individual agent in `agent.yaml` | Each CrewAI Agent can also be its own AG agent |
| `Task` | Part of your crew logic (unchanged) | AG does not inspect task definitions |
| `Crew` | Export as `crew` from `agent.py` | AG wraps the entire crew as one deployable unit |
| `Process.sequential` | `strategy: sequential` in `orchestration.yaml` | Optional -- AG can orchestrate at a higher level too |
| `Process.hierarchical` | `strategy: hierarchical` or `supervisor` | Maps to AG orchestration strategies |
| `Tool` / `@tool` | `tools:` in `agent.yaml` | Declare for documentation; tools run inside your code |
| `crew.kickoff()` | `POST /invoke` on the deployed container | AG server wrapper calls `crew.kickoff()` |
| `manager_llm` | Supervisor agent in `orchestration.yaml` | For AG-level orchestration across crews |
| CrewAI memory | `knowledge_bases:` in `agent.yaml` | AG provides registry-managed knowledge bases |
| `.env` file | `deploy.secrets` + `deploy.env_vars` | Cloud-native secret management |

---

## Mapping CrewAI Orchestration to AgentBreeder

CrewAI has built-in multi-agent orchestration via `Process`. When you deploy a CrewAI crew to AgentBreeder, the crew's internal orchestration works as-is. But you can also use AgentBreeder's orchestration layer to coordinate multiple crews.

### Option A: Single Crew as One Agent (Simple)

Deploy your entire crew as a single AgentBreeder agent. The crew handles its own internal orchestration:

```yaml
# agent.yaml -- the entire crew is one AG agent
name: research-crew
framework: crewai
# ... rest of config
```

This is the recommended starting point. Your crew's `Process.sequential` or `Process.hierarchical` runs inside the container.

### Option B: Individual Agents + AG Orchestration (Advanced)

Split each CrewAI Agent into its own AG agent, then use `orchestration.yaml` to coordinate them:

```yaml
# orchestration.yaml
name: research-pipeline
version: 1.0.0
team: content
owner: you@company.com
strategy: sequential

agents:
  researcher:
    ref: agents/research-analyst
  writer:
    ref: agents/content-writer

deploy:
  target: local
```

This gives you:
- Independent scaling per agent
- Mix frameworks (e.g., researcher is CrewAI, writer is LangGraph)
- Cross-team agent reuse
- Per-agent cost tracking

### CrewAI Process to AG Strategy Mapping

| CrewAI Process | AG Orchestration Strategy | When to use |
|---------------|--------------------------|-------------|
| `Process.sequential` | `strategy: sequential` | Tasks must run in order, output chains to next |
| `Process.hierarchical` | `strategy: supervisor` | Manager delegates to workers, synthesizes results |
| Custom routing | `strategy: router` | Route input to different agents based on content |
| Parallel tasks | `strategy: parallel` | Independent tasks that can run concurrently |
| Map-reduce | `strategy: fan_out_fan_in` | Fan out to workers, merge agent combines results |

---

## Adding Governance Features

### Guardrails

```yaml
guardrails:
  - pii_detection
  - content_filter
  - hallucination_check
```

### Access control

```yaml
access:
  visibility: team
  allowed_callers:
    - team:content
    - team:marketing
```

### Model fallback

```yaml
model:
  primary: gpt-4o
  fallback: claude-sonnet-4
  gateway: litellm
```

With LiteLLM gateway, you get unified access to 100+ models with a single API format.

---

## What You Gain

| Feature | CrewAI Only | CrewAI + AgentBreeder |
|---------|-------------|----------------------|
| Multi-agent orchestration | Built-in (Crew) | Crew-level + AG-level orchestration |
| Deploy | Manual | `agentbreeder deploy agent.yaml` |
| Multi-cloud | Manual | One-line change |
| Scaling | Manual | Declarative autoscaling |
| RBAC | Not available | Automatic |
| Cost tracking | Manual | Per-crew, per-agent, per-model |
| Agent registry | Not available | Org-wide discovery |
| Health checks | Not available | Automatic |
| Model fallback | Manual try/except | Declarative |
| Guardrails | Manual | Declarative |

## What Stays the Same

- Your CrewAI Agent definitions (role, goal, backstory)
- Your Task definitions (description, expected_output)
- Your Crew configuration (process, agents, tasks)
- Your custom tools
- Your ability to use `crew.kickoff()` locally

---

## Troubleshooting

### "Missing agent.py" error

AgentBreeder expects `agent.py` in the same directory as `agent.yaml`. If your crew is defined in a different file:

```python
# agent.py
from my_crew import crew  # re-export
```

### CrewAI verbose output in container logs

CrewAI's `verbose=True` writes to stdout. In the container, this goes to Docker logs. Access via:

```bash
agentbreeder logs research-crew
```

### Crew takes a long time to complete

CrewAI crews with many agents and tasks can take minutes. The default health check timeout is 5 seconds, but that only checks if the server is responding -- it does not wait for task completion. Long-running crew kickoffs are handled asynchronously by the server wrapper.

### Missing API keys

CrewAI agents often need multiple API keys (LLM, search, scraping). Add them all to secrets:

```yaml
deploy:
  secrets:
    - OPENAI_API_KEY
    - SERPER_API_KEY
    - BROWSERLESS_API_KEY
```

### CrewAI memory / caching

CrewAI's built-in memory works inside the container. For persistence across container restarts, use AG knowledge bases:

```yaml
knowledge_bases:
  - ref: kb/research-cache
```

---

## Full Example

**agent.py:**

```python
from crewai import Agent, Task, Crew, Process
from crewai_tools import SerperDevTool

search_tool = SerperDevTool()

researcher = Agent(
    role="Senior Research Analyst",
    goal="Find cutting-edge AI developments",
    backstory="Expert AI researcher with a focus on practical applications.",
    tools=[search_tool],
    verbose=True,
)

writer = Agent(
    role="Tech Content Writer",
    goal="Write engaging, accurate articles about AI",
    backstory="Technical writer specializing in AI and developer tools.",
    verbose=True,
)

research_task = Task(
    description="Research the latest developments in {topic}. "
                "Focus on practical applications and industry impact.",
    expected_output="A detailed research report with citations.",
    agent=researcher,
)

write_task = Task(
    description="Write a 1000-word article based on the research. "
                "Make it engaging and accessible to developers.",
    expected_output="A publishable markdown article.",
    agent=writer,
)

crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, write_task],
    process=Process.sequential,
    verbose=True,
)
```

**requirements.txt:**

```
crewai>=0.28.0
crewai-tools>=0.2.0
```

**agent.yaml:**

```yaml
name: ai-content-crew
version: 1.0.0
description: "Research and write AI content articles"
team: content
owner: content-lead@company.com
tags: [crewai, research, content, production]

framework: crewai

model:
  primary: gpt-4o
  fallback: gpt-4o-mini

tools:
  - name: SerperDevTool
    type: function
    description: "Search the web using Serper API"

guardrails:
  - hallucination_check
  - content_filter

deploy:
  cloud: local
  resources:
    cpu: "1"
    memory: "2Gi"
  secrets:
    - OPENAI_API_KEY
    - SERPER_API_KEY

access:
  visibility: team
  allowed_callers:
    - team:content
    - team:marketing
```

**Deploy:**

```bash
agentbreeder deploy agent.yaml
```
