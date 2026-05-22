"""Tests for the container-runtime diagnostic helpers (issue #467)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cli.commands import quickstart


class TestDockerHostSocketPath:
    def test_returns_path_for_unix_socket(self) -> None:
        with patch.dict("os.environ", {"DOCKER_HOST": "unix:///tmp/docker.sock"}, clear=False):
            assert quickstart._docker_host_socket_path() == "/tmp/docker.sock"

    def test_returns_none_for_tcp(self) -> None:
        with patch.dict("os.environ", {"DOCKER_HOST": "tcp://localhost:2375"}, clear=False):
            assert quickstart._docker_host_socket_path() is None

    def test_returns_none_when_unset(self) -> None:
        env = {k: v for k, v in __import__("os").environ.items() if k != "DOCKER_HOST"}
        with patch.dict("os.environ", env, clear=True):
            assert quickstart._docker_host_socket_path() is None


class TestDockerSocketCandidates:
    def test_includes_docker_host_first(self) -> None:
        with patch.dict("os.environ", {"DOCKER_HOST": "unix:///custom/sock"}, clear=False):
            candidates = quickstart._docker_socket_candidates()
        assert candidates[0] == "/custom/sock"

    def test_includes_xdg_runtime_dir_socket(self) -> None:
        with patch.dict("os.environ", {"XDG_RUNTIME_DIR": "/run/user/1000"}, clear=False):
            candidates = quickstart._docker_socket_candidates()
        assert "/run/user/1000/docker.sock" in candidates

    def test_deduplicates_paths(self) -> None:
        # Force a duplicate: $XDG_RUNTIME_DIR/docker.sock == /run/user/$UID/docker.sock
        with patch.dict(
            "os.environ",
            {"XDG_RUNTIME_DIR": "/run/user/1000", "UID": "1000"},
            clear=False,
        ):
            candidates = quickstart._docker_socket_candidates()
        assert candidates.count("/run/user/1000/docker.sock") == 1


class TestDockerSocketStatus:
    def test_returns_none_when_no_socket_anywhere(self) -> None:
        with patch("os.stat", side_effect=FileNotFoundError):
            assert quickstart._docker_socket_status() == (None, None)

    def test_reachable_socket(self) -> None:
        with patch("os.stat", return_value=MagicMock()):
            with patch("os.access", return_value=True):
                path, status = quickstart._docker_socket_status()
        assert status == "reachable"
        assert path is not None

    def test_permission_denied_socket(self) -> None:
        with patch("os.stat", return_value=MagicMock()):
            with patch("os.access", return_value=False):
                path, status = quickstart._docker_socket_status()
        assert status == "permission_denied"
        assert path is not None

    def test_permission_error_on_stat_returns_permission_denied(self) -> None:
        with patch("os.stat", side_effect=PermissionError):
            path, status = quickstart._docker_socket_status()
        assert status == "permission_denied"
        assert path is not None


class TestDockerIsRootless:
    def test_returns_false_when_docker_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            assert quickstart._docker_is_rootless() is False

    def test_returns_true_when_security_options_contain_rootless(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch(
                "subprocess.run",
                return_value=MagicMock(
                    returncode=0,
                    stdout="[name=seccomp,profile=builtin name=rootless]",
                ),
            ):
                assert quickstart._docker_is_rootless() is True

    def test_returns_false_when_security_options_lack_rootless(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch(
                "subprocess.run",
                return_value=MagicMock(returncode=0, stdout="[name=seccomp,profile=default]"),
            ):
                assert quickstart._docker_is_rootless() is False

    def test_returns_false_when_docker_info_fails(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
                assert quickstart._docker_is_rootless() is False


class TestDiagnoseMissingRuntime:
    def test_returns_none_when_no_socket(self) -> None:
        with patch.object(quickstart, "_docker_socket_status", return_value=(None, None)):
            assert quickstart._diagnose_missing_runtime() is None

    def test_partial_install_hint_when_socket_reachable_but_no_cli(self) -> None:
        with patch.object(
            quickstart,
            "_docker_socket_status",
            return_value=("/var/run/docker.sock", "reachable"),
        ):
            lines = quickstart._diagnose_missing_runtime()
        assert lines is not None
        joined = "\n".join(lines)
        assert "partial install" in joined.lower()
        assert "/var/run/docker.sock" in joined
        assert "docker-ce-cli" in joined or "brew install docker" in joined

    def test_permission_denied_hint(self) -> None:
        with patch.object(
            quickstart,
            "_docker_socket_status",
            return_value=("/var/run/docker.sock", "permission_denied"),
        ):
            lines = quickstart._diagnose_missing_runtime()
        assert lines is not None
        joined = "\n".join(lines)
        assert "permission denied" in joined.lower()
        assert "usermod -aG docker" in joined
        assert "newgrp docker" in joined


class TestDiagnoseRuntimeFailure:
    def test_stale_docker_host_takes_priority(self) -> None:
        with patch.dict("os.environ", {"DOCKER_HOST": "unix:///nonexistent.sock"}, clear=False):
            with patch.object(quickstart.Path, "exists", return_value=False):
                lines = quickstart._diagnose_runtime_failure("docker")
        joined = "\n".join(lines)
        assert "DOCKER_HOST" in joined
        assert "/nonexistent.sock" in joined
        assert "unset" in joined.lower()

    def test_permission_denied_branch(self) -> None:
        # No DOCKER_HOST → fall through to permission/socket logic.
        env = {k: v for k, v in __import__("os").environ.items() if k != "DOCKER_HOST"}
        with patch.dict("os.environ", env, clear=True):
            with patch.object(
                quickstart, "_docker_info_error", return_value="got permission denied"
            ):
                with patch.object(
                    quickstart,
                    "_docker_socket_status",
                    return_value=("/var/run/docker.sock", "permission_denied"),
                ):
                    lines = quickstart._diagnose_runtime_failure("docker")
        joined = "\n".join(lines)
        assert "permission denied" in joined.lower()
        assert "usermod -aG docker" in joined

    def test_socket_path_mismatch_branch(self) -> None:
        # Socket file is reachable but daemon-info call fails with "cannot connect"
        env = {k: v for k, v in __import__("os").environ.items() if k != "DOCKER_HOST"}
        with patch.dict("os.environ", env, clear=True):
            with patch.object(
                quickstart,
                "_docker_info_error",
                return_value="cannot connect to the docker daemon",
            ):
                with patch.object(
                    quickstart,
                    "_docker_socket_status",
                    return_value=("/run/user/1000/docker.sock", "reachable"),
                ):
                    lines = quickstart._diagnose_runtime_failure("docker")
        joined = "\n".join(lines)
        assert "socket path mismatch" in joined.lower()
        assert "/run/user/1000/docker.sock" in joined
        assert "DOCKER_HOST" in joined

    def test_docker_daemon_down_macos(self) -> None:
        env = {k: v for k, v in __import__("os").environ.items() if k != "DOCKER_HOST"}
        with patch.dict("os.environ", env, clear=True):
            with patch.object(quickstart, "_docker_info_error", return_value=""):
                with patch.object(quickstart, "_docker_socket_status", return_value=(None, None)):
                    with patch("platform.system", return_value="Darwin"):
                        lines = quickstart._diagnose_runtime_failure("docker")
        joined = "\n".join(lines)
        assert "Docker Desktop" in joined or "OrbStack" in joined

    def test_podman_daemon_down(self) -> None:
        lines = quickstart._diagnose_runtime_failure("podman")
        joined = "\n".join(lines)
        assert "podman machine start" in joined

    def test_unknown_binary_falls_back_generic(self) -> None:
        lines = quickstart._diagnose_runtime_failure("nerdctl")
        joined = "\n".join(lines)
        assert "nerdctl" in joined
