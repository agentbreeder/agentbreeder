# Agent Architect Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve `/agent-build` into an AI Agent Architect with an opt-in 6-question advisory interview that recommends framework, mode, model, RAG, memory, MCP/A2A, deployment, and eval dimensions — with per-item reasoning — before scaffolding a complete tier-interoperable agent project plus IDE config files.

**Architecture:** A single `.claude/commands/agent-build.md` skill file is rewritten in-place. A mode-selection fork is added at the top. The fast path (existing behavior) is preserved byte-for-byte. The advisory path adds a 6-question interview, a deterministic recommendation engine, and a Recommendations Summary with override support. New scaffold outputs (memory/, rag/, mcp/, tests/evals/, ARCHITECT_NOTES.md, CLAUDE.md, AGENTS.md, .cursorrules, .antigravity.md) are added to the generation phase. A structural test in `tests/unit/` validates the skill file has all required sections and gates CI.

**Tech Stack:** Markdown (Claude Code skill file), Python + pytest (structural tests).

---

### Task 1: Write structural test for agent-build.md (TDD gate)

**Files:**
- Create: `tests/unit/test_agent_build_skill.py`

- [ ] **Step 1: Write the failing test**

```python
"""Structural tests for the /agent-build skill file.

Validates that the skill file contains all required sections
for both fast path and advisory path flows.
"""
from pathlib import Path


SKILL_FILE = Path(__file__).parents[2] / ".claude/commands/agent-build.md"


def skill_content() -> str:
    return SKILL_FILE.read_text()


def test_skill_file_exists():
    assert SKILL_FILE.exists(), f"Skill file not found: {SKILL_FILE}"


def test_fast_path_preserved():
    content = skill_content()
    assert "I know my stack" in content or "know your stack" in content.lower()


def test_advisory_path_present():
    content = skill_content()
    assert "Recommend for me" in content or "recommend" in content.lower()


def test_all_six_advisory_questions_present():
    content = skill_content()
    questions = [
        "Business Goal",
        "Technical Use Case",
        "State Complexity",
        "Team",
        "Data Access",
        "Scale",
    ]
    for q in questions:
        assert q in content, f"Missing advisory question: {q}"


def test_recommendation_dimensions_present():
    content = skill_content()
    dimensions = ["Framework", "Model", "RAG", "Memory", "MCP", "Deploy", "Eval"]
    for dim in dimensions:
        assert dim in content, f"Missing recommendation dimension: {dim}"


def test_new_scaffold_outputs_present():
    content = skill_content()
    outputs = [
        "memory/",
        "rag/",
        "tests/evals/",
        "ARCHITECT_NOTES",
        "CLAUDE.md",
        "AGENTS.md",
        ".cursorrules",
        ".antigravity",
    ]
    for output in outputs:
        assert output in content, f"Missing scaffold output: {output}"


def test_ide_config_templates_present():
    content = skill_content()
    for name in ["CLAUDE.md", "AGENTS.md", ".cursorrules", ".antigravity.md"]:
        assert name in content, f"Missing IDE config template: {name}"


def test_architect_notes_template_present():
    content = skill_content()
    assert "ARCHITECT_NOTES" in content
    assert "Business Goal" in content
    assert "Why" in content
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/unit/test_agent_build_skill.py -v 2>&1 | head -40
```

Expected: `test_skill_file_exists` and `test_fast_path_preserved` PASS. All advisory/new-output tests FAIL.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/unit/test_agent_build_skill.py
git commit -m "test(skill): structural tests for /agent-build advisory mode (failing)"
```

---

### Task 2: Rewrite agent-build.md — Header, Fork, and Fast Path

**Files:**
- Modify: `.claude/commands/agent-build.md` (full rewrite — replaces all 213 lines)

- [ ] **Step 1: Write new file content (header + fork + complete fast path)**

Replace `.claude/commands/agent-build.md` entirely with:

```markdown
# /agent-build — AI Agent Architect

You are an AI Agent Architect for AgentBreeder. You can collect the user's stack choices directly OR advise them on the best choices for their use case — then scaffold a complete, tier-interoperable agent project.

## Do NOT generate any files until you have completed the full intake flow. Ask one question at a time.

---

## Step 0: Mode Selection

Ask exactly:

> "Do you already know your stack (framework, cloud, model), or would you like me to recommend the best setup for your use case?
>
> **(a) I know my stack** — I'll ask 5 quick questions and scaffold your project
> **(b) Recommend for me** — I'll ask 6 questions about your use case and advise on the best framework, model, RAG, memory, MCP/A2A, deployment, and evaluation setup"

- User picks **(a)** → proceed to **FAST PATH** below.
- User picks **(b)** → proceed to **ADVISORY PATH** below, then converge at **Step 2: Generate Project**.

---

## FAST PATH

### Step 1: Collect Inputs

Ask the following questions **one at a time**, in order.

#### 1a. Agent Name
Ask: "What should we call this agent?"
- Validate: `^[a-z0-9][a-z0-9-]*[a-z0-9]$` (slug-friendly, min 2 chars)
- Example: `customer-support-agent`

#### 1b. Purpose / Description
Ask: "What will this agent do? Describe its purpose in a sentence or two."
- Drives the tailored system prompt and tool stubs.

#### 1c. Framework
Ask: "Which framework?" Present:
1. **LangGraph** — Stateful multi-actor workflows
2. **CrewAI** — Role-playing agent crews
3. **Claude SDK** — Anthropic's native agent SDK
4. **OpenAI Agents** — OpenAI's agent framework
5. **Google ADK** — Google's Agent Development Kit
6. **Custom** — Bring your own agent code

