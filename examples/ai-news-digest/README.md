# AI News Digest Agent

Daily AI news digest using **Google ADK + Ollama Gemma3:27b**, deployed with AgentBreeder.

Fetches top stories from Hacker News, ArXiv (cs.AI + cs.LG), and industry RSS feeds,
synthesises a grouped digest, and emails it to configured recipients via Gmail.

## What AgentBreeder handles automatically

- Installing `google-adk`, framework deps — injected by the runtime into the container
- Server boilerplate (`server.py`) — auto-copied from AgentBreeder's template
- Dockerfile — auto-generated
- `pip install` inside the container

## What you provide

- `agent.yaml` — model, secrets, env vars
- `root_agent.yaml` — agent identity + instruction (no Python required once #66 resolved)
- `agent.py` — workaround until [AgentBreeder #66](https://github.com/rajitsaha/agentbreeder/issues/66) lands
- `tools/` — MCP tool server (news fetch + email)

## Quick start

### 1. Prerequisites

```bash
# Install Ollama (manages local LLM)
brew install ollama        # macOS
ollama pull gemma3:27b     # ~18GB — grab a coffee

# Start Ollama
ollama serve
```

### 2. Install

```bash
pip install agentbreeder
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env:
#   SMTP_USER      — your Gmail address
#   SMTP_PASSWORD  — Gmail App Password (not your login password)
#                    Setup: https://myaccount.google.com/apppasswords
#   RECIPIENT_EMAILS — comma-separated list
```

### 4. Run

```bash
# Send digest immediately
python agent.py --once

# Run as daily daemon (fires at DIGEST_HOUR, default 8am)
python agent.py --schedule

# Deploy via AgentBreeder (production path)
agentbreeder deploy --target local
```

## Known limitations (open AgentBreeder issues)

| Issue | Impact | Workaround |
|-------|--------|------------|
| [#63](https://github.com/rajitsaha/agentbreeder/issues/63) | Ollama models need manual LiteLlmModel wiring | `agent.py` handles this |
| [#64](https://github.com/rajitsaha/agentbreeder/issues/64) | Ollama not auto-started by `agentbreeder deploy` | Run `ollama serve` manually |
| [#65](https://github.com/rajitsaha/agentbreeder/issues/65) | Model weights not auto-pulled | Run `ollama pull gemma3:27b` manually |
| [#66](https://github.com/rajitsaha/agentbreeder/issues/66) | `root_agent.yaml` can't use Ollama without Python | `agent.py` exists as workaround |
