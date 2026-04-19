# Incident Responder Agent

Coordinates incident response automatically: creates PagerDuty incidents, opens Slack war rooms, queries runbooks, and drafts status page updates.

## Use Case

During an outage, the first 15 minutes are critical and chaotic. On-call engineers are simultaneously triaging the problem, notifying stakeholders, setting up communication channels, and writing status updates — all while trying to actually fix things. This agent handles the coordination overhead automatically: the moment an alert fires, it searches runbooks for relevant procedures, creates a PagerDuty incident, opens a dedicated Slack war room with the right responders, and drafts the first customer-facing status page update. Engineers can focus on the fix while the agent handles the comms.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- PagerDuty account with API access
- Slack workspace with a bot token
- Runbooks registered in the AgentBreeder registry as `kb/runbooks`
- Service catalog registered as `kb/service-catalog`

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `SLACK_BOT_TOKEN` | Slack Bot OAuth token | [Slack API Console](https://api.slack.com/apps) → Your App → OAuth & Permissions |
| `PAGERDUTY_API_KEY` | PagerDuty API key | PagerDuty → Configuration → API Access Keys |
| `ANTHROPIC_API_KEY` | Anthropic API key | [console.anthropic.com](https://console.anthropic.com) |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use incident-responder my-incident-responder

# 2. Register your runbooks
agentbreeder register kb runbooks ./runbooks/
agentbreeder register kb service-catalog ./docs/services.yaml
agentbreeder register kb incident-playbooks ./playbooks/incidents/

# 3. Set credentials
agentbreeder secret set SLACK_BOT_TOKEN
agentbreeder secret set PAGERDUTY_API_KEY
agentbreeder secret set ANTHROPIC_API_KEY

# 4. Update env_vars in agent.yaml (SLACK_WORKSPACE, etc.)
# 5. Deploy
agentbreeder deploy --target aws
```

## Customization

- **Add monitoring integration**: Wire to PagerDuty webhooks, Datadog alerts, or OpsGenie to trigger the agent automatically
- **Configure auto-status-updates**: Set `STATUS_PAGE_AUTO_UPDATE: "false"` to require human approval before publishing
- **Add runbook steps to Slack**: Extend `create_war_room` to post relevant runbook excerpts as the first message
- **Integrate with Jira**: Add `ref: tools/jira-mcp` to automatically create an incident ticket in your Jira board
- **Customize severity mapping**: Edit the `create_incident` tool schema enum and your PagerDuty escalation policies

## Agent Behavior

1. Triggered by a PagerDuty alert webhook, Slack command (`/incident`), or direct API call
2. Searches `kb/runbooks` and `kb/incident-playbooks` for procedures matching the alert context
3. Calls `create_incident` to create a structured incident record and PagerDuty incident
4. Calls `create_war_room` to create a `#inc-XXXX-service-name` Slack channel and invite on-call groups
5. Posts initial incident summary with severity, affected services, and relevant runbook links to the war room
6. Calls `draft_status_update` with status `investigating` and posts for human review/approval
7. Monitors for resolution commands and drafts final `resolved` status update

## Cost Estimate

~$0.50–$1.50 per incident using `claude-sonnet-4-6` (most cost is in runbook search, not generation). Maintain a high `min: 2` replica count for low-latency response during incidents.
