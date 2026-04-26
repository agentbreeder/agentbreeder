"""Tests for BaseDeployer helper methods."""

from __future__ import annotations

import os
from unittest.mock import patch

from engine.deployers.docker_compose import DockerComposeDeployer


class TestGetApsEnvVars:
    def test_returns_both_vars(self) -> None:
        deployer = DockerComposeDeployer.__new__(DockerComposeDeployer)
        with patch.dict(
            os.environ,
            {
                "AGENTBREEDER_URL": "http://api:8000",
                "AGENTBREEDER_API_KEY": "test-key-123",
            },
        ):
            result = deployer.get_aps_env_vars()
        assert result["AGENTBREEDER_URL"] == "http://api:8000"
        assert result["AGENTBREEDER_API_KEY"] == "test-key-123"

    def test_falls_back_to_defaults_when_env_unset(self) -> None:
        deployer = DockerComposeDeployer.__new__(DockerComposeDeployer)
        env_without_aps = {
            k: v
            for k, v in os.environ.items()
            if k not in ("AGENTBREEDER_URL", "AGENTBREEDER_API_KEY")
        }
        with patch.dict(os.environ, env_without_aps, clear=True):
            result = deployer.get_aps_env_vars()
        assert result["AGENTBREEDER_URL"] == "http://agentbreeder-api:8000"
        assert result["AGENTBREEDER_API_KEY"] == ""
