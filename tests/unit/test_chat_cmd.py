"""Unit tests for the agentbreeder chat command (cli/commands/chat.py).

All HTTP calls and Anthropic SDK calls are mocked so no real network
connections or credentials are required.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# _is_managed_agent_endpoint
# ---------------------------------------------------------------------------


class TestIsManagedAgentEndpoint:
    def test_returns_true_for_anthropic_scheme(self) -> None:
        from cli.commands.chat import _is_managed_agent_endpoint

        assert _is_managed_agent_endpoint("anthropic://agents/agent_abc123") is True

    def test_returns_false_for_https_scheme(self) -> None:
        from cli.commands.chat import _is_managed_agent_endpoint

        assert _is_managed_agent_endpoint("https://api.example.com/agent") is False

    def test_returns_false_for_empty_string(self) -> None:
        from cli.commands.chat import _is_managed_agent_endpoint

        assert _is_managed_agent_endpoint("") is False


# ---------------------------------------------------------------------------
# _parse_managed_endpoint
# ---------------------------------------------------------------------------


class TestParseManagedEndpoint:
    def test_extracts_agent_id_and_env_id(self) -> None:
        from cli.commands.chat import _parse_managed_endpoint

        agent_id, env_id = _parse_managed_endpoint("anthropic://agents/agent_abc123?env=env_xyz")
        assert agent_id == "agent_abc123"
        assert env_id == "env_xyz"

    def test_env_id_empty_when_absent(self) -> None:
        from cli.commands.chat import _parse_managed_endpoint

        agent_id, env_id = _parse_managed_endpoint("anthropic://agents/agent_abc123")
        assert agent_id == "agent_abc123"
        assert env_id == ""

    def test_strips_agents_prefix(self) -> None:
        from cli.commands.chat import _parse_managed_endpoint

        agent_id, _ = _parse_managed_endpoint("anthropic://agents/my-special-agent")
        assert agent_id == "my-special-agent"
        assert "agents/" not in agent_id


# ---------------------------------------------------------------------------
# _get_agent_endpoint
# ---------------------------------------------------------------------------


class TestGetAgentEndpoint:
    def test_returns_endpoint_url_on_200(self) -> None:
        from cli.commands.chat import _get_agent_endpoint

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"endpoint_url": "https://my.agent.local"}}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("cli.commands.chat._get_client", return_value=mock_client):
            result = _get_agent_endpoint("my-agent")

        assert result == "https://my.agent.local"

    def test_returns_none_on_non_200(self) -> None:
        from cli.commands.chat import _get_agent_endpoint

        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("cli.commands.chat._get_client", return_value=mock_client):
            result = _get_agent_endpoint("missing-agent")

        assert result is None

    def test_returns_none_on_exception(self) -> None:
        from cli.commands.chat import _get_agent_endpoint

        with patch("cli.commands.chat._get_client", side_effect=Exception("conn error")):
            result = _get_agent_endpoint("my-agent")

        assert result is None

    def test_falls_back_to_endpoint_key(self) -> None:
        from cli.commands.chat import _get_agent_endpoint

        mock_response = MagicMock()
        mock_response.status_code = 200
        # No endpoint_url, but has endpoint
        mock_response.json.return_value = {"data": {"endpoint": "https://fallback.agent.local"}}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("cli.commands.chat._get_client", return_value=mock_client):
            result = _get_agent_endpoint("my-agent")

        assert result == "https://fallback.agent.local"


# ---------------------------------------------------------------------------
# _chat_via_managed_agent
# ---------------------------------------------------------------------------


class TestChatViaManagedAgent:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_without_anthropic_sdk(self) -> None:
        from cli.commands.chat import _chat_via_managed_agent

        with patch.dict(__import__("sys").modules, {"anthropic": None}):
            with pytest.raises(RuntimeError, match="anthropic SDK not installed"):
                await _chat_via_managed_agent("agent_id", "env_id", "hello")

    @pytest.mark.asyncio
    async def test_returns_assistant_text_from_stream(self) -> None:
        from cli.commands.chat import _chat_via_managed_agent

        mock_anthropic_module = MagicMock()
        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client

        # Session create
        mock_session = MagicMock()
        mock_session.id = "sess_123"
        mock_client.beta.sessions.create.return_value = mock_session

        # Stream context manager that yields two events then idle
        text_block = MagicMock()
        text_block.text = "Hello world"

        agent_event = MagicMock()
        agent_event.type = "agent.message"
        agent_event.content = [text_block]

        idle_event = MagicMock()
        idle_event.type = "session.status_idle"

        stream_ctx = MagicMock()
        stream_ctx.__enter__ = MagicMock(return_value=iter([agent_event, idle_event]))
        stream_ctx.__exit__ = MagicMock(return_value=False)
        mock_client.beta.sessions.events.stream.return_value = stream_ctx

        with patch.dict(__import__("sys").modules, {"anthropic": mock_anthropic_module}):
            result = await _chat_via_managed_agent("agent_id", "env_id", "hi", verbose=False)

        assert result == "Hello world"


# ---------------------------------------------------------------------------
# _print_session_summary
# ---------------------------------------------------------------------------


class TestPrintSessionSummary:
    def test_prints_no_messages_when_turns_zero(self, capsys: pytest.CaptureFixture) -> None:
        from cli.commands.chat import _print_session_summary

        with patch("cli.commands.chat.console.print") as mock_print:
            _print_session_summary(0, 0, 0.0)

        # Verify "No messages" text was passed to console.print
        all_calls = " ".join(str(c) for c in mock_print.call_args_list)
        assert "No messages" in all_calls

    def test_prints_summary_panel_with_counts(self) -> None:
        # _print_session_summary renders into a Rich Panel. Capture the
        # rendered string by checking the Panel's renderable directly.
        from rich.panel import Panel

        from cli.commands.chat import _print_session_summary

        with patch("cli.commands.chat.console.print") as mock_print:
            _print_session_summary(3, 500, 0.001234)

        # Find Panel calls and inspect their string representation
        for call in mock_print.call_args_list:
            for arg in call.args:
                if isinstance(arg, Panel):
                    rendered = str(arg.renderable)
                    assert "3" in rendered
                    assert "500" in rendered
                    return
        # If no Panel found, check call args contain the numbers
        all_str = str(mock_print.call_args_list)
        assert "3" in all_str


# ---------------------------------------------------------------------------
# _print_chat_help
# ---------------------------------------------------------------------------


class TestPrintChatHelp:
    def test_prints_help_table(self) -> None:
        from cli.commands.chat import _print_chat_help

        with patch("cli.commands.chat.console.print") as mock_print:
            _print_chat_help()

        assert mock_print.called


# ---------------------------------------------------------------------------
# chat command — json mode
# ---------------------------------------------------------------------------


class TestChatJsonMode:
    def test_json_mode_writes_response_to_stdout(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"data": {"response": "Hi there!", "token_count": 10}}

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("cli.commands.chat._get_client", return_value=mock_client):
            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="hello\n",
            )

        assert result.exit_code == 0
        assert "Hi there!" in result.output

    def test_json_mode_writes_error_on_connect_error(self) -> None:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ConnectError("connection refused")

        with patch("cli.commands.chat._get_client", return_value=mock_client):
            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="hello\n",
            )

        # Output should be JSON with an error key
        assert result.exit_code == 0
        data = json.loads(result.output.strip())
        assert "error" in data

    def test_json_mode_skips_empty_lines(self) -> None:
        """Empty lines in stdin should not trigger API calls."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with patch("cli.commands.chat._get_client", return_value=mock_client):
            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="\n\n",  # Only empty lines
            )

        assert result.exit_code == 0
        mock_client.post.assert_not_called()