#### 1d. Cloud Target
Ask: "Where will it run?" Present:
1. **Local** — Docker Compose on your machine
2. **AWS** — ECS Fargate
3. **GCP** — Cloud Run
4. **Kubernetes** — Any K8s cluster

#### 1e. Tools Needed
Ask: "What tools should this agent have? List them (or say 'none')."
- Free text. Claude generates typed tool stubs and MCP refs in agent.yaml.

#### 1f. Team & Owner
- Infer owner email from `git config user.email`.
- Ask: "Team name and owner email?" with defaults shown. Default team: `engineering`.

### Step 1-Confirm: Confirm Before Generating

Present a summary table of all choices. Ask: "Look good? I'll generate your project."
Wait for confirmation before proceeding to **Step 2: Generate Project**.

---
```

- [ ] **Step 2: Verify fast path test passes**

```bash
python -m pytest tests/unit/test_agent_build_skill.py::test_fast_path_preserved -v
```

Expected: PASS

---

### Task 3: Add Advisory Path (Steps A–I)

**Files:**
- Modify: `.claude/commands/agent-build.md` (append after Step 1-Confirm)

- [ ] **Step 1: Append the advisory path**

Append to `.claude/commands/agent-build.md`:

```markdown
## ADVISORY PATH

### Step A: Business Goal

Ask:
> "What problem does this agent solve, and for whom? Give me a one or two sentence description."
>
> **Examples:**
> - "Reduce tier-1 support tickets for our SaaS product by deflecting common questions"
> - "Automate weekly financial reporting for the CFO from our data warehouse"
> - "Speed up our engineering team's code review process"

Record as `BUSINESS_GOAL`. Use it to pre-populate data/tool suggestions in later questions and to select eval dimensions.

---

### Step B: Technical Use Case

Ask:
> "What does the agent need to do, step by step? Describe the workflow."
>
> **Example:** "User sends a support ticket → agent searches knowledge base → agent looks up order status → if found, responds → if not found, escalates to human agent"

Record as `TECHNICAL_USE_CASE`. Look for signals: loops, conditional branches, human handoffs, parallel tasks.

---

### Step C: State Complexity

Ask:
> "Does your agent need any of the following? Select all that apply:
>
> **(a)** Loops or retries (retry a failing tool call, loop until condition met)
> **(b)** Checkpoints / resume from failure (pick up where it left off after a crash)
> **(c)** Human-in-the-loop approvals (pause and wait for a human to approve before continuing)
> **(d)** Parallel branches (run two sub-tasks simultaneously)
> **(e)** None of the above — straightforward sequential flow"

Record selected options as `STATE_FLAGS`. Full Code trigger: 2+ of (a)–(d) → Full Code. Fewer → Low Code.

---

### Step D: Team & Org Context

Ask (two sub-questions in one message):
> "Two quick questions about your team:
>
> **1. Primary cloud provider?**
> (a) AWS  (b) GCP  (c) Azure  (d) Local / none
>
> **2. Language preference?**
> (a) Python  (b) TypeScript  (c) No preference"

Record as `CLOUD_PREFERENCE` and `LANGUAGE_PREFERENCE`.

---

### Step E: Data Access

Ask:
> "What data does this agent need to work with? Select all that apply:
>
> **(a)** Unstructured documents — PDFs, text files, web pages, knowledge base articles
> **(b)** Structured database — SQL or NoSQL tables, data warehouse
> **(c)** Knowledge graph / relationship data — entities and their connections
> **(d)** Live APIs or real-time web data
> **(e)** None — the agent generates output without external data"

Record as `DATA_FLAGS`. Pre-populate likely answers inferred from `BUSINESS_GOAL` (e.g., "data warehouse" → suggest (b)).

---

### Step F: Scale Profile

Ask:
> "What's the traffic pattern for this agent?
>
> **(a)** Real-time interactive — users are waiting for responses (< 5s latency required)
> **(b)** Async batch — scheduled jobs or queue-driven, latency not critical
> **(c)** Event-driven — triggered by external events (webhooks, alerts, file uploads)
> **(d)** Internal tooling / low-volume — a few runs per day"

Record as `SCALE_PROFILE`.

---

### Step G: Build Recommendations

Apply the following decision logic. Do NOT show this logic to the user — only show the output in Step H.

#### Framework + Mode

| Signals | Recommendation |
|---|---|
| STATE_FLAGS contains (b) or (c), LANGUAGE_PREFERENCE Python or none | **LangGraph — Full Code** |
| TECHNICAL_USE_CASE mentions multiple specialized agents or crew, Python | **CrewAI — Full Code** (STATE_FLAGS ≥ 2) or **CrewAI — Low Code** |
| TECHNICAL_USE_CASE mentions Claude tool use or adaptive thinking, Python, no strong state complexity | **Claude SDK — Full Code** |
| CLOUD_PREFERENCE is GCP, or TECHNICAL_USE_CASE mentions Vertex AI / Google Workspace | **Google ADK — Low Code or Full Code** |
| LANGUAGE_PREFERENCE is TypeScript | **OpenAI Agents SDK — Full Code** |
| STATE_FLAGS is (e) only | **Low Code YAML** (any framework matching cloud/language) |

Full Code trigger: STATE_FLAGS contains 2+ of (a)–(d) → Full Code. Otherwise → Low Code.

#### Model

| Signals | Recommendation |
|---|---|
| TECHNICAL_USE_CASE involves complex multi-step planning, research, analysis | **claude-opus-4** |
| SCALE_PROFILE is (a) real-time, balanced tool use (default) | **claude-sonnet-4-6** |
| SCALE_PROFILE is (b) batch or (d) internal, cost-sensitive | **claude-haiku-4-5** |
| CLOUD_PREFERENCE is GCP | **gemini-2.5-pro** (complex) or **gemini-2.0-flash** (speed) |
| LANGUAGE_PREFERENCE is TypeScript | **gpt-4o** (default) or **o3** (complex reasoning) |

