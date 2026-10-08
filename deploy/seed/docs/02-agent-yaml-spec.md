# The agent.yaml Specification

Every AgentBreeder agent is defined by an `agent.yaml` file. This is the canonical format.

## Required fields

```yaml
name: my-agent          # slug-friendly, lowercase-alphanumeric-with-hyphens
version: 1.0.0          # SemVer
team: engineering        # must match a registered team
owner: alice@company.com # email of responsible engineer
framework: langgraph     # langgraph | crewai | claude_sdk | openai_agents | google_adk | custom
```

## Model configuration

```yaml
model:
  primary: claude-sonnet-4      # required — registry ref or provider/model-id
  fallback: gpt-4o              # optional — used if primary unavailable
  gateway: litellm              # optional — defaults to org gateway
  temperature: 0.7
  max_tokens: 4096
```

## Tools

```yaml
tools:
  - ref: tools/zendesk-mcp        # registry reference (recommended)
  - name: search                  # inline definition
    type: function
    description: "Search the knowledge base"
    schema:
      type: object
      properties:
        query: {type: string}
      required: [query]
```

Retrieval (RAG) is not an `agent.yaml` field. Implement it in your agent code
or expose it as a tool or MCP server.

## Prompts

```yaml
prompts:
  system: prompts/support-v3      # registry reference (versioned)
  # Or inline:
  # system: "You are a helpful assistant..."
```

## Guardrails

```yaml
guardrails:
  - pii_detection         # strips PII from outputs
  - hallucination_check   # flags low-confidence responses
  - content_filter        # blocks harmful content
```

## Deploy configuration

```yaml
deploy:
  cloud: aws                      # aws | gcp | azure | kubernetes | local | claude-managed
  runtime: ecs-fargate            # defaults: aws→ecs-fargate, gcp→cloud-run, azure→container-apps
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

## Access control

```yaml
access:
  visibility: team                # public | team | private
  allowed_callers:
    - team:engineering
  require_approval: false
```
