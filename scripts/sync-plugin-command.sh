#!/usr/bin/env bash
# Re-copy the canonical /agent-build command to the distributable plugin.
# Run this after editing .claude/commands/agent-build.md so the plugin copy stays in sync.
set -euo pipefail
cd "$(dirname "$0")/.."
cp .claude/commands/agent-build.md plugins/agent-build/commands/agent-build.md
echo "Synced plugins/agent-build/commands/agent-build.md from .claude/commands/agent-build.md"