If framework is claude_sdk: always add `prompt_caching: true` and `thinking: {type: adaptive}` to agent.yaml.

#### RAG

| Signals | Recommendation |
|---|---|
| DATA_FLAGS contains (a) unstructured docs | **Vector DB RAG** — pgvector default; Pinecone/Weaviate for > 1M docs |
| DATA_FLAGS contains (c) knowledge graph | **Graph RAG** — Neo4j + LangGraph integration |
| DATA_FLAGS contains (a) + (c) | **Hybrid RAG** |
| DATA_FLAGS contains (b) only | **SQL tool** — direct DB access via typed tool function, not RAG |
| DATA_FLAGS is (d) or (e) | **None** |

#### Memory

| Signals | Recommendation |
|---|---|
| SCALE_PROFILE is (a) and TECHNICAL_USE_CASE implies per-session context | **Short-term (Redis)** |
| BUSINESS_GOAL implies cross-session user state or preferences | **Long-term (PostgreSQL)** |
| Both signals present | **Short-term (Redis) + Long-term (PostgreSQL)** |
| SCALE_PROFILE is (b) batch or agent is stateless | **None** |

#### MCP / A2A

| Signals | Recommendation |
|---|---|
| DATA_FLAGS contains (d), or TECHNICAL_USE_CASE names specific external tools/APIs | **MCP servers** |
| TECHNICAL_USE_CASE mentions delegating to sub-agents or specialized agents | **A2A protocol** |
| Both signals | **MCP + A2A** |
| Neither | **Neither** |

#### Deployment

| Signals | Recommendation |
|---|---|
| CLOUD_PREFERENCE is AWS and SCALE_PROFILE is (a) real-time | **ECS Fargate** |
| CLOUD_PREFERENCE is AWS and SCALE_PROFILE is (c) event-driven | **Lambda** *(planned — flag as not yet available)* |
| CLOUD_PREFERENCE is GCP | **Cloud Run** |
| CLOUD_PREFERENCE is Local or (d) | **Docker Compose** |
| CLOUD_PREFERENCE is Azure | **Azure Container Apps** *(planned — flag as not yet available)* |

#### Eval Dimensions

Map BUSINESS_GOAL to eval dimensions by keyword:

| Keywords in BUSINESS_GOAL | Eval dimensions |
|---|---|
| support, helpdesk, tickets, customer | Deflection rate, escalation accuracy, CSAT proxy, PII non-leakage, response tone |
| financial, report, accounting, CFO | Numerical accuracy, schema correctness, completeness, hallucination rate |
| code, review, engineering, developer | Correctness, security (no injection), format compliance, test pass rate |
| research, analysis, knowledge, documents | Citation accuracy, hallucination rate, completeness, source relevance |
| pipeline, data, ETL, warehouse | Schema validation, row completeness, latency, error rate |
| sales, CRM, lead, email | Lead scoring accuracy, email tone, compliance (opt-out handling) |
| (no match) | Correctness, latency, tool call accuracy, hallucination rate |

---

### Step H: Present Recommendations Summary

Present this structured table. Replace each `[X]` with computed values from Step G.

> **Here's what I recommend for your agent:**
>
> | Dimension | Recommendation | Why |
> |---|---|---|
> | **Framework** | [FRAMEWORK — FULL/LOW CODE] | [1-sentence reasoning referencing STATE_FLAGS and TECHNICAL_USE_CASE] |
> | **Model** | [MODEL] | [1-sentence reasoning referencing SCALE_PROFILE and use case] |
> | **RAG** | [RAG TYPE or None] | [1-sentence reasoning referencing DATA_FLAGS] |
> | **Memory** | [MEMORY CONFIG or None] | [1-sentence reasoning referencing SCALE_PROFILE and BUSINESS_GOAL] |
> | **MCP/A2A** | [MCP / A2A / Both / Neither] | [1-sentence reasoning referencing DATA_FLAGS and TECHNICAL_USE_CASE] |
> | **Deployment** | [DEPLOY TARGET] | [1-sentence reasoning referencing CLOUD_PREFERENCE and SCALE_PROFILE] |
> | **Eval dimensions** | [COMMA-SEPARATED LIST] | Derived from: "[BUSINESS_GOAL excerpt]" |
>
> **Any changes?** Type a dimension name and your preferred value, or say "looks good" to proceed.

Process any overrides and update the recommendation. Then ask: "What should we call this agent?" and "Team name and owner email?" (default from git config).

---

### Step I: Advisory Confirm

Present the final confirmed choices in a summary table (same format as Step 1-Confirm). Ask: "Ready to generate your project?"

Wait for confirmation. Then proceed to **Step 2: Generate Project**.

---
```

- [ ] **Step 2: Run advisory tests**

```bash
python -m pytest tests/unit/test_agent_build_skill.py::test_advisory_path_present \
  tests/unit/test_agent_build_skill.py::test_all_six_advisory_questions_present \
  tests/unit/test_agent_build_skill.py::test_recommendation_dimensions_present -v
```

Expected: All 3 PASS.

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/agent-build.md
git commit -m "feat(skill): advisory interview + recommendation engine in /agent-build"
```

---

### Task 4: Add new scaffold generation instructions — Step 2

**Files:**
- Modify: `.claude/commands/agent-build.md` (append Step 2: Generate Project)

- [ ] **Step 1: Append Step 2 (convergence point + all new scaffold outputs)**

Append to `.claude/commands/agent-build.md`:

