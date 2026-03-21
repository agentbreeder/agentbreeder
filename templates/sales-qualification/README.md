# Sales Qualification Agent

Lead qualification agent that integrates with your CRM, enriches company data, scores leads using the BANT+ framework, and drafts personalized outreach emails for qualified prospects.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- CRM API access (HubSpot, Salesforce, or Pipedrive)
- Clearbit or Apollo API key (for company enrichment)
- Anthropic API key (primary) and OpenAI API key (fallback)

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your CRM and API keys
   ```

2. **Deploy locally:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Qualify a lead:**
   ```bash
   agentbreeder chat sales-qualification-agent --message "Qualify lead: jane@acmecorp.com"
   ```

## Architecture

```
Inbound Lead
    |
    v
[CRM Lookup] -- Find existing record
    |
    v
[Enrich] -- Company data from Clearbit/Apollo
    |
    v
[Score] -- BANT+ framework (0-100)
    |
    +---> 80-100 (Hot): Book demo, draft outreach
    +---> 60-79 (Warm): Discovery call, nurture
    +---> 40-59 (Cool): Drip campaign
    +---> 0-39 (Not qualified): Archive
    |
    v
[Update CRM] -- Score, notes, recommended action
```

### BANT+ Scoring

- **Budget** -- Can they afford the solution?
- **Authority** -- Decision-maker or influencer?
- **Need** -- Clear pain point we solve?
- **Timeline** -- Active evaluation timeline?
- **Fit** -- ICP alignment (size, industry, tech stack)

## Customization

### Connect to Salesforce

```bash
# In .env
CRM_BASE_URL=https://yourorg.salesforce.com
SALESFORCE_ACCESS_TOKEN=xxxxx
```

### Enable auto-outreach

```yaml
env_vars:
  AUTO_OUTREACH_ENABLED: "true"
  QUALIFICATION_THRESHOLD: "70"    # Only auto-send for score >= 70
```

### Customize scoring weights

Edit the `SCORING CRITERIA` section in the system prompt to adjust thresholds and weight different BANT+ dimensions.
