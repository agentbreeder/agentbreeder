"""Unit tests for engine/deployers/kubernetes.py.

All Kubernetes API calls and Docker SDK calls are mocked — no real cluster or
container daemon is required to run these tests.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.config_parser import AgentConfig, FrameworkType
from engine.deployers.base import DeployResult
from engine.deployers.kubernetes import (
    DEFAULT_NAMESPACE,
    K8sConfig,
    KubernetesDeployer,
    _build_deployment_manifest,
    _build_hpa_manifest,
    _build_service_manifest,
    _extract_k8s_config,
    _resolve_image_name,
)
from engine.runtimes.base import ContainerImage

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """Build a minimal AgentConfig suitable for Kubernetes deployer tests."""
    defaults: dict = {
        "name": "my-agent",
        "version": "1.2.3",
        "team": "platform",
        "owner": "bob@example.com",
        "framework": FrameworkType.langgraph,
        "model": {"primary": "claude-sonnet-4"},
        "deploy": {
            "cloud": "kubernetes",
            "scaling": {"min": 1, "max": 3, "target_cpu": 70},
            "resources": {"cpu": "1", "memory": "512Mi"},
            "env_vars": {},
        },
    }
    if "deploy" in overrides:
        defaults["deploy"].update(overrides.pop("deploy"))
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _make_image() -> ContainerImage:
    d = Path(tempfile.mkdtemp())
    (d / "Dockerfile").write_text("FROM python:3.11-slim")
    return ContainerImage(
        tag="agentbreeder/my-agent:1.2.3",
        dockerfile_content="FROM python:3.11-slim",
        context_dir=d,
    )


def _make_api_exception(status: int) -> MagicMock:
    """Return a MagicMock that looks like a kubernetes ApiException."""
    exc = MagicMock()
    exc.status = status
    return exc


def _make_k8s_modules() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return (kubernetes_module, apps_v1_mock, core_v1_mock).

    Patches sys.modules so that ``import kubernetes`` returns the mock.
    """
    mock_k8s = MagicMock()
    apps_v1 = MagicMock()
    core_v1 = MagicMock()
    autoscaling_v2 = MagicMock()

    mock_k8s_client = MagicMock()
    mock_k8s_client.AppsV1Api.return_value = apps_v1
    mock_k8s_client.CoreV1Api.return_value = core_v1
    mock_k8s_client.AutoscalingV2Api.return_value = autoscaling_v2
    mock_k8s.client = mock_k8s_client
    mock_k8s.config = MagicMock()

    # ApiException lives in kubernetes.client.rest in the real library; mock it here
    mock_api_exc_class = type("ApiException", (Exception,), {"status": 0})
    mock_k8s.client.rest = MagicMock()
    mock_k8s.client.rest.ApiException = mock_api_exc_class

    return mock_k8s, apps_v1, core_v1, autoscaling_v2


# ---------------------------------------------------------------------------
# _extract_k8s_config
# ---------------------------------------------------------------------------


class TestExtractK8sConfig:
    def test_defaults_namespace(self) -> None:
        config = _make_config()
        k8s = _extract_k8s_config(config)
        assert k8s.namespace == DEFAULT_NAMESPACE

    def test_reads_custom_namespace(self) -> None:
        config = _make_config(deploy={"env_vars": {"K8S_NAMESPACE": "production"}})
        k8s = _extract_k8s_config(config)
        assert k8s.namespace == "production"

    def test_reads_context(self) -> None:
        config = _make_config(deploy={"env_vars": {"K8S_CONTEXT": "my-cluster"}})
        k8s = _extract_k8s_config(config)
        assert k8s.context == "my-cluster"

    def test_context_defaults_to_none(self) -> None:
        config = _make_config()
        k8s = _extract_k8s_config(config)
        assert k8s.context is None

    def test_reads_image_pull_secret(self) -> None:
        config = _make_config(deploy={"env_vars": {"K8S_IMAGE_PULL_SECRET": "registry-creds"}})
        k8s = _extract_k8s_config(config)
        assert k8s.image_pull_secret == "registry-creds"

    def test_reads_image_override(self) -> None:
        config = _make_config(
            deploy={"env_vars": {"K8S_IMAGE": "registry.example.com/my-agent:latest"}}
        )
        k8s = _extract_k8s_config(config)
        assert k8s.image == "registry.example.com/my-agent:latest"


# ---------------------------------------------------------------------------
# _resolve_image_name
# ---------------------------------------------------------------------------


class TestResolveImageName:
    def test_defaults_to_name_version(self) -> None:
        config = _make_config()
        k8s = K8sConfig()
        assert _resolve_image_name(config, k8s) == "my-agent:1.2.3"

    def test_uses_k8s_image_override(self) -> None:
        config = _make_config()
        k8s = K8sConfig(image="registry.example.com/my-agent:v1")
        assert _resolve_image_name(config, k8s) == "registry.example.com/my-agent:v1"


# ---------------------------------------------------------------------------
# _build_deployment_manifest
# ---------------------------------------------------------------------------


