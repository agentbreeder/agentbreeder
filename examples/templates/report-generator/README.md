# Report Generator Agent

Generates daily business intelligence digests from BigQuery and Snowflake and posts them to Slack automatically.

## Use Case

Leadership teams and business stakeholders need a daily pulse on key metrics — revenue, signups, churn, support volume — but nobody wants to manually compile these numbers every morning. This agent runs on a schedule (default: 8am weekdays), pulls the latest metrics from your data warehouses, compares them to the prior period, identifies anomalies and highlights, and posts a clean, structured digest to your executive and team Slack channels. Built on CrewAI's multi-agent coordination, it uses specialized sub-agents for data collection, analysis, and formatting.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- BigQuery and/or Snowflake data warehouse with metrics tables
- Slack workspace with a bot token and access to target channels
- Metrics definitions registered in `kb/metrics-definitions`

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `OPENAI_API_KEY` | OpenAI API key for GPT-4o | [platform.openai.com](https://platform.openai.com) |
| `SLACK_BOT_TOKEN` | Slack Bot OAuth token | [Slack API Console](https://api.slack.com/apps) |
| `BIGQUERY_CREDENTIALS` | GCP service account JSON for BigQuery | Google Cloud Console → IAM |
| `SNOWFLAKE_CONNECTION_STRING` | Snowflake connection string | Snowflake console → Admin |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use report-generator my-daily-digest

# 2. Register your metrics definitions
agentbreeder register kb metrics-definitions ./docs/metrics.yaml
agentbreeder register kb reporting-config ./config/reports.yaml

# 3. Configure channels in agent.yaml env_vars
# Edit: SLACK_CHANNEL_EXEC, SLACK_CHANNEL_DATA, REPORT_SCHEDULE

# 4. Set credentials
agentbreeder secret set OPENAI_API_KEY
agentbreeder secret set SLACK_BOT_TOKEN
agentbreeder secret set BIGQUERY_CREDENTIALS

# 5. Deploy
agentbreeder deploy --target aws

# 6. Test a manual run
agentbreeder chat --agent my-daily-digest "Generate today's report"
```

## Customization

- **Change schedule**: Edit `REPORT_SCHEDULE` (cron format) — e.g., `"0 7 * * 1"` for Monday morning only
- **Add more channels**: Extend `env_vars` with additional `SLACK_CHANNEL_*` variables and route different sections to different channels
- **Add more data sources**: Include `ref: tools/mysql-mcp` or `ref: tools/postgres-mcp` for additional warehouses
- **Customize sections**: Modify `kb/reporting-config` to define which metrics appear in which report sections
- **Add email delivery**: Add `ref: connectors/email` to send the digest as a formatted HTML email in addition to Slack
- **Switch model**: Use `claude-sonnet-4-6` instead of `gpt-4o` for better narrative writing quality

## Agent Behavior

1. Triggered by cron schedule or manual API call
2. Data Collection agent fetches metrics from BigQuery and Snowflake for the current and comparison period
3. Analysis agent compares periods, calculates changes, and identifies anomalies (>2 standard deviations from 30-day average)
4. Writer agent calls `generate_digest` to compose the full report with sections, highlights, and anomaly callouts
5. Distribution agent posts the formatted digest to configured Slack channels
6. Logs report metadata (run time, metrics fetched, channels posted) to AgentBreeder audit trail

## Cost Estimate

~$0.10–$0.20 per daily report using `gpt-4o` at 0.4 temperature. Scale is minimal since this runs once per day.
