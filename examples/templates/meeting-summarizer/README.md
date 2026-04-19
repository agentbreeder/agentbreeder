# Meeting Summarizer Agent

Transcribes meetings, extracts action items with owners, creates Notion pages, and sends Slack recaps automatically.

## Use Case

After every meeting, someone manually writes up notes, identifies action items, assigns owners, creates tasks in the project tracker, and sends a recap — this is repetitive coordination work that adds up to hours per week across a team. This agent automates the entire post-meeting workflow: once a meeting ends, it transcribes the recording, identifies key decisions and action items with their owners and due dates, creates a structured notes page in Notion, and sends a formatted recap to attendees via Slack. PII guardrails ensure sensitive personal information in meeting transcripts doesn't leak into logs.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Google Calendar or Outlook with calendar API access
- Notion workspace with API access and a meeting notes database
- Slack workspace with a bot token
- Meeting recordings available via URL (Zoom, Google Meet, Teams)

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude | [console.anthropic.com](https://console.anthropic.com) |
| `NOTION_API_KEY` | Notion internal integration token | [notion.so/my-integrations](https://www.notion.so/my-integrations) |
| `SLACK_BOT_TOKEN` | Slack Bot OAuth token | [Slack API Console](https://api.slack.com/apps) |
| `CALENDAR_SERVICE_ACCOUNT` | Google Calendar service account JSON | Google Cloud Console → IAM |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use meeting-summarizer my-meeting-summarizer

# 2. Create a Notion database for meeting notes and get its ID from the URL

# 3. Update env_vars in agent.yaml:
#    - NOTION_MEETINGS_DATABASE_ID
#    - SLACK_RECAP_CHANNEL

# 4. Set credentials
agentbreeder secret set ANTHROPIC_API_KEY
agentbreeder secret set NOTION_API_KEY
agentbreeder secret set SLACK_BOT_TOKEN

# 5. Deploy
agentbreeder deploy --target aws

# 6. Configure your meeting tool (Zoom/Meet) to send a webhook when recordings are ready
```

## Customization

- **Disable Slack recaps**: Set `SEND_SLACK_RECAP: "false"` to only create Notion pages
- **Add Jira integration**: Include `ref: tools/jira-mcp` to create Jira tickets for action items instead of Notion tasks
- **Customize summary template**: Edit `prompts/meeting-summarizer-v1` to match your team's preferred meeting notes format
- **Add agenda context**: Connect to your calendar's meeting description to provide the agent with pre-meeting context
- **Multi-language support**: Claude Sonnet handles multi-language transcripts — add a language detection step for international teams
- **Focus meetings**: Configure the agent to only process meetings with certain title keywords (e.g., only process "Sprint" meetings)

## Agent Behavior

1. Triggered by a webhook when a meeting recording is ready, or by a calendar event end time
2. Calls `transcribe_meeting` with the recording URL and attendee list (speaker diarization identifies who said what)
3. Calls `extract_summary` to pull key decisions, action items with owners, and discussion topics from the transcript
4. Calls `create_notion_page` to create a structured meeting notes page in the configured database
5. If `AUTO_CREATE_TASKS: true`, creates follow-up tasks in Notion for each action item with due dates
6. If `SEND_SLACK_RECAP: true`, posts a concise formatted recap to the configured channel and DMs to each attendee
7. PII guardrail strips personal contact information from all stored transcripts

## Cost Estimate

~$0.40–$0.80 per meeting using `claude-sonnet-4-6` with adaptive thinking. Cost scales with meeting duration (longer transcripts = more tokens). Prompt caching significantly reduces cost for teams with consistent meeting formats.