class TestBuildDeploymentManifest:
    def test_has_correct_metadata(self) -> None:
        config = _make_config()
        k8s = K8sConfig()
        manifest = _build_deployment_manifest(config, k8s, "my-agent:1.2.3")

        assert manifest["metadata"]["name"] == "my-agent"
        assert manifest["metadata"]["namespace"] == DEFAULT_NAMESPACE
        assert manifest["metadata"]["labels"]["managed-by"] == "agentbreeder"

    def test_replicas_from_scaling_min(self) -> None:
        config = _make_config(deploy={"scaling": {"min": 2, "max": 5, "target_cpu": 70}})
        k8s = K8sConfig()
        manifest = _build_deployment_manifest(config, k8s, "img:1.0.0")
        assert manifest["spec"]["replicas"] == 2

    def test_replicas_at_least_one(self) -> None:
        config = _make_config(deploy={"scaling": {"min": 0, "max": 5, "target_cpu": 70}})
        k8s = K8sConfig()
        manifest = _build_deployment_manifest(config, k8s, "img:1.0.0")
        assert manifest["spec"]["replicas"] == 1

    def test_container_image_set(self) -> None:
        config = _make_config()
        k8s = K8sConfig()
        manifest = _build_deployment_manifest(config, k8s, "custom-image:tag")
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == "custom-image:tag"

    def test_container_has_health_probes(self) -> None:
        config = _make_config()
        k8s = K8sConfig()
        manifest = _build_deployment_manifest(config, k8s, "img:1.0.0")
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        assert container["livenessProbe"]["httpGet"]["path"] == "/health"
        assert container["readinessProbe"]["httpGet"]["path"] == "/health"

    def test_env_vars_include_agent_metadata(self) -> None:
        config = _make_config()
        k8s = K8sConfig()
        manifest = _build_deployment_manifest(config, k8s, "img:1.0.0")
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        env_names = {e["name"] for e in container["env"]}
        assert "AGENT_NAME" in env_names
        assert "AGENT_VERSION" in env_names
        assert "AGENT_FRAMEWORK" in env_names

    def test_k8s_prefixed_env_vars_excluded(self) -> None:
        config = _make_config(
            deploy={
                "env_vars": {
                    "K8S_NAMESPACE": "prod",
                    "LOG_LEVEL": "info",
                },
            }
        )
        k8s = K8sConfig()
        manifest = _build_deployment_manifest(config, k8s, "img:1.0.0")
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        env_names = {e["name"] for e in container["env"]}
        assert "LOG_LEVEL" in env_names
        assert "K8S_NAMESPACE" not in env_names

    def test_image_pull_secret_included(self) -> None:
        config = _make_config()
        k8s = K8sConfig(image_pull_secret="my-secret")
        manifest = _build_deployment_manifest(config, k8s, "img:1.0.0")
        pod_spec = manifest["spec"]["template"]["spec"]
        assert pod_spec["imagePullSecrets"] == [{"name": "my-secret"}]

    def test_no_image_pull_secret_when_not_set(self) -> None:
        config = _make_config()
        k8s = K8sConfig()
        manifest = _build_deployment_manifest(config, k8s, "img:1.0.0")
        pod_spec = manifest["spec"]["template"]["spec"]
        assert "imagePullSecrets" not in pod_spec

    def test_resource_limits_set(self) -> None:
        config = _make_config(deploy={"resources": {"cpu": "2", "memory": "2Gi"}})
        k8s = K8sConfig()
        manifest = _build_deployment_manifest(config, k8s, "img:1.0.0")
        container = manifest["spec"]["template"]["spec"]["containers"][0]
        assert container["resources"]["limits"]["cpu"] == "2"
        assert container["resources"]["limits"]["memory"] == "2Gi"


# ---------------------------------------------------------------------------
# _build_service_manifest
# ---------------------------------------------------------------------------


class TestBuildServiceManifest:
    def test_clusterip_service(self) -> None:
        config = _make_config()
        manifest = _build_service_manifest(config, DEFAULT_NAMESPACE)
        assert manifest["spec"]["type"] == "ClusterIP"

    def test_service_port_8080(self) -> None:
        config = _make_config()
        manifest = _build_service_manifest(config, DEFAULT_NAMESPACE)
        port = manifest["spec"]["ports"][0]
        assert port["port"] == 8080
        assert port["targetPort"] == 8080

    def test_service_selector_matches_agent(self) -> None:
        config = _make_config()
        manifest = _build_service_manifest(config, DEFAULT_NAMESPACE)
        assert manifest["spec"]["selector"]["app"] == "my-agent"


# ---------------------------------------------------------------------------
# _build_hpa_manifest
# ---------------------------------------------------------------------------


class TestBuildHpaManifest:
    def test_hpa_scale_target(self) -> None:
        config = _make_config()
        manifest = _build_hpa_manifest(config, DEFAULT_NAMESPACE)
        spec = manifest["spec"]
        assert spec["scaleTargetRef"]["kind"] == "Deployment"
        assert spec["scaleTargetRef"]["name"] == "my-agent"

    def test_hpa_min_max_replicas(self) -> None:
        config = _make_config(deploy={"scaling": {"min": 2, "max": 8, "target_cpu": 60}})
        manifest = _build_hpa_manifest(config, DEFAULT_NAMESPACE)
        assert manifest["spec"]["minReplicas"] == 2
        assert manifest["spec"]["maxReplicas"] == 8

    def test_hpa_target_cpu(self) -> None:
        config = _make_config(deploy={"scaling": {"min": 1, "max": 5, "target_cpu": 80}})
        manifest = _build_hpa_manifest(config, DEFAULT_NAMESPACE)
        metric = manifest["spec"]["metrics"][0]
        assert metric["resource"]["target"]["averageUtilization"] == 80


# ---------------------------------------------------------------------------
# KubernetesDeployer._get_k8s_clients — ImportError behaviour
# ---------------------------------------------------------------------------


class TestGetK8sClients:
    def test_raises_import_error_with_install_hint_when_sdk_missing(self) -> None:
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        with patch.dict("sys.modules", {"kubernetes": None}):
            with pytest.raises(ImportError, match="pip install agentbreeder\\[kubernetes\\]"):
                deployer._get_k8s_clients()

    def test_loads_kubeconfig_with_context(self) -> None:
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig(context="staging-cluster")

        mock_k8s, apps_v1, core_v1, _autoscaling_v2 = _make_k8s_modules()

        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s,
                "kubernetes.client": mock_k8s.client,
                "kubernetes.client.rest": mock_k8s.client.rest,
            },
        ):
            deployer._get_k8s_clients()

        mock_k8s.config.load_kube_config.assert_called_once_with(context="staging-cluster")

    def test_loads_kubeconfig_without_context(self) -> None:
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()  # context=None

        mock_k8s, apps_v1, core_v1, _autoscaling_v2 = _make_k8s_modules()

        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s,
                "kubernetes.client": mock_k8s.client,
                "kubernetes.client.rest": mock_k8s.client.rest,
            },
        ):
            deployer._get_k8s_clients()

        mock_k8s.config.load_kube_config.assert_called_once_with(context=None)


# ---------------------------------------------------------------------------
# KubernetesDeployer.provision
# ---------------------------------------------------------------------------


