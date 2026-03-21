# Slack Bot Agent

Conversational Slack bot with channel-aware responses, thread management, knowledge base search, Jira ticket creation, and meeting scheduling.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Slack app created at [api.slack.com](https://api.slack.com/apps) with Bot Token and App Token
- Anthropic API key (primary) and OpenAI API key (fallback)

## Quick Start

1. **Create a Slack app** and configure Bot Token Scopes: `chat:write`, `channels:read`, `channels:history`, `app_mentions:read`

2. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your Slack tokens and API keys
   ```

3. **Deploy:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

## Architecture

```
Slack Message / Mention
    |
    v
[Slack Bot Agent] -- Claude Sonnet (primary) / GPT-4o (fallback)
    |
    +---> Knowledge Base Search (internal docs)
    +---> Jira MCP (create tickets)
    +---> Calendar API (schedule meetings)
    |
    v
Slack Thread Reply (with formatting)
```

### Features

- Responds to `@mentions` and direct messages
- Thread-aware -- replies in threads, not channels
- Slack-native formatting (bold, code blocks, lists)
- Creates Jira tickets with user confirmation
- Schedules meetings with availability checks

## Customization

### Add more Slack scopes

Update your Slack app's Bot Token Scopes at [api.slack.com](https://api.slack.com/apps) to add capabilities like file uploads or reactions.

### Restrict to specific channels

Add channel filtering in the system prompt or configure allowed channels in your Slack app settings.

### Connect to Jira

Uncomment the Jira environment variables in `.env` and provide your Jira credentials.