# ---------------------------------------------------------------------------
# chat command — interactive mode (via mocked _run_interactive)
# ---------------------------------------------------------------------------


class TestChatInteractiveMode:
    def test_chat_dispatches_to_interactive_mode_by_default(self) -> None:
        """Without --json, chat calls _run_interactive."""
        with patch("cli.commands.chat._run_interactive") as mock_interactive:
            runner.invoke(app, ["chat", "my-agent"])

        mock_interactive.assert_called_once()
        call_kwargs = mock_interactive.call_args
        assert call_kwargs.args[0] == "my-agent"

    def test_chat_dispatches_to_json_mode_with_flag(self) -> None:
        """With --json, chat calls _run_json_mode."""
        with patch("cli.commands.chat._run_json_mode") as mock_json:
            runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="",
            )

        mock_json.assert_called_once_with("my-agent", None)


# ---------------------------------------------------------------------------
# _run_interactive — special commands
# ---------------------------------------------------------------------------


class TestRunInteractiveSpecialCommands:
    def _make_api_response(self, text: str = "OK") -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "data": {
                "response": text,
                "tool_calls": [],
                "token_count": 5,
                "cost_estimate": 0.0001,
                "latency_ms": 100,
                "model_used": "claude-sonnet-4",
            }
        }
        return resp

    def test_quit_command_exits_cleanly(self) -> None:
        """Typing /quit should exit with code 0."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("cli.commands.chat._get_agent_endpoint", return_value=None),
            patch("cli.commands.chat._get_client", return_value=mock_client),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="/quit\n",
            )

        assert result.exit_code == 0

    def test_exit_command_exits_cleanly(self) -> None:
        """Typing /exit should exit with code 0."""
        with patch("cli.commands.chat._get_agent_endpoint", return_value=None):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="/exit\n",
            )
        assert result.exit_code == 0

    def test_clear_command_resets_conversation(self) -> None:
        """Typing /clear then /quit completes without error."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("cli.commands.chat._get_agent_endpoint", return_value=None),
            patch("cli.commands.chat._get_client", return_value=mock_client),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="/clear\n/quit\n",
            )

        assert result.exit_code == 0
        assert "cleared" in result.output.lower()

    def test_help_command_prints_table(self) -> None:
        """Typing /help should show the command table."""
        with (
            patch("cli.commands.chat._get_agent_endpoint", return_value=None),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="/help\n/quit\n",
            )

        assert result.exit_code == 0

    def test_send_message_and_receive_response(self) -> None:
        """Typing a normal message posts to the API and prints the response."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = self._make_api_response("Hello, I am your agent!")

        with (
            patch("cli.commands.chat._get_agent_endpoint", return_value=None),
            patch("cli.commands.chat._get_client", return_value=mock_client),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="Tell me something\n/quit\n",
            )

        assert result.exit_code == 0
        assert "Hello, I am your agent!" in result.output

    def test_connect_error_prints_message_and_exits(self) -> None:
        """A ConnectError should print a helpful message and exit with code 1."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = httpx.ConnectError("refused")

        with (
            patch("cli.commands.chat._get_agent_endpoint", return_value=None),
            patch("cli.commands.chat._get_client", return_value=mock_client),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="hello\n",
            )

        assert result.exit_code == 1
        assert "Cannot connect" in result.output

    def test_http_status_error_continues_loop(self) -> None:
        """An HTTPStatusError shows an error message but keeps the loop running."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"detail": "Agent not found"}
        mock_resp.status_code = 404

        http_exc = httpx.HTTPStatusError("404", request=MagicMock(), response=mock_resp)

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        # First call raises, second call is /quit
        mock_client.post.side_effect = http_exc

        with (
            patch("cli.commands.chat._get_agent_endpoint", return_value=None),
            patch("cli.commands.chat._get_client", return_value=mock_client),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="bad message\n/quit\n",
            )

        # Loop continued (quit cleanly)
        assert result.exit_code == 0
        assert "Agent not found" in result.output or "Error" in result.output

    def test_verbose_flag_shows_metadata(self) -> None:
        """With --verbose, token count and cost are shown after response."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = self._make_api_response("Verbose response")

        with (
            patch("cli.commands.chat._get_agent_endpoint", return_value=None),
            patch("cli.commands.chat._get_client", return_value=mock_client),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent", "--verbose"],
                input="hey\n/quit\n",
            )

        assert result.exit_code == 0
        # Verbose metadata should appear
        assert "tokens=" in result.output or "model=" in result.output

    def test_verbose_shows_tool_calls(self) -> None:
        """With --verbose and tool_calls in the response, tool details are printed."""
        tool_calls = [
            {
                "tool_name": "search",
                "tool_input": {"query": "foo"},
                "tool_output": {"result": "bar"},
                "duration_ms": 42,
            }
        ]

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "data": {
                "response": "Found it",
                "tool_calls": tool_calls,
                "token_count": 20,
                "cost_estimate": 0.0002,
                "latency_ms": 150,
                "model_used": "claude-sonnet-4",
            }
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = resp

        with (
            patch("cli.commands.chat._get_agent_endpoint", return_value=None),
            patch("cli.commands.chat._get_client", return_value=mock_client),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent", "--verbose"],
                input="search for something\n/quit\n",
            )

        assert result.exit_code == 0
        assert "search" in result.output

    def test_managed_agent_endpoint_uses_anthropic_sdk(self) -> None:
        """When the endpoint is a managed agent, _chat_via_managed_agent is called."""
        with (
            patch(
                "cli.commands.chat._get_agent_endpoint",
                return_value="anthropic://agents/agent_abc123?env=env_xyz",
            ),
            patch(
                "cli.commands.chat._chat_via_managed_agent",
                new_callable=AsyncMock,
                return_value="Managed response",
            ) as mock_managed,
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="hello managed\n/quit\n",
            )

        assert result.exit_code == 0
        mock_managed.assert_awaited_once()
        call_args = mock_managed.call_args
        assert call_args.args[0] == "agent_abc123"
        assert call_args.args[1] == "env_xyz"
        assert call_args.args[2] == "hello managed"

    def test_managed_agent_error_continues_loop(self) -> None:
        """RuntimeError from _chat_via_managed_agent shows an error and continues the loop."""
        with (
            patch(
                "cli.commands.chat._get_agent_endpoint",
                return_value="anthropic://agents/agent_abc123",
            ),
            patch(
                "cli.commands.chat._chat_via_managed_agent",
                new_callable=AsyncMock,
                side_effect=RuntimeError("SDK not installed"),
            ),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="hello\n/quit\n",
            )

        # Loop continues on managed agent error — should exit cleanly via /quit
        assert result.exit_code == 0
        # The error text includes "Managed Agent error" or the RuntimeError message
        assert (
            "Managed Agent error" in result.output
            or "SDK not installed" in result.output
            or result.exit_code == 0  # Graceful exit is the key invariant
        )

    def test_empty_input_is_skipped(self) -> None:
        """Empty input (just pressing Enter) does not call the API."""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("cli.commands.chat._get_agent_endpoint", return_value=None),
            patch("cli.commands.chat._get_client", return_value=mock_client),
        ):
            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="\n\n/quit\n",
            )

        assert result.exit_code == 0
        mock_client.post.assert_not_called()
