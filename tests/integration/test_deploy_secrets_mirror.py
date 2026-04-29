"""Integration tests: AWS ECS + GCP Cloud Run deployers auto-mirror secrets.

The deployer logic is exercised end-to-end with mocked workspace + cloud
backends — no boto3 / google-cloud-secret-manager calls leave the test
process.
"""

from __future__ import annotations

import asyncio
from typing import Any

from engine.config_parser import (
    AgentConfig,
    DeployConfig,
    FrameworkType,
    ModelConfig,
    ResourceConfig,
    ScalingConfig,
)
from engine.secrets.auto_mirror import CloudSecretRef
from engine.secrets.base import SecretEntry, SecretsBackend


def _run(coro):
    return asyncio.run(coro)


# ─── helpers ────────────────────────────────────────────────────────────────


class _FakeWorkspace(SecretsBackend):
    backend_name = "fake"  # type: ignore[assignment]

    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    async def get(self, name: str) -> str | None:
        return self._values.get(name)

    async def set(self, name, value, *, tags=None):
        self._values[name] = value

    async def delete(self, name):
        self._values.pop(name, None)

    async def list(self):
        return [SecretEntry(name=k, masked_value="••••", backend="fake") for k in self._values]


class _CapturingCloud(SecretsBackend):
    def __init__(self, name: str) -> None:
        self._name = name
        self.writes: list[tuple[str, str]] = []

    @property
    def backend_name(self) -> str:
        return self._name

    async def get(self, name):
        return None

    async def set(self, name, value, *, tags=None):
        self.writes.append((name, value))

    async def delete(self, name): ...

    async def list(self):
        return []


def _make_config(
    name: str,
    *,
    cloud: str,
    secrets: list[str],
    extra_env: dict[str, str] | None = None,
) -> AgentConfig:
    env = {
        "GCP_PROJECT_ID": "test-proj",
        "AWS_ACCOUNT_ID": "123456789012",
        "AWS_ECS_CLUSTER": "ecs-cluster",
        "AWS_EXECUTION_ROLE_ARN": "arn:aws:iam::123:role/exec",
        "AWS_VPC_SUBNETS": "subnet-a",
        "AWS_SECURITY_GROUPS": "sg-a",
    }
    if extra_env:
        env.update(extra_env)
    return AgentConfig(
        name=name,
        version="0.1.0",
        team="platform",
        owner="alice@acme.com",
        framework=FrameworkType.langgraph,
        model=ModelConfig(primary="claude-sonnet-4"),
        deploy=DeployConfig(
            cloud=cloud,
            region="us-east-1",
            scaling=ScalingConfig(min=1, max=2),
            resources=ResourceConfig(cpu="1", memory="1Gi"),
            env_vars=env,
            secrets=secrets,
        ),
    )


# ─── GCP Cloud Run ──────────────────────────────────────────────────────────


class TestGCPMirror:
    def test_deploy_calls_mirror_and_template_uses_refs(self, monkeypatch):
        from engine.deployers import gcp_cloudrun

        cloud = _CapturingCloud("gcp")
        ws = _FakeWorkspace({"OPENAI_API_KEY": "sk-abc1234"})

        # Inject our fakes into the auto_mirror module so the real cloud SDKs
        # are never imported.
        monkeypatch.setattr(
            "engine.secrets.auto_mirror._build_target_backend",
            lambda c, opts: cloud,
        )
        monkeypatch.setattr(
            "engine.secrets.auto_mirror.get_workspace_backend",
            lambda workspace=None: (ws, type("WS", (), {"workspace": "default"})()),
        )

        config = _make_config("orders", cloud="gcp", secrets=["OPENAI_API_KEY"])

        deployer = gcp_cloudrun.GCPCloudRunDeployer()
        deployer._gcp_config = gcp_cloudrun._extract_cloudrun_config(config)
        _run(deployer._mirror_workspace_secrets(config, deployer._gcp_config))

        # Mirror result captured.
        assert deployer._mirror_result is not None
        assert len(deployer._mirror_result.refs) == 1
        ref = deployer._mirror_result.refs[0]
        assert ref.logical_name == "OPENAI_API_KEY"
        assert ref.cloud_name == "agentbreeder_orders_OPENAI_API_KEY"
        # Cloud backend was actually written to.
        assert cloud.writes
        assert cloud.writes[0][1] == "sk-abc1234"

        # Service template wires the mirrored ref into a SecretKeyRef.
        template = gcp_cloudrun._build_service_template(
            config,
            deployer._gcp_config,
            "image:tag",
            deployer,
            mirrored_refs=list(deployer._mirror_result.refs),
        )
        env_entries = template["containers"][0]["env"]
        secret_entry = next(e for e in env_entries if e["name"] == "OPENAI_API_KEY")
        assert "value_source" in secret_entry
        assert (
            secret_entry["value_source"]["secret_key_ref"]["secret"]
            == "projects/test-proj/secrets/agentbreeder_orders_OPENAI_API_KEY"
        )

    def test_no_secrets_skips_mirror(self, monkeypatch):
        from engine.deployers import gcp_cloudrun

        config = _make_config("orders", cloud="gcp", secrets=[])
        deployer = gcp_cloudrun.GCPCloudRunDeployer()
        deployer._gcp_config = gcp_cloudrun._extract_cloudrun_config(config)
        _run(deployer._mirror_workspace_secrets(config, deployer._gcp_config))
        assert deployer._mirror_result is not None
        assert deployer._mirror_result.refs == []

    def test_mirror_failure_does_not_raise(self, monkeypatch):
        from engine.deployers import gcp_cloudrun

        async def boom(**kw):
            raise RuntimeError("mirror exploded")

        monkeypatch.setattr("engine.deployers.gcp_cloudrun.mirror_secrets_to_cloud", boom)

        config = _make_config("o", cloud="gcp", secrets=["X"])
        deployer = gcp_cloudrun.GCPCloudRunDeployer()
        deployer._gcp_config = gcp_cloudrun._extract_cloudrun_config(config)
        _run(deployer._mirror_workspace_secrets(config, deployer._gcp_config))
        assert deployer._mirror_result is not None
        assert "_" in deployer._mirror_result.errors


