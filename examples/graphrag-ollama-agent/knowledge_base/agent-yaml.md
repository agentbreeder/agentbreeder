# agent.yaml Specification

The agent.yaml file is the single config file that defines an agent.

## Required Fields

- `name` — slug-friendly agent name
- `version` — SemVer (e.g. "1.0.0")
- `team` — must match a team registered in the registry
- `owner` — email of the responsible engineer
- `framework` — one of: langgraph, crewai, claude_sdk, openai_agents, google_adk, custom
- `model.primary` — model reference (e.g. claude-sonnet-4, gpt-4o, ollama/qwen2.5:7b)
- `deploy.cloud` — one of: aws, gcp, azure, kubernetes, local, claude-managed

## Model Configuration

```yaml
model:
  primary: claude-sonnet-4
  fallback: gpt-4o          # used if primary unavailable
  gateway: litellm          # optional org gateway
  temperature: 0.7
  max_tokens: 4096
```

## Knowledge Bases

Knowledge bases are referenced from the registry:

```yaml
knowledge_bases:
  - ref: kb/product-docs
  - ref: kb/return-policy
```

## Deployment Configuration

```yaml
deploy:
  cloud: aws
  runtime: ecs-fargate      # default for aws
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
    - ZENDESK_API_KEY
```

## Access Control

```yaml
access:
  visibility: team          # public | team | private
  allowed_callers:
    - team:engineering
  require_approval: false
```

## RAG Knowledge Base Schema

When creating a knowledge base with GraphRAG:

```yaml
index_type: graph           # vector | graph | hybrid
embedding_model: ollama/nomic-embed-text
entity_model: ollama/qwen2.5:7b
max_hops: 2
```