class TestProvision:
    @pytest.mark.asyncio
    async def test_provision_creates_namespace_when_absent(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config()

        mock_k8s, apps_v1, core_v1, _autoscaling_v2 = _make_k8s_modules()

        # Simulate namespace not found (404)
        api_exc = mock_k8s.client.rest.ApiException("not found")
        api_exc.status = 404
        core_v1.read_namespace.side_effect = api_exc

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            with patch(
                "engine.deployers.kubernetes.KubernetesDeployer._ensure_namespace"
            ) as mock_ensure:
                result = await deployer.provision(config)

        mock_ensure.assert_called_once_with(core_v1, DEFAULT_NAMESPACE)
        assert result.resource_ids["namespace"] == DEFAULT_NAMESPACE

    @pytest.mark.asyncio
    async def test_provision_returns_cluster_local_url(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config()

        mock_k8s, apps_v1, core_v1, _autoscaling_v2 = _make_k8s_modules()
        core_v1.read_namespace.return_value = MagicMock()

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            with patch.object(deployer, "_ensure_namespace"):
                result = await deployer.provision(config)

        assert "my-agent" in result.endpoint_url
        assert "svc.cluster.local" in result.endpoint_url
        assert "8080" in result.endpoint_url

    @pytest.mark.asyncio
    async def test_provision_includes_resource_ids(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config()

        mock_k8s, apps_v1, core_v1, _autoscaling_v2 = _make_k8s_modules()

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            with patch.object(deployer, "_ensure_namespace"):
                result = await deployer.provision(config)

        assert result.resource_ids["deployment"] == "my-agent"
        assert result.resource_ids["service"] == "my-agent"

    @pytest.mark.asyncio
    async def test_provision_raises_import_error_when_sdk_missing(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config()

        with patch.object(
            deployer,
            "_get_k8s_clients",
            side_effect=ImportError("pip install agentbreeder[kubernetes]"),
        ):
            with pytest.raises(ImportError, match="pip install agentbreeder"):
                await deployer.provision(config)

    @pytest.mark.asyncio
    async def test_provision_skips_namespace_creation_when_it_exists(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config()

        mock_k8s, apps_v1, core_v1, _autoscaling_v2 = _make_k8s_modules()
        # read_namespace succeeds (namespace exists)
        core_v1.read_namespace.return_value = MagicMock()

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            # Let _ensure_namespace run for real (it uses the mocked core_v1)
            deployer._k8s_config = K8sConfig()
            with patch(
                "engine.deployers.kubernetes.KubernetesDeployer._ensure_namespace"
            ) as mock_ensure:
                await deployer.provision(config)

            # Called once with the right namespace
            mock_ensure.assert_called_once()
            assert mock_ensure.call_args[0][1] == DEFAULT_NAMESPACE


# ---------------------------------------------------------------------------
# KubernetesDeployer.deploy
# ---------------------------------------------------------------------------


class TestDeploy:
    @pytest.mark.asyncio
    async def test_deploy_creates_deployment_and_service(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config()
        image = _make_image()

        mock_k8s, apps_v1, core_v1, _autoscaling_v2 = _make_k8s_modules()

        # Simulate Deployment not yet existing (create path)
        apps_v1.read_namespaced_deployment.side_effect = Exception("not found")
        core_v1.read_namespaced_service.side_effect = Exception("not found")

        deployment_status = MagicMock()
        deployment_status.status.available_replicas = 1
        apps_v1.read_namespaced_deployment.side_effect = [
            Exception("not found"),  # first call in _apply_deployment (check existence)
            deployment_status,  # poll in _wait_for_rollout
        ]

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)),
            patch.object(deployer, "_ensure_namespace"),
            patch.object(deployer, "_build_docker_image", new_callable=AsyncMock),
            patch.object(deployer, "_apply_deployment") as mock_apply_dep,
            patch.object(deployer, "_apply_service") as mock_apply_svc,
            patch.object(deployer, "_apply_hpa"),
            patch.object(deployer, "_wait_for_rollout", new_callable=AsyncMock),
        ):
            result = await deployer.deploy(config, image)

        mock_apply_dep.assert_called_once()
        mock_apply_svc.assert_called_once()
        assert result.agent_name == "my-agent"
        assert result.version == "1.2.3"
        assert result.status == "running"

    @pytest.mark.asyncio
    async def test_deploy_creates_hpa_when_max_replicas_gt_one(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config(deploy={"scaling": {"min": 1, "max": 5, "target_cpu": 70}})
        image = _make_image()

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(MagicMock(), MagicMock())),
            patch.object(deployer, "_ensure_namespace"),
            patch.object(deployer, "_build_docker_image", new_callable=AsyncMock),
            patch.object(deployer, "_apply_deployment"),
            patch.object(deployer, "_apply_service"),
            patch.object(deployer, "_apply_hpa") as mock_apply_hpa,
            patch.object(deployer, "_wait_for_rollout", new_callable=AsyncMock),
        ):
            await deployer.deploy(config, image)

        mock_apply_hpa.assert_called_once()

    @pytest.mark.asyncio
    async def test_deploy_skips_hpa_when_max_replicas_is_one(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config(deploy={"scaling": {"min": 1, "max": 1, "target_cpu": 70}})
        image = _make_image()

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(MagicMock(), MagicMock())),
            patch.object(deployer, "_ensure_namespace"),
            patch.object(deployer, "_build_docker_image", new_callable=AsyncMock),
            patch.object(deployer, "_apply_deployment"),
            patch.object(deployer, "_apply_service"),
            patch.object(deployer, "_apply_hpa") as mock_apply_hpa,
            patch.object(deployer, "_wait_for_rollout", new_callable=AsyncMock),
        ):
            await deployer.deploy(config, image)

        mock_apply_hpa.assert_not_called()

    @pytest.mark.asyncio
    async def test_deploy_returns_cluster_local_endpoint(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config()
        image = _make_image()

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(MagicMock(), MagicMock())),
            patch.object(deployer, "_ensure_namespace"),
            patch.object(deployer, "_build_docker_image", new_callable=AsyncMock),
            patch.object(deployer, "_apply_deployment"),
            patch.object(deployer, "_apply_service"),
            patch.object(deployer, "_apply_hpa"),
            patch.object(deployer, "_wait_for_rollout", new_callable=AsyncMock),
        ):
            result = await deployer.deploy(config, image)

        assert "my-agent" in result.endpoint_url
        assert "svc.cluster.local" in result.endpoint_url

    @pytest.mark.asyncio
    async def test_deploy_uses_k8s_image_env_var_override(self) -> None:
        deployer = KubernetesDeployer()
        config = _make_config(
            deploy={"env_vars": {"K8S_IMAGE": "registry.example.com/custom:latest"}}
        )
        image = _make_image()

        captured_image_name: list[str] = []

        async def capture_build(image, name):
            captured_image_name.append(name)

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(MagicMock(), MagicMock())),
            patch.object(deployer, "_ensure_namespace"),
            patch.object(deployer, "_build_docker_image", side_effect=capture_build),
            patch.object(deployer, "_apply_deployment"),
            patch.object(deployer, "_apply_service"),
            patch.object(deployer, "_apply_hpa"),
            patch.object(deployer, "_wait_for_rollout", new_callable=AsyncMock),
        ):
            result = await deployer.deploy(config, image)

        assert captured_image_name[0] == "registry.example.com/custom:latest"
        assert result.container_id == "registry.example.com/custom:latest"


