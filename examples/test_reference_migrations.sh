#!/usr/bin/env bash
# End-to-end verification for the 3 reference registry-pattern migrations:
#   - examples/google-adk-agent       (Python ADK)
#   - examples/ai-news-digest          (Python ADK with multiple tools)
#   - examples/registry-pattern-ts     (TypeScript)
#
# Pre-reqs:
#   - postgres + redis up (docker compose -f deploy/docker-compose.yml)
#   - api running on :8000
#   - $AGENTBREEDER_API_TOKEN exported (JWT)
#   - microlearning venv active so `agentbreeder` CLI is on PATH
set -euo pipefail
cd "$(dirname "$0")"

API="${AGENTBREEDER_API_URL:-http://localhost:8000}"
export AGENTBREEDER_API_URL="$API"

if [ -z "${AGENTBREEDER_API_TOKEN:-}" ]; then
  echo "ERROR: AGENTBREEDER_API_TOKEN not set." >&2
  exit 1
fi

pass() { echo "  ✅  $*"; }
fail() { echo "  ❌  $*"; exit 1; }
section() { echo; echo "════════════════════════════════════════════════════════════════"; echo "  $*"; echo "════════════════════════════════════════════════════════════════"; }

section "1. EXAMPLE 1 — google-adk-agent (Python ADK)"
cd google-adk-agent
bash scripts/register.sh > /tmp/reg-google.log 2>&1 && pass "register.sh ran clean" || fail "register.sh failed: see /tmp/reg-google.log"
PYTHONPATH=".:..":"${PYTHONPATH:-}" python3 -c "
import agent
print(' name=', agent.root_agent.name)
print(' model=', agent.root_agent.model)
print(' instruction starts with:', repr(agent.root_agent.instruction[:60]))
print(' tools:', [t.__name__ for t in agent.root_agent.tools])
" && pass "agent.py imports + resolves prompt + tool" || fail "agent.py import failed"
cd ..

section "2. EXAMPLE 2 — ai-news-digest (Python ADK, 4 tools)"
cd ai-news-digest
bash scripts/register.sh > /tmp/reg-ainews.log 2>&1 && pass "register.sh ran clean" || fail "register.sh failed: see /tmp/reg-ainews.log"
PYTHONPATH=".:..":"${PYTHONPATH:-}" python3 -c "
import agent
print(' name=', agent.root_agent.name)
print(' instruction lines=', agent.root_agent.instruction.count('\n')+1)
tools = [t.__name__ for t in agent.root_agent.tools]
print(' tools:', tools)
expected = {'fetch_hackernews', 'fetch_arxiv', 'fetch_rss', 'send_email'}
missing = expected - set(tools)
if missing:
    raise SystemExit(f'missing tools: {missing}')
" && pass "agent.py resolves all 4 tools from local files" || fail "agent.py import failed"
cd ..

section "3. EXAMPLE 3 — registry-pattern-ts (TypeScript)"
cd registry-pattern-ts
bash scripts/register.sh > /tmp/reg-ts.log 2>&1 && pass "register.sh ran clean (prompt + agent only — TS tool push is follow-up)" || fail "register.sh failed: see /tmp/reg-ts.log"

if command -v npx >/dev/null 2>&1; then
  npx --yes tsx agent.ts > /tmp/ts-run.log 2>&1 && pass "tsx agent.ts ran end-to-end" || {
    cat /tmp/ts-run.log
    fail "TS resolver run failed"
  }
  if grep -q "polite TypeScript assistant" /tmp/ts-run.log; then
    pass "resolved prompt body matched ./prompts/ts-greeter-system.md"
  else
    fail "resolved prompt did not match"
  fi
  if grep -q "utc_time" /tmp/ts-run.log; then
    pass "resolved tool returned a UTC timestamp"
  else
    fail "tool call did not return utc_time"
  fi
else
  echo "  ⚠️   npx not installed — skipping TS runtime check"
fi
cd ..

section "4. REGISTRY TOTALS"
agentbreeder registry prompt list
agentbreeder registry tool list
agentbreeder registry agent list

section "✅ ALL CHECKS PASSED"
