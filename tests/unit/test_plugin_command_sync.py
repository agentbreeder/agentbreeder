import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_plugin_agent_build_command_exists_and_has_frontmatter():
    """The committed plugin command file must exist, be non-empty, and open with a YAML
    frontmatter block that contains a description field.  This is the git-tracked source
    of truth; .claude/commands/ is gitignored and must NOT be read here."""
    command_file = REPO / "plugins/agent-build/commands/agent-build.md"
    assert command_file.exists(), f"Missing committed plugin command: {command_file}"

    text = command_file.read_text()
    assert text.strip(), "plugins/agent-build/commands/agent-build.md must not be empty"

    # Must start with a YAML frontmatter block
    assert text.startswith("---"), (
        "plugins/agent-build/commands/agent-build.md must begin with a YAML "
        "frontmatter block (---)"
    )

    # Frontmatter must contain a description field
    end = text.find("---", 3)
    assert end != -1, "Frontmatter block is not closed (missing second ---)"
    frontmatter = text[3:end]
    assert "description:" in frontmatter, (
        "Frontmatter in plugins/agent-build/commands/agent-build.md must include "
        "a 'description:' field"
    )


def test_plugin_manifest_is_valid():
    manifest = json.loads((REPO / "plugins/agent-build/.claude-plugin/plugin.json").read_text())
    assert manifest["name"] == "agent-build"
    market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
    assert any(p["name"] == "agent-build" for p in market["plugins"])
