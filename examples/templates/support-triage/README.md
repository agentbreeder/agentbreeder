# Support Triage Agent

Classifies and routes tier-1 customer support tickets automatically using Zendesk and your knowledge base.

## Use Case

Support teams are often overwhelmed by the volume of incoming tickets and spend significant time manually reading, categorizing, and routing each one. This agent reads each new Zendesk ticket, searches the product knowledge base and support playbooks for context, then classifies the ticket by category (billing, technical, account, etc.) and priority, and routes it to the correct agent queue — all within seconds of the ticket arriving.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Zendesk account with API access (Admin or Agent role)
- Knowledge base and support playbooks registered in the AgentBreeder registry
- `kb/product-docs`, `kb/support-playbooks`, and `kb/billing-faq` knowledge bases set up

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `ZENDESK_API_KEY` | Zendesk API token | Zendesk Admin → Apps & Integrations → APIs → Zendesk API |
| `OPENAI_API_KEY` | OpenAI API key (fallback model) | [platform.openai.com](https://platform.openai.com) |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use support-triage my-support-triage

# 2. Edit agent.yaml — set your team, owner, and ZENDESK_SUBDOMAIN env var
vim agent.yaml

# 3. Register your knowledge bases in the registry (if not already done)
agentbreeder register kb product-docs ./docs/
agentbreeder register kb support-playbooks ./playbooks/

# 4. Deploy to AWS
agentbreeder deploy --target aws
```

## Customization

- **Change triage categories**: Edit the `classify_ticket` tool schema `enum` values to match your team's queues
- **Add more knowledge bases**: Add more `ref: kb/` entries under `knowledge_bases`
- **Adjust sensitivity**: Lower `model.temperature` (e.g., `0.1`) for more deterministic classifications
- **Switch to local deployment**: Change `deploy.cloud: local` and remove the secrets block for testing
- **Add Slack notifications**: Add `ref: tools/slack-mcp` and post triage summaries to a #support-ops channel

## Agent Behavior

1. Triggered via Zendesk webhook when a new ticket arrives
2. Fetches the ticket body and metadata from Zendesk
3. Searches the knowledge base to understand the product context
4. Calls `classify_ticket` to determine category, priority, and target queue
5. Calls `update_ticket` to apply tags, assign to the correct group, and add an internal note
6. Logs the triage decision to the AgentBreeder audit trail

## Cost Estimate

~$0.08–$0.15 per 1,000 tickets using `claude-sonnet-4-6` at default settings (0.3 temperature, 2048 max tokens).
