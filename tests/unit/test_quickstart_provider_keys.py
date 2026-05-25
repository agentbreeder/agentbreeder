"""Tests for the restructured _collect_provider_keys flow (Phase 6, Task 2).

Flow under test:
  1. Always offer OpenRouter first ("one key → 100+ models")
  2. Single gate: "Add direct provider keys (OpenAI / Anthropic / Google)? [y/N]"
     - If y → prompt for all three direct providers
     - If n / Enter → skip the three
  3. --yes skips all prompts
  4. Already-set keys still show masked value; Enter keeps them
"""

from __future__ import annotations

from unittest.mock import patch

import httpx

from cli.commands.quickstart import _collect_provider_keys


def _no_ollama():
    """Context manager: make the Ollama liveness check fail."""
    return patch("httpx.get", side_effect=httpx.ConnectError("nope"))


def _with_ollama():
    """Context manager: make the Ollama liveness check succeed."""
    mock = patch("httpx.get")

    class _Ctx:
        def __enter__(self):
            self._p = mock.__enter__()
            self._p.return_value.status_code = 200
            return self._p

        def __exit__(self, *a):
            return mock.__exit__(*a)

    return _Ctx()


class TestOpenRouterLeads:
    """OpenRouter is always prompted first, before the direct-provider gate."""

    def test_openrouter_is_prompted_before_direct_providers(self) -> None:
        """Inputs: OpenRouter key, then decline direct providers."""
        inputs = iter(["sk-or-test", "n"])
        with _no_ollama(), patch("cli.commands.quickstart.console.input", side_effect=inputs):
            collected, _ = _collect_provider_keys({})
        assert "OPENROUTER_API_KEY" in collected
        assert collected["OPENROUTER_API_KEY"] == "sk-or-test"
        # No direct-provider keys collected
        assert "OPENAI_API_KEY" not in collected
        assert "ANTHROPIC_API_KEY" not in collected
        assert "GOOGLE_API_KEY" not in collected

    def test_skip_openrouter_then_decline_direct(self) -> None:
        """Skip OpenRouter (Enter) and decline direct providers."""
        inputs = iter(["", "n"])
        with _no_ollama(), patch("cli.commands.quickstart.console.input", side_effect=inputs):
            collected, _ = _collect_provider_keys({})
        assert collected == {}

    def test_skip_openrouter_then_enter_on_gate_also_declines(self) -> None:
        """Enter at the gate prompt declines direct providers (default N)."""
        inputs = iter(["", ""])
        with _no_ollama(), patch("cli.commands.quickstart.console.input", side_effect=inputs):
            collected, _ = _collect_provider_keys({})
        assert collected == {}


class TestDirectProviderGate:
    """The 'Add direct provider keys? [y/N]' gate controls OpenAI/Anthropic/Google."""

    def test_accepting_gate_prompts_all_three_direct_providers(self) -> None:
        """y at gate → three more prompts (OpenAI, Anthropic, Google)."""
        inputs = iter(
            [
                "",  # OpenRouter: skip
                "y",  # gate: yes
                "sk-openai",
                "sk-ant-test",
                "AIza-test",
            ]
        )
        with _no_ollama(), patch("cli.commands.quickstart.console.input", side_effect=inputs):
            collected, _ = _collect_provider_keys({})
        assert collected["OPENAI_API_KEY"] == "sk-openai"
        assert collected["ANTHROPIC_API_KEY"] == "sk-ant-test"
        assert collected["GOOGLE_API_KEY"] == "AIza-test"

    def test_declining_gate_skips_all_direct_providers(self) -> None:
        inputs = iter(["", "n"])
        with _no_ollama(), patch("cli.commands.quickstart.console.input", side_effect=inputs):
            collected, _ = _collect_provider_keys({})
        assert "OPENAI_API_KEY" not in collected
        assert "ANTHROPIC_API_KEY" not in collected
        assert "GOOGLE_API_KEY" not in collected

    def test_enter_at_gate_is_equivalent_to_n(self) -> None:
        """Empty answer at the gate should default to N (skip direct providers)."""
        inputs = iter(["", ""])  # OpenRouter skip, gate Enter = N
        with _no_ollama(), patch("cli.commands.quickstart.console.input", side_effect=inputs):
            collected, _ = _collect_provider_keys({})
        assert "OPENAI_API_KEY" not in collected

    def test_can_skip_some_direct_providers(self) -> None:
        """Accepting the gate but entering blank for some keys skips those."""
        inputs = iter(
            [
                "",  # OpenRouter: skip
                "y",  # gate: yes
                "",  # OpenAI: skip
                "sk-ant-test",
                "",  # Google: skip
            ]
        )
        with _no_ollama(), patch("cli.commands.quickstart.console.input", side_effect=inputs):
            collected, _ = _collect_provider_keys({})
        assert "OPENAI_API_KEY" not in collected
        assert collected["ANTHROPIC_API_KEY"] == "sk-ant-test"
        assert "GOOGLE_API_KEY" not in collected


class TestAssumeYesSkipsAll:
    def test_assume_yes_skips_all_prompts(self) -> None:
        with _no_ollama(), patch("cli.commands.quickstart.console.input") as mock_input:
            collected, has_ollama = _collect_provider_keys({}, assume_yes=True)
        assert collected == {}
        assert has_ollama is False
        mock_input.assert_not_called()

    def test_assume_yes_with_ollama_running(self) -> None:
        with _with_ollama(), patch("cli.commands.quickstart.console.input") as mock_input:
            collected, has_ollama = _collect_provider_keys({}, assume_yes=True)
        assert collected == {}
        assert has_ollama is True
        mock_input.assert_not_called()


class TestExistingKeysKept:
    def test_existing_openrouter_key_can_be_kept_with_enter(self) -> None:
        """If OPENROUTER_API_KEY already set, Enter keeps it (no new value collected)."""
        existing = {"OPENROUTER_API_KEY": "sk-or-existing-1234567890"}
        inputs = iter(["", "n"])  # keep OpenRouter, decline direct
        with _no_ollama(), patch("cli.commands.quickstart.console.input", side_effect=inputs):
            collected, _ = _collect_provider_keys(existing)
        # No new value for OpenRouter; existing untouched
        assert "OPENROUTER_API_KEY" not in collected

    def test_existing_key_replaced_with_new_value(self) -> None:
        existing = {"OPENROUTER_API_KEY": "sk-or-old"}
        inputs = iter(["sk-or-new", "n"])
        with _no_ollama(), patch("cli.commands.quickstart.console.input", side_effect=inputs):
            collected, _ = _collect_provider_keys(existing)
        assert collected["OPENROUTER_API_KEY"] == "sk-or-new"


class TestOllamaStatusReturned:
    def test_has_ollama_true_when_running(self) -> None:
        with _with_ollama():
            _, has_ollama = _collect_provider_keys({}, assume_yes=True)
        assert has_ollama is True

    def test_has_ollama_false_when_not_running(self) -> None:
        with _no_ollama():
            _, has_ollama = _collect_provider_keys({}, assume_yes=True)
        assert has_ollama is False