# ---------------------------------------------------------------------------
# KubernetesDeployer.teardown
# ---------------------------------------------------------------------------


class TestTeardown:
    @pytest.mark.asyncio
    async def test_teardown_deletes_deployment_service_hpa_configmap(self) -> None:
        """All four delete calls are made; 404 errors are silently ignored."""
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        fake_api_exc = type("ApiException", (Exception,), {"status": 404})
        fake_rest = MagicMock()
        fake_rest.ApiException = fake_api_exc

        apps_v1 = MagicMock()
        core_v1 = MagicMock()
        autoscaling_v2 = MagicMock()

        # All resources return 404 — already gone
        apps_v1.delete_namespaced_deployment.side_effect = fake_api_exc(404)
        core_v1.delete_namespaced_service.side_effect = fake_api_exc(404)
        autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler.side_effect = fake_api_exc(404)
        core_v1.delete_namespaced_config_map.side_effect = fake_api_exc(404)

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)),
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict("sys.modules", {"kubernetes.client.rest": fake_rest}),
        ):
            await deployer.teardown("my-agent")

        apps_v1.delete_namespaced_deployment.assert_called_once_with(
            name="my-agent", namespace=DEFAULT_NAMESPACE
        )
        core_v1.delete_namespaced_service.assert_called_once_with(
            name="my-agent", namespace=DEFAULT_NAMESPACE
        )
        autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler.assert_called_once_with(
            name="my-agent", namespace=DEFAULT_NAMESPACE
        )
        core_v1.delete_namespaced_config_map.assert_called_once_with(
            name="my-agent", namespace=DEFAULT_NAMESPACE
        )

    @pytest.mark.asyncio
    async def test_teardown_raises_without_k8s_config(self) -> None:
        deployer = KubernetesDeployer()
        with pytest.raises(RuntimeError, match="Cannot teardown without Kubernetes config"):
            await deployer.teardown("my-agent")

    @pytest.mark.asyncio
    async def test_teardown_propagates_non_404_errors(self) -> None:
        """Non-404 API errors (e.g., 403 Forbidden) should propagate."""
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        fake_api_exc_class = type("ApiException", (Exception,), {"status": 403})
        fake_rest = MagicMock()
        fake_rest.ApiException = fake_api_exc_class

        apps_v1 = MagicMock()
        core_v1 = MagicMock()
        autoscaling_v2 = MagicMock()

        apps_v1.delete_namespaced_deployment.side_effect = fake_api_exc_class(403)

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)),
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict("sys.modules", {"kubernetes.client.rest": fake_rest}),
            pytest.raises(fake_api_exc_class),
        ):
            await deployer.teardown("my-agent")


# ---------------------------------------------------------------------------
# KubernetesDeployer.get_logs
# ---------------------------------------------------------------------------


class TestGetLogs:
    @pytest.mark.asyncio
    async def test_get_logs_returns_message_when_no_config(self) -> None:
        deployer = KubernetesDeployer()
        logs = await deployer.get_logs("my-agent")
        assert len(logs) == 1
        assert "not initialized" in logs[0].lower()

    @pytest.mark.asyncio
    async def test_get_logs_returns_message_when_no_pods(self) -> None:
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        apps_v1 = MagicMock()
        core_v1 = MagicMock()
        core_v1.list_namespaced_pod.return_value = MagicMock(items=[])

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            logs = await deployer.get_logs("my-agent")

        assert len(logs) == 1
        assert "no pods" in logs[0].lower()
        core_v1.list_namespaced_pod.assert_called_once_with(
            namespace=DEFAULT_NAMESPACE,
            label_selector="app=my-agent",
        )

    @pytest.mark.asyncio
    async def test_get_logs_reads_from_running_pod(self) -> None:
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        apps_v1 = MagicMock()
        core_v1 = MagicMock()

        running_pod = MagicMock()
        running_pod.metadata.name = "my-agent-abc123"
        running_pod.status.phase = "Running"

        core_v1.list_namespaced_pod.return_value = MagicMock(items=[running_pod])
        core_v1.read_namespaced_pod_log.return_value = "line1\nline2\nline3"

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            logs = await deployer.get_logs("my-agent")

        assert logs == ["line1", "line2", "line3"]
        core_v1.read_namespaced_pod_log.assert_called_once_with(
            name="my-agent-abc123",
            namespace=DEFAULT_NAMESPACE,
            container="my-agent",
            tail_lines=200,
        )

    @pytest.mark.asyncio
    async def test_get_logs_prefers_running_pod_over_pending(self) -> None:
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        apps_v1 = MagicMock()
        core_v1 = MagicMock()

        pending_pod = MagicMock()
        pending_pod.metadata.name = "my-agent-pending"
        pending_pod.status.phase = "Pending"

        running_pod = MagicMock()
        running_pod.metadata.name = "my-agent-running"
        running_pod.status.phase = "Running"

        core_v1.list_namespaced_pod.return_value = MagicMock(items=[pending_pod, running_pod])
        core_v1.read_namespaced_pod_log.return_value = "output"

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            await deployer.get_logs("my-agent")

        # Should read from the running pod
        call_kwargs = core_v1.read_namespaced_pod_log.call_args[1]
        assert call_kwargs["name"] == "my-agent-running"

    @pytest.mark.asyncio
    async def test_get_logs_passes_since_seconds(self) -> None:
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        apps_v1 = MagicMock()
        core_v1 = MagicMock()

        pod = MagicMock()
        pod.metadata.name = "my-agent-xyz"
        pod.status.phase = "Running"
        core_v1.list_namespaced_pod.return_value = MagicMock(items=[pod])
        core_v1.read_namespaced_pod_log.return_value = "log line"

        since = datetime(2026, 1, 1, 12, 0, 0)

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            await deployer.get_logs("my-agent", since=since)

        call_kwargs = core_v1.read_namespaced_pod_log.call_args[1]
        assert "since_seconds" in call_kwargs
        assert call_kwargs["since_seconds"] > 0

    @pytest.mark.asyncio
    async def test_get_logs_handles_empty_log_output(self) -> None:
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        apps_v1 = MagicMock()
        core_v1 = MagicMock()

        pod = MagicMock()
        pod.metadata.name = "my-agent-abc"
        pod.status.phase = "Running"
        core_v1.list_namespaced_pod.return_value = MagicMock(items=[pod])
        core_v1.read_namespaced_pod_log.return_value = ""

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            logs = await deployer.get_logs("my-agent")

        assert len(logs) == 1
        assert "no log output" in logs[0].lower()


