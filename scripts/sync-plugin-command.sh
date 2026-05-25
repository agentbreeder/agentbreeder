#!/usr/bin/env bash
# Copy the committed plugin source of truth into the local (gitignored) .claude/commands/
# so that the developer's /agent-build slash-command matches the shipped plugin.
# Run this after pulling changes or when .claude/commands/agent-build.md is missing.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .claude/commands
cp plugins/agent-build/commands/agent-build.md .claude/commands/agent-build.md
echo "Synced .claude/commands/agent-build.md from plugins/agent-build/commands/agent-build.md"
