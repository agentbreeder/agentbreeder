#!/usr/bin/env bash
# Smoke test for deploy/docker-compose.standalone.yml
#
# Verifies that the standalone compose file (which uses pre-built Docker Hub
# images) can start the full platform without a repo clone and that both the
# health endpoint and the agents API respond correctly.
#
# Usage:
#   bash tests/e2e/test_standalone_compose.sh
#
# Requirements: docker, curl, jq
# Expected runtime: ~60-90s (image pull + service startup)
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed

set -euo pipefail

COMPOSE_FILE="deploy/docker-compose.standalone.yml"
PROJECT_NAME="agentbreeder_smoke_$$"
API_URL="http://localhost:8000"
DASHBOARD_URL="http://localhost:3001"
PASS=0
FAIL=0

log()  { echo "[smoke] $*"; }
ok()   { echo "[PASS] $*"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $*"; FAIL=$((FAIL + 1)); }

cleanup() {
  log "Tearing down..."
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

# --- Start stack ---
log "Starting standalone compose stack (project: $PROJECT_NAME)..."
docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d

# --- Wait for API health ---
log "Waiting for API to become healthy (up to 90s)..."
DEADLINE=$(( $(date +%s) + 90 ))
until curl -sf "$API_URL/health" >/dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    fail "API did not become healthy within 90s"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" logs api
    exit 1
  fi
  sleep 3
done
ok "API health endpoint responded"

# --- Check health response body ---
HEALTH=$(curl -sf "$API_URL/health")
if echo "$HEALTH" | grep -qi "ok\|healthy\|alive"; then
  ok "API health body looks correct"
else
  fail "API health body unexpected: $HEALTH"
fi

# --- Check agents endpoint ---
AGENTS_STATUS=$(curl -o /dev/null -sw "%{http_code}" "$API_URL/api/v1/agents" 2>/dev/null)
if [ "$AGENTS_STATUS" = "200" ]; then
  ok "GET /api/v1/agents returned 200"
else
  fail "GET /api/v1/agents returned $AGENTS_STATUS (expected 200)"
fi

# --- Check agents response is valid JSON ---
AGENTS_BODY=$(curl -sf "$API_URL/api/v1/agents" 2>/dev/null || echo "")
if echo "$AGENTS_BODY" | python3 -c "import sys, json; json.load(sys.stdin)" 2>/dev/null; then
  ok "GET /api/v1/agents response is valid JSON"
else
  fail "GET /api/v1/agents response is not valid JSON: $AGENTS_BODY"
fi

# --- Check dashboard ---
DASH_STATUS=$(curl -o /dev/null -sw "%{http_code}" "$DASHBOARD_URL/" 2>/dev/null)
if [ "$DASH_STATUS" = "200" ]; then
  ok "Dashboard root returned 200"
else
  fail "Dashboard root returned $DASH_STATUS (expected 200)"
fi

# --- Summary ---
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
