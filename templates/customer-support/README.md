# Customer Support Agent

Production-ready tier-1 customer support agent with Zendesk integration, RAG-powered product knowledge, and automatic escalation to human agents.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Zendesk account with API access
- Anthropic API key (primary model) and OpenAI API key (fallback)
- Product documentation indexed in AgentBreeder knowledge base

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

2. **Validate the configuration:**
   ```bash
   agentbreeder validate
   ```

3. **Deploy locally:**
   ```bash
   agentbreeder deploy --target local
   ```

## Architecture

```
User Message
    |
    v
[Customer Support Agent] -- Claude Sonnet (primary) / GPT-4o (fallback)
    |
    +---> Knowledge Base Search (product docs, FAQ, return policy)
    +---> Zendesk MCP (ticket create/update/search)
    +---> Order Lookup (status, tracking)
    +---> Escalate to Human (when triggers are met)
```

### Escalation Triggers

The agent automatically escalates to a human when:
- Refund requests exceed $500
- Legal or compliance concerns arise
- Customer expresses extreme frustration (3+ negative messages)
- Technical issues cannot be diagnosed

### Guardrails

- **PII Detection** -- strips sensitive data from responses and logs
- **Content Filter** -- blocks inappropriate content
- **Hallucination Check** -- flags low-confidence responses for review

## Customization

### Change the LLM model

Edit `agent.yaml` and update `model.primary`:
```yaml
model:
  primary: gpt-4o          # Switch to OpenAI
  fallback: claude-sonnet-4 # Use Claude as fallback
```

### Deploy to AWS

```yaml
deploy:
  cloud: aws
  runtime: ecs-fargate
  region: us-east-1
```

### Add more knowledge bases

```yaml
knowledge_bases:
  - ref: kb/product-docs
  - ref: kb/return-policy
  - ref: kb/troubleshooting-guides   # Add new KB
```

### Adjust escalation policy

Edit the system prompt in `agent.yaml` under `prompts.system` to modify escalation triggers, tone, or response format.
