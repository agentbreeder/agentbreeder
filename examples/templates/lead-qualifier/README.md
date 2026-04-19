# Lead Qualifier Agent

Qualifies inbound leads from HubSpot by enriching them with Clearbit data and web research, then scores and routes them to the right sales rep.

## Use Case

Sales teams waste enormous time working low-quality leads while high-value prospects wait. This agent processes every new HubSpot lead as it comes in: it pulls company data from Clearbit (employee count, industry, funding stage, technology stack), researches the company and contact on the web for intent signals, compares against your Ideal Customer Profile criteria, and produces a fit score, intent score, and qualification tier. Hot leads get immediate routing and a sales rep notification; disqualified leads are marked and excluded from sequences automatically.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- HubSpot account with API access and a form or integration creating leads
- Clearbit account with Enrichment API access
- ICP and qualification criteria registered in the AgentBreeder registry

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `OPENAI_API_KEY` | OpenAI API key | [platform.openai.com](https://platform.openai.com) |
| `HUBSPOT_API_KEY` | HubSpot private app token | HubSpot → Settings → Integrations → Private Apps |
| `CLEARBIT_API_KEY` | Clearbit API key | [clearbit.com/docs](https://clearbit.com/docs#authentication) |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MIN_EMPLOYEE_COUNT` | Minimum company size to qualify | `50` |
| `TARGET_INDUSTRIES` | Comma-separated list of target industries | `saas,fintech,healthtech,ecommerce` |
| `HOT_LEAD_THRESHOLD` | Minimum combined score to mark as hot | `75` |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use lead-qualifier my-lead-qualifier

# 2. Register your ICP and qualification criteria
agentbreeder register kb ideal-customer-profile ./sales/icp.yaml
agentbreeder register kb qualification-criteria ./sales/qualification.yaml

# 3. Set credentials
agentbreeder secret set OPENAI_API_KEY
agentbreeder secret set HUBSPOT_API_KEY
agentbreeder secret set CLEARBIT_API_KEY

# 4. Adjust ICP parameters in env_vars, then deploy
agentbreeder deploy --target aws

# 5. Configure HubSpot workflow to call the agent on new contact creation
```

## Customization

- **Update ICP criteria**: Edit `kb/ideal-customer-profile` to match your target segments — changes take effect immediately, no redeploy needed
- **Add LinkedIn enrichment**: Include `ref: tools/linkedin-mcp` for deeper contact-level data
- **Add Slack notifications**: Notify the sales team's Slack channel when a hot lead arrives
- **Customize scoring weights**: Edit `prompts/lead-qualifier-v1` to weight industry fit vs. company size vs. intent signals
- **Multi-language support**: Add language detection and route non-English leads to regional sales reps

## Agent Behavior

1. Triggered by HubSpot webhook when a new contact is created
2. Fetches contact properties (email, company, job title) from HubSpot
3. Calls Clearbit to enrich with company data (employees, industry, funding, tech stack)
4. Runs web search for recent company news and buying intent signals
5. Compares against `kb/ideal-customer-profile` to calculate fit score
6. Calls `score_lead` to produce qualification tier and recommended action
7. Calls `update_crm` to write scores back to HubSpot, assign sales owner, and optionally enroll in a sequence
8. Logs qualification decision to the AgentBreeder audit trail

## Cost Estimate

~$0.15–$0.30 per 100 leads using `gpt-4o` at 0.3 temperature. Clearbit API costs are separate and depend on your plan.