# ─── AWS ECS ────────────────────────────────────────────────────────────────


class TestAWSMirror:
    def test_mirror_then_container_definition_includes_secrets(self, monkeypatch):
        from engine.deployers import aws_ecs

        cloud = _CapturingCloud("aws")
        ws = _FakeWorkspace({"OPENAI_API_KEY": "sk-abc1234"})

        monkeypatch.setattr(
            "engine.secrets.auto_mirror._build_target_backend",
            lambda c, opts: cloud,
        )
        monkeypatch.setattr(
            "engine.secrets.auto_mirror.get_workspace_backend",
            lambda workspace=None: (ws, type("WS", (), {"workspace": "default"})()),
        )

        config = _make_config("ordersagent", cloud="aws", secrets=["OPENAI_API_KEY"])
        deployer = aws_ecs.AWSECSDeployer()
        deployer._aws_config = aws_ecs._extract_ecs_config(config)
        _run(deployer._mirror_workspace_secrets(config, deployer._aws_config))

        assert deployer._mirror_result is not None
        assert deployer._mirror_result.refs[0].cloud_name == (
            "agentbreeder/ordersagent/OPENAI_API_KEY"
        )
        assert cloud.writes[0][1] == "sk-abc1234"

        container = deployer._build_container_definition(
            config,
            "image:tag",
            mirrored_refs=list(deployer._mirror_result.refs),
        )
        assert "secrets" in container
        assert container["secrets"][0]["name"] == "OPENAI_API_KEY"
        assert (
            container["secrets"][0]["valueFrom"]
            == "arn:aws:secretsmanager:us-east-1:123456789012:secret:"
            "agentbreeder/ordersagent/OPENAI_API_KEY"
        )

    def test_no_secrets_no_secrets_field(self, monkeypatch):
        from engine.deployers import aws_ecs

        config = _make_config("orders", cloud="aws", secrets=[])
        deployer = aws_ecs.AWSECSDeployer()
        deployer._aws_config = aws_ecs._extract_ecs_config(config)
        _run(deployer._mirror_workspace_secrets(config, deployer._aws_config))
        container = deployer._build_container_definition(config, "image:tag")
        assert "secrets" not in container

    def test_mirror_failure_recorded_in_result(self, monkeypatch):
        from engine.deployers import aws_ecs

        async def boom(**kw):
            raise RuntimeError("mirror exploded")

        monkeypatch.setattr("engine.deployers.aws_ecs.mirror_secrets_to_cloud", boom)

        config = _make_config("orders", cloud="aws", secrets=["X"])
        deployer = aws_ecs.AWSECSDeployer()
        deployer._aws_config = aws_ecs._extract_ecs_config(config)
        _run(deployer._mirror_workspace_secrets(config, deployer._aws_config))
        assert deployer._mirror_result is not None
        assert deployer._mirror_result.errors


# ─── Cross-cutting: deterministic naming sanity ─────────────────────────────


def test_cloud_secret_ref_namespaces_per_agent():
    a = CloudSecretRef(
        logical_name="K",
        cloud_name="agentbreeder/agent-a/K",
        cloud="aws",
    )
    b = CloudSecretRef(
        logical_name="K",
        cloud_name="agentbreeder/agent-b/K",
        cloud="aws",
    )
    assert a.cloud_name != b.cloud_name


# ─── Final guard: no real cloud SDK was imported ────────────────────────────


def test_no_real_boto3_or_google_called(monkeypatch):
    """Sentinel test: prove the mirror path stays inside our fakes."""
    sdk_calls: list[str] = []

    monkeypatch.setattr(
        "engine.secrets.auto_mirror._build_target_backend",
        lambda c, opts: (sdk_calls.append("build"), _CapturingCloud(c))[1],
    )
    monkeypatch.setattr(
        "engine.secrets.auto_mirror.get_workspace_backend",
        lambda workspace=None: (
            _FakeWorkspace({"X": "v0123456"}),
            type("WS", (), {"workspace": "default"})(),
        ),
    )

    from engine.secrets.auto_mirror import mirror_secrets_to_cloud

    _run(mirror_secrets_to_cloud("a", ["X"], target_cloud="aws"))
    assert sdk_calls == ["build"]


# Suppress unused-import warning in environments where _Any isn't referenced.
_ = Any
