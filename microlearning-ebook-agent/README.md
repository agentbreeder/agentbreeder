# microlearning-ebook-agent

[![Framework](https://img.shields.io/badge/framework-Google%20ADK-blue)]()
[![Cloud](https://img.shields.io/badge/cloud-GCP%20Cloud%20Run-4285F4)]()
[![Model](https://img.shields.io/badge/model-gemini--2.5--pro-orange)]()

Researches a user-supplied topic and produces a structured microlearning ebook (intro, lessons, quizzes, summary) for corporate L&D teams. Built on Google ADK, deployed to GCP Cloud Run via AgentBreeder.

## Prerequisites
- Python 3.11+
- Docker + Docker Compose
- A `GOOGLE_API_KEY` for Gemini and a web research API key (Tavily recommended)
- `agentbreeder` CLI (`pip install agentbreeder`)

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in: GOOGLE_API_KEY, TAVILY_API_KEY, GCS_BUCKET, DATABASE_URL
```

## Run locally
```bash
adk run                # interactive REPL
# or
adk api_server --host 0.0.0.0 --port 8080
```

## Run in container
```bash
docker compose up
# Postgres comes up alongside the agent for session checkpoints + HITL state
```

## Validate
```bash
agentbreeder validate
```

## Deploy to GCP Cloud Run
```bash
agentbreeder deploy --target gcp
```

## Project Structure
```
microlearning-ebook-agent/
├── agent.yaml                  # AgentBreeder config (model, deploy, guardrails)
├── agent.py                    # ADK agent entrypoint
├── tools/
│   ├── research_web.py         # Web search via Tavily/Serper
│   ├── extract_concepts.py     # Distill learning objectives from sources
│   ├── structure_modules.py    # Outline intro/lessons/quizzes/summary
│   ├── generate_quiz.py        # MCQ generation with explanations
│   └── render_ebook.py         # PDF/EPUB renderer (writes to GCS)
├── memory/config.py            # PostgreSQL config for ADK sessions + HITL state
├── mcp/servers.yaml            # MCP server refs (web search)
├── tests/
│   ├── test_agent.py           # Smoke tests
│   └── evals/                  # PromptFoo eval harness + criteria
├── .agentbreeder/layout.json   # No Code tier visual layout
├── Dockerfile                  # ADK api_server image
├── docker-compose.yml          # Local stack: agent + postgres
├── requirements.txt
├── .env.example
├── CLAUDE.md                   # Claude Code context
├── AGENTS.md                   # AI skill roster
├── .cursorrules                # Cursor IDE rules
├── .antigravity.md             # Hard "do not" constraints
├── ARCHITECT_NOTES.md          # Why each architectural choice was made
└── README.md
```

## Pipeline
```
user topic
  -> research_web         (gather authoritative sources)
  -> extract_concepts     (distill 4-8 learning objectives)
  -> structure_modules    (intro / lessons / quiz / summary)
  -> generate_quiz        (per lesson: 3-5 MCQs)
  -> [HITL pause]         (L&D reviewer approves outline)
  -> render_ebook         (PDF or EPUB to GCS, return signed URL)
```

## Tier Mobility
- **No Code** — open in the AgentBreeder dashboard to edit visually
- **Low Code** — edit `agent.yaml` directly
- **Full Code** — modify `agent.py` and the tool stubs in `tools/`
