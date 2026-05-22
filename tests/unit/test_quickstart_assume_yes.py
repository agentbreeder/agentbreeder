"""Tests for the quickstart --yes (assume_yes) non-interactive plumbing (issue #468)."""

from __future__ import annotations

from unittest.mock import patch

from cli.commands import quickstart
from cli.commands.quickstart import (
    _ask_model_source,
    _collect_provider_keys,
    _is_assume_yes,
    _set_assume_yes,
)


class TestAssumeYesFlagToggle:
    def setup_method(self) -> None:
        _set_assume_yes(False)

    def teardown_method(self) -> None:
        _set_assume_yes(False)

    def test_default_state_is_false(self) -> None:
        assert _is_assume_yes() is False

    def test_set_assume_yes_flips_flag(self) -> None:
        _set_assume_yes(True)
        assert _is_assume_yes() is True
        _set_assume_yes(False)
        assert _is_assume_yes() is False


class TestAskModelSourceWithAssumeYes:
    def test_assume_yes_without_no_ollama_returns_legacy_default(self) -> None:
        """--yes alone falls into the non-TTY path = (False, False) = Both."""
        with patch("cli.commands.quickstart.console.input") as mock_input:
            result = _ask_model_source(no_ollama_flag=False, assume_yes=True)
        assert result == (False, False)
        mock_input.assert_not_called()

    def test_assume_yes_with_no_ollama_short_circuits_to_cloud(self) -> None:
        with patch("cli.commands.quickstart.console.input") as mock_input:
            result = _ask_model_source(no_ollama_flag=True, assume_yes=True)
        assert result == (True, False)
        mock_input.assert_not_called()

    def test_assume_yes_overrides_tty_check(self) -> None:
        """Even on a TTY, --yes must skip the interactive prompt."""
        with patch("sys.stdin.isatty", return_value=True):
            with patch("cli.commands.quickstart.console.input") as mock_input:
                result = _ask_model_source(no_ollama_flag=False, assume_yes=True)
        assert result == (False, False)
        mock_input.assert_not_called()


class TestCollectProviderKeysWithAssumeYes:
    def test_assume_yes_skips_all_prompts(self) -> None:
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            with patch("cli.commands.quickstart.console.input") as mock_input:
                collected, has_ollama = _collect_provider_keys({}, assume_yes=True)
        assert collected == {}
        assert has_ollama is True
        mock_input.assert_not_called()

    def test_assume_yes_still_reports_ollama_status(self) -> None:
        """The ollama detection should still run — only the per-key prompts skip."""
        with patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            collected, has_ollama = _collect_provider_keys({}, assume_yes=True)
        assert has_ollama is True
        assert collected == {}

    def test_assume_yes_handles_ollama_unreachable(self) -> None:
        import httpx

        with patch("httpx.get", side_effect=httpx.ConnectError("nope")):
            collected, has_ollama = _collect_provider_keys({}, assume_yes=True)
        assert collected == {}
        assert has_ollama is False


class TestQuickstartFunctionWiresAssumeYes:
    """Smoke test: the typer command exposes --yes / -y and threads it through."""

    def test_help_mentions_yes_flag(self) -> None:
        # Inspect the typer command's params instead of running the full command.
        # Each typer.Option becomes a default value with a `.param_decls` tuple.
        func = quickstart.quickstart
        # Defaults are typer.OptionInfo objects. We look for the --yes flag.
        import inspect

        sig = inspect.signature(func)
        assume_yes_param = sig.parameters.get("assume_yes")
        assert assume_yes_param is not None, "quickstart() must accept assume_yes"
        opt = assume_yes_param.default
        # typer.OptionInfo stores declarations under `param_decls`
        decls = getattr(opt, "param_decls", ()) or ()
        flags = {d for d in decls if isinstance(d, str)}
        assert "--yes" in flags
        assert "-y" in flags