# ---------------------------------------------------------------------------
# KubernetesDeployer.health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_passes_on_200(self) -> None:
        deployer = KubernetesDeployer()
        result = DeployResult(
            endpoint_url="http://my-agent.agentbreeder.svc.cluster.local:8080",
            container_id="my-agent:1.2.3",
            status="running",
            agent_name="my-agent",
            version="1.2.3",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            health = await deployer.health_check(result, timeout=5, interval=1)

        assert health.healthy is True
        assert health.checks["reachable"] is True
        assert health.checks["healthy"] is True

    @pytest.mark.asyncio
    async def test_health_check_fails_after_timeout(self) -> None:
        deployer = KubernetesDeployer()
        result = DeployResult(
            endpoint_url="http://my-agent.agentbreeder.svc.cluster.local:8080",
            container_id="my-agent:1.2.3",
            status="running",
            agent_name="my-agent",
            version="1.2.3",
        )

        import httpx as httpx_mod

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx_mod.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            health = await deployer.health_check(result, timeout=3, interval=1)

        assert health.healthy is False
        assert health.checks["reachable"] is False
        assert health.checks["healthy"] is False

    @pytest.mark.asyncio
    async def test_health_check_retries_on_non_200(self) -> None:
        deployer = KubernetesDeployer()
        result = DeployResult(
            endpoint_url="http://my-agent.agentbreeder.svc.cluster.local:8080",
            container_id="my-agent:1.2.3",
            status="running",
            agent_name="my-agent",
            version="1.2.3",
        )

        mock_response_503 = MagicMock()
        mock_response_503.status_code = 503
        mock_response_200 = MagicMock()
        mock_response_200.status_code = 200

        mock_client = AsyncMock()
        mock_client.get.side_effect = [mock_response_503, mock_response_200]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            health = await deployer.health_check(result, timeout=4, interval=1)

        assert health.healthy is True

    @pytest.mark.asyncio
    async def test_health_check_url_is_health_endpoint(self) -> None:
        deployer = KubernetesDeployer()
        result = DeployResult(
            endpoint_url="http://my-agent.agentbreeder.svc.cluster.local:8080",
            container_id="my-agent:1.2.3",
            status="running",
            agent_name="my-agent",
            version="1.2.3",
        )

        captured_urls: list[str] = []
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()

        async def capture_get(url, **kwargs):
            captured_urls.append(url)
            return mock_response

        mock_client.get.side_effect = capture_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await deployer.health_check(result, timeout=5, interval=1)

        assert captured_urls[0].endswith("/health")


# ---------------------------------------------------------------------------
# KubernetesDeployer._wait_for_rollout
# ---------------------------------------------------------------------------


class TestWaitForRollout:
    @pytest.mark.asyncio
    async def test_wait_for_rollout_returns_when_replica_available(self) -> None:
        deployer = KubernetesDeployer()
        apps_v1 = MagicMock()

        deployment_ready = MagicMock()
        deployment_ready.status.available_replicas = 1
        apps_v1.read_namespaced_deployment.return_value = deployment_ready

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await deployer._wait_for_rollout(apps_v1, "my-agent", DEFAULT_NAMESPACE)

        # Should not sleep at all since the first poll returns available=1
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_for_rollout_polls_until_available(self) -> None:
        deployer = KubernetesDeployer()
        apps_v1 = MagicMock()

        dep_not_ready = MagicMock()
        dep_not_ready.status.available_replicas = 0
        dep_ready = MagicMock()
        dep_ready.status.available_replicas = 1

        apps_v1.read_namespaced_deployment.side_effect = [
            dep_not_ready,
            dep_not_ready,
            dep_ready,
        ]

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await deployer._wait_for_rollout(
                apps_v1, "my-agent", DEFAULT_NAMESPACE, poll_interval=1
            )

        assert apps_v1.read_namespaced_deployment.call_count == 3

    @pytest.mark.asyncio
    async def test_wait_for_rollout_times_out_gracefully(self) -> None:
        deployer = KubernetesDeployer()
        apps_v1 = MagicMock()

        dep_not_ready = MagicMock()
        dep_not_ready.status.available_replicas = None
        apps_v1.read_namespaced_deployment.return_value = dep_not_ready

        # Small max_wait so the test is fast
        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Should NOT raise — just log a warning
            await deployer._wait_for_rollout(
                apps_v1, "my-agent", DEFAULT_NAMESPACE, max_wait=3, poll_interval=1
            )


# ---------------------------------------------------------------------------
# KubernetesDeployer._get_autoscaling_client — lines 263-268
# ---------------------------------------------------------------------------


class TestGetAutoscalingClient:
    def test_raises_import_error_when_kubernetes_sdk_missing(self) -> None:
        deployer = KubernetesDeployer()
        with patch.dict("sys.modules", {"kubernetes": None}):
            with pytest.raises(ImportError, match="pip install agentbreeder\\[kubernetes\\]"):
                deployer._get_autoscaling_client()

    def test_returns_autoscaling_v2_api(self) -> None:
        deployer = KubernetesDeployer()
        mock_k8s, _apps_v1, _core_v1, autoscaling_v2 = _make_k8s_modules()
        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s,
                "kubernetes.client": mock_k8s.client,
                "kubernetes.client.rest": mock_k8s.client.rest,
            },
        ):
            result = deployer._get_autoscaling_client()
        # Should be the return value of AutoscalingV2Api()
        assert result is mock_k8s.client.AutoscalingV2Api.return_value


# ---------------------------------------------------------------------------
# KubernetesDeployer._ensure_namespace — lines 276-300
# ---------------------------------------------------------------------------


