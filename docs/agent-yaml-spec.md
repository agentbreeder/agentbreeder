> Extracted from CLAUDE.md (thin-router refactor). Read on demand — see CLAUDE.md router for when.

## 📝 The `agent.yaml` Specification

This is the canonical YAML config. AI assistants must understand every field.

```yaml
# Identity
name: customer-support-agent          # Required. Slug-friendly name.
version: 1.0.0                        # Required. SemVer.
description: "Handles tier-1 support" # Optional but encouraged.
team: customer-success                # Required. Must match a team in registry.
owner: alice@company.com              # Required. Email of responsible engineer.
tags: [support, zendesk, production]  # Optional. Used for discovery.

# Model Configuration
model:
  primary: claude-sonnet-4            # Required. Registry ref or provider/model-id.
  fallback: gpt-4o                    # Optional. Used if primary unavailable.
  gateway: litellm                    # Optional. Defaults to org gateway setting.
  temperature: 0.7                    # Optional. Model parameter.
  max_tokens: 4096                    # Optional. Model parameter.

# Framework
framework: langgraph                  # Required. One of: langgraph | crewai | claude_sdk
                                      #   | openai_agents | google_adk | custom

# Tools & MCP Servers
tools:
  - ref: tools/zendesk-mcp            # Registry reference (recommended)
  - ref: tools/order-lookup
  - name: search                      # Inline definition (for simple tools)
    type: function
    description: "Search knowledge base"
    schema: { ... }                   # OpenAPI-compatible schema

# Knowledge Bases
knowledge_bases:
  - ref: kb/product-docs              # Registry reference
  - ref: kb/return-policy

# Prompts
prompts:
  system: prompts/support-system-v3   # Registry reference (versioned)
  # Or inline:
  # system: "You are a helpful customer support agent..."

# Guardrails
guardrails:
  - pii_detection                     # Built-in: strips PII from outputs
  - hallucination_check               # Built-in: flags low-confidence responses
  - content_filter                    # Built-in: blocks harmful content
  # Custom guardrail:
  # - name: custom_check
  #   endpoint: https://guardrails.company.com/check

# Deployment Configuration
deploy:
  cloud: aws                          # Required. One of: aws | gcp | azure | kubernetes | local | claude-managed
  runtime: ecs-fargate                # Optional. Defaults per cloud:
                                      #   aws → ecs-fargate  (also: app-runner)
                                      #   gcp → cloud-run
                                      #   azure → container-apps
                                      #   kubernetes → deployment
                                      #   local → docker-compose
                                      #   claude-managed → (no container; Anthropic manages runtime)
  region: us-east-1                   # Optional. Cloud-specific.
  scaling:
    min: 1
    max: 10
    target_cpu: 70                    # Percentage for autoscaling trigger
  resources:
    cpu: "1"                          # vCPU units
    memory: "2Gi"                     # Memory
  env_vars:                           # Non-secret environment variables
    LOG_LEVEL: info
    ENVIRONMENT: production
  secrets:                            # Secret references (from AWS Secrets Manager / GCP Secret Manager)
    - ZENDESK_API_KEY
    - OPENAI_API_KEY

# Access Control (optional — defaults to team's policy)
access:
  visibility: team                    # One of: public | team | private
  allowed_callers:                    # Optional. Restrict who can call this agent.
    - team:engineering
    - team:customer-success
  require_approval: false             # If true, deploys require admin approval

# Framework-Specific Configuration (optional — only read by the matching runtime)

# Claude SDK — adaptive thinking + prompt caching
claude_sdk:
  thinking:
    type: adaptive                    # "adaptive" (default) | "enabled"
    effort: high                      # "low" | "medium" | "high"
  prompt_caching: true                # Cache system prompt (requires ≥8 192 chars for Sonnet)

# CrewAI — no extra config needed; AGENT_MODEL/AGENT_TEMPERATURE are auto-injected
# crewai: {}

# Google ADK — session and memory backends
google_adk:
  session_backend: memory             # "memory" | "database" | "vertex_ai"
  session_db_url: ""                  # Required if session_backend is "database"
  memory_service: memory              # "memory" | "vertex_ai_bank" | "vertex_ai_rag"
  artifact_service: memory            # "memory" | "gcs"
  gcs_bucket: ""                      # Required if artifact_service is "gcs"

# Claude Managed Agents — only read when deploy.cloud == "claude-managed"
# No container is built. Anthropic manages the runtime.
claude_managed:
  environment:
    networking: unrestricted          # "unrestricted" | "restricted"
  tools:
    - type: agent_toolset_20260401    # Full built-in toolset (default)
```

---
