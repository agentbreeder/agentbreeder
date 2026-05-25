from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_plugin_agent_build_matches_canonical():
    canonical = (REPO / ".claude/commands/agent-build.md").read_text()
    plugin = (REPO / "plugins/agent-build/commands/agent-build.md").read_text()
    assert plugin == canonical, (
        "plugins/agent-build/commands/agent-build.md has drifted from "
        ".claude/commands/agent-build.md — re-run scripts/sync-plugin-command.sh"
    )


def test_plugin_manifest_is_valid():
    import json

    manifest = json.loads(
        (REPO / "plugins/agent-build/.claude-plugin/plugin.json").read_text()
    )
    assert manifest["name"] == "agent-build"
    market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
    assert any(p["name"] == "agent-build" for p in market["plugins"])
