# Code Review Agent

Automated PR review agent that integrates with GitHub to provide thorough, actionable code review feedback. Checks for correctness, security, performance, readability, testing, and architecture.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- GitHub personal access token or GitHub App with `repo` and `pull_requests` scopes
- Anthropic API key (primary) and OpenAI API key (fallback)

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your GitHub token and API keys
   ```

2. **Validate and deploy:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Trigger a review:**
   ```bash
   agentbreeder review --repo your-org/your-repo --pr 123
   ```

## Architecture

```
PR Webhook / CLI Trigger
    |
    v
[Code Review Agent] -- Claude Sonnet (primary) / GPT-4o (fallback)
    |
    +---> GitHub MCP (read diff, post comments)
    +---> Lint Check (static analysis)
    +---> Security Scan (vulnerability detection)
    +---> Suggest Improvement (inline comments)
```

### Review Output

Each review categorizes findings as:
- **[critical]** -- Must fix before merge (security, correctness)
- **[suggestion]** -- Recommended improvement (performance, architecture)
- **[nit]** -- Minor style/preference issues

## Customization

### Integrate with CI/CD

Set up a GitHub webhook pointing to your deployed agent, or add to your CI pipeline:
```yaml
# .github/workflows/review.yml
- name: AI Code Review
  run: agentbreeder review --repo ${{ github.repository }} --pr ${{ github.event.pull_request.number }}
```

### Adjust review strictness

Edit the system prompt in `agent.yaml` to modify which checks are [critical] vs [suggestion].

### Deploy to AWS

```yaml
deploy:
  cloud: aws
  runtime: ecs-fargate
  region: us-east-1
```
