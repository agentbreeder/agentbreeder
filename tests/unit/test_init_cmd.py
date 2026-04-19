"""Tests for the agentbreeder init command."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# _validate_agent_name
# ---------------------------------------------------------------------------


class TestValidateAgentName:
    def test_valid_name_returns_none(self) -> None:
        from cli.commands.init_cmd import _validate_agent_name

        assert _validate_agent_name("my-agent") is None
        assert _validate_agent_name("ab") is None
        assert _validate_agent_name("agent123") is None

    def test_too_short_returns_error(self) -> None:
        from cli.commands.init_cmd import _validate_agent_name

        result = _validate_agent_name("a")
        assert result is not None
        assert "2 characters" in result

    def test_uppercase_returns_error(self) -> None:
        from cli.commands.init_cmd import _validate_agent_name

        result = _validate_agent_name("MyAgent")
        assert result is not None

    def test_leading_hyphen_returns_error(self) -> None:
        from cli.commands.init_cmd import _validate_agent_name

        result = _validate_agent_name("-bad")
        assert result is not None


# ---------------------------------------------------------------------------
# _validate_email
# ---------------------------------------------------------------------------


class TestValidateEmail:
    def test_valid_email_returns_none(self) -> None:
        from cli.commands.init_cmd import _validate_email

        assert _validate_email("alice@corp.com") is None

    def test_missing_at_returns_error(self) -> None:
        from cli.commands.init_cmd import _validate_email

        result = _validate_email("notanemail")
        assert result is not None

    def test_missing_dot_in_domain_returns_error(self) -> None:
        from cli.commands.init_cmd import _validate_email

        result = _validate_email("user@nodot")
        assert result is not None


# ---------------------------------------------------------------------------
# _gather_model_suggestions — Ollama and OpenRouter paths
# ---------------------------------------------------------------------------


class TestGatherModelSuggestions:
    def test_returns_empty_when_ollama_unavailable_and_no_openrouter_key(self) -> None:
        """When neither provider is available, empty list is returned."""
        from cli.commands.init_cmd import _gather_model_suggestions

        with patch("cli.commands.init_cmd.asyncio.run", return_value=False):
            result = _gather_model_suggestions()

        assert result == []

    def test_returns_ollama_models_when_available(self) -> None:
        """Detected Ollama models are returned as 'ollama/<name>'."""

        from cli.commands.init_cmd import _gather_model_suggestions

        mock_model_1 = MagicMock()
        mock_model_1.name = "llama3"
        mock_model_2 = MagicMock()
        mock_model_2.name = "mistral"

        run_results = iter([True, [mock_model_1, mock_model_2], None])

        with patch("cli.commands.init_cmd.asyncio.run", side_effect=run_results):
            result = _gather_model_suggestions()

        assert "ollama/llama3" in result
        assert "ollama/mistral" in result

    def test_ollama_exception_is_swallowed(self) -> None:
        """Exception during Ollama detection is caught and returns empty list."""
        from cli.commands.init_cmd import _gather_model_suggestions

        with patch("cli.commands.init_cmd.asyncio.run", side_effect=Exception("conn refused")):
            result = _gather_model_suggestions()

        assert result == []

    def test_reads_openrouter_models_from_registry_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OpenRouter models are loaded from the cached registry JSON."""
        import json as _json

        from cli.commands.init_cmd import _gather_model_suggestions

        registry_path = tmp_path / ".agentbreeder" / "registry"
        registry_path.mkdir(parents=True)
        models_file = registry_path / "models.json"
        models_file.write_text(
            _json.dumps(
                {
                    "or1": {"source": "openrouter", "name": "openai/gpt-4o"},
                    "or2": {"source": "openrouter", "name": "anthropic/claude-3"},
                }
            )
        )

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        # asyncio.run returns False (Ollama not available) so we skip straight to OpenRouter
        with patch("cli.commands.init_cmd.asyncio.run", return_value=False):
            result = _gather_model_suggestions()

        assert "openrouter/openai/gpt-4o" in result
        assert "openrouter/anthropic/claude-3" in result


# ---------------------------------------------------------------------------
# _prompt_model_suggestion
# ---------------------------------------------------------------------------


