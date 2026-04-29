#!/usr/bin/env bash
# Push the local system prompt file to the AgentBreeder prompt registry.
#
# Usage:
#   bash scripts/register_prompt.sh                            # registers v1.0.0
#   bash scripts/register_prompt.sh 1.1.0                      # custom version
#   AGENTBREEDER_API_URL=https://api.agentbreeder.io bash scripts/register_prompt.sh
#
# Prereqs: an AgentBreeder API server reachable at AGENTBREEDER_API_URL
# (default http://localhost:8000) with an authenticated session — set
# AGENTBREEDER_API_TOKEN if your environment requires it.
set -euo pipefail

cd "$(dirname "$0")/.."

API_URL="${AGENTBREEDER_API_URL:-http://localhost:8000}"
PROMPT_NAME="microlearning-system"
PROMPT_FILE="prompts/${PROMPT_NAME}.md"
VERSION="${1:-1.0.0}"
TEAM="$(grep '^team:' agent.yaml | awk '{print $2}')"
DESCRIPTION="System prompt for the microlearning-ebook-agent (instructional-designer persona, research+render workflow)."

if [ ! -f "$PROMPT_FILE" ]; then
  echo "ERROR: prompt file not found: $PROMPT_FILE" >&2
  exit 1
fi

CONTENT="$(cat "$PROMPT_FILE")"

AUTH_ARGS=()
if [ -n "${AGENTBREEDER_API_TOKEN:-}" ]; then
  AUTH_ARGS+=(-H "Authorization: Bearer ${AGENTBREEDER_API_TOKEN}")
fi

echo "Registering prompt:"
echo "  name:        ${PROMPT_NAME}"
echo "  version:     ${VERSION}"
echo "  team:        ${TEAM}"
echo "  bytes:       $(printf '%s' "$CONTENT" | wc -c | tr -d ' ')"
echo "  registry:    ${API_URL}/api/v1/registry/prompts"
echo

# Build the JSON body with jq so the multi-line prompt is escaped correctly.
if command -v jq >/dev/null 2>&1; then
  BODY="$(jq -n \
    --arg name "$PROMPT_NAME" \
    --arg version "$VERSION" \
    --arg content "$CONTENT" \
    --arg description "$DESCRIPTION" \
    --arg team "$TEAM" \
    '{name:$name, version:$version, content:$content, description:$description, team:$team}')"
else
  echo "ERROR: jq is required (brew install jq)" >&2
  exit 1
fi

curl -fsS -X POST "${API_URL}/api/v1/registry/prompts" \
  "${AUTH_ARGS[@]}" \
  -H "Content-Type: application/json" \
  -d "$BODY"
echo
echo
echo "Done. Verify with:"
echo "  curl -s ${API_URL}/api/v1/registry/prompts | jq '.data[] | select(.name==\"${PROMPT_NAME}\")'"
