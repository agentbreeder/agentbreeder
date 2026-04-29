#!/usr/bin/env bash
# Push generic tool metadata to the AgentBreeder tool registry.
#
# Usage:
#   AGENTBREEDER_API_TOKEN=<jwt> bash scripts/register_tools.sh
#
# Pushes the standard-library tools the agent actually uses:
#   - tools/web-search       (engine.tools.standard.web_search)
#   - tools/markdown-writer  (engine.tools.standard.markdown_writer)
#
# After running, the dashboard at /tools shows them as discoverable / shareable.
set -euo pipefail

cd "$(dirname "$0")/.."

API_URL="${AGENTBREEDER_API_URL:-http://localhost:8000}"
TOKEN="${AGENTBREEDER_API_TOKEN:?Set AGENTBREEDER_API_TOKEN to a logged-in JWT}"

echo "Registering tools at ${API_URL}/api/v1/registry/tools"

python3 <<PY
import json, os, urllib.request, urllib.error, sys

API = "${API_URL}".rstrip("/")
TOKEN = "${TOKEN}"

# Pull schemas from the standard-library modules so they stay in sync with code.
sys.path.insert(0, "..")
import engine.tools.standard.web_search as ws_mod
import engine.tools.standard.markdown_writer as mw_mod

tools = [
    {
        "name": "web-search",
        "description": (
            "Generic web search via Tavily. Returns ranked sources + a "
            "distilled answer for any topic. Use for research synthesis, "
            "fact lookup, and citation gathering."
        ),
        "tool_type": "function",
        "schema_definition": ws_mod.SCHEMA,
        "endpoint": "engine.tools.standard.web_search",
        "source": "manual",
    },
    {
        "name": "markdown-writer",
        "description": (
            "Persists model-produced markdown content to a slug-named file "
            "under DOCUMENT_OUTPUT_DIR. Generic file writer — works for any "
            "agent that produces markdown (ebooks, reports, notes, briefs)."
        ),
        "tool_type": "function",
        "schema_definition": mw_mod.SCHEMA,
        "endpoint": "engine.tools.standard.markdown_writer",
        "source": "manual",
    },
]

for tool in tools:
    body = json.dumps(tool).encode()
    req = urllib.request.Request(
        f"{API}/api/v1/registry/tools",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())["data"]
        print(f"  [{resp.status}] {data['name']:20s}  id={data['id']}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 409 or "already exists" in body.lower():
            print(f"  [skip] {tool['name']:20s}  already registered")
        else:
            print(f"  [{e.code}] {tool['name']:20s}  {body[:200]}")

print()
print("Done. View in the dashboard: ${API_URL/8000/3001}/tools")
PY
