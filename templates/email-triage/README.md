# Email Triage Agent

Automated email processing agent that parses incoming emails, classifies them by category and priority, routes to the appropriate team, and generates draft replies for standard requests.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Email account with IMAP/SMTP access
- Anthropic API key (primary) and OpenAI API key (fallback)

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your email credentials and API keys
   ```

2. **Deploy locally:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Process emails:**
   ```bash
   agentbreeder chat email-triage-agent --message "Process the latest 10 unread emails"
   ```

## Architecture

```
Incoming Email (IMAP)
    |
    v
[Parse] -- Extract sender, subject, body, attachments
    |
    v
[Classify] -- Category: support | sales | billing | partnership | spam
    |
    v
[Score Priority] -- 1 (minimal) to 5 (critical)
    |
    v
[Route] -- Send to appropriate team queue
    |
    +---> Priority 5: Slack alert to on-call
    +---> Priority <= 3: Auto-draft reply
    +---> Spam: Mark and archive
```

### Priority Levels

| Level | Label | Examples |
|-------|-------|---------|
| 5 | Critical | Service outage, security incident |
| 4 | High | Revenue-impacting, VIP customer |
| 3 | Medium | Standard request with deadline |
| 2 | Low | General inquiry |
| 1 | Minimal | Newsletter, FYI |

## Customization

### Disable auto-replies

```yaml
env_vars:
  AUTO_REPLY_ENABLED: "false"
```

### Add Slack alerts for high-priority emails

Set `SLACK_WEBHOOK_URL` in `.env` to receive alerts for priority 4-5 emails.

### Customize classification categories

Edit the `CLASSIFICATION CATEGORIES` section in the system prompt to add or modify categories to match your team structure.