class TestEnsureNamespace:
    def test_raises_import_error_when_kubernetes_sdk_missing(self) -> None:
        deployer = KubernetesDeployer()
        core_v1 = MagicMock()
        with patch.dict("sys.modules", {"kubernetes.client.rest": None}):
            with pytest.raises(ImportError, match="pip install agentbreeder\\[kubernetes\\]"):
                deployer._ensure_namespace(core_v1, "test-ns")

    def test_skips_creation_when_namespace_exists(self) -> None:
        """read_namespace succeeds → no create_namespace call."""
        deployer = KubernetesDeployer()
        core_v1 = MagicMock()
        core_v1.read_namespace.return_value = MagicMock()

        mock_k8s, _, _, _ = _make_k8s_modules()
        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s,
                "kubernetes.client": mock_k8s.client,
                "kubernetes.client.rest": mock_k8s.client.rest,
            },
        ):
            deployer._ensure_namespace(core_v1, "existing-ns")

        core_v1.create_namespace.assert_not_called()

    def test_creates_namespace_on_404(self) -> None:
        """read_namespace raises 404 → create_namespace is called."""
        deployer = KubernetesDeployer()
        core_v1 = MagicMock()

        mock_k8s, _, _, _ = _make_k8s_modules()
        api_exc_class = mock_k8s.client.rest.ApiException
        exc_404 = api_exc_class("not found")
        exc_404.status = 404
        core_v1.read_namespace.side_effect = exc_404

        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s,
                "kubernetes.client": mock_k8s.client,
                "kubernetes.client.rest": mock_k8s.client.rest,
            },
        ):
            deployer._ensure_namespace(core_v1, "new-ns")

        core_v1.create_namespace.assert_called_once()
        body = core_v1.create_namespace.call_args[1]["body"]
        assert body["metadata"]["name"] == "new-ns"
        assert body["metadata"]["labels"]["managed-by"] == "agentbreeder"

    def test_propagates_non_404_api_exception(self) -> None:
        """Non-404 exceptions (e.g. 403 Forbidden) should re-raise."""
        deployer = KubernetesDeployer()
        core_v1 = MagicMock()

        mock_k8s, _, _, _ = _make_k8s_modules()
        api_exc_class = mock_k8s.client.rest.ApiException
        exc_403 = api_exc_class("forbidden")
        exc_403.status = 403
        core_v1.read_namespace.side_effect = exc_403

        with (
            patch.dict(
                "sys.modules",
                {
                    "kubernetes": mock_k8s,
                    "kubernetes.client": mock_k8s.client,
                    "kubernetes.client.rest": mock_k8s.client.rest,
                },
            ),
            pytest.raises(type(exc_403)),
        ):
            deployer._ensure_namespace(core_v1, "secret-ns")


# ---------------------------------------------------------------------------
# KubernetesDeployer.teardown — success paths (resources exist)
# lines 464-466, 473, 482, 485, 493, 496, 502, 505
# ---------------------------------------------------------------------------


class TestTeardownSuccessPaths:
    @pytest.mark.asyncio
    async def test_teardown_deletes_all_resources_successfully(self) -> None:
        """All four delete calls succeed (resources exist) — happy path."""
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        fake_api_exc = type("ApiException", (Exception,), {"status": 0})
        fake_rest = MagicMock()
        fake_rest.ApiException = fake_api_exc

        apps_v1 = MagicMock()
        core_v1 = MagicMock()
        autoscaling_v2 = MagicMock()

        # All deletes succeed (return None-like)
        apps_v1.delete_namespaced_deployment.return_value = MagicMock()
        core_v1.delete_namespaced_service.return_value = MagicMock()
        autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler.return_value = MagicMock()
        core_v1.delete_namespaced_config_map.return_value = MagicMock()

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)),
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict("sys.modules", {"kubernetes.client.rest": fake_rest}),
        ):
            await deployer.teardown("my-agent")

        apps_v1.delete_namespaced_deployment.assert_called_once_with(
            name="my-agent", namespace=DEFAULT_NAMESPACE
        )
        core_v1.delete_namespaced_service.assert_called_once_with(
            name="my-agent", namespace=DEFAULT_NAMESPACE
        )
        autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler.assert_called_once_with(
            name="my-agent", namespace=DEFAULT_NAMESPACE
        )
        core_v1.delete_namespaced_config_map.assert_called_once_with(
            name="my-agent", namespace=DEFAULT_NAMESPACE
        )

    @pytest.mark.asyncio
    async def test_teardown_propagates_403_on_service_delete(self) -> None:
        """Non-404 error on Service delete should propagate."""
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        fake_api_exc_class = type("ApiException", (Exception,), {"status": 403})
        fake_rest = MagicMock()
        fake_rest.ApiException = fake_api_exc_class

        apps_v1 = MagicMock()
        core_v1 = MagicMock()
        autoscaling_v2 = MagicMock()

        # Deployment deletes fine; Service throws 403
        apps_v1.delete_namespaced_deployment.return_value = MagicMock()
        core_v1.delete_namespaced_service.side_effect = fake_api_exc_class(403)

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)),
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict("sys.modules", {"kubernetes.client.rest": fake_rest}),
            pytest.raises(fake_api_exc_class),
        ):
            await deployer.teardown("my-agent")

    @pytest.mark.asyncio
    async def test_teardown_propagates_403_on_hpa_delete(self) -> None:
        """Non-404 error on HPA delete should propagate."""
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        fake_api_exc_class = type("ApiException", (Exception,), {"status": 403})
        fake_rest = MagicMock()
        fake_rest.ApiException = fake_api_exc_class

        apps_v1 = MagicMock()
        core_v1 = MagicMock()
        autoscaling_v2 = MagicMock()

        apps_v1.delete_namespaced_deployment.return_value = MagicMock()
        core_v1.delete_namespaced_service.return_value = MagicMock()
        autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler.side_effect = (
            fake_api_exc_class(403)
        )

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)),
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict("sys.modules", {"kubernetes.client.rest": fake_rest}),
            pytest.raises(fake_api_exc_class),
        ):
            await deployer.teardown("my-agent")

    @pytest.mark.asyncio
    async def test_teardown_propagates_403_on_configmap_delete(self) -> None:
        """Non-404 error on ConfigMap delete should propagate."""
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        fake_api_exc_class = type("ApiException", (Exception,), {"status": 403})
        fake_rest = MagicMock()
        fake_rest.ApiException = fake_api_exc_class

        apps_v1 = MagicMock()
        core_v1 = MagicMock()
        autoscaling_v2 = MagicMock()

        apps_v1.delete_namespaced_deployment.return_value = MagicMock()
        core_v1.delete_namespaced_service.return_value = MagicMock()
        autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler.return_value = MagicMock()
        core_v1.delete_namespaced_config_map.side_effect = fake_api_exc_class(403)

        with (
            patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)),
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict("sys.modules", {"kubernetes.client.rest": fake_rest}),
            pytest.raises(fake_api_exc_class),
        ):
            await deployer.teardown("my-agent")


