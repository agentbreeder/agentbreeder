# Competitor Monitor Agent

Monitors competitor news, product updates, pricing changes, and hiring signals, then synthesizes a weekly competitive intelligence digest for your team.

## Use Case

Staying on top of the competitive landscape is critical but time-consuming — someone has to manually check competitor websites, read their blogs, scan for pricing updates, and track their job postings for strategic signals. This agent automates the entire workflow. It runs every Monday morning, scans your configured competitor list across web search, RSS feeds, and key pages, and posts a structured digest to your `#competitive-intel` Slack channel with key insights, strategic implications, and any high-priority alerts (e.g., a competitor just launched a direct feature you're building, or announced a major pricing drop).

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Anthropic API key
- Slack workspace with a bot token
- Competitor profiles registered in `kb/competitors`
- Your positioning and differentiators in `kb/our-positioning`

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude | [console.anthropic.com](https://console.anthropic.com) |
| `SLACK_BOT_TOKEN` | Slack Bot OAuth token | [Slack API Console](https://api.slack.com/apps) |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use competitor-monitor my-competitor-monitor

# 2. Register your competitor profiles and positioning
agentbreeder register kb competitors ./marketing/competitors.yaml
agentbreeder register kb our-positioning ./marketing/positioning.yaml

# 3. Configure competitors in agent.yaml env_vars (COMPETITORS)
# 4. Set your Slack channel (SLACK_CHANNEL)

# 5. Set credentials
agentbreeder secret set ANTHROPIC_API_KEY
agentbreeder secret set SLACK_BOT_TOKEN

# 6. Deploy
agentbreeder deploy --target aws

# 7. Test a manual run
agentbreeder chat --agent my-competitor-monitor "Run a competitor scan now"
```

## Customization

- **Add more competitors**: Update the `COMPETITORS` env var (comma-separated domains)
- **Change scan frequency**: Edit `SCAN_SCHEDULE` — e.g., `"0 8 * * 1,3,5"` for Monday/Wednesday/Friday
- **Focus on specific areas**: Edit `scan_areas` in the tool schema to focus on pricing and product only
- **Add email delivery**: Include `ref: connectors/email` to also email the digest to key stakeholders
- **RSS feed monitoring**: Configure `rss-reader` with competitor blog RSS feeds for more accurate content tracking
- **Alert thresholds**: Add logic to immediately notify `#leadership` when a `significance: high` item is detected rather than waiting for the weekly digest

## Agent Behavior

1. Triggered by cron schedule (Monday 7am) or manual API call
2. Loads competitor list from `COMPETITORS` env var and `kb/competitors`
3. For each competitor, runs web searches for recent news, checks RSS feeds, and scans key pages
4. Calls `scan_competitor` to structure findings with change type and significance ratings
5. Aggregates all findings and compares against `kb/our-positioning` to generate strategic insights
6. Calls `post_digest` to post the formatted weekly digest to Slack
7. High-significance items (`significance: high`) are highlighted at the top as alert items

## Cost Estimate

~$0.50–$1.50 per weekly scan covering 5 competitors using `claude-sonnet-4-6` with adaptive thinking. Web search API costs are additional and depend on your search provider.
