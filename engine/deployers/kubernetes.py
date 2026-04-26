"""Kubernetes deployer.

Deploys agents to a Kubernetes cluster with:
- Namespace isolation per organization (default: agentbreeder)
- Deployment + ClusterIP Service for each agent
- Horizontal Pod Autoscaler when scaling.max > 1
- Health check via liveness and readiness probes on /health

Cloud-specific logic stays in this module — never leak Kubernetes details elsewhere.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from engine.config_parser import AgentConfig
from engine.deployers.base import BaseDeployer, DeployResult, HealthStatus, InfraResult
from engine.runtimes.base import ContainerImage

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_NAMESPACE = "agentbreeder"
DEFAULT_CPU_REQUEST = "250m"
DEFAULT_CPU_LIMIT = "1"
DEFAULT_MEMORY_REQUEST = "256Mi"
DEFAULT_MEMORY_LIMIT = "1Gi"
DEFAULT_CONTAINER_PORT = 8080
DEFAULT_TARGET_CPU = 70
HEALTH_CHECK_TIMEOUT = 120
HEALTH_CHECK_INTERVAL = 5
ROLLOUT_POLL_INTERVAL = 3
ROLLOUT_MAX_WAIT = 180


class K8sConfig(BaseModel):
    """Kubernetes-specific configuration extracted from AgentConfig.deploy.env_vars."""

    namespace: str = DEFAULT_NAMESPACE
    context: str | None = None
    image_pull_secret: str | None = None
    image: str | None = None  # Override for the container image name


def _extract_k8s_config(config: AgentConfig) -> K8sConfig:
    """Extract Kubernetes config from the agent's deploy section.

    All K8S_ prefixed keys in deploy.env_vars are treated as Kubernetes
    infrastructure settings and are NOT forwarded to the container.
    """
    env = config.deploy.env_vars
    return K8sConfig(
        namespace=env.get("K8S_NAMESPACE", DEFAULT_NAMESPACE),
        context=env.get("K8S_CONTEXT") or None,
        image_pull_secret=env.get("K8S_IMAGE_PULL_SECRET") or None,
        image=env.get("K8S_IMAGE") or None,
    )


def _resolve_image_name(config: AgentConfig, k8s_config: K8sConfig) -> str:
    """Determine the container image name to use.

    Uses K8S_IMAGE override if set, otherwise falls back to
    ``{agent-name}:{version}`` as a local image reference.
    """
    if k8s_config.image:
        return k8s_config.image
    return f"{config.name}:{config.version}"


def _parse_resource(value: str | None, default: str) -> str:
    """Return the resource value or its default."""
    return value if value else default


def _build_deployment_manifest(
    config: AgentConfig,
    k8s_config: K8sConfig,
    image_name: str,
) -> dict[str, Any]:
    """Build the Kubernetes Deployment manifest dict.

    Produces a manifest compatible with the kubernetes Python client's
    ``create_namespaced_deployment`` / ``patch_namespaced_deployment`` APIs.
    """
    cpu_raw = config.deploy.resources.cpu
    memory_raw = config.deploy.resources.memory

    # Convert Gi/Mi shorthand used in agent.yaml to Kubernetes notation.
    # The agent.yaml spec uses "1" (vCPU) for cpu, but k8s prefers "1000m".
    cpu_request = DEFAULT_CPU_REQUEST
    cpu_limit = _parse_resource(cpu_raw, DEFAULT_CPU_LIMIT)
    memory_request = DEFAULT_MEMORY_REQUEST
    memory_limit = _parse_resource(memory_raw, DEFAULT_MEMORY_LIMIT)

    # Build environment variables list (exclude K8S_ infra keys)
    env_list: list[dict[str, str]] = [
        {"name": "AGENT_NAME", "value": config.name},
        {"name": "AGENT_VERSION", "value": config.version},
        {
            "name": "AGENT_FRAMEWORK",
            "value": config.framework.value
            if config.framework
            else (config.runtime.framework if config.runtime else "unknown"),
        },
    ]
    for key, val in config.deploy.env_vars.items():
        if not key.startswith("K8S_"):
            env_list.append({"name": key, "value": val})

    replicas = config.deploy.scaling.min if config.deploy.scaling.min >= 1 else 1

    container: dict[str, Any] = {
        "name": config.name,
        "image": image_name,
        "ports": [{"containerPort": DEFAULT_CONTAINER_PORT}],
        "env": env_list,
        "resources": {
            "requests": {"cpu": cpu_request, "memory": memory_request},
            "limits": {"cpu": cpu_limit, "memory": memory_limit},
        },
        "livenessProbe": {
            "httpGet": {"path": "/health", "port": DEFAULT_CONTAINER_PORT},
            "periodSeconds": 30,
        },
        "readinessProbe": {
            "httpGet": {"path": "/health", "port": DEFAULT_CONTAINER_PORT},
            "initialDelaySeconds": 5,
            "periodSeconds": 10,
        },
    }

    pod_spec: dict[str, Any] = {"containers": [container]}
    if k8s_config.image_pull_secret:
        pod_spec["imagePullSecrets"] = [{"name": k8s_config.image_pull_secret}]

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": config.name,
            "namespace": k8s_config.namespace,
            "labels": {
                "app": config.name,
                "managed-by": "agentbreeder",
                "agent-version": config.version,
                "team": config.team,
            },
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": config.name}},
            "template": {
                "metadata": {"labels": {"app": config.name}},
                "spec": pod_spec,
            },
        },
    }


def _build_service_manifest(config: AgentConfig, namespace: str) -> dict[str, Any]:
    """Build a ClusterIP Service manifest for the agent."""
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": config.name,
            "namespace": namespace,
            "labels": {
                "app": config.name,
                "managed-by": "agentbreeder",
            },
        },
        "spec": {
            "selector": {"app": config.name},
            "ports": [
                {
                    "protocol": "TCP",
                    "port": DEFAULT_CONTAINER_PORT,
                    "targetPort": DEFAULT_CONTAINER_PORT,
                }
            ],
            "type": "ClusterIP",
        },
    }


def _build_hpa_manifest(config: AgentConfig, namespace: str) -> dict[str, Any]:
    """Build a HorizontalPodAutoscaler manifest for the agent."""
    target_cpu = config.deploy.scaling.target_cpu or DEFAULT_TARGET_CPU
    return {
        "apiVersion": "autoscaling/v2",
        "kind": "HorizontalPodAutoscaler",
        "metadata": {
            "name": config.name,
            "namespace": namespace,
            "labels": {"managed-by": "agentbreeder"},
        },
        "spec": {
            "scaleTargetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": config.name,
            },
            "minReplicas": max(config.deploy.scaling.min, 1),
            "maxReplicas": config.deploy.scaling.max,
            "metrics": [
                {
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {
                            "type": "Utilization",
                            "averageUtilization": target_cpu,
                        },
                    },
                }
            ],
        },
    }


class KubernetesDeployer(BaseDeployer):
    """Deploys agents to a Kubernetes cluster.

    Uses the official ``kubernetes`` Python client to apply Deployments,
    Services, and HPAs. Kubeconfig is loaded lazily so that the class can be
    instantiated without the SDK installed.
    """

    def __init__(self) -> None:
        self._k8s_config: K8sConfig | None = None

    # ------------------------------------------------------------------
    # Client bootstrap
    # ------------------------------------------------------------------

    def _get_k8s_clients(
        self,
    ) -> tuple[Any, Any]:  # (AppsV1Api, CoreV1Api)
        """Lazy-import the kubernetes SDK and return API clients.

        Raises:
            ImportError: If the ``kubernetes`` package is not installed.
        """
        try:
            import kubernetes  # noqa: PLC0415
            from kubernetes import client as k8s_client  # noqa: PLC0415
        except ImportError as exc:
            msg = "Kubernetes SDK not installed. Run: pip install agentbreeder[kubernetes]"
            raise ImportError(msg) from exc

        context = self._k8s_config.context if self._k8s_config else None
        kubernetes.config.load_kube_config(context=context)

        apps_v1 = k8s_client.AppsV1Api()
        core_v1 = k8s_client.CoreV1Api()
        return apps_v1, core_v1

    def _get_autoscaling_client(self) -> Any:
        """Return an AutoscalingV2Api client (lazy import)."""
        try:
            from kubernetes import client as k8s_client  # noqa: PLC0415
        except ImportError as exc:
            msg = "Kubernetes SDK not installed. Run: pip install agentbreeder[kubernetes]"
            raise ImportError(msg) from exc
        return k8s_client.AutoscalingV2Api()

    # ------------------------------------------------------------------
    # Namespace helpers
    # ------------------------------------------------------------------

    def _ensure_namespace(self, core_v1: Any, namespace: str) -> None:
        """Create the namespace if it doesn't already exist."""
        try:
            from kubernetes.client.rest import ApiException  # noqa: PLC0415
        except ImportError as exc:
            msg = "Kubernetes SDK not installed. Run: pip install agentbreeder[kubernetes]"
            raise ImportError(msg) from exc

        try:
            core_v1.read_namespace(name=namespace)
            logger.info("Namespace '%s' already exists", namespace)
        except ApiException as exc:
            if exc.status == 404:
                logger.info("Creating namespace '%s'", namespace)
                core_v1.create_namespace(
                    body={
                        "apiVersion": "v1",
                        "kind": "Namespace",
                        "metadata": {
                            "name": namespace,
                            "labels": {"managed-by": "agentbreeder"},
                        },
                    }
                )
                logger.info("Namespace '%s' created", namespace)
            else:
                raise

    # ------------------------------------------------------------------
    # BaseDeployer interface
    # ------------------------------------------------------------------

    async def provision(self, config: AgentConfig) -> InfraResult:
        """Validate the Kubernetes connection and ensure the namespace exists.

        Steps:
        1. Extract K8s config from agent config
        2. Load kubeconfig and validate connectivity
        3. Create namespace if absent
        4. Return the expected ClusterIP service endpoint
        """
        self._k8s_config = _extract_k8s_config(config)
        k8s = self._k8s_config
        namespace = k8s.namespace

        logger.info(
            "Provisioning Kubernetes resources for '%s' in namespace '%s'",
            config.name,
            namespace,
        )

        _apps_v1, core_v1 = self._get_k8s_clients()
        self._ensure_namespace(core_v1, namespace)

        endpoint_url = (
            f"http://{config.name}.{namespace}.svc.cluster.local:{DEFAULT_CONTAINER_PORT}"
        )

        return InfraResult(
            endpoint_url=endpoint_url,
            resource_ids={
                "namespace": namespace,
                "deployment": config.name,
                "service": config.name,
            },
        )

    async def deploy(self, config: AgentConfig, image: ContainerImage | None) -> DeployResult:
        """Build the Docker image and apply Deployment, Service, and (optional) HPA.

        Steps:
        1. Build Docker image locally via Docker SDK
        2. Apply Deployment (create or patch)
        3. Apply ClusterIP Service (create or patch)
        4. Apply HPA if scaling.max > 1
        5. Wait for at least one replica to become available
        6. Return DeployResult
        """
        if self._k8s_config is None:
            self._k8s_config = _extract_k8s_config(config)
        k8s = self._k8s_config
        namespace = k8s.namespace

        image_name = _resolve_image_name(config, k8s)

        # Step 1: Build Docker image
        assert image is not None, "ContainerImage required for Kubernetes deployer"
        await self._build_docker_image(image, image_name)

        # Steps 2-4: Apply Kubernetes manifests
        apps_v1, core_v1 = self._get_k8s_clients()

        # Ensure namespace exists
        self._ensure_namespace(core_v1, namespace)

        # Apply Deployment
        deployment_manifest = _build_deployment_manifest(config, k8s, image_name)
        self._apply_deployment(apps_v1, config.name, namespace, deployment_manifest)

        # Apply Service
        service_manifest = _build_service_manifest(config, namespace)
        self._apply_service(core_v1, config.name, namespace, service_manifest)

        # Apply HPA if max replicas > 1
        if config.deploy.scaling.max > 1:
            hpa_manifest = _build_hpa_manifest(config, namespace)
            self._apply_hpa(hpa_manifest)

        # Step 5: Wait for rollout
        await self._wait_for_rollout(apps_v1, config.name, namespace)

        endpoint_url = (
            f"http://{config.name}.{namespace}.svc.cluster.local:{DEFAULT_CONTAINER_PORT}"
        )

        logger.info("Kubernetes deployment complete: %s → %s", config.name, endpoint_url)

        return DeployResult(
            endpoint_url=endpoint_url,
            container_id=image_name,
            status="running",
            agent_name=config.name,
            version=config.version,
        )

    async def health_check(
        self,
        deploy_result: DeployResult,
        timeout: int = HEALTH_CHECK_TIMEOUT,
        interval: int = HEALTH_CHECK_INTERVAL,
    ) -> HealthStatus:
        """Poll the agent's /health endpoint until it returns 200 or timeout expires.

        Uses the ClusterIP service URL from DeployResult. Intended to be called
        from within the cluster (e.g., from the CLI pod or a Job). For out-of-cluster
        health checks, port-forward or use an Ingress first.
        """
        url = f"{deploy_result.endpoint_url}/health"
        checks: dict[str, bool] = {"reachable": False, "healthy": False}
        max_attempts = timeout // interval

        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url)
                    checks["reachable"] = True
                    if response.status_code == 200:
                        checks["healthy"] = True
                        logger.info(
                            "Health check passed (attempt %d/%d)", attempt + 1, max_attempts
                        )
                        return HealthStatus(healthy=True, checks=checks)
                    logger.debug(
                        "Health check returned %d (attempt %d/%d)",
                        response.status_code,
                        attempt + 1,
                        max_attempts,
                    )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout):
                pass

            logger.debug(
                "Health check attempt %d/%d — waiting %ds...",
                attempt + 1,
                max_attempts,
                interval,
            )
            await asyncio.sleep(interval)

        logger.warning(
            "Health check failed after %d seconds for '%s'", timeout, deploy_result.agent_name
        )
        return HealthStatus(healthy=False, checks=checks)

    async def teardown(self, agent_name: str) -> None:
        """Delete the Deployment, Service, HPA, and any ConfigMap for the agent.

        404 errors are silently ignored — the resource may have already been
        deleted or may never have been created.
        """
        if self._k8s_config is None:
            msg = "Cannot teardown without Kubernetes config. Call provision() or deploy() first."
            raise RuntimeError(msg)

        namespace = self._k8s_config.namespace
        apps_v1, core_v1 = self._get_k8s_clients()
        autoscaling_v2 = self._get_autoscaling_client()

        try:
            from kubernetes.client.rest import ApiException  # noqa: PLC0415
        except ImportError as exc:
            msg = "Kubernetes SDK not installed. Run: pip install agentbreeder[kubernetes]"
            raise ImportError(msg) from exc

        logger.info("Tearing down Kubernetes resources for '%s' in '%s'", agent_name, namespace)

        # Delete Deployment
        try:
            apps_v1.delete_namespaced_deployment(name=agent_name, namespace=namespace)
            logger.info("Deleted Deployment '%s'", agent_name)
        except ApiException as exc:
            if exc.status != 404:
                raise
            logger.debug("Deployment '%s' not found — skipping", agent_name)

        # Delete Service
        try:
            core_v1.delete_namespaced_service(name=agent_name, namespace=namespace)
            logger.info("Deleted Service '%s'", agent_name)
        except ApiException as exc:
            if exc.status != 404:
                raise
            logger.debug("Service '%s' not found — skipping", agent_name)

        # Delete HPA (may not exist if scaling.max == 1)
        try:
            autoscaling_v2.delete_namespaced_horizontal_pod_autoscaler(
                name=agent_name, namespace=namespace
            )
            logger.info("Deleted HPA '%s'", agent_name)
        except ApiException as exc:
            if exc.status != 404:
                raise
            logger.debug("HPA '%s' not found — skipping", agent_name)

        # Delete ConfigMap (if any was created)
        try:
            core_v1.delete_namespaced_config_map(name=agent_name, namespace=namespace)
            logger.info("Deleted ConfigMap '%s'", agent_name)
        except ApiException as exc:
            if exc.status != 404:
                raise
            logger.debug("ConfigMap '%s' not found — skipping", agent_name)

        logger.info("Teardown complete for '%s'", agent_name)

    async def get_logs(self, agent_name: str, since: datetime | None = None) -> list[str]:
        """Retrieve logs from the first running pod for the agent.

        Uses label selector ``app={agent_name}`` to find pods in the
        configured namespace, then reads logs from the first Running pod.
        """
        if self._k8s_config is None:
            return [f"Cannot get logs: Kubernetes config not initialized for '{agent_name}'"]

        namespace = self._k8s_config.namespace
        _apps_v1, core_v1 = self._get_k8s_clients()

        # List pods matching the agent's label
        pod_list = core_v1.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"app={agent_name}",
        )

        if not pod_list.items:
            return [f"No pods found for agent '{agent_name}' in namespace '{namespace}'"]

        # Pick first Running pod
        target_pod = None
        for pod in pod_list.items:
            if pod.status and pod.status.phase == "Running":
                target_pod = pod
                break

        if target_pod is None:
            # Fall back to any pod if none are Running yet
            target_pod = pod_list.items[0]

        pod_name = target_pod.metadata.name

        kwargs: dict[str, Any] = {
            "name": pod_name,
            "namespace": namespace,
            "container": agent_name,
            "tail_lines": 200,
        }
        if since:
            kwargs["since_seconds"] = int((datetime.utcnow() - since).total_seconds())

        try:
            raw_logs: str = core_v1.read_namespaced_pod_log(**kwargs)
            if not raw_logs:
                return [f"No log output from pod '{pod_name}'"]
            return raw_logs.splitlines()
        except Exception as exc:  # noqa: BLE001
            return [f"Error reading logs from pod '{pod_name}': {exc}"]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _build_docker_image(self, image: ContainerImage, image_name: str) -> None:
        """Build the Docker image locally using the Docker SDK."""
        try:
            import docker  # noqa: PLC0415
        except ImportError as exc:
            msg = "Docker SDK not installed. Run: pip install docker"
            raise ImportError(msg) from exc

        client = docker.from_env()

        logger.info("Building Docker image: %s", image_name)
        _built_image, build_logs = client.images.build(
            path=str(image.context_dir),
            tag=image_name,
            rm=True,
        )
        for chunk in build_logs:
            if "stream" in chunk:
                line = chunk["stream"].strip()
                if line:
                    logger.debug("  %s", line)
        logger.info("Docker image built: %s", image_name)

    def _apply_deployment(
        self,
        apps_v1: Any,
        name: str,
        namespace: str,
        manifest: dict[str, Any],
    ) -> None:
        """Create or replace the Deployment."""
        try:
            from kubernetes.client.rest import ApiException  # noqa: PLC0415
        except ImportError as exc:
            msg = "Kubernetes SDK not installed. Run: pip install agentbreeder[kubernetes]"
            raise ImportError(msg) from exc

        try:
            apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
            logger.info("Patching existing Deployment '%s'", name)
            apps_v1.patch_namespaced_deployment(name=name, namespace=namespace, body=manifest)
        except ApiException as exc:
            if exc.status == 404:
                logger.info("Creating Deployment '%s'", name)
                apps_v1.create_namespaced_deployment(namespace=namespace, body=manifest)
            else:
                raise

    def _apply_service(
        self,
        core_v1: Any,
        name: str,
        namespace: str,
        manifest: dict[str, Any],
    ) -> None:
        """Create or patch the ClusterIP Service."""
        try:
            from kubernetes.client.rest import ApiException  # noqa: PLC0415
        except ImportError as exc:
            msg = "Kubernetes SDK not installed. Run: pip install agentbreeder[kubernetes]"
            raise ImportError(msg) from exc

        try:
            core_v1.read_namespaced_service(name=name, namespace=namespace)
            logger.info("Patching existing Service '%s'", name)
            core_v1.patch_namespaced_service(name=name, namespace=namespace, body=manifest)
        except ApiException as exc:
            if exc.status == 404:
                logger.info("Creating Service '%s'", name)
                core_v1.create_namespaced_service(namespace=namespace, body=manifest)
            else:
                raise

    def _apply_hpa(self, manifest: dict[str, Any]) -> None:
        """Create or patch the HorizontalPodAutoscaler."""
        try:
            from kubernetes.client.rest import ApiException  # noqa: PLC0415
        except ImportError as exc:
            msg = "Kubernetes SDK not installed. Run: pip install agentbreeder[kubernetes]"
            raise ImportError(msg) from exc

        autoscaling_v2 = self._get_autoscaling_client()
        name = manifest["metadata"]["name"]
        namespace = manifest["metadata"]["namespace"]

        try:
            autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(
                name=name, namespace=namespace
            )
            logger.info("Patching existing HPA '%s'", name)
            autoscaling_v2.patch_namespaced_horizontal_pod_autoscaler(
                name=name, namespace=namespace, body=manifest
            )
        except ApiException as exc:
            if exc.status == 404:
                logger.info("Creating HPA '%s'", name)
                autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(
                    namespace=namespace, body=manifest
                )
            else:
                raise

    async def _wait_for_rollout(
        self,
        apps_v1: Any,
        name: str,
        namespace: str,
        max_wait: int = ROLLOUT_MAX_WAIT,
        poll_interval: int = ROLLOUT_POLL_INTERVAL,
    ) -> None:
        """Wait until the Deployment has at least one available replica."""
        elapsed = 0
        while elapsed < max_wait:
            deployment = apps_v1.read_namespaced_deployment(name=name, namespace=namespace)
            available = deployment.status.available_replicas or 0
            if available >= 1:
                logger.info("Rollout complete — %d replica(s) available", available)
                return
            logger.debug(
                "Waiting for rollout of '%s' (%d/%ds elapsed)…",
                name,
                elapsed,
                max_wait,
            )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning(
            "Rollout of '%s' did not complete within %d seconds — continuing anyway",
            name,
            max_wait,
        )
