"""Tests for the quickstart model-source prompt (issue #466)."""

from __future__ import annotations

from unittest.mock import patch

from cli.commands.quickstart import _ask_model_source


class TestNoOllamaFlagShortCircuit:
    def test_no_ollama_flag_skips_prompt_and_returns_cloud_path(self) -> None:
        with patch("cli.commands.quickstart.console.input") as mock_input:
            result = _ask_model_source(no_ollama_flag=True)
        assert result == (True, False)
        mock_input.assert_not_called()


class TestNonTtyShortCircuit:
    def test_non_tty_keeps_legacy_default(self) -> None:
        with patch("sys.stdin.isatty", return_value=False):
            with patch("cli.commands.quickstart.console.input") as mock_input:
                result = _ask_model_source(no_ollama_flag=False)
        assert result == (False, False)
        mock_input.assert_not_called()

    def test_non_tty_respects_no_ollama_flag(self) -> None:
        with patch("sys.stdin.isatty", return_value=False):
            result = _ask_model_source(no_ollama_flag=True)
        assert result == (True, False)


class TestInteractivePrompt:
    def _mock_tty_and_inputs(self, *answers: str):
        return (
            patch("sys.stdin.isatty", return_value=True),
            patch(
                "cli.commands.quickstart.console.input",
                side_effect=list(answers),
            ),
        )

    def test_choice_1_local_skips_cloud_keys(self) -> None:
        tty, inputs = self._mock_tty_and_inputs("1")
        with tty, inputs:
            assert _ask_model_source(no_ollama_flag=False) == (False, True)

    def test_choice_2_cloud_skips_ollama(self) -> None:
        tty, inputs = self._mock_tty_and_inputs("2")
        with tty, inputs:
            assert _ask_model_source(no_ollama_flag=False) == (True, False)

    def test_choice_3_both_is_default(self) -> None:
        tty, inputs = self._mock_tty_and_inputs("3")
        with tty, inputs:
            assert _ask_model_source(no_ollama_flag=False) == (False, False)

    def test_empty_answer_without_ollama_falls_back_to_both(self) -> None:
        """Pressing Enter when Ollama is NOT detected picks option 3 (Both)."""
        tty, inputs = self._mock_tty_and_inputs("")
        with (
            tty,
            inputs,
            patch("cli.commands.quickstart._ollama_running", return_value=False),
            patch("cli.commands.quickstart._ollama_models", return_value=[]),
        ):
            assert _ask_model_source(no_ollama_flag=False) == (False, False)

    def test_invalid_answer_retries_then_accepts(self) -> None:
        tty, inputs = self._mock_tty_and_inputs("hello", "42", "2")
        with tty, inputs:
            assert _ask_model_source(no_ollama_flag=False) == (True, False)

    def test_whitespace_is_trimmed_before_matching(self) -> None:
        tty, inputs = self._mock_tty_and_inputs("  1  ")
        with tty, inputs:
            assert _ask_model_source(no_ollama_flag=False) == (False, True)


class TestOllamaDetectedDefault:
    """When Ollama is running with models, Enter should select Local (no key)."""

    def _mock_tty_ollama_and_inputs(self, models: list[str], *answers: str):
        return (
            patch("sys.stdin.isatty", return_value=True),
            patch("cli.commands.quickstart._ollama_running", return_value=True),
            patch("cli.commands.quickstart._ollama_models", return_value=models),
            patch(
                "cli.commands.quickstart.console.input",
                side_effect=list(answers),
            ),
        )

    def test_enter_selects_local_when_ollama_detected(self) -> None:
        """Empty input defaults to Local (skip cloud keys) when Ollama has models."""
        tty, running, models, inputs = self._mock_tty_ollama_and_inputs(["gemma3:latest"], "")
        with tty, running, models, inputs:
            result = _ask_model_source(no_ollama_flag=False)
        assert result == (False, True)

    def test_enter_selects_local_with_multiple_models(self) -> None:
        tty, running, models, inputs = self._mock_tty_ollama_and_inputs(
            ["gemma3:latest", "llama3.2:latest"], ""
        )
        with tty, running, models, inputs:
            result = _ask_model_source(no_ollama_flag=False)
        assert result == (False, True)

    def test_can_still_choose_cloud_when_ollama_detected(self) -> None:
        """User can type '2' to choose Cloud even when Ollama is detected."""
        tty, running, models, inputs = self._mock_tty_ollama_and_inputs(["gemma3:latest"], "2")
        with tty, running, models, inputs:
            result = _ask_model_source(no_ollama_flag=False)
        assert result == (True, False)

    def test_can_still_choose_both_when_ollama_detected(self) -> None:
        """User can type '3' to choose Both even when Ollama is detected."""
        tty, running, models, inputs = self._mock_tty_ollama_and_inputs(["gemma3:latest"], "3")
        with tty, running, models, inputs:
            result = _ask_model_source(no_ollama_flag=False)
        assert result == (False, False)

    def test_no_ollama_models_falls_back_to_both_default(self) -> None:
        """Ollama running but no models pulled → default remains Both (3)."""
        tty, running, models, inputs = self._mock_tty_ollama_and_inputs([], "")
        with tty, running, models, inputs:
            result = _ask_model_source(no_ollama_flag=False)
        assert result == (False, False)

    def test_no_ollama_flag_still_short_circuits_even_when_ollama_detected(self) -> None:
        """--no-ollama flag overrides everything, even detected Ollama."""
        with (
            patch("cli.commands.quickstart._ollama_running", return_value=True),
            patch("cli.commands.quickstart._ollama_models", return_value=["gemma3"]),
        ):
            result = _ask_model_source(no_ollama_flag=True)
        assert result == (True, False)
