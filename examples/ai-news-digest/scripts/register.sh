#!/usr/bin/env bash
# Register this agent's prompt + 4 tools + agent definition with the registry.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "════ Registering ai-news-digest ════"
agentbreeder registry prompt push prompts/ai-news-digest-system.md \
  --version 1.0.0 --team examples \
  --description "AI news curator: HN + ArXiv + RSS digest, emailed daily"

for tool in fetch_hackernews fetch_arxiv fetch_rss send_email; do
  agentbreeder registry tool push "tools/${tool}.py"
done

agentbreeder registry agent push agent.yaml

echo
echo "Done. Verify in the dashboard at http://localhost:3001/{prompts,tools,agents}"
