# Returns Processor Agent

Handles customer return and refund requests autonomously from intake to resolution.

## Use Case

Processing returns is repetitive but consequential: agents must look up orders, check return eligibility against policy, initiate refunds through the payment system, generate return shipping labels, and update the support ticket — all while handling sensitive customer data carefully. This agent automates the entire flow end-to-end. Refunds below a configurable threshold are approved automatically; larger refunds are flagged for human review. PII guardrails ensure customer payment and address data never leaks into logs.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Stripe account with refund API access
- Zendesk account for ticket management
- Shippo or similar shipping API for return labels
- Return policy document registered as `kb/return-policy`

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `STRIPE_SECRET_KEY` | Stripe secret key for refund processing | [Stripe Dashboard](https://dashboard.stripe.com/apikeys) |
| `ZENDESK_API_KEY` | Zendesk API token | Zendesk Admin → APIs |
| `SHIPPO_API_KEY` | Shippo API key for return labels | [goshippo.com](https://goshippo.com/user/apikeys) |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RETURN_WINDOW_DAYS` | Number of days after purchase that returns are accepted | `30` |
| `MAX_REFUND_AUTO_APPROVE` | Max refund amount (USD) the agent can approve without human review | `500` |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use returns-processor my-returns-agent

# 2. Register your return policy knowledge base
agentbreeder register kb return-policy ./docs/return-policy.pdf

# 3. Set your credentials
agentbreeder secret set STRIPE_SECRET_KEY
agentbreeder secret set ZENDESK_API_KEY
agentbreeder secret set SHIPPO_API_KEY

# 4. Adjust thresholds in agent.yaml env_vars, then deploy
agentbreeder deploy --target aws
```

## Customization

- **Increase auto-approval limit**: Change `MAX_REFUND_AUTO_APPROVE` in `env_vars`
- **Change return window**: Adjust `RETURN_WINDOW_DAYS` per your policy
- **Add carrier options**: Edit the `carrier` enum in `send_return_label` to include your preferred carriers
- **Require human review for all refunds**: Set `MAX_REFUND_AUTO_APPROVE: "0"` to always escalate
- **Add store credit flow**: Extend `initiate_refund` to handle `store_credit` method with your platform's API

## Agent Behavior

1. Receives a return request from Zendesk webhook or direct API call
2. Looks up the original order using the order-lookup tool
3. Calls `check_return_eligibility` against the return policy knowledge base
4. If ineligible: closes the ticket with a polite explanation and policy link
5. If eligible and under `MAX_REFUND_AUTO_APPROVE`: initiates refund via Stripe, sends return label, updates ticket
6. If eligible but over threshold: adds internal note for human agent review and pauses
7. PII guardrail strips customer payment details from all audit logs

## Cost Estimate

~$0.20–$0.40 per 1,000 return requests using `claude-sonnet-4-6` with adaptive thinking enabled.
