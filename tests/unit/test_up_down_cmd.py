"""Tests for agentbreeder up and agentbreeder down CLI commands."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from cli.main import app

runner = CliRunner()


# ── Helpers ──────────────────────────────────────────────────────────


def _mock_subprocess_ok(*args, **kwargs):
    """Return a successful subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")


def _mock_subprocess_fail(*args, **kwargs):
    """Return a failed subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")


def _mock_subprocess_git_root(root: str):
    """Return a subprocess mock that returns a git root path."""

    def _run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd and "rev-parse" in cmd:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=root + "\n",
                stderr="",
            )
        return _mock_subprocess_ok(*args, **kwargs)

    return _run


# ── TestFindComposeDir ───────────────────────────────────────────────


class TestFindComposeDir:
    """Tests for _find_compose_dir() helper."""

    def test_finds_compose_from_cwd(self, tmp_path: Path) -> None:
        """Should find deploy/docker-compose.yml from cwd."""
        from cli.commands.up import _find_compose_dir

        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        with patch("cli.commands.up.Path") as mock_path:
            mock_path.cwd.return_value = tmp_path
            mock_path.home.return_value = Path.home()
            # Make Path(x) / y work normally for everything else
            mock_path.side_effect = Path
            mock_path.cwd.return_value = tmp_path
            # We need to patch Path.cwd specifically
        with patch("cli.commands.up.Path.cwd", return_value=tmp_path):
            result = _find_compose_dir()

        assert result is not None
        assert result == deploy

    def test_finds_compose_from_parent(self, tmp_path: Path) -> None:
        """Should find deploy/docker-compose.yml from parent of cwd."""
        from cli.commands.up import _find_compose_dir

        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        subdir = tmp_path / "subdir"
        subdir.mkdir()

        with patch("cli.commands.up.Path.cwd", return_value=subdir):
            result = _find_compose_dir()

        assert result is not None
        assert result == deploy

    def test_finds_compose_from_git_root(self, tmp_path: Path) -> None:
        """Should find compose via git rev-parse when cwd doesn't have it."""
        from cli.commands.up import _find_compose_dir

        git_root = tmp_path / "repo"
        git_root.mkdir()
        deploy = git_root / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        # cwd is somewhere else entirely
        other = tmp_path / "other"
        other.mkdir()

        with (
            patch("cli.commands.up.Path.cwd", return_value=other),
            patch(
                "cli.commands.up.subprocess.run",
                side_effect=_mock_subprocess_git_root(str(git_root)),
            ),
        ):
            result = _find_compose_dir()

        assert result is not None
        assert result == deploy

    def test_finds_compose_from_bundled(self, tmp_path: Path) -> None:
        """Should find compose in _bundled directory."""
        import cli.commands.up as up_mod

        other = tmp_path / "other"
        other.mkdir()

        # Create a fake _bundled dir and swap __file__ on the module
        # _find_compose_dir does: Path(__file__).parent.parent / "_bundled"
        # So if __file__ = .../cli/commands/up.py, _bundled = .../cli/_bundled
        fake_commands = tmp_path / "cli" / "commands"
        fake_commands.mkdir(parents=True)
        bundled = tmp_path / "cli" / "_bundled"
        bundled.mkdir()
        (bundled / "docker-compose.yml").write_text("version: '3'\n")
        fake_module_file = str(fake_commands / "up.py")

        original_file = up_mod.__file__
        try:
            up_mod.__file__ = fake_module_file
            with (
                patch("cli.commands.up.Path.cwd", return_value=other),
                patch(
                    "cli.commands.up.subprocess.run",
                    side_effect=_mock_subprocess_fail,
                ),
            ):
                result = up_mod._find_compose_dir()
        finally:
            up_mod.__file__ = original_file

        assert result is not None
        assert result == bundled

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """Should return None when no compose file is found."""
        from cli.commands.up import _find_compose_dir

        with (
            patch("cli.commands.up.Path.cwd", return_value=tmp_path),
            patch(
                "cli.commands.up.subprocess.run",
                side_effect=_mock_subprocess_fail,
            ),
        ):
            result = _find_compose_dir()

        assert result is None

    def test_returns_none_when_git_raises(self, tmp_path: Path) -> None:
        """Should handle git subprocess exceptions gracefully."""
        from cli.commands.up import _find_compose_dir

        def _raise(*a, **kw):
            raise OSError("git not found")

        with (
            patch("cli.commands.up.Path.cwd", return_value=tmp_path),
            patch("cli.commands.up.subprocess.run", side_effect=_raise),
        ):
            result = _find_compose_dir()

        assert result is None


