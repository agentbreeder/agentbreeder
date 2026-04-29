#!/usr/bin/env bash
# Register this example's prompt + tool + agent with the AgentBreeder registry.
#
# Pre-reqs:
#   - AgentBreeder API running at $AGENTBREEDER_API_URL (default http://localhost:8000)
#   - $AGENTBREEDER_API_TOKEN exported (JWT from /api/v1/auth/login)
set -euo pipefail

cd "$(dirname "$0")/.."

echo "════ Registering gemini-assistant ════"
echo
agentbreeder registry prompt push prompts/gemini-assistant-system.md \
  --version 1.0.0 --team examples \
  --description "Helpful assistant with current-time tool"

agentbreeder registry tool push tools/get_current_time.py \
  --description "Returns the current UTC time as ISO 8601"

agentbreeder registry agent push agent.yaml

echo
echo "Done. Verify in the dashboard at http://localhost:3001/{prompts,tools,agents}"
