#!/usr/bin/env bash
# Run the AgentBreeder Google ADK runtime locally with this project's agent.
#
# Usage:
#   bash scripts/serve.sh
#
# The server listens on http://localhost:8080.
# /health is unauthenticated. /invoke and /stream require:
#   Authorization: Bearer ${AGENT_AUTH_TOKEN}
# (set in .env -- the launcher loads it).
set -euo pipefail

cd "$(dirname "$0")/.."

# Activate venv if present
if [ -d "venv" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# Load env (keys, AGENT_AUTH_TOKEN, model overrides)
set -a
# shellcheck disable=SC1091
source .env
set +a

# Project root for agent.py + tools/
AGENT_PROJECT="$(pwd)"

# AgentBreeder repo root for engine.* imports (assumes this project lives inside it)
AGENTBREEDER_REPO="$(cd .. && pwd)"

# PYTHONPATH must include both so `import agent` AND `from engine.config_parser import ToolRef` work.
export PYTHONPATH="${AGENT_PROJECT}:${AGENTBREEDER_REPO}:${PYTHONPATH:-}"

# Default ADK config (in-memory session/memory/artifacts for local dev)
export AGENTBREEDER_ADK_CONFIG='{"session_backend":"memory","memory_service":"memory","artifact_service":"memory"}'
export AGENT_NAME="${AGENT_NAME:-microlearning-ebook-agent}"
export AGENT_VERSION="${AGENT_VERSION:-0.1.0}"

echo "Starting Google ADK runtime on http://localhost:8080"
echo "  Agent project: ${AGENT_PROJECT}"
echo "  Agentbreeder: ${AGENTBREEDER_REPO}"
echo "  Auth: $([ -n "${AGENT_AUTH_TOKEN:-}" ] && echo "enabled (Bearer)" || echo "disabled (set AGENT_AUTH_TOKEN to enable)")"
echo

exec uvicorn engine.runtimes.templates.google_adk_server:app \
  --host 0.0.0.0 \
  --port 8080 \
  --log-level info
