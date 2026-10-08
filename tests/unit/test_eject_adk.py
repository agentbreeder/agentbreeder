"""Tests for Google ADK eject scaffold."""

_BASE_YAML = (
    "name: test-agent\nversion: 1.0.0\nframework: google_adk\n"
    "description: A test agent\nteam: eng\nowner: a@b.com\n"
    "model:\n  primary: gemini-2.0-flash\ndeploy:\n  cloud: gcp\n"
)

_DESC_YAML = (
    "name: test-agent\nversion: 1.0.0\nframework: google_adk\n"
    "description: Handles customer queries\nteam: eng\nowner: a@b.com\n"
    "model:\n  primary: gemini-2.0-flash\ndeploy:\n  cloud: gcp\n"
)


def test_generate_adk_writes_agent_py(tmp_path):
    from cli.commands.eject import _generate_google_adk_scaffold

    _generate_google_adk_scaffold(_BASE_YAML, tmp_path)
    agent_py = (tmp_path / "agent.py").read_text()
    assert "LlmAgent" in agent_py


def test_generate_adk_uses_model_from_yaml(tmp_path):
    from cli.commands.eject import _generate_google_adk_scaffold

    _generate_google_adk_scaffold(_BASE_YAML, tmp_path)
    agent_py = (tmp_path / "agent.py").read_text()
    assert "gemini-2.0-flash" in agent_py


def test_generate_adk_writes_requirements(tmp_path):
    from cli.commands.eject import _generate_google_adk_scaffold

    _generate_google_adk_scaffold(_BASE_YAML, tmp_path)
    reqs = (tmp_path / "requirements.txt").read_text()
    assert "google-adk" in reqs


def test_generate_adk_llm_agent_when_no_subagents(tmp_path):
    from cli.commands.eject import _generate_google_adk_scaffold

    _generate_google_adk_scaffold(_BASE_YAML, tmp_path)
    agent_py = (tmp_path / "agent.py").read_text()
    assert "LlmAgent" in agent_py
    assert "SequentialAgent" not in agent_py


def test_generate_adk_root_agent_exported(tmp_path):
    from cli.commands.eject import _generate_google_adk_scaffold

    _generate_google_adk_scaffold(_BASE_YAML, tmp_path)
    agent_py = (tmp_path / "agent.py").read_text()
    assert "root_agent" in agent_py


def test_generate_adk_uses_description(tmp_path):
    from cli.commands.eject import _generate_google_adk_scaffold

    _generate_google_adk_scaffold(_DESC_YAML, tmp_path)
    agent_py = (tmp_path / "agent.py").read_text()
    assert "Handles customer queries" in agent_py