# ── TestCheckDocker ──────────────────────────────────────────────────


class TestCheckDocker:
    """Tests for _check_docker() helper."""

    def test_docker_not_installed(self) -> None:
        """Should return False when docker binary is not on PATH."""
        from cli.commands.up import _check_docker

        with patch("cli.commands.up.shutil.which", return_value=None):
            assert _check_docker() is False

    def test_docker_compose_not_found(self) -> None:
        """Should return False when 'docker compose version' fails."""
        from cli.commands.up import _check_docker

        with (
            patch("cli.commands.up.shutil.which", return_value="/usr/bin/docker"),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=1,
                    stdout="",
                    stderr="",
                ),
            ),
        ):
            assert _check_docker() is False

    def test_docker_daemon_not_running(self) -> None:
        """Should return False when 'docker info' fails."""
        from cli.commands.up import _check_docker

        call_count = 0

        def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            cmd = args[0] if args else kwargs.get("args", [])
            if "version" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="",
                    stderr="",
                )
            # docker info fails
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="",
            )

        with (
            patch("cli.commands.up.shutil.which", return_value="/usr/bin/docker"),
            patch("cli.commands.up.subprocess.run", side_effect=_side_effect),
        ):
            assert _check_docker() is False

    def test_all_checks_pass(self) -> None:
        """Should return True when everything is fine."""
        from cli.commands.up import _check_docker

        with (
            patch("cli.commands.up.shutil.which", return_value="/usr/bin/docker"),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="",
                    stderr="",
                ),
            ),
        ):
            assert _check_docker() is True

    def test_docker_compose_called_after_which(self) -> None:
        """Should only call compose version after confirming docker exists."""
        from cli.commands.up import _check_docker

        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="",
                stderr="",
            ),
        )

        with (
            patch("cli.commands.up.shutil.which", return_value="/usr/bin/docker"),
            patch("cli.commands.up.subprocess.run", mock_run),
        ):
            _check_docker()

        # First call should be compose version, second docker info
        calls = mock_run.call_args_list
        assert len(calls) == 2
        assert "compose" in calls[0][0][0]
        assert "info" in calls[1][0][0]


# ── TestGenerateEnv ──────────────────────────────────────────────────