# ---------------------------------------------------------------------------
# KubernetesDeployer.get_logs — lines 540, 558-559
# ---------------------------------------------------------------------------


class TestGetLogsAdditional:
    @pytest.mark.asyncio
    async def test_get_logs_falls_back_to_first_pod_when_none_running(self) -> None:
        """When no pod has phase 'Running', fall back to items[0]."""
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        apps_v1 = MagicMock()
        core_v1 = MagicMock()

        pending_pod = MagicMock()
        pending_pod.metadata.name = "my-agent-pending"
        pending_pod.status.phase = "Pending"

        core_v1.list_namespaced_pod.return_value = MagicMock(items=[pending_pod])
        core_v1.read_namespaced_pod_log.return_value = "log from pending pod"

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            logs = await deployer.get_logs("my-agent")

        # Should still attempt to read from the fallback pod
        call_kwargs = core_v1.read_namespaced_pod_log.call_args[1]
        assert call_kwargs["name"] == "my-agent-pending"
        assert logs == ["log from pending pod"]

    @pytest.mark.asyncio
    async def test_get_logs_returns_error_message_on_exception(self) -> None:
        """SDK exception during log read → error message returned, no raise."""
        deployer = KubernetesDeployer()
        deployer._k8s_config = K8sConfig()

        apps_v1 = MagicMock()
        core_v1 = MagicMock()

        pod = MagicMock()
        pod.metadata.name = "my-agent-abc"
        pod.status.phase = "Running"
        core_v1.list_namespaced_pod.return_value = MagicMock(items=[pod])
        core_v1.read_namespaced_pod_log.side_effect = RuntimeError("permission denied")

        with patch.object(deployer, "_get_k8s_clients", return_value=(apps_v1, core_v1)):
            logs = await deployer.get_logs("my-agent")

        assert len(logs) == 1
        assert "Error reading logs" in logs[0]
        assert "permission denied" in logs[0]


# ---------------------------------------------------------------------------
# KubernetesDeployer._build_docker_image — lines 567-586
# ---------------------------------------------------------------------------


class TestBuildDockerImage:
    @pytest.mark.asyncio
    async def test_raises_import_error_when_docker_sdk_missing(self) -> None:
        deployer = KubernetesDeployer()
        image = _make_image()

        with patch.dict("sys.modules", {"docker": None}):
            with pytest.raises(ImportError, match="pip install docker"):
                await deployer._build_docker_image(image, "my-agent:1.2.3")

    @pytest.mark.asyncio
    async def test_builds_image_with_correct_tag(self) -> None:
        deployer = KubernetesDeployer()
        image = _make_image()

        mock_docker = MagicMock()
        mock_client = MagicMock()
        mock_docker.from_env.return_value = mock_client

        built_image = MagicMock()
        # Simulate build logs — one with stream, one without
        build_logs = [
            {"stream": "Step 1/3 : FROM python:3.11-slim\n"},
            {"stream": "   \n"},  # whitespace-only line — should be skipped
            {"other_key": "no stream key"},
        ]
        mock_client.images.build.return_value = (built_image, iter(build_logs))

        with patch.dict("sys.modules", {"docker": mock_docker}):
            await deployer._build_docker_image(image, "my-agent:1.2.3")

        mock_client.images.build.assert_called_once_with(
            path=str(image.context_dir),
            tag="my-agent:1.2.3",
            rm=True,
        )


# ---------------------------------------------------------------------------
# KubernetesDeployer._apply_deployment — lines 596-611
# ---------------------------------------------------------------------------


class TestApplyDeployment:
    def _make_manifest(self) -> dict:
        return {"metadata": {"name": "my-agent", "namespace": DEFAULT_NAMESPACE}, "spec": {}}

    def test_creates_deployment_when_not_found(self) -> None:
        deployer = KubernetesDeployer()

        mock_k8s, apps_v1, _, _ = _make_k8s_modules()
        api_exc_class = mock_k8s.client.rest.ApiException
        exc_404 = api_exc_class("not found")
        exc_404.status = 404
        apps_v1.read_namespaced_deployment.side_effect = exc_404

        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s,
                "kubernetes.client": mock_k8s.client,
                "kubernetes.client.rest": mock_k8s.client.rest,
            },
        ):
            deployer._apply_deployment(
                apps_v1, "my-agent", DEFAULT_NAMESPACE, self._make_manifest()
            )

        apps_v1.create_namespaced_deployment.assert_called_once()
        apps_v1.patch_namespaced_deployment.assert_not_called()

    def test_patches_existing_deployment(self) -> None:
        deployer = KubernetesDeployer()

        mock_k8s, apps_v1, _, _ = _make_k8s_modules()
        apps_v1.read_namespaced_deployment.return_value = MagicMock()  # exists

        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s,
                "kubernetes.client": mock_k8s.client,
                "kubernetes.client.rest": mock_k8s.client.rest,
            },
        ):
            deployer._apply_deployment(
                apps_v1, "my-agent", DEFAULT_NAMESPACE, self._make_manifest()
            )

        apps_v1.patch_namespaced_deployment.assert_called_once()
        apps_v1.create_namespaced_deployment.assert_not_called()

    def test_propagates_non_404_api_exception(self) -> None:
        deployer = KubernetesDeployer()

        mock_k8s, apps_v1, _, _ = _make_k8s_modules()
        api_exc_class = mock_k8s.client.rest.ApiException
        exc_403 = api_exc_class("forbidden")
        exc_403.status = 403
        apps_v1.read_namespaced_deployment.side_effect = exc_403

        with (
            patch.dict(
                "sys.modules",
                {
                    "kubernetes": mock_k8s,
                    "kubernetes.client": mock_k8s.client,
                    "kubernetes.client.rest": mock_k8s.client.rest,
                },
            ),
            pytest.raises(type(exc_403)),
        ):
            deployer._apply_deployment(
                apps_v1, "my-agent", DEFAULT_NAMESPACE, self._make_manifest()
            )

    def test_raises_import_error_when_kubernetes_sdk_missing(self) -> None:
        deployer = KubernetesDeployer()
        apps_v1 = MagicMock()
        with patch.dict("sys.modules", {"kubernetes.client.rest": None}):
            with pytest.raises(ImportError, match="pip install agentbreeder\\[kubernetes\\]"):
                deployer._apply_deployment(apps_v1, "my-agent", DEFAULT_NAMESPACE, {})


