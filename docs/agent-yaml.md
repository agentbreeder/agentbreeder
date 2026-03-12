# agent.yaml Reference

The `agent.yaml` file is the canonical configuration for an Agent Garden agent. It defines everything needed to build, deploy, and govern an agent.

## Minimal Example

```yaml
name: my-agent
version: 1.0.0
team: engineering
owner: dev@company.com
framework: langgraph

model:
  primary: claude-sonnet-4

deploy:
  cloud: local
```

## Complete Example

```yaml
spec_version: v1

name: customer-support-agent
version: 1.0.0
description: "Handles tier-1 customer support tickets"
team: customer-success
owner: alice@company.com
tags: [support, zendesk, production]

framework: langgraph

model:
  primary: claude-sonnet-4
  fallback: gpt-4o
  gateway: litellm
  temperature: 0.7
  max_tokens: 4096

tools:
  - ref: tools/zendesk-mcp
  - ref: tools/order-lookup
  - name: search
    type: function
    description: "Search knowledge base"
    schema: {}

knowledge_bases:
  - ref: kb/product-docs
  - ref: kb/return-policy

prompts:
  system: prompts/support-system-v3

guardrails:
  - pii_detection
  - hallucination_check
  - content_filter
  - name: custom_check
    endpoint: https://guardrails.company.com/check

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
  env_vars:
    LOG_LEVEL: info
    ENVIRONMENT: production
  secrets:
    - ZENDESK_API_KEY
    - OPENAI_API_KEY

access:
  visibility: team
  allowed_callers:
    - team:engineering
    - team:customer-success
  require_approval: false
```

---

## Field Reference

### Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `spec_version` | string | No | Schema version. Currently `v1`. |
| `name` | string | **Yes** | Agent name. Slug-friendly: lowercase alphanumeric and hyphens, 2–63 characters. Must start and end with alphanumeric. |
| `version` | string | **Yes** | Semantic version (`X.Y.Z`). |
| `description` | string | No | Short description, max 500 characters. |
| `team` | string | **Yes** | Owning team name. |
| `owner` | string | **Yes** | Email of the responsible engineer. |
| `tags` | string[] | No | Tags for discovery and search. |
| `framework` | string | **Yes** | Agent framework. See [Framework values](#framework). |

### `framework`

One of:

| Value | Framework |
|-------|-----------|
| `langgraph` | LangGraph (v0.1+) |
| `crewai` | CrewAI (v0.2) |
| `claude_sdk` | Anthropic Claude SDK (v0.2) |
| `openai_agents` | OpenAI Agents SDK (v0.2) |
| `google_adk` | Google Agent Development Kit (v0.2) |
| `custom` | Any Python/TS agent (v0.2) |

---

### `model`

Model configuration for the agent's LLM.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `primary` | string | **Yes** | Primary model. Registry reference or `provider/model-id`. |
| `fallback` | string | No | Fallback model if primary is unavailable. |
| `gateway` | string | No | Model gateway (e.g., `litellm`). Defaults to org setting. |
| `temperature` | number | No | Sampling temperature, 0–2. |
| `max_tokens` | integer | No | Maximum tokens per response. |

**Gateway values:**

| Gateway | Description |
|---------|-------------|
| `litellm` | Route through LiteLLM proxy |
| `ollama` | Route to local Ollama instance |
| _(unset)_ | Direct provider call via the provider abstraction layer |

The provider abstraction layer (`engine/providers/`) supports OpenAI and Ollama natively, with automatic fallback chains when `fallback` is specified.

**Examples:**
```yaml
model:
  primary: claude-sonnet-4

model:
  primary: claude-sonnet-4
  fallback: gpt-4o
  gateway: litellm
  temperature: 0.7
  max_tokens: 4096

# Local development with Ollama
model:
  primary: ollama/llama3
  gateway: ollama
```

---

### `tools`

List of tools and MCP servers available to the agent.

Each tool is either a **registry reference** or an **inline definition**:

```yaml
tools:
  # Registry reference (recommended)
  - ref: tools/zendesk-mcp

  # Inline definition
  - name: search
    type: function
    description: "Search knowledge base"
    schema: {}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ref` | string | No | Registry reference path. |
| `name` | string | No | Tool name (for inline definitions). |
| `type` | string | No | Tool type. Currently only `function`. |
| `description` | string | No | Tool description. |
| `schema` | object | No | OpenAPI-compatible schema. |

---

### `knowledge_bases`

List of knowledge base references.

```yaml
knowledge_bases:
  - ref: kb/product-docs
  - ref: kb/return-policy
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ref` | string | **Yes** | Registry reference path. |

---

### `prompts`

Prompt configuration.

```yaml
# Registry reference
prompts:
  system: prompts/support-system-v3

# Inline
prompts:
  system: "You are a helpful customer support agent..."
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `system` | string | No | System prompt — registry reference or inline string. |

---

### `guardrails`

List of guardrails to enforce on agent outputs. Can be built-in names or custom endpoints.

```yaml
guardrails:
  - pii_detection           # Built-in
  - hallucination_check     # Built-in
  - content_filter          # Built-in
  - name: custom_check      # Custom
    endpoint: https://guardrails.company.com/check
```

**Built-in guardrails:**

| Name | Description |
|------|-------------|
| `pii_detection` | Strips PII from outputs |
| `hallucination_check` | Flags low-confidence responses |
| `content_filter` | Blocks harmful content |

**Custom guardrails:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | **Yes** | Guardrail name. |
| `endpoint` | string | No | URL of the guardrail service. |

---

### `deploy`

Deployment configuration.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `cloud` | string | **Yes** | — | Target platform: `local`, `kubernetes`, `aws`, `gcp`. |
| `runtime` | string | No | Per cloud | Deployment runtime. See defaults below. |
| `region` | string | No | — | Cloud region (e.g., `us-east-1`). |
| `scaling` | object | No | See below | Auto-scaling configuration. |
| `resources` | object | No | See below | CPU and memory allocation. |
| `env_vars` | map | No | — | Non-secret environment variables. |
| `secrets` | string[] | No | — | Secret references (from cloud secret managers). |

**Default runtimes by cloud:**

| Cloud | Default Runtime |
|-------|----------------|
| `local` | `docker-compose` |
| `kubernetes` | `deployment` |
| `aws` | `ecs-fargate` |
| `gcp` | `cloud-run` |

#### `deploy.scaling`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `min` | integer | `1` | Minimum instances (0 = scale to zero). |
| `max` | integer | `10` | Maximum instances. |
| `target_cpu` | integer | `70` | CPU % threshold to trigger scaling (1–100). |

#### `deploy.resources`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `cpu` | string | `"0.5"` | vCPU units. |
| `memory` | string | `"1Gi"` | Memory allocation. |

---

### `access`

Access control configuration. Optional — defaults to team's policy.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `visibility` | string | `team` | One of: `public`, `team`, `private`. |
| `allowed_callers` | string[] | — | Restrict who can call this agent (e.g., `team:engineering`). |
| `require_approval` | boolean | `false` | If true, deploys require admin approval. |

---

---

### CLI Deploy Targets

The `--target` flag on `garden deploy` maps to cloud + runtime combinations:

```bash
garden deploy --target local         # docker-compose
garden deploy --target cloud-run     # GCP Cloud Run
```

---

## Validation

Validate your config without deploying:

```bash
garden validate ./agent.yaml
```

The JSON Schema is at [`engine/schema/agent.schema.json`](../engine/schema/agent.schema.json).

## Name Constraints

- Lowercase alphanumeric and hyphens only
- Must start and end with an alphanumeric character
- Length: 2–63 characters
- Pattern: `^[a-z0-9][a-z0-9-]*[a-z0-9]$`

Valid: `my-agent`, `customer-support-v2`, `data-monitor`
Invalid: `-my-agent`, `My_Agent`, `a`
