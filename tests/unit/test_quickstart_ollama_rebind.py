"""Tests for the opt-in Ollama rebind behavior (Phase 6, Task 3).

The rebind prompt must:
  - Default to SKIP ([y/N])
  - Clearly explain WHY (host.docker.internal routing means 127.0.0.1 is invisible
    to Docker containers)
  - On skip → print a clear consequence ("local Ollama won't be reachable from
    the Docker stack — agents fall back to cloud")
  - On rebind failure → print the consequence + manual commands; NOT silent
  - On rebind success → no warning
"""

from __future__ import annotations

from unittest.mock import patch

from cli.commands.quickstart import _ensure_ollama


def _mock_ollama_running_with_model(models: list[str] | None = None):
    """Ollama is installed, running, has models — skip install/start steps."""
    if models is None:
        models = ["gemma3:latest"]
    return (
        patch("cli.commands.quickstart._ollama_installed", return_value=True),
        patch("cli.commands.quickstart._ollama_running", return_value=True),
        patch("cli.commands.quickstart._ollama_models", return_value=models),
    )


class TestRebindDefaultIsSkip:
    """Pressing Enter at the rebind prompt must skip (not rebind)."""

    def test_enter_skips_rebind_and_prints_consequence(self) -> None:
        """Enter at the rebind prompt → no rebind attempted, consequence printed."""
        installed, running, models = _mock_ollama_running_with_model()
        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=True),
            patch("cli.commands.quickstart._rebind_ollama_all_interfaces") as mock_rebind,
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart.console.input", return_value="") as mock_input,
        ):
            result = _ensure_ollama(skip=False, default_model="gemma3")

        # Rebind must NOT have been attempted
        mock_rebind.assert_not_called()
        # Function still returns True (Ollama is running with models)
        assert result is True
        # Prompt must contain [y/N] (capital N = default skip)
        prompt_call = mock_input.call_args_list[0]
        prompt_text = prompt_call[0][0]
        assert "[y/N]" in prompt_text

    def test_explicit_n_skips_rebind(self) -> None:
        installed, running, models = _mock_ollama_running_with_model()
        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=True),
            patch("cli.commands.quickstart._rebind_ollama_all_interfaces") as mock_rebind,
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart.console.input", return_value="n"),
        ):
            _ensure_ollama(skip=False, default_model="gemma3")

        mock_rebind.assert_not_called()


class TestRebindPromptExplainsWhy:
    """The rebind prompt must explain host.docker.internal routing."""

    def test_prompt_mentions_host_docker_internal(self) -> None:
        installed, running, models = _mock_ollama_running_with_model()
        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=True),
            patch("cli.commands.quickstart._rebind_ollama_all_interfaces", return_value=False),
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart.console.input", return_value="n") as mock_input,
        ):
            _ensure_ollama(skip=False, default_model="gemma3")

        prompt_text = mock_input.call_args_list[0][0][0]
        assert "host.docker.internal" in prompt_text


class TestSkipConsequenceMessage:
    """On skip (Enter or n), a clear consequence message must be printed."""

    def test_skip_prints_consequence_about_fallback(self) -> None:
        installed, running, models = _mock_ollama_running_with_model()
        printed: list[str] = []

        def capture_info(msg: str) -> None:
            printed.append(msg)

        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=True),
            patch("cli.commands.quickstart._rebind_ollama_all_interfaces") as mock_rebind,
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart._info", side_effect=capture_info),
            patch("cli.commands.quickstart.console.input", return_value=""),
        ):
            _ensure_ollama(skip=False, default_model="gemma3")

        mock_rebind.assert_not_called()
        # At least one _info / console message must mention the consequence
        combined = " ".join(printed)
        assert any(
            phrase in combined.lower()
            for phrase in [
                "fall back",
                "fallback",
                "not reachable",
                "unreachable",
                "cloud",
            ]
        ), f"No consequence message found in: {combined!r}"

    def test_consequence_message_not_empty(self) -> None:
        """The consequence message must be non-trivial (not just a stub)."""
        installed, running, models = _mock_ollama_running_with_model()
        printed: list[str] = []

        def capture_info(msg: str) -> None:
            printed.append(msg)

        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=True),
            patch("cli.commands.quickstart._rebind_ollama_all_interfaces"),
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart._info", side_effect=capture_info),
            patch("cli.commands.quickstart.console.input", return_value=""),
        ):
            _ensure_ollama(skip=False, default_model="gemma3")

        # There must be at least one non-trivial info message
        assert any(len(m) > 20 for m in printed)


