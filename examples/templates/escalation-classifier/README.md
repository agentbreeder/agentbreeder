# Escalation Classifier Agent

Determines whether a support ticket needs immediate human escalation with high precision and low latency.

## Use Case

Not every support ticket needs a human agent, but some absolutely do — and getting that wrong in either direction is costly. This lightweight classifier agent reads each incoming ticket and detects escalation signals: high customer anger, VIP account status, legal threats, large billing disputes, safety concerns, or media mentions. It outputs a binary decision (escalate or not), a confidence score, and the signals it detected. Tickets flagged for escalation are immediately prioritized and routed to a senior agent queue. The agent runs with a very low temperature (0.1) for maximum consistency.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Zendesk account with API access
- OpenAI API key (primary model) and Anthropic API key (fallback)

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `ZENDESK_API_KEY` | Zendesk API token | Zendesk Admin → APIs |
| `OPENAI_API_KEY` | OpenAI API key | [platform.openai.com](https://platform.openai.com) |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ESCALATION_CONFIDENCE_THRESHOLD` | Minimum confidence score to trigger escalation | `0.7` |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use escalation-classifier my-escalation-agent

# 2. Set your Zendesk subdomain in agent.yaml env_vars

# 3. Set credentials
agentbreeder secret set ZENDESK_API_KEY
agentbreeder secret set OPENAI_API_KEY

# 4. Deploy
agentbreeder deploy --target aws
```

## Customization

- **Tune confidence threshold**: Lower `ESCALATION_CONFIDENCE_THRESHOLD` to catch more edge cases at the cost of more false positives
- **Add custom signals**: Extend the `signals` enum with your org's specific escalation criteria (e.g., `enterprise_contract`, `data_breach_report`)
- **Chain with triage**: Run this classifier first, then pipe escalated tickets to a human queue and non-escalated tickets to the `support-triage-agent`
- **Switch primary model**: The fallback is Claude Sonnet — swap `primary` and `fallback` to run Claude-first if preferred

## Agent Behavior

1. Triggered by Zendesk webhook on new ticket or first reply
2. Reads ticket subject, body, requester metadata, and account tier
3. Detects escalation signals across multiple dimensions
4. Calls `classify_escalation` with confidence score and signal list
5. If `confidence >= ESCALATION_CONFIDENCE_THRESHOLD` and `escalate: true`: calls `flag_ticket` with `needs-human` tag, sets priority to urgent/high, adds internal note
6. If not escalating: tags ticket `auto-handled` and lets automation continue

## Cost Estimate

~$0.05–$0.10 per 1,000 tickets using `gpt-4o` at 0.1 temperature and 1024 max tokens. This is the cheapest template in the support suite — designed to be run on every ticket.
