"""Smoke tests for microlearning-ebook-agent.

These tests verify the project structure and that the agent module loads cleanly.
They do NOT make external API calls -- see tests/test_live.py for that.
"""

import importlib
from pathlib import Path


def test_agent_yaml_exists():
    assert Path("agent.yaml").exists()


def test_env_example_exists():
    assert Path(".env.example").exists()


def test_layout_json_exists():
    assert Path(".agentbreeder/layout.json").exists()


def test_pyproject_toml_exists():
    assert Path("pyproject.toml").exists()


def test_standard_tools_resolvable():
    """The agent's tools must resolve from engine.tools.standard via the resolver."""
    from engine.tool_resolver import resolve_tool

    web_search = resolve_tool("tools/web-search")
    md_writer = resolve_tool("tools/markdown-writer")
    assert callable(web_search)
    assert callable(md_writer)
    assert web_search.__name__ == "web_search"
    assert md_writer.__name__ == "markdown_writer"


def test_root_agent_loads():
    """agent.py must export a root_agent with the expected wiring.

    This is what the AgentBreeder runtime wrapper looks for at startup.
    """
    mod = importlib.import_module("agent")
    assert hasattr(mod, "root_agent"), "agent.py must export root_agent"

    agent = mod.root_agent
    assert agent.name == "microlearning_ebook_agent"
    assert "gemini" in agent.model.lower()
    tool_names = [t.__name__ for t in agent.tools]
    assert "web_search" in tool_names, "should pull web_search from engine.tools.standard"
    assert "markdown_writer" in tool_names, (
        "should pull markdown_writer from engine.tools.standard"
    )


def test_agent_yaml_uses_tool_refs():
    """agent.yaml must list tools as registry refs, not inline definitions."""
    yaml_content = Path("agent.yaml").read_text(encoding="utf-8")
    assert "ref: tools/web-search" in yaml_content
    assert "ref: tools/markdown-writer" in yaml_content


def test_system_prompt_resolved_from_registry_ref():
    """The system instruction must be loaded from prompts/<name>.md, not inline.

    This proves the agent uses the prompt registry pattern rather than baking
    the prompt into the source code.
    """
    mod = importlib.import_module("agent")
    instruction = mod.root_agent.instruction
    prompt_file = Path("prompts/microlearning-system.md")
    assert prompt_file.exists(), "prompt file is the source of truth — must exist"
    file_content = prompt_file.read_text(encoding="utf-8")
    assert instruction.strip() == file_content.strip(), (
        "agent.instruction must equal the resolved prompt file content"
    )


def test_prompt_file_referenced_in_agent_yaml():
    """agent.yaml must reference the registry, not embed the prompt inline."""
    yaml_content = Path("agent.yaml").read_text(encoding="utf-8")
    assert "system: prompts/microlearning-system" in yaml_content, (
        "agent.yaml must reference the registry: 'system: prompts/<name>'"
    )
    assert "instructional designer" not in yaml_content, (
        "agent.yaml must not embed the prompt body inline -- it lives in "
        "prompts/microlearning-system.md"
    )


def test_render_ebook_writes_file(tmp_path, monkeypatch):
    """render_ebook is pure I/O -- safe to test offline."""
    monkeypatch.setenv("EBOOK_OUTPUT_DIR", str(tmp_path))
    # Re-import so the module picks up the new env
    import importlib

    import tools.render_ebook

    importlib.reload(tools.render_ebook)
    from tools.render_ebook import render_ebook

    result = render_ebook(
        title="Zero Trust Networking",
        markdown_content="# Zero Trust\n\nIntro lesson.",
        fmt="md",
    )
    assert result["format"] == "md"
    assert result["byte_size"] > 0
    assert Path(result["path"]).exists()
    assert "zero-trust-networking" in result["path"]
