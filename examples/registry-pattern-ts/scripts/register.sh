#!/usr/bin/env bash
# Push the TS example's prompt + agent to the registry.
# (No CLI tool-push for TS yet — that needs a JS module loader extension to
# extract SCHEMA exports. Tracked as follow-up.)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "════ Registering ts-greeter ════"
agentbreeder registry prompt push prompts/ts-greeter-system.md \
  --version 1.0.0 --team examples \
  --description "Polite TS assistant with a single get_utc_time tool"

agentbreeder registry agent push agent.yaml

echo
echo "Note: TS tool registration via CLI is a follow-up. The local-file resolver"
echo "(./tools/get_utc_time.ts) works without registry registration."