class TestGenerateEnv:
    """Tests for _generate_env() helper."""

    def test_generates_env_with_secure_keys(self, tmp_path: Path) -> None:
        """Generated .env should contain unique SECRET_KEY and JWT_SECRET_KEY."""
        from cli.commands.up import _generate_env

        env_path = tmp_path / ".env"
        _generate_env(env_path, interactive=False)

        content = env_path.read_text()
        assert "SECRET_KEY=" in content
        assert "JWT_SECRET_KEY=" in content

        # Keys should be non-empty hex strings
        for line in content.splitlines():
            if line.startswith("SECRET_KEY="):
                val = line.split("=", 1)[1]
                assert len(val) == 64  # 32 bytes hex
            if line.startswith("JWT_SECRET_KEY="):
                val = line.split("=", 1)[1]
                assert len(val) == 64

    def test_non_interactive_skips_prompts(self, tmp_path: Path) -> None:
        """Non-interactive mode should not call console.input."""
        from cli.commands.up import _generate_env

        env_path = tmp_path / ".env"
        with patch("cli.commands.up.console") as mock_console:
            _generate_env(env_path, interactive=False)

        mock_console.input.assert_not_called()
        assert env_path.exists()

    def test_all_required_vars_present(self, tmp_path: Path) -> None:
        """All required infrastructure variables should be in the .env."""
        from cli.commands.up import _generate_env

        env_path = tmp_path / ".env"
        _generate_env(env_path, interactive=False)

        content = env_path.read_text()
        required = [
            "DATABASE_URL",
            "REDIS_URL",
            "SECRET_KEY",
            "JWT_SECRET_KEY",
            "JWT_ALGORITHM",
            "ACCESS_TOKEN_EXPIRE_MINUTES",
            "GARDEN_ENV",
            "LITELLM_BASE_URL",
            "LITELLM_MASTER_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "GOOGLE_AI_API_KEY",
        ]
        for var in required:
            assert f"{var}=" in content, f"Missing {var} in generated .env"

    def test_interactive_mode_prompts_for_keys(
        self,
        tmp_path: Path,
    ) -> None:
        """Interactive mode should prompt for API keys."""
        from cli.commands.up import _generate_env

        env_path = tmp_path / ".env"
        with patch("cli.commands.up.console") as mock_console:
            mock_console.input.return_value = ""
            _generate_env(env_path, interactive=True)

        # Should have prompted 3 times (OpenAI, Anthropic, Google)
        assert mock_console.input.call_count == 3

    def test_interactive_mode_writes_provided_keys(
        self,
        tmp_path: Path,
    ) -> None:
        """User-provided keys should appear in the .env file."""
        from cli.commands.up import _generate_env

        env_path = tmp_path / ".env"

        call_num = 0
        keys = ["sk-test-openai-key", "", "AI-google-key"]

        def _fake_input(prompt):
            nonlocal call_num
            val = keys[call_num]
            call_num += 1
            return val

        with patch("cli.commands.up.console") as mock_console:
            mock_console.input.side_effect = _fake_input
            _generate_env(env_path, interactive=True)

        content = env_path.read_text()
        assert "OPENAI_API_KEY=sk-test-openai-key" in content
        assert "ANTHROPIC_API_KEY=" in content
        # Anthropic should be empty
        for line in content.splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                assert line == "ANTHROPIC_API_KEY="
        assert "GOOGLE_AI_API_KEY=AI-google-key" in content

    def test_generates_unique_keys_each_time(self, tmp_path: Path) -> None:
        """Two calls should produce different SECRET_KEYs."""
        from cli.commands.up import _generate_env

        env1 = tmp_path / ".env1"
        env2 = tmp_path / ".env2"
        _generate_env(env1, interactive=False)
        _generate_env(env2, interactive=False)

        def _get_key(path, var):
            for line in path.read_text().splitlines():
                if line.startswith(f"{var}="):
                    return line.split("=", 1)[1]
            return None

        assert _get_key(env1, "SECRET_KEY") != _get_key(env2, "SECRET_KEY")


# ── TestUpCommand ────────────────────────────────────────────────────