```markdown
---

## Step 2: Generate Project

Both paths converge here. Create the project at `./<agent-name>/` relative to the user's current working directory. Generate ALL applicable files below.

### 2a. `agent.yaml`
Must include: name, version (0.1.0), description, team, owner, tags, framework, model.primary (from choice or recommendation), guardrails: [pii_detection, content_filter], deploy.cloud, deploy.runtime (infer from cloud or recommendation).

If claude_sdk: also include:
```yaml
claude_sdk:
  thinking:
    type: adaptive
  prompt_caching: true
```
If RAG recommended: add knowledge_bases section with a placeholder registry ref.
System prompt (`prompts.system`) must be **tailored to the stated purpose** — never a placeholder.

### 2b. `agent.py`
Working agent code for the chosen framework. Wire tool stubs matching described tools (not generic `get_weather`). Include `if __name__ == "__main__":` block.

- **LangGraph:** `StateGraph` with typed `AgentState`, tools node, `MemorySaver` checkpointer if checkpoints recommended
- **CrewAI:** `Agent` + `Task` + `Crew` with roles derived from `TECHNICAL_USE_CASE`
- **Claude SDK:** `anthropic.Anthropic()` client, tool-use loop checking `stop_reason == "tool_use"`, adaptive thinking wired
- **OpenAI Agents:** `Agent` with tools list, `handoff()` stubs if A2A recommended
- **Google ADK:** `adk.Agent` with session backend from recommendation
- **Custom:** minimal wrapper with HTTP entrypoint

### 2c. `requirements.txt`
- langgraph: `langgraph>=0.2.0`, `langchain-anthropic>=0.1.0`
- crewai: `crewai>=0.80.0`, `crewai-tools>=0.14.0`
- claude_sdk: `anthropic>=0.40.0`
- openai_agents: `openai-agents>=0.0.7`
- google_adk: `google-adk>=1.0.0`
- If Vector RAG: `pgvector>=0.3.0`, `sqlalchemy>=2.0.0`
- If Graph RAG: `neo4j>=5.0.0`, `langchain-community>=0.0.1`
- If Redis: `redis>=5.0.0`
- Always: `agentbreeder-sdk>=1.5.0`

### 2d. `.env.example`
- langgraph/crewai: `OPENAI_API_KEY=sk-...`
- claude_sdk: `ANTHROPIC_API_KEY=sk-ant-...`
- google_adk: `GOOGLE_API_KEY=AIza...`
- If Vector RAG: `DATABASE_URL=postgresql://localhost:5432/agentdb`
- If Redis: `REDIS_URL=redis://localhost:6379`
- Always: `AGENTBREEDER_ENV=development`

### 2e. `Dockerfile`
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "print('ok')"
CMD ["python", "agent.py"]
```
For google_adk: `CMD ["adk", "api_server", "--host", "0.0.0.0", "--port", "8080"]`

### 2f. `docker-compose.yml`
Base:
```yaml
version: "3.8"
services:
  agent:
    build: .
    ports: ["8080:8080"]
    env_file: [.env]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "print('ok')"]
      interval: 30s
      timeout: 5s
      retries: 3
```
If Redis: add `redis: {image: redis:7-alpine, ports: ["6379:6379"]}` service.
If Vector RAG: add `postgres: {image: pgvector/pgvector:pg16, environment: {POSTGRES_DB: agentdb, POSTGRES_USER: agent, POSTGRES_PASSWORD: agent}, ports: ["5432:5432"]}` service.

### 2g. `.agentbreeder/layout.json`
Always generate. Agent at (400,300), model at (400,100), prompt at (150,300). Tools fan right at (650, 200+i×100). RAG node at (150,500) if applicable. Memory node at (650,500) if applicable.

```json
{
  "version": "1.0",
  "canvas": {"zoom": 1.0, "x": 0, "y": 0},
  "nodes": [
    {"id": "agent", "type": "agent", "position": {"x": 400, "y": 300}, "data": {"ref": "agent.yaml"}},
    {"id": "model", "type": "model", "position": {"x": 400, "y": 100}, "data": {"ref": "model.primary"}},
    {"id": "prompt", "type": "prompt", "position": {"x": 150, "y": 300}, "data": {"ref": "prompts.system"}}
  ],
  "edges": [
    {"id": "e-model-agent", "source": "model", "target": "agent"},
    {"id": "e-prompt-agent", "source": "prompt", "target": "agent"}
  ]
}
```

### 2h. `memory/config.py` — ONLY if memory recommended

```python
"""Memory configuration for <agent-name>."""
import os

# Short-term (Redis) — include only if short-term recommended
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

def get_redis_client():
    import redis
    return redis.from_url(REDIS_URL, decode_responses=True)

# Long-term (PostgreSQL) — include only if long-term recommended
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/agentdb")

def get_db_url() -> str:
    return DATABASE_URL
```
Omit Redis section if only long-term. Omit PostgreSQL section if only short-term.

### 2i. `rag/index.py` — ONLY if RAG recommended

For **Vector RAG**:
```python
"""Vector RAG index setup using pgvector."""
from sqlalchemy import create_engine, text
import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost:5432/agentdb")

def setup_vector_index():
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS document_embeddings (
                id SERIAL PRIMARY KEY,
                content TEXT NOT NULL,
                embedding vector(1536),
                metadata JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_embedding ON document_embeddings "
            "USING ivfflat (embedding vector_cosine_ops)"
        ))
        conn.commit()

