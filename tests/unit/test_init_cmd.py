"""Tests for the agentbreeder init command."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


def _make_init_input(
    framework: int = 1,
    cloud: int = 1,
    name: str = "test-agent",
    team: str = "engineering",
    owner: str = "test@example.com",
) -> str:
    """Build the interactive input string for the init wizard."""
    return f"{framework}\n{cloud}\n{name}\n{team}\n{owner}\n"


class TestInitCommand:
    """Tests for agentbreeder init."""

    def test_init_creates_all_files(self) -> None:
        """Init should create agent.yaml, agent.py, requirements.txt, .env.example, README.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=_make_init_input(),
            )
            assert result.exit_code == 0, result.output
            assert (outdir / "agent.yaml").exists()
            assert (outdir / "agent.py").exists()
            assert (outdir / "requirements.txt").exists()
            assert (outdir / ".env.example").exists()
            assert (outdir / "README.md").exists()

    def test_init_agent_yaml_is_valid(self) -> None:
        """The generated agent.yaml must pass validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=_make_init_input(),
            )
            assert result.exit_code == 0, result.output
            assert "validated" in result.output

    def test_init_shows_next_steps(self) -> None:
        """Init should show next steps with exact commands."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=_make_init_input(),
            )
            assert result.exit_code == 0, result.output
            assert "Next steps" in result.output
            assert "agentbreeder deploy" in result.output
            assert "pip install" in result.output

    @pytest.mark.parametrize(
        "fw_num,fw_key",
        [
            (1, "langgraph"),
            (2, "crewai"),
            (3, "claude_sdk"),
            (4, "openai_agents"),
            (5, "google_adk"),
            (6, "custom"),
        ],
    )
    def test_init_all_frameworks(self, fw_num: int, fw_key: str) -> None:
        """Each framework should produce a valid agent.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=_make_init_input(framework=fw_num),
            )
            assert result.exit_code == 0, result.output

            yaml_content = (outdir / "agent.yaml").read_text()
            assert f"framework: {fw_key}" in yaml_content

            # agent.py should exist and be non-empty
            agent_py = (outdir / "agent.py").read_text()
            assert len(agent_py) > 50

    @pytest.mark.parametrize(
        "cloud_num,cloud_key",
        [
            (1, "local"),
            (2, "aws"),
            (3, "gcp"),
            (4, "kubernetes"),
        ],
    )
    def test_init_all_clouds(self, cloud_num: int, cloud_key: str) -> None:
        """Each cloud target should be written to agent.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=_make_init_input(cloud=cloud_num),
            )
            assert result.exit_code == 0, result.output

            yaml_content = (outdir / "agent.yaml").read_text()
            assert f"cloud: {cloud_key}" in yaml_content

    def test_init_json_output(self) -> None:
        """--json flag should produce output containing JSON with project info."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir), "--json"],
                input=_make_init_input(),
            )
            assert result.exit_code == 0, result.output

            # The output mixes interactive prompts with JSON.
            # Verify key JSON fields appear in the raw output.
            assert '"name": "test-agent"' in result.output
            assert '"framework": "langgraph"' in result.output
            assert '"cloud": "local"' in result.output
            assert '"valid": true' in result.output
            # Files should still be created
            assert (outdir / "agent.yaml").exists()

    def test_init_custom_team_and_owner(self) -> None:
        """Custom team and owner should appear in agent.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=_make_init_input(team="platform", owner="alice@corp.com"),
            )
            assert result.exit_code == 0, result.output

            yaml_content = (outdir / "agent.yaml").read_text()
            assert "team: platform" in yaml_content
            assert "owner: alice@corp.com" in yaml_content

    def test_init_requirements_match_framework(self) -> None:
        """requirements.txt should contain framework-specific dependencies."""
        framework_deps = {
            1: "langgraph",
            2: "crewai",
            3: "anthropic",
            4: "openai-agents",
            5: "google-adk",
        }
        for fw_num, expected_dep in framework_deps.items():
            with tempfile.TemporaryDirectory() as tmpdir:
                outdir = Path(tmpdir) / "test-agent"
                result = runner.invoke(
                    app,
                    ["init", str(outdir)],
                    input=_make_init_input(framework=fw_num),
                )
                assert result.exit_code == 0, result.output

                reqs = (outdir / "requirements.txt").read_text()
                assert expected_dep in reqs, (
                    f"Expected '{expected_dep}' in requirements for framework {fw_num}"
                )

    def test_init_env_example_has_correct_key(self) -> None:
        """The .env.example should have the right API key placeholder."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Claude SDK → ANTHROPIC_API_KEY
            outdir = Path(tmpdir) / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=_make_init_input(framework=3),
            )
            assert result.exit_code == 0, result.output
            env = (outdir / ".env.example").read_text()
            assert "ANTHROPIC_API_KEY" in env

    def test_init_readme_mentions_framework(self) -> None:
        """README should reference the chosen framework."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=_make_init_input(framework=1),
            )
            assert result.exit_code == 0, result.output
            readme = (outdir / "README.md").read_text()
            assert "LangGraph" in readme

    def test_init_invalid_name_reprompts(self) -> None:
        """Invalid agent name should cause a reprompt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            # First enter invalid name (uppercase), then valid name
            input_str = "1\n1\nBAD_NAME\ntest-agent\nengineering\ntest@example.com\n"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=input_str,
            )
            assert result.exit_code == 0, result.output

    def test_init_invalid_email_reprompts(self) -> None:
        """Invalid email should cause a reprompt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            # First enter invalid email, then valid
            input_str = "1\n1\ntest-agent\nengineering\nnot-an-email\ntest@example.com\n"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=input_str,
            )
            assert result.exit_code == 0, result.output

    def test_init_nonempty_dir_abort(self) -> None:
        """If target dir is not empty, asking to abort should exit cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            outdir.mkdir()
            (outdir / "existing.txt").write_text("hello")

            # Answer 'n' to the overwrite prompt
            input_str = "1\n1\ntest-agent\nengineering\ntest@example.com\nn\n"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=input_str,
            )
            assert result.exit_code == 0
            # Should not have created agent.yaml
            assert not (outdir / "agent.yaml").exists()

    def test_init_nonempty_dir_continue(self) -> None:
        """If target dir is not empty, answering 'y' should continue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            outdir.mkdir()
            (outdir / "existing.txt").write_text("hello")

            input_str = "1\n1\ntest-agent\nengineering\ntest@example.com\ny\n"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=input_str,
            )
            assert result.exit_code == 0
            assert (outdir / "agent.yaml").exists()

    def test_init_agent_py_is_runnable_syntax(self) -> None:
        """All generated agent.py files should at least be valid Python syntax."""
        for fw_num in range(1, 7):
            with tempfile.TemporaryDirectory() as tmpdir:
                outdir = Path(tmpdir) / "test-agent"
                result = runner.invoke(
                    app,
                    ["init", str(outdir)],
                    input=_make_init_input(framework=fw_num),
                )
                assert result.exit_code == 0, result.output

                agent_code = (outdir / "agent.py").read_text()
                # compile() checks syntax without executing
                compile(agent_code, "agent.py", "exec")

    def test_init_creates_parent_dirs(self) -> None:
        """Init should create parent directories if they don't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "deep" / "nested" / "test-agent"
            result = runner.invoke(
                app,
                ["init", str(outdir)],
                input=_make_init_input(),
            )
            assert result.exit_code == 0, result.output
            assert (outdir / "agent.yaml").exists()
