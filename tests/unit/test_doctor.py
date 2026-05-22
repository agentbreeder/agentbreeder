"""Tests for `agentbreeder doctor` — prerequisite preflight (issue #462)."""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from cli.commands.doctor import (
    MIN_FREE_DISK_BYTES,
    MIN_RAM_BYTES,
    CheckResult,
    _check_container_runtime,
    _check_disk,
    _check_python,
    _check_ram,
    has_blocker,
    run_all_checks,
)
from cli.main import app

runner = CliRunner()


class TestIndividualChecks:
    def test_python_check_passes_on_current_interpreter(self) -> None:
        result = _check_python()
        assert result.ok is True
        assert result.blocker is True
        assert "." in result.detail

    def test_python_check_fails_when_too_old(self) -> None:
        # Raise the floor above the current interpreter to simulate "too old".
        import sys

        unreachable = (sys.version_info.major, sys.version_info.minor + 99)
        with patch("cli.commands.doctor.MIN_PYTHON", unreachable):
            result = _check_python()
        assert result.ok is False
        assert "pyenv" in " ".join(result.fix)

    def test_disk_check_passes_when_enough_free(self) -> None:
        with patch("cli.commands.doctor.shutil.disk_usage") as mock_du:
            mock_du.return_value = type(
                "DU", (), {"total": 0, "used": 0, "free": MIN_FREE_DISK_BYTES * 2}
            )()
            result = _check_disk()
        assert result.ok is True

    def test_disk_check_fails_when_low(self) -> None:
        with patch("cli.commands.doctor.shutil.disk_usage") as mock_du:
            mock_du.return_value = type("DU", (), {"total": 0, "used": 0, "free": 1024**3})()
            result = _check_disk()
        assert result.ok is False
        assert "Free up" in " ".join(result.fix)

    def test_runtime_check_missing_returns_install_instructions(self) -> None:
        with patch("cli.commands.quickstart._detect_runtime", return_value=None):
            with patch(
                "cli.commands.quickstart._install_instructions",
                return_value=["Install docker desktop"],
            ):
                result = _check_container_runtime()
        assert result.ok is False
        assert result.fix == ("Install docker desktop",)

    def test_runtime_check_daemon_down(self) -> None:
        with patch(
            "cli.commands.quickstart._detect_runtime",
            return_value=("docker", "docker compose"),
        ):
            with patch("cli.commands.quickstart._runtime_is_running", return_value=False):
                result = _check_container_runtime()
        assert result.ok is False
        assert "daemon not reachable" in result.detail

    def test_runtime_check_happy_path(self) -> None:
        with patch(
            "cli.commands.quickstart._detect_runtime",
            return_value=("podman", "podman compose"),
        ):
            with patch("cli.commands.quickstart._runtime_is_running", return_value=True):
                result = _check_container_runtime()
        assert result.ok is True
        assert "podman" in result.detail

    def test_ram_check_undetectable_does_not_block(self) -> None:
        with patch("cli.commands.doctor._total_ram_bytes", return_value=None):
            result = _check_ram()
        assert result.ok is True
        assert result.blocker is False

    def test_ram_check_below_minimum_blocks(self) -> None:
        with patch("cli.commands.doctor._total_ram_bytes", return_value=MIN_RAM_BYTES // 2):
            result = _check_ram()
        assert result.ok is False
        assert result.blocker is True


class TestHasBlocker:
    def test_returns_false_when_only_warnings_failed(self) -> None:
        results = [
            CheckResult("a", ok=True, detail="ok"),
            CheckResult("b", ok=False, detail="warn", blocker=False),
        ]
        assert has_blocker(results) is False

    def test_returns_true_on_failed_blocker(self) -> None:
        results = [
            CheckResult("a", ok=True, detail="ok"),
            CheckResult("b", ok=False, detail="bad", blocker=True),
        ]
        assert has_blocker(results) is True


class TestDoctorCommand:
    def test_doctor_exits_zero_when_all_pass(self) -> None:
        passing = [
            CheckResult("Python ≥ 3.11", ok=True, detail="3.12"),
            CheckResult("Container runtime", ok=True, detail="docker"),
            CheckResult("Free disk ≥ 8 GiB", ok=True, detail="20 GiB"),
            CheckResult("RAM ≥ 4 GiB", ok=True, detail="16 GiB"),
        ]
        with patch("cli.commands.doctor.run_all_checks", return_value=passing):
            result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "prerequisites satisfied" in result.output

    def test_doctor_exits_one_on_blocker(self) -> None:
        failing = [
            CheckResult(
                "Container runtime",
                ok=False,
                detail="not found",
                fix=("Install docker",),
            ),
        ]
        with patch("cli.commands.doctor.run_all_checks", return_value=failing):
            result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 1
        assert "Missing prerequisites" in result.output
        assert "Install docker" in result.output

    def test_doctor_json_output_shape(self) -> None:
        results = [
            CheckResult("Python ≥ 3.11", ok=True, detail="3.12"),
            CheckResult(
                "Container runtime",
                ok=False,
                detail="not found",
                fix=("install x",),
            ),
        ]
        with patch("cli.commands.doctor.run_all_checks", return_value=results):
            result = runner.invoke(app, ["doctor", "--json"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert len(payload["checks"]) == 2
        assert payload["checks"][1]["fix"] == ["install x"]


class TestRunAllChecks:
    def test_returns_all_four_checks(self) -> None:
        results = run_all_checks()
        names = {r.name for r in results}
        assert "Python ≥ 3.11" in names
        assert "Container runtime" in names
        assert any("disk" in n.lower() for n in names)
        assert any("ram" in n.lower() for n in names)
