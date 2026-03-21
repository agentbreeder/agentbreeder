# Multi-Agent Pipeline

Production multi-agent orchestration with triage routing, specialist agents, and QA verification. Demonstrates the supervisor strategy pattern for complex workflows.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Redis (for shared state in production; optional for local dev)
- Anthropic API key (primary) and OpenAI API key (fallback)

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

2. **Deploy the pipeline:**
   ```bash
   agentbreeder validate && agentbreeder orchestration deploy ./orchestration.yaml
   ```

3. **Test the pipeline:**
   ```bash
   agentbreeder orchestration chat multi-agent-support-pipeline \
     --message "I was double-charged on my last invoice"
   ```

## Architecture

```
User Request
    |
    v
[Triage Agent] -- Classify intent, lookup customer
    |
    +---> billing   --> [Billing Specialist]   --+
    +---> technical --> [Technical Specialist]  --+--> [QA Reviewer]
    +---> account   --> [Account Specialist]   --+        |
    +---> general   --> (handle directly)                 |
                                                          v
                                                   Score >= 80? --yes--> Deliver
                                                          |
                                                         no
                                                          |
                                                          v
                                                   Revise (max 2x) --> Escalate to human
```

### Pipeline Flow

1. **Triage** -- classifies intent and routes to the appropriate specialist
2. **Specialist** -- handles the domain-specific query
3. **QA Review** -- scores the response for accuracy, tone, and completeness
4. **Delivery** -- if QA score >= 80, delivers to user; otherwise requests revision

### Shared State

All agents share context via Redis-backed state, ensuring smooth handoffs between triage, specialists, and QA.

## Customization

### Adjust quality threshold

```yaml
env_vars:
  QA_THRESHOLD: "90"     # Stricter quality requirement
  MAX_REVISIONS: "3"     # Allow more revision cycles
```

### Add a new specialist

1. Create the specialist agent with `agentbreeder init --template customer-support`
2. Add it to `orchestration.yaml` under `agents:`
3. Add routing conditions in the `triage` agent's `routes`

### Switch orchestration strategy

Change `strategy` in `orchestration.yaml`:
- `router` -- simple keyword-based routing
- `sequential` -- agents run in fixed order
- `parallel` -- agents run simultaneously
- `supervisor` -- supervisor controls routing (current)
- `fan_out_fan_in` -- parallel execution with result merging
