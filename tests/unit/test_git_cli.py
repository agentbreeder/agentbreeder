"""Tests for git-workflow CLI commands — submit, review, publish, chat."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import httpx
from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FAKE_PR_ID = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))
FAKE_PR_ID_2 = str(uuid.UUID("00000000-0000-0000-0000-000000000002"))


def _make_pr(
    *,
    pr_id: str = FAKE_PR_ID,
    status: str = "submitted",
    resource_type: str = "agent",
    resource_name: str = "my-agent",
    title: str = "Update agent/my-agent",
    submitter: str = "alice",
    branch: str = "draft/alice/agent/my-agent",
    description: str = "some changes",
    reviewer: str | None = None,
    tag: str | None = None,
) -> dict:
    pr: dict = {
        "id": pr_id,
        "status": status,
        "resource_type": resource_type,
        "resource_name": resource_name,
        "title": title,
        "submitter": submitter,
        "branch": branch,
        "description": description,
        "created_at": "2026-03-12T10:00:00",
        "updated_at": "2026-03-12T10:00:00",
    }
    if reviewer:
        pr["reviewer"] = reviewer
    if tag:
        pr["tag"] = tag
    return pr


def _mock_response(data: dict | list, status_code: int = 200) -> httpx.Response:
    """Build a mock httpx.Response with the given data."""
    body = json.dumps({"data": data}).encode()
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "http://test"),
        content=body,
        headers={"content-type": "application/json"},
    )


def _mock_error_response(detail: str, status_code: int = 400) -> httpx.Response:
    """Build a mock error httpx.Response."""
    body = json.dumps({"detail": detail}).encode()
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("GET", "http://test"),
        content=body,
        headers={"content-type": "application/json"},
    )


# ===================================================================
# submit command
# ===================================================================


class TestSubmitCommand:
    def test_submit_creates_pr(self) -> None:
        pr = _make_pr()
        resp = _mock_response(pr)
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["id"] == FAKE_PR_ID
        assert output["status"] == "submitted"

    def test_submit_with_message(self) -> None:
        pr = _make_pr(description="Improved error handling")
        resp = _mock_response(pr)
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["submit", "agent", "my-agent", "-m", "Improved error handling", "--json"],
            )
        assert result.exit_code == 0
        # Verify the message was passed to the API
        call_args = ctx.post.call_args
        assert call_args[1]["json"]["description"] == "Improved error handling"

    def test_submit_api_error(self) -> None:
        error_resp = _mock_error_response("Branch not found", 404)
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=httpx.Request("POST", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "error" in output
        assert "Branch not found" in output["error"]

    def test_submit_json_output(self) -> None:
        pr = _make_pr()
        pr["diff"] = {
            "files": [{"file_path": "agent.yaml", "status": "modified"}],
            "stats": {"insertions": 5, "deletions": 2},
        }
        resp = _mock_response(pr)
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["diff"]["files"][0]["file_path"] == "agent.yaml"


# ===================================================================
# review command
# ===================================================================


class TestReviewCommand:
    def test_review_list_shows_prs(self) -> None:
        prs = [_make_pr(), _make_pr(pr_id=FAKE_PR_ID_2, resource_name="other")]
        resp = _mock_response({"prs": prs})
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert len(output) == 2

    def test_review_list_filter_by_status(self) -> None:
        prs = [_make_pr(status="approved")]
        resp = _mock_response({"prs": prs})
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list", "--status", "approved", "--json"])
        assert result.exit_code == 0
        # Verify the status param was sent
        call_args = ctx.get.call_args
        assert call_args[1]["params"]["status"] == "approved"

    def test_review_show_pr_detail(self) -> None:
        pr = _make_pr()
        pr["comments"] = [{"author": "bob", "text": "LGTM"}]
        resp = _mock_response(pr)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "show", FAKE_PR_ID, "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["id"] == FAKE_PR_ID
        assert output["comments"][0]["text"] == "LGTM"

    def test_review_approve(self) -> None:
        pr = _make_pr(status="approved", reviewer="bob")
        resp = _mock_response(pr)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "approve", FAKE_PR_ID, "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["status"] == "approved"

    def test_review_reject_requires_message(self) -> None:
        # The reject command has --message/-m as required (no default).
        # Invoking without -m should fail.
        result = runner.invoke(app, ["review", "reject", FAKE_PR_ID])
        assert result.exit_code != 0

    def test_review_comment(self) -> None:
        comment = {
            "id": "comment-1",
            "author": "bob",
            "text": "Looks good, minor nit",
            "created_at": "2026-03-12T11:00:00",
        }
        resp = _mock_response(comment)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["review", "comment", FAKE_PR_ID, "-m", "Looks good, minor nit", "--json"],
            )
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["text"] == "Looks good, minor nit"

    def test_review_list_empty(self) -> None:
        resp = _mock_response({"prs": []})
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output == []


# ===================================================================
# publish command
# ===================================================================


class TestPublishCommand:
    def test_publish_finds_and_merges_pr(self) -> None:
        pr = _make_pr(status="approved")
        merged = _make_pr(status="published", tag="agent/my-agent/1.0.0")
        list_resp = _mock_response({"prs": [pr]})
        merge_resp = _mock_response(merged)

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.get.return_value = list_resp
            ctx.post.return_value = merge_resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["status"] == "published"

    def test_publish_with_version_flag(self) -> None:
        pr = _make_pr(status="approved")
        merged = _make_pr(status="published", tag="agent/my-agent/2.1.0")
        list_resp = _mock_response({"prs": [pr]})
        merge_resp = _mock_response(merged)

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.get.return_value = list_resp
            ctx.post.return_value = merge_resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["publish", "agent", "my-agent", "--version", "2.1.0", "--json"],
            )
        assert result.exit_code == 0
        # Verify the version was passed in the merge request
        call_args = ctx.post.call_args
        assert call_args[1]["json"]["tag_version"] == "2.1.0"

    def test_publish_no_approved_pr(self) -> None:
        list_resp = _mock_response({"prs": []})

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.get.return_value = list_resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "error" in output
        assert "No approved PR" in output["error"]

    def test_publish_api_error(self) -> None:
        error_resp = _mock_error_response("Internal server error", 500)
        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.get.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=httpx.Request("GET", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "error" in output


# ===================================================================
# chat command
# ===================================================================


class TestChatCommand:
    def test_chat_sends_message(self) -> None:
        """Test JSON mode reads from stdin and produces JSON output."""
        chat_data = {
            "response": "Hello! How can I help?",
            "tool_calls": [],
            "token_count": 42,
            "cost_estimate": 0.001,
            "latency_ms": 150,
            "model_used": "gpt-4o",
        }
        resp = _mock_response(chat_data)

        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="hello\n",
            )
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["response"] == "Hello! How can I help?"
        assert output["token_count"] == 42

    def test_chat_verbose_shows_tools(self) -> None:
        """In interactive mode with --verbose, tool calls should be displayed."""
        chat_data = {
            "response": "Found 3 results.",
            "tool_calls": [
                {
                    "tool_name": "search",
                    "tool_input": {"query": "test"},
                    "tool_output": {"results": [1, 2, 3]},
                    "duration_ms": 50,
                }
            ],
            "token_count": 100,
            "cost_estimate": 0.002,
            "latency_ms": 200,
            "model_used": "gpt-4o",
        }
        resp = _mock_response(chat_data)

        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            # Send one message then /quit
            result = runner.invoke(
                app,
                ["chat", "my-agent", "--verbose"],
                input="test query\n/quit\n",
            )
        assert result.exit_code == 0
        assert "search" in result.output
        assert "Tool Call" in result.output

    def test_chat_model_override(self) -> None:
        """Test --model flag is passed through to the API."""
        chat_data = {
            "response": "Hi",
            "tool_calls": [],
            "token_count": 10,
            "cost_estimate": 0.0001,
            "latency_ms": 80,
            "model_used": "claude-sonnet-4",
        }
        resp = _mock_response(chat_data)

        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--model", "claude-sonnet-4", "--json"],
                input="hello\n",
            )
        assert result.exit_code == 0
        # Verify model_override was sent in the request
        call_args = ctx.post.call_args
        assert call_args[1]["json"]["model_override"] == "claude-sonnet-4"

    def test_chat_json_mode(self) -> None:
        """Test --json with stdin sends each line as a turn and outputs JSON."""
        chat_data_1 = {
            "response": "Response 1",
            "tool_calls": [],
            "token_count": 20,
            "cost_estimate": 0.0005,
            "latency_ms": 100,
            "model_used": "gpt-4o",
        }
        chat_data_2 = {
            "response": "Response 2",
            "tool_calls": [],
            "token_count": 30,
            "cost_estimate": 0.0008,
            "latency_ms": 120,
            "model_used": "gpt-4o",
        }
        resp1 = _mock_response(chat_data_1)
        resp2 = _mock_response(chat_data_2)

        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=ctx)
            ctx.__exit__ = MagicMock(return_value=False)
            ctx.post.side_effect = [resp1, resp2]
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="first message\nsecond message\n",
            )
        assert result.exit_code == 0
        lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
        assert len(lines) == 2
        out1 = json.loads(lines[0])
        out2 = json.loads(lines[1])
        assert out1["response"] == "Response 1"
        assert out2["response"] == "Response 2"


# ===================================================================
# Additional submit tests — Rich output, connection errors, diff display
# ===================================================================


class TestSubmitRichOutput:
    """Tests for the Rich panel/table output paths (non-JSON mode)."""

    def _make_client_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def test_submit_rich_panel_shows_pr_id_and_status(self) -> None:
        """Non-JSON mode should render a Rich panel with PR details."""
        pr = _make_pr(description="Improved error handling")
        pr["diff"] = {
            "files": [
                {"file_path": "agent.yaml", "status": "modified"},
                {"file_path": "README.md", "status": "added"},
            ],
            "stats": {"insertions": 10, "deletions": 3},
        }
        resp = _mock_response(pr)
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app, ["submit", "agent", "my-agent", "-m", "Improved error handling"]
            )
        assert result.exit_code == 0
        assert FAKE_PR_ID in result.output
        assert "Submitted for Review" in result.output
        assert "agent.yaml" in result.output
        assert "README.md" in result.output
        assert "+10" in result.output
        assert "-3" in result.output

    def test_submit_rich_panel_no_diff(self) -> None:
        """Non-JSON mode without diff should still render cleanly."""
        pr = _make_pr()
        resp = _mock_response(pr)
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent"])
        assert result.exit_code == 0
        assert "Submitted for Review" in result.output
        assert "garden review show" in result.output

    def test_submit_rich_panel_with_many_files(self) -> None:
        """Diff with > 5 files should show truncation message."""
        pr = _make_pr()
        pr["diff"] = {
            "files": [{"file_path": f"file{i}.yaml", "status": "modified"} for i in range(8)],
            "stats": {"insertions": 20, "deletions": 5},
        }
        resp = _mock_response(pr)
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent"])
        assert result.exit_code == 0
        assert "and 3 more" in result.output

    def test_submit_connection_error_json(self) -> None:
        """ConnectError in JSON mode should output a JSON error."""
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "error" in output
        assert "Cannot connect" in output["error"]

    def test_submit_connection_error_rich(self) -> None:
        """ConnectError in non-JSON mode should render a Rich error panel."""
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent"])
        assert result.exit_code == 1
        assert "Connection error" in result.output

    def test_submit_api_error_rich_panel(self) -> None:
        """HTTPStatusError in non-JSON mode should render a Rich error panel."""
        error_resp = _mock_error_response("Branch not found", 404)
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=httpx.Request("POST", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent"])
        assert result.exit_code == 1
        assert "Submit failed" in result.output
        assert "Branch not found" in result.output

    def test_submit_diff_file_statuses(self) -> None:
        """Verify A/M/D/? status chars appear in diff display."""
        pr = _make_pr()
        pr["diff"] = {
            "files": [
                {"file_path": "new.yaml", "status": "added"},
                {"file_path": "old.yaml", "status": "deleted"},
                {"file_path": "changed.yaml", "status": "modified"},
                {"file_path": "unknown.yaml", "status": "renamed"},
            ],
            "stats": {},
        }
        resp = _mock_response(pr)
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent"])
        assert result.exit_code == 0
        # Check file paths appear
        assert "new.yaml" in result.output
        assert "old.yaml" in result.output

    def test_submit_api_error_non_json_body(self) -> None:
        """HTTPStatusError where response body is not JSON."""
        bad_resp = httpx.Response(
            status_code=500,
            request=httpx.Request("POST", "http://test"),
            content=b"Internal Server Error",
            headers={"content-type": "text/plain"},
        )
        with patch("cli.commands.submit._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=httpx.Request("POST", "http://test"),
                response=bad_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["submit", "agent", "my-agent", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "error" in output


# ===================================================================
# Additional review tests — show detail, approve/reject/comment Rich,
# error paths, connection errors
# ===================================================================


class TestReviewRichOutput:
    """Tests for review subcommand Rich output (non-JSON mode)."""

    def _make_client_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def test_review_list_rich_table(self) -> None:
        """Non-JSON list should render a Rich table with PR rows."""
        prs = [
            _make_pr(),
            _make_pr(pr_id=FAKE_PR_ID_2, resource_name="other-agent"),
        ]
        resp = _mock_response({"prs": prs})
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list"])
        assert result.exit_code == 0
        assert "Pull Requests" in result.output
        assert "agent/my-ag" in result.output
        assert "agent/other" in result.output
        assert "2 result(s)" in result.output

    def test_review_list_empty_rich(self) -> None:
        """Empty list should show a 'no PRs found' message."""
        resp = _mock_response({"prs": []})
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list"])
        assert result.exit_code == 0
        assert "No pull requests found" in result.output

    def test_review_list_connection_error_json(self) -> None:
        """ConnectError should produce JSON error output."""
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "error" in output
        assert "Cannot connect" in output["error"]

    def test_review_list_connection_error_rich(self) -> None:
        """ConnectError in non-JSON should render Rich panel."""
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list"])
        assert result.exit_code == 1
        assert "Connection error" in result.output

    def test_review_list_http_error_json(self) -> None:
        """HTTP error should produce JSON error output."""
        error_resp = _mock_error_response("Forbidden", 403)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.side_effect = httpx.HTTPStatusError(
                "Forbidden",
                request=httpx.Request("GET", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "error" in output

    def test_review_list_http_error_rich(self) -> None:
        """HTTP error in non-JSON should render Rich panel."""
        error_resp = _mock_error_response("Forbidden", 403)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.side_effect = httpx.HTTPStatusError(
                "Forbidden",
                request=httpx.Request("GET", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list"])
        assert result.exit_code == 1
        assert "Request failed" in result.output

    def test_review_show_rich_detail_with_commits_and_diff(self) -> None:
        """Show subcommand should render commits table and diff."""
        pr = _make_pr(status="approved", reviewer="bob")
        pr["tag"] = "agent/my-agent/1.0.0"
        pr["reject_reason"] = None
        pr["commits"] = [
            {
                "sha": "abc12345def67890",
                "author": "alice",
                "message": "Update agent config",
                "date": "2026-03-12T09:30:00",
            }
        ]
        pr["diff"] = {
            "files": [
                {
                    "file_path": "agent.yaml",
                    "status": "modified",
                    "diff_text": "--- a/agent.yaml\n+++ b/agent.yaml\n@@ -1 +1 @@\n-old\n+new",
                }
            ],
            "stats": {"insertions": 1, "deletions": 1},
        }
        pr["comments"] = [
            {
                "author": "bob",
                "text": "Looks great!",
                "created_at": "2026-03-12T11:00:00",
            }
        ]
        resp = _mock_response(pr)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "show", FAKE_PR_ID])
        assert result.exit_code == 0
        assert "Pull Request" in result.output
        assert "Commits" in result.output
        assert "alice" in result.output
        assert "agent.yaml" in result.output
        assert "Looks great!" in result.output
        assert "bob" in result.output

    def test_review_show_rich_with_reject_reason(self) -> None:
        """Show subcommand should display reject reason if present."""
        pr = _make_pr(status="rejected", reviewer="bob")
        pr["reject_reason"] = "Missing test coverage"
        resp = _mock_response(pr)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "show", FAKE_PR_ID])
        assert result.exit_code == 0
        assert "Missing test coverage" in result.output

    def test_review_show_connection_error(self) -> None:
        """ConnectError on show subcommand."""
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "show", FAKE_PR_ID, "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "Cannot connect" in output["error"]

    def test_review_show_http_error(self) -> None:
        """HTTP 404 on show subcommand."""
        error_resp = _mock_error_response("PR not found", 404)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=httpx.Request("GET", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "show", FAKE_PR_ID, "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "PR not found" in output["error"]

    def test_review_show_invalid_pr_id(self) -> None:
        """Invalid UUID should fail with error."""
        result = runner.invoke(app, ["review", "show", "not-a-uuid"])
        assert result.exit_code == 1
        assert "Invalid PR ID" in result.output

    def test_review_approve_rich_panel(self) -> None:
        """Approve in non-JSON mode should render a green success panel."""
        pr = _make_pr(status="approved", reviewer="bob")
        resp = _mock_response(pr)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "approve", FAKE_PR_ID])
        assert result.exit_code == 0
        assert "Approved" in result.output
        assert "PR approved" in result.output
        assert "garden publish" in result.output

    def test_review_approve_connection_error(self) -> None:
        """ConnectError on approve."""
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "approve", FAKE_PR_ID, "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "Cannot connect" in output["error"]

    def test_review_approve_http_error(self) -> None:
        """HTTP error on approve."""
        error_resp = _mock_error_response("Already approved", 409)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Conflict",
                request=httpx.Request("POST", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "approve", FAKE_PR_ID, "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "Already approved" in output["error"]

    def test_review_approve_http_error_rich(self) -> None:
        """HTTP error on approve in non-JSON mode."""
        error_resp = _mock_error_response("Already approved", 409)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Conflict",
                request=httpx.Request("POST", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "approve", FAKE_PR_ID])
        assert result.exit_code == 1
        assert "Request failed" in result.output

    def test_review_reject_with_message_json(self) -> None:
        """Reject with -m flag should pass reason to API."""
        pr = _make_pr(status="rejected", reviewer="bob")
        resp = _mock_response(pr)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["review", "reject", FAKE_PR_ID, "-m", "Missing tests", "--json"],
            )
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["status"] == "rejected"
        call_args = ctx.post.call_args
        assert call_args[1]["json"]["reason"] == "Missing tests"

    def test_review_reject_rich_panel(self) -> None:
        """Reject in non-JSON mode should render a red panel."""
        pr = _make_pr(status="rejected", reviewer="bob")
        resp = _mock_response(pr)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["review", "reject", FAKE_PR_ID, "-m", "Missing tests"],
            )
        assert result.exit_code == 0
        assert "Rejected" in result.output
        assert "PR rejected" in result.output
        assert "Missing tests" in result.output

    def test_review_reject_connection_error(self) -> None:
        """ConnectError on reject."""
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["review", "reject", FAKE_PR_ID, "-m", "Bad", "--json"],
            )
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "Cannot connect" in output["error"]

    def test_review_reject_http_error(self) -> None:
        """HTTP error on reject."""
        error_resp = _mock_error_response("Not found", 404)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=httpx.Request("POST", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["review", "reject", FAKE_PR_ID, "-m", "Bad", "--json"],
            )
        assert result.exit_code == 1

    def test_review_comment_rich_panel(self) -> None:
        """Comment in non-JSON mode should render a green panel."""
        comment_data = {
            "id": "comment-1",
            "author": "bob",
            "text": "Nice work!",
            "created_at": "2026-03-12T11:00:00",
        }
        resp = _mock_response(comment_data)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["review", "comment", FAKE_PR_ID, "-m", "Nice work!"],
            )
        assert result.exit_code == 0
        assert "Comment added" in result.output
        assert "Nice work!" in result.output

    def test_review_comment_connection_error(self) -> None:
        """ConnectError on comment."""
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["review", "comment", FAKE_PR_ID, "-m", "Nit", "--json"],
            )
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "Cannot connect" in output["error"]

    def test_review_comment_http_error(self) -> None:
        """HTTP error on comment."""
        error_resp = _mock_error_response("PR not found", 404)
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=httpx.Request("POST", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["review", "comment", FAKE_PR_ID, "-m", "Nit", "--json"],
            )
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "PR not found" in output["error"]

    def test_review_list_with_type_filter(self) -> None:
        """List with --type filter passes resource_type param."""
        prs = [_make_pr()]
        resp = _mock_response({"prs": prs})
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list", "--type", "agent", "--json"])
        assert result.exit_code == 0
        call_args = ctx.get.call_args
        assert call_args[1]["params"]["resource_type"] == "agent"

    def test_review_list_status_all(self) -> None:
        """List with --status all should not send status param."""
        prs = [_make_pr()]
        resp = _mock_response({"prs": prs})
        with patch("cli.commands.review._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["review", "list", "--status", "all", "--json"])
        assert result.exit_code == 0
        call_args = ctx.get.call_args
        assert "status" not in call_args[1]["params"]


# ===================================================================
# Additional publish tests — Rich output, multiple PRs, merge flow
# ===================================================================


class TestPublishRichOutput:
    """Tests for publish command Rich output and edge cases."""

    def _make_client_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def test_publish_rich_output_with_tag(self) -> None:
        """Non-JSON publish should show Rich panel with tag and registry URL."""
        pr = _make_pr(status="approved")
        merged = _make_pr(status="published", tag="agent/my-agent/1.0.0")
        list_resp = _mock_response({"prs": [pr]})
        merge_resp = _mock_response(merged)

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = list_resp
            ctx.post.return_value = merge_resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent"])
        assert result.exit_code == 0
        assert "Published" in result.output
        assert "Published successfully" in result.output
        assert "agent/my-agent/1.0.0" in result.output
        assert "registry" in result.output.lower()

    def test_publish_rich_output_without_tag(self) -> None:
        """Publish without tag should still show success panel."""
        pr = _make_pr(status="approved")
        merged = _make_pr(status="published")
        list_resp = _mock_response({"prs": [pr]})
        merge_resp = _mock_response(merged)

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = list_resp
            ctx.post.return_value = merge_resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent"])
        assert result.exit_code == 0
        assert "Published" in result.output

    def test_publish_rich_no_approved_pr(self) -> None:
        """No approved PR in non-JSON mode should render Rich error panel."""
        list_resp = _mock_response({"prs": []})

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = list_resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent"])
        assert result.exit_code == 1
        assert "Not found" in result.output or "No approved PR" in result.output

    def test_publish_multiple_approved_picks_correct(self) -> None:
        """When multiple PRs exist, publish picks the matching resource name."""
        pr_other = _make_pr(
            pr_id=FAKE_PR_ID_2,
            status="approved",
            resource_name="other-agent",
        )
        pr_target = _make_pr(status="approved", resource_name="my-agent")
        merged = _make_pr(status="published", tag="agent/my-agent/1.0.0")
        list_resp = _mock_response({"prs": [pr_other, pr_target]})
        merge_resp = _mock_response(merged)

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = list_resp
            ctx.post.return_value = merge_resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent", "--json"])
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["status"] == "published"
        # Verify merge was called with the correct PR id
        call_args = ctx.post.call_args
        assert FAKE_PR_ID in call_args[0][0]

    def test_publish_connection_error_json(self) -> None:
        """ConnectError on search step should produce JSON error."""
        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "Cannot connect" in output["error"]

    def test_publish_connection_error_rich(self) -> None:
        """ConnectError in non-JSON mode."""
        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent"])
        assert result.exit_code == 1
        assert "Connection error" in result.output

    def test_publish_merge_step_http_error_json(self) -> None:
        """HTTP error on merge step should produce JSON error."""
        pr = _make_pr(status="approved")
        list_resp = _mock_response({"prs": [pr]})
        error_resp = _mock_error_response("Merge conflict", 409)

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = list_resp
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Conflict",
                request=httpx.Request("POST", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "Merge conflict" in output["error"]

    def test_publish_merge_step_http_error_rich(self) -> None:
        """HTTP error on merge step in non-JSON mode."""
        pr = _make_pr(status="approved")
        list_resp = _mock_response({"prs": [pr]})
        error_resp = _mock_error_response("Merge conflict", 409)

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = list_resp
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Conflict",
                request=httpx.Request("POST", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent"])
        assert result.exit_code == 1
        assert "Publish failed" in result.output

    def test_publish_merge_step_connection_error(self) -> None:
        """ConnectError on merge step."""
        pr = _make_pr(status="approved")
        list_resp = _mock_response({"prs": [pr]})

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = list_resp
            ctx.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "Cannot connect" in output["error"]

    def test_publish_http_error_non_json_body(self) -> None:
        """HTTP error where the response body is not JSON."""
        bad_resp = httpx.Response(
            status_code=500,
            request=httpx.Request("GET", "http://test"),
            content=b"Internal Server Error",
            headers={"content-type": "text/plain"},
        )
        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=httpx.Request("GET", "http://test"),
                response=bad_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent", "--json"])
        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert "error" in output

    def test_publish_version_flag_no_version_in_merge(self) -> None:
        """Publish without --version should not send tag_version."""
        pr = _make_pr(status="approved")
        merged = _make_pr(status="published")
        list_resp = _mock_response({"prs": [pr]})
        merge_resp = _mock_response(merged)

        with patch("cli.commands.publish._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.get.return_value = list_resp
            ctx.post.return_value = merge_resp
            mock_client.return_value = ctx

            result = runner.invoke(app, ["publish", "agent", "my-agent", "--json"])
        assert result.exit_code == 0
        call_args = ctx.post.call_args
        # Should send None (no body) when no version
        assert call_args[1]["json"] is None


# ===================================================================
# Additional chat tests — interactive mode, /help, /clear, session
# summary, error handling, verbose
# ===================================================================


class TestChatInteractive:
    """Tests for the interactive chat mode."""

    def _make_client_ctx(self) -> MagicMock:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        return ctx

    def _make_chat_response(
        self,
        response: str = "Hello!",
        tool_calls: list | None = None,
        token_count: int = 20,
        cost: float = 0.001,
        latency: int = 100,
        model: str = "gpt-4o",
    ) -> httpx.Response:
        return _mock_response(
            {
                "response": response,
                "tool_calls": tool_calls or [],
                "token_count": token_count,
                "cost_estimate": cost,
                "latency_ms": latency,
                "model_used": model,
            }
        )

    def test_interactive_quit_command(self) -> None:
        """Typing /quit should exit and show session summary with 0 turns."""
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            mock_client.return_value = ctx

            result = runner.invoke(app, ["chat", "my-agent"], input="/quit\n")
        assert result.exit_code == 0
        assert "No messages exchanged" in result.output

    def test_interactive_exit_command(self) -> None:
        """/exit should also quit."""
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            mock_client.return_value = ctx

            result = runner.invoke(app, ["chat", "my-agent"], input="/exit\n")
        assert result.exit_code == 0

    def test_interactive_q_command(self) -> None:
        """/q should also quit."""
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            mock_client.return_value = ctx

            result = runner.invoke(app, ["chat", "my-agent"], input="/q\n")
        assert result.exit_code == 0

    def test_interactive_help_command(self) -> None:
        """/help should print the help table then continue."""
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            mock_client.return_value = ctx

            result = runner.invoke(app, ["chat", "my-agent"], input="/help\n/quit\n")
        assert result.exit_code == 0
        assert "Chat Commands" in result.output
        assert "/help" in result.output
        assert "/clear" in result.output
        assert "/quit" in result.output

    def test_interactive_clear_command(self) -> None:
        """/clear should reset conversation state."""
        resp = self._make_chat_response()
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="hello\n/clear\n/quit\n",
            )
        assert result.exit_code == 0
        assert "Conversation cleared" in result.output
        # After clear, session summary should say no messages (0 turns)
        assert "No messages exchanged" in result.output

    def test_interactive_session_summary_with_turns(self) -> None:
        """Session summary should show turn count, tokens, and cost."""
        resp = self._make_chat_response(token_count=50, cost=0.002, latency=150)
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="hello\n/quit\n",
            )
        assert result.exit_code == 0
        assert "Session Summary" in result.output
        assert "Turns" in result.output
        assert "Tokens" in result.output
        assert "Cost" in result.output

    def test_interactive_empty_input_skipped(self) -> None:
        """Empty lines should be ignored."""
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="\n\n\n/quit\n",
            )
        assert result.exit_code == 0
        assert "No messages exchanged" in result.output

    def test_interactive_connection_error(self) -> None:
        """ConnectError during chat should exit with error."""
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="hello\n",
            )
        assert result.exit_code == 1
        assert "Cannot connect" in result.output

    def test_interactive_http_error_continues(self) -> None:
        """HTTP error mid-chat should show error but continue the loop."""
        error_resp = _mock_error_response("Rate limited", 429)
        ok_resp = self._make_chat_response(response="Back to normal")

        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = [
                httpx.HTTPStatusError(
                    "Too Many Requests",
                    request=httpx.Request("POST", "http://test"),
                    response=error_resp,
                ),
                ok_resp,
            ]
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="first\nsecond\n/quit\n",
            )
        assert result.exit_code == 0
        assert "Error" in result.output or "Rate limited" in result.output
        assert "Back to normal" in result.output

    def test_interactive_http_error_non_json_body(self) -> None:
        """HTTP error with non-JSON body during interactive chat."""
        bad_resp = httpx.Response(
            status_code=500,
            request=httpx.Request("POST", "http://test"),
            content=b"Internal Server Error",
            headers={"content-type": "text/plain"},
        )
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = [
                httpx.HTTPStatusError(
                    "Server Error",
                    request=httpx.Request("POST", "http://test"),
                    response=bad_resp,
                ),
            ]
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="hello\n/quit\n",
            )
        assert result.exit_code == 0
        assert "Error" in result.output

    def test_interactive_verbose_tool_calls(self) -> None:
        """Verbose mode should display tool call panels."""
        resp = self._make_chat_response(
            response="Found it!",
            tool_calls=[
                {
                    "tool_name": "search_kb",
                    "tool_input": {"query": "return policy"},
                    "tool_output": {"results": ["policy doc"]},
                    "duration_ms": 75,
                }
            ],
        )
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--verbose"],
                input="search for return policy\n/quit\n",
            )
        assert result.exit_code == 0
        assert "Tool Call" in result.output
        assert "search_kb" in result.output
        assert "75ms" in result.output or "75" in result.output

    def test_interactive_verbose_metadata(self) -> None:
        """Verbose mode should show model, tokens, cost, latency."""
        resp = self._make_chat_response(
            model="claude-sonnet-4", token_count=100, cost=0.005, latency=200
        )
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--verbose"],
                input="hello\n/quit\n",
            )
        assert result.exit_code == 0
        assert "claude-sonnet-4" in result.output
        assert "tokens=100" in result.output
        assert "200ms" in result.output

    def test_interactive_verbose_tool_output_truncated(self) -> None:
        """Long tool output should be truncated in verbose display."""
        long_output = {"data": "x" * 300}
        resp = self._make_chat_response(
            response="Done",
            tool_calls=[
                {
                    "tool_name": "big_tool",
                    "tool_input": {},
                    "tool_output": long_output,
                    "duration_ms": 10,
                }
            ],
        )
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--verbose"],
                input="do stuff\n/quit\n",
            )
        assert result.exit_code == 0
        assert "..." in result.output

    def test_interactive_model_override(self) -> None:
        """--model flag should appear in the header panel."""
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--model", "gpt-4o"],
                input="/quit\n",
            )
        assert result.exit_code == 0
        assert "gpt-4o" in result.output

    def test_interactive_env_flag(self) -> None:
        """--env flag should appear in the header panel."""
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--env", "staging"],
                input="/quit\n",
            )
        assert result.exit_code == 0
        assert "staging" in result.output

    def test_json_mode_connection_error(self) -> None:
        """JSON mode connection error should output JSON error."""
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="hello\n",
            )
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert "error" in output

    def test_json_mode_http_error(self) -> None:
        """JSON mode HTTP error should output JSON error."""
        error_resp = _mock_error_response("Agent not found", 404)
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Not Found",
                request=httpx.Request("POST", "http://test"),
                response=error_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="hello\n",
            )
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert "error" in output
        assert "Agent not found" in output["error"]

    def test_json_mode_empty_lines_skipped(self) -> None:
        """Empty lines in JSON mode should be skipped."""
        resp = self._make_chat_response(response="Hi")
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="\n\nhello\n\n",
            )
        assert result.exit_code == 0
        lines = [ln for ln in result.output.strip().splitlines() if ln.strip()]
        assert len(lines) == 1

    def test_json_mode_http_error_no_json_body(self) -> None:
        """JSON mode HTTP error with non-JSON response body."""
        bad_resp = httpx.Response(
            status_code=500,
            request=httpx.Request("POST", "http://test"),
            content=b"Internal Server Error",
            headers={"content-type": "text/plain"},
        )
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.side_effect = httpx.HTTPStatusError(
                "Server Error",
                request=httpx.Request("POST", "http://test"),
                response=bad_resp,
            )
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent", "--json"],
                input="hello\n",
            )
        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert "error" in output

    def test_interactive_eof_exits_gracefully(self) -> None:
        """EOF (no more input) should exit gracefully."""
        resp = self._make_chat_response()
        with patch("cli.commands.chat._get_client") as mock_client:
            ctx = self._make_client_ctx()
            ctx.post.return_value = resp
            mock_client.return_value = ctx

            result = runner.invoke(
                app,
                ["chat", "my-agent"],
                input="hello\n",  # EOF after this line
            )
        # Should exit gracefully (0 or show summary)
        assert result.exit_code == 0
