"""GCP + Azure deployer parity with AWS ECS (epic #505, #533).

These tests lock the deployer-level capabilities that AWS ECS gained during its
end-to-end hardening (#532) onto GCP Cloud Run and Azure Container Apps:

- agent-facing ``AGENTBREEDER_MCP_SERVERS`` env so ``agenthub.mcp.load_mcp_tools``
  can discover co-deployed MCP servers (#533),
- sidecar boot-auth env so the sidecar starts (GCP),
- CPU/memory normalization so the documented ``agent.yaml`` resource notation
  (vCPU + Gi/Mi/G/M/raw) renders into each platform's accepted form.
"""

from __future__ import annotations

import json

import pytest

from engine.config_parser import (
    AccessConfig,
    AgentConfig,
    CloudType,
    DeployConfig,
    FrameworkType,
    McpServerRef,
    ModelConfig,
    ResourceConfig,
    ScalingConfig,
)

SIDECAR = "agentbreeder-sidecar"


def _config(
    *,
    cloud: CloudType,
    runtime: str,
    env_vars: dict[str, str],
    cpu: str = "1",
    memory: str = "1Gi",
    guardrails: bool = False,
    mcp: bool = False,
) -> AgentConfig:
    config = AgentConfig(
        name="my-agent",
        version="1.0.0",
        team="engineering",
        owner="dev@example.com",
        framework=FrameworkType.langgraph,
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(
            cloud=cloud,
            runtime=runtime,
            region=env_vars.get("AZURE_LOCATION", "us-central1"),
            env_vars=env_vars,
            resources=ResourceConfig(cpu=cpu, memory=memory),
            scaling=ScalingConfig(min=1, max=3),
        ),
        access=AccessConfig(),
    )
    if guardrails:
        config.guardrails = ["pii_detection"]
    if mcp:
        config.mcp_servers = [
            McpServerRef(
                ref="mcp/example-tools",
                transport="streamable_http",
                url="http://remote/mcp",
            )
        ]
    return config


# --------------------------------------------------------------------------- #
# GCP Cloud Run
# --------------------------------------------------------------------------- #


def _gcp_template(config: AgentConfig):
    from engine.deployers.gcp_cloudrun import (
        _build_service_template,
        _extract_cloudrun_config,
    )

    gcp = _extract_cloudrun_config(config)
    return _build_service_template(config, gcp, "img:1.0.0")


def _agent_env(template) -> dict[str, str]:
    containers = template["containers"]
    agent = next(c for c in containers if c.get("name") != SIDECAR)
    return {e["name"]: e.get("value") for e in agent["env"] if "value" in e}


class TestGcpMcpEnvInjection:
    def test_agent_env_carries_mcp_servers_map(self) -> None:
        config = _config(
            cloud=CloudType.gcp,
            runtime="cloud-run",
            env_vars={"GCP_PROJECT_ID": "my-project-123"},
            mcp=True,
        )
        env = _agent_env(_gcp_template(config))
        assert "AGENTBREEDER_MCP_SERVERS" in env
        parsed = json.loads(env["AGENTBREEDER_MCP_SERVERS"])
        assert "example-tools" in parsed
        assert parsed["example-tools"]["url"] == "http://remote/mcp"

    def test_no_mcp_env_when_no_servers(self) -> None:
        config = _config(
            cloud=CloudType.gcp,
            runtime="cloud-run",
            env_vars={"GCP_PROJECT_ID": "my-project-123"},
        )
        env = _agent_env(_gcp_template(config))
        assert "AGENTBREEDER_MCP_SERVERS" not in env


class TestGcpSidecarAuth:
    def test_allow_no_auth_when_no_token(self) -> None:
        from engine.deployers.gcp_cloudrun import _build_cloudrun_sidecar_container

        config = _config(
            cloud=CloudType.gcp,
            runtime="cloud-run",
            env_vars={"GCP_PROJECT_ID": "p"},
            guardrails=True,
        )
        env = {e["name"]: e.get("value") for e in _build_cloudrun_sidecar_container(config)["env"]}
        assert env.get("AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH") == "1"
        assert "AGENT_AUTH_TOKEN" not in env

    def test_forwards_configured_auth_token(self) -> None:
        from engine.deployers.gcp_cloudrun import _build_cloudrun_sidecar_container

        config = _config(
            cloud=CloudType.gcp,
            runtime="cloud-run",
            env_vars={"GCP_PROJECT_ID": "p", "AGENT_AUTH_TOKEN": "s3cr3t"},
            guardrails=True,
        )
        env = {e["name"]: e.get("value") for e in _build_cloudrun_sidecar_container(config)["env"]}
        assert env.get("AGENT_AUTH_TOKEN") == "s3cr3t"
        assert "AGENTBREEDER_SIDECAR_ALLOW_NO_AUTH" not in env


class TestGcpResourceNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1", "1"),
            ("2", "2"),
            ("0.5", "1000m"),  # clamped to Cloud Run minimum for concurrency
            ("500m", "1000m"),
            ("2000m", "2"),
            ("1024", "1"),  # AWS-style units → vCPU
        ],
    )
    def test_cpu_normalizer(self, raw: str, expected: str) -> None:
        from engine.deployers.gcp_cloudrun import _normalize_cloudrun_cpu

        assert _normalize_cloudrun_cpu(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1Gi", "1Gi"),
            ("2Gi", "2Gi"),
            ("2G", "2Gi"),
            ("512Mi", "512Mi"),
            ("512M", "512Mi"),
            ("1024", "1024Mi"),
        ],
    )
    def test_memory_normalizer(self, raw: str, expected: str) -> None:
        from engine.deployers.gcp_cloudrun import _normalize_cloudrun_memory

        assert _normalize_cloudrun_memory(raw) == expected

    def test_template_applies_normalized_resources(self) -> None:
        config = _config(
            cloud=CloudType.gcp,
            runtime="cloud-run",
            env_vars={"GCP_PROJECT_ID": "p"},
            cpu="2",
            memory="2G",
        )
        limits = _gcp_template(config)["containers"][0]["resources"]["limits"]
        assert limits["cpu"] == "2"
        assert limits["memory"] == "2Gi"


# --------------------------------------------------------------------------- #
# Azure Container Apps
# --------------------------------------------------------------------------- #


def _azure_body(config: AgentConfig):
    from engine.deployers.azure_container_apps import (
        AzureContainerAppsDeployer,
        _extract_azure_config,
    )

    azure = _extract_azure_config(config)
    return AzureContainerAppsDeployer()._build_container_app_body(
        config, azure, "img:1.0.0", "env-id"
    )


def _azure_env_vars(body: dict) -> dict[str, str]:
    containers = body["properties"]["template"]["containers"]
    agent = next(c for c in containers if c.get("name") != SIDECAR)
    return {e["name"]: e.get("value") for e in agent["env"] if "value" in e}


_AZURE_ENV = {
    "AZURE_SUBSCRIPTION_ID": "sub-1234",
    "AZURE_RESOURCE_GROUP": "rg-agents",
    "AZURE_CONTAINER_APPS_ENV": "aca-env-prod",
    "AZURE_REGISTRY_SERVER": "myregistry.azurecr.io",
    "AZURE_LOCATION": "eastus",
}


class TestAzureMcpEnvInjection:
    def test_agent_env_carries_mcp_servers_map(self) -> None:
        config = _config(
            cloud=CloudType.azure,
            runtime="container-apps",
            env_vars=_AZURE_ENV,
            mcp=True,
        )
        env = _azure_env_vars(_azure_body(config))
        assert "AGENTBREEDER_MCP_SERVERS" in env
        parsed = json.loads(env["AGENTBREEDER_MCP_SERVERS"])
        assert parsed["example-tools"]["url"] == "http://remote/mcp"

    def test_no_mcp_env_when_no_servers(self) -> None:
        config = _config(
            cloud=CloudType.azure,
            runtime="container-apps",
            env_vars=_AZURE_ENV,
        )
        env = _azure_env_vars(_azure_body(config))
        assert "AGENTBREEDER_MCP_SERVERS" not in env


class TestAzureResourceNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1", 1.0), ("0.5", 0.5), ("500m", 0.5), ("2", 2.0)],
    )
    def test_cpu_normalizer(self, raw: str, expected: float) -> None:
        from engine.deployers.azure_container_apps import _normalize_aca_cpu

        assert _normalize_aca_cpu(raw) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("1Gi", "1Gi"), ("2G", "2Gi"), ("512Mi", "0.5Gi"), ("2048", "2Gi")],
    )
    def test_memory_normalizer(self, raw: str, expected: str) -> None:
        from engine.deployers.azure_container_apps import _normalize_aca_memory

        assert _normalize_aca_memory(raw) == expected

    def test_body_applies_normalized_resources(self) -> None:
        config = _config(
            cloud=CloudType.azure,
            runtime="container-apps",
            env_vars=_AZURE_ENV,
            cpu="500m",
            memory="2G",
        )
        containers = _azure_body(config)["properties"]["template"]["containers"]
        agent = next(c for c in containers if c.get("name") != SIDECAR)
        assert float(agent["resources"]["cpu"]) == pytest.approx(0.5)
        assert agent["resources"]["memory"] == "2Gi"