# ---------------------------------------------------------------------------
# KubernetesDeployer._apply_service — lines 621-636
# ---------------------------------------------------------------------------


class TestApplyService:
    def _make_manifest(self) -> dict:
        return {"metadata": {"name": "my-agent", "namespace": DEFAULT_NAMESPACE}, "spec": {}}

    def test_creates_service_when_not_found(self) -> None:
        deployer = KubernetesDeployer()

        mock_k8s, _, core_v1, _ = _make_k8s_modules()
        api_exc_class = mock_k8s.client.rest.ApiException
        exc_404 = api_exc_class("not found")
        exc_404.status = 404
        core_v1.read_namespaced_service.side_effect = exc_404

        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s,
                "kubernetes.client": mock_k8s.client,
                "kubernetes.client.rest": mock_k8s.client.rest,
            },
        ):
            deployer._apply_service(core_v1, "my-agent", DEFAULT_NAMESPACE, self._make_manifest())

        core_v1.create_namespaced_service.assert_called_once()
        core_v1.patch_namespaced_service.assert_not_called()

    def test_patches_existing_service(self) -> None:
        deployer = KubernetesDeployer()

        mock_k8s, _, core_v1, _ = _make_k8s_modules()
        core_v1.read_namespaced_service.return_value = MagicMock()  # exists

        with patch.dict(
            "sys.modules",
            {
                "kubernetes": mock_k8s,
                "kubernetes.client": mock_k8s.client,
                "kubernetes.client.rest": mock_k8s.client.rest,
            },
        ):
            deployer._apply_service(core_v1, "my-agent", DEFAULT_NAMESPACE, self._make_manifest())

        core_v1.patch_namespaced_service.assert_called_once()
        core_v1.create_namespaced_service.assert_not_called()

    def test_propagates_non_404_api_exception(self) -> None:
        deployer = KubernetesDeployer()

        mock_k8s, _, core_v1, _ = _make_k8s_modules()
        api_exc_class = mock_k8s.client.rest.ApiException
        exc_403 = api_exc_class("forbidden")
        exc_403.status = 403
        core_v1.read_namespaced_service.side_effect = exc_403

        with (
            patch.dict(
                "sys.modules",
                {
                    "kubernetes": mock_k8s,
                    "kubernetes.client": mock_k8s.client,
                    "kubernetes.client.rest": mock_k8s.client.rest,
                },
            ),
            pytest.raises(type(exc_403)),
        ):
            deployer._apply_service(core_v1, "my-agent", DEFAULT_NAMESPACE, self._make_manifest())

    def test_raises_import_error_when_kubernetes_sdk_missing(self) -> None:
        deployer = KubernetesDeployer()
        core_v1 = MagicMock()
        with patch.dict("sys.modules", {"kubernetes.client.rest": None}):
            with pytest.raises(ImportError, match="pip install agentbreeder\\[kubernetes\\]"):
                deployer._apply_service(core_v1, "my-agent", DEFAULT_NAMESPACE, {})


# ---------------------------------------------------------------------------
# KubernetesDeployer._apply_hpa — lines 640-665
# ---------------------------------------------------------------------------


class TestApplyHpa:
    def _make_manifest(self) -> dict:
        return {
            "metadata": {"name": "my-agent", "namespace": DEFAULT_NAMESPACE},
            "spec": {},
        }

    def test_creates_hpa_when_not_found(self) -> None:
        deployer = KubernetesDeployer()

        mock_k8s, _, _, autoscaling_v2 = _make_k8s_modules()
        api_exc_class = mock_k8s.client.rest.ApiException
        exc_404 = api_exc_class("not found")
        exc_404.status = 404
        autoscaling_v2.read_namespaced_horizontal_pod_autoscaler.side_effect = exc_404

        with (
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict(
                "sys.modules",
                {
                    "kubernetes": mock_k8s,
                    "kubernetes.client": mock_k8s.client,
                    "kubernetes.client.rest": mock_k8s.client.rest,
                },
            ),
        ):
            deployer._apply_hpa(self._make_manifest())

        autoscaling_v2.create_namespaced_horizontal_pod_autoscaler.assert_called_once()
        autoscaling_v2.patch_namespaced_horizontal_pod_autoscaler.assert_not_called()

    def test_patches_existing_hpa(self) -> None:
        deployer = KubernetesDeployer()

        mock_k8s, _, _, autoscaling_v2 = _make_k8s_modules()
        autoscaling_v2.read_namespaced_horizontal_pod_autoscaler.return_value = MagicMock()

        with (
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict(
                "sys.modules",
                {
                    "kubernetes": mock_k8s,
                    "kubernetes.client": mock_k8s.client,
                    "kubernetes.client.rest": mock_k8s.client.rest,
                },
            ),
        ):
            deployer._apply_hpa(self._make_manifest())

        autoscaling_v2.patch_namespaced_horizontal_pod_autoscaler.assert_called_once()
        autoscaling_v2.create_namespaced_horizontal_pod_autoscaler.assert_not_called()

    def test_propagates_non_404_api_exception(self) -> None:
        deployer = KubernetesDeployer()

        mock_k8s, _, _, autoscaling_v2 = _make_k8s_modules()
        api_exc_class = mock_k8s.client.rest.ApiException
        exc_403 = api_exc_class("forbidden")
        exc_403.status = 403
        autoscaling_v2.read_namespaced_horizontal_pod_autoscaler.side_effect = exc_403

        with (
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict(
                "sys.modules",
                {
                    "kubernetes": mock_k8s,
                    "kubernetes.client": mock_k8s.client,
                    "kubernetes.client.rest": mock_k8s.client.rest,
                },
            ),
            pytest.raises(type(exc_403)),
        ):
            deployer._apply_hpa(self._make_manifest())

    def test_raises_import_error_when_kubernetes_sdk_missing(self) -> None:
        deployer = KubernetesDeployer()
        autoscaling_v2 = MagicMock()
        with (
            patch.object(deployer, "_get_autoscaling_client", return_value=autoscaling_v2),
            patch.dict("sys.modules", {"kubernetes.client.rest": None}),
            pytest.raises(ImportError, match="pip install agentbreeder\\[kubernetes\\]"),
        ):
            deployer._apply_hpa(self._make_manifest())