class TestRebindFailureIsHonest:
    """When the user accepts rebind but it fails, the consequence must be printed."""

    def test_rebind_failure_prints_consequence_not_silent(self) -> None:
        installed, running, models = _mock_ollama_running_with_model()
        warn_calls: list[str] = []

        def capture_warn(msg: str) -> None:
            warn_calls.append(msg)

        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=True),
            patch("cli.commands.quickstart._rebind_ollama_all_interfaces", return_value=False),
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart._warn", side_effect=capture_warn),
            patch("cli.commands.quickstart.console.input", return_value="y"),
        ):
            result = _ensure_ollama(skip=False, default_model="gemma3")

        # Ollama is still running with models → True
        assert result is True
        # A warning must be printed
        assert len(warn_calls) >= 1
        combined = " ".join(warn_calls)
        # Must mention manual steps or consequence
        assert any(
            phrase in combined.lower()
            for phrase in [
                "manual",
                "launchctl",
                "rebind",
                "not reachable",
                "unreachable",
                "fall back",
                "fallback",
            ]
        ), f"No failure consequence found in warn calls: {combined!r}"

    def test_rebind_failure_is_not_silent_no_fall_through(self) -> None:
        """After a failed rebind the consequence message must appear (not just a no-op)."""
        installed, running, models = _mock_ollama_running_with_model()
        info_calls: list[str] = []
        warn_calls: list[str] = []

        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=True),
            patch("cli.commands.quickstart._rebind_ollama_all_interfaces", return_value=False),
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart._info", side_effect=lambda m: info_calls.append(m)),
            patch("cli.commands.quickstart._warn", side_effect=lambda m: warn_calls.append(m)),
            patch("cli.commands.quickstart.console.input", return_value="y"),
        ):
            _ensure_ollama(skip=False, default_model="gemma3")

        all_messages = info_calls + warn_calls
        assert len(all_messages) >= 1, "Expected at least one message after rebind failure"


class TestRebindSuccess:
    """When rebind succeeds, no warning or consequence message is emitted."""

    def test_rebind_success_no_warning(self) -> None:
        installed, running, models = _mock_ollama_running_with_model()
        warn_calls: list[str] = []

        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=True),
            patch("cli.commands.quickstart._rebind_ollama_all_interfaces", return_value=True),
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart._warn", side_effect=lambda m: warn_calls.append(m)),
            patch("cli.commands.quickstart.console.input", return_value="y"),
        ):
            result = _ensure_ollama(skip=False, default_model="gemma3")

        assert result is True
        # No warning about reachability
        reachability_warnings = [
            w
            for w in warn_calls
            if any(p in w.lower() for p in ["not reachable", "unreachable", "fall back"])
        ]
        assert reachability_warnings == []


class TestBindNotLocalhostOnly:
    """When Ollama is NOT localhost-only, no rebind prompt is shown at all."""

    def test_no_rebind_prompt_when_bind_is_0_0_0_0(self) -> None:
        installed, running, models = _mock_ollama_running_with_model()
        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=False),
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart.console.input") as mock_input,
        ):
            result = _ensure_ollama(skip=False, default_model="gemma3")

        assert result is True
        mock_input.assert_not_called()

    def test_no_rebind_prompt_when_bind_check_returns_none(self) -> None:
        installed, running, models = _mock_ollama_running_with_model()
        with (
            installed,
            running,
            models,
            patch("cli.commands.quickstart._ollama_bind_is_localhost_only", return_value=None),
            patch("cli.commands.quickstart._is_assume_yes", return_value=False),
            patch("cli.commands.quickstart.console.input") as mock_input,
        ):
            result = _ensure_ollama(skip=False, default_model="gemma3")

        assert result is True
        mock_input.assert_not_called()
