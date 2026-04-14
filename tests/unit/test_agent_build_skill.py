"""Structural tests for the /agent-build skill file.

Validates that the skill file contains all required sections
for both fast path and advisory path flows.
"""

from pathlib import Path

SKILL_FILE = Path(__file__).parents[2] / ".claude/commands/agent-build.md"


def skill_content() -> str:
    return SKILL_FILE.read_text()


def test_skill_file_exists():
    assert SKILL_FILE.exists(), f"Skill file not found: {SKILL_FILE}"


def test_fast_path_preserved():
    content = skill_content()
    assert "I know my stack" in content


def test_advisory_path_present():
    content = skill_content()
    assert "Recommend for me" in content


def test_all_six_advisory_questions_present():
    content = skill_content()
    questions = [
        "Business Goal",
        "Technical Use Case",
        "State Complexity",
        "Team",
        "Data Access",
        "Scale",
    ]
    for q in questions:
        assert q in content, f"Missing advisory question: {q}"


def test_recommendation_dimensions_present():
    content = skill_content()
    dimensions = ["Framework", "Model", "RAG", "Memory", "MCP", "Deploy", "Eval"]
    for dim in dimensions:
        assert dim in content, f"Missing recommendation dimension: {dim}"


def test_new_scaffold_outputs_present():
    content = skill_content()
    outputs = [
        "memory/",
        "rag/",
        "tests/evals/",
        "ARCHITECT_NOTES",
        "CLAUDE.md",
        "AGENTS.md",
        ".cursorrules",
        ".antigravity.md",
    ]
    for output in outputs:
        assert output in content, f"Missing scaffold output: {output}"


def test_ide_config_templates_present():
    content = skill_content()
    for name in ["CLAUDE.md", "AGENTS.md", ".cursorrules", ".antigravity.md"]:
        assert name in content, f"Missing IDE config template: {name}"


def test_architect_notes_template_present():
    content = skill_content()
    assert "ARCHITECT_NOTES" in content, "Missing ARCHITECT_NOTES section"
    assert "Business Goal" in content, "Missing Business Goal in ARCHITECT_NOTES"
    assert "## Why" in content, "Missing '## Why' markdown heading in ARCHITECT_NOTES"
