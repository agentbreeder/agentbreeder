# Research Agent

Multi-step research pipeline that searches the web, verifies sources, cross-references findings, and produces structured reports with citations and confidence levels.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Tavily API key (or alternative search API)
- Anthropic API key (primary) and OpenAI API key (fallback)

## Quick Start

1. **Configure secrets:**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

2. **Deploy locally:**
   ```bash
   agentbreeder validate && agentbreeder deploy --target local
   ```

3. **Run a research query:**
   ```bash
   agentbreeder chat research-agent --message "Research the current state of quantum computing in 2026"
   ```

## Architecture

```
Research Question
    |
    v
[Scope] -- Break into sub-questions
    |
    v
[Search] -- Web search for each sub-question (parallel)
    |
    v
[Verify] -- Cross-reference across sources
    |
    v
[Synthesize] -- Combine findings with confidence levels
    |
    v
[Report] -- Structured output with citations
```

### Report Output

Each report includes:
- Executive summary (2-3 sentences)
- Key findings with confidence levels (HIGH/MEDIUM/LOW)
- Detailed analysis organized by sub-question
- Numbered source list with URLs
- Identified gaps and follow-up suggestions

## Customization

### Use a different search API

Replace `TAVILY_API_KEY` with your preferred search provider in `.env` and update the tool reference in `agent.yaml`.

### Adjust research depth

```yaml
env_vars:
  MAX_SEARCH_QUERIES: "25"      # More queries = deeper research
  MAX_URLS_PER_QUERY: "10"      # More URLs = broader coverage
```

### Change output format

```yaml
env_vars:
  REPORT_FORMAT: json           # Options: markdown | json | html
```
