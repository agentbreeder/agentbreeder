#!/usr/bin/env bash
# API-level Docker E2E smoke — exercises the same auth + agent registry flow
# as the (now-skipped) Playwright auth.spec.ts and agents.spec.ts, but via
# direct curl against the running stack. No browser, no DOM polling — fast
# and reliable on slow CI runners.
#
# Pre-reqs: docker compose stack already up and reachable at:
#   API:       http://localhost:8000
#   Dashboard: http://localhost:3001
#
# Used by .github/workflows/ci.yml (E2E Tests (Docker) job) alongside the
# slimmed-down Playwright health.spec.ts.
set -euo pipefail

API="${API_BASE_URL:-http://localhost:8000}"
DASH="${DASHBOARD_BASE_URL:-http://localhost:3001}"

pass() { printf "  \033[32m✓\033[0m  %s\n" "$*"; }
fail() { printf "  \033[31m✗\033[0m  %s\n" "$*"; exit 1; }

echo "════ Stack reachability ════"
curl -sf "$API/api/v1/health" >/dev/null && pass "API /health 200" || fail "API not reachable"
curl -sf "$DASH/" >/dev/null         && pass "Dashboard / 200"   || fail "Dashboard not reachable"

echo
echo "════ Auth flow ════"
EMAIL="e2e-$(date +%s%N | head -c12)@test.local"
PASSWORD="Test1234!"

REG_BODY=$(printf '{"email":"%s","password":"%s","name":"E2E"}' "$EMAIL" "$PASSWORD")
REG_STATUS=$(curl -s -o /tmp/reg.json -w "%{http_code}" -X POST "$API/api/v1/auth/register" \
  -H "Content-Type: application/json" -d "$REG_BODY")
[ "$REG_STATUS" = "201" ] && pass "register 201" || fail "register $REG_STATUS — $(cat /tmp/reg.json)"

LOGIN_BODY=$(printf '{"email":"%s","password":"%s"}' "$EMAIL" "$PASSWORD")
TOKEN=$(curl -sf -X POST "$API/api/v1/auth/login" \
  -H "Content-Type: application/json" -d "$LOGIN_BODY" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token') or d.get('data',{}).get('access_token',''))")
[ -n "$TOKEN" ] && pass "login -> JWT acquired (length=${#TOKEN})" || fail "login returned no token"

echo
echo "════ Auth gate sanity ════"
NO_AUTH=$(curl -s -o /dev/null -w "%{http_code}" "$API/api/v1/agents")
[ "$NO_AUTH" = "401" ] && pass "/api/v1/agents without bearer -> 401" || fail "expected 401, got $NO_AUTH"

WITH_AUTH=$(curl -s -o /dev/null -w "%{http_code}" "$API/api/v1/agents" -H "Authorization: Bearer $TOKEN")
[ "$WITH_AUTH" = "200" ] && pass "/api/v1/agents with bearer -> 200" || fail "expected 200, got $WITH_AUTH"

echo
echo "════ Agent registry ════"
NAME="e2e-smoke-$(date +%s)"
YAML=$(cat <<EOF
{
  "yaml_content": "name: $NAME\nversion: 0.1.0\nframework: custom\nteam: e2e\nowner: $EMAIL\nmodel:\n  primary: gemini-2.5-flash\ndeploy:\n  cloud: local\n  runtime: docker-compose\n"
}
EOF
)
CREATE_STATUS=$(curl -s -o /tmp/agent.json -w "%{http_code}" -X POST "$API/api/v1/agents/from-yaml" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$YAML")
[ "$CREATE_STATUS" = "201" ] && pass "POST /agents/from-yaml -> 201" || fail "from-yaml $CREATE_STATUS — $(cat /tmp/agent.json | head -c 300)"

AGENT_ID=$(python3 -c "import json; print(json.load(open('/tmp/agent.json'))['data']['id'])")
[ -n "$AGENT_ID" ] && pass "agent id parsed: $AGENT_ID" || fail "no id in response"

curl -sf "$API/api/v1/agents/$AGENT_ID" -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; assert d['name']=='$NAME', d" \
  && pass "GET /agents/{id} returns the just-created agent" || fail "GET /agents/{id} mismatch"

LIST_HAS=$(curl -sf "$API/api/v1/agents" -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(any(a['name']=='$NAME' for a in d['data']))")
[ "$LIST_HAS" = "True" ] && pass "agent appears in /agents list" || fail "agent missing from list"

echo
echo "════════════════════════════════════════════════"
echo "  ✅ All API E2E smoke checks passed"
echo "════════════════════════════════════════════════"