class TestUpCommand:
    """Tests for the full 'agentbreeder up' CLI command."""

    def test_docker_not_found_exits_1(self) -> None:
        """Should exit 1 when Docker is not installed."""
        with patch(
            "cli.commands.up._check_docker",
            return_value=False,
        ):
            result = runner.invoke(app, ["up", "--no-input", "--no-browser"])

        assert result.exit_code == 1

    def test_compose_not_found_exits_1(self) -> None:
        """Should exit 1 when docker-compose.yml is not found."""
        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch("cli.commands.up._find_compose_dir", return_value=None),
        ):
            result = runner.invoke(app, ["up", "--no-input", "--no-browser"])

        assert result.exit_code == 1

    def test_successful_run(self, tmp_path: Path) -> None:
        """Should exit 0 on a successful start."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        # Create .env so it doesn't try to generate
        (tmp_path / ".env").write_text("SECRET_KEY=test\n")

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
            patch(
                "cli.commands.up._wait_for_health",
                return_value=True,
            ),
            patch("cli.commands.up.webbrowser.open"),
        ):
            result = runner.invoke(
                app,
                ["up", "--no-input", "--no-browser"],
            )

        assert result.exit_code == 0
        assert "running" in result.output.lower()

    def test_existing_env_file_used(self, tmp_path: Path) -> None:
        """Should use existing .env file without regenerating."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        env_file = tmp_path / ".env"
        env_file.write_text("SECRET_KEY=existing\n")

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
            patch(
                "cli.commands.up._wait_for_health",
                return_value=True,
            ),
            patch("cli.commands.up.webbrowser.open"),
            patch(
                "cli.commands.up._generate_env",
            ) as mock_gen,
        ):
            result = runner.invoke(
                app,
                ["up", "--no-input", "--no-browser"],
            )

        assert result.exit_code == 0
        mock_gen.assert_not_called()
        assert "existing" in result.output.lower() or "Using" in result.output

    def test_env_file_option_nonexistent_exits_1(
        self,
        tmp_path: Path,
    ) -> None:
        """--env-file pointing to missing file should exit 1."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        missing = tmp_path / "nonexistent.env"

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
        ):
            result = runner.invoke(
                app,
                [
                    "up",
                    "--no-input",
                    "--no-browser",
                    "--env-file",
                    str(missing),
                ],
            )

        assert result.exit_code == 1

    def test_env_file_option_existing_is_used(
        self,
        tmp_path: Path,
    ) -> None:
        """--env-file with valid path should use that file."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        custom_env = tmp_path / "custom.env"
        custom_env.write_text("SECRET_KEY=custom\n")

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
            patch(
                "cli.commands.up._wait_for_health",
                return_value=True,
            ),
            patch("cli.commands.up.webbrowser.open"),
        ):
            result = runner.invoke(
                app,
                [
                    "up",
                    "--no-input",
                    "--no-browser",
                    "--env-file",
                    str(custom_env),
                ],
            )

        assert result.exit_code == 0

    def test_docker_compose_up_fails_exits_1(
        self,
        tmp_path: Path,
    ) -> None:
        """Should exit 1 when docker compose up returns non-zero."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        (tmp_path / ".env").write_text("SECRET_KEY=test\n")

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=1,
                ),
            ),
        ):
            result = runner.invoke(
                app,
                ["up", "--no-input", "--no-browser"],
            )

        assert result.exit_code == 1

    def test_no_browser_flag_prevents_open(
        self,
        tmp_path: Path,
    ) -> None:
        """--no-browser should prevent webbrowser.open from being called."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        (tmp_path / ".env").write_text("SECRET_KEY=test\n")

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
            patch(
                "cli.commands.up._wait_for_health",
                return_value=True,
            ),
            patch("cli.commands.up.webbrowser.open") as mock_browser,
        ):
            result = runner.invoke(
                app,
                ["up", "--no-input", "--no-browser"],
            )

        assert result.exit_code == 0
        mock_browser.assert_not_called()

    def test_browser_opens_when_healthy_and_flag_not_set(
        self,
        tmp_path: Path,
    ) -> None:
        """Without --no-browser, should open browser when dashboard is healthy."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        (tmp_path / ".env").write_text("SECRET_KEY=test\n")

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
            patch(
                "cli.commands.up._wait_for_health",
                return_value=True,
            ),
            patch("cli.commands.up.webbrowser.open") as mock_browser,
        ):
            result = runner.invoke(app, ["up", "--no-input"])

        assert result.exit_code == 0
        mock_browser.assert_called_once_with("http://localhost:3001")

    def test_no_build_flag_omits_build_arg(
        self,
        tmp_path: Path,
    ) -> None:
        """--no-build should not include --build in the docker command."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        (tmp_path / ".env").write_text("SECRET_KEY=test\n")

        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
            ),
        )

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch("cli.commands.up.subprocess.run", mock_run),
            patch(
                "cli.commands.up._wait_for_health",
                return_value=True,
            ),
            patch("cli.commands.up.webbrowser.open"),
        ):
            result = runner.invoke(
                app,
                ["up", "--no-input", "--no-browser", "--no-build"],
            )

        assert result.exit_code == 0
        # Inspect the command passed to subprocess.run
        cmd = mock_run.call_args[0][0]
        assert "--build" not in cmd

    def test_build_flag_includes_build_arg(
        self,
        tmp_path: Path,
    ) -> None:
        """Default --build should include --build in the docker command."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        (tmp_path / ".env").write_text("SECRET_KEY=test\n")

        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
            ),
        )

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch("cli.commands.up.subprocess.run", mock_run),
            patch(
                "cli.commands.up._wait_for_health",
                return_value=True,
            ),
            patch("cli.commands.up.webbrowser.open"),
        ):
            result = runner.invoke(
                app,
                ["up", "--no-input", "--no-browser"],
            )

        assert result.exit_code == 0
        cmd = mock_run.call_args[0][0]
        assert "--build" in cmd

    def test_health_check_failure_still_exits_0(
        self,
        tmp_path: Path,
    ) -> None:
        """Health check timeout should warn but not fail the command."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        (tmp_path / ".env").write_text("SECRET_KEY=test\n")

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
            patch(
                "cli.commands.up._wait_for_health",
                return_value=False,
            ),
            patch("cli.commands.up.webbrowser.open"),
        ):
            result = runner.invoke(
                app,
                ["up", "--no-input", "--no-browser"],
            )

        assert result.exit_code == 0

    def test_generates_env_when_missing(self, tmp_path: Path) -> None:
        """Should generate .env when no .env exists and no --env-file."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")
        # Do NOT create .env

        with (
            patch("cli.commands.up._check_docker", return_value=True),
            patch(
                "cli.commands.up._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.up.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
            patch(
                "cli.commands.up._wait_for_health",
                return_value=True,
            ),
            patch("cli.commands.up.webbrowser.open"),
        ):
            result = runner.invoke(
                app,
                ["up", "--no-input", "--no-browser"],
            )

        assert result.exit_code == 0
        assert (tmp_path / ".env").exists()


# ── TestWaitForHealth ────────────────────────────────────────────────


class TestWaitForHealth:
    """Tests for _wait_for_health() helper."""

    def test_returns_true_on_200(self) -> None:
        """Should return True immediately when endpoint returns 200."""
        from cli.commands.up import _wait_for_health

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        import httpx as _httpx

        with patch.object(_httpx, "get", return_value=mock_resp):
            result = _wait_for_health(
                "http://localhost:8000/health",
                "API",
                timeout=5,
            )
        assert result is True

    def test_returns_false_on_timeout(self) -> None:
        """Should return False when endpoint never responds."""
        import httpx as _httpx

        from cli.commands.up import _wait_for_health

        with (
            patch.object(
                _httpx,
                "get",
                side_effect=_httpx.ConnectError("refused"),
            ),
            patch("cli.commands.up.time.sleep"),
        ):
            result = _wait_for_health(
                "http://localhost:8000/health",
                "API",
                timeout=0,
            )

        assert result is False

    def test_retries_until_success(self) -> None:
        """Should retry on connection error then succeed."""
        import httpx as _httpx

        from cli.commands.up import _wait_for_health

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        call_count = 0

        def _side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise _httpx.ConnectError("refused")
            return mock_resp

        with (
            patch.object(_httpx, "get", side_effect=_side_effect),
            patch("cli.commands.up.time.sleep"),
        ):
            result = _wait_for_health(
                "http://localhost:8000/health",
                "API",
                timeout=30,
            )

        assert result is True
        assert call_count == 3


# ── TestDownCommand ──────────────────────────────────────────────────


class TestDownCommand:
    """Tests for the 'agentbreeder down' CLI command."""

    def test_compose_dir_not_found_exits_1(self) -> None:
        """Should exit 1 when compose dir is not found."""
        with patch(
            "cli.commands.down._find_compose_dir",
            return_value=None,
        ):
            result = runner.invoke(app, ["down"])

        assert result.exit_code == 1

    def test_successful_stop(self, tmp_path: Path) -> None:
        """Should exit 0 on successful docker compose down."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        with (
            patch(
                "cli.commands.down._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.down.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
        ):
            result = runner.invoke(app, ["down"])

        assert result.exit_code == 0
        assert "stopped" in result.output.lower()

    def test_clean_flag_passes_volumes(self, tmp_path: Path) -> None:
        """--clean should include --volumes in the docker command."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
            ),
        )

        with (
            patch(
                "cli.commands.down._find_compose_dir",
                return_value=deploy,
            ),
            patch("cli.commands.down.subprocess.run", mock_run),
        ):
            result = runner.invoke(app, ["down", "--clean"])

        assert result.exit_code == 0
        cmd = mock_run.call_args[0][0]
        assert "--volumes" in cmd

    def test_no_clean_omits_volumes(self, tmp_path: Path) -> None:
        """Without --clean, --volumes should not appear."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
            ),
        )

        with (
            patch(
                "cli.commands.down._find_compose_dir",
                return_value=deploy,
            ),
            patch("cli.commands.down.subprocess.run", mock_run),
        ):
            result = runner.invoke(app, ["down"])

        assert result.exit_code == 0
        cmd = mock_run.call_args[0][0]
        assert "--volumes" not in cmd

    def test_json_output_on_success(self, tmp_path: Path) -> None:
        """--json should produce JSON output on success."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        with (
            patch(
                "cli.commands.down._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.down.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
        ):
            result = runner.invoke(app, ["down", "--json"])

        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["status"] == "stopped"
        assert output["clean"] is False

    def test_json_output_with_clean(self, tmp_path: Path) -> None:
        """--json --clean should show clean: true in output."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        with (
            patch(
                "cli.commands.down._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.down.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
        ):
            result = runner.invoke(app, ["down", "--json", "--clean"])

        assert result.exit_code == 0
        output = json.loads(result.output.strip())
        assert output["status"] == "stopped"
        assert output["clean"] is True

    def test_json_output_on_failure(self, tmp_path: Path) -> None:
        """--json should produce JSON error output on failure."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        with (
            patch(
                "cli.commands.down._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.down.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=1,
                ),
            ),
        ):
            result = runner.invoke(app, ["down", "--json"])

        assert result.exit_code == 1
        output = json.loads(result.output.strip())
        assert output["status"] == "error"

    def test_failed_stop_exits_1(self, tmp_path: Path) -> None:
        """Should exit 1 when docker compose down fails."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        with (
            patch(
                "cli.commands.down._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.down.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=1,
                ),
            ),
        ):
            result = runner.invoke(app, ["down"])

        assert result.exit_code == 1

    def test_clean_shows_volumes_removed_message(
        self,
        tmp_path: Path,
    ) -> None:
        """--clean should mention volumes removal in output."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        with (
            patch(
                "cli.commands.down._find_compose_dir",
                return_value=deploy,
            ),
            patch(
                "cli.commands.down.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                ),
            ),
        ):
            result = runner.invoke(app, ["down", "--clean"])

        assert result.exit_code == 0
        low = result.output.lower()
        assert "volume" in low or "deleted" in low

    def test_down_uses_correct_compose_file(
        self,
        tmp_path: Path,
    ) -> None:
        """Should pass the correct -f flag to docker compose."""
        deploy = tmp_path / "deploy"
        deploy.mkdir()
        (deploy / "docker-compose.yml").write_text("version: '3'\n")

        mock_run = MagicMock(
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
            ),
        )

        with (
            patch(
                "cli.commands.down._find_compose_dir",
                return_value=deploy,
            ),
            patch("cli.commands.down.subprocess.run", mock_run),
        ):
            runner.invoke(app, ["down"])

        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        compose_idx = cmd.index("-f")
        assert str(deploy / "docker-compose.yml") == cmd[compose_idx + 1]