class TestPromptModelSuggestion:
    def test_returns_default_when_no_suggestions(self) -> None:
        """When there are no suggestions, the default model is returned immediately."""
        from cli.commands.init_cmd import _prompt_model_suggestion

        with patch("cli.commands.init_cmd._gather_model_suggestions", return_value=[]):
            result = _prompt_model_suggestion("claude-sonnet-4")

        assert result == "claude-sonnet-4"

    def test_user_keeps_default_by_picking_keep_number(self) -> None:
        """Picking the 'keep default' entry returns the original default."""
        from cli.commands.init_cmd import _prompt_model_suggestion

        suggestions = ["ollama/llama3", "ollama/mistral"]
        keep_num = str(len(suggestions) + 1)  # "3"

        with (
            patch("cli.commands.init_cmd._gather_model_suggestions", return_value=suggestions),
            patch("cli.commands.init_cmd.console.input", return_value=keep_num),
            patch("cli.commands.init_cmd.console.print"),
        ):
            result = _prompt_model_suggestion("claude-sonnet-4")

        assert result == "claude-sonnet-4"

    def test_user_picks_suggestion_by_number(self) -> None:
        """Picking suggestion index 1 returns the first suggestion."""
        from cli.commands.init_cmd import _prompt_model_suggestion

        suggestions = ["ollama/llama3", "ollama/mistral"]

        with (
            patch("cli.commands.init_cmd._gather_model_suggestions", return_value=suggestions),
            patch("cli.commands.init_cmd.console.input", return_value="1"),
            patch("cli.commands.init_cmd.console.print"),
        ):
            result = _prompt_model_suggestion("claude-sonnet-4")

        assert result == "ollama/llama3"

    def test_invalid_then_valid_input_reprompts(self) -> None:
        """Non-integer input is ignored and the loop continues until a valid choice."""
        from cli.commands.init_cmd import _prompt_model_suggestion

        suggestions = ["ollama/llama3"]
        inputs = iter(["abc", "0", "1"])  # bad, out-of-range, valid

        with (
            patch("cli.commands.init_cmd._gather_model_suggestions", return_value=suggestions),
            patch("cli.commands.init_cmd.console.input", side_effect=inputs),
            patch("cli.commands.init_cmd.console.print"),
        ):
            result = _prompt_model_suggestion("claude-sonnet-4")

        assert result == "ollama/llama3"


# ---------------------------------------------------------------------------
# init — json output with validation errors
# ---------------------------------------------------------------------------


class TestInitJsonOutputValidationErrors:
    def test_json_output_includes_validation_errors_when_invalid(self) -> None:
        """When agent.yaml fails validation, JSON output contains validation_errors key."""
        import json as _json

        from engine.config_parser import ConfigValidationError, ValidationResult

        fake_error = ConfigValidationError(path="model.primary", message="required field missing")
        fake_result = ValidationResult(valid=False, errors=[fake_error])

        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "broken-agent"
            with patch("cli.commands.init_cmd.validate_config", return_value=fake_result):
                result = runner.invoke(
                    app,
                    ["init", str(outdir), "--json"],
                    input=_make_init_input(),
                )

        assert result.exit_code == 0
        # Find the JSON blob in output (last non-empty lines)
        json_str = ""
        brace_depth = 0
        collecting = False
        for ch in result.output:
            if ch == "{":
                brace_depth += 1
                collecting = True
            if collecting:
                json_str += ch
            if ch == "}" and collecting:
                brace_depth -= 1
                if brace_depth == 0:
                    break
        data = _json.loads(json_str)
        assert data["valid"] is False
        assert "validation_errors" in data


# ---------------------------------------------------------------------------
# init — relative-path cd_cmd variations
# ---------------------------------------------------------------------------


class TestInitNextStepsRelPath:
    def test_next_steps_shows_relative_cd_when_simple(self) -> None:
        """When output dir is a direct child of cwd, next steps show the relative path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            # Run from the tmpdir so relative path is simple
            import os

            old_cwd = os.getcwd()
            try:
                os.chdir(tmpdir)
                result = runner.invoke(
                    app,
                    ["init", str(outdir)],
                    input=_make_init_input(),
                )
            finally:
                os.chdir(old_cwd)

        assert result.exit_code == 0, result.output
        assert "Next steps" in result.output

    def test_next_steps_shows_absolute_path_when_relative_goes_up(self) -> None:
        """When the relative path would start with '..', the absolute path is shown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            # Create the project in a sibling directory
            sibling = Path(tmpdir) / "sibling" / "my-proj"
            work_dir = Path(tmpdir) / "work"
            work_dir.mkdir()
            old_cwd = os.getcwd()
            try:
                os.chdir(str(work_dir))
                result = runner.invoke(
                    app,
                    ["init", str(sibling)],
                    input=_make_init_input(),
                )
            finally:
                os.chdir(old_cwd)

        assert result.exit_code == 0, result.output
        assert "agentbreeder deploy" in result.output

    def test_init_with_no_output_dir_uses_agent_name(self) -> None:
        """When no output dir is given, the project is created in cwd/<name>."""
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                result = runner.invoke(
                    app,
                    ["init"],  # No output_dir argument
                    input=_make_init_input(name="hello-agent"),
                )
            finally:
                os.chdir(old_cwd)

            # Check inside the with block while tmpdir still exists
            assert result.exit_code == 0, result.output
            expected = Path(tmpdir) / "hello-agent" / "agent.yaml"
            assert expected.exists()

    def test_init_shows_validation_warning_in_console_mode(self) -> None:
        """When validation fails in console mode, a warning is shown (not an error exit)."""
        from engine.config_parser import ConfigValidationError, ValidationResult

        fake_result = ValidationResult(
            valid=False,
            errors=[ConfigValidationError(path="name", message="too short")],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            outdir = Path(tmpdir) / "test-agent"
            with patch("cli.commands.init_cmd.validate_config", return_value=fake_result):
                result = runner.invoke(
                    app,
                    ["init", str(outdir)],
                    input=_make_init_input(),
                )

        assert result.exit_code == 0, result.output
        assert "validation warnings" in result.output or "⚠" in result.output
