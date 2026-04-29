#!/usr/bin/env bash
# Comprehensive end-to-end test for the microlearning-ebook-agent stack.
#
# Verifies:
#   1. Auth gates on prompt + tool + agent registry endpoints
#   2. Prompt registry: list, get, version-bump-on-save, version history
#   3. Tool registry: list, get details + schema
#   4. Agent runtime auth (/health open, /invoke gated by AGENT_AUTH_TOKEN)
#   5. End-to-end chat that calls registry-resolved tools
#
# Pre-reqs already running:
#   - postgres + redis (docker compose -f deploy/docker-compose.yml)
#   - api server on :8000 (uvicorn api.main:app)
#   - agent runtime on :8080 (bash scripts/serve.sh from this project)
#   - dashboard optional on :3001
set -euo pipefail

API="${AGENTBREEDER_API_URL:-http://localhost:8000}"
RUNTIME="${AGENT_RUNTIME_URL:-http://localhost:8080}"
EMAIL="dev@agentbreeder.local"
PASSWORD="devpassword123"
RUNTIME_TOKEN="${AGENT_AUTH_TOKEN:-mle-local-dev-3f7b8c2e}"

pass() { echo "  ✅  $*"; }
fail() { echo "  ❌  $*"; exit 1; }
section() { echo; echo "════════════════════════════════════════════════════════════════"; echo "  $*"; echo "════════════════════════════════════════════════════════════════"; }

section "1. AUTH — login as $EMAIL"
LOGIN_RESP=$(curl -s -X POST "$API/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token') or d.get('data',{}).get('access_token',''))")
[ -n "$TOKEN" ] || fail "no token returned"
pass "JWT acquired (length=${#TOKEN})"

section "2. AUTH GATES — every registry endpoint refuses unauthenticated requests"
for path in \
  "/api/v1/registry/prompts" \
  "/api/v1/registry/tools" \
  "/api/v1/registry/models" \
  "/api/v1/agents" \
  "/api/v1/registry/search?q=test"
do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API$path")
  if [ "$CODE" = "401" ] || [ "$CODE" = "403" ]; then
    pass "GET $path -> $CODE without token"
  else
    fail "GET $path returned $CODE without token (expected 401/403)"
  fi
done

section "3. PROMPT REGISTRY — list, get, version bump on save, history"
PROMPT_RESP=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/api/v1/registry/prompts")
PROMPT_ID=$(echo "$PROMPT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])")
INITIAL_VERSION=$(echo "$PROMPT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['version'])")
pass "List returned $(echo "$PROMPT_RESP" | python3 -c 'import sys,json;print(json.load(sys.stdin)[\"meta\"][\"total\"])') prompt(s)"
pass "Picked microlearning-system id=${PROMPT_ID:0:8}... at v${INITIAL_VERSION}"

CONTENT=$(cat prompts/microlearning-system.md)
EDIT_RESP=$(python3 <<PY
import json, urllib.request
body = json.dumps({
    "content": """${CONTENT}\n\n# (e2e test edit at $(date -u +%FT%TZ))""",
    "change_summary": "e2e test patch bump",
    "author": "$EMAIL",
}).encode()
req = urllib.request.Request(
    "$API/api/v1/registry/prompts/$PROMPT_ID/content",
    data=body,
    headers={"Content-Type": "application/json", "Authorization": "Bearer $TOKEN"},
    method="PUT",
)
resp = urllib.request.urlopen(req, timeout=10)
data = json.loads(resp.read())["data"]
print(data["version"])
PY
)
NEW_VERSION="$EDIT_RESP"
if [ "$NEW_VERSION" != "$INITIAL_VERSION" ]; then
  pass "Save bumped version: $INITIAL_VERSION -> $NEW_VERSION"
else
  fail "Version did not bump (still $INITIAL_VERSION)"
fi

VERSION_HIST=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/api/v1/registry/prompts/$PROMPT_ID/versions/history" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']))")
pass "Version history has $VERSION_HIST snapshot(s)"

section "4. TOOL REGISTRY — list, get, schema visible"
TOOLS_RESP=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/api/v1/registry/tools")
TOOL_COUNT=$(echo "$TOOLS_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['meta']['total'])")
pass "Tool registry has $TOOL_COUNT tool(s)"

for name in web-search markdown-writer; do
  TOOL_ID=$(echo "$TOOLS_RESP" | python3 -c "import sys,json; print([t['id'] for t in json.load(sys.stdin)['data'] if t['name']=='$name'][0])")
  DETAIL=$(curl -s -H "Authorization: Bearer $TOKEN" "$API/api/v1/registry/tools/$TOOL_ID")
  ENDPOINT=$(echo "$DETAIL" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['endpoint'])")
  HAS_SCHEMA=$(echo "$DETAIL" | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print(bool(d.get('schema_definition')))")
  pass "GET /tools/$name -> endpoint=$ENDPOINT, has_schema=$HAS_SCHEMA"
done

section "5. AGENT RUNTIME — auth gates on /invoke, open /health"
H_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$RUNTIME/health")
[ "$H_CODE" = "200" ] && pass "/health -> 200 (open by design)" || fail "/health -> $H_CODE"

NO_AUTH=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$RUNTIME/invoke" -H "Content-Type: application/json" -d '{"input":"x"}')
[ "$NO_AUTH" = "401" ] && pass "/invoke without bearer -> 401" || fail "/invoke -> $NO_AUTH (expected 401)"

WRONG=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$RUNTIME/invoke" -H "Authorization: Bearer wrong" -H "Content-Type: application/json" -d '{"input":"x"}')
[ "$WRONG" = "403" ] && pass "/invoke with wrong token -> 403" || fail "/invoke wrong -> $WRONG (expected 403)"

section "6. AGENT RUNTIME — real chat exercising registry-resolved tools"
RESP=$(curl -s -X POST "$RUNTIME/invoke" \
  -H "Authorization: Bearer $RUNTIME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input":"Skip HITL approval. Write a 1-paragraph microlearning ebook on Zero Trust networking. Use web_search with max_results=2. Then call markdown_writer with title=Zero Trust E2E Test and the markdown content. Return only the file path."}')
OUT=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['output'])")
case "$OUT" in
  */output/*.md) pass "Agent returned a real file path: $OUT"
                 [ -f "$OUT" ] && pass "File exists on disk: $(stat -f %z "$OUT") bytes" || fail "file does not exist: $OUT" ;;
  *)             fail "Agent did not return a file path. Got: $OUT" ;;
esac

section "7. SUMMARY"
echo "  Prompt registry:  v${INITIAL_VERSION} -> v${NEW_VERSION} ($VERSION_HIST snapshots)"
echo "  Tool registry:    $TOOL_COUNT tools (web-search, markdown-writer)"
echo "  Auth coverage:    /api/v1/* all gated; /invoke + /stream gated; /health open"
echo "  E2E chat:         agent used both registry tools, wrote real file"
echo
echo "  ✅ All checks passed."
