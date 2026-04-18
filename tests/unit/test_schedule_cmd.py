"""Tests for agentbreeder schedule CLI command."""

from __future__ import annotations

import tempfile
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()

MINIMAL_AGENT_YAML = """\
name: test-agent
version: 0.1.0
team: engineering
owner: dev@example.com
framework: langgraph
model:
  primary: gpt-4o
deploy:
  cloud: local
"""


def _make_agent_dir() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "agent.yaml").write_text(MINIMAL_AGENT_YAML)
    return d


def _mock_apscheduler():
    """Return a context manager that injects a mock APScheduler into the module."""
    mock_trigger_cls = MagicMock()
    mock_trigger = MagicMock()
    mock_trigger.get_next_fire_time = MagicMock(return_value=None)
    mock_trigger_cls.from_crontab = MagicMock(return_value=mock_trigger)

    mock_scheduler_cls = MagicMock()
    mock_scheduler = MagicMock()
    mock_scheduler_cls.return_value = mock_scheduler

    return mock_trigger_cls, mock_trigger, mock_scheduler_cls, mock_scheduler


class TestScheduleCommand:
    def test_no_args_shows_help(self) -> None:
        result = runner.invoke(app, ["schedule", "--help"])
        assert result.exit_code == 0
        assert "cron" in result.output.lower()

    def test_once_runs_immediately(self) -> None:
        d = _make_agent_dir()
        mock_trigger_cls, _, mock_scheduler_cls, _ = _mock_apscheduler()
        with patch.dict(
            "sys.modules",
            {
                "apscheduler": MagicMock(),
                "apscheduler.schedulers": MagicMock(),
                "apscheduler.schedulers.blocking": MagicMock(BlockingScheduler=mock_scheduler_cls),
                "apscheduler.triggers": MagicMock(),
                "apscheduler.triggers.cron": MagicMock(CronTrigger=mock_trigger_cls),
            },
        ):
            result = runner.invoke(app, ["schedule", str(d), "--once"])
        assert result.exit_code == 0
        assert "test-agent" in result.output

    def test_missing_cron_without_once_exits_1(self) -> None:
        d = _make_agent_dir()
        mock_trigger_cls, _, mock_scheduler_cls, _ = _mock_apscheduler()
        with patch.dict(
            "sys.modules",
            {
                "apscheduler": MagicMock(),
                "apscheduler.schedulers": MagicMock(),
                "apscheduler.schedulers.blocking": MagicMock(BlockingScheduler=mock_scheduler_cls),
                "apscheduler.triggers": MagicMock(),
                "apscheduler.triggers.cron": MagicMock(CronTrigger=mock_trigger_cls),
            },
        ):
            result = runner.invoke(app, ["schedule", str(d)])
        assert result.exit_code == 1
        assert "--cron" in result.output

    def test_missing_agent_yaml_exits_1(self) -> None:
        d = Path(tempfile.mkdtemp())  # no agent.yaml
        mock_trigger_cls, _, mock_scheduler_cls, _ = _mock_apscheduler()
        with patch.dict(
            "sys.modules",
            {
                "apscheduler": MagicMock(),
                "apscheduler.schedulers": MagicMock(),
                "apscheduler.schedulers.blocking": MagicMock(BlockingScheduler=mock_scheduler_cls),
                "apscheduler.triggers": MagicMock(),
                "apscheduler.triggers.cron": MagicMock(CronTrigger=mock_trigger_cls),
            },
        ):
            result = runner.invoke(app, ["schedule", str(d), "--once"])
        assert result.exit_code == 1
        assert "agent.yaml" in result.output

    def test_dry_run_prints_fire_times(self) -> None:
        from datetime import datetime

        d = _make_agent_dir()
        fire_times = [
            datetime(2026, 4, 19, 8, 0, tzinfo=UTC),
            datetime(2026, 4, 20, 8, 0, tzinfo=UTC),
            None,
        ]
        mock_trigger = MagicMock()
        mock_trigger.get_next_fire_time = MagicMock(side_effect=fire_times)
        mock_trigger_cls = MagicMock()
        mock_trigger_cls.from_crontab = MagicMock(return_value=mock_trigger)
        mock_scheduler_cls = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "apscheduler": MagicMock(),
                "apscheduler.schedulers": MagicMock(),
                "apscheduler.schedulers.blocking": MagicMock(BlockingScheduler=mock_scheduler_cls),
                "apscheduler.triggers": MagicMock(),
                "apscheduler.triggers.cron": MagicMock(CronTrigger=mock_trigger_cls),
            },
        ):
            result = runner.invoke(app, ["schedule", str(d), "--cron", "0 8 * * *", "--dry-run"])
        assert result.exit_code == 0
        assert "0 8 * * *" in result.output
        assert "2026-04-19" in result.output

    def test_apscheduler_not_installed_exits_1(self) -> None:
        d = _make_agent_dir()
        import builtins

        real_import = builtins.__import__

        def mock_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if name.startswith("apscheduler"):
                raise ImportError("No module named 'apscheduler'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = runner.invoke(app, ["schedule", str(d), "--once"])
        assert result.exit_code == 1
        assert "agentbreeder" in result.output
        assert "schedule" in result.output

    def test_invalid_cron_exits_1(self) -> None:
        d = _make_agent_dir()
        mock_trigger_cls = MagicMock()
        mock_trigger_cls.from_crontab = MagicMock(side_effect=Exception("invalid cron"))
        mock_scheduler_cls = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "apscheduler": MagicMock(),
                "apscheduler.schedulers": MagicMock(),
                "apscheduler.schedulers.blocking": MagicMock(BlockingScheduler=mock_scheduler_cls),
                "apscheduler.triggers": MagicMock(),
                "apscheduler.triggers.cron": MagicMock(CronTrigger=mock_trigger_cls),
            },
        ):
            result = runner.invoke(app, ["schedule", str(d), "--cron", "bad cron expr"])
        assert result.exit_code == 1
        assert "Invalid cron" in result.output
