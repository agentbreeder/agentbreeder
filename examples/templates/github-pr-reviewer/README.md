# GitHub PR Reviewer Agent

Automated pull request reviewer that analyzes code diffs and posts structured inline review comments directly on GitHub.

## Use Case

Code review is a critical quality gate but it's time-consuming, especially for large teams. This agent integrates into your CI/CD pipeline via a GitHub webhook or Actions workflow, analyzes every PR diff using Claude Opus (the highest-quality model for code reasoning), and posts inline comments for bugs, security vulnerabilities, performance issues, and style violations. It uses adaptive thinking with high effort for careful multi-step reasoning over complex diffs. The agent can approve, request changes, or comment — matching standard GitHub review workflows.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- GitHub Personal Access Token or GitHub App with `pull_requests: write` and `contents: read` permissions
- Anthropic API key
- Docker (for local deployment — recommended for CI/CD pipelines)

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `GITHUB_TOKEN` | GitHub PAT or App token with PR write access | [GitHub Settings → Developer settings → PATs](https://github.com/settings/tokens) |
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude Opus | [console.anthropic.com](https://console.anthropic.com) |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `REVIEW_LANGUAGES` | Comma-separated list of languages to review | `python,typescript,go,java,rust` |
| `MIN_SEVERITY_TO_BLOCK` | Minimum finding severity that triggers `REQUEST_CHANGES` | `critical` |
| `MAX_FILES_PER_PR` | Maximum number of files to review per PR (large PRs are summarized) | `50` |

## Quick Start

### Option 1: GitHub Actions

```yaml
# .github/workflows/ai-review.yml
name: AI Code Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run AgentBreeder PR Reviewer
        run: |
          pip install agentbreeder
          agentbreeder deploy --target local
          # Trigger review via API
          curl -X POST http://localhost:8080/review \
            -d '{"repo": "${{ github.repository }}", "pr": ${{ github.event.number }}}'
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Option 2: Webhook

```bash
# 1. Clone this template
agentbreeder template use github-pr-reviewer my-pr-reviewer

# 2. Set credentials
agentbreeder secret set GITHUB_TOKEN
agentbreeder secret set ANTHROPIC_API_KEY

# 3. Deploy locally
agentbreeder deploy --target local

# 4. Configure GitHub webhook to POST to your agent endpoint on PR events
```

## Customization

- **Focus on specific languages**: Update `REVIEW_LANGUAGES` to only the languages your repo uses
- **Lower the blocking threshold**: Change `MIN_SEVERITY_TO_BLOCK` to `warning` to be stricter
- **Exclude files**: Add a `.agentbreederignore` file listing paths to skip (e.g., `*.generated.ts`, `migrations/`)
- **Custom review checklist**: Edit `prompts/pr-reviewer-v1` to add org-specific guidelines (e.g., "always check for SQL injection in database queries")
- **Add to existing CI**: The local deploy target makes this easy to run in any CI environment without cloud infrastructure

## Agent Behavior

1. Receives a PR webhook event or direct API call with repo and PR number
2. Fetches the PR diff from GitHub (respects `MAX_FILES_PER_PR` limit)
3. For each changed file, calls `analyze_diff` with the diff content and detected language
4. Aggregates findings by severity — collects all critical and warning findings
5. Determines verdict: `REQUEST_CHANGES` if any findings at or above `MIN_SEVERITY_TO_BLOCK`, `COMMENT` for warnings only, `APPROVE` if clean
6. Calls `post_review` to post inline comments and the top-level review summary
7. Logs the review decision to the AgentBreeder audit trail

## Cost Estimate

~$2–$5 per 100 PRs using `claude-opus-4-7` with high-effort adaptive thinking. Larger diffs cost more. Consider using `claude-sonnet-4-6` as primary for cost savings on high-volume repos.