def search(query_embedding: list[float], limit: int = 5) -> list[dict]:
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT content, metadata,
                       1 - (embedding <=> :embedding) AS similarity
                FROM document_embeddings
                ORDER BY embedding <=> :embedding
                LIMIT :limit
            """),
            {"embedding": str(query_embedding), "limit": limit}
        )
        return [dict(row._mapping) for row in result]
```

For **Graph RAG**:
```python
"""Graph RAG index setup using Neo4j."""
from neo4j import GraphDatabase
import os

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

def get_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def search(entity: str, depth: int = 2) -> list[dict]:
    """Traverse the knowledge graph from an entity up to depth hops."""
    driver = get_driver()
    with driver.session() as session:
        result = session.run(
            "MATCH (n {name: $entity})-[*1..$depth]-(related) "
            "RETURN n, related LIMIT 20",
            entity=entity, depth=depth
        )
        return [dict(record) for record in result]
```

### 2j. `rag/ingest.py` — ONLY if Vector RAG recommended

```python
"""Document ingestion for <agent-name> RAG index.

Usage: python rag/ingest.py --path /path/to/docs
"""
import argparse
from pathlib import Path
from rag.index import setup_vector_index
import os

def ingest_file(path: Path) -> None:
    content = path.read_text()
    # Replace with your embedding model: e.g. openai.embeddings.create(...)
    print(f"[ingest] {path.name} — {len(content)} chars (wire up embedding model)")

def main():
    parser = argparse.ArgumentParser(description="Ingest documents into RAG index")
    parser.add_argument("--path", required=True, help="Path to documents directory")
    args = parser.parse_args()
    setup_vector_index()
    for p in Path(args.path).rglob("*.txt"):
        ingest_file(p)
    print("Ingestion complete.")

if __name__ == "__main__":
    main()
```

### 2k. `mcp/servers.yaml` — ONLY if MCP recommended

```yaml
# MCP server references for <agent-name>
# Register with: agentbreeder scan
mcp_servers:
  # - name: <tool-name>-mcp
  #   registry_ref: tools/<tool-name>-mcp
  #   description: <what it does>
```

### 2l. `tests/test_agent.py`

```python
"""Smoke tests for <agent-name>."""
import importlib
from pathlib import Path

def test_agent_module_loads():
    mod = importlib.import_module("agent")
    assert mod is not None

def test_agent_yaml_exists():
    assert Path("agent.yaml").exists()

def test_env_example_exists():
    assert Path(".env.example").exists()

def test_layout_json_exists():
    assert Path(".agentbreeder/layout.json").exists()
```

### 2m. `tests/evals/eval_runner.py`

**LangGraph** (LangSmith):
```python
"""Eval runner for <agent-name> using LangSmith.
Usage: python tests/evals/eval_runner.py
Requires: LANGSMITH_API_KEY in .env
"""
from langsmith import Client
from langsmith.evaluation import evaluate

DATASET_NAME = "<agent-name>-evals"

def target(inputs: dict) -> dict:
    # TODO: wire up your agent
    # from agent import graph
    # result = graph.invoke({"messages": [{"role": "user", "content": inputs["input"]}]})
    # return {"output": result["messages"][-1]["content"]}
    return {"output": "placeholder — wire up your agent"}

def main():
    results = evaluate(target, data=DATASET_NAME, experiment_prefix="<agent-name>")
    print(results)

if __name__ == "__main__":
    main()
```

**Claude SDK** (Inspect AI):
```python
"""Eval runner for <agent-name> using Inspect AI.
Usage: inspect eval tests/evals/eval_runner.py
"""
from inspect_ai import task, Task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import model_graded_fact
from inspect_ai.solver import generate

@task
def agent_evals() -> Task:
    return Task(
        dataset=[
            # Sample(input="...", target="...")
        ],
        solver=[generate()],
        scorer=model_graded_fact(),
    )
```

**All other frameworks** (PromptFoo) — generate both `eval_runner.py` and `tests/evals/promptfooconfig.yaml`:
```python
"""Eval runner for <agent-name> using PromptFoo.
Usage: npx promptfoo eval --config tests/evals/promptfooconfig.yaml
"""
print("Run: npx promptfoo eval --config tests/evals/promptfooconfig.yaml")
```
```yaml
# tests/evals/promptfooconfig.yaml
description: "<agent-name> evals"
prompts: ["{{input}}"]
providers:
  - id: http
    config:
      url: http://localhost:8080/run
      method: POST
      body: {input: "{{input}}"}
tests:
  # - vars: {input: "example input"}
  #   assert: [{type: contains, value: "expected output"}]
```

### 2n. `tests/evals/criteria.md`

Generate with the eval dimensions from Step G, tailored to BUSINESS_GOAL. Structure:
```markdown
# Eval Criteria — <agent-name>
Generated by /agent-build. Business goal: "<BUSINESS_GOAL>"

## Eval Dimensions

### <Dimension 1 from Step G>
<What constitutes pass vs fail for this agent's use case>

### <Dimension 2 from Step G>
<What constitutes pass vs fail>

[... one section per eval dimension ...]

## Starter Test Cases

| Input | Expected behavior | Eval dimension |
|---|---|---|
| <input derived from BUSINESS_GOAL> | <expected agent behavior> | <dimension> |
| <input derived from BUSINESS_GOAL> | <expected agent behavior> | <dimension> |
| <input derived from BUSINESS_GOAL> | <expected agent behavior> | <dimension> |
```
Generate at least 3 starter test cases derived from BUSINESS_GOAL and TECHNICAL_USE_CASE.

---
```

- [ ] **Step 2: Run scaffold output tests**

```bash
python -m pytest tests/unit/test_agent_build_skill.py::test_new_scaffold_outputs_present -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/agent-build.md
git commit -m "feat(skill): new scaffold outputs (memory, rag, mcp, evals) in /agent-build"
```

---

### Task 5: Add IDE Config Generation Instructions

**Files:**
- Modify: `.claude/commands/agent-build.md` (append IDE config + ARCHITECT_NOTES + repo updates + post-gen summary + rules)

- [ ] **Step 1: Append IDE config sections, ARCHITECT_NOTES, repo updates, post-gen summary, and rules**

Append to `.claude/commands/agent-build.md`:

```markdown
### 2o. `CLAUDE.md` (inside agent project)

```markdown
# <agent-name> — Claude Code Context

## What This Agent Does
<BUSINESS_GOAL from advisory path, or PURPOSE from fast path>

## Stack
- **Framework:** <framework> (<Full Code / Low Code>) — <one-line framework description>
- **Model:** <model> — <key property, e.g., "best tool-use/speed tradeoff for interactive agents">
- **RAG:** <RAG type and lib> — see `rag/index.py` | None
- **Memory:** <memory config> — see `memory/config.py` | None
- **Deploy:** <deploy target>

## Rules for AI-Assisted Development
- New tools go in `tools/` as typed Python functions with docstrings
- Tests required for every new tool — see `tests/test_agent.py` for patterns
- Run `agentbreeder validate` before every commit
- Never modify `agent.yaml` model fields without re-running `tests/evals/`
- Never store PII in memory without a TTL
- Never bypass RBAC or eval gates
```

### 2p. `AGENTS.md` (inside agent project)

```markdown
# AGENTS.md — AI Skills for <agent-name>

## add-tool
Add a new tool to this agent.
Read `tools/<existing-tool>.py` first for patterns.
Files: `tools/`, `agent.yaml` (tools section), `tests/test_agent.py`

## update-prompt
Revise the system prompt. Read `ARCHITECT_NOTES.md` for original intent.
Files: `agent.yaml` (prompts.system)

## add-eval
Add a new eval test case. Read `tests/evals/criteria.md` for dimensions.
Files: `tests/evals/eval_runner.py`, `tests/evals/criteria.md`

## update-rag
Change RAG index or ingestion logic. Read `rag/index.py` first.
Files: `rag/index.py`, `rag/ingest.py`

## deploy
Validate and deploy.
Commands: `agentbreeder validate && agentbreeder deploy --target <deploy-target>`
```
Omit `update-rag` if no RAG. Omit memory-related skills if no memory.

### 2q. `.cursorrules` (inside agent project)

Generate framework-specific content:

**LangGraph:**
```
# <agent-name> Cursor Rules — LangGraph

- State classes use TypedDict, never plain dict
- Nodes: `async def node_name(state: AgentState) -> AgentState`
- Use `interrupt()` for HITL — never polling loops
- Checkpoints: `MemorySaver` locally, `AsyncPostgresSaver` in production
- Never import LangGraph internals — use public API (`langgraph.graph`, `langgraph.checkpoint`)
- Every new node needs a unit test (`@pytest.mark.asyncio`)
- Never hardcode API keys — `os.getenv("OPENAI_API_KEY")`
- Never skip `agentbreeder validate` before deploy
- Never edit `.agentbreeder/layout.json` manually
```

**CrewAI:**
```
# <agent-name> Cursor Rules — CrewAI

- Agents need specific role, goal, backstory — no generic values
- Tasks need description and expected_output — no vague descriptions
- Tool functions must have `@tool` decorator and docstrings
- `Process.sequential` by default; `Process.hierarchical` only for complex delegation
- Test each agent's task independently before testing the full crew
- `AGENT_MODEL` and `AGENT_TEMPERATURE` set via env vars, not hardcoded
- Never skip `agentbreeder validate` before deploy
```

**Claude SDK:**
```
# <agent-name> Cursor Rules — Claude SDK

- Tool loop: always check `stop_reason == "tool_use"` before processing
- Adaptive thinking: do not disable — configured in `agent.yaml`
- Prompt caching: keep system prompt > 1024 tokens
- `max_tokens` must be set when thinking is enabled (≥ 2000 for complex tasks)
- Mock `anthropic.Anthropic()` in tests — never make real API calls in tests
- Never hardcode `api_key` — always `os.getenv("ANTHROPIC_API_KEY")`
- Never disable guardrails in production
```

**Google ADK:**
```
# <agent-name> Cursor Rules — Google ADK

- Tools need `@adk.tool` decorator with Google-style docstrings (Args/Returns)
- Session backend set in `agent.yaml` (google_adk.session_backend), not in code
- Serve with `adk api_server`, not `python agent.py`
- Memory service configured in `agent.yaml` (google_adk.memory_service)
- Test with `adk run` locally before containerizing
- Never hardcode project ID or region — use env vars
```

**OpenAI Agents:**
```
# <agent-name> Cursor Rules — OpenAI Agents SDK

- Tools: typed Python functions with docstrings — SDK infers schema
- Handoffs: use `handoff()` for agent delegation — never call agents directly
- Prefer `Runner.run_streamed()` for interactive agents
- Guardrails as `input_guardrails` / `output_guardrails` on Agent constructor
- Model name on `Agent(model=...)` must match `agent.yaml model.primary`
- Never skip `agentbreeder validate` before deploy
```

### 2r. `.antigravity.md` (inside agent project)

```markdown
# .antigravity.md — What NOT To Do With <agent-name>

## Model
- Do NOT swap to a cheaper model without re-running `tests/evals/`
- Do NOT disable adaptive thinking in production (Claude SDK only)
- Do NOT change `model.primary` in `agent.yaml` without updating `.cursorrules`

## Memory
- Do NOT store PII in Redis without a TTL (`ex=3600` minimum)
- Do NOT let long-term memory grow unbounded — enforce retention policy in `memory/config.py`

## Tools
- Do NOT add tools without tests in `tests/`
- Do NOT call external APIs directly in `agent.py` — always via `tools/`
- Do NOT expose internal errors or stack traces via tool outputs

## RAG
- Do NOT ingest documents without content validation
- Do NOT skip the health check in `docker-compose.yml` — pgvector needs warm-up time

## Deploy
- Do NOT deploy without `agentbreeder validate` passing
- Do NOT hardcode region, account IDs, or project names — use env vars
- Do NOT skip the health check endpoint — ECS/Cloud Run need it for traffic routing

## Evals
- Do NOT merge changes that drop any eval dimension below its baseline
- Do NOT add eval test cases that always pass — they provide no signal
```

### 2s. `ARCHITECT_NOTES.md` — ADVISORY PATH ONLY

```markdown
# Architect Notes — <agent-name>
Generated by /agent-build on <ISO-8601 date>. Edit freely — this file is for humans.

## Business Goal
<BUSINESS_GOAL verbatim>

## Technical Use Case
<TECHNICAL_USE_CASE verbatim>

## Why <Framework> (<Full Code / Low Code>)?
<Reference specific STATE_FLAGS that drove the decision. E.g.: "You selected checkpoints (b) and HITL (c). LangGraph is the only framework with native StateGraph checkpointing and interrupt() for HITL. CrewAI would require custom workarounds.">

## Why <Model>?
<Reference SCALE_PROFILE and use case. E.g.: "Real-time interactive (a) + tool use. Opus is overkill for latency-sensitive tool-calling; Haiku lacks reasoning depth for multi-step planning.">

## Why <RAG type>? / Why No RAG?
<Reference DATA_FLAGS>

## Why <Memory config>? / Why No Memory?
<Reference SCALE_PROFILE and BUSINESS_GOAL signals>

## Why <MCP/A2A/Neither>?
<Reference DATA_FLAGS and TECHNICAL_USE_CASE>

## Why <Deploy target>?
<Reference CLOUD_PREFERENCE and SCALE_PROFILE>

## Eval Dimensions
<List each dimension with one-line rationale derived from BUSINESS_GOAL keywords>

## Overrides Made
<If user overrode any item: dimension → original → override → user's stated reason>
<If no overrides: "None — all recommendations accepted as-is">
```

---

## Step 3: AgentBreeder Repo Updates — ADVISORY PATH ONLY

### 3a. Update `AGENT.md`
In the `## 🏗️ BUILD Skills` section, append after the last existing build skill (before `## 🧪 TEST Skills`):

```
### `build:agent-scaffold`
**Purpose:** Run the `/agent-build` advisory interview and scaffold a complete agent project with IDE config files, eval harness, and all recommended infrastructure stubs.

**Skill Prompt:**
```
Invoke /agent-build. The skill will:
1. Ask 6 questions (business goal, technical use case, state complexity, team/org, data access, scale)
2. Recommend: framework+mode, model, RAG, memory, MCP/A2A, deployment, eval dimensions
3. Present Recommendations Summary with per-item reasoning + allow overrides
4. Scaffold: agent.yaml, agent.py, memory/, rag/, mcp/, tests/evals/, ARCHITECT_NOTES.md,
   CLAUDE.md, AGENTS.md, .cursorrules, .antigravity.md
5. Update AGENT.md and CLAUDE.md in the AgentBreeder repo

Output: ./<agent-name>/ relative to cwd
```

**MCP Tools:** `filesystem`, `sequential-thinking`

---
```

### 3b. Update `CLAUDE.md`
Append after the `engine/runtimes/templates/` entry in the Project Structure section:

```
> **IDE config files:** The `/agent-build` skill generates per-agent `CLAUDE.md`, `AGENTS.md`,
> `.cursorrules`, and `.antigravity.md` inside each scaffolded agent project. These follow the
> patterns established in this file.
```

---

## Step 4: Post-Generation Summary

After writing all files, print:

```
Project created! <framework-emoji> <agent-name>

Files generated:
  agent.yaml                  — AgentBreeder config
  agent.py                    — Agent code
  requirements.txt            — Python dependencies
  Dockerfile + docker-compose — Container build & local run
  .env.example                — Environment template
  .agentbreeder/layout.json   — Visual builder metadata
  tools/                      — Tool stubs
  memory/config.py            — Memory setup          [if applicable]
  rag/index.py + ingest.py    — RAG index + ingestion [if applicable]
  mcp/servers.yaml            — MCP server refs       [if applicable]
  tests/test_agent.py         — Smoke tests
  tests/evals/                — Eval harness + criteria
  ARCHITECT_NOTES.md          — Why each choice was made [advisory path]
  CLAUDE.md                   — AI context for this agent
  AGENTS.md                   — AI skill roster
  .cursorrules                — Cursor IDE rules
  .antigravity.md             — Hard constraints
  README.md                   — Quick start guide

Next steps:
  $ cd <agent-name>
  $ pip install -r requirements.txt
  $ cp .env.example .env        # add your API keys
  $ python agent.py             # test locally
  $ docker compose up           # run in container
  $ agentbreeder validate       # validate config
  $ agentbreeder deploy         # deploy to <cloud>

Tier mobility:
  No Code  — open in AgentBreeder dashboard to edit visually
  Low Code — edit agent.yaml in any editor
  Full Code — modify agent.py directly
```

---

## Rules

- NEVER skip `.agentbreeder/layout.json` — required for No Code tier interop
- NEVER put layout metadata in `agent.yaml`
- NEVER use generic tool stubs (like `get_weather`) if the user described specific tools
- ALWAYS generate a tailored system prompt based on stated purpose — never a placeholder
- ALWAYS validate agent name format before proceeding
- ALWAYS include guardrails (pii_detection, content_filter) by default
- ALWAYS generate CLAUDE.md, AGENTS.md, .cursorrules, .antigravity.md for every project
- ONLY generate ARCHITECT_NOTES.md for advisory path projects
- ONLY update AGENT.md and CLAUDE.md in the AgentBreeder repo for advisory path projects
- The fast path must produce identical output to the old skill for identical inputs (zero regression)
```

- [ ] **Step 2: Run full structural test suite**

```bash
python -m pytest tests/unit/test_agent_build_skill.py -v
```

Expected: ALL tests PASS.

- [ ] **Step 3: Check skill file line count**

```bash
wc -l .claude/commands/agent-build.md
grep -c "^##\|^###" .claude/commands/agent-build.md
```

Expected: > 400 lines, > 20 sections.

- [ ] **Step 4: Commit**

```bash
git add .claude/commands/agent-build.md
git commit -m "feat(skill): IDE config generation + ARCHITECT_NOTES + repo update instructions"
```

---

### Task 6: Update AGENT.md and CLAUDE.md

**Files:**
- Modify: `AGENT.md` (add build:agent-scaffold before `## 🧪 TEST Skills`)
- Modify: `CLAUDE.md` (append note to Project Structure section)

- [ ] **Step 1: Insert build:agent-scaffold into AGENT.md**

Find the line `## 🧪 TEST Skills` (line 360). Insert before it:

```markdown
### `build:agent-scaffold`
**Purpose:** Run the `/agent-build` advisory interview and scaffold a complete agent project with IDE config files, eval harness, and all recommended infrastructure stubs.

**Skill Prompt:**
```
Invoke /agent-build. The skill will:
1. Ask 6 questions: business goal, technical use case, state complexity, team/org context,
   data access, scale profile
2. Recommend: framework + mode, model, RAG type, memory, MCP/A2A, deployment, eval dimensions
3. Present Recommendations Summary with per-item reasoning; let the user override any item
4. Scaffold a complete agent project including ARCHITECT_NOTES.md, CLAUDE.md, AGENTS.md,
   .cursorrules, .antigravity.md, tests/evals/, and all recommended infra stubs (memory/, rag/, mcp/)
5. Update AGENT.md and CLAUDE.md in the AgentBreeder repo (advisory path only)

Output location: ./<agent-name>/ relative to current working directory
```

**MCP Tools:** `filesystem` (write generated files), `sequential-thinking` (recommendation reasoning)

---
```

- [ ] **Step 2: Append note to CLAUDE.md**

Find the `engine/runtimes/templates/` entry in the Project Structure section. After the `templates` line, append:

```markdown
> **IDE config files (per-agent):** The `/agent-build` skill generates `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, and `.antigravity.md` inside each scaffolded agent project. These follow the patterns in this file.
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/unit/ -v --tb=short 2>&1 | tail -20
```

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add AGENT.md CLAUDE.md
git commit -m "docs: add build:agent-scaffold to AGENT.md + IDE config note to CLAUDE.md"
```

---

### Task 7: Final check and push

- [ ] **Step 1: Run all unit tests**

```bash
python -m pytest tests/unit/ -v 2>&1 | tail -20
```

Expected: All pass.

- [ ] **Step 2: Spot-check skill file completeness**

```bash
grep -n "Step 0\|FAST PATH\|ADVISORY PATH\|Step A\|Step B\|Step C\|Step D\|Step E\|Step F\|Step G\|Step H\|Step I\|Step 2\|Step 3\|Step 4\|Rules" .claude/commands/agent-build.md
```

Expected: All sections present with correct line numbers.

- [ ] **Step 3: Verify no regressions in existing tests**

```bash
python -m pytest tests/unit/ tests/integration/ -v --tb=short -x 2>&1 | tail -30
```

Expected: Pass (or only pre-existing failures unrelated to this change).

- [ ] **Step 4: Push**

```bash
git push origin main
```

---

## Self-Review

**Spec coverage check:**
- [x] Mode fork (Step 0) — Task 2
- [x] Fast path preserved — Task 2 (byte-for-byte existing content)
- [x] 6-question advisory interview (A–F) — Task 3
- [x] Recommendation engine (all 7 dimensions) — Task 3, Step G
- [x] Recommendations Summary with per-item reasoning — Task 3, Step H
- [x] User override handling — Task 3, Step H
- [x] memory/ directory — Task 4 (2h)
- [x] rag/index.py + ingest.py — Task 4 (2i, 2j)
- [x] mcp/servers.yaml — Task 4 (2k)
- [x] tests/evals/ harness (LangSmith/Inspect AI/PromptFoo) — Task 4 (2m)
- [x] tests/evals/criteria.md with use-case dimensions — Task 4 (2n)
- [x] ARCHITECT_NOTES.md (advisory path only) — Task 5 (2s)
- [x] CLAUDE.md, AGENTS.md, .cursorrules, .antigravity.md — Task 5 (2o–2r)
- [x] AgentBreeder repo updates (AGENT.md, CLAUDE.md) — Task 5 (Step 3), Task 6
- [x] Post-generation summary — Task 5 (Step 4)
- [x] Structural tests gate CI — Task 1

**No placeholders:** All code blocks are complete. All templates have explicit structure with field names and example content.

**Type consistency:** No cross-task type references (this is a markdown skill, not Python code). Template variable names (`BUSINESS_GOAL`, `TECHNICAL_USE_CASE`, `STATE_FLAGS`, `CLOUD_PREFERENCE`, `LANGUAGE_PREFERENCE`, `DATA_FLAGS`, `SCALE_PROFILE`) are used consistently across Steps G, H, and ARCHITECT_NOTES.md.
