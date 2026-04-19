# Market Researcher Agent

Synthesizes comprehensive market research reports from web sources, academic papers, and industry news — with citations, trend analysis, and strategic implications.

## Use Case

Commissioning a market research report from an analyst firm costs tens of thousands of dollars and takes weeks. Ad hoc web research by a product manager is shallow and inconsistent. This agent bridges the gap: given a research topic, it searches across web sources, ArXiv papers (for emerging tech trends), and industry news simultaneously, deduplicates and ranks sources by relevance, and synthesizes a structured report with market size estimates, trend analysis by time horizon, key player mapping, and strategic implications. Built on CrewAI's multi-agent coordination, specialized sub-agents handle research collection, synthesis, and formatting in parallel. Uses Claude Opus for the highest-quality synthesis and reasoning.

## Prerequisites

- AgentBreeder CLI installed (`pip install agentbreeder`)
- Anthropic API key (Claude Opus)
- News API key for industry news access
- Optional: existing research notes registered as `kb/previous-research`

## Required Credentials

| Secret | Description | Where to get it |
|--------|-------------|-----------------|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude Opus | [console.anthropic.com](https://console.anthropic.com) |
| `NEWS_API_KEY` | News API key for industry news | [newsapi.org](https://newsapi.org) or your preferred provider |

## Quick Start

```bash
# 1. Clone this template
agentbreeder template use market-researcher my-market-researcher

# 2. Register any existing market context and previous research
agentbreeder register kb market-context ./research/market-context.yaml
agentbreeder register kb previous-research ./research/previous/

# 3. Set credentials
agentbreeder secret set ANTHROPIC_API_KEY
agentbreeder secret set NEWS_API_KEY

# 4. Deploy
agentbreeder deploy --target aws

# 5. Run a research task
agentbreeder chat --agent my-market-researcher \
  "Research the enterprise AI agent platform market for 2025-2026"
```

## Customization

- **Focus on verticals**: Update `prompts/market-researcher-v1` to specialize in your industry vertical for more relevant source selection
- **Add analyst feeds**: Include `ref: tools/rss-reader` configured with Gartner, Forrester, or CB Insights RSS feeds
- **Output to Notion**: Change `DEFAULT_OUTPUT_FORMAT: "notion"` and add `ref: tools/notion-mcp` to publish directly to your research workspace
- **Add competitive framing**: Include competitor profiles from `kb/competitors` to automatically frame market research relative to your position
- **Scheduled reports**: Set up a monthly cron trigger to auto-generate research updates on topics of ongoing interest
- **Export to PDF**: Add a PDF rendering step using headless Chrome for distribution to stakeholders

## Agent Behavior

1. Receives a research topic via API or chat
2. Research Collection agent searches web, ArXiv, and news concurrently using the topic and related keywords
3. Calls `collect_sources` to deduplicate and rank sources by relevance (targeting `MAX_SOURCES_PER_TOPIC` best sources)
4. Analysis agent reads and processes each source, extracting key data points and insights
5. Synthesis agent calls `synthesize_research` to build the structured report with market size, trends, players, and implications
6. Formatting agent calls `format_report` to produce the final document in the configured output format
7. Returns the completed report and stores it in `kb/previous-research` for future reference
8. Logs the research task (topic, sources reviewed, report length) to the AgentBreeder audit trail

## Cost Estimate

~$2–$8 per research report using `claude-opus-4-7` (Opus is significantly more expensive but produces substantially better synthesis quality). Research on broad topics with many sources will be at the higher end. Consider `claude-sonnet-4-6` for faster, cheaper preliminary scans.
